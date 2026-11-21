"""WordprocessingML access shared by the DOCX reader, the writer and the review reader.

A DOCX is handled as a dict of part name -> bytes. A paragraph's text is what Word shows:
runs inside hyperlinks, smart tags, content controls and insertions are text, deletions
are not, and every non-text inline object becomes one PLACEHOLDER character.
"""

import io
import posixpath
import zipfile
import zlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass

from lxml import etree

from jerome.documents.model import PLACEHOLDER, Mark, MarkKind
from jerome.errors import UnsupportedDocumentError

NS: dict[str, str] = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    # package-level parts
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "op": "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
}

DOCUMENT_PART = "word/document.xml"
NOTE_PARTS = ("word/footnotes.xml", "word/endnotes.xml")
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024  # a DOCX larger than this when unzipped is refused


def qn(name: str) -> str:
    """Clark notation for a prefixed name: ``qn("w:t")`` -> ``"{...main}t"``."""
    prefix, local = name.split(":")
    return f"{{{NS[prefix]}}}{local}"


# --- package --------------------------------------------------------------------------


def read_package(data: bytes) -> dict[str, bytes]:
    """Unzip a DOCX into {part name: bytes}, in zip order.

    Refuses archives whose declared uncompressed size exceeds MAX_UNCOMPRESSED_BYTES.
    That bound holds while reading, because zipfile stops each entry at its declared size
    (and fails its CRC check if the data was longer).
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
                raise UnsupportedDocumentError(
                    f"Document is too large: over {MAX_UNCOMPRESSED_BYTES // 2**20} MB unzipped"
                )
            return {info.filename: zf.read(info) for info in infos}
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError) as exc:
        raise UnsupportedDocumentError("Not a DOCX file: it is not a readable zip archive") from exc


def write_package(package: dict[str, bytes], original: bytes) -> bytes:
    """Zip ``package`` keeping the original's entry order and per-entry compression.

    Entries missing from ``package`` are dropped; new entries are appended, deflated.
    """
    out = io.BytesIO()
    written: set[str] = set()
    with zipfile.ZipFile(io.BytesIO(original)) as src, zipfile.ZipFile(out, "w") as dst:
        for old in src.infolist():
            if old.filename not in package or old.filename in written:
                continue
            info = zipfile.ZipInfo(old.filename, date_time=old.date_time)
            info.compress_type = old.compress_type
            info.external_attr = old.external_attr
            info.create_system = old.create_system
            dst.writestr(info, package[old.filename])
            written.add(old.filename)
        for name, data in package.items():
            if name not in written:
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                dst.writestr(info, data)
    return out.getvalue()


def parse_xml(data: bytes, part: str) -> etree._ElementTree:
    """Parse a package part without resolving entities or touching the network."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        return etree.parse(io.BytesIO(data), parser)
    except etree.XMLSyntaxError as exc:
        raise UnsupportedDocumentError(f"{part} is not well-formed XML: {exc}") from exc


def read_rels(package: Mapping[str, bytes], part: str) -> dict[str, str]:
    """Relationship id -> target for ``part`` (e.g. hyperlink r:id -> URL)."""
    folder, name = posixpath.split(part)
    rels_part = posixpath.join(folder, "_rels", f"{name}.rels")
    if rels_part not in package:
        return {}
    root = parse_xml(package[rels_part], rels_part).getroot()
    return {rel.get("Id"): rel.get("Target") for rel in root.iter(qn("pr:Relationship"))}


# --- paragraphs -----------------------------------------------------------------------

_SKIPPED_NOTE_TYPES = {"separator", "continuationSeparator", "continuationNotice"}


def iter_paragraphs(
    package: Mapping[str, bytes],
) -> Iterator[tuple[str, etree._Element, etree._ElementTree]]:
    """Yield ``(key, w:p, tree)`` for every proofreadable paragraph, in document order.

    Parts are read in this order: the document body (tables included, text boxes
    excluded), then footnotes, then endnotes (separator notes excluded). Headers,
    footers and comments live in other parts and are never read. The key is
    ``"<part>#<n>"``, n being the paragraph's 0-based index in that part's list.
    """
    for part in (DOCUMENT_PART, *NOTE_PARTS):
        if part not in package:
            continue
        tree = parse_xml(package[part], part)
        for n, p in enumerate(_part_paragraphs(tree.getroot())):
            yield f"{part}#{n}", p, tree


