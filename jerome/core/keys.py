import hashlib
import hmac
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jerome.core.app_settings import InvalidSetting
from jerome.core.settings_changes import record_audit
from jerome.errors import Conflict, NotFound
from jerome.orm import ApiKey

PREFIX = "jk_"
MAX_NAME = 100


class KeyNameTaken(Conflict):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_api_key(session: Session, name: str) -> str:
    """Create a key and return it. It is shown once; only its sha256 is stored."""
    token = PREFIX + secrets.token_urlsafe(24)[:32]
    session.add(ApiKey(name=name, key_hash=_hash(token)))
    session.flush()
    return token


def verify_api_key(session: Session, token: str) -> str | None:
    """Return the key's name, or None if the token is unknown or revoked."""
    digest = _hash(token)
    row = session.scalar(select(ApiKey).where(ApiKey.key_hash == digest))
    if row is None or row.revoked_at is not None:
        return None
    if not hmac.compare_digest(row.key_hash, digest):
        return None
    return row.name


def create_named_key(session: Session, name: str, *, actor: str) -> tuple[ApiKey, str]:
    """Create a key for the admin page: a unique name, audited. Returns the row and the token."""
    name = name.strip()
    if not name or len(name) > MAX_NAME:
        raise InvalidSetting(f"name must be 1 to {MAX_NAME} characters")
    existing = session.scalar(select(ApiKey).where(ApiKey.name == name))
    if existing is not None:
        if existing.revoked_at is None:
            raise KeyNameTaken("a key with this name already exists")
        raise KeyNameTaken("a key with this name existed; choose another name")
    token = create_api_key(session, name)
    key = session.scalar(select(ApiKey).where(ApiKey.name == name))
    assert key is not None
    record_audit(session, actor=actor, key=f"api_key:{name}", action="key_created")
    return key, token


def list_api_keys(session: Session) -> list[ApiKey]:
    return list(session.scalars(select(ApiKey).order_by(ApiKey.created_at, ApiKey.id)))


def revoke_api_key(session: Session, key_id: int, *, actor: str) -> ApiKey:
    key = session.get(ApiKey, key_id)
    if key is None:
        raise NotFound(f"unknown key: {key_id}")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        session.flush()
        record_audit(session, actor=actor, key=f"api_key:{key.name}", action="key_revoked")
    return key
