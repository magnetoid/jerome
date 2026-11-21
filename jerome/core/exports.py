"""Exports: apply the recorded decisions to the original file.

Only blocks with accepted or edited suggestions are touched.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from jerome.core.books import get_book
from jerome.core.suggestions import spans_overlap
from jerome.documents.changes import Change
from jerome.documents.docx_writer import DocxWriteOptions, write_docx
from jerome.documents.epub_writer import write_epub
from jerome.errors import NotFound, UnsupportedDocumentError
from jerome.orm import Block, Chapter, ReviewExport, Suggestion
from jerome.storage import Storage
from jerome.types import Category, Severity

ExportFormat = Literal["docx-review", "docx-tracked", "docx-clean", "epub-clean"]

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EPUB_MEDIA_TYPE = "application/epub+zip"


@dataclass
class ExportFile:
    filename: str
    media_type: str
    data: bytes


@dataclass
class ReviewFilters:
    min_severity: Severity | None = None
    categories: list[Category] | None = None
    chapter_ids: list[int] | None = None


def recorded_changes(session: Session, book_id: int) -> dict[str, list[Change]]:
    """Accepted and edited suggestions as changes, keyed by block key, ordered by start."""
    rows = session.execute(
        select(Suggestion, Block.source_ref)
        .join(Block, Block.id == Suggestion.block_id)
        .where(Suggestion.book_id == book_id, Suggestion.status.in_(("accepted", "edited")))
        .order_by(Suggestion.start, Suggestion.end, Suggestion.id)
    ).all()
    changes: dict[str, list[Change]] = {}
    for s, key in rows:
        text = s.final_text if s.status == "edited" else s.replacement
        changes.setdefault(key, []).append(Change(s.start, s.end, text, ref=str(s.id)))
    return changes


def _exported_chapter_ids(session: Session, book_id: int, filters: ReviewFilters) -> list[int]:
    chapters = session.scalars(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.ordinal)
    ).all()
    if filters.chapter_ids is None:
        return [c.id for c in chapters if c.proofread]
    own = {c.id for c in chapters}
    unknown = [cid for cid in filters.chapter_ids if cid not in own]
    if unknown:
        raise NotFound(f"chapter {unknown[0]} does not belong to book {book_id}")
    wanted = set(filters.chapter_ids)
    return [c.id for c in chapters if c.id in wanted]


def _review_changes(
    session: Session, chapter_ids: list[int], filters: ReviewFilters
) -> tuple[dict[str, list[Change]], dict[str, str]]:
    """Changes keyed by block key, and the status of every suggestion included."""
    rows = session.execute(
        select(Suggestion, Block.source_ref)
        .join(Block, Block.id == Suggestion.block_id)
        .where(
            Block.chapter_id.in_(chapter_ids), Suggestion.status.in_(("open", "accepted", "edited"))
        )
        .order_by(Suggestion.start, Suggestion.end, Suggestion.id)
        .execution_options(populate_existing=True)
    ).all()
    floor = filters.min_severity.rank if filters.min_severity else 0
    wanted = {Category(c).value for c in filters.categories} if filters.categories else None
    by_block: dict[int, list[tuple[Suggestion, str]]] = {}
    for s, key in rows:
        if s.status == "open" and (
            Severity(s.severity).rank < floor or (wanted is not None and s.category not in wanted)
        ):
            continue
        by_block.setdefault(s.block_id, []).append((s, key))
    changes: dict[str, list[Change]] = {}
    statuses: dict[str, str] = {}
    for items in by_block.values():
        kept: list[tuple[Suggestion, str]] = []
        # recorded decisions win over open suggestions, then the earlier start
        for s, key in sorted(items, key=lambda it: (it[0].status == "open", it[0].start, it[0].id)):
            if not any(spans_overlap(s.start, s.end, k.start, k.end) for k, _ in kept):
                kept.append((s, key))
        for s, key in sorted(kept, key=lambda it: (it[0].start, it[0].end, it[0].id)):
            text = s.final_text if s.status == "edited" else s.replacement
            comment = f"[{s.category} · {s.severity}] {s.explanation}"
            changes.setdefault(key, []).append(
                Change(s.start, s.end, text, ref=str(s.id), comment=comment)
            )
            statuses[str(s.id)] = s.status
    return changes, statuses


def _safe_title(book) -> str:
    title = re.sub(r"[/\\\x00-\x1f\x7f]", "_", book.title or "").strip()
    return title or PurePath(book.original_filename or "").stem or "book"


def _review_copy(
    session: Session,
    storage: Storage,
    book,
    filters: ReviewFilters | None,
    actor: str | None,
    now: datetime | None,
) -> ExportFile:
    filters = filters or ReviewFilters()
    chapter_ids = _exported_chapter_ids(session, book.id, filters)
    blocks = session.scalars(
        select(Block)
        .join(Chapter, Chapter.id == Block.chapter_id)
        .where(Block.chapter_id.in_(chapter_ids))
        .order_by(Chapter.ordinal, Block.ordinal)
    ).all()
    changes, statuses = _review_changes(session, chapter_ids, filters)
    rx = ReviewExport(
        book_id=book.id,
        suggestion_statuses=statuses,
        block_ids=[b.id for b in blocks],
        filters={
            "min_severity": filters.min_severity.value if filters.min_severity else None,
            "categories": [Category(c).value for c in filters.categories]
            if filters.categories
            else None,
            "chapter_ids": filters.chapter_ids,
        },
        created_by=actor,
    )
    session.add(rx)
    session.flush()
    bookmarks = {b.source_ref: f"_jb{b.id}" for b in blocks if b.source_ref}
    options = DocxWriteOptions(
        "tracked",
        now or datetime.now(UTC),
        bookmarks,
        {"jerome_review_export_id": str(rx.id)},
    )
    data = write_docx(storage.read_original(book.id), changes, options)
    filename = f"{_safe_title(book)}-review-{rx.id}.docx"
    storage.save_export(book.id, filename, data)
    return ExportFile(filename, DOCX_MEDIA_TYPE, data)


def export_book(
    session: Session,
    storage: Storage,
    book_id: int,
    fmt: ExportFormat,
    *,
    filters: ReviewFilters | None = None,
    actor: str | None = None,
    now: datetime | None = None,
) -> ExportFile:
    book = get_book(session, book_id)
    wanted = {
        "epub-clean": "epub",
        "docx-clean": "docx",
        "docx-tracked": "docx",
        "docx-review": "docx",
    }.get(fmt)
    if wanted is None:
        raise UnsupportedDocumentError(f"unknown export format {fmt!r}")
    if book.format != wanted:
        raise UnsupportedDocumentError(
            f"{fmt} export needs a {wanted.upper()} book, but this book is {book.format.upper()}"
        )
    if fmt == "docx-review":
        return _review_copy(session, storage, book, filters, actor, now)
    original = storage.read_original(book_id)
    changes = recorded_changes(session, book_id)
    if fmt == "epub-clean":
        data, media_type = write_epub(original, changes), EPUB_MEDIA_TYPE
    else:
        mode = "clean" if fmt == "docx-clean" else "tracked"
        options = DocxWriteOptions(mode, now or datetime.now(UTC))
        data, media_type = write_docx(original, changes, options), DOCX_MEDIA_TYPE
    stem = PurePath(book.original_filename or "book").stem or "book"
    filename = f"{stem}-{fmt}.{wanted}"
    storage.save_export(book_id, filename, data)
    return ExportFile(filename, media_type, data)
