"""EPUB package and XHTML access shared by the EPUB reader and writer.

A block's text is its rendered text: runs of XML whitespace collapse to one space (as in a
browser), leading and trailing whitespace is dropped, a no-break space is kept, and every
non-text inline object becomes one PLACEHOLDER. CSS classes from stylesheets are NOT
resolved in this slice; marks come from elements and inline ``style`` attributes only.
"""

import codecs
import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.entities import html5
from typing import Literal
from urllib.parse import unquote, urldefrag

from lxml import etree
from lxml import html as lxml_html

from jerome.documents.docx_xml import (
    MAX_UNCOMPRESSED_BYTES,
    ParagraphText,
    merge_marks,
    read_package,
)
from jerome.documents.model import PLACEHOLDER, BlockKind, Mark, MarkKind
from jerome.errors import UnsupportedDocumentError

__all__ = [
    "CONTENT_TYPES",
    "ELEMENT_MARKS",
    "IGNORED",
    "MAX_UNCOMPRESSED_BYTES",
    "NS",
    "ContentDocument",
    "EpubPackage",
    "EpubPiece",
    "ManifestItem",
    "NavPoint",
    "SpineItem",
    "block_kind",
    "content_documents",
    "element_text",
    "epub_type",
    "find_blocks",
    "is_block_text",
    "iter_epub_blocks",
    "local_name",
    "own_marks",
    "parse_content_document",
    "parse_xml_root",
    "read_container",
    "resolve_href",
    "serialize_document",
    "style_marks",
]

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
}
_XML_WS = " \t\n\r"


def local_name(el: etree._Element) -> str:
    """Tag without namespace, lower-cased; '' for comments and processing instructions."""
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag.rpartition("}")[2].lower()


def epub_type(el: etree._Element) -> set[str]:
    """Tokens of ``epub:type`` (also when an HTML parser kept the prefix literally)."""
    value = el.get(f"{{{NS['epub']}}}type") or el.get("epub:type") or ""
    return set(value.split())


# --- package --------------------------------------------------------------------------


@dataclass
class ManifestItem:
    id: str
    path: str  # full zip entry path
    media_type: str
    properties: frozenset[str]


@dataclass
class SpineItem:
    idref: str
    path: str  # full zip entry path ('' when the idref is not in the manifest)
    linear: bool


@dataclass
class NavPoint:
    """A top-level table-of-contents entry."""

    title: str
    path: str
    anchor: str  # '' for the whole file


@dataclass
class EpubPackage:
    """The unzipped package. ``order`` is the zip entry order (``mimetype`` first)."""

    opf_path: str
    manifest: dict[str, ManifestItem]
    spine: list[SpineItem]
    nav_points: list[NavPoint]
    files: dict[str, bytes]
    order: list[str]
    title: str | None = None
    language: str | None = None
    creators: list[str] = field(default_factory=list)
    publisher: str | None = None
    date: str | None = None
    # epub:type (cover, titlepage, copyright-page, toc, ...) -> document path, from the nav
    # landmarks and the EPUB 2 guide (guide types mapped to their epub:type names)
    landmarks: dict[str, str] = field(default_factory=dict)
    cover_item: str | None = None  # manifest id of the cover image


def parse_xml_root(data: bytes, part: str) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        return etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise UnsupportedDocumentError(f"{part} is not well-formed XML: {exc}") from exc


def resolve_href(base: str, href: str) -> tuple[str, str]:
    """(zip path, anchor) of ``href`` relative to the file ``base``."""
    target, anchor = urldefrag(href)
    target = unquote(target)
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), target)) if target else base
    return path, unquote(anchor)


