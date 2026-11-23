"""Per-process cache of the effective settings, refreshed from the database within seconds."""

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from jerome.config import Settings
from jerome.core.app_settings import Effective, overlay
from jerome.core.secretbox import secret_box
from jerome.core.settings_changes import load_rows
from jerome.orm import AppSetting

log = logging.getLogger(__name__)

_Fingerprint = tuple[tuple[Any, ...], ...]


def _fingerprint(rows: list[AppSetting]) -> _Fingerprint:
    return tuple(
        (
            r.key,
            json.dumps(r.value, sort_keys=True, default=str),
            None if r.secret is None else bytes(r.secret),
            r.updated_at,
        )
        for r in sorted(rows, key=lambda r: r.key)
    )


def _detached(row: AppSetting) -> AppSetting:
    """A plain copy that outlives the session the row was read in."""
    return AppSetting(
        key=row.key,
        value=row.value,
        secret=None if row.secret is None else bytes(row.secret),
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


class _Hidden:
    """Compares by value but never shows it, so a secret cannot reach a log or a repr."""

    __slots__ = ("_value",)

    def __init__(self, value: str | None) -> None:
        self._value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Hidden) and self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)

    def __repr__(self) -> str:
        return "<hidden>"


def registry_inputs(settings: Settings) -> tuple:
    """What build_registry depends on, comparable with ==. Never shows the API key."""
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    return (
        _Hidden(json.dumps(settings.models, sort_keys=True, default=str)),  # may hold a key
        json.dumps(settings.prices, sort_keys=True, default=str),
        _Hidden(key),
    )


class SettingsStore:
    def __init__(
        self,
        base: Settings,
        *,
        interval: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = base
        self._interval = interval
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: list[AppSetting] = []
        self._fingerprint: _Fingerprint = ()
        self._effective = overlay(base, self._rows, secret_box(base))
        self._due = True
        self._last_read = 0.0
        self.version = 0

    @property
    def base(self) -> Settings:
        return self._base

    @base.setter
    def base(self, value: Settings) -> None:
        with self._lock:
            if value is self._base:
                return
            self._base = value
            self._effective = overlay(value, self._rows, secret_box(value))
            self.version += 1

    def cached(self) -> Effective:
        return self._effective

    def invalidate(self) -> None:
        self._due = True

    def current(self, session: Session) -> Effective:
        if not self._due and self._clock() - self._last_read < self._interval:
            return self._effective
        with self._lock:
            if not self._due and self._clock() - self._last_read < self._interval:
                return self._effective
            self._due = False  # cleared first so an invalidate() during the read is kept
            try:
                with session.begin_nested():  # a failure rolls back only this savepoint
                    rows = load_rows(session)
                    fingerprint = _fingerprint(rows)
            except Exception:  # noqa: BLE001 - a failed read must never take a request down
                log.exception("settings read failed; keeping the last settings")
                self._due = True  # the next call retries at once
                return self._effective
            self._last_read = self._clock()
            if fingerprint != self._fingerprint:
                self._rows = [_detached(r) for r in rows]
                self._fingerprint = fingerprint
                self._effective = overlay(self._base, self._rows, secret_box(self._base))
                self.version += 1
            return self._effective
