"""Exports: apply the recorded decisions to the original file.

Only blocks with accepted or edited suggestions are touched.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from jerome.core.books import get_book
from jerome.documents.changes import Change
from jerome.documents.docx_writer import DocxWriteOptions, write_docx
from jerome.documents.epub_writer import write_epub
from jerome.errors import UnsupportedDocumentError
from jerome.orm import Block, Suggestion
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
    if fmt == "docx-review":
        raise NotImplementedError("docx-review arrives with the Word round trip")
    wanted = "epub" if fmt == "epub-clean" else "docx"
    if book.format != wanted:
        raise UnsupportedDocumentError(
            f"{fmt} export needs a {wanted.upper()} book, but this book is {book.format.upper()}"
        )
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
