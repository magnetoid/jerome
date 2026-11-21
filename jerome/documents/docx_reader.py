"""Read a DOCX into chapters of proofreadable blocks."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from lxml import etree

from jerome.documents.docx_xml import (
    DOCUMENT_PART,
    NS,
    is_block_text,
    iter_paragraph_runs,
    parse_xml,
    qn,
    read_package,
)
from jerome.documents.model import (
    PLACEHOLDER,
    BlockKind,
    ParsedBlock,
    ParsedBook,
    ParsedChapter,
)
from jerome.errors import UnsupportedDocumentError

PART_WORDS = 5000
FRONT_MATTER_TITLE = "Front matter"

_TRACKED_CHANGES = tuple(qn(f"w:{tag}") for tag in ("ins", "del", "moveFrom", "moveTo"))
_QUOTE_STYLE_NAMES = {"quote", "intense quote"}


def read_docx(data: bytes) -> ParsedBook:
    """Parse a DOCX.

    Raises UnsupportedDocumentError for anything that is not a readable DOCX, and for
    documents with unresolved tracked changes.
    """
    package = read_package(data)
    if DOCUMENT_PART not in package:
        raise UnsupportedDocumentError(f"Not a Word document: {DOCUMENT_PART} is missing")
    styles = _Styles.load(package)

    body: list[tuple[ParsedBlock, int | None]] = []  # with the outline level of headings
    notes: list[ParsedBlock] = []
    checked: set[str] = set()  # parts already checked for tracked changes
    for key, p, tree, pt in iter_paragraph_runs(package):
        part = key.partition("#")[0]
        if part not in checked:
            _refuse_tracked_changes(tree)
            checked.add(part)
        if not is_block_text(pt.text):
            continue  # nothing to proofread (e.g. inside a TOC field); the key index is kept
        if part != DOCUMENT_PART:
            notes.append(ParsedBlock(key, BlockKind.FOOTNOTE, pt.text, pt.marks))
            continue
        kind, level = _body_kind(p, styles)
        body.append((ParsedBlock(key, kind, pt.text, pt.marks), level))

    return ParsedBook(
        format="docx",
        title=_core_title(package),
        language=styles.language,
        chapters=_chapters(body, notes),
    )


def _refuse_tracked_changes(tree: etree._ElementTree) -> None:
    if next(tree.getroot().iter(*_TRACKED_CHANGES), None) is not None:
        raise UnsupportedDocumentError(
            "Document contains unresolved tracked changes; accept or reject them in Word first"
        )


def _core_title(package: Mapping[str, bytes]) -> str | None:
    core = package.get("docProps/core.xml")
    if core is None:
        return None
    title = parse_xml(core, "docProps/core.xml").getroot().findtext("dc:title", None, NS)
    return (title or "").strip() or None


# --- block kinds ----------------------------------------------------------------------


def _body_kind(p: etree._Element, styles: "_Styles") -> tuple[BlockKind, int | None]:
    """The block kind, and the outline level when it is a heading."""
    if next(p.iterancestors(qn("w:tc")), None) is not None:
        return BlockKind.TABLE_CELL, None
    level = styles.outline_level(p)
    if level is not None:
        return BlockKind.HEADING, level
    if styles.is_list_item(p):
        return BlockKind.LIST_ITEM, None
    if styles.is_quote(p):
        return BlockKind.QUOTE, None
    return BlockKind.PARAGRAPH, None


@dataclass
class _Style:
    name: str
    based_on: str | None
    ppr: etree._Element | None


class _Styles:
    """Paragraph styles from word/styles.xml, resolved along their w:basedOn chain."""

    def __init__(self) -> None:
        self.by_id: dict[str, _Style] = {}
        self.default_id: str | None = None
        self.language: str | None = None

    @classmethod
    def load(cls, package: Mapping[str, bytes]) -> "_Styles":
        styles = cls()
        data = package.get("word/styles.xml")
        if data is None:
            return styles
        root = parse_xml(data, "word/styles.xml").getroot()
        for el in root.iterchildren(qn("w:style")):
            style_id = el.get(qn("w:styleId"))
            if el.get(qn("w:type")) != "paragraph" or style_id is None:
                continue
            styles.by_id[style_id] = _Style(
                name=(_val(el.find("w:name", NS)) or "").lower(),
                based_on=_val(el.find("w:basedOn", NS)),
                ppr=el.find("w:pPr", NS),
            )
            if el.get(qn("w:default")) in ("1", "true"):
                styles.default_id = style_id
        styles.language = _val(root.find("w:docDefaults/w:rPrDefault/w:rPr/w:lang", NS))
        return styles

    def chain(self, p: etree._Element) -> Iterator[_Style]:
        """The paragraph's style, then the styles it is based on."""
        style_id = _val(p.find("w:pPr/w:pStyle", NS))
        if style_id not in self.by_id:
            style_id = self.default_id
        seen: set[str] = set()
        while style_id in self.by_id and style_id not in seen:
            seen.add(style_id)
            style = self.by_id[style_id]
            yield style
            style_id = style.based_on

    def property(self, p: etree._Element, path: str) -> etree._Element | None:
        """A paragraph property (e.g. ``w:outlineLvl``), set directly or by a style."""
        direct = p.find(f"w:pPr/{path}", NS)
        if direct is not None:
            return direct
        for style in self.chain(p):
            if style.ppr is not None and (found := style.ppr.find(path, NS)) is not None:
                return found
        return None

    def outline_level(self, p: etree._Element) -> int | None:
        """0-8 for headings; None for body text (no level, or level 9)."""
        value = _val(self.property(p, "w:outlineLvl"))
        level = int(value) if value is not None and value.isdigit() else None
        return level if level is not None and level <= 8 else None

    def is_list_item(self, p: etree._Element) -> bool:
        num_pr = self.property(p, "w:numPr")
        num_id = _val(num_pr.find("w:numId", NS)) if num_pr is not None else None
        return num_id not in (None, "0")

    def is_quote(self, p: etree._Element) -> bool:
        return any(style.name in _QUOTE_STYLE_NAMES for style in self.chain(p))


