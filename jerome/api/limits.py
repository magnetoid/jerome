import json

from jerome.api.runtime import cached_for_app

_MIB = 1024 * 1024


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Pure-ASGI request-body limit, applied before the app reads or parses the body.

    The limit is ``max_upload_mb`` MiB plus 1 MiB of slack for multipart framing. Read from
    the effective settings as last cached (no database read here) per request. Streamed
    bodies without Content-Length are counted.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = (cached_for_app(scope["app"]).max_upload_mb + 1) * _MIB
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
            message = await receive()
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
