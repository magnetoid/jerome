"""Editable settings: which keys exist, how values are validated, and the overlay on the env."""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
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
    "models.translate",
    "models.ocr",
    "prices",
    "text_daily_cost_cap",
    "text_rpm",
    "max_upload_mb",
    "session_days",
    "languagetool_url",
    "languagetool_username",
    "public_base_url",
)
SECRET_KEYS: tuple[str, ...] = (
    "anthropic_api_key",
    "webhook_secret",
    "openai_compat_api_key",
    "openai_api_key",
    "gemini_api_key",
    "mistral_api_key",
    "openrouter_api_key",
    "xai_api_key",
    "deepl_api_key",
    "languagetool_api_key",
)

_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_PRESETS = ("openai", "gemini", "mistral", "openrouter", "xai")  # jerome.models.registry
_PROVIDERS = ("anthropic", *_PRESETS, "openai_compat", "deepl")
_PRICE_FIELDS = ("input", "output", "cache_read", "cache_write")
_MODEL_KEYS = ("models.ai", "models.translate", "models.ocr")
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
    # each secret's effective value (app, else environment); never shown or logged
    secrets: dict[str, str | None] = dc_field(default_factory=dict, repr=False)


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
    if parts.username or parts.password:
        raise InvalidSetting(f"{label} must not contain a username or password")
    return text.rstrip("/")


check_url = _url  # for routes that take a URL outside a setting


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


def _models_pass(name: str) -> Callable[[Any], dict[str, Any]]:
    return lambda value: _model_config(f"models.{name}", value)


def _model_config(label: str, value: Any) -> dict[str, Any]:
    out = _model_fields(label, value)
    if out["provider"] == "deepl" and label != "models.translate":
        raise InvalidSetting("deepl can only translate")
    return out


def _model_fields(label: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidSetting(f"{label} must be an object")
    if "api_key" in value:
        raise InvalidSetting(f"{label} cannot hold an API key; set it on the Settings page")
    extra = set(value) - {"provider", "model", "effort", "base_url"}
    if extra:
        raise InvalidSetting(f"{label} has unknown field: {sorted(extra)[0]}")
    provider = value.get("provider")
    if provider not in _PROVIDERS:
        raise InvalidSetting(f"provider must be one of {', '.join(_PROVIDERS)}")
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


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidSetting(f"{label} must be text")
    return value.strip() or None


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
    "models.ai": _models_pass("ai"),
    "models.translate": _models_pass("translate"),
    "models.ocr": _models_pass("ocr"),
    "prices": _prices,
    "text_daily_cost_cap": _cost_cap,
    "text_rpm": _integer(1, 10000),
    "max_upload_mb": _integer(1, 500),
    "session_days": _integer(1, 365),
    "languagetool_url": lambda v: _url(v, "languagetool_url"),
    "languagetool_username": lambda v: _optional_text(v, "languagetool_username"),
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
    if key in _MODEL_KEYS:
        pass_name = key.removeprefix("models.")
        return {k: v for k, v in settings.models.get(pass_name, {}).items() if k != "api_key"}
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
        field = "models" if key in _MODEL_KEYS else key
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
        if key in _MODEL_KEYS:
            update["models"] = {
                **update.get("models", base.models),
                key.removeprefix("models."): value,
            }
        elif key == "text_daily_cost_cap":
            update[key] = Decimal(value)
        else:
            update[key] = value

    env_secrets: dict[str, str | None] = {
        "anthropic_api_key": (
            base.anthropic_api_key.get_secret_value() if base.anthropic_api_key else None
        ),
        "webhook_secret": base.webhook_secret,
        "languagetool_api_key": (
            base.languagetool_api_key.get_secret_value() if base.languagetool_api_key else None
        ),
    }
    # A provider key in the environment comes inside JEROME_MODELS, beside its provider
    for pass_name in ("ai", "translate", "ocr"):
        config = base.models.get(pass_name)
        if isinstance(config, dict) and config.get("api_key"):
            key = f"{config.get('provider')}_api_key"
            if key in SECRET_KEYS and key != "anthropic_api_key":
                env_secrets.setdefault(key, str(config["api_key"]))
    for key in SECRET_KEYS:
        env_secrets.setdefault(key, None)
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
    lt_key = secrets["languagetool_api_key"]
    update["languagetool_api_key"] = SecretStr(lt_key) if lt_key else None
    anthropic = secrets["anthropic_api_key"]
    update["anthropic_api_key"] = SecretStr(anthropic) if anthropic else None

    models = {
        k: dict(v) if isinstance(v, dict) else v
        for k, v in update.get("models", base.models).items()
    }
    for pass_name in ("ai", "translate", "ocr"):
        config = models.get(pass_name)
        if isinstance(config, dict):
            config.pop("api_key", None)
            provider = config.get("provider")
            secret = secrets.get(f"{provider}_api_key") if provider != "anthropic" else None
            if secret:
                config["api_key"] = secret
    update["models"] = models
    return Effective(settings=base.model_copy(update=update), meta=meta, secrets=secrets)