def _val(el: etree._Element | None) -> str | None:
    return el.get(qn("w:val")) if el is not None else None


# --- chapters -------------------------------------------------------------------------


def _chapters(
    body: list[tuple[ParsedBlock, int | None]], notes: list[ParsedBlock]
) -> list[ParsedChapter]:
    """Split at outline level 0, or into ~PART_WORDS parts without such headings.

    Notes are read after the body, so they go in the last chapter.
    """
    if any(level == 0 for _, level in body):
        chapters = _split_at_headings(body)
    else:
        chapters = _split_into_parts([block for block, _ in body])
    if notes:
        if not chapters:
            chapters.append(_chapter("Part 1", notes[0]))
        chapters[-1].blocks += notes
    return chapters


def _split_at_headings(body: list[tuple[ParsedBlock, int | None]]) -> list[ParsedChapter]:
    chapters: list[ParsedChapter] = []
    for block, level in body:
        if level == 0:
            chapters.append(_chapter(" ".join(block.text.replace(PLACEHOLDER, " ").split()), block))
        elif not chapters:
            chapters.append(_chapter(FRONT_MATTER_TITLE, block))
        chapters[-1].blocks.append(block)
    return chapters


def _split_into_parts(blocks: list[ParsedBlock]) -> list[ParsedChapter]:
    chapters: list[ParsedChapter] = []
    words = 0
    for block in blocks:
        if not chapters or words >= PART_WORDS:
            chapters.append(_chapter(f"Part {len(chapters) + 1}", block))
            words = 0
        chapters[-1].blocks.append(block)
        words += len(block.text.split())
    return chapters


def _chapter(title: str, first: ParsedBlock) -> ParsedChapter:
    return ParsedChapter(title=title, proofread=True, source_ref=first.key)