def read_container(data: bytes) -> EpubPackage:
    """Unzip an EPUB and read its OPF: manifest, spine, metadata and table of contents."""
    files = read_package(data, kind="EPUB")
    if "META-INF/container.xml" not in files:
        raise UnsupportedDocumentError("Not an EPUB file: META-INF/container.xml is missing")
    container = parse_xml_root(files["META-INF/container.xml"], "META-INF/container.xml")
    rootfile = container.find(f".//{{{NS['c']}}}rootfile")
    opf_path = unquote(rootfile.get("full-path", "")) if rootfile is not None else ""
    if opf_path not in files:
        raise UnsupportedDocumentError("Not an EPUB file: the package document (OPF) is missing")
    opf = parse_xml_root(files[opf_path], opf_path)

    manifest: dict[str, ManifestItem] = {}
    for item in opf.iterfind(f"{{{NS['opf']}}}manifest/{{{NS['opf']}}}item"):
        path, _ = resolve_href(opf_path, item.get("href", ""))
        manifest[item.get("id", "")] = ManifestItem(
            item.get("id", ""),
            path,
            item.get("media-type", ""),
            frozenset(item.get("properties", "").split()),
        )
    spine_el = opf.find(f"{{{NS['opf']}}}spine")
    spine = [
        SpineItem(
            ref.get("idref", ""),
            manifest[ref.get("idref", "")].path if ref.get("idref") in manifest else "",
            ref.get("linear", "yes").lower() != "no",
        )
        for ref in (spine_el.iterfind(f"{{{NS['opf']}}}itemref") if spine_el is not None else ())
    ]
    if not spine:
        raise UnsupportedDocumentError("Not a usable EPUB: its spine is empty")

    def metas(tag: str) -> list[str]:
        found = opf.iterfind(f"{{{NS['opf']}}}metadata/{{{NS['dc']}}}{tag}")
        texts = (" ".join("".join(el.itertext()).split()) for el in found)
        return [t for t in texts if t]

    def meta(tag: str) -> str | None:
        return next(iter(metas(tag)), None)

    package = EpubPackage(
        opf_path, manifest, spine, [], files, list(files), meta("title"), meta("language")
    )
    package.creators, package.publisher, package.date = (
        metas("creator"),
        meta("publisher"),
        meta("date"),
    )
    package.nav_points = _read_toc(package, spine_el.get("toc") if spine_el is not None else None)
    package.landmarks = _guide(opf, opf_path) | _landmarks(package)
    package.cover_item = _cover_item(opf, manifest)
    return package


_GUIDE_TYPES = {"title-page": "titlepage", "copyright-page": "copyright-page", "toc": "toc"}


