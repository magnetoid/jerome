"""Map an exception from a tool body to the tool error the agent reads.

The text is ``<code>: <message>``, with the same message REST puts in ``detail``. Anything
unexpected becomes ``error: internal error``; its details stay in the server log.
"""

from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from jerome.core.app_settings import InvalidSetting
from jerome.core.publication import InvalidPublication
from jerome.errors import (
    AiCheckError,
    Conflict,
    InvalidChangeError,
    InvalidFeedback,
    NotFound,
    UnknownModelPrice,
    UnsupportedDocumentError,
    UnsupportedLanguagePair,
)
from jerome.models.listing import ProviderListError

INTERNAL = "error: internal error"

# Same classes as the REST handlers in jerome.api.app (404, 409, 400, the 422s, 502).
_CODES: dict[type[Exception], str] = {
    NotFound: "not_found",
    Conflict: "conflict",
    InvalidChangeError: "invalid",
    UnsupportedDocumentError: "invalid",
    UnknownModelPrice: "invalid",
    InvalidSetting: "invalid",
    UnsupportedLanguagePair: "invalid",
    InvalidPublication: "invalid",
    InvalidFeedback: "invalid",
    AiCheckError: "unavailable",  # 502: no usable verdict from the AI
    ProviderListError: "unavailable",  # 502: the provider failed
}


def _validation_message(errors: Iterable[Any]) -> str:
    """Where and what, without echoing the rejected values (they are the caller's data)."""
    parts = []
    for err in errors:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{loc}: {err.get('msg', 'invalid')}" if loc else str(err.get("msg")))
    return "; ".join(parts) or "invalid arguments"


def _message(exc: Exception) -> tuple[str, str] | None:
    """(code, message) for an anticipated error, else None."""
    for exc_type, code in _CODES.items():
        if isinstance(exc, exc_type):
            return code, str(exc)
    if isinstance(exc, HTTPException):
        if exc.status_code == 429:
            wait = (exc.headers or {}).get("Retry-After")
            suffix = f" (retry after {wait} s)" if wait else ""
            return "rate_limited", f"{exc.detail}{suffix}"
        return "invalid", str(exc.detail)
    if isinstance(exc, RequestValidationError | ValidationError):
        return "invalid", _validation_message(exc.errors())
    return None


def to_tool_error(exc: Exception) -> ToolError:
    """The SDK tool error for ``exc``: ``<code>: <message>``, or the generic internal error."""
    known = _message(exc)
    if known is None:
        return ToolError(INTERNAL)
    code, message = known
    return ToolError(f"{code}: {message}")
