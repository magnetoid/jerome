"""``PUT`` and ``GET /mcp/files/{token}``: the single-use links the MCP file tools return.

The token in the path is the only credential: no API key, and the session cookie is never
looked at. A bad token (unknown, used, expired or of the other kind) is a plain 404.
These routes are registered on the main app ahead of the ``/mcp`` routes, so they get the
same session and storage dependencies (and test overrides) as REST, and a request for them
never reaches the MCP app.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from jerome.api.deps import SessionDep, get_settings_dep, get_storage
from jerome.api.routes_exports import content_disposition
from jerome.config import Settings
from jerome.core import transfer_links
from jerome.errors import InvalidChangeError
from jerome.storage import Storage

PREFIX = "/mcp/files"
MIB = 1024 * 1024

router = APIRouter(include_in_schema=False)


def _too_large() -> JSONResponse:
    return JSONResponse({"detail": "Upload too large"}, status_code=413)


async def _read_bounded(request: Request, limit: int) -> bytes | None:
    """The body, or None as soon as it is known to exceed ``limit`` (nothing kept)."""
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > limit:
        return None
    parts: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        parts.append(chunk)
    return b"".join(parts)


@router.put(PREFIX + "/{token}", status_code=204)
async def put_file(
    token: str,
    request: Request,
    session: SessionDep,
    storage: Annotated[Storage, Depends(get_storage)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    limit = settings.max_upload_mb * MIB
    # Refuse an unknown or spent token before buffering any of the body.
    await run_in_threadpool(transfer_links.check_upload_open, session, token, datetime.now(UTC))
    data = await _read_bounded(request, limit)
    if data is None:
        return _too_large()
    try:
        await run_in_threadpool(
            transfer_links.receive_upload,
            session,
            storage,
            token,
            data,
            limit,
            datetime.now(UTC),
        )
    except InvalidChangeError:  # over the limit: 413, not the 422 the app would give
        return _too_large()
    return Response(status_code=204)


@router.get(PREFIX + "/{token}")
def get_file(
    token: str,
    session: SessionDep,
    storage: Annotated[Storage, Depends(get_storage)],
) -> Response:
    link, data = transfer_links.serve_download(session, storage, token, datetime.now(UTC))
    return Response(
        data,
        media_type=link.media_type,
        headers={
            "Content-Disposition": content_disposition(link.filename or "download"),
            "Cache-Control": "no-store",
        },
    )