def _guide(opf: etree._Element, opf_path: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for ref in opf.iterfind(f"{{{NS['opf']}}}guide/{{{NS['opf']}}}reference"):
        kind = (ref.get("type") or "").lower()
        kind = _GUIDE_TYPES.get(kind, kind)
        if kind and ref.get("href"):
            found.setdefault(kind, resolve_href(opf_path, ref.get("href", ""))[0])
    return found


def _landmarks(package: "EpubPackage") -> dict[str, str]:
    found: dict[str, str] = {}
    for item in (m for m in package.manifest.values() if "nav" in m.properties):
        if item.path not in package.files:
            continue
        root = parse_content_document(package.files[item.path], item.path).root
        for nav in (el for el in root.iter() if local_name(el) == "nav"):
            if "landmarks" not in epub_type(nav):
                continue
            for a in (el for el in nav.iter() if local_name(el) == "a" and el.get("href")):
                for kind in epub_type(a):
                    found.setdefault(kind, resolve_href(item.path, a.get("href", ""))[0])
    return found


def _cover_item(opf: etree._Element, manifest: dict[str, "ManifestItem"]) -> str | None:
    for item in manifest.values():
        if "cover-image" in item.properties:
            return item.id
    for meta in opf.iterfind(f"{{{NS['opf']}}}metadata/{{{NS['opf']}}}meta"):
        if meta.get("name") == "cover" and meta.get("content") in manifest:
            return meta.get("content")
    return None


CONTENT_TYPES = frozenset({"application/xhtml+xml", "text/html"})


def content_documents(package: EpubPackage) -> list[SpineItem]:
    """The spine items that are XHTML documents present in the package, in spine order:
    the documents whose blocks are read, and so the only ones block keys can name."""
    media_types = {m.path: m.media_type for m in package.manifest.values()}
    return [
        item
        for item in package.spine
        if media_types.get(item.path) in CONTENT_TYPES and item.path in package.files
    ]


def _read_toc(package: EpubPackage, ncx_id: str | None) -> list[NavPoint]:
    nav_items = [m for m in package.manifest.values() if "nav" in m.properties]
    points = _nav_points(package, nav_items[0]) if nav_items else []
    if points:
        return points
    ncx = package.manifest.get(ncx_id or "")
    if ncx is None:
        ncx = next(
            (m for m in package.manifest.values() if m.media_type == "application/x-dtbncx+xml"),
            None,
        )
    return _ncx_points(package, ncx) if ncx else []


def _label(el: etree._Element) -> str:
    return " ".join("".join(el.itertext()).split())


def _nav_points(package: EpubPackage, item: ManifestItem) -> list[NavPoint]:
    if item.path not in package.files:
        return []
    root = parse_content_document(package.files[item.path], item.path).root
    nav = next(
        (el for el in root.iter() if local_name(el) == "nav" and "toc" in epub_type(el)), None
    )
    ol = next((c for c in nav if local_name(c) == "ol"), None) if nav is not None else None
    points = []
    for li in ol if ol is not None else ():
        if local_name(li) != "li":
            continue
        own = next((c for c in li if local_name(c) in ("a", "span")), None)
        link = own if own is not None and local_name(own) == "a" else None
        if link is None or not link.get("href"):
            link = next((a for a in li.iter() if local_name(a) == "a" and a.get("href")), None)
        if link is None or own is None:
            continue
        path, anchor = resolve_href(item.path, link.get("href"))
        points.append(NavPoint(_label(own), path, anchor))
    return points


def _ncx_points(package: EpubPackage, item: ManifestItem) -> list[NavPoint]:
    if item.path not in package.files:
        return []
    root = parse_xml_root(package.files[item.path], item.path)
    points = []
    for nav_point in root.iterfind(f"{{{NS['ncx']}}}navMap/{{{NS['ncx']}}}navPoint"):
        text = nav_point.find(f"{{{NS['ncx']}}}navLabel/{{{NS['ncx']}}}text")
        content = nav_point.find(f"{{{NS['ncx']}}}content")
        if content is None or not content.get("src"):
            continue
        path, anchor = resolve_href(item.path, content.get("src"))
        points.append(NavPoint(_label(text) if text is not None else "", path, anchor))
    return points


# --- content documents ----------------------------------------------------------------


@dataclass
class ContentDocument:
    """A parsed XHTML document. ``parser`` is "xml", or "html" when the file was not
    well-formed XML and lxml.html was used instead; a writer must serialize it the same
    way (``etree.tostring(root, method=parser)``)."""

    root: etree._Element
    parser: Literal["xml", "html"]


def parse_content_document(data: bytes, path: str) -> ContentDocument:
    """Parse XHTML as XML (no entity resolution, no network); fall back to lxml.html."""
    try:
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
        return ContentDocument(root, "xml")
    except etree.XMLSyntaxError:
        pass
    try:
        parser = lxml_html.HTMLParser(encoding=_sniff_encoding(data))
        root = lxml_html.document_fromstring(data, parser=parser)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError) as exc:
        raise UnsupportedDocumentError(f"{path} cannot be parsed: {exc}") from exc
    return ContentDocument(root, "html")


_BOMS = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
_XML_ENCODING = re.compile(rb"""^\s*<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._-]+)["']""")


def _sniff_encoding(data: bytes) -> str:
    """Encoding of a content document: a BOM, else the XML declaration, else UTF-8."""
    for bom, name in _BOMS:
        if data.startswith(bom):
            return name
    if m := _XML_ENCODING.match(data):
        name = m[1].decode("ascii")
        try:
            return codecs.lookup(name).name
        except LookupError:
            pass
    return "utf-8"


# --- blocks ---------------------------------------------------------------------------

