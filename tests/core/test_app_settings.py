from datetime import UTC, datetime
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from jerome.config import Settings
from jerome.core.app_settings import (
    RUNTIME_KEYS,
    SECRET_KEYS,
    InvalidSetting,
    Meta,
    UnknownSetting,
    overlay,
    validate,
    value_of,
)
from jerome.core.secretbox import SecretBox
from jerome.errors import NotFound
from jerome.orm import AppSetting

FIXED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "JEROME_SECRET_KEY",
        "JEROME_TEXT_RPM",
        "JEROME_MODELS",
        "JEROME_WEBHOOK_SECRET",
        "JEROME_PRICES",
        "JEROME_SESSION_DAYS",
        "JEROME_MAX_UPLOAD_MB",
        "JEROME_LANGUAGETOOL_URL",
        "JEROME_PUBLIC_BASE_URL",
        "JEROME_TEXT_DAILY_COST_CAP",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def row(key, value=None, secret=None):
    return AppSetting(key=key, value=value, secret=secret, updated_by="ana@x.org", updated_at=FIXED)


def test_key_lists():
    assert RUNTIME_KEYS == (
        "models.ai",
        "prices",
        "text_daily_cost_cap",
        "text_rpm",
        "max_upload_mb",
        "session_days",
        "languagetool_url",
        "public_base_url",
    )
    assert SECRET_KEYS == ("anthropic_api_key", "webhook_secret", "openai_compat_api_key")
    assert issubclass(UnknownSetting, NotFound)
    assert str(UnknownSetting("database_url")) == "unknown setting: database_url"


def test_validate_rejects():
    bad = [
        ("text_rpm", True),
        ("text_rpm", 0),
        ("text_rpm", 10001),
        ("session_days", 2.5),
        ("max_upload_mb", 501),
        ("text_daily_cost_cap", -1),
        ("models.ai", {"provider": "openai_compat", "model": "m"}),
        ("models.ai", {"provider": "anthropic", "model": "claude-opus-5-5", "api_key": "k"}),
        ("models.ai", {"provider": "anthropic", "model": "", "effort": "high"}),
        ("models.ai", {"provider": "anthropic", "model": "m", "effort": "huge"}),
        ("prices", {"m": {"input": 1, "output": 1, "cache_read": -1, "cache_write": 1}}),
        ("prices", {"m": {"input": 1}}),
        ("languagetool_url", "ftp://lt"),
        ("public_base_url", "https://x.org/path"),
        ("public_base_url", "https://x.org?q=1"),
        ("public_base_url", "http://x.org"),
    ]
    for key, value in bad:
        with pytest.raises(InvalidSetting):
            validate(key, value)
    with pytest.raises(UnknownSetting):
        validate("database_url", "x")


def test_validate_api_key_message_names_secret_setting_without_value():
    with pytest.raises(InvalidSetting, match="openai_compat_api_key") as info:
        validate("models.ai", {"provider": "anthropic", "model": "m", "api_key": "sekrit"})
    assert "sekrit" not in str(info.value)


def test_validate_normalizes():
    assert (
        validate("public_base_url", "https://jerome.example.com/") == "https://jerome.example.com"
    )
    assert validate("public_base_url", "http://localhost:8000") == "http://localhost:8000"
    assert validate("public_base_url", "") is None
    assert validate("public_base_url", None) is None
    assert validate("text_daily_cost_cap", 7.5) == "7.5"
    assert validate("text_daily_cost_cap", "3") == "3"
    assert validate("languagetool_url", "http://lt:8010/") == "http://lt:8010"
    assert validate("models.ai", {"provider": "anthropic", "model": "m", "effort": ""}) == {
        "provider": "anthropic",
        "model": "m",
    }
    assert validate(
        "models.ai",
        {"provider": "openai_compat", "model": "m", "base_url": "http://llm:1/", "effort": "max"},
    ) == {"provider": "openai_compat", "model": "m", "effort": "max", "base_url": "http://llm:1"}
    assert validate(
        "prices", {"m": {"input": 1, "output": 2.5, "cache_read": 0, "cache_write": 3}}
    ) == {"m": {"input": 1, "output": 2.5, "cache_read": 0, "cache_write": 3}}


def test_overlay_sources_and_precedence():
    base = Settings(_env_file=None, text_rpm=40)
    eff = overlay(base, [row("session_days", 7)], None)
    assert eff.settings.session_days == 7 and eff.meta["session_days"].source == "app"
    assert eff.meta["session_days"].updated_by == "ana@x.org"
    assert eff.meta["text_rpm"].source == "environment" and eff.settings.text_rpm == 40
    assert eff.meta["max_upload_mb"].source == "default"
    assert eff.settings.models["ai"]["model"] == "claude-opus-5-5"
    assert set(eff.meta) == set(RUNTIME_KEYS) | set(SECRET_KEYS)


def test_overlay_does_not_mutate_base():
    base = Settings(_env_file=None)
    ai = {"provider": "anthropic", "model": "x"}
    overlay(base, [row("models.ai", ai)], None)
    assert base.models["ai"]["model"] == "claude-opus-5-5"


