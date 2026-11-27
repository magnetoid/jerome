"""The book preview: a chapter's paragraphs in their three states, and the file the book
was made from."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from jerome.api.deps import SessionDep, get_storage
from jerome.api.routes_exports import content_disposition
from jerome.core.books import get_book
from jerome.core.exports import safe_title
from jerome.core.preview import chapter_preview
from jerome.core.translation import ensure_not_source
from jerome.errors import NotFound
from jerome.orm import Book
from jerome.storage import Storage

router = APIRouter()

MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".epub": "application/epub+zip",
    ".pdf": "application/pdf",
}


@router.get("/chapters/{chapter_id}/preview")
def preview(
    chapter_id: int,
    session: SessionDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    return chapter_preview(session, chapter_id, offset, limit)


@router.get("/books/{book_id}/original")
def original(
    book_id: int,
    session: SessionDep,
    storage: Annotated[Storage, Depends(get_storage)],
    pdf: Annotated[bool, Query()] = False,
):
    """The uploaded file (a translated book's is its source book's), or with ``pdf`` the PDF
    a book was imported from."""
    book = get_book(session, book_id)
    ensure_not_source(session, book_id)
    holders = [book.id] + ([book.source_book_id] if book.source_book_id else [])
    if pdf:
        for holder in holders:
            if storage.has_source_pdf(holder):
                return _file(book, storage.read_source_pdf(holder), ".pdf")
        raise NotFound(f"source PDF of book {book_id}")
    for holder in holders:
        suffix = storage.original_suffix(holder)
        if suffix is not None:
            return _file(book, storage.read_original(holder), suffix)
    raise NotFound(f"original file of book {book_id}")


def _file(book: Book, data: bytes, suffix: str) -> Response:
    return Response(
        data,
        media_type=MEDIA_TYPES.get(suffix, "application/octet-stream"),
        headers={"Content-Disposition": content_disposition(f"{safe_title(book)}{suffix}")},
    )
