import pytest

from jerome.core.users import (
    authenticate,
    change_password,
    create_user,
    list_users,
    reset_password,
    update_user,
)
from jerome.errors import Conflict, InvalidChangeError


def test_create_user_with_generated_password_must_change_it(session):
    user, pw = create_user(session, "Ana@x.org", "Ana")
    assert pw is not None
    assert user.must_change_password
    assert user.email == "ana@x.org"
    assert authenticate(session, "ANA@x.org ", pw) is user
    assert user.last_login_at is not None


def test_duplicate_email_conflicts(session):
    create_user(session, "ana@x.org", "Ana")
    with pytest.raises(Conflict):
        create_user(session, " ANA@X.org", "Other")


def test_authenticate_rejects_wrong_password_and_inactive_user(session):
    user, pw = create_user(session, "ana@x.org", "Ana")
    create_user(session, "bo@x.org", "Bo")
    assert authenticate(session, "ana@x.org", "wrong-password-1") is None
    assert authenticate(session, "nobody@x.org", pw) is None
    update_user(session, user.id, active=False)
    assert authenticate(session, "ana@x.org", pw) is None


def test_change_password_needs_current_and_clears_flag(session):
    user, pw = create_user(session, "ana@x.org", "Ana")
    with pytest.raises(InvalidChangeError):
        change_password(session, user, "not-the-password", "a-new-password-1")
    change_password(session, user, pw, "a-new-password-1")
    assert not user.must_change_password
    assert authenticate(session, "ana@x.org", "a-new-password-1") is user
    assert authenticate(session, "ana@x.org", pw) is None


def test_short_password_is_refused(session):
    with pytest.raises(InvalidChangeError):
        create_user(session, "ana@x.org", "Ana", password="123456789")
    user, pw = create_user(session, "bo@x.org", "Bo", password="1234567890")
    assert pw is None and not user.must_change_password
    with pytest.raises(InvalidChangeError):
        change_password(session, user, "1234567890", "123456789")


def test_reset_password_returns_new_temporary_password(session):
    user, pw = create_user(session, "ana@x.org", "Ana", password="1234567890")
    new = reset_password(session, user.id)
    assert user.must_change_password
    assert authenticate(session, "ana@x.org", new) is user
    assert authenticate(session, "ana@x.org", "1234567890") is None


def test_list_users_ordered_by_name(session):
    create_user(session, "z@x.org", "Zed")
    create_user(session, "a@x.org", "Amy")
    assert [u.name for u in list_users(session)] == ["Amy", "Zed"]


def test_cannot_deactivate_last_active_member(session):
    a, _ = create_user(session, "a@x.org", "A")
    b, _ = create_user(session, "b@x.org", "B")
    update_user(session, a.id, active=False)
    with pytest.raises(Conflict, match="last active member"):
        update_user(session, b.id, active=False)
    assert update_user(session, b.id, name="Bee").name == "Bee"


def test_unknown_or_inactive_email_still_runs_a_password_verify(session, monkeypatch):
    """No early return: a wrong email costs the same argon2 verify as a wrong password."""
    from argon2 import PasswordHasher

    calls = []
    real_verify = PasswordHasher.verify

    def spy(self, hash, password):
        calls.append(password)
        return real_verify(self, hash, password)

    user, pw = create_user(session, "ana@x.org", "Ana")
    create_user(session, "bo@x.org", "Bo")
    monkeypatch.setattr(PasswordHasher, "verify", spy)

    assert authenticate(session, "nobody@x.org", "a-guess-123") is None
    assert calls == ["a-guess-123"]

    update_user(session, user.id, active=False)
    assert authenticate(session, "ana@x.org", pw) is None
    assert calls == ["a-guess-123", pw]