def _part_paragraphs(root: etree._Element) -> Iterator[etree._Element]:
    if root.tag == qn("w:document"):
        containers = root.iterchildren(qn("w:body"))
    else:
        containers = (
            note
            for note in root.iterchildren(qn("w:footnote"), qn("w:endnote"))
            if note.get(qn("w:type"), "normal") not in _SKIPPED_NOTE_TYPES
        )
    for container in containers:
        for p in container.iter(qn("w:p")):
            if next(p.iterancestors(qn("w:txbxContent")), None) is None:
                yield p


def iter_paragraph_runs(
    package: Mapping[str, bytes],
    read: Callable[..., "ParagraphText"] | None = None,
) -> Iterator[tuple[str, etree._Element, etree._ElementTree, "ParagraphText"]]:
    """``iter_paragraphs`` plus each paragraph's text, with fields carried across paragraphs.

    A complex field can span paragraphs (a table of contents, an index). The open-field
    depth flows from one paragraph to the next within a story (the body, or one note), so
    such a field is one placeholder per paragraph it touches: paragraphs wholly inside it
    read as a lone PLACEHOLDER (no text to proofread), and the closing paragraph starts
    with one. If a story ends with a field still open, the document is malformed; that
    story is re-read paragraph by paragraph so the field cannot swallow the rest of it.

    ``read(p, links, open_fields)`` reads one paragraph and defaults to ``paragraph_runs``.
    Callers needing another view of the text (e.g. revision-aware) pass their own, and
    must return the depth still open at the paragraph's end as ``open_fields``. Hyperlink
    ids are resolved through each part's relationships.
    """
    read = read or paragraph_runs
    links: dict[str, dict[str, str]] = {}
    story: list[tuple[str, etree._Element, etree._ElementTree]] = []
    story_root = None
    for key, p, tree in iter_paragraphs(package):
        part = key.partition("#")[0]
        if part not in links:
            links[part] = read_rels(package, part)
        root = next(p.iterancestors(qn("w:body"), qn("w:footnote"), qn("w:endnote")), None)
        if story and root is not story_root:
            yield from _read_story(story, read, links)
            story = []
        story_root = root
        story.append((key, p, tree))
    yield from _read_story(story, read, links)


def _read_story(
    story: list[tuple[str, etree._Element, etree._ElementTree]],
    read: Callable[..., "ParagraphText"],
    links: Mapping[str, Mapping[str, str]],
) -> list[tuple[str, etree._Element, etree._ElementTree, "ParagraphText"]]:
    out = []
    open_fields = 0
    for key, p, tree in story:
        pt = read(p, links[key.partition("#")[0]], open_fields)
        out.append((key, p, tree, pt))
        open_fields = pt.open_fields
    if open_fields:  # a field that never ends: no carrying over in this story
        out = [(key, p, tree, read(p, links[key.partition("#")[0]], 0)) for key, p, tree in story]
    return out


# --- paragraph text -------------------------------------------------------------------


@dataclass
class Piece:
    """The paragraph text offsets ``[start, end)`` and the XML that holds them.

    - Text: ``run`` is the ``w:r`` and ``node`` its ``w:t``; ``node.text[i]`` is
      character ``start + i``.
    - Placeholder inside a run (tab, break, symbol, drawing, note reference, ...):
      ``run`` is the ``w:r`` and ``node`` the placeholder element.
    - Placeholder outside a run (``w:fldSimple``, math, ``mc:AlternateContent``):
      ``run`` and ``node`` are that element.
    - Complex field (``fldChar`` begin ... end): ``run`` is the run holding the begin
      ``fldChar`` (``node``), ``last_run`` the run holding the end ``fldChar``, or the
      paragraph's last run when the field continues into the next paragraph. For the
      part of a field that began in an earlier paragraph, ``run`` and ``node`` are this
      paragraph's first run.

    ``last_run`` is ``run`` for everything but complex fields, so content inserted
    before a piece goes before ``run`` and content inserted after it goes after
    ``last_run``.
    """

    start: int
    end: int
    run: etree._Element
    node: etree._Element
    last_run: etree._Element | None = None

    def __post_init__(self) -> None:
        if self.last_run is None:
            self.last_run = self.run

    @property
    def is_text(self) -> bool:
        return self.node.tag == _T


