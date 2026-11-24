from decimal import Decimal
from pathlib import Path

from jerome.config import Settings, get_settings


def test_defaults(monkeypatch):
    for k in ("DATABASE_URL", "DATA_DIR", "MODELS", "PRICES", "WEBHOOK_SECRET"):
        monkeypatch.delenv(f"JEROME_{k}", raising=False)
    s = Settings(_env_file=None)
    assert s.database_url == "postgresql+psycopg://jerome:jerome@localhost:5433/jerome"
    assert s.data_dir == Path.home() / ".local/share/jerome"
    assert s.languagetool_url == "http://localhost:8010"
    assert s.models == {
        "ai": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"},
        "translate": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"},
    }
    assert s.prices == {}
    assert s.webhook_secret is None
    assert s.text_daily_cost_cap == Decimal("5")
    assert s.text_rpm == 30
    assert s.max_upload_mb == 50


def test_env_parsing(monkeypatch):
    monkeypatch.setenv("JEROME_MODELS", '{"proofread": "claude-opus-5-5"}')
    monkeypatch.setenv("JEROME_PRICES", '{"m": {"in": 1.5}}')
    monkeypatch.setenv("JEROME_DATA_DIR", "~/jdata")
    monkeypatch.setenv("JEROME_TEXT_DAILY_COST_CAP", "2.50")
    monkeypatch.setenv("JEROME_TEXT_RPM", "7")
    monkeypatch.setenv("JEROME_WEBHOOK_SECRET", "s3")
    s = Settings(_env_file=None)
    assert s.models == {"proofread": "claude-opus-5-5"}
    assert s.prices == {"m": {"in": 1.5}}
    assert s.data_dir == Path.home() / "jdata"
    assert s.text_daily_cost_cap == Decimal("2.50")
    assert s.text_rpm == 7
    assert s.webhook_secret == "s3"


def test_get_settings_is_cached():
    assert get_settings() is get_settings()


def test_anthropic_api_key_is_read_from_env_and_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert Settings(_env_file=None).anthropic_api_key is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    key = Settings(_env_file=None).anthropic_api_key
    assert key is not None and key.get_secret_value() == "test-key"
    assert "test-key" not in repr(Settings(_env_file=None))
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=from-dotenv\n")
    assert Settings(_env_file=env).anthropic_api_key.get_secret_value() == "from-dotenv"


def test_login_settings(monkeypatch):
    for k in ("TRUSTED_PROXIES", "SESSION_DAYS", "COOKIE_SECURE", "WEB_DIR"):
        monkeypatch.delenv(f"JEROME_{k}", raising=False)
    s = Settings(_env_file=None)
    assert (s.trusted_proxies, s.session_days, s.cookie_secure, s.web_dir) == ([], 30, True, None)
    monkeypatch.setenv("JEROME_TRUSTED_PROXIES", '["172.17.0.1"]')
    monkeypatch.setenv("JEROME_COOKIE_SECURE", "false")
    s = Settings(_env_file=None)
    assert s.trusted_proxies == ["172.17.0.1"] and s.cookie_secure is False
