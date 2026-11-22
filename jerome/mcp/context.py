"""What a tool body gets: the app, one database transaction and the caller.

Every tool is a plain (sync) function that hands its body to :func:`call`::

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
    def list_books() -> list[dict]:
        return call(lambda c: [BookOut.model_validate(b).model_dump(mode="json")
                               for b in books.list_books(c.session)])

:func:`call` finds the MCP request in progress (see ``jerome.mcp.server``), opens a session
the way REST does (``get_session`` from ``app.dependency_overrides`` when a test set one),
runs the body in a savepoint, and turns any exception into the SDK's ``ToolError`` with
:func:`jerome.mcp.errors.to_tool_error`. One call is one transaction: committed when the
body returns, rolled back when it raises.
"""

import contextlib
import inspect
import logging
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.orm import Session
from starlette.requests import Request

from jerome.api.deps import Principal, get_registry, get_session, get_storage, get_worker_deps
from jerome.config import Settings
from jerome.db import session_scope
from jerome.jobs.worker import WorkerDeps
from jerome.mcp.errors import INTERNAL, to_tool_error
from jerome.models import Registry
from jerome.storage import Storage

log = logging.getLogger(__name__)

# Keys the auth middleware puts in the ASGI scope's "state" for each /mcp request.
PRINCIPAL_KEY = "jerome_principal"
APP_KEY = "jerome_app"

# The SDK Context of the tool call in progress. Set by JeromeMCP.call_tool; a sync tool
# runs on a worker thread, and anyio copies context variables into it.
current_mcp_context: ContextVar[Context | None] = ContextVar("jerome_mcp_context", default=None)


@dataclass
class ToolContext:
    app: FastAPI
    session: Session
    principal: Principal
    settings: Settings
    storage: Storage
    registry: Registry
    worker_deps: WorkerDeps


def current_request() -> Request:
    """The HTTP request behind the tool call in progress."""
    ctx = current_mcp_context.get()
    request = ctx.request_context.request if ctx is not None else None
    if not isinstance(request, Request):
        raise RuntimeError("no MCP HTTP request in progress")
    return request


def _override(app: FastAPI, dependency: Callable[..., Any]) -> Callable[..., Any] | None:
    return app.dependency_overrides.get(dependency)


@contextlib.contextmanager
def open_session(app: FastAPI) -> Iterator[Session]:
    """A session as REST's get_session gives it: the test override, else one transaction."""
    override = _override(app, get_session)
    if override is None:
        with session_scope(app.state.engine) as session:
            yield session
    elif inspect.isgeneratorfunction(override):
        with contextlib.contextmanager(override)() as session:
            yield session
    else:
        yield override()


def _storage(app: FastAPI) -> Storage:
    override = _override(app, get_storage)
    return override() if override is not None else Storage(app.state.settings.data_dir)


def _registry(app: FastAPI) -> Registry:
    override = _override(app, get_registry)
    return override() if override is not None else app.state.registry


def _worker_deps(app: FastAPI) -> WorkerDeps:
    override = _override(app, get_worker_deps)
    if override is not None:
        return override()
    state = app.state
    return WorkerDeps(state.registry, state.lt_client, state.settings.webhook_secret)


def call[T](fn: Callable[[ToolContext], T]) -> T:
    """Run one tool body in its own transaction and map its errors to a ToolError."""
    request = current_request()
    state = request.scope["state"]
    app: FastAPI = state[APP_KEY]
    principal: Principal = state[PRINCIPAL_KEY]
    try:
        with open_session(app) as session, session.begin_nested():
            return fn(
                ToolContext(
                    app=app,
                    session=session,
                    principal=principal,
                    settings=app.state.settings,
                    storage=_storage(app),
                    registry=_registry(app),
                    worker_deps=_worker_deps(app),
                )
            )
    except ToolError:
        raise
    except Exception as exc:
        error = to_tool_error(exc)
        if str(error) == INTERNAL:
            log.exception("MCP tool body failed")
        raise error from exc
