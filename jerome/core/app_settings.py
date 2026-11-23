"""Editable settings: which keys exist, how values are validated, and the overlay on the env."""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import SecretStr

from jerome.config import Settings
from jerome.core.secretbox import SecretBox
from jerome.errors import NotFound
from jerome.orm import AppSetting

RUNTIME_KEYS: tuple[str, ...] = (
    "models.ai",
    "prices",
    "text_daily_cost_cap",
    "text_rpm",
    "max_upload_mb",
    "session_days",
    "languagetool_url",
    "public_base_url",
)
SECRET_KEYS: tuple[str, ...] = ("anthropic_api_key", "webhook_secret", "openai_compat_api_key")

_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_PROVIDERS = ("anthropic", "openai_compat")
_PRICE_FIELDS = ("input", "output", "cache_read", "cache_write")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class InvalidSetting(ValueError):
    pass


class UnknownSetting(NotFound):
    def __init__(self, key: str) -> None:
        super().__init__(f"unknown setting: {key}")


@dataclass(frozen=True)
class Meta:
    source: Literal["default", "environment", "app"]
    updated_by: str | None = None
    updated_at: datetime | None = None
    unreadable: bool = False
    is_set: bool | None = None  # secrets only


@dataclass(frozen=True)
class Effective:
    settings: Settings
    meta: dict[str, Meta]


