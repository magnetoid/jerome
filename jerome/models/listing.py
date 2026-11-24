"""A provider's model list, for the Settings page: what the stored key can use.

Only models that write text are kept. A key never appears in an error message."""

from typing import Any

import httpx

from jerome.errors import JeromeError
from jerome.models.registry import PRESET_BASE_URLS

LISTABLE: tuple[str, ...] = ("anthropic", *PRESET_BASE_URLS, "openai_compat")
LABELS: dict[str, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "mistral": "Mistral",
    "openrouter": "OpenRouter",
    "xai": "xAI",
    "openai_compat": "Own server",
}
ANTHROPIC_URL = "https://api.anthropic.com/v1/models"
MAX_BYTES = 5 * 1024 * 1024
MAX_PAGES = 20  # Anthropic's list comes in pages
# ids naming models that do not write text: embeddings, speech, pictures, video, moderation
_NOT_TEXT = (
    "embed",
    "tts",
    "whisper",
    "dall-e",
    "moderation",
    "image",
    "realtime",
    "transcribe",
    "audio",
    "sora",
    "imagen",
    "veo",
    "aqa",
)


class ProviderListError(JeromeError):
    """The provider did not give a usable list (the message names the provider only)."""


def list_models(
    provider: str,
    *,
    api_key: str | None,
    base_url: str | None = None,
    timeout: float = 10.0,
) -> list[dict[str, str]]:
    """The provider's text models as ``{"id", "name"}``, sorted by id. Raises ValueError for
    a provider without a list (or an own server without a URL), ProviderListError when the
    provider fails."""
    if provider not in LISTABLE:
        raise ValueError(f"no model list for provider {provider!r}")
    if provider == "openai_compat" and not base_url:
        raise ValueError("an own server needs its base URL")
    label = LABELS[provider]
    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        if provider == "anthropic":
            raw = _anthropic(client, label, api_key)
        else:
            url = (base_url if provider == "openai_compat" else PRESET_BASE_URLS[provider]) or ""
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            raw = _data(_get(client, label, f"{url.rstrip('/')}/models", headers, {}), label)
    models: dict[str, str] = {}
    for item in raw:
        model_id = _model_id(item, provider)
        if model_id and _writes_text(item, model_id):
            name = item.get("display_name") or item.get("name") or model_id
            models.setdefault(model_id, str(name))
    return [{"id": i, "name": models[i]} for i in sorted(models)]


def _anthropic(client: httpx.Client, label: str, api_key: str | None) -> list[Any]:
    headers = {"x-api-key": api_key or "", "anthropic-version": "2023-06-01"}
    out: list[Any] = []
    params: dict[str, str] = {"limit": "1000"}
    for _ in range(MAX_PAGES):
        page = _get(client, label, ANTHROPIC_URL, headers, params)
        out += _data(page, label)
        if not page.get("has_more") or not page.get("last_id"):
            break
        params = {"limit": "1000", "after_id": str(page["last_id"])}
    return out


def _get(
    client: httpx.Client, label: str, url: str, headers: dict[str, str], params: dict[str, str]
) -> dict[str, Any]:
    try:
        with client.stream("GET", url, headers=headers, params=params) as resp:
            if resp.status_code in (401, 403):
                raise ProviderListError(f"{label} refused the key")
            if resp.status_code != 200:
                raise ProviderListError(f"{label} answered HTTP {resp.status_code}")
            body = bytearray()
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise ProviderListError(f"{label} sent a list larger than 5 MB")
    except httpx.TimeoutException as exc:
        raise ProviderListError(f"{label} did not answer in time") from exc
    except httpx.HTTPError as exc:
        raise ProviderListError(f"{label} could not be reached ({type(exc).__name__})") from exc
    try:
        payload = httpx.Response(200, content=bytes(body)).json()
    except ValueError as exc:
        raise ProviderListError(f"{label} did not send a model list") from exc
    if not isinstance(payload, dict):
        raise ProviderListError(f"{label} did not send a model list")
    return payload


def _data(payload: dict[str, Any], label: str) -> list[Any]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise ProviderListError(f"{label} did not send a model list")
    return [item for item in data if isinstance(item, dict)]


def _model_id(item: dict[str, Any], provider: str) -> str | None:
    model_id = item.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    model_id = model_id.strip()
    if provider == "gemini":
        model_id = model_id.removeprefix("models/")
    return model_id


def _writes_text(item: dict[str, Any], model_id: str) -> bool:
    if any(word in model_id.lower() for word in _NOT_TEXT):
        return False
    capabilities = item.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get("completion_chat") is False:
        return False
    architecture = item.get("architecture")
    if isinstance(architecture, dict):
        outputs = architecture.get("output_modalities")
        if isinstance(outputs, list) and "text" not in outputs:
            return False
    return True
