"""Server-side login sessions. The cookie holds a random token; only its sha256 is stored."""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from jerome.orm import User, UserSession

# A live session's slide is written at most this often. Writing on every request would hold
# a row lock on the session for the whole request transaction, queueing a browser's other
# requests behind any long one and blocking revocation.
SLIDE_INTERVAL = timedelta(minutes=5)


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

    A live session slides: once ``last_seen_at`` is ``SLIDE_INTERVAL`` old, this use is
    recorded and the session now expires ``days`` after it.
    """
    row = session.scalar(select(UserSession).where(UserSession.token_hash == _hash(token)))
    if row is None or row.expires_at <= now:
        return None
    user = session.get(User, row.user_id)
    if user is None or not user.active:
        return None
    if now - row.last_seen_at >= SLIDE_INTERVAL:
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
