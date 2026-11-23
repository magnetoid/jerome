"""Write a translated DOCX on the structure of the original.

Each translated paragraph is rebuilt in place: its text runs are replaced by one run per
constant mark span of the translation, its placeholders (note references, images, fields,
tabs, breaks) are moved to where the translation puts them, and everything else about the
paragraph stays. Other paragraphs keep their XML, and story parts (body, footnotes, endnotes)
with no translated paragraph keep their original bytes. ``read_docx`` of the result gives
each translated block's text and marks exactly; the proofreading writers then work on it
like on any other DOCX.
"""

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass

from lxml import etree

from jerome.documents.changes import NOT_XML
from jerome.documents.docx_runs import REVISIONS, cut, text_run
from jerome.documents.docx_writer import (
    _OFFICE_REL,
    _next_id,
    _related_part,
    _serialize,
    _word_roots,
)
from jerome.documents.docx_xml import (
    DOCUMENT_PART,
    NS,
    ParagraphText,
    is_block_text,
    iter_paragraph_runs,
    parse_xml,
    qn,
    read_package,
    read_rels,
    write_package,
)
from jerome.documents.model import PLACEHOLDER, Mark, MarkKind
from jerome.documents.translated import TranslatedBlock
from jerome.errors import InvalidChangeError, UnsupportedDocumentError

_R = qn("w:r")
_RPR = qn("w:rPr")
_T = qn("w:t")
_ID = qn("w:id")
_VAL = qn("w:val")
_HYPERLINK = qn("w:hyperlink")
_FLD_CHAR = qn("w:fldChar")
_FLD_CHAR_TYPE = qn("w:fldCharType")
_LANG = qn("w:lang")
_VERT_ALIGN = qn("w:vertAlign")
_SDT = qn("w:sdt")
_SDT_CONTENT = qn("w:sdtContent")
_LAST_RENDERED = qn("w:lastRenderedPageBreak")

# Containers the reader walks into (see docx_xml._Walker), and their property children.
_CONTAINERS = {
    qn(f"w:{tag}") for tag in "hyperlink smartTag customXml ins moveTo dir bdo sdt".split()
} | {_SDT_CONTENT}
_CONTAINER_PROPERTIES = {qn(f"w:{tag}") for tag in "smartTagPr customXmlPr sdtPr sdtEndPr".split()}
# Paragraph-level placeholders (docx_xml._OBJECTS): they cannot carry run properties.
_OBJECTS = {qn("w:fldSimple"), qn("m:oMath"), qn("m:oMathPara"), qn("mc:AlternateContent")}

# Direct run properties that make marks, as docx_xml._run_marks reads them.
_OFF = {"0", "false", "off"}
_TOGGLES: dict[MarkKind, tuple[str, ...]] = {
    MarkKind.BOLD: ("w:b",),
    MarkKind.ITALIC: ("w:i",),
    MarkKind.UNDERLINE: ("w:u",),
    MarkKind.SMALLCAPS: ("w:smallCaps",),
    MarkKind.STRIKE: ("w:strike", "w:dstrike"),
}
_VERT_ALIGNS = {MarkKind.SUPERSCRIPT: "superscript", MarkKind.SUBSCRIPT: "subscript"}
# The w:rPr child sequence of the schema (CT_RPr), so inserted toggles land where Word wants.
_RPR_ORDER = {
    qn(f"w:{tag}"): n
    for n, tag in enumerate(
        "rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint"
        " noProof snapToGrid vanish webHidden color spacing w kern position sz szCs highlight u"
        " effect bdr shd fitText vertAlign rtl cs em lang eastAsianLayout specVanish oMath".split()
    )
}
_RPR_CHANGE = qn("w:rPrChange")

_SETTINGS_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"
_CORE_REL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
_CORE_TYPE = "application/vnd.openxmlformats-package.core-properties+xml"
# w:settings children that come after w:updateFields in the schema (CT_Settings).
_AFTER_UPDATE_FIELDS = set(
    (
        "hdrShapeDefaults footnotePr endnotePr compat docVars rsids mathPr attachedSchema"
        " themeFontLang clrSchemeMapping doNotIncludeSubdocsInStats doNotAutoCompressPictures"
        " forceUpgrade captions readModeInkLockDown smartTagType schemaLibrary shapeDefaults"
        " doNotEmbedSmartTags decimalSymbol listSeparator"
    ).split()
)