def test_overlay_models_ai_keeps_other_passes_and_injects_key():
    box = SecretBox(Fernet.generate_key().decode())
    base = Settings(
        _env_file=None,
        models={
            "ai": {"provider": "anthropic", "model": "a"},
            "other": {"provider": "anthropic", "model": "b"},
        },
    )
    ai = {"provider": "openai_compat", "model": "m", "base_url": "http://llm:1"}
    eff = overlay(
        base,
        [row("models.ai", ai), row("openai_compat_api_key", secret=box.encrypt("k1"))],
        box,
    )
    assert eff.settings.models["other"]["model"] == "b"
    assert eff.settings.models["ai"]["api_key"] == "k1"
    assert "api_key" not in value_of(eff.settings, "models.ai")
    assert eff.meta["models.ai"].source == "app"
    assert eff.meta["openai_compat_api_key"].is_set is True


def test_overlay_env_models_key_and_anthropic_drops_it():
    base = Settings(
        _env_file=None,
        models={
            "ai": {
                "provider": "openai_compat",
                "model": "m",
                "base_url": "http://l",
                "api_key": "envk",
            }
        },
    )
    eff = overlay(base, [], None)
    assert eff.settings.models["ai"]["api_key"] == "envk"
    assert eff.meta["openai_compat_api_key"].source == "environment"
    assert eff.meta["openai_compat_api_key"].is_set is True
    eff = overlay(base, [row("models.ai", {"provider": "anthropic", "model": "x"})], None)
    assert "api_key" not in eff.settings.models["ai"]


def test_overlay_cleared_openai_key_removes_env_key():
    base = Settings(
        _env_file=None,
        models={
            "ai": {
                "provider": "openai_compat",
                "model": "m",
                "base_url": "http://l",
                "api_key": "envk",
            }
        },
    )
    eff = overlay(base, [row("openai_compat_api_key")], None)
    assert "api_key" not in eff.settings.models["ai"]
    assert eff.meta["openai_compat_api_key"].is_set is False


def test_overlay_anthropic_key_from_app_is_secretstr():
    box = SecretBox(Fernet.generate_key().decode())
    base = Settings(_env_file=None, anthropic_api_key="envkey")
    eff = overlay(base, [row("anthropic_api_key", secret=box.encrypt("appkey"))], box)
    assert isinstance(eff.settings.anthropic_api_key, SecretStr)
    assert eff.settings.anthropic_api_key.get_secret_value() == "appkey"
    assert base.anthropic_api_key.get_secret_value() == "envkey"


def test_overlay_secret_without_row():
    eff = overlay(Settings(_env_file=None, webhook_secret="e"), [], None)
    assert eff.meta["webhook_secret"] == Meta(source="environment", is_set=True)
    assert eff.meta["anthropic_api_key"] == Meta(source="default", is_set=False)


def test_overlay_cleared_secret_wins_over_env():
    base = Settings(_env_file=None, webhook_secret="env-secret")
    eff = overlay(base, [row("webhook_secret")], None)
    assert eff.settings.webhook_secret is None
    assert eff.meta["webhook_secret"] == Meta(
        source="app", updated_by="ana@x.org", updated_at=FIXED, is_set=False
    )


def test_overlay_wrong_key_falls_back_and_flags():
    token = SecretBox(Fernet.generate_key().decode()).encrypt("app-secret")
    other = SecretBox(Fernet.generate_key().decode())
    base = Settings(_env_file=None, webhook_secret="env-secret")
    for box in (other, None):
        eff = overlay(base, [row("webhook_secret", secret=token)], box)
        assert eff.settings.webhook_secret == "env-secret"
        assert eff.meta["webhook_secret"].unreadable is True


def test_overlay_invalid_stored_value_falls_back():
    eff = overlay(Settings(_env_file=None), [row("text_rpm", "lots")], None)
    assert eff.settings.text_rpm == 30 and eff.meta["text_rpm"].unreadable is True


def test_overlay_cost_cap_and_public_url():
    eff = overlay(
        Settings(_env_file=None),
        [row("text_daily_cost_cap", "7.5"), row("public_base_url", "https://j.example.com")],
        None,
    )
    assert eff.settings.text_daily_cost_cap == Decimal("7.5")
    assert value_of(eff.settings, "text_daily_cost_cap") == "7.5"
    assert value_of(eff.settings, "public_base_url") == "https://j.example.com"
    assert value_of(Settings(_env_file=None), "public_base_url") is None


def test_validate_huge_int_is_invalid_not_a_crash():
    entry = {"input": 10**400, "output": 1, "cache_read": 0, "cache_write": 0}
    try:
        validate("prices", {"m": entry})
    except InvalidSetting:
        pass


def test_public_base_url_rebuilt_and_rejects_empty_markers():
    assert validate("public_base_url", "HTTPS://X.Org:8443/") == "https://x.org:8443"
    assert validate("public_base_url", "http://LOCALHOST:8000") == "http://localhost:8000"
    assert validate("public_base_url", "http://[::1]:8000") == "http://[::1]:8000"
    for bad in ("https://x.org?", "https://x.org#", "https://x.org/?", "https://x.org/#"):
        with pytest.raises(InvalidSetting):
            validate("public_base_url", bad)


def test_overlay_empty_decrypted_secret_counts_as_not_set():
    box = SecretBox(Fernet.generate_key().decode())
    base = Settings(_env_file=None, webhook_secret="env-secret")
    eff = overlay(base, [row("webhook_secret", secret=box.encrypt(""))], box)
    assert eff.settings.webhook_secret is None
    assert eff.meta["webhook_secret"].is_set is False
    assert eff.meta["webhook_secret"].unreadable is False
