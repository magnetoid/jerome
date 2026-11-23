"""Write a translated EPUB on the structure of the original.

Each translated block element is rebuilt in place. Its text and inline elements are
replaced by the translation's text inside mark elements built from its marks, and its
placeholders (images, line breaks, note references, ...) are moved, whole, to where the
translation puts them. The block element itself and its attributes stay.

- A mark element copies the block's first source element that makes that mark: its tag
  and attributes (``<b class="x">``, ``<span class="sc" style="font-variant: small-caps">``),
  less any ``style`` declarations that would make another mark. A source element whose
  tag makes another mark (``<i style="font-weight: bold">`` for bold) is no template.
- A link copies a source ``a`` with the same href, by link index: the k-th link to an href
  takes the k-th source link to it (the first when there are fewer).
- With no source element for a kind, a plain one is made: ``b``, ``i``, ``u``, ``sup``,
  ``sub``, ``s``, or ``<span style="font-variant: small-caps">``.
- Marks the block element itself makes (``<p style="font-style: italic">``) cover the whole
  block and are not written inside it: the block reads back with them over all of its
  text, as the original looks, whatever the translation's marks say for them.
- A ``lang`` or ``xml:lang`` of the block element becomes the target language.
- An ``id`` or ``name`` goes on the first copy of a source element only. Those of source
  inline elements the translation does not reuse are kept on empty ``a`` anchors at the
  start of the block, so links into the block still land there.

Documents that hold a translated block, and the nav document when TOC labels change it, get
the target language on their root ``html`` (``lang`` and ``xml:lang``) and are serialized
the way ``write_epub`` does. Every other document is in its source language still and keeps
its bytes. The OPF gets ``dc:language`` and, when given, ``dc:title``.

``read_epub`` of the result gives each translated block's text and marks exactly, so the
proofreading writer then works on it like on any other EPUB.
"""

import re
from collections.abc import Mapping
from typing import Literal, cast

from lxml import etree

from jerome.documents.changes import NOT_XML
from jerome.documents.docx_xml import ParagraphText, merge_marks, write_package
from jerome.documents.epub_xml import (
    ELEMENT_MARKS,
    IGNORED,
    NS,
    ContentDocument,
    EpubPackage,
    EpubPiece,
    epub_type,
    find_blocks,
    is_block_text,
    local_name,
    own_marks,
    parse_content_document,
    parse_xml_root,
    read_container,
    resolve_href,
    serialize_document,
    style_marks,
)
from jerome.documents.model import PLACEHOLDER, Mark, MarkKind
from jerome.documents.translated import TranslatedBlock
from jerome.errors import InvalidChangeError

__all__ = ["TranslatedEpubError", "write_translated_epub"]

_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_OPF = f"{{{NS['opf']}}}"
_DC = f"{{{NS['dc']}}}"
_NCX = f"{{{NS['ncx']}}}"
_NCX_TYPE = "application/x-dtbncx+xml"

# Mark elements nest in this order, outermost first, when several open at one offset.
_NESTING = (
    MarkKind.LINK,
    MarkKind.BOLD,
    MarkKind.ITALIC,
    MarkKind.UNDERLINE,
    MarkKind.SMALLCAPS,
    MarkKind.SUPERSCRIPT,
    MarkKind.SUBSCRIPT,
    MarkKind.STRIKE,
)
_PLAIN = {
    MarkKind.BOLD: "b",
    MarkKind.ITALIC: "i",
    MarkKind.UNDERLINE: "u",
    MarkKind.SUPERSCRIPT: "sup",
    MarkKind.SUBSCRIPT: "sub",
    MarkKind.STRIKE: "s",
    MarkKind.SMALLCAPS: "span",
}
_SMALLCAPS_STYLE = "font-variant: small-caps"
# Text the reader would not give back as it is: XML whitespace it collapses or drops.
_NOT_RENDERED = re.compile(r"[\t\n\r]|  |^ | $")

_Item = tuple[MarkKind, str | None]  # a mark kind, with its href for a link


