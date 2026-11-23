import pytest

from jerome.core.app_settings import InvalidSetting
from jerome.core.keys import (
    KeyNameTaken,
    create_named_key,
    list_api_keys,
    revoke_api_key,
    verify_api_key,
)
from jerome.core.settings_changes import list_audit
from jerome.errors import NotFound


def test_key_names_stay_unique(session):
    key, token = create_named_key(session, " janus ", actor="a")
    assert key.name == "janus" and verify_api_key(session, token) == "janus"
    with pytest.raises(KeyNameTaken, match="already exists"):
        create_named_key(session, "janus", actor="a")
    revoke_api_key(session, key.id, actor="a")
    assert verify_api_key(session, token) is None
    with pytest.raises(KeyNameTaken, match="existed; choose another name"):
        create_named_key(session, "janus", actor="a")


def test_bad_names_rejected(session):
    for name in ("", "   ", "x" * 101):
        with pytest.raises(InvalidSetting):
            create_named_key(session, name, actor="a")
    create_named_key(session, "x" * 100, actor="a")


def test_revoke_audits_once_and_unknown_is_not_found(session):
    key, _ = create_named_key(session, "one", actor="a")
    first = revoke_api_key(session, key.id, actor="b")
    again = revoke_api_key(session, key.id, actor="b")
    assert first.revoked_at is not None and again.revoked_at == first.revoked_at
    rows = list_audit(session)
    assert [(r.action, r.key, r.actor) for r in rows] == [
        ("key_revoked", "api_key:one", "b"),
        ("key_created", "api_key:one", "a"),
    ]
    with pytest.raises(NotFound):
        revoke_api_key(session, 999999, actor="a")


def test_list_ordered_by_created(session):
    create_named_key(session, "b", actor="a")
    create_named_key(session, "a", actor="a")
    assert [k.name for k in list_api_keys(session)] == ["b", "a"]
