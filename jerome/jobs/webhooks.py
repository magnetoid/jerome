"""Signed job webhooks. Delivery is best effort: it logs failures and never raises."""

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)

ATTEMPTS = 3


def send_webhook(
    url: str,
    event: str,
    payload: dict[str, Any],
    secret: str | None,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    body = json.dumps({"event": event, **payload}, default=str).encode()
    headers = {"Content-Type": "application/json"}
    if secret:
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Jerome-Signature"] = f"sha256={digest}"
    for attempt in range(1, ATTEMPTS + 1):
        try:
            resp = httpx.post(url, content=body, headers=headers, timeout=10)
            if resp.is_success:
                return
            reason = f"HTTP {resp.status_code}"
        except Exception as exc:  # delivery must never break the worker
            reason = repr(exc)
        log.warning(
            "webhook %s to %s failed (attempt %d/%d): %s", event, url, attempt, ATTEMPTS, reason
        )
        if attempt < ATTEMPTS:
            sleep(attempt)
