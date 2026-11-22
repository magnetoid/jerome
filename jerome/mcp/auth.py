"""Bearer-only auth for /mcp, checked before the MCP layer sees the request.

Every request needs ``Authorization: Bearer <API key>``; a session cookie is never looked
at. A missing, unknown or revoked key gets 401. Stateless mode means this runs on every
request, so a key revoked mid-conversation is refused on the next call.

``/mcp/files/`` links carry their own single-use token and are served by their own routes
(``jerome.mcp.files``), matched before this one. A request under that prefix that still
lands here (another method, a nested path) is a plain 404: it never reaches the MCP app.
"""

import json

import anyio.to_thread
from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from jerome.api.deps import Principal
from jerome.core.keys import verify_api_key
from jerome.mcp.context import APP_KEY, PRINCIPAL_KEY, open_session

FILES_PREFIX = "/mcp/files/"


def _bearer_token(scope: Scope) -> str | None:
    for key, value in scope["headers"]:
        if key == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
            return None
    return None


async def _reply(send: Send, status: int, detail: str, *extra: tuple[bytes, bytes]) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                *extra,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _unauthorized(send: Send) -> None:
    await _reply(send, 401, "Missing or invalid API key", (b"www-authenticate", b"Bearer"))


class McpAuthMiddleware:
    """Wraps only the MCP app. Puts the Principal and the main FastAPI app in scope state."""

    def __init__(self, app: ASGIApp, main_app: FastAPI) -> None:
        self.app = app
        self.main_app = main_app

    def _key_name(self, token: str) -> str | None:
        with open_session(self.main_app) as session:
            return verify_api_key(session, token)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["path"].startswith(FILES_PREFIX):
            await _reply(send, 404, "Not Found")
            return
        token = _bearer_token(scope)
        name = await anyio.to_thread.run_sync(self._key_name, token) if token else None
        if name is None:
            await _unauthorized(send)
            return
        state = scope.setdefault("state", {})
        state[PRINCIPAL_KEY] = Principal(
            actor=name, channel="mcp", user_id=None, must_change_password=False
        )
        state[APP_KEY] = self.main_app
        await self.app(scope, receive, send)
