from dataclasses import dataclass
from decimal import Decimal

from jerome.config import Settings
from jerome.errors import UnknownModelPrice
from jerome.models.base import Provider
from jerome.models.claude import ClaudeProvider
from jerome.models.deepl import DeepLProvider
from jerome.models.openai_compat import OpenAICompatProvider
from jerome.models.pricing import DEFAULT_PRICES, Price

# OpenAI-compatible services Jerome knows by name; each has its own key setting.
PRESET_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "mistral": "https://api.mistral.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}
HOSTED_PROVIDERS: tuple[str, ...] = tuple(PRESET_BASE_URLS)


@dataclass(frozen=True)
class PassConfig:
    provider: str
    model: str
    effort: str | None = None


class Registry:
    def __init__(self, settings: Settings, providers: dict[str, Provider]):
        self.settings = settings
        self.providers = providers

    def for_pass(self, pass_name: str, *, local_only: bool) -> tuple[Provider, PassConfig] | None:
        raw = self.settings.models.get(pass_name)
        if not raw:
            return None
        provider = self.providers.get(raw["provider"])
        if provider is None or (local_only and not provider.local):
            return None
        return provider, PassConfig(raw["provider"], raw["model"], raw.get("effort"))

    def price(self, model: str) -> Price:
        override = self.settings.prices.get(model)
        if override is not None:
            return Price(
                input=Decimal(str(override["input"])),
                output=Decimal(str(override["output"])),
                cache_read=Decimal(str(override["cache_read"])),
                cache_write=Decimal(str(override["cache_write"])),
            )
        if model in DEFAULT_PRICES:
            return DEFAULT_PRICES[model]
        raise UnknownModelPrice(f"No price known for model {model!r}; set it in the prices setting")


def build_registry(settings: Settings) -> Registry:
    """Providers for the app. Construction needs no network or API key."""
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    providers: dict[str, Provider | DeepLProvider] = {
        "anthropic": ClaudeProvider(api_key=key or None)
    }
    for cfg in settings.models.values():
        name = cfg.get("provider")
        if name == "openai_compat":
            providers["openai_compat"] = OpenAICompatProvider(
                base_url=cfg["base_url"], api_key=cfg.get("api_key")
            )
        elif name == "deepl":
            providers["deepl"] = DeepLProvider(api_key=cfg.get("api_key"))
        elif name in PRESET_BASE_URLS:
            providers[name] = OpenAICompatProvider(
                base_url=PRESET_BASE_URLS[name],
                api_key=cfg.get("api_key"),
                name=name,
                require_key=True,
            )
    return Registry(settings, providers)
