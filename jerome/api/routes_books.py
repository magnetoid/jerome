from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from jerome.api.deps import SessionDep, get_settings_dep, get_storage, read_limited
from jerome.api.schemas import (
    AutoApplyBody,
    BookOut,
    ChapterOut,
    ChapterPatch,
    StyleSheetOut,
    StyleSheetUpdate,
)
from jerome.config import Settings
from jerome.core import books, style_sheets, translation
from jerome.core.auto_apply import (
    DEFAULT_POLICY,
    OFF_POLICY,
    AutoApplyPolicy,
    get_policy,
    policy_to_json,
    set_policy,
)
from jerome.orm import Book
from jerome.storage import Storage
from jerome.types import Variant

router = APIRouter()

StorageDep = Annotated[Storage, Depends(get_storage)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


Work = Literal["proofread", "translate"]


def import_upload(
    session: Session,
    storage: Storage,
    *,
    filename: str,
    data: bytes,
    variant: Variant,
    title: str | None,
    local_only: bool,
    auto_apply: bool,
    work: Work,
    target_variant: Variant | None,
    translated_title: str | None,
) -> Book:
    """Import an upload as POST /v1/books does. With ``work="translate"``, ``variant`` is the
    source language, ``target_variant`` is required, and the translated book is returned."""
    policy = DEFAULT_POLICY if auto_apply else OFF_POLICY
    if work == "proofread":
        return books.import_book(
            session,
            storage,
            filename=filename,
            data=data,
            variant=variant,
            title=title,
            local_only=local_only,
            auto_apply=policy,
        )
    if target_variant is None:
        raise RequestValidationError(
            [
                {
                    "loc": ("body", "target_variant"),
                    "msg": "target_variant is required to translate",
                    "type": "missing",
                }
            ]
        )
    return translation.import_for_translation(
        session,
        storage,
        filename=filename,
        data=data,
        source_variant=variant,
        target_variant=target_variant,
        translated_title=translated_title,
        title=title,
        local_only=local_only,
        auto_apply=policy,
    )


@router.post("/books", status_code=201, response_model=BookOut)
def upload_book(
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    variant: Annotated[Variant, Form()],
    title: Annotated[str | None, Form()] = None,
    local_only: Annotated[bool, Form()] = False,
    auto_apply: Annotated[bool, Form()] = True,
    work: Annotated[Work, Form()] = "proofread",
    target_variant: Annotated[Variant | None, Form()] = None,
    translated_title: Annotated[str | None, Form()] = None,
):
    data = read_limited(file, settings.max_upload_mb * 1024 * 1024)
    return import_upload(
        session,
        storage,
        filename=file.filename or "upload",
        data=data,
        variant=variant,
        title=title,
        local_only=local_only,
        auto_apply=auto_apply,
        work=work,
        target_variant=target_variant,
        translated_title=translated_title,
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


@router.get("/books/{book_id}/auto-apply", response_model=AutoApplyBody)
def get_auto_apply(book_id: int, session: SessionDep):
    return policy_to_json(get_policy(session, book_id))


@router.put("/books/{book_id}/auto-apply", response_model=AutoApplyBody)
def put_auto_apply(book_id: int, body: AutoApplyBody, session: SessionDep):
    set_policy(
        session,
        book_id,
        AutoApplyPolicy(
            enabled=body.enabled,
            categories=frozenset(body.categories),
            min_severity=body.min_severity,
            house_rules=body.house_rules,
        ),
    )
    return policy_to_json(get_policy(session, book_id))
