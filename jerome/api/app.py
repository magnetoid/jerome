from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from jerome.api import (
    routes_activity,
    routes_agent_actions,
    routes_auth,
    routes_books,
    routes_exports,
    routes_jobs,
    routes_projects,
    routes_settings,
    routes_suggestions,
    routes_text,
    routes_translation,
    routes_users,
)
from jerome.api.auth import LoginThrottle
from jerome.api.deps import current_principal
from jerome.api.limits import BodyLimitMiddleware
from jerome.api.runtime import install as install_settings
from jerome.api.web import mount_web
from jerome.config import Settings, get_settings
from jerome.core.app_settings import InvalidSetting
from jerome.db import make_engine
from jerome.errors import (
    Conflict,
    InvalidChangeError,
    NotFound,
    UnknownModelPrice,
    UnsupportedDocumentError,
    UnsupportedLanguagePair,
)
from jerome.mcp.server import mount_mcp
from jerome.models.registry import build_registry
from jerome.proofread.languagetool import LanguageToolClient
from jerome.proofread.text import TextLimits

_ERROR_STATUS: dict[type[Exception], int] = {
    NotFound: 404,
    Conflict: 409,
    UnsupportedDocumentError: 422,
    InvalidChangeError: 422,
    UnknownModelPrice: 422,
    InvalidSetting: 400,
    UnsupportedLanguagePair: 400,
}


def _handler(status: int):
    async def handle(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=status)

    return handle


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's 422, except that settings routes never echo the submitted value (it may be
    a secret)."""
    if not request.url.path.startswith("/v1/settings"):
        return await request_validation_exception_handler(request, exc)
    errors = [{k: v for k, v in e.items() if k in ("loc", "msg", "type")} for e in exc.errors()]
    return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The MCP transport is stateless, but the SDK still needs its session manager running.
    async with app.state.mcp_session_manager.run():
        yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Jerome", lifespan=_lifespan)
    app.state.settings = settings
    app.state.registry = build_registry(settings)
    app.state.lt_client = LanguageToolClient(settings.languagetool_url)
    app.state.text_limits = TextLimits(settings.text_rpm)
    install_settings(app, settings)  # app values from the database, laid on these settings
    app.state.engine = make_engine(settings.database_url)
    app.state.login_throttle = LoginThrottle()
    app.add_middleware(BodyLimitMiddleware)
    app.add_exception_handler(RequestValidationError, _validation_error)
    for exc_type, status in _ERROR_STATUS.items():
        app.add_exception_handler(exc_type, _handler(status))

    # Every /v1 route needs an API key or a session cookie. The auth router is the exception:
    # login is open, and its other routes depend on current_principal themselves.
    # current_principal also declares the Bearer scheme, so /docs shows Authorize.
    app.include_router(routes_auth.router, prefix="/v1")
    auth = [Depends(current_principal)]
    app.include_router(routes_books.router, prefix="/v1", dependencies=auth)
    app.include_router(routes_suggestions.router, prefix="/v1", dependencies=auth)
    app.include_router(routes_exports.router, prefix="/v1", dependencies=auth)
    app.include_router(routes_jobs.router, prefix="/v1", dependencies=auth)
    app.include_router(routes_translation.router, prefix="/v1", dependencies=auth)
    app.include_router(routes_text.router, prefix="/v1", dependencies=auth)
    app.include_router(routes_users.router, prefix="/v1", dependencies=auth)
    app.include_router(routes_settings.router, prefix="/v1", dependencies=auth)
    app.include_router(routes_projects.router, prefix="/v1", dependencies=auth)
    app.include_router(routes_agent_actions.router, prefix="/v1", dependencies=auth)
    app.include_router(routes_activity.router, prefix="/v1", dependencies=auth)
    mount_mcp(app)  # /mcp, outside /v1, with its own Bearer-only auth
    # The catch-all web app goes last, so it never shadows an API route.
    if settings.web_dir is not None:
        mount_web(app, settings.web_dir)
    return app
