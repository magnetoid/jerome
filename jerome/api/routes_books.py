from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy.orm import Session

from jerome.api.deps import get_session, get_settings_dep, get_storage, read_limited
from jerome.api.schemas import (
    BookOut,
    ChapterOut,
    ChapterPatch,
    StyleSheetOut,
    StyleSheetUpdate,
)
from jerome.config import Settings
from jerome.core import books, style_sheets
from jerome.storage import Storage
from jerome.types import Variant

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]
StorageDep = Annotated[Storage, Depends(get_storage)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


@router.post("/books", status_code=201, response_model=BookOut)
def upload_book(
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    variant: Annotated[Variant, Form()],
    title: Annotated[str | None, Form()] = None,
    local_only: Annotated[bool, Form()] = False,
):
    data = read_limited(file, settings.max_upload_mb * 1024 * 1024)
    return books.import_book(
        session,
        storage,
        filename=file.filename or "upload",
        data=data,
        variant=variant,
        title=title,
        local_only=local_only,
    )


@router.get("/books", response_model=list[BookOut])
def list_books(session: SessionDep):
    return books.list_books(session)


@router.get("/books/{book_id}", response_model=BookOut)
def get_book(book_id: int, session: SessionDep):
    return books.get_book(session, book_id)


@router.get("/books/{book_id}/chapters", response_model=list[ChapterOut])
def list_chapters(book_id: int, session: SessionDep):
    return books.list_chapters(session, book_id)


@router.patch("/chapters/{chapter_id}", response_model=ChapterOut)
def patch_chapter(chapter_id: int, body: ChapterPatch, session: SessionDep):
    return books.set_chapter_proofread(session, chapter_id, body.proofread)


@router.get("/books/{book_id}/style-sheet", response_model=StyleSheetOut)
def get_style_sheet(book_id: int, session: SessionDep):
    return asdict(style_sheets.get_style_sheet(session, book_id))


@router.put("/books/{book_id}/style-sheet", response_model=StyleSheetOut)
def put_style_sheet(book_id: int, body: StyleSheetUpdate, session: SessionDep):
    return asdict(style_sheets.update_style_sheet(session, book_id, **body.model_dump()))


@router.delete("/books/{book_id}", status_code=204)
def delete_book(book_id: int, session: SessionDep, storage: StorageDep):
    books.delete_book(session, storage, book_id)
    return Response(status_code=204)
