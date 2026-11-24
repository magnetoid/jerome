"""Publication details and cover of a book: what its finished files say about it."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, File, Response, UploadFile

from jerome.api.deps import SessionDep, read_limited
from jerome.api.routes_books import StorageDep
from jerome.api.schemas import PublicationIn, PublicationOut
from jerome.core import publication
from jerome.core.books import get_book
from jerome.errors import NotFound

router = APIRouter()

MAX_COVER_BYTES = 20 * 1024 * 1024


@router.get("/books/{book_id}/publication", response_model=PublicationOut)
def get_publication(book_id: int, session: SessionDep):
    return asdict(publication.get_publication(session, book_id))


@router.put("/books/{book_id}/publication", response_model=PublicationOut)
def put_publication(book_id: int, body: PublicationIn, session: SessionDep):
    values = body.model_dump(exclude_unset=True)
    return asdict(publication.update_publication(session, book_id, values))


@router.put("/books/{book_id}/cover", response_model=PublicationOut)
def put_cover(
    book_id: int,
    session: SessionDep,
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
):
    data = read_limited(file, MAX_COVER_BYTES)
    return asdict(publication.set_cover(session, storage, book_id, data))


@router.get("/books/{book_id}/cover")
def get_cover(book_id: int, session: SessionDep, storage: StorageDep):
    publication.get_publication(session, book_id)  # unknown and source books are refused
    cover = publication.read_cover(storage, get_book(session, book_id))
    if cover is None:
        raise NotFound(f"cover of book {book_id}")
    data, media_type = cover
    return Response(data, media_type=media_type, headers={"Cache-Control": "no-store"})


@router.delete("/books/{book_id}/cover", response_model=PublicationOut)
def delete_cover(book_id: int, session: SessionDep, storage: StorageDep):
    return asdict(publication.delete_cover(session, storage, book_id))