def write_translated_docx(
    original: bytes,
    blocks: Mapping[str, TranslatedBlock],
    *,
    language: str,
    title: str | None,
) -> bytes:
    """The DOCX ``original`` with each block in ``blocks`` (by ``ParsedBlock.key``) translated.

    ``language`` (e.g. ``de-DE``) becomes the document's default language and replaces the
    ``w:lang`` of rebuilt runs. Word is asked to update fields on open (``w:updateFields``),
    so a table of contents picks up the translated headings. ``title``, when given, becomes
    the core properties' ``dc:title``.

    Raises InvalidChangeError for a key that names no block, and for a translation that
    cannot be written: placeholders that do not match the source's, marks out of range or
    linking to a target the source paragraph has no link to, characters XML cannot carry,
    or text that would land inside a field reaching into another paragraph.
    """
    if title is not None and NOT_XML.search(title):
        raise InvalidChangeError("title contains characters not allowed in XML")
    package = read_package(original)
    if DOCUMENT_PART not in package:
        raise UnsupportedDocumentError(f"Not a Word document: {DOCUMENT_PART} is missing")

    found, trees = _find_blocks(package, set(blocks))
    dirty: set[str] = set()
    links: dict[str, dict[str, str]] = {}
    for key, (p, pt, incoming) in found.items():
        part = key.partition("#")[0]
        if part not in links:
            links[part] = read_rels(package, part)
        _Rebuild(key, p, pt, incoming, links[part], language).write(blocks[key])
        dirty.add(part)
    if dirty:
        _number_revisions(package, trees, dirty)
        for part in dirty:
            package[part] = _serialize(trees[part])

    _set_default_language(package, language)
    _set_update_fields(package)
    if title is not None:
        _set_title(package, title)
    return write_package(package, original)


def _find_blocks(
    package: Mapping[str, bytes], keys: set[str]
) -> tuple[dict[str, tuple[etree._Element, ParagraphText, int]], dict[str, etree._ElementTree]]:
    """For each key, its ``(w:p, text, fields open when it starts)``, and the parsed parts."""
    found: dict[str, tuple[etree._Element, ParagraphText, int]] = {}
    trees: dict[str, etree._ElementTree] = {}
    if keys:
        story, open_fields = None, 0
        for key, p, tree, pt in iter_paragraph_runs(package):
            trees.setdefault(key.partition("#")[0], tree)
            root = next(p.iterancestors(qn("w:body"), qn("w:footnote"), qn("w:endnote")), None)
            incoming = open_fields if root is story else 0
            story, open_fields = root, pt.open_fields
            if key in keys and is_block_text(pt.text):
                found[key] = (p, pt, incoming)
    if missing := keys - found.keys():
        raise InvalidChangeError(f"No block with key {min(missing)!r}")
    return found, trees


def _number_revisions(
    package: Mapping[str, bytes], trees: Mapping[str, etree._ElementTree], dirty: set[str]
) -> None:
    """Number revisions in copied run properties (``copy_rpr`` leaves their w:id empty)."""
    next_id = _next_id(_word_roots(package, trees), REVISIONS)
    for part in dirty:
        for el in trees[part].getroot().iter(*REVISIONS):
            if not el.get(_ID):
                el.set(_ID, str(next_id))
                next_id += 1


# --- one paragraph --------------------------------------------------------------------


@dataclass
class _Unit:
    """The elements of one source placeholder, siblings in document order."""

    elements: list[etree._Element]
    mark_run: etree._Element | None  # the run whose properties give its marks, if any


@dataclass
class _SourceRun:
    marks: frozenset[tuple[MarkKind, str | None]]
    link: str | None
    rpr: etree._Element | None


