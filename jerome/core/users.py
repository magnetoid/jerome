import secrets
from datetime import UTC, datetime
from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jerome.core.sessions import revoke_user_sessions
from jerome.errors import Conflict, InvalidChangeError, NotFound
from jerome.orm import User

MIN_PASSWORD_LENGTH = 10
_hasher = PasswordHasher()


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


def create_user(
    session: Session, email: str, name: str, password: str | None = None
) -> tuple[User, str | None]:
    """Create a user. Without a password, a temporary one is generated and returned."""
    email = normalize_email(email)
    temporary = password is None
    if password is None:
        password = _temporary_password()
    _check_length(password)
    if session.scalar(select(User).where(User.email == email)) is not None:
        raise Conflict(f"a user with email {email} already exists")
    user = User(
        email=email,
        name=name,
        password_hash=_hasher.hash(password),
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
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def _verify(user: User, password: str) -> bool:
    return _verify_hash(user.password_hash, password)


@cache
def _dummy_hash() -> str:
    """A hash made with the same parameters as real ones, so verifying against it costs the same."""
    return _hasher.hash(secrets.token_urlsafe(16))


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


def change_password(session: Session, user: User, current: str, new: str) -> None:
    if not _verify(user, current):
        raise InvalidChangeError("current password is wrong")
    _check_length(new)
    user.password_hash = _hasher.hash(new)
    user.must_change_password = False
    session.flush()


def reset_password(session: Session, user_id: int) -> str:
    user = _get(session, user_id)
    password = _temporary_password()
    user.password_hash = _hasher.hash(password)
    user.must_change_password = True
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
        user.active = active
        if not active:
            revoke_user_sessions(session, user.id)  # their open browsers are logged out now
    session.flush()
    return user
