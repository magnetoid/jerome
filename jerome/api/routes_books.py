from dataclasses import asdict
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from jerome.api.deps import (
    SessionDep,
    current_actor,
    get_registry,
    get_settings_dep,
    get_storage,
    read_limited,
)
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
from jerome.models import Registry
from jerome.orm import Book
from jerome.storage import Storage
from jerome.types import Variant

router = APIRouter()

StorageDep = Annotated[Storage, Depends(get_storage)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


Work = Literal["proofread", "translate"]
OcrEngine = Literal["ai", "tesseract"]


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


def _start_pdf(session, storage, actor, *, auto_apply, **fields) -> Book:
    if fields["work"] == "translate" and fields["target_variant"] is None:
        raise RequestValidationError(
            [
                {
                    "loc": ("body", "target_variant"),
                    "msg": "target_variant is required to translate",
                    "type": "missing",
                }
            ]
        )
    return books.start_pdf_import(
        session,
        storage,
        auto_apply=DEFAULT_POLICY if auto_apply else OFF_POLICY,
        actor=actor,
        **fields,
    )


def default_ocr_engine(registry: Registry, local_only: bool) -> OcrEngine:
    """The AI model when an ``ocr`` (or else an ``ai``) model is set up and the book may use
    it, else Tesseract."""
    if not local_only and (
        registry.for_pass("ocr", local_only=False) is not None
        or registry.for_pass("ai", local_only=False) is not None
    ):
        return "ai"
    return "tesseract"


def is_pdf(filename: str | None, head: bytes) -> bool:
    """A PDF is recognised by its ``.pdf`` name or by its ``%PDF-`` magic."""
    return (filename or "").lower().endswith(".pdf") or head.startswith(b"%PDF-")


@router.post("/books", status_code=201, response_model=BookOut)
def upload_book(
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    registry: Annotated[Registry, Depends(get_registry)],
    actor: Annotated[str, Depends(current_actor)],
    file: Annotated[UploadFile, File()],
    variant: Annotated[Variant, Form()],
    title: Annotated[str | None, Form()] = None,
    local_only: Annotated[bool, Form()] = False,
    auto_apply: Annotated[bool, Form()] = True,
    work: Annotated[Work, Form()] = "proofread",
    target_variant: Annotated[Variant | None, Form()] = None,
    translated_title: Annotated[str | None, Form()] = None,
    ocr_engine: Annotated[OcrEngine | None, Form()] = None,
    reocr: Annotated[bool, Form()] = True,
):
    head = file.file.read(5)
    file.file.seek(0)
    if is_pdf(file.filename, head):
        if ocr_engine == "ai" and local_only:
            raise HTTPException(
                status_code=422, detail="a local-only book cannot be read with the AI engine"
            )
        data = read_limited(file, settings.max_pdf_upload_mb * 1024 * 1024)
        return _start_pdf(
            session,
            storage,
            actor,
            filename=file.filename or "upload.pdf",
            data=data,
            variant=variant,
            title=title,
            local_only=local_only,
            auto_apply=auto_apply,
            engine=ocr_engine or default_ocr_engine(registry, local_only),
            reocr=reocr,
            work=work,
            target_variant=target_variant,
            translated_title=translated_title,
        )
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


class ConfirmImportBody(BaseModel):
    max_cost: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=4)]


@router.post("/books/{book_id}/import/confirm")
def confirm_import(book_id: int, body: ConfirmImportBody, session: SessionDep):
    from jerome.api.routes_jobs import _job_json
    from jerome.jobs import service

    books.get_book(session, book_id)
    return _job_json(service.confirm_import(session, book_id, body.max_cost))


@router.post("/books/{book_id}/import/use-tesseract")
def use_tesseract(book_id: int, session: SessionDep):
    from jerome.api.routes_jobs import _job_json
    from jerome.jobs import service

    books.get_book(session, book_id)
    return _job_json(service.use_tesseract(session, book_id))


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