class _Rebuild:
    def __init__(
        self,
        key: str,
        p: etree._Element,
        pt: ParagraphText,
        incoming: int,
        links: Mapping[str, str],
        language: str,
    ):
        self.key = key
        self.p = p
        self.pt = pt
        self.incoming = incoming  # complex fields open when the paragraph starts
        self.links = links
        self.language = language

    def write(self, block: TranslatedBlock) -> None:
        hyperlinks = self._hyperlinks()
        order = self._check(block, hyperlinks)
        sources = self._source_runs()
        units = self._isolate_units()

        sentinel = etree.Element(qn("w:jeromeSentinel"))
        self._top(self.pt.pieces[0].node).addprevious(sentinel)
        for piece in self.pt.pieces:
            if piece.is_text:
                piece.node.getparent().remove(piece.node)
        for unit in units:
            for el in unit.elements:
                el.getparent().remove(el)
        self._tidy()

        items = self._items(block, order, units, sources)
        for el in _group_links(items, hyperlinks):
            sentinel.addprevious(el)
        self.p.remove(sentinel)

    # --- checks

    def _check(self, block: TranslatedBlock, hyperlinks: Mapping[str, etree._Element]) -> list[int]:
        """The placeholder order, after checking the translation can be written."""
        text, count = block.text, self.pt.text.count(PLACEHOLDER)
        if NOT_XML.search(text):
            raise InvalidChangeError(f"{self.key}: translation has characters not allowed in XML")
        if text.count(PLACEHOLDER) != count:
            raise InvalidChangeError(
                f"{self.key}: translation has {text.count(PLACEHOLDER)} placeholders,"
                f" the source {count}"
            )
        order = list(block.placeholders) if block.placeholders is not None else list(range(count))
        if sorted(order) != list(range(count)):
            raise InvalidChangeError(f"{self.key}: placeholder order {order} is not a permutation")
        for m in block.marks:
            if not 0 <= m.start <= m.end <= len(text):
                raise InvalidChangeError(f"{self.key}: mark ({m.start}, {m.end}) is out of range")
            if m.kind == MarkKind.LINK and m.href not in hyperlinks:
                raise InvalidChangeError(f"{self.key}: the source has no link to {m.href!r}")
        pieces = self.pt.pieces
        if pieces[0].node.tag == _R and not (text.startswith(PLACEHOLDER) and order[0] == 0):
            raise InvalidChangeError(
                f"{self.key}: the field continued from the previous paragraph must stay first"
            )
        if self.pt.open_fields and not (text.endswith(PLACEHOLDER) and order[-1] == count - 1):
            raise InvalidChangeError(
                f"{self.key}: the field continuing into the next paragraph must stay last"
            )
        return order

    # --- the source

    def _source_runs(self) -> list[_SourceRun]:
        """The marks and run properties of each text piece, in order."""
        out = []
        for piece in self.pt.pieces:
            if piece.is_text:
                marks = _marks_at(self.pt.marks, piece.start)
                link = next((href for kind, href in marks if kind == MarkKind.LINK), None)
                rpr = piece.node.getparent().find(_RPR)
                out.append(_SourceRun(marks, link, deepcopy(rpr) if rpr is not None else None))
        return out

    def _hyperlinks(self) -> dict[str, etree._Element]:
        """The first source hyperlink of each href."""
        out: dict[str, etree._Element] = {}
        for link in self._walk(self.p, _HYPERLINK):
            href = _href(link, self.links)
            if href is not None:
                out.setdefault(href, link)
        return out

    def _isolate_units(self) -> list[_Unit]:
        """Split runs so each placeholder is whole elements; return them in source order."""
        ends = {}
        for piece in self.pt.pieces:  # find field ends before any run is split
            if piece.node.tag == _FLD_CHAR:
                ends[id(piece)] = self._field_end(piece.node, 1)
            elif piece.node.tag == _R:  # a field continued from the previous paragraph
                ends[id(piece)] = self._field_end(None, self.incoming)
        units = []
        for piece in self.pt.pieces:
            if piece.is_text:
                continue
            node, parent = piece.node, piece.node.getparent()
            if node.tag in (_FLD_CHAR, _R):
                end = ends[id(piece)]
                if node.tag == _FLD_CHAR:
                    cut(node, 0)
                    first = node.getparent()
                else:
                    first = node
                if end is not None:
                    cut(end, 1)
                    last = end.getparent()
                else:  # the field goes on into the next paragraph
                    last = list(self._walk(self.p, _R))[-1]
                units.append(_Unit(self._span(first, last), first))
            elif parent.tag == _R:  # an object inside a run
                cut(node, 0)
                cut(node, 1)
                units.append(_Unit([node.getparent()], node.getparent()))
            else:  # a paragraph-level object
                units.append(_Unit([node], None))
        return units

    def _field_end(self, begin: etree._Element | None, depth: int) -> etree._Element | None:
        """The fldChar closing the field that ``begin`` opens (None: from the paragraph's
        start, with ``depth`` fields open), or None when it closes in a later paragraph."""
        chars = list(self._walk(self.p, _FLD_CHAR))
        rest = chars[chars.index(begin) + 1 :] if begin is not None else chars
        for char in rest:
            kind = char.get(_FLD_CHAR_TYPE)
            if kind == "begin":
                depth += 1
            elif kind == "end":
                depth -= 1
                if depth == 0:
                    return char
        return None

    def _span(self, first: etree._Element, last: etree._Element) -> list[etree._Element]:
        """The siblings from the one holding ``first`` to the one holding ``last``."""
        first_chain = [first, *first.iterancestors()]
        last_chain = [last, *last.iterancestors()]
        common = next(a for a in first_chain if a in last_chain)
        a = first_chain[first_chain.index(common) - 1] if first is not common else first
        b = last_chain[last_chain.index(common) - 1] if last is not common else last
        out = [a]
        for sibling in a.itersiblings():
            if out[-1] is b:
                break
            out.append(sibling)
        return out

    def _top(self, el: etree._Element) -> etree._Element:
        """The child of the paragraph that holds ``el``."""
        while el.getparent() is not self.p:
            el = el.getparent()
        return el

    def _walk(self, container: etree._Element, tag: str) -> Iterator[etree._Element]:
        """``tag`` elements the reader sees: in runs and containers, not inside objects."""
        for child in container:
            if child.tag == tag:
                yield child
            if child.tag == _R or child.tag in _CONTAINERS:
                yield from self._walk(child, tag)

    def _tidy(self) -> None:
        """Drop runs left with nothing, and unwrap containers left without runs or objects."""
        for run in list(self._walk(self.p, _R)):
            if all(_is_empty(child) for child in run):
                run.getparent().remove(run)
        containers = [
            el for el in self.p.iter(*_CONTAINERS) if el.tag != _SDT_CONTENT and self._is_walked(el)
        ]
        for container in reversed(containers):  # innermost first
            if next(container.iter(_R, *_OBJECTS), None) is not None:
                continue
            if container.tag == _SDT:
                content = container.find(_SDT_CONTENT)
                children = list(content) if content is not None else []
            else:
                children = [c for c in container if c.tag not in _CONTAINER_PROPERTIES]
            for child in children:
                container.addprevious(child)
            container.getparent().remove(container)

    def _is_walked(self, el: etree._Element) -> bool:
        for ancestor in el.iterancestors():
            if ancestor is self.p:
                return True
            if ancestor.tag not in _CONTAINERS:
                return False
        return False

    # --- the translation

    def _items(
        self,
        block: TranslatedBlock,
        order: list[int],
        units: list[_Unit],
        sources: list[_SourceRun],
    ) -> list[tuple[etree._Element, str | None]]:
        """The new content, each element with the href of the link it belongs in."""
        text = block.text
        marks = [m for m in block.marks if m.start < m.end]
        cuts = {0, len(text)} | {m.start for m in marks} | {m.end for m in marks}
        for i, ch in enumerate(text):
            if ch == PLACEHOLDER:
                cuts |= {i, i + 1}
        bounds = sorted(cuts)
        items: list[tuple[etree._Element, str | None]] = []
        slot = 0
        for start, end in zip(bounds, bounds[1:], strict=False):
            span = _marks_at(marks, start)
            link = next((href for kind, href in span if kind == MarkKind.LINK), None)
            kinds = {kind for kind, _ in span if kind != MarkKind.LINK}
            if text[start] == PLACEHOLDER:
                unit = units[order[slot]]
                slot += 1
                if unit.mark_run is not None:
                    _mark_run(unit.mark_run, kinds)
                items += [(el, link) for el in unit.elements]
            else:
                items.append((text_run(text[start:end], self._rpr(span, link, sources)), link))
        return items

    def _rpr(
        self,
        span: frozenset[tuple[MarkKind, str | None]],
        link: str | None,
        sources: list[_SourceRun],
    ) -> etree._Element | None:
        """Run properties for a span: those of the first source run with the same marks, or
        else of the first with the same link (or none), or else of the first text run, with
        the mark toggles set or cleared."""
        source = (
            next((s for s in sources if s.marks == span), None)
            or next((s for s in sources if s.link == link), None)
            or sources[0]
        )
        rpr = deepcopy(source.rpr) if source.rpr is not None else etree.Element(_RPR)
        _set_toggles(rpr, {kind for kind, _ in span if kind != MarkKind.LINK})
        for lang in rpr.findall(_LANG):
            lang.set(_VAL, self.language)
        return rpr if len(rpr) else None