@dataclass
class ParagraphText:
    text: str
    marks: list[Mark]
    pieces: list[Piece]
    open_fields: int = 0  # complex fields still open at the paragraph's end


def paragraph_runs(
    p: etree._Element, links: Mapping[str, str] | None = None, open_fields: int = 0
) -> ParagraphText:
    """Text, marks and offset map of a ``w:p``.

    ``links`` resolves ``w:hyperlink/@r:id`` to the LINK mark's href (see
    ``read_rels``); without it only anchor links (``#bookmark``) get an href, and a
    hyperlink with no href gets no LINK mark. Marks come from direct run properties
    only, and adjacent marks of the same kind are merged.

    ``open_fields`` is the depth of complex fields already open when the paragraph
    starts; ``iter_paragraph_runs`` carries it from paragraph to paragraph.
    """
    walker = _Walker(links or {})
    if open_fields:
        walker.field = _OpenField(None, None, None, depth=open_fields)
    walker.walk(p)
    still_open = walker.field.depth if walker.field is not None else 0
    walker.close_field()  # this paragraph's share of a field that continues
    return ParagraphText(
        "".join(walker.chunks), merge_marks(walker.marks), walker.pieces, still_open
    )


_R = qn("w:r")
_T = qn("w:t")
_HYPERLINK = qn("w:hyperlink")
_SDT = qn("w:sdt")
_SDT_CONTENT = qn("w:sdtContent")
_FLD_CHAR = qn("w:fldChar")
_FLD_CHAR_TYPE = qn("w:fldCharType")
_RPR = qn("w:rPr")
_VAL = qn("w:val")

# Containers whose runs are part of the visible text. Everything else at paragraph level
# holds no visible text: properties, bookmarks, proofErr, comment ranges, and deleted
# content (w:del, w:moveFrom).
_TRANSPARENT = {qn(f"w:{tag}") for tag in ("smartTag", "customXml", "ins", "moveTo", "dir", "bdo")}
# Paragraph-level elements that are one placeholder each.
_OBJECTS = {qn("w:fldSimple"), qn("m:oMath"), qn("m:oMathPara"), qn("mc:AlternateContent")}
# Run children that are one placeholder each.
_RUN_OBJECTS = {
    qn(f"w:{tag}")
    for tag in (
        "tab ptab br cr softHyphen noBreakHyphen sym drawing pict object"
        " footnoteReference endnoteReference footnoteRef endnoteRef"
    ).split()
} | {qn("mc:AlternateContent")}

_TOGGLE_MARKS = {
    qn("w:b"): MarkKind.BOLD,
    qn("w:i"): MarkKind.ITALIC,
    qn("w:smallCaps"): MarkKind.SMALLCAPS,
    qn("w:strike"): MarkKind.STRIKE,
    qn("w:dstrike"): MarkKind.STRIKE,
}
_OFF = {"0", "false", "off"}
_VERT_ALIGN_MARKS = {"superscript": MarkKind.SUPERSCRIPT, "subscript": MarkKind.SUBSCRIPT}


@dataclass
class _OpenField:
    run: etree._Element | None  # None until a run of a field continued from earlier is seen
    node: etree._Element | None
    last_run: etree._Element | None
    depth: int = 1


