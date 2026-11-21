from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from jerome.api.deps import current_actor, get_session, get_storage
from jerome.core.exports import ExportFormat, export_book
from jerome.storage import Storage

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
):
    try:
        f = export_book(session, storage, book_id, format, actor=actor)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return Response(
        f.data,
        media_type=f.media_type,
        headers={"Content-Disposition": content_disposition(f.filename)},
    )