def write_translated_epub(
    original: bytes,
    blocks: Mapping[str, TranslatedBlock],
    *,
    language: str,
    title: str | None,
    toc_labels: Mapping[str, str],
) -> bytes:
    """The EPUB ``original`` with each block in ``blocks`` (by ``ParsedBlock.key``) translated.

    ``language`` (e.g. ``de-DE``) becomes the OPF's ``dc:language`` and the ``lang`` and
    ``xml:lang`` of the root ``html`` of every document with a translated block. ``title``,
    when given and not blank, replaces ``dc:title``; None or a blank title keeps the
    original.

    ``toc_labels`` maps a TOC target to its new label, keyed exactly like
    ``ParsedChapter.source_ref`` of a TOC-based chapter: the target's zip path (resolved
    against the TOC file, percent-decoded), ``#``, and its anchor, empty for a whole file
    (``OEBPS/ch1.xhtml#``, ``OEBPS/book.xhtml#c2``); a bare path is accepted for a whole
    file too. It is applied to the text of the nav document's TOC links and to the NCX
    ``navLabel/text``, at every depth. Entries with no mapping, or a blank label, keep their
    label, and so does a nav link inside a translated block (the TOC page in the spine),
    which the block's translation already gives.

    Raises InvalidChangeError for a language, title or label with characters XML cannot
    carry, and TranslatedEpubError, listing every failing key with its reason, for keys
    that name no block and for translations that cannot be written: placeholders that do
    not match the source's, marks out of range, linking to an href the source block has no
    link to, or overlapping links to different hrefs, characters XML cannot carry,
    whitespace the reader would collapse (leading, trailing, doubled spaces, tabs,
    newlines), or no text besides placeholders. Nothing is written unless every block can
    be.
    """
    if title is not None and not title.strip():
        title = None
    toc_labels = {target: label for target, label in toc_labels.items() if label.strip()}
    _check_metadata(language, title, toc_labels)
    package = read_container(original)
    found, docs = find_blocks(package, set(blocks))
    problems = {key: "no block with this key" for key in blocks if key not in found}
    rebuilds: list[tuple[_Rebuild, list[int]]] = []
    for key, (el, text) in found.items():  # check them all before changing any
        rebuild = _Rebuild(key, el, text, language, blocks[key])
        try:
            rebuilds.append((rebuild, rebuild.check()))
        except _Refused as exc:
            problems[key] = str(exc)
    if problems:
        raise TranslatedEpubError({key: problems[key] for key in blocks if key in problems})

    dirty: set[str] = set()
    for rebuild, order in rebuilds:
        rebuild.write(order)
        dirty.add(rebuild.key.partition("#")[0])

    opf = parse_xml_root(package.files[package.opf_path], package.opf_path)
    keyed = {el for el, _ in found.values()}
    if nav := _relabel_nav(package, docs, keyed, toc_labels):
        dirty.add(nav)
    _relabel_ncx(package, opf, toc_labels, language)
    for path in dirty:
        _set_language(docs[path], language)
        package.files[path] = serialize_document(docs[path], package.files[path], path)
    _set_metadata(package, opf, language, title)
    return write_package(package.files, original)


def _check_metadata(language: str, title: str | None, toc_labels: Mapping[str, str]) -> None:
    if not language.strip() or NOT_XML.search(language):
        raise InvalidChangeError(f"language {language!r} cannot be written")
    if title is not None and NOT_XML.search(title):
        raise InvalidChangeError("title contains characters not allowed in XML")
    for target, label in toc_labels.items():
        if NOT_XML.search(label):
            raise InvalidChangeError(f"TOC label for {target!r} has characters not allowed in XML")


class TranslatedEpubError(InvalidChangeError):
    """Translations that cannot be written. ``problems`` maps each failing key, as passed to
    ``write_translated_epub``, to the reason, in the caller's order."""

    def __init__(self, problems: Mapping[str, str]):
        self.problems = dict(problems)
        super().__init__("; ".join(f"{key}: {why}" for key, why in self.problems.items()))


class _Refused(Exception):
    """One block's translation cannot be written (the message says why)."""


# --- one block ------------------------------------------------------------------------


