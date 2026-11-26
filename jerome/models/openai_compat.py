import base64
import json
import re
from collections.abc import Sequence
from urllib.parse import urlparse

import httpx

from jerome.errors import JeromeError
from jerome.models.base import CompletionResult, ModelOutputError, SystemPart, Usage

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
_OPENAI_EFFORTS = {"low", "medium", "high"}
# OpenAI's reasoning models; the others answer reasoning_effort with an error
_OPENAI_REASONING = re.compile(r"^(o[0-9]|gpt-5)")


def _user_content(user: str, images: Sequence[tuple[bytes, str]]) -> str | list[dict]:
    if not images:
        return user
    parts: list[dict] = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{base64.b64encode(data).decode()}"},
        }
        for data, media_type in images
    ]
    return [*parts, {"type": "text", "text": user}]


class OpenAICompatProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        name: str = "openai_compat",
        *,
        require_key: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.name = name
        self.require_key = require_key  # a hosted preset: never call it without a key
        host = (urlparse(self.base_url).hostname or "").lower()
        self.local = host in _LOCAL_HOSTS or host.endswith(".local")

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
        if self.require_key and not self.api_key:
            raise JeromeError(f"{self.name} API key is not set")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": "\n\n".join(p.text for p in system)},
                {"role": "user", "content": _user_content(user, images)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": schema, "strict": True},
            },
        }
        if self.name == "openai":  # OpenAI's reasoning models refuse max_tokens
            body["max_completion_tokens"] = max_tokens
            if effort in _OPENAI_EFFORTS and _OPENAI_REASONING.match(model):
                body["reasoning_effort"] = effort
        else:
            body["max_tokens"] = max_tokens
        resp = httpx.post(
            f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=600
        )
        resp.raise_for_status()
        payload = resp.json()
        choice = payload["choices"][0]
        u = payload.get("usage") or {}
        usage = Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        model_name = payload.get("model", model)
        billed = {"usage": usage, "model": model_name, "provider": self.name}
        if choice.get("finish_reason") == "length":
            raise ModelOutputError("Model output was truncated (max tokens reached)", **billed)
        try:
            data = json.loads(choice["message"]["content"])
        except (TypeError, ValueError) as exc:
            raise ModelOutputError("Model output was not valid JSON", **billed) from exc
        return CompletionResult(data, usage, self.name, model_name)

    def count_tokens(self, *, model: str, system: list[SystemPart], user: str) -> int | None:
        return None
