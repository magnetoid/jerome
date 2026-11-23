import pytest


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
