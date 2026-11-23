from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, Response, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from jerome.api.runtime import effective_for_app
from jerome.config import Settings
from jerome.core.keys import verify_api_key
from jerome.core.sessions import resolve_session
from jerome.db import session_scope
from jerome.jobs.worker import WorkerDeps
from jerome.models import Registry
from jerome.storage import Storage

_CHUNK = 1024 * 1024


def read_limited(file: UploadFile, limit: int) -> bytes:
    """Read the upload in chunks, stopping as soon as it exceeds the limit."""
    parts: list[bytes] = []
    size = 0
    while chunk := file.file.read(_CHUNK):
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail="Upload too large")
        parts.append(chunk)
    return b"".join(parts)


def get_session(request: Request) -> Iterator[Session]:
    """One transaction per request: commit on success, roll back on any exception."""
    with session_scope(request.app.state.engine) as session:
        yield session


# Every route and dependency takes the session through this one alias. scope="function" ends
# the transaction (commit, or rollback on error) when the endpoint and its serialization
# finish, *before* the response is sent: a client that sees success can rely on the write.
# (The default "request" scope commits only after the response has gone out.) It must be
# the same everywhere: FastAPI caches dependencies by callable and scope, so mixing scopes
# would open two transactions in one request.
SessionDep = Annotated[Session, Depends(get_session, scope="function")]


def get_settings_dep(request: Request, session: SessionDep) -> Settings:
    """The effective settings: app values over the environment, read through the request's
    session (and kept for a few seconds). Brings app.state's registry and clients in step."""
    return effective_for_app(request.app, session)


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def get_registry(request: Request, _: SettingsDep) -> Registry:
    return request.app.state.registry  # brought in step by get_settings_dep


def get_worker_deps(request: Request, settings: SettingsDep) -> WorkerDeps:
    state = request.app.state
    return WorkerDeps(state.registry, state.lt_client, settings.webhook_secret)


def get_storage(request: Request) -> Storage:
    return Storage(request.app.state.settings.data_dir)


SESSION_COOKIE = "jerome_session"
CLIENT_HEADER = "x-jerome-client"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# What a person who must change their password may still do.
_PASSWORD_CHANGE_PATHS = frozenset({"/v1/auth/me", "/v1/auth/password", "/v1/auth/logout"})

# Declared as a dependency so the OpenAPI schema (and /docs) shows the Bearer scheme.
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    actor: str
    channel: Literal["api", "web", "mcp"]
    user_id: int | None
    must_change_password: bool
    role: str | None = None  # the user's role for a web session; None for an API key


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 24 * 3600,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax"
    )


def current_principal(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Principal:
    """Who is calling: an API key (Bearer) first, else the session cookie."""
    if credentials is not None:
        name = verify_api_key(session, credentials.credentials.strip())
        if name is None:
            raise _unauthorized("Missing or invalid API key")
        return Principal(actor=name, channel="api", user_id=None, must_change_password=False)

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _unauthorized("Missing or invalid API key")
    # Cross-site guard: a browser only sends this custom header from our own pages.
    if request.method not in _SAFE_METHODS and request.headers.get(CLIENT_HEADER) != "web":
        raise HTTPException(status_code=403, detail="missing X-Jerome-Client header")
    user = resolve_session(session, token, days=settings.session_days, now=datetime.now(UTC))
    if user is None:
        raise _unauthorized("Session expired or ended; log in again")
    if user.must_change_password and request.url.path not in _PASSWORD_CHANGE_PATHS:
        raise HTTPException(status_code=403, detail="password change required")
    set_session_cookie(response, token, settings)  # the browser's copy slides with the session
    return Principal(
        actor=user.email,
        channel="web",
        user_id=user.id,
        must_change_password=user.must_change_password,
        role=user.role,
    )


def require_super_admin(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    """Team management is for super admins signed in on the web; API keys never qualify."""
    if principal.channel != "web" or principal.role != "super_admin":
        raise HTTPException(status_code=403, detail="super admin only")
    return principal


def current_actor(principal: Annotated[Principal, Depends(current_principal)]) -> str:
    return principal.actor
