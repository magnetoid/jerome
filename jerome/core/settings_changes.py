"""Writing admin settings: save, clear and reset, each with an audit row. Never commits."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jerome.config import Settings
from jerome.core.app_settings import (
    SECRET_KEYS,
    InvalidSetting,
    UnknownSetting,
    overlay,
    validate,
    value_of,
)
from jerome.core.secretbox import secret_box
from jerome.errors import Conflict
from jerome.orm import AppSetting, SettingsAudit


class SecretStorageUnavailable(Conflict):
    def __init__(self) -> None:
        super().__init__("secret storage is not set up: set JEROME_SECRET_KEY")


def load_rows(session: Session) -> list[AppSetting]:
    return list(session.scalars(select(AppSetting)))


def list_audit(session: Session, limit: int = 50) -> list[SettingsAudit]:
    """Newest first; rows written in one transaction share a timestamp, so id breaks ties."""
    stmt = (
        select(SettingsAudit)
        .order_by(SettingsAudit.at.desc(), SettingsAudit.id.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def record_audit(
    session: Session,
    *,
    actor: str,
    key: str,
    action: str,
    before: Any = None,
    after: Any = None,
) -> None:
    session.add(SettingsAudit(actor=actor, key=key, action=action, before=before, after=after))
    session.flush()


def _effective(session: Session, base: Settings):
    return overlay(base, load_rows(session), secret_box(base))


def _upsert(session: Session, key: str, actor: str, *, value: Any, secret: bytes | None) -> None:
    row = session.get(AppSetting, key)
    now = datetime.now(UTC)
    if row is None:
        session.add(
            AppSetting(key=key, value=value, secret=secret, updated_by=actor, updated_at=now)
        )
    else:
        row.value, row.secret, row.updated_by, row.updated_at = value, secret, actor, now
    session.flush()


def set_value(session: Session, key: str, value: Any, *, actor: str, base: Settings) -> None:
    stored = validate(key, value)
    before = value_of(_effective(session, base).settings, key)
    _upsert(session, key, actor, value=stored, secret=None)
    after = value_of(_effective(session, base).settings, key)
    record_audit(session, actor=actor, key=key, action="set", before=before, after=after)


def set_secret(
    session: Session, key: str, value: str | None, *, actor: str, base: Settings
) -> None:
    if key not in SECRET_KEYS:
        raise UnknownSetting(key)
    if value is None:
        _upsert(session, key, actor, value=None, secret=None)
        record_audit(session, actor=actor, key=key, action="cleared")
        return
    text = value.strip()
    if not text:
        raise InvalidSetting("enter a value, or clear it")
    box = secret_box(base)
    if box is None:
        raise SecretStorageUnavailable()
    was_set = bool(_effective(session, base).meta[key].is_set)
    _upsert(session, key, actor, value=None, secret=box.encrypt(text))
    record_audit(session, actor=actor, key=key, action="replaced" if was_set else "set")


def reset(session: Session, key: str, *, actor: str, base: Settings) -> None:
    secret = key in SECRET_KEYS
    row = session.get(AppSetting, key)
    if row is None:
        if not secret:
            value_of(base, key)  # unknown key -> UnknownSetting
        return
    before = None if secret else value_of(_effective(session, base).settings, key)
    session.delete(row)
    session.flush()
    after = None if secret else value_of(_effective(session, base).settings, key)
    record_audit(session, actor=actor, key=key, action="reset", before=before, after=after)