def _is_empty(run_child: etree._Element) -> bool:
    """Whether a run child shows nothing: properties, an empty w:t, a stale page break."""
    tag = run_child.tag
    return tag in (_RPR, _LAST_RENDERED) or (tag == _T and not run_child.text)


def _marks_at(marks: list[Mark], offset: int) -> frozenset[tuple[MarkKind, str | None]]:
    return frozenset(
        (m.kind, m.href if m.kind == MarkKind.LINK else None)
        for m in marks
        if m.start <= offset < m.end
    )


def _href(link: etree._Element, links: Mapping[str, str]) -> str | None:
    """A hyperlink's href, as the reader gives it in its LINK mark."""
    rid = link.get(qn("r:id"))
    href = links.get(rid) if rid else None
    if anchor := link.get(qn("w:anchor")):
        href = f"{href or ''}#{anchor}"
    return href


def _group_links(
    items: list[tuple[etree._Element, str | None]], hyperlinks: Mapping[str, etree._Element]
) -> list[etree._Element]:
    """Put consecutive items of the same link into one w:hyperlink, a copy of the source's
    (its attributes: r:id, anchor, tooltip, history)."""
    out: list[etree._Element] = []
    current: tuple[str, etree._Element] | None = None
    for el, href in items:
        if href is None:
            out.append(el)
            current = None
            continue
        if current is None or current[0] != href:
            wrapper = etree.Element(_HYPERLINK, dict(hyperlinks[href].attrib))
            out.append(wrapper)
            current = (href, wrapper)
        current[1].append(el)
    return out


