from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from jerome.config import Settings
from jerome.core.keys import verify_api_key
from jerome.db import session_scope
from jerome.jobs.worker import WorkerDeps
from jerome.models import Registry
from jerome.storage import Storage


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_registry(request: Request) -> Registry:
    return request.app.state.registry


def get_worker_deps(request: Request) -> WorkerDeps:
    state = request.app.state
    return WorkerDeps(state.registry, state.lt_client, state.settings.webhook_secret)


def get_session(request: Request) -> Iterator[Session]:
    """One transaction per request: commit on success, roll back on any exception."""
    with session_scope(request.app.state.engine) as session:
        yield session


def get_storage(request: Request) -> Storage:
    return Storage(request.app.state.settings.data_dir)


def current_actor(request: Request, session: Annotated[Session, Depends(get_session)]) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    name = verify_api_key(session, token.strip()) if scheme.lower() == "bearer" and token else None
    if name is None:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return name