_HEADINGS = {f"h{n}" for n in range(1, 7)}
_BLOCKS = _HEADINGS | {
    "p", "li", "blockquote", "td", "th", "div", "dt", "dd", "figcaption", "caption",
    "ul", "ol", "dl", "table", "tr", "thead", "tbody", "tfoot", "section", "article",
    "aside", "figure", "nav", "header", "footer", "main", "body", "pre",
}  # fmt: skip
# Elements that hold text of their own and are blocks when they contain no other block.
_LEAVES = _HEADINGS | {
    "p",
    "li",
    "blockquote",
    "td",
    "th",
    "div",
    "dt",
    "dd",
    "figcaption",
    "caption",
}


def iter_epub_blocks(root: etree._Element) -> Iterator[tuple[int, etree._Element]]:
    """Yield ``(n, element)`` for every leaf block in document order, n counting from 0.

    A leaf block is a ``p``, ``h1``-``h6``, ``li``, ``blockquote``, ``td``/``th``, ``div``
    (and ``dt``/``dd``/``figcaption``/``caption``) with no block element inside it. Empty
    blocks are yielded too; the caller decides which are blocks, so n stays stable.
    """
    n = 0
    for el in root.iter():
        name = local_name(el)
        if name in _LEAVES and not any(local_name(d) in _BLOCKS for d in el.iterdescendants()):
            yield n, el
            n += 1


def block_kind(el: etree._Element) -> BlockKind:
    name = local_name(el)
    if name in _HEADINGS:
        return BlockKind.HEADING
    if name == "li":
        return BlockKind.LIST_ITEM
    if name in ("td", "th"):
        return BlockKind.TABLE_CELL
    if name == "blockquote" or any(local_name(a) == "blockquote" for a in el.iterancestors()):
        return BlockKind.QUOTE
    return BlockKind.PARAGRAPH


def is_block_text(text: str) -> bool:
    """Whether a block with this text is a block: more than placeholders and whitespace."""
    return bool(text.replace(PLACEHOLDER, "").strip())


# --- block text -----------------------------------------------------------------------


@dataclass
class EpubPiece:
    """Block text offsets ``[start, end)`` and the XML node that holds them.

    - ``where == "text"``: the characters are in ``element.text``; ``"tail"``: in
      ``element.tail`` (``raw`` returns that string).
    - ``spans[i]`` is the ``(raw_start, raw_end)`` slice of ``raw`` that produced block
      character ``start + i``. For ordinary characters the slice is one character; a
      collapsed run of whitespace is one output space whose slice is the whole run in
      that node. Raw characters not in any span (leading/trailing whitespace of the block,
      whitespace dropped after another space) are not in the block text.
    - ``where == "entity"``: the characters are an entity reference node (``&nbsp;``,
      ``&mdash;``; ``element.name`` is its name, ``raw`` the characters it stands for).
      ``spans`` is empty; the node is not text. Written back untouched it stays an entity
      reference. A change that covers it must replace the node with text (its tail text
      moves along with the replacement).
    - ``where == "self"``: a placeholder; ``element`` is the object (``img``, ``br``,
      ``svg``, a ``noteref`` link, ...) and ``spans`` is empty. Its tail is a separate piece.
    - ``collapsed``: when a text piece ends in a space whose whitespace run goes on into
      later nodes (``big\\n</b>  world``), those later characters, as ``(element, where,
      raw_start, raw_end)`` slices of their nodes, in document order. They are in no span:
      they collapsed into this space. A writer that removes this space, or puts text right
      after it, must remove them too, or they would show as a space of their own.
    """

    start: int
    end: int
    element: etree._Element
    where: Literal["text", "tail", "entity", "self"]
    spans: tuple[tuple[int, int], ...] = ()
    collapsed: tuple[tuple[etree._Element, Literal["text", "tail"], int, int], ...] = ()

    @property
    def is_text(self) -> bool:
        return self.where in ("text", "tail")

    @property
    def raw(self) -> str:
        if self.where == "entity":
            return html5.get(f"{self.element.name};", "")
        return (getattr(self.element, self.where) or "") if self.is_text else ""


