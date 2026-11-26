import json
import re

from starlette.concurrency import run_in_threadpool

from jerome.api.runtime import cached_for_app, refresh_if_due

_MIB = 1024 * 1024
_PEEK = 64 * 1024  # how much of an upload to look at to see whether it is a PDF
_PDF_NAME = re.compile(rb'filename="[^"]*\.pdf"', re.IGNORECASE)


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Pure-ASGI request-body limit, applied before the app reads or parses the body.

    The limit is ``max_upload_mb`` MiB (``max_pdf_upload_mb`` for a PDF upload to
    ``POST /v1/books``, found by peeking at the start of its body) plus 1 MiB of slack for
    multipart framing. Read from the effective settings, refreshed first when the cache is due (at most every few
    seconds, in a worker thread). Streamed bodies without Content-Length are counted.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        app = scope["app"]
        if app.state.settings_store.due:  # a raised limit must not be refused against a stale one
            await run_in_threadpool(refresh_if_due, app)
        settings = cached_for_app(app)
        limit = (settings.max_upload_mb + 1) * _MIB
        replay: list[dict] = []
        if scope["method"] == "POST" and scope["path"] == "/v1/books":
            # a PDF has its own, larger limit (its route checks it): look at the start of the
            # multipart body for the file's name
            seen = b""
            while len(seen) < _PEEK:
                message = await receive()
                replay.append(message)
                if message["type"] != "http.request":
                    break
                seen += message.get("body", b"")
                if not message.get("more_body", False):
                    break
            if _PDF_NAME.search(seen):
                limit = (max(settings.max_upload_mb, settings.max_pdf_upload_mb) + 1) * _MIB
        for key, value in scope["headers"]:
            if key == b"content-length":
                try:
                    too_big = int(value) > limit
                except ValueError:
                    too_big = False
                if too_big:
                    await self._reject(send)
                    return
                break

        received = 0
        started = False
        exceeded = False

        async def counting_receive():
            nonlocal received, exceeded
            message = replay.pop(0) if replay else await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _TooLarge
            return message

        async def tracking_send(message):
            nonlocal started
            if exceeded:
                return  # the app's own (e.g. 400) reply to the aborted read is replaced by 413
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except Exception:
            # FastAPI may wrap the read error (e.g. as a 400), so go by the flag, not the type
            if not exceeded or started:
                raise
        if exceeded and not started:
            await self._reject(send)

    @staticmethod
    async def _reject(send) -> None:
        body = json.dumps({"detail": "Request body too large"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
