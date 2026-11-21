from dataclasses import asdict
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile

from jerome.api.deps import SessionDep, current_actor, get_settings_dep, get_storage, read_limited
from jerome.config import Settings
from jerome.core.exports import ExportFormat, ReviewFilters, export_book
from jerome.core.review_imports import import_review
from jerome.errors import NotFound
from jerome.orm import ReviewImport
from jerome.storage import Storage
from jerome.types import Category, Severity

router = APIRouter()


def content_disposition(filename: str) -> str:
    fallback = filename.encode("ascii", "replace").decode().replace("?", "_")
    fallback = fallback.replace("\\", "_").replace('"', "_")
    value = f'attachment; filename="{fallback}"'
    if fallback != filename:
        value += f"; filename*=UTF-8''{quote(filename, safe='')}"
    return value


@router.get("/books/{book_id}/export")
def export(
    book_id: int,
    format: Annotated[ExportFormat, Query()],
    session: SessionDep,
    storage: Annotated[Storage, Depends(get_storage)],
    actor: Annotated[str, Depends(current_actor)],
    min_severity: Annotated[Severity | None, Query()] = None,
    categories: Annotated[list[Category] | None, Query()] = None,
    chapters: Annotated[list[int] | None, Query()] = None,
):
    filters = ReviewFilters(min_severity, categories, chapters)
    f = export_book(session, storage, book_id, format, filters=filters, actor=actor)
    return Response(
        f.data,
        media_type=f.media_type,
        headers={"Content-Disposition": content_disposition(f.filename)},
    )


@router.post("/books/{book_id}/review-imports", status_code=201)
def create_review_import(
    book_id: int,
    file: Annotated[UploadFile, File()],
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    actor: Annotated[str, Depends(current_actor)],
):
    data = read_limited(file, settings.max_upload_mb * 1024 * 1024)
    return asdict(import_review(session, book_id, data, actor))


@router.get("/books/{book_id}/review-imports/{import_id}")
def get_review_import(
    book_id: int,
    import_id: int,
    session: SessionDep,
):
    row = session.get(ReviewImport, import_id)
    if row is None or row.book_id != book_id:
        raise NotFound(f"review import {import_id} not found in book {book_id}")
    return row.report
