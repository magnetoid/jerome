import pytest
from cryptography.fernet import Fernet

from jerome.config import Settings
from jerome.core.app_settings import InvalidSetting, UnknownSetting
from jerome.core.settings_changes import (
    SecretStorageUnavailable,
    list_audit,
    reset,
    set_secret,
    set_value,
)
from jerome.orm import AppSetting

BOX_SETTINGS = Settings(_env_file=None, secret_key=Fernet.generate_key().decode())


def test_set_value_audits_before_and_after(session):
    set_value(session, "text_rpm", 12, actor="ana@x.org", base=BOX_SETTINGS)
    set_value(session, "text_rpm", 20, actor="ana@x.org", base=BOX_SETTINGS)
    rows = list_audit(session)
    assert [(r.action, r.before, r.after) for r in rows] == [("set", 12, 20), ("set", 30, 12)]


def test_set_value_rejects_invalid_unknown_and_secret_keys(session):
    with pytest.raises(InvalidSetting):
        set_value(session, "text_rpm", 0, actor="a", base=BOX_SETTINGS)
    with pytest.raises(UnknownSetting):
        set_value(session, "nope", 1, actor="a", base=BOX_SETTINGS)
    with pytest.raises(UnknownSetting):
        set_value(session, "webhook_secret", "x", actor="a", base=BOX_SETTINGS)
    assert list_audit(session) == []


def test_secret_actions_and_ciphertext(session):
    set_secret(session, "anthropic_api_key", " sk-1 ", actor="a", base=BOX_SETTINGS)
    stored = session.get(AppSetting, "anthropic_api_key")
    assert b"sk-1" not in bytes(stored.secret) and stored.value is None
    set_secret(session, "anthropic_api_key", "sk-2", actor="a", base=BOX_SETTINGS)
    set_secret(session, "anthropic_api_key", None, actor="a", base=BOX_SETTINGS)
    assert [r.action for r in list_audit(session)] == ["cleared", "replaced", "set"]
    assert all(r.before is None and r.after is None for r in list_audit(session))
    stored = session.get(AppSetting, "anthropic_api_key")
    assert stored.secret is None  # cleared


def test_secret_validation(session):
    with pytest.raises(InvalidSetting, match="enter a value, or clear it"):
        set_secret(session, "webhook_secret", "   ", actor="a", base=BOX_SETTINGS)
    with pytest.raises(UnknownSetting):
        set_secret(session, "text_rpm", "x", actor="a", base=BOX_SETTINGS)
    with pytest.raises(UnknownSetting):
        set_secret(session, "nope", None, actor="a", base=BOX_SETTINGS)


def test_secret_needs_storage_but_clear_does_not(session):
    with pytest.raises(SecretStorageUnavailable, match="set JEROME_SECRET_KEY"):
        set_secret(session, "webhook_secret", "s", actor="a", base=Settings(_env_file=None))
    assert session.get(AppSetting, "webhook_secret") is None
    set_secret(session, "webhook_secret", None, actor="a", base=Settings(_env_file=None))
    assert [r.action for r in list_audit(session)] == ["cleared"]


def test_reset_removes_override(session):
    set_value(session, "session_days", 3, actor="a", base=BOX_SETTINGS)
    reset(session, "session_days", actor="a", base=BOX_SETTINGS)
    assert session.get(AppSetting, "session_days") is None
    latest = list_audit(session)[0]
    assert latest.action == "reset" and latest.before == 3
    reset(session, "session_days", actor="a", base=BOX_SETTINGS)  # no row: no new audit
    assert len(list_audit(session)) == 2


def test_reset_secret_audits_null(session):
    set_secret(session, "webhook_secret", "s", actor="a", base=BOX_SETTINGS)
    reset(session, "webhook_secret", actor="a", base=BOX_SETTINGS)
    latest = list_audit(session)[0]
    assert latest.action == "reset" and latest.before is None and latest.after is None
    assert session.get(AppSetting, "webhook_secret") is None


def test_list_audit_limit(session):
    for n in range(1, 6):
        set_value(session, "text_rpm", n, actor="a", base=BOX_SETTINGS)
    assert [r.after for r in list_audit(session, limit=2)] == [5, 4]


def test_reset_unknown_key_raises(session):
    with pytest.raises(UnknownSetting):
        reset(session, "nope", actor="a", base=BOX_SETTINGS)
