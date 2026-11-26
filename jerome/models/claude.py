import base64
import json
from collections.abc import Sequence

import anthropic

from jerome.errors import JeromeError
from jerome.models.base import (
    CompletionResult,
    ModelOutputError,
    ModelRefusal,
    SystemPart,
    Usage,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _system_blocks(system: list[SystemPart]) -> list[dict]:
    return [
        {
            "type": "text",
            "text": p.text,
            **({"cache_control": {"type": "ephemeral"}} if p.cache else {}),
        }
        for p in system
    ]


def _user_content(user: str, images: Sequence[tuple[bytes, str]]) -> str | list[dict]:
    if not images:
        return user
    parts: list[dict] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(data).decode(),
            },
        }
        for data, media_type in images
    ]
    return [*parts, {"type": "text", "text": user}]


class ClaudeProvider:
    name = "anthropic"
    local = False

    def __init__(self, client: anthropic.Anthropic | None = None, api_key: str | None = None):
        self._client = client
        self._api_key = api_key

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not self._api_key:
                # never let the SDK fall back to ANTHROPIC_API_KEY: Settings already read it,
                # so a missing key here is a key the admin cleared
                raise JeromeError("Anthropic API key is not set")
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def complete(
        self,
        *,
        model: str,
        system: list[SystemPart],
        user: str,
        schema: dict,
        max_tokens: int = 16000,
        effort: str | None = None,
        images: Sequence[tuple[bytes, str]] = (),
    ) -> CompletionResult:
        response = self.client.beta.messages.create(
            betas=[FALLBACK_BETA],
            fallbacks="default",
            model=model,
            max_tokens=max_tokens,
            system=_system_blocks(system),
            messages=[{"role": "user", "content": _user_content(user, images)}],
            thinking={"type": "adaptive"},
            output_config={
                "format": {"type": "json_schema", "schema": schema},
                **({"effort": effort} if effort else {}),
            },
        )
        u = response.usage
        usage = Usage(
            u.input_tokens,
            u.output_tokens,
            u.cache_read_input_tokens or 0,
            u.cache_creation_input_tokens or 0,
        )
        billed = {"usage": usage, "model": response.model, "provider": self.name}
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            raise ModelRefusal(
                "Model refused the request" + (f" (category: {category})" if category else ""),
                **billed,
            )
        if response.stop_reason == "max_tokens":
            raise ModelOutputError("Model output was truncated (max_tokens reached)", **billed)
        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ModelOutputError("Model output was not valid JSON", **billed) from exc
        return CompletionResult(data, usage, self.name, response.model)

    def count_tokens(self, *, model: str, system: list[SystemPart], user: str) -> int | None:
        result = self.client.messages.count_tokens(
            model=model,
            system=_system_blocks(system),
            messages=[{"role": "user", "content": user}],
        )
        return result.input_tokens
