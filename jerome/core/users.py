import secrets
import threading
from datetime import UTC, datetime
from functools import cache
from typing import get_args

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jerome.core.sessions import revoke_user_sessions
from jerome.errors import Conflict, InvalidChangeError, NotFound
from jerome.orm import User
from jerome.types import Role

MIN_PASSWORD_LENGTH = 10
_hasher = PasswordHasher()
# Each argon2 hash or verify uses 64 MiB. Login is unauthenticated, so a burst of attempts
# must queue rather than run all at once: at most this many run together.
_ARGON2_SLOTS = threading.BoundedSemaphore(4)


def _hash(password: str) -> str:
    with _ARGON2_SLOTS:
        return _hasher.hash(password)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _check_length(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise InvalidChangeError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def _temporary_password() -> str:
    return secrets.token_urlsafe(12)


def _get(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFound(f"user {user_id} not found")
    return user


def get_user_by_email(session: Session, email: str) -> User:
    user = session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        raise NotFound(f"no user with email {normalize_email(email)}")
    return user


def _check_role(role: str) -> None:
    if role not in get_args(Role):
        raise InvalidChangeError(f"unknown role {role!r}")


def _lock_active_super_admins(session: Session) -> list[int]:
    """Select every active super admin FOR UPDATE in id order and return their ids.
    Concurrent removals then queue up in one fixed order, so they cannot deadlock, as long
    as each takes these locks before writing to any super admin row."""
    return list(
        session.scalars(
            select(User.id)
            .where(User.role == "super_admin", User.active)
            .order_by(User.id)
            .with_for_update()
        ).all()
    )


def _guard_last_super_admin(session: Session, user: User) -> None:
    """Refuse to remove the only active super admin. Concurrent removals cannot both pass,
    because each locks all active super admins first."""
    if not any(uid != user.id for uid in _lock_active_super_admins(session)):
        raise Conflict("cannot remove the last active super admin")


def set_role(session: Session, user_id: int, role: str) -> User:
    _check_role(role)
    user = _get(session, user_id)
    if user.role == "super_admin" and user.active and role != "super_admin":
        _guard_last_super_admin(session, user)
    user.role = role
    session.flush()
    return user


def create_user(
    session: Session,
    email: str,
    name: str,
    password: str | None = None,
    role: Role | None = None,
) -> tuple[User, str | None]:
    """Create a user. Without a password, a temporary one is generated and returned.
    Without a role, the user becomes super admin if none is active, else a member."""
    email = normalize_email(email)
    if role is not None:
        _check_role(role)
    temporary = password is None
    if password is None:
        password = _temporary_password()
    _check_length(password)
    if session.scalar(select(User).where(User.email == email)) is not None:
        raise Conflict(f"a user with email {email} already exists")
    if role is None:
        has_super_admin = session.scalar(
            select(func.count()).select_from(User).where(User.role == "super_admin", User.active)
        )
        role = "member" if has_super_admin else "super_admin"
    user = User(
        email=email,
        name=name,
        role=role,
        password_hash=_hash(password),
        must_change_password=temporary,
    )
    try:
        with session.begin_nested():
            session.add(user)
    except IntegrityError as exc:
        raise Conflict(f"a user with email {email} already exists") from exc
    return user, (password if temporary else None)


def _verify_hash(password_hash: str, password: str) -> bool:
    try:
        with _ARGON2_SLOTS:
            return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def _verify(user: User, password: str) -> bool:
    return _verify_hash(user.password_hash, password)


@cache
def _dummy_hash() -> str:
    """A hash made with the same parameters as real ones, so verifying against it costs the same."""
    return _hash(secrets.token_urlsafe(16))


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or not user.active:
        # Spend the same argon2 verify as a wrong password, so response time does not
        # reveal which emails belong to (active) members.
        _verify_hash(_dummy_hash(), password)
        return None
    if not _verify(user, password):
        return None
    user.last_login_at = datetime.now(UTC)
    session.flush()
    return user


def change_password(
    session: Session, user: User, current: str, new: str, *, keep_token: str | None = None
) -> None:
    """Change the user's own password and end their other sessions. ``keep_token`` is the
    session making the change, which stays logged in; without it every session ends."""
    if not _verify(user, current):
        raise InvalidChangeError("current password is wrong")
    _check_length(new)
    user.password_hash = _hash(new)
    user.must_change_password = False
    revoke_user_sessions(session, user.id, keep_token=keep_token)
    session.flush()


def reset_password(session: Session, user_id: int) -> str:
    user = _get(session, user_id)
    password = _temporary_password()
    user.password_hash = _hash(password)
    user.must_change_password = True
    revoke_user_sessions(session, user.id)  # whoever held the old password is logged out
    session.flush()
    return password


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.name, User.id)))


def update_user(
    session: Session, user_id: int, *, name: str | None = None, active: bool | None = None
) -> User:
    user = _get(session, user_id)
    if name is not None:
        user.name = name
    if active is not None:
        if not active and user.active:
            others = session.scalar(
                select(func.count()).select_from(User).where(User.active, User.id != user.id)
            )
            if not others:
                raise Conflict("cannot deactivate the last active member")
            if user.role == "super_admin":
                _guard_last_super_admin(session, user)
        user.active = active
        if not active:
            revoke_user_sessions(session, user.id)  # their open browsers are logged out now
    session.flush()
    return user


def update_member(
    session: Session,
    user_id: int,
    *,
    name: str | None = None,
    active: bool | None = None,
    role: str | None = None,
) -> User:
    """Apply one team-page edit: name, active flag and role together. When the edit may
    remove a super admin, the super admin locks are taken before anything is written:
    a rename flushed first would lock the target row out of order and could deadlock."""
    if role is not None:
        _check_role(role)
    target = _get(session, user_id)
    removes = active is False or (role is not None and role != "super_admin")
    if removes and target.active and target.role == "super_admin":
        _lock_active_super_admins(session)
    user = update_user(session, user_id, name=name, active=active)
    if role is not None:
        set_role(session, user_id, role)
    return user