@dataclass
class _Atom:
    ch: str
    element: etree._Element
    where: str
    raw_index: int  # index in the node's string; -1 for a placeholder


_INLINE = {
    "a", "span", "b", "strong", "i", "em", "cite", "u", "sup", "sub", "s", "strike", "del",
    "ins", "small", "big", "abbr", "acronym", "code", "q", "mark", "font", "dfn", "kbd",
    "samp", "var", "time", "bdi", "bdo", "tt", "label", "ruby", "rb", "data", "nobr",
}  # fmt: skip
IGNORED = {"script", "style", "wbr", "rt", "rp", "template", "noscript"}
ELEMENT_MARKS = {
    "b": MarkKind.BOLD,
    "strong": MarkKind.BOLD,
    "i": MarkKind.ITALIC,
    "em": MarkKind.ITALIC,
    "cite": MarkKind.ITALIC,
    "u": MarkKind.UNDERLINE,
    "sup": MarkKind.SUPERSCRIPT,
    "sub": MarkKind.SUBSCRIPT,
    "s": MarkKind.STRIKE,
    "strike": MarkKind.STRIKE,
    "del": MarkKind.STRIKE,
}


def style_marks(style: str) -> list[MarkKind]:
    kinds = []
    for declaration in style.split(";"):
        prop, _, value = declaration.partition(":")
        prop, value = prop.strip().lower(), value.strip().lower().replace("!important", "").strip()
        if prop == "font-weight" and (
            value in ("bold", "bolder") or (value.isdigit() and int(value) >= 600)
        ):
            kinds.append(MarkKind.BOLD)
        elif prop == "font-style" and value in ("italic", "oblique"):
            kinds.append(MarkKind.ITALIC)
        elif prop == "font-variant" and "small-caps" in value:
            kinds.append(MarkKind.SMALLCAPS)
    return kinds


def own_marks(el: etree._Element) -> list[tuple[MarkKind, str | None]]:
    """The marks ``el`` itself makes over its content: from its name (``b``, ``i``, ...),
    as a link (``a`` with an ``href``, which is the mark's href), and from its ``style``."""
    name = local_name(el)
    marks: list[tuple[MarkKind, str | None]] = []
    if name in ELEMENT_MARKS:
        marks.append((ELEMENT_MARKS[name], None))
    if name == "a" and el.get("href"):
        marks.append((MarkKind.LINK, el.get("href")))
    marks += [(kind, None) for kind in style_marks(el.get("style") or "")]
    return marks


def _is_placeholder(el: etree._Element) -> bool:
    name = local_name(el)
    if name == "a":
        return "noteref" in epub_type(el)
    return name not in _INLINE


class _Collector:
    def __init__(self) -> None:
        self.atoms: list[_Atom] = []
        self.ranges: list[tuple[int, int, MarkKind, str | None]] = []

    def text(self, s: str | None, el: etree._Element, where: str) -> None:
        for i, ch in enumerate(s or ""):
            self.atoms.append(_Atom(ch, el, where, i))

    def entity(self, el: etree._Element) -> None:
        """A named entity reference (kept as a node because entities are not resolved)."""
        chars = html5.get(f"{el.name};")
        if chars is None:  # unknown: not text we can read, so one placeholder
            self.atoms.append(_Atom(PLACEHOLDER, el, "self", -1))
        else:
            self.atoms += [_Atom(ch, el, "entity", i) for i, ch in enumerate(chars)]

    def walk(self, el: etree._Element) -> None:
        start = len(self.atoms)
        self.text(el.text, el, "text")
        for child in el:
            name = local_name(child)
            if child.tag is etree.Entity:
                self.entity(child)
            elif name and name not in IGNORED:
                if _is_placeholder(child):
                    self.atoms.append(_Atom(PLACEHOLDER, child, "self", -1))
                else:
                    self.walk(child)
            self.text(child.tail, child, "tail")
        end = len(self.atoms)
        for kind, href in own_marks(el):
            self.ranges.append((start, end, kind, href))


