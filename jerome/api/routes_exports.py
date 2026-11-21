from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from jerome.api.deps import current_actor, get_session, get_storage
from jerome.core.exports import ExportFormat, ReviewFilters, export_book
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
    session: Annotated[Session, Depends(get_session)],
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
