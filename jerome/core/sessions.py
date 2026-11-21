"""Server-side login sessions. The cookie holds a random token; only its sha256 is stored."""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from jerome.orm import User, UserSession


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(session: Session, user: User, *, days: int, now: datetime) -> str:
    """Start a session for the user and return its raw token, which is never stored."""
    token = secrets.token_urlsafe(32)
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=_hash(token),
            created_at=now,
            expires_at=now + timedelta(days=days),
            last_seen_at=now,
        )
    )
    session.flush()
    return token


def resolve_session(session: Session, token: str, *, days: int, now: datetime) -> User | None:
    """The session's user, or None if the session is unknown, expired or the user inactive.

    A live session slides: it now expires ``days`` after this use.
    """
    row = session.scalar(select(UserSession).where(UserSession.token_hash == _hash(token)))
    if row is None or row.expires_at <= now:
        return None
    user = session.get(User, row.user_id)
    if user is None or not user.active:
        return None
    row.last_seen_at = now
    row.expires_at = now + timedelta(days=days)
    session.flush()
    return user


def revoke_session(session: Session, token: str) -> None:
    session.execute(delete(UserSession).where(UserSession.token_hash == _hash(token)))


def revoke_user_sessions(session: Session, user_id: int, *, keep_token: str | None = None) -> None:
    """End all the user's sessions, except the one for ``keep_token`` if given."""
    stmt = delete(UserSession).where(UserSession.user_id == user_id)
    if keep_token is not None:
        stmt = stmt.where(UserSession.token_hash != _hash(keep_token))
    session.execute(stmt)
