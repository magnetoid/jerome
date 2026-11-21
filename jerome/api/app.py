from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from jerome.api import routes_books, routes_exports, routes_suggestions
from jerome.api.deps import current_actor
from jerome.api.limits import BodyLimitMiddleware
from jerome.config import Settings, get_settings
from jerome.db import make_engine
from jerome.errors import Conflict, InvalidChangeError, NotFound, UnsupportedDocumentError

_ERROR_STATUS: dict[type[Exception], int] = {
    NotFound: 404,
    Conflict: 409,
    UnsupportedDocumentError: 422,
    InvalidChangeError: 422,
}


def _handler(status: int):
    async def handle(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=status)

    return handle


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Jerome")
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.add_middleware(BodyLimitMiddleware)
    for exc_type, status in _ERROR_STATUS.items():
        app.add_exception_handler(exc_type, _handler(status))

    auth = [Depends(current_actor)]
    app.include_router(routes_books.router, prefix="/v1", dependencies=auth)
    app.include_router(routes_suggestions.router, prefix="/v1", dependencies=auth)
    app.include_router(routes_exports.router, prefix="/v1", dependencies=auth)
    return app
