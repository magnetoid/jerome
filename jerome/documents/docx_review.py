"""Reads a DOCX that came back from the editor: base text and pending revisions per block.

The base view of a paragraph is its text with pending insertions removed and pending
deletions kept. Offsets are in code points of that text, the same as ``paragraph_runs``
(non-text objects are one placeholder each). Revisions the editor already accepted or
rejected are plain text or gone; only still-pending ones are listed.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from lxml import etree

from jerome.documents.docx_xml import (
    NS,
    is_block_text,
    iter_paragraph_runs,
    paragraph_revisions,
    parse_xml,
    qn,
    read_package,
)

CUSTOM_PROPERTIES_PART = "docProps/custom.xml"
BOOKMARK_PREFIX = "_jb"


@dataclass
class PendingRevision:
    """A pending change at ``[start, end)`` of the block's base text.

    A deletion spans text; an insertion has ``start == end`` and carries its ``text``.
    """

    start: int
    end: int
    kind: Literal["ins", "del"]
    author: str
    text: str


@dataclass
class ReviewedBlock:
    bookmark: str | None  # the first ``_jb`` bookmark starting in the paragraph
    text: str  # base view
    pending: list[PendingRevision]
    bookmarks: tuple[str, ...] = ()  # every ``_jb`` bookmark starting in the paragraph
    bookmark_ends: tuple[str, ...] = ()  # names of the ``_jb`` bookmarks ending in it

    def __post_init__(self) -> None:
        if self.bookmark and not self.bookmarks:
            self.bookmarks = (self.bookmark,)


@dataclass
class ReviewedDocument:
    blocks: list[ReviewedBlock]
    properties: dict[str, str]  # custom document properties


def read_reviewed_docx(data: bytes) -> ReviewedDocument:
    """Blocks of a reviewed DOCX in document order, and its custom properties.

    A paragraph is a block when it carries a ``_jb`` bookmark (even if the editor emptied
    it) or its base text is block text, as for ``iter_paragraphs``.
    """
    package = read_package(data)
    blocks = []
    end_ids: list[list[tuple[str, int]]] = []  # per block: its bookmarkEnd (tree, id) pairs
    names: dict[tuple[int, str], str] = {}  # (tree, bookmark id) -> ``_jb`` name
    for _key, p, tree, pt in iter_paragraph_runs(package, read=paragraph_revisions):
        starts = _bookmark_starts(p)
        for name, bid in starts:
            names.setdefault((id(tree), bid), name)
        ends = [(id(tree), i) for i in _bookmark_end_ids(p)]
        if not starts and not is_block_text(pt.text):
            continue
        pending = _merge([PendingRevision(**vars(r)) for r in pt.revisions])
        bookmarks = tuple(name for name, _ in starts)
        blocks.append(
            ReviewedBlock(bookmarks[0] if bookmarks else None, pt.text, pending, bookmarks)
        )
        end_ids.append(ends)
    for rb, ends in zip(blocks, end_ids, strict=True):
        rb.bookmark_ends = tuple(names[e] for e in ends if e in names)
    return ReviewedDocument(blocks, _custom_properties(package))


def _bookmark_starts(p: etree._Element) -> list[tuple[str, str]]:
    """``(name, id)`` of each ``_jb`` bookmark starting in the paragraph, in order."""
    return [
        (name, start.get(qn("w:id"), ""))
        for start in p.iter(qn("w:bookmarkStart"))
        if (name := start.get(qn("w:name"), "")).startswith(BOOKMARK_PREFIX)
    ]


def _bookmark_end_ids(p: etree._Element) -> list[str]:
    return [end.get(qn("w:id"), "") for end in p.iter(qn("w:bookmarkEnd"))]


def _merge(revisions: Iterable[PendingRevision]) -> list[PendingRevision]:
    """Join the pieces of one change: touching deletions, and insertions at one offset.

    The writer emits one ``w:del`` per container a change crosses (a hyperlink, say).
    Only revisions of the same kind and author are joined.
    """
    merged: list[PendingRevision] = []
    for r in revisions:
        for last in reversed(merged):
            if (last.kind, last.author) == (r.kind, r.author) and last.end == r.start:
                last.end = r.end
                last.text += r.text
                break
        else:
            merged.append(r)
    return merged


def _custom_properties(package: dict[str, bytes]) -> dict[str, str]:
    if CUSTOM_PROPERTIES_PART not in package:
        return {}
    root = parse_xml(package[CUSTOM_PROPERTIES_PART], CUSTOM_PROPERTIES_PART).getroot()
    return {
        prop.get("name", ""): "".join(prop.itertext()) for prop in root.iterfind("op:property", NS)
    }