# --- run properties -------------------------------------------------------------------


def _mark_run(run: etree._Element, kinds: set[MarkKind]) -> None:
    """Make a moved placeholder's run carry exactly the marks ``kinds``."""
    rpr = run.find(_RPR)
    created = rpr is None
    if created:
        rpr = etree.Element(_RPR)
        run.insert(0, rpr)
    _set_toggles(rpr, kinds)
    if created and not len(rpr):
        run.remove(rpr)


def _set_toggles(rpr: etree._Element, kinds: set[MarkKind]) -> None:
    """Set or clear the direct properties that make marks, so the run has exactly ``kinds``.

    A property set by a style is not a mark and is left alone. A run cannot be both
    superscript and subscript; superscript wins.
    """
    for kind, names in _TOGGLES.items():
        tags = {qn(name) for name in names}
        props = [child for child in rpr if child.tag in tags]
        on = any(_is_on(prop) for prop in props)
        if on != (kind in kinds):
            for prop in props:
                rpr.remove(prop)
            if kind in kinds:
                attrs = {_VAL: "single"} if kind == MarkKind.UNDERLINE else {}
                _insert(rpr, etree.Element(qn(names[0]), attrs))
    want = next((v for k, v in _VERT_ALIGNS.items() if k in kinds), None)
    aligns = rpr.findall(_VERT_ALIGN)
    have = {a.get(_VAL) for a in aligns} & set(_VERT_ALIGNS.values())
    if have != ({want} if want else set()):
        for align in aligns:
            rpr.remove(align)
        if want:
            _insert(rpr, etree.Element(_VERT_ALIGN, {_VAL: want}))


