"""Per-process cache of the effective settings, refreshed from the database within seconds."""

import dataclasses
import json
import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session, sessionmaker

from jerome.config import Settings
from jerome.core.app_settings import Effective, overlay
from jerome.core.secretbox import secret_box
from jerome.core.settings_changes import load_rows
from jerome.models.registry import Registry, build_registry
from jerome.orm import AppSetting
from jerome.proofread.languagetool import LanguageToolClient, lt_client, lt_inputs

if TYPE_CHECKING:  # jerome.jobs.worker imports jerome.core; keep this module below it
    from jerome.jobs.worker import WorkerDeps

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
        self._base_values = dict(base.__dict__)  # to notice a change made in place
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

    def _same_base(self, value: Settings) -> bool:
        return value is self._base and value.__dict__ == self._base_values

    @base.setter
    def base(self, value: Settings) -> None:
        """Another base, or the same object changed in place: recompute from the saved rows."""
        if self._same_base(value):  # every request sets it; most of the time nothing changed
            return
        with self._lock:
            if self._same_base(value):
                return
            self._base = value
            self._base_values = dict(value.__dict__)
            self._effective = overlay(value, self._rows, secret_box(value))
            self.version += 1

    def cached(self) -> Effective:
        return self._effective

    @property
    def due(self) -> bool:
        """Whether the next ``current()`` would read the table."""
        return self._due or self._clock() - self._last_read >= self._interval

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
                changed = fingerprint != self._fingerprint
                detached = [_detached(r) for r in rows] if changed else self._rows
                effective = (
                    overlay(self._base, detached, secret_box(self._base))
                    if changed
                    else self._effective
                )
            except Exception:  # noqa: BLE001 - a failed read must never take a request down
                log.exception("settings read failed; keeping the last settings")
                self._due = True  # the next call retries at once
                return self._effective
            self._last_read = self._clock()
            if changed:
                self._rows = detached
                self._fingerprint = fingerprint
                self._effective = effective
                self.version += 1
            return self._effective


class LiveRefresh:
    """The worker's ``WorkerDeps.refresh``: deps for the current effective settings.

    The deps first handed in are taken to be built from ``store.base``. A new registry or
    LanguageTool client is built only when what it is made from changed, and is then handed
    to every later call, whichever copy of the deps the caller still holds.
    """

    def __init__(self, store: SettingsStore, session_factory: sessionmaker[Session]) -> None:
        self._store = store
        self._session_factory = session_factory
        self._registry: Registry | None = None  # None: keep the registry of the deps given
        self._registry_inputs = registry_inputs(store.cached().settings)
        self._lt: LanguageToolClient | None = None
        self._lt_inputs = lt_inputs(store.cached().settings)

    def __call__(self, deps: "WorkerDeps") -> "WorkerDeps":
        with self._session_factory() as session:
            settings = self._store.current(session).settings
        inputs = registry_inputs(settings)
        if inputs != self._registry_inputs:
            self._registry = build_registry(settings)
            self._registry_inputs = inputs
        if lt_inputs(settings) != self._lt_inputs:
            self._lt = lt_client(settings)
            self._lt_inputs = lt_inputs(settings)
        return dataclasses.replace(
            deps,
            registry=deps.registry if self._registry is None else self._registry,
            lt=deps.lt if self._lt is None else self._lt,
            webhook_secret=settings.webhook_secret,
        )