def element_text(el: etree._Element) -> ParagraphText:
    """Text, marks and offset map of a block element (``el.tail`` is not part of it)."""
    c = _Collector()
    c.walk(el)
    atoms = c.atoms

    chars: list[str] = []
    out_atom: list[int] = []  # output character -> index of the atom that produced it
    before = [0] * (len(atoms) + 1)  # before[i] = output characters produced by atoms[:i]
    prev_space = True  # nothing yet: leading whitespace is dropped
    for i, atom in enumerate(atoms):
        if atom.ch in _XML_WS and atom.where != "entity":
            if not prev_space:
                chars.append(" ")
                out_atom.append(i)
            prev_space = True
        else:
            chars.append(atom.ch)
            out_atom.append(i)
            prev_space = False
        before[i + 1] = len(chars)
    if chars and chars[-1] == " ":
        chars.pop()
        out_atom.pop()
    text = "".join(chars)
    before = [min(b, len(text)) for b in before]

    marks = merge_marks([Mark(before[s], before[e], k, h) for s, e, k, h in c.ranges])
    marks = [_trimmed(m, text) for m in marks]
    marks = [m for m in marks if m.start < m.end]

    pieces: list[EpubPiece] = []
    for pos, ai in enumerate(out_atom):
        atom = atoms[ai]
        if atom.where in ("self", "entity"):
            last = pieces[-1] if pieces else None
            if atom.where == "entity" and last and last.element is atom.element:
                last.end = pos + 1  # a multi-character entity
            else:
                pieces.append(EpubPiece(pos, pos + 1, atom.element, atom.where))
            continue
        raw_end = atom.raw_index + 1
        if atom.ch in _XML_WS:  # the collapsed run, within this node
            raw = getattr(atom.element, atom.where)
            while raw_end < len(raw) and raw[raw_end] in _XML_WS:
                raw_end += 1
        span = (atom.raw_index, raw_end)
        last = pieces[-1] if pieces else None
        if last and last.is_text and last.element is atom.element and last.where == atom.where:
            last.end = pos + 1
            last.spans += (span,)
        else:
            pieces.append(EpubPiece(pos, pos + 1, atom.element, atom.where, (span,)))

    for piece in pieces:
        if piece.is_text and piece.end < len(text) and text[piece.end - 1] == " ":
            # Atoms between this space and the next character are dropped whitespace; the
            # ones in this space's own node are in its span already.
            space = atoms[out_atom[piece.end - 1]]
            dropped: list = []
            for atom in atoms[out_atom[piece.end - 1] + 1 : out_atom[piece.end]]:
                if atom.element is space.element and atom.where == space.where:
                    continue
                last = dropped[-1] if dropped else None
                if last and last[0] is atom.element and last[1] == atom.where:
                    dropped[-1] = (*last[:3], atom.raw_index + 1)
                else:
                    dropped.append((atom.element, atom.where, atom.raw_index, atom.raw_index + 1))
            piece.collapsed = tuple(dropped)
    return ParagraphText(text, marks, pieces)  # type: ignore[arg-type]


def _trimmed(mark: Mark, text: str) -> Mark:
    start, end = mark.start, mark.end
    while start < end and text[start] == " ":
        start += 1
    while end > start and text[end - 1] == " ":
        end -= 1
    return Mark(start, end, mark.kind, mark.href)


# --- writing -------------------------------------------------------------------------


def find_blocks(
    package: EpubPackage, keys: set[str]
) -> tuple[dict[str, tuple[etree._Element, ParagraphText]], dict[str, ContentDocument]]:
    """The ``(element, text)`` of each block in ``keys`` that names one, and the parsed
    documents holding them. Keys that name no block are left out."""
    paths = {key.partition("#")[0] for key in keys}
    docs: dict[str, ContentDocument] = {}
    blocks: dict[str, tuple[etree._Element, ParagraphText]] = {}
    for item in content_documents(package):
        if item.path not in paths or item.path in docs:
            continue
        doc = docs[item.path] = parse_content_document(package.files[item.path], item.path)
        for n, el in iter_epub_blocks(doc.root):
            key = f"{item.path}#{n}"
            if key in keys and is_block_text((text := element_text(el)).text):
                blocks[key] = (el, text)
    return blocks, docs