def _is_on(prop: etree._Element) -> bool:
    if prop.tag == qn("w:u"):
        return prop.get(_VAL) != "none"
    return prop.get(_VAL) not in _OFF


def _insert(parent: etree._Element, el: etree._Element) -> None:
    """Insert a run property where the schema's sequence puts it."""
    rank = _rank(el)
    for child in parent:
        if _rank(child) > rank:
            child.addprevious(el)
            return
    parent.append(el)


def _rank(el: etree._Element) -> int:
    if el.tag == _RPR_CHANGE:
        return len(_RPR_ORDER) + 1
    return _RPR_ORDER.get(el.tag, len(_RPR_ORDER)) if isinstance(el.tag, str) else len(_RPR_ORDER)


# --- package parts --------------------------------------------------------------------


def _set_default_language(package: dict[str, bytes], language: str) -> None:
    """Write ``language`` to the w:lang of the styles' w:docDefaults (no styles part: none)."""
    part = "word/styles.xml"
    if part not in package:
        return
    tree = parse_xml(package[part], part)
    root = tree.getroot()
    defaults = _child(root, "w:docDefaults", first=True)
    rpr = _child(_child(defaults, "w:rPrDefault", first=True), "w:rPr")
    lang = rpr.find(_LANG)
    if lang is None:
        lang = etree.Element(_LANG)
        _insert(rpr, lang)
    lang.set(_VAL, language)
    package[part] = _serialize(tree)


def _child(parent: etree._Element, name: str, first: bool = False) -> etree._Element:
    """The ``name`` child of ``parent``, created (first or last) when missing."""
    child = parent.find(name, NS)
    if child is None:
        child = etree.Element(qn(name))
        if first:
            parent.insert(0, child)
        else:
            parent.append(child)
    return child


def _set_update_fields(package: dict[str, bytes]) -> None:
    """Add ``w:updateFields w:val="true"`` to the settings, creating the part if needed."""
    part = _related_part(
        package, DOCUMENT_PART, f"{_OFFICE_REL}/settings", "word/settings.xml", _SETTINGS_TYPE
    )
    if part in package:
        tree = parse_xml(package[part], part)
    else:
        tree = etree.ElementTree(etree.Element(qn("w:settings"), nsmap={"w": NS["w"]}))
    root = tree.getroot()
    update = root.find("w:updateFields", NS)
    if update is None:
        update = etree.Element(qn("w:updateFields"))
        later = next(
            (
                child
                for child in root
                if isinstance(child.tag, str)
                and (
                    etree.QName(child).namespace != NS["w"]
                    or etree.QName(child).localname in _AFTER_UPDATE_FIELDS
                )
            ),
            None,
        )
        if later is not None:
            later.addprevious(update)
        else:
            root.append(update)
    update.set(_VAL, "true")
    package[part] = _serialize(tree)


def _set_title(package: dict[str, bytes], title: str) -> None:
    part = _related_part(package, "", _CORE_REL, "docProps/core.xml", _CORE_TYPE)
    if part in package:
        tree = parse_xml(package[part], part)
    else:
        nsmap = {"cp": NS["cp"], "dc": NS["dc"]}
        tree = etree.ElementTree(etree.Element(qn("cp:coreProperties"), nsmap=nsmap))
    root = tree.getroot()
    el = root.find("dc:title", NS)
    if el is None:
        el = etree.SubElement(root, qn("dc:title"), nsmap={"dc": NS["dc"]})
    el.text = title
    package[part] = _serialize(tree)
