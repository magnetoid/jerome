import pytest

from jerome.core.users import (
    authenticate,
    change_password,
    create_user,
    get_user_by_email,
    list_users,
    reset_password,
    set_role,
    update_member,
    update_user,
)
from jerome.errors import Conflict, InvalidChangeError, NotFound


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
    create_user(session, "bo@x.org", "Bo", role="super_admin")
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
    a, _ = create_user(session, "a@x.org", "A", role="super_admin")
    b, _ = create_user(session, "b@x.org", "B", role="super_admin")
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
    create_user(session, "bo@x.org", "Bo", role="super_admin")
    monkeypatch.setattr(PasswordHasher, "verify", spy)

    assert authenticate(session, "nobody@x.org", "a-guess-123") is None
    assert calls == ["a-guess-123"]

    update_user(session, user.id, active=False)
    assert authenticate(session, "ana@x.org", pw) is None
    assert calls == ["a-guess-123", pw]


def test_argon2_work_is_bounded_to_a_few_at_a_time(monkeypatch):
    """Each argon2 hash or verify takes 64 MiB, so a burst of logins must not run them all at
    once: at most four run together, the rest wait their turn."""
    import threading
    import time

    from argon2 import PasswordHasher

    from jerome.core import users

    running, peak, lock = 0, 0, threading.Lock()

    def slow(self, *_args):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return "hashed"

    monkeypatch.setattr(PasswordHasher, "verify", slow)
    monkeypatch.setattr(PasswordHasher, "hash", slow)
    work = [
        lambda: users._verify_hash("h", "pw"),
        users._dummy_hash.__wrapped__,  # the unknown-email path's hash, uncached
        lambda: users._hash("pw"),
    ]
    threads = [threading.Thread(target=work[i % len(work)]) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 4


def test_first_user_is_super_admin_and_later_ones_are_members(session):
    a, _ = create_user(session, "a@x.org", "A")
    b, _ = create_user(session, "b@x.org", "B")
    c, _ = create_user(session, "c@x.org", "C", role="super_admin")
    assert (a.role, b.role, c.role) == ("super_admin", "member", "super_admin")


def test_set_role_promotes_and_demotes(session):
    a, _ = create_user(session, "a@x.org", "A")
    b, _ = create_user(session, "b@x.org", "B")
    assert set_role(session, b.id, "super_admin").role == "super_admin"
    assert set_role(session, a.id, "member").role == "member"


def test_cannot_demote_or_deactivate_the_last_active_super_admin(session):
    a, _ = create_user(session, "a@x.org", "A")
    create_user(session, "b@x.org", "B")  # an active member keeps the member guard quiet
    create_user(session, "z@x.org", "Z", role="super_admin")
    update_user(session, get_user_by_email(session, "z@x.org").id, active=False)
    # an inactive super admin does not count
    with pytest.raises(Conflict, match="cannot remove the last active super admin"):
        set_role(session, a.id, "member")
    with pytest.raises(Conflict, match="cannot remove the last active super admin"):
        update_user(session, a.id, active=False)


def test_unknown_role_and_unknown_email_are_refused(session):
    a, _ = create_user(session, "a@x.org", "A")
    with pytest.raises(InvalidChangeError):
        set_role(session, a.id, "owner")
    with pytest.raises(InvalidChangeError):
        create_user(session, "b@x.org", "B", role="owner")
    with pytest.raises(NotFound):
        get_user_by_email(session, "nobody@x.org")
    assert get_user_by_email(session, " A@X.org ").id == a.id


def test_crossing_super_admin_demotions_do_not_deadlock(engine, session_factory):
    """Two admins demote themselves at once: exactly one succeeds, the other gets the
    Conflict, and Postgres never reports a deadlock."""
    import threading

    with session_factory() as s, s.begin():
        a, _ = create_user(s, "a@x.org", "A", role="super_admin")
        b, _ = create_user(s, "b@x.org", "B", role="super_admin")
        ids = (a.id, b.id)

    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def demote(user_id: int) -> None:
        try:
            with session_factory() as s, s.begin():
                barrier.wait(timeout=10)
                set_role(s, user_id, "member")
            outcomes.append("ok")
        except Exception as exc:  # noqa: BLE001 - the test inspects the type
            outcomes.append(exc)

    threads = [threading.Thread(target=demote, args=(i,)) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert outcomes.count("ok") == 1, outcomes
    conflicts = [o for o in outcomes if isinstance(o, Conflict)]
    assert len(conflicts) == 1, outcomes


@pytest.mark.parametrize(
    "change",
    [{"role": "member"}, {"active": False}],
    ids=["demote", "deactivate"],
)
def test_crossing_edits_that_rename_and_remove_super_admins_do_not_deadlock(
    engine, session_factory, change
):
    """A team-page edit renames and removes a super admin in one request. Two admins doing
    that to themselves at once: one succeeds, the other gets the Conflict, never a deadlock.
    Several rounds, because the deadlock depends on how the two transactions interleave."""
    import threading

    with session_factory() as s, s.begin():
        a, _ = create_user(s, "a@x.org", "A", role="super_admin")
        b, _ = create_user(s, "b@x.org", "B", role="super_admin")
        create_user(s, "m@x.org", "M", role="member")  # so deactivating stays allowed
        ids = (a.id, b.id)

    for round_ in range(6):
        with session_factory() as s, s.begin():
            for uid in ids:
                update_member(s, uid, active=True, role="super_admin")

        barrier = threading.Barrier(2)
        outcomes: list[object] = []

        def edit(user_id: int, barrier: threading.Barrier, outcomes: list[object]) -> None:
            try:
                with session_factory() as s, s.begin():
                    barrier.wait(timeout=10)
                    update_member(s, user_id, name="Renamed", **change)
                outcomes.append("ok")
            except Exception as exc:  # noqa: BLE001 - the test inspects the type
                outcomes.append(exc)

        threads = [threading.Thread(target=edit, args=(i, barrier, outcomes)) for i in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert outcomes.count("ok") == 1, (round_, outcomes)
        assert sum(isinstance(o, Conflict) for o in outcomes) == 1, (round_, outcomes)