_BOM_CODECS = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_XHTML_NS = "http://www.w3.org/1999/xhtml"
_VOID = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)  # HTML elements that never have content or an end tag
_COMMENT_OR_PI = re.compile(r"<!--.*?-->|<\?.*?\?>", re.S)
_DOCTYPE = re.compile("<!DOCTYPE", re.I)


def serialize_document(doc: ContentDocument, original: bytes, path: str) -> bytes:
    """The changed document: the original's bytes up to its root element, then the root
    and what follows it serialized as parsed, then the original's trailing whitespace.

    Keeps the original's encoding, and gives empty non-void XHTML elements an end tag
    (``<a id="n"></a>``) when the document was parsed as XML."""
    codec = _codec(doc, original, path)
    if doc.parser == "xml":
        _keep_end_tags(doc.root)
    tostring = lxml_html.tostring if doc.parser == "html" else etree.tostring
    nodes = (doc.root, *doc.root.itersiblings())
    body = "".join(tostring(node, encoding="unicode") for node in nodes)
    try:
        text = original.decode(codec, "surrogateescape")  # a BOM stays, as U+FEFF
        start, end = _root_start(text), len(text.rstrip(_XML_WS))
        return (
            text[:start].encode(codec, "surrogateescape")
            + body.encode(codec, "xmlcharrefreplace")  # character references if need be
            + text[max(start, end) :].encode(codec, "surrogateescape")
        )
    except UnicodeError as exc:
        raise UnsupportedDocumentError(f"{path} cannot be written back as {codec}") from exc


def _keep_end_tags(root: etree._Element) -> None:
    """Make empty non-void XHTML elements serialize as ``<a id="n"></a>``, not ``<a/>``.

    Reading systems with an HTML parser read ``<a/>``, ``<div/>`` or ``<script/>`` as a
    start tag and swallow what follows; some show a page with ``<title/>`` blank. The tree
    cannot tell how the original wrote them, so every one gets an end tag. Void elements
    (``<br/>``), and SVG and MathML elements, stay self-closed.
    """
    for el in root.iter(etree.Element):
        if el.text is None and len(el) == 0:
            name = etree.QName(el)
            if name.namespace in (_XHTML_NS, None) and name.localname.lower() not in _VOID:
                el.text = ""


def _codec(doc: ContentDocument, original: bytes, path: str) -> str:
    """The original's encoding: from its BOM, else the one it was parsed with."""
    for bom, codec in _BOM_CODECS:
        if original.startswith(bom):
            return codec
    name = doc.root.getroottree().docinfo.encoding or "utf-8"
    try:
        return codecs.lookup(name).name
    except LookupError as exc:
        raise UnsupportedDocumentError(f"{path} is in an unknown encoding: {name}") from exc


def _root_start(text: str) -> int:
    """Where the root element starts: after a BOM, and the XML declaration, comments,
    processing instructions, doctype and whitespace before the root."""
    pos = 1 if text.startswith("﻿") else 0
    while True:
        while pos < len(text) and text[pos] in _XML_WS:
            pos += 1
        if m := _COMMENT_OR_PI.match(text, pos):
            pos = m.end()
        elif _DOCTYPE.match(text, pos) and (end := _doctype_end(text, pos)):
            pos = end
        else:
            return pos


def _doctype_end(text: str, pos: int) -> int | None:
    """The end of the doctype at ``pos``, or None if it has none. Quoted strings, and
    comments and processing instructions in the internal subset, may hold ``>`` or ``]``."""
    i, subset = pos + len("<!DOCTYPE"), False
    while i < len(text):
        ch = text[i]
        if ch in "\"'":
            i = text.find(ch, i + 1)
            if i < 0:
                return None
        elif subset and (m := _COMMENT_OR_PI.match(text, i)):
            i = m.end() - 1
        elif ch == "[":
            subset = True
        elif ch == "]":
            subset = False
        elif ch == ">" and not subset:
            return i + 1
        i += 1
    return None
