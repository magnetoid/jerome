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
        "ai": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"}
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