def _number(value: Any, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidSetting(f"{label} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise InvalidSetting(f"{label} must be a finite number")
    return value


def _url(value: Any, label: str, schemes: tuple[str, ...] = ("http", "https")) -> str:
    if not isinstance(value, str):
        raise InvalidSetting(f"{label} must be a URL")
    text = value.strip()
    try:
        parts = urlsplit(text)
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError as exc:
        raise InvalidSetting(f"{label} is not a valid URL") from exc
    if parts.scheme not in schemes or not parts.hostname:
        raise InvalidSetting(f"{label} must be an http or https URL with a host")
    return text.rstrip("/")


def _integer(low: int, high: int) -> Callable[[Any], int]:
    def check(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise InvalidSetting(f"must be a whole number from {low} to {high}")
        if isinstance(value, float) and not (math.isfinite(value) and value == int(value)):
            raise InvalidSetting(f"must be a whole number from {low} to {high}")
        number = int(value)
        if not low <= number <= high:
            raise InvalidSetting(f"must be a whole number from {low} to {high}")
        return number

    return check


def _models_ai(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidSetting("models.ai must be an object")
    if "api_key" in value:
        raise InvalidSetting("models.ai cannot hold an API key; set openai_compat_api_key instead")
    extra = set(value) - {"provider", "model", "effort", "base_url"}
    if extra:
        raise InvalidSetting(f"models.ai has unknown field: {sorted(extra)[0]}")
    provider = value.get("provider")
    if provider not in _PROVIDERS:
        raise InvalidSetting("provider must be anthropic or openai_compat")
    model = value.get("model")
    if not isinstance(model, str) or not model.strip():
        raise InvalidSetting("model must not be empty")
    out: dict[str, Any] = {"provider": provider, "model": model.strip()}
    effort = value.get("effort")
    if effort not in (None, ""):
        if effort not in _EFFORTS:
            raise InvalidSetting(f"effort must be empty or one of {', '.join(_EFFORTS)}")
        out["effort"] = effort
    base_url = value.get("base_url")
    if provider == "openai_compat":
        if base_url in (None, ""):
            raise InvalidSetting("base_url is required for openai_compat")
        out["base_url"] = _url(base_url, "base_url")
    elif base_url not in (None, ""):
        raise InvalidSetting("base_url applies only to openai_compat")
    return out


def _prices(value: Any) -> dict[str, dict[str, int | float]]:
    if not isinstance(value, dict):
        raise InvalidSetting("prices must be an object keyed by model name")
    out: dict[str, dict[str, int | float]] = {}
    for name, entry in value.items():
        if not isinstance(name, str) or not name.strip():
            raise InvalidSetting("price model names must not be empty")
        if not isinstance(entry, dict) or set(entry) != set(_PRICE_FIELDS):
            raise InvalidSetting(f"prices for {name} need exactly {', '.join(_PRICE_FIELDS)}")
        row: dict[str, int | float] = {}
        for field in _PRICE_FIELDS:
            number = _number(entry[field], f"{name} {field}")
            if number < 0:
                raise InvalidSetting(f"{name} {field} must be 0 or more")
            row[field] = number
        out[name.strip()] = row
    return out


def _cost_cap(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise InvalidSetting("text_daily_cost_cap must be a number")
    try:
        cap = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise InvalidSetting("text_daily_cost_cap must be a number") from exc
    if not cap.is_finite() or cap < 0:
        raise InvalidSetting("text_daily_cost_cap must be 0 or more")
    return str(cap)


def _public_base_url(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = _url(value, "public_base_url")
    if "?" in text or "#" in text:
        raise InvalidSetting("public_base_url must be only scheme, host and optional port")
    parts = urlsplit(text)
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        raise InvalidSetting("public_base_url must be only scheme, host and optional port")
    if parts.scheme == "http" and parts.hostname not in _LOCAL_HOSTS:
        raise InvalidSetting("public_base_url must use https (http only for localhost)")
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme}://{host}{port}"


_VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "models.ai": _models_ai,
    "prices": _prices,
    "text_daily_cost_cap": _cost_cap,
    "text_rpm": _integer(1, 10000),
    "max_upload_mb": _integer(1, 500),
    "session_days": _integer(1, 365),
    "languagetool_url": lambda v: _url(v, "languagetool_url"),
    "public_base_url": _public_base_url,
}


def validate(key: str, value: Any) -> Any:
    """The normalized JSON value to store for a runtime key; raises InvalidSetting."""
    check = _VALIDATORS.get(key)
    if check is None:
        raise UnknownSetting(key)
    return check(value)


def value_of(settings: Settings, key: str) -> Any:
    """The JSON-able effective value of a runtime key (never an API key)."""
    if key == "models.ai":
        return {k: v for k, v in settings.models.get("ai", {}).items() if k != "api_key"}
    if key == "text_daily_cost_cap":
        return str(settings.text_daily_cost_cap)
    if key in _VALIDATORS:
        return getattr(settings, key)
    raise UnknownSetting(key)


def _decrypt(row: AppSetting, box: SecretBox | None) -> str | None:
    """The plain secret, or None when it cannot be read (no box, wrong key, damaged)."""
    if box is None or row.secret is None:
        return None
    try:
        return box.decrypt(bytes(row.secret))
    except Exception:  # noqa: BLE001 - any failure means unreadable; never echo the cause
        return None


def overlay(base: Settings, rows: Sequence[AppSetting], box: SecretBox | None) -> Effective:
    by_key = {r.key: r for r in rows}
    update: dict[str, Any] = {}
    meta: dict[str, Meta] = {}

    for key in RUNTIME_KEYS:
        field = "models" if key == "models.ai" else key
        env_source: Literal["default", "environment"] = (
            "environment" if field in base.model_fields_set else "default"
        )
        row = by_key.get(key)
        if row is None:
            meta[key] = Meta(source=env_source)
            continue
        try:
            value = validate(key, row.value)
        except InvalidSetting:
            meta[key] = Meta(source=env_source, unreadable=True)
            continue
        meta[key] = Meta(source="app", updated_by=row.updated_by, updated_at=row.updated_at)
        if key == "models.ai":
            update["models"] = {**base.models, "ai": value}
        elif key == "text_daily_cost_cap":
            update[key] = Decimal(value)
        else:
            update[key] = value

    env_secrets: dict[str, str | None] = {
        "anthropic_api_key": (
            base.anthropic_api_key.get_secret_value() if base.anthropic_api_key else None
        ),
        "webhook_secret": base.webhook_secret,
        "openai_compat_api_key": base.models.get("ai", {}).get("api_key"),
    }
    secrets: dict[str, str | None] = {}
    for key in SECRET_KEYS:
        env_value = env_secrets[key] or None
        row = by_key.get(key)
        if row is None:
            secrets[key] = env_value
            meta[key] = Meta(
                source="environment" if env_value else "default", is_set=env_value is not None
            )
        elif row.secret is None:
            secrets[key] = None
            meta[key] = Meta(
                source="app", updated_by=row.updated_by, updated_at=row.updated_at, is_set=False
            )
        else:
            plain = _decrypt(row, box)
            if plain == "":
                secrets[key] = None
                meta[key] = Meta(
                    source="app", updated_by=row.updated_by, updated_at=row.updated_at, is_set=False
                )
            elif plain is None:
                secrets[key] = env_value
                meta[key] = Meta(
                    source="environment" if env_value else "default",
                    unreadable=True,
                    is_set=env_value is not None,
                )
            else:
                secrets[key] = plain
                meta[key] = Meta(
                    source="app", updated_by=row.updated_by, updated_at=row.updated_at, is_set=True
                )

    update["webhook_secret"] = secrets["webhook_secret"]
    anthropic = secrets["anthropic_api_key"]
    update["anthropic_api_key"] = SecretStr(anthropic) if anthropic else None

    models = {
        k: dict(v) if isinstance(v, dict) else v
        for k, v in update.get("models", base.models).items()
    }
    ai = models.get("ai")
    if isinstance(ai, dict):
        ai.pop("api_key", None)
        if ai.get("provider") != "anthropic" and secrets["openai_compat_api_key"]:
            ai["api_key"] = secrets["openai_compat_api_key"]
    update["models"] = models
    return Effective(settings=base.model_copy(update=update), meta=meta)
