"""EPUB package and XHTML access shared by the EPUB reader and writer.

A block's text is its rendered text: runs of XML whitespace collapse to one space (as in a
browser), leading and trailing whitespace is dropped, a no-break space is kept, and every
non-text inline object becomes one PLACEHOLDER. CSS classes from stylesheets are NOT
resolved in this slice; marks come from elements and inline ``style`` attributes only.
"""

import posixpath
from collections.abc import Iterator
from dataclasses import dataclass
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
    "MAX_UNCOMPRESSED_BYTES",
    "ContentDocument",
    "EpubPackage",
    "EpubPiece",
    "ManifestItem",
    "NavPoint",
    "SpineItem",
    "block_kind",
    "element_text",
    "is_block_text",
    "iter_epub_blocks",
    "local_name",
    "parse_content_document",
    "read_container",
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


def _xml(data: bytes, part: str) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        return etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise UnsupportedDocumentError(f"{part} is not well-formed XML: {exc}") from exc


def _resolve(base: str, href: str) -> tuple[str, str]:
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
    container = _xml(files["META-INF/container.xml"], "META-INF/container.xml")
    rootfile = container.find(f".//{{{NS['c']}}}rootfile")
    opf_path = unquote(rootfile.get("full-path", "")) if rootfile is not None else ""
    if opf_path not in files:
        raise UnsupportedDocumentError("Not an EPUB file: the package document (OPF) is missing")
    opf = _xml(files[opf_path], opf_path)

    manifest: dict[str, ManifestItem] = {}
    for item in opf.iterfind(f"{{{NS['opf']}}}manifest/{{{NS['opf']}}}item"):
        path, _ = _resolve(opf_path, item.get("href", ""))
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

    def meta(tag: str) -> str | None:
        el = opf.find(f"{{{NS['opf']}}}metadata/{{{NS['dc']}}}{tag}")
        text = " ".join("".join(el.itertext()).split()) if el is not None else ""
        return text or None

    package = EpubPackage(
        opf_path, manifest, spine, [], files, list(files), meta("title"), meta("language")
    )
    package.nav_points = _read_toc(package, spine_el.get("toc") if spine_el is not None else None)
    return package


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
        path, anchor = _resolve(item.path, link.get("href"))
        points.append(NavPoint(_label(own), path, anchor))
    return points


def _ncx_points(package: EpubPackage, item: ManifestItem) -> list[NavPoint]:
    if item.path not in package.files:
        return []
    root = _xml(package.files[item.path], item.path)
    points = []
    for nav_point in root.iterfind(f"{{{NS['ncx']}}}navMap/{{{NS['ncx']}}}navPoint"):
        text = nav_point.find(f"{{{NS['ncx']}}}navLabel/{{{NS['ncx']}}}text")
        content = nav_point.find(f"{{{NS['ncx']}}}content")
        if content is None or not content.get("src"):
            continue
        path, anchor = _resolve(item.path, content.get("src"))
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
        root = lxml_html.document_fromstring(data)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError) as exc:
        raise UnsupportedDocumentError(f"{path} cannot be parsed: {exc}") from exc
    return ContentDocument(root, "html")


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
    - ``where == "self"``: a placeholder; ``element`` is the object (``img``, ``br``,
      ``svg``, a ``noteref`` link, ...) and ``spans`` is empty. Its tail is a separate piece.
    """

    start: int
    end: int
    element: etree._Element
    where: Literal["text", "tail", "self"]
    spans: tuple[tuple[int, int], ...] = ()

    @property
    def is_text(self) -> bool:
        return self.where != "self"

    @property
    def raw(self) -> str:
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
_IGNORED = {"script", "style", "wbr", "rt", "rp", "template", "noscript"}
_ELEMENT_MARKS = {
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


def _style_marks(style: str) -> list[MarkKind]:
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

    def walk(self, el: etree._Element) -> None:
        start = len(self.atoms)
        self.text(el.text, el, "text")
        for child in el:
            name = local_name(child)
            if name and name not in _IGNORED:
                if _is_placeholder(child):
                    self.atoms.append(_Atom(PLACEHOLDER, child, "self", -1))
                else:
                    self.walk(child)
            self.text(child.tail, child, "tail")
        end = len(self.atoms)
        name = local_name(el)
        if name in _ELEMENT_MARKS:
            self.ranges.append((start, end, _ELEMENT_MARKS[name], None))
        if name == "a" and el.get("href"):
            self.ranges.append((start, end, MarkKind.LINK, el.get("href")))
        for kind in _style_marks(el.get("style") or ""):
            self.ranges.append((start, end, kind, None))


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
        if atom.ch in _XML_WS:
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
        if atom.where == "self":
            pieces.append(EpubPiece(pos, pos + 1, atom.element, "self"))
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
    return ParagraphText(text, marks, pieces)  # type: ignore[arg-type]


def _trimmed(mark: Mark, text: str) -> Mark:
    start, end = mark.start, mark.end
    while start < end and text[start] == " ":
        start += 1
    while end > start and text[end - 1] == " ":
        end -= 1
    return Mark(start, end, mark.kind, mark.href)
