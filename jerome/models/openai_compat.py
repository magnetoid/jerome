import json
from urllib.parse import urlparse

import httpx

from jerome.models.base import CompletionResult, ModelOutputError, SystemPart, Usage

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


class OpenAICompatProvider:
    def __init__(self, base_url: str, api_key: str | None, name: str = "openai_compat"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.name = name
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
    ) -> CompletionResult:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": "\n\n".join(p.text for p in system)},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": schema, "strict": True},
            },
        }
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