class _Rebuild:
    def __init__(
        self,
        key: str,
        block: etree._Element,
        text: ParagraphText,
        language: str,
        translation: TranslatedBlock,
    ):
        self.key = key
        self.block = block
        self.language = language
        self.translation = translation
        pieces = cast(list[EpubPiece], text.pieces)
        self.placeholders = [p.element for p in pieces if p.where == "self"]
        self.inline = _inline_elements(block, set(self.placeholders))
        self.implied = set(own_marks(block))  # marks the block element makes over all of it
        self.used: set[etree._Element] = set()  # source elements copied already
        self.templates: dict[MarkKind, etree._Element | None] = {}

    def write(self, order: list[int]) -> None:
        """Rebuild the block; ``order`` is what ``check`` returned."""
        block = self.translation
        marks = [
            m for m in block.marks if m.start < m.end and (m.kind, _href(m)) not in self.implied
        ]
        links = self._link_sources(marks)
        for el in self.placeholders:
            el.tail = None
            el.getparent().remove(el)
        self.block.text = None
        for child in list(self.block):
            self.block.remove(child)
        self._fill(block.text, marks, order, links)
        self._keep_ids()
        for name in ("lang", _XML_LANG, "xml:lang"):  # the block's own, if it has one
            if self.block.get(name) is not None:
                self.block.set(name, self.language)

    def check(self) -> list[int]:
        """The placeholder order, after checking the translation can be written; raises
        _Refused, saying why, when it cannot."""
        block = self.translation
        text, count = block.text, len(self.placeholders)
        if NOT_XML.search(text):
            raise _Refused("translation has characters not allowed in XML")
        if _NOT_RENDERED.search(text):
            raise _Refused("translation has whitespace the EPUB collapses")
        if not is_block_text(text):
            raise _Refused("translation has no text")
        if text.count(PLACEHOLDER) != count:
            raise _Refused(
                f"translation has {text.count(PLACEHOLDER)} placeholders, the source {count}"
            )
        order = list(block.placeholders) if block.placeholders is not None else list(range(count))
        if sorted(order) != list(range(count)):
            raise _Refused(f"placeholder order {order} is not a permutation")
        hrefs = {
            href for el in self.inline for kind, href in own_marks(el) if kind == MarkKind.LINK
        }
        for m in block.marks:
            if not 0 <= m.start <= m.end <= len(text):
                raise _Refused(f"mark ({m.start}, {m.end}) is out of range")
            if m.kind == MarkKind.LINK and m.href not in hrefs:
                raise _Refused(f"the source has no link to {m.href!r}")
        links = [m for m in block.marks if m.kind == MarkKind.LINK and m.start < m.end]
        for i, a in enumerate(links):
            for b in links[i + 1 :]:
                if a.href != b.href and a.start < b.end and b.start < a.end:
                    raise _Refused(f"links to {a.href!r} and {b.href!r} overlap")
        return order

    # --- templates

    def _template(self, kind: MarkKind) -> etree._Element | None:
        """The first source element that makes ``kind`` and whose tag makes no other mark."""
        if kind not in self.templates:
            self.templates[kind] = next(
                (
                    el
                    for el in self.inline
                    if local_name(el) != "a"
                    and (kind, None) in own_marks(el)
                    and ELEMENT_MARKS.get(local_name(el), kind) == kind
                ),
                None,
            )
        return self.templates[kind]

    def _link_sources(self, marks: list[Mark]) -> list[tuple[int, int, str, etree._Element]]:
        """Each link run of the translation, ``(start, end, href, source a)``: the k-th run
        to an href takes the k-th source link to it, or the first."""
        sources: dict[str, list[etree._Element]] = {}
        for el in self.inline:
            if local_name(el) == "a" and el.get("href"):
                sources.setdefault(el.get("href"), []).append(el)
        runs = []
        seen: dict[str, int] = {}
        for run in merge_marks([m for m in marks if m.kind == MarkKind.LINK]):
            href = run.href or ""
            k = seen[href] = seen.get(href, -1) + 1
            candidates = sources[href]
            runs.append((run.start, run.end, href, candidates[k if k < len(candidates) else 0]))
        return runs

    def _open(
        self, parent: etree._Element, kind: MarkKind, source: etree._Element | None
    ) -> etree._Element:
        """A new mark element for ``kind`` at the end of ``parent``."""
        if source is None:
            attrs = {"style": _SMALLCAPS_STYLE} if kind == MarkKind.SMALLCAPS else {}
            return etree.SubElement(parent, _tag(self.block, _PLAIN[kind]), attrs)
        attrs = dict(source.attrib)
        style = _style_keeping(attrs.pop("style", None), kind)
        if style:
            attrs["style"] = style
        if source in self.used:
            attrs.pop("id", None)
            attrs.pop("name", None)
        self.used.add(source)
        return etree.SubElement(parent, source.tag, attrs)

    # --- the translation

    def _fill(
        self,
        text: str,
        marks: list[Mark],
        order: list[int],
        links: list[tuple[int, int, str, etree._Element]],
    ) -> None:
        """Write ``text`` into the emptied block: one span per constant set of marks, its
        mark elements kept open from the span before while they still apply."""
        cuts = {0, len(text)} | {m.start for m in marks} | {m.end for m in marks}
        for i, ch in enumerate(text):
            if ch == PLACEHOLDER:
                cuts |= {i, i + 1}
        bounds = sorted(cuts)
        stack: list[tuple[_Item, etree._Element]] = []
        slot = 0
        for start, end in zip(bounds, bounds[1:], strict=False):
            want = {(m.kind, _href(m)) for m in marks if m.start <= start < m.end}
            keep = 0
            while keep < len(stack) and stack[keep][0] in want:
                keep += 1
            del stack[keep:]
            for kind, href in sorted(
                want - {item for item, _ in stack}, key=lambda i: (_NESTING.index(i[0]), i[1] or "")
            ):
                if kind == MarkKind.LINK:
                    source = next(s for a, b, h, s in links if h == href and a <= start < b)
                else:
                    source = self._template(kind)
                parent = stack[-1][1] if stack else self.block
                stack.append(((kind, href), self._open(parent, kind, source)))
            parent = stack[-1][1] if stack else self.block
            if text[start] == PLACEHOLDER:
                parent.append(self.placeholders[order[slot]])
                slot += 1
            else:
                _append_text(parent, text[start:end])

    def _keep_ids(self) -> None:
        """Empty ``a`` anchors at the block's start for the ids and names of source inline
        elements that no copy carries."""
        lost = [
            el
            for el in self.inline
            if el not in self.used and (el.get("id") is not None or el.get("name") is not None)
        ]
        for el in reversed(lost):
            attrs = {k: el.get(k) for k in ("id", "name") if el.get(k) is not None}
            anchor = etree.SubElement(self.block, _tag(self.block, "a"), attrs)
            anchor.tail, self.block.text = self.block.text, None
            self.block.insert(0, anchor)


