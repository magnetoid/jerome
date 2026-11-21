from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from JEROME_* environment variables.

    Provider API keys are not here: the provider SDKs read their standard env vars.
    """

    model_config = SettingsConfigDict(env_prefix="JEROME_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://jerome:jerome@localhost:5433/jerome"
    data_dir: Path = Path("~/.local/share/jerome")
    languagetool_url: str = "http://localhost:8010"
    models: dict[str, Any] = {
        "ai": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"}
    }
    prices: dict[str, Any] = {}
    webhook_secret: str | None = None
    text_daily_cost_cap: Decimal = Decimal(5)
    text_rpm: int = 30
    max_upload_mb: int = 50

    @field_validator("data_dir")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return v.expanduser()


@lru_cache
def get_settings() -> Settings:
    return Settings()