class _Walker:
    """Accumulates text, pieces and marks while walking a paragraph's content."""

    def __init__(self, links: Mapping[str, str]):
        self.links = links
        self.chunks: list[str] = []
        self.length = 0
        self.pieces: list[Piece] = []
        self.marks: list[Mark] = []
        self.href: str | None = None  # of the enclosing hyperlink
        self.field: _OpenField | None = None  # complex field being read

    def walk(self, container: etree._Element) -> None:
        for child in container:
            tag = child.tag
            if tag == _R:
                self.run(child)
            elif tag == _HYPERLINK:
                self.hyperlink(child)
            elif tag in _TRANSPARENT:
                self.walk(child)
            elif tag == _SDT:
                content = child.find(_SDT_CONTENT)
                if content is not None:
                    self.walk(content)
            elif tag in _OBJECTS:
                self.emit(PLACEHOLDER, child, child)

    def hyperlink(self, link: etree._Element) -> None:
        rid = link.get(qn("r:id"))
        href = self.links.get(rid) if rid else None
        if anchor := link.get(qn("w:anchor")):
            href = f"{href or ''}#{anchor}"
        outer, self.href = self.href, href
        self.walk(link)
        self.href = outer

    def run(self, r: etree._Element) -> None:
        if self.field is not None and self.field.run is None:
            self.field.run = self.field.node = r
        for child in r:
            tag = child.tag
            if tag == _FLD_CHAR:
                self.field_char(r, child)
            elif tag == _T:
                if child.text:
                    self.emit(child.text, r, child)
            elif tag in _RUN_OBJECTS:
                self.emit(PLACEHOLDER, r, child)
        if self.field is not None:
            self.field.last_run = r

    def field_char(self, r: etree._Element, char: etree._Element) -> None:
        kind = char.get(_FLD_CHAR_TYPE)
        if kind == "begin":
            if self.field is None:
                self.field = _OpenField(r, char, r)
            else:
                self.field.depth += 1
        elif kind == "end" and self.field is not None:
            self.field.depth -= 1
            if self.field.depth == 0:
                self.field.last_run = r
                self.close_field()
        # "separate", and an "end" without a "begin", add nothing.

    def close_field(self) -> None:
        field, self.field = self.field, None
        if field is not None and field.run is not None:
            self.emit(PLACEHOLDER, field.run, field.node, field.last_run)

    def emit(
        self,
        text: str,
        run: etree._Element,
        node: etree._Element,
        last_run: etree._Element | None = None,
    ) -> None:
        if self.field is not None:  # inside a complex field: part of its placeholder
            return
        start = self.length
        self.length += len(text)
        self.chunks.append(text)
        self.pieces.append(Piece(start, self.length, run, node, last_run))
        self.marks += [Mark(start, self.length, kind) for kind in _run_marks(run)]
        if self.href is not None:
            self.marks.append(Mark(start, self.length, MarkKind.LINK, self.href))


def _run_marks(run: etree._Element) -> list[MarkKind]:
    """Mark kinds set by the run's direct properties (styles are not followed)."""
    rpr = run.find(_RPR)
    if rpr is None:
        return []
    kinds = []
    for prop in rpr:
        if prop.tag in _TOGGLE_MARKS:
            if prop.get(_VAL) not in _OFF:
                kinds.append(_TOGGLE_MARKS[prop.tag])
        elif prop.tag == qn("w:u"):
            if prop.get(_VAL) != "none":
                kinds.append(MarkKind.UNDERLINE)
        elif prop.tag == qn("w:vertAlign"):
            if kind := _VERT_ALIGN_MARKS.get(prop.get(_VAL)):
                kinds.append(kind)
    return kinds


def merge_marks(marks: list[Mark]) -> list[Mark]:
    """Merge touching or overlapping marks of the same kind (and href); sort by position."""
    merged: list[Mark] = []
    for m in sorted(marks, key=lambda m: (m.kind, m.href or "", m.start)):
        last = merged[-1] if merged else None
        if last and (last.kind, last.href) == (m.kind, m.href) and m.start <= last.end:
            last.end = max(last.end, m.end)
        else:
            merged.append(Mark(m.start, m.end, m.kind, m.href))
    return sorted(merged, key=lambda m: (m.start, m.end, m.kind))
