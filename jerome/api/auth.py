"""Login helpers: the client address behind a trusted proxy, and failed-login throttling."""

import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import Request

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60
BLOCK_SECONDS = 15 * 60


def client_ip(request: Request, trusted: list[str]) -> str | None:
    """The client's address as reported by a trusted proxy, else None.

    Only a direct peer in ``trusted`` is believed; without one, every request would seem to
    come from the Docker gateway, and throttling that address would lock out the whole team.
    Each proxy appends the address it saw on the right of ``X-Forwarded-For``, and anything
    to the left may be client-supplied. So the list is walked from the right, skipping our
    own trusted proxies, and the first other address is the client.
    """
    peer = request.client.host if request.client else None
    if peer is None or peer not in trusted:
        return None
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    for hop in reversed(forwarded.split(",")):
        address = hop.strip()
        if address and address not in trusted:
            return address
    return None


class LoginThrottle:
    """Failed logins per key, in memory (one process).

    A key is blocked for ``BLOCK_SECONDS`` once it has ``MAX_FAILURES`` failures within
    ``WINDOW_SECONDS``. Keys are ``email:<normalized>`` and, behind a trusted proxy,
    ``ip:<addr>``.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}
        self._blocked_until: dict[str, float] = {}

    def check(self, keys: list[str]) -> float | None:
        """Seconds to wait before another attempt, or None if no key is blocked."""
        now = self._clock()
        with self._lock:
            waits = [self._blocked_until.get(k, now) - now for k in keys]
        wait = max(waits, default=0.0)
        return wait if wait > 0 else None

    def fail(self, keys: list[str]) -> None:
        now = self._clock()
        with self._lock:
            self._prune(now)
            for key in keys:
                failures = self._failures.setdefault(key, deque())
                failures.append(now)
                if len(failures) >= MAX_FAILURES:
                    self._blocked_until[key] = now + BLOCK_SECONDS
                    del self._failures[key]  # a fresh window starts after the block

    def succeed(self, keys: list[str]) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)
                self._blocked_until.pop(key, None)

    def _prune(self, now: float) -> None:
        """Forget failures older than the window and blocks that have ended."""
        for key in list(self._failures):
            failures = self._failures[key]
            while failures and failures[0] <= now - WINDOW_SECONDS:
                failures.popleft()
            if not failures:
                del self._failures[key]
        for key in [k for k, until in self._blocked_until.items() if until <= now]:
            del self._blocked_until[key]
