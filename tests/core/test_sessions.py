import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from jerome.core.sessions import (
    create_session,
    resolve_session,
    revoke_session,
    revoke_user_sessions,
)
from jerome.core.users import change_password, create_user, reset_password, update_user
from jerome.orm import UserSession

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def test_session_round_trip_and_sliding_expiry(session):
    user, _ = create_user(session, "ana@x.org", "Ana")
    token = create_session(session, user, days=30, now=NOW)

    row = session.scalar(select(UserSession).where(UserSession.user_id == user.id))
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row.token_hash
    assert row.expires_at == NOW + timedelta(days=30)

    later = NOW + timedelta(days=10)
    assert resolve_session(session, token, days=30, now=later) is user
    assert row.last_seen_at == later
    assert row.expires_at == later + timedelta(days=30)

    # past the first expiry, but within 30 days of the last use: still valid
    assert resolve_session(session, token, days=30, now=NOW + timedelta(days=35)) is user
    assert resolve_session(session, token + "x", days=30, now=later) is None


def test_expired_session_is_refused(session):
    user, _ = create_user(session, "ana@x.org", "Ana")
    token = create_session(session, user, days=30, now=NOW)
    assert resolve_session(session, token, days=30, now=NOW + timedelta(days=30)) is None
    assert resolve_session(session, token, days=30, now=NOW + timedelta(days=31)) is None


def test_inactive_user_session_is_refused(session):
    user, _ = create_user(session, "ana@x.org", "Ana")
    token = create_session(session, user, days=30, now=NOW)
    user.active = False  # directly, so the session row is still there
    session.flush()
    assert resolve_session(session, token, days=30, now=NOW) is None


def test_revoke_one_session_or_all_of_a_users_sessions(session):
    ana, _ = create_user(session, "ana@x.org", "Ana")
    bo, _ = create_user(session, "bo@x.org", "Bo")
    first = create_session(session, ana, days=30, now=NOW)
    second = create_session(session, ana, days=30, now=NOW)
    other = create_session(session, bo, days=30, now=NOW)

    revoke_session(session, first)
    assert resolve_session(session, first, days=30, now=NOW) is None
    assert resolve_session(session, second, days=30, now=NOW) is ana

    revoke_user_sessions(session, ana.id)
    assert resolve_session(session, second, days=30, now=NOW) is None
    assert resolve_session(session, other, days=30, now=NOW) is bo


def test_deactivating_a_user_revokes_their_sessions(session):
    ana, _ = create_user(session, "ana@x.org", "Ana")
    create_user(session, "bo@x.org", "Bo")
    create_session(session, ana, days=30, now=NOW)
    update_user(session, ana.id, active=False)
    assert session.scalar(select(UserSession).where(UserSession.user_id == ana.id)) is None


def test_reset_password_revokes_all_the_users_sessions(session):
    ana, _ = create_user(session, "ana@x.org", "Ana")
    bo, _ = create_user(session, "bo@x.org", "Bo")
    token = create_session(session, ana, days=30, now=NOW)
    other = create_session(session, bo, days=30, now=NOW)
    reset_password(session, ana.id)
    assert resolve_session(session, token, days=30, now=NOW) is None
    assert resolve_session(session, other, days=30, now=NOW) is bo


def test_change_password_keeps_only_the_given_session(session):
    ana, _ = create_user(session, "ana@x.org", "Ana", "old-password-1")
    current = create_session(session, ana, days=30, now=NOW)
    elsewhere = create_session(session, ana, days=30, now=NOW)
    change_password(session, ana, "old-password-1", "new-password-1", keep_token=current)
    assert resolve_session(session, current, days=30, now=NOW) is ana
    assert resolve_session(session, elsewhere, days=30, now=NOW) is None

    change_password(session, ana, "new-password-1", "newer-password-1")  # no session to keep
    assert resolve_session(session, current, days=30, now=NOW) is None
