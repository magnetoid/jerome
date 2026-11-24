"""Read an EPUB into chapters of blocks.

Chapters follow the table of contents (EPUB 3 nav, else the EPUB 2 NCX): a chapter runs
from its TOC target (file plus optional anchor) to the next entry's target, following
spine order. Documents before the first TOC target, ``linear="no"`` items and documents
typed cover/titlepage/copyright-page/toc/frontmatter/backmatter are not proofread. Without
a usable TOC every spine document is a chapter.
"""

from dataclasses import dataclass

from lxml import etree

from jerome.documents.epub_xml import (
    EpubPackage,
    block_kind,
    content_documents,
    element_text,
    epub_type,
    is_block_text,
    iter_epub_blocks,
    local_name,
    parse_content_document,
    read_container,
)
from jerome.documents.model import BlockKind, ParsedBlock, ParsedBook, ParsedChapter
from jerome.errors import UnsupportedDocumentError

_NON_PROOFREAD_TYPES = {"cover", "titlepage", "copyright-page", "toc", "frontmatter", "backmatter"}


@dataclass
class _Doc:
    path: str
    flagged: bool  # linear="no" or typed as front/back matter
    root: etree._Element
    blocks: list[tuple[int, ParsedBlock, etree._Element]]  # (n, block, element), non-empty only
    start_of: dict[str, int]  # anchor -> index in ``blocks`` where a chapter starting there begins


def read_epub(data: bytes) -> ParsedBook:
    package = read_container(data)
    docs = _read_docs(package)
    if not docs:
        raise UnsupportedDocumentError("Not a usable EPUB: its spine has no content documents")
    book = ParsedBook("epub", package.title, package.language)
    book.authors, book.publisher = list(package.creators), package.publisher
    if package.date and package.date[:4].isdigit():
        book.year = int(package.date[:4])
    cuts = _cuts(package, docs)
    if cuts:
        book.chapters = _toc_chapters(docs, cuts)
    else:
        for doc in docs:
            book.chapters.append(_chapter(doc, 0, len(doc.blocks), None, len(book.chapters) + 1))
    book.chapters = [c for c in book.chapters if c.blocks]
    return book


def _read_docs(package: EpubPackage) -> list[_Doc]:
    docs = []
    for item in content_documents(package):
        root = parse_content_document(package.files[item.path], item.path).root
        blocks = []
        for n, el in iter_epub_blocks(root):
            text = element_text(el)
            if is_block_text(text.text):
                blocks.append(
                    (n, ParsedBlock(f"{item.path}#{n}", block_kind(el), text.text, text.marks), el)
                )
        order = {el: i for i, el in enumerate(root.iter())}
        positions = {id(el): order[el] for _, _, el in blocks}
        flagged = not item.linear or _typed_non_proofread(root)
        docs.append(
            _Doc(item.path, flagged, root, blocks, _anchor_starts(root, order, blocks, positions))
        )
    return docs


def _typed_non_proofread(root: etree._Element) -> bool:
    body = next((el for el in root.iter() if local_name(el) == "body"), None)
    if body is None:
        return False
    return any(epub_type(el) & _NON_PROOFREAD_TYPES for el in (body, *body))


def _anchor_starts(root, order, blocks, positions) -> dict[str, int]:
    """For every id in the document: the index of the first block at or after it."""
    block_els = [el for _, _, el in blocks]
    starts = {}
    for el in root.iter():
        anchor = el.get("id")
        if not anchor or anchor in starts:
            continue
        ancestors = {id(a) for a in el.iterancestors()} | {id(el)}
        idx = next(
            (
                i
                for i, b in enumerate(block_els)
                if id(b) in ancestors or positions[id(b)] > order[el]
            ),
            len(block_els),
        )
        starts[anchor] = idx
    return starts


def _cuts(package: EpubPackage, docs: list[_Doc]) -> list[tuple[int, int, str, str]]:
    """TOC entries as (doc index, block index, title, anchor), in reading order."""
    index = {d.path: i for i, d in enumerate(docs)}
    cuts: list[tuple[int, int, str, str]] = []
    for point in package.nav_points:
        if point.path not in index:
            continue
        di = index[point.path]
        bi = docs[di].start_of.get(point.anchor, 0) if point.anchor else 0
        cuts.append((di, bi, point.title, point.anchor))
    cuts.sort(key=lambda c: (c[0], c[1]))
    unique: list[tuple[int, int, str, str]] = []
    for cut in cuts:
        if not unique or cut[:2] != unique[-1][:2]:
            unique.append(cut)
    return unique


def _toc_chapters(docs: list[_Doc], cuts: list[tuple[int, int, str, str]]) -> list[ParsedChapter]:
    chapters: list[ParsedChapter] = []
    first_doc, first_block = cuts[0][0], cuts[0][1]
    for doc in docs[:first_doc]:  # before the first TOC target
        chapters.append(_chapter(doc, 0, len(doc.blocks), None, len(chapters) + 1, proofread=False))
    if first_block:  # the part of the first target's file before its anchor
        doc = docs[first_doc]
        chapters.append(_chapter(doc, 0, first_block, None, len(chapters) + 1, proofread=False))

    for k, (di, bi, title, anchor) in enumerate(cuts):
        end_doc, end_block = (
            (cuts[k + 1][0], cuts[k + 1][1]) if k + 1 < len(cuts) else (len(docs), 0)
        )
        chapter: ParsedChapter | None = None
        for dj in range(di, min(end_doc, len(docs) - 1) + 1):
            doc = docs[dj]
            lo = bi if dj == di else 0
            hi = end_block if dj == end_doc else len(doc.blocks)
            if dj == end_doc and hi == 0:
                break
            flagged_here = doc.flagged
            if chapter is None:
                chapter = ParsedChapter(
                    title or _first_heading(doc, lo) or f"Section {len(chapters) + 1}",
                    not flagged_here,
                    f"{doc.path}#{anchor}",
                )
                chapters.append(chapter)
                chapter_flagged = flagged_here
            elif flagged_here != chapter_flagged:  # front/back matter inside a chapter's run
                chapter = ParsedChapter(chapters[-1].title, not flagged_here, f"{doc.path}#")
                chapters.append(chapter)
                chapter_flagged = flagged_here
            chapter.blocks += [b for _, b, _ in doc.blocks[lo:hi]]
    return chapters


def _first_heading(doc: _Doc, lo: int = 0) -> str | None:
    return next((b.text for _, b, _ in doc.blocks[lo:] if b.kind == BlockKind.HEADING), None)


def _chapter(
    doc: _Doc, lo: int, hi: int, title: str | None, number: int, proofread: bool | None = None
) -> ParsedChapter:
    return ParsedChapter(
        title or _first_heading(doc, lo) or f"Section {number}",
        (not doc.flagged) if proofread is None else proofread,
        f"{doc.path}#",
        [b for _, b, _ in doc.blocks[lo:hi]],
    )