def _inline_elements(block: etree._Element, placeholders: set) -> list[etree._Element]:
    """The inline elements the reader walks in ``block``, in document order: not
    placeholders, nor what they hold, nor ignored elements (``script``, ``rt``, ...)."""
    out: list[etree._Element] = []

    def walk(el: etree._Element) -> None:
        for child in el:
            if not isinstance(child.tag, str) or child in placeholders:
                continue
            if local_name(child) in IGNORED:
                continue
            out.append(child)
            walk(child)

    walk(block)
    return out


def _href(m: Mark) -> str | None:
    return m.href if m.kind == MarkKind.LINK else None


def _tag(block: etree._Element, name: str) -> str:
    """``name`` in the namespace of the block's element (none for an HTML-parsed one)."""
    namespace = etree.QName(block).namespace
    return f"{{{namespace}}}{name}" if namespace else name


def _style_keeping(style: str | None, kind: MarkKind) -> str | None:
    """``style`` without the declarations that make a mark other than ``kind``."""
    if style is None:
        return None
    kept = [d for d in style.split(";") if d.strip() and set(style_marks(d)) <= {kind}]
    return ";".join(kept).strip() or None


def _append_text(parent: etree._Element, text: str) -> None:
    if len(parent):
        last = parent[-1]
        last.tail = (last.tail or "") + text
    else:
        parent.text = (parent.text or "") + text


