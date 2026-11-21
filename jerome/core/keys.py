import hashlib
import hmac
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from jerome.orm import ApiKey

PREFIX = "jk_"


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
