from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from JEROME_* environment variables.

    The Anthropic key is read from the standard ``ANTHROPIC_API_KEY`` (environment or
    ``.env``) so local runs find it too; it is never logged or printed.
    """

    model_config = SettingsConfigDict(
        env_prefix="JEROME_", env_file=".env", extra="ignore", populate_by_name=True
    )

    database_url: str = "postgresql+psycopg://jerome:jerome@localhost:5433/jerome"
    data_dir: Path = Path("~/.local/share/jerome")
    languagetool_url: str = "http://localhost:8010"
    # LanguageTool Premium: both set sends them with every check (point the URL at
    # https://api.languagetoolplus.com). The key is a secret set on the Settings page.
    languagetool_username: str | None = None
    languagetool_api_key: SecretStr | None = None
    models: dict[str, Any] = {
        "ai": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"},
        "translate": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"},
    }
    prices: dict[str, Any] = {}
    webhook_secret: str | None = None
    text_daily_cost_cap: Decimal = Decimal(5)
    text_rpm: int = 30
    max_upload_mb: int = 50
    trusted_proxies: list[str] = []  # direct peers whose X-Forwarded-For is believed (JSON list)
    session_days: int = 30
    cookie_secure: bool = True
    web_dir: Path | None = None
    # Where clients reach Jerome (e.g. https://jerome.example.com), for the absolute links
    # MCP file tools return. Unset: the MCP request's own base URL.
    public_base_url: str | None = None
    # Fernet key that encrypts secrets saved from the Settings page; environment only.
    secret_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    @field_validator("data_dir")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return v.expanduser()


@lru_cache
def get_settings() -> Settings:
    return Settings()