# --- table of contents ----------------------------------------------------------------


def _label_for(labels: Mapping[str, str], path: str, anchor: str) -> str | None:
    label = labels.get(f"{path}#{anchor}")
    if label is None and not anchor:
        label = labels.get(path)
    return label


def _set_label(el: etree._Element, label: str) -> None:
    for child in list(el):
        el.remove(child)
    el.text = label


def _relabel_nav(
    package: EpubPackage,
    docs: dict[str, ContentDocument],
    keyed: set[etree._Element],
    labels: Mapping[str, str],
) -> str | None:
    """Relabel the links of the nav document's TOC; its path if any changed."""
    item = next((m for m in package.manifest.values() if "nav" in m.properties), None)
    if not labels or item is None or item.path not in package.files:
        return None
    doc = docs.get(item.path) or parse_content_document(package.files[item.path], item.path)
    nav = next(
        (el for el in doc.root.iter() if local_name(el) == "nav" and "toc" in epub_type(el)), None
    )
    changed = False
    for a in nav.iter() if nav is not None else ():
        if local_name(a) != "a" or not a.get("href"):
            continue
        if any(ancestor in keyed for ancestor in a.iterancestors()):
            continue  # inside a translated block: the translation gives its text
        label = _label_for(labels, *resolve_href(item.path, a.get("href")))
        if label is not None:
            _set_label(a, label)
            changed = True
    if not changed:
        return None
    docs[item.path] = doc
    return item.path


def _relabel_ncx(
    package: EpubPackage, opf: etree._Element, labels: Mapping[str, str], language: str
) -> None:
    """Relabel the NCX's navPoints; it is rewritten only if one changed."""
    spine = opf.find(f"{_OPF}spine")
    item = package.manifest.get((spine.get("toc") if spine is not None else None) or "")
    if item is None:
        item = next((m for m in package.manifest.values() if m.media_type == _NCX_TYPE), None)
    if not labels or item is None or item.path not in package.files:
        return
    root = parse_xml_root(package.files[item.path], item.path)
    nav_map = root.find(f"{_NCX}navMap")
    changed = False
    for point in nav_map.iter(f"{_NCX}navPoint") if nav_map is not None else ():
        content = point.find(f"{_NCX}content")
        text = point.find(f"{_NCX}navLabel/{_NCX}text")
        if content is None or text is None or not content.get("src"):
            continue
        label = _label_for(labels, *resolve_href(item.path, content.get("src")))
        if label is not None:
            _set_label(text, label)
            changed = True
    if changed:
        if root.get(_XML_LANG) is not None:
            root.set(_XML_LANG, language)
        package.files[item.path] = serialize_document(
            ContentDocument(root, "xml"), package.files[item.path], item.path
        )


# --- metadata -------------------------------------------------------------------------


def _set_language(doc: ContentDocument, language: str) -> None:
    root = doc.root
    if local_name(root) != "html":
        return
    root.set("lang", language)
    root.set(_XML_LANG if doc.parser == "xml" else "xml:lang", language)


def _set_metadata(
    package: EpubPackage, opf: etree._Element, language: str, title: str | None
) -> None:
    """Write ``dc:language`` and, when given, ``dc:title`` (the first of each)."""
    metadata = opf.find(f"{_OPF}metadata")
    if metadata is None:
        metadata = etree.Element(f"{_OPF}metadata")
        opf.insert(0, metadata)
    values: list[tuple[Literal["language", "title"], str | None]] = [
        ("language", language),
        ("title", title),
    ]
    for name, value in values:
        if value is None:
            continue
        el = metadata.find(f"{_DC}{name}")
        if el is None:
            el = etree.SubElement(metadata, f"{_DC}{name}", nsmap={"dc": NS["dc"]})
        _set_label(el, value)
    path = package.opf_path
    package.files[path] = serialize_document(ContentDocument(opf, "xml"), package.files[path], path)
