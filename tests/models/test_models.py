import json
from decimal import Decimal

import anthropic
import httpx
import httpx2
import pytest

from jerome.config import Settings
from jerome.errors import JeromeError
from jerome.models import (
    DEFAULT_PRICES,
    ClaudeProvider,
    FakeProvider,
    ModelOutputError,
    ModelRefusal,
    OpenAICompatProvider,
    Registry,
    SystemPart,
    Usage,
    cost,
)

SCHEMA = {"type": "object", "properties": {"suggestions": {"type": "array"}}}


def settings_with(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def claude_json_reply(
    data, *, stop_reason="end_turn", model="claude-opus-5-5", usage=None, raw=None
):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [
            {"type": "thinking", "thinking": "hmm", "signature": "sig"},
            {"type": "text", "text": raw if raw is not None else json.dumps(data)},
        ],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 10, "output_tokens": 5},
    }


class Recorder:
    def __init__(self, reply):
        self.reply = reply
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, json=self.reply)

    def body(self, i=0):
        return json.loads(self.requests[i].content)


def make_client(recorder: Recorder) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(recorder)),
    )


def claude(reply) -> tuple[ClaudeProvider, Recorder]:
    rec = Recorder(reply)
    return ClaudeProvider(client=make_client(rec)), rec


def call(provider, **kw):
    return provider.complete(
        model="claude-opus-5-5", system=[SystemPart("rules")], user="u", schema=SCHEMA, **kw
    )


# --- pricing / registry -------------------------------------------------------


def test_cost_opus_one_million_each():
    assert cost(DEFAULT_PRICES["claude-opus-5-5"], Usage(1_000_000, 1_000_000)) == Decimal("24")


def test_cost_includes_cache_tokens():
    p = DEFAULT_PRICES["claude-opus-5-5"]
    assert cost(p, Usage(0, 0, 1_000_000, 1_000_000)) == Decimal("5.20")


def test_registry_local_only_returns_local_or_none():
    reg = Registry(
        settings_with(models={"ai": {"provider": "anthropic", "model": "claude-opus-5-5"}}),
        {"anthropic": FakeProvider([], local=False)},
    )
    assert reg.for_pass("ai", local_only=True) is None
    assert reg.for_pass("ai", local_only=False) is not None


def test_registry_default_config_and_pass_config():
    fake = FakeProvider([])
    reg = Registry(settings_with(), {"anthropic": fake})
    provider, cfg = reg.for_pass("ai", local_only=False)
    assert provider is fake
    assert (cfg.provider, cfg.model, cfg.effort) == ("anthropic", "claude-opus-5-5", "high")


def test_registry_none_for_unconfigured_or_unregistered():
    reg = Registry(settings_with(), {})
    assert reg.for_pass("ai", local_only=False) is None
    assert reg.for_pass("nope", local_only=False) is None


def test_registry_local_provider_allowed_when_local_only():
    reg = Registry(
        settings_with(models={"ai": {"provider": "fake", "model": "m"}}),
        {"fake": FakeProvider([], local=True)},
    )
    provider, cfg = reg.for_pass("ai", local_only=True)
    assert cfg.effort is None


def test_registry_price_override_and_unknown():
    reg = Registry(
        settings_with(
            prices={"m": {"input": 1, "output": "2.5", "cache_read": 0.1, "cache_write": 3}}
        ),
        {},
    )
    p = reg.price("m")
    assert (p.input, p.output, p.cache_read, p.cache_write) == (
        Decimal(1), Decimal("2.5"), Decimal("0.1"), Decimal(3),
    )  # fmt: skip
    assert reg.price("claude-opus-5-5") == DEFAULT_PRICES["claude-opus-5-5"]
    with pytest.raises(JeromeError, match="mystery"):
        reg.price("mystery")


# --- fake ---------------------------------------------------------------------


def test_fake_provider_pops_in_order_raises_and_records():
    fake = FakeProvider([{"a": 1}, ValueError("boom")], token_count=42)
    r = fake.complete(model="m", system=[], user="x", schema={})
    assert r.data == {"a": 1} and r.provider == "fake" and r.model == "m"
    assert r.usage == Usage(1000, 150)
    with pytest.raises(ValueError):
        fake.complete(model="m", system=[], user="y", schema={})
    assert [c["user"] for c in fake.calls] == ["x", "y"]
    with pytest.raises(AssertionError, match="no responses left"):
        fake.complete(model="m", system=[], user="z", schema={})
    assert fake.count_tokens(model="m", system=[], user="x") == 42
    assert FakeProvider([]).count_tokens(model="m", system=[], user="x") is None


# --- claude -------------------------------------------------------------------


def test_claude_request_shape():
    provider, rec = claude(claude_json_reply({"suggestions": []}))
    result = provider.complete(
        model="claude-opus-5-5",
        system=[SystemPart("rules", cache=True), SystemPart("more")],
        user="u",
        schema=SCHEMA,
        effort="high",
    )
    assert rec.requests[0].url.path == "/v1/messages"
    body = rec.body()
    assert body["model"] == "claude-opus-5-5"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in body["system"][1]
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"] == SCHEMA
    assert body["thinking"] == {"type": "adaptive"}
    assert body["fallbacks"] == "default"
    assert body["max_tokens"] == 16000
    assert body["messages"] == [{"role": "user", "content": "u"}]
    assert "server-side-fallback-2026-07-01" in rec.requests[0].headers["anthropic-beta"]
    assert result.data == {"suggestions": []} and result.provider == "anthropic"


def test_claude_omits_effort_when_none():
    provider, rec = claude(claude_json_reply({}))
    call(provider)
    assert "effort" not in rec.body()["output_config"]


def test_claude_refusal_raises():
    reply = claude_json_reply({}, stop_reason="refusal", raw="")
    reply["stop_details"] = {"type": "refusal", "category": "cyber", "explanation": None}
    provider, _ = claude(reply)
    with pytest.raises(ModelRefusal, match="cyber"):
        call(provider)


def test_claude_refusal_without_details_raises():
    provider, _ = claude(claude_json_reply({}, stop_reason="refusal", raw=""))
    with pytest.raises(ModelRefusal):
        call(provider)


def test_claude_max_tokens_raises_output_error():
    provider, _ = claude(claude_json_reply({}, stop_reason="max_tokens", raw='{"a":'))
    with pytest.raises(ModelOutputError) as err:
        call(provider)
    assert err.value.usage is not None and err.value.usage.input_tokens > 0
    assert err.value.provider == "anthropic" and err.value.model


def test_claude_invalid_json_raises_output_error():
    provider, _ = claude(claude_json_reply({}, raw="not json"))
    with pytest.raises(ModelOutputError):
        call(provider)


def test_claude_usage_and_model_passthrough():
    provider, _ = claude(
        claude_json_reply(
            {"x": 1},
            model="claude-fallback-9",
            usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 700,
                "cache_creation_input_tokens": 30,
            },
        )
    )
    r = call(provider)
    assert r.usage == Usage(100, 50, 700, 30)
    assert r.model == "claude-fallback-9"


def test_claude_usage_null_cache_fields_are_zero():
    provider, _ = claude(
        claude_json_reply(
            {},
            usage={
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_read_input_tokens": None,
                "cache_creation_input_tokens": None,
            },
        )
    )
    assert call(provider).usage == Usage(1, 2, 0, 0)


def test_claude_count_tokens():
    provider, rec = claude({"input_tokens": 1234})
    n = provider.count_tokens(model="claude-opus-5-5", system=[SystemPart("rules", True)], user="u")
    assert n == 1234
    assert rec.requests[0].url.path == "/v1/messages/count_tokens"
    body = rec.body()
    assert body["model"] == "claude-opus-5-5"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"] == [{"role": "user", "content": "u"}]


def test_claude_client_is_lazy(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = ClaudeProvider()  # must not raise without a key
    assert provider.name == "anthropic" and provider.local is False


# --- openai-compatible ----------------------------------------------------------


def oa_reply(content, finish="stop", usage=None):
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
    if usage is not None:
        body["usage"] = usage
    return body


def test_openai_compat_parses_json_and_local_flag(respx_mock):
    route = respx_mock.post("http://localhost:11434/v1/chat/completions").respond(
        json=oa_reply('{"suggestions": []}', usage={"prompt_tokens": 7, "completion_tokens": 3})
    )
    p = OpenAICompatProvider("http://localhost:11434/v1", None)
    r = p.complete(
        model="llama",
        system=[SystemPart("a"), SystemPart("b")],
        user="u",
        schema=SCHEMA,
        effort="high",
    )
    assert p.local is True and p.name == "openai_compat"
    assert r.data == {"suggestions": []} and r.usage == Usage(7, 3)
    req = route.calls[0].request
    assert "authorization" not in req.headers
    body = json.loads(req.content)
    assert body["messages"] == [
        {"role": "system", "content": "a\n\nb"},
        {"role": "user", "content": "u"},
    ]
    assert body["max_tokens"] == 16000 and body["model"] == "llama"
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "result", "schema": SCHEMA, "strict": True},
    }
    assert "effort" not in body


def test_openai_compat_sends_bearer_and_missing_usage(respx_mock):
    route = respx_mock.post("https://api.example.com/v1/chat/completions").respond(
        json=oa_reply("{}")
    )
    p = OpenAICompatProvider("https://api.example.com/v1/", "sk-x", name="remote")
    r = p.complete(model="m", system=[], user="u", schema=SCHEMA)
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-x"
    assert r.usage == Usage(0, 0) and r.provider == "remote"
    assert p.count_tokens(model="m", system=[], user="u") is None


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://localhost:11434/v1", True),
        ("http://127.0.0.1:8000/v1", True),
        ("http://[::1]:8000/v1", True),
        ("http://host.docker.internal:1234/v1", True),
        ("http://box.local:1234/v1", True),
        ("https://api.openai.com/v1", False),
        ("https://localhost.evil.com/v1", False),
    ],
)
def test_openai_compat_local_flag(url, local):
    assert OpenAICompatProvider(url, None).local is local


def test_openai_compat_length_and_bad_json_raise(respx_mock):
    respx_mock.post("http://localhost:1/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=oa_reply('{"a":', finish="length")),
            httpx.Response(200, json=oa_reply("nope")),
        ]
    )
    p = OpenAICompatProvider("http://localhost:1/v1", None)
    for _ in range(2):
        with pytest.raises(ModelOutputError):
            p.complete(model="m", system=[], user="u", schema=SCHEMA)


def test_build_registry_hands_the_api_key_to_the_claude_provider(monkeypatch):
    from jerome.models.registry import build_registry

    seen = {}

    class FakeAnthropic:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    provider = build_registry(settings_with(anthropic_api_key="test-key")).providers["anthropic"]
    assert provider.client is provider.client
    assert seen == {"api_key": "test-key"}
    seen.clear()
    unset = build_registry(settings_with(anthropic_api_key="")).providers["anthropic"]
    with pytest.raises(JeromeError, match="Anthropic API key is not set"):
        unset.client  # noqa: B018 - never builds an SDK client without a key
    assert seen == {}


def test_cleared_app_key_beats_the_environment_key(monkeypatch):
    from datetime import UTC, datetime

    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from jerome.core.app_settings import overlay
    from jerome.models.registry import build_registry
    from jerome.orm import AppSetting

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-SENTINEL")
    base = Settings(_env_file=None, secret_key=SecretStr(Fernet.generate_key().decode()))
    assert base.anthropic_api_key is not None  # Settings read the environment key
    cleared = AppSetting(
        key="anthropic_api_key",
        value=None,
        secret=None,
        updated_by="ana@x.org",
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    effective = overlay(base, [cleared], None)
    provider = build_registry(effective.settings).providers["anthropic"]
    with pytest.raises(JeromeError, match="Anthropic API key is not set") as exc:
        provider.client  # noqa: B018
    assert "SENTINEL" not in str(exc.value)


@pytest.mark.parametrize(
    ("preset", "url"),
    [
        ("openai", "https://api.openai.com/v1"),
        ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
        ("mistral", "https://api.mistral.ai/v1"),
        ("openrouter", "https://openrouter.ai/api/v1"),
        ("xai", "https://api.x.ai/v1"),
    ],
)
def test_build_registry_builds_presets(preset, url):
    from jerome.models.registry import build_registry

    s = settings_with(models={"ai": {"provider": preset, "model": "m", "api_key": "k"}})
    reg = build_registry(s)
    provider, cfg = reg.for_pass("ai", local_only=False)
    assert provider.name == preset and provider.base_url == url and provider.api_key == "k"
    assert provider.local is False and cfg.model == "m"
    assert reg.for_pass("ai", local_only=True) is None  # a local-only book never leaves


def test_preset_without_key_raises_before_any_request(respx_mock):
    p = OpenAICompatProvider("https://api.mistral.ai/v1", None, name="mistral", require_key=True)
    with pytest.raises(JeromeError, match="mistral API key is not set"):
        p.complete(model="m", system=[], user="u", schema=SCHEMA)
    assert not respx_mock.calls


def test_openai_preset_sends_max_completion_tokens_and_effort(respx_mock):
    route = respx_mock.post("https://api.openai.com/v1/chat/completions").respond(
        json=oa_reply("{}")
    )
    p = OpenAICompatProvider("https://api.openai.com/v1", "k", name="openai", require_key=True)
    p.complete(model="gpt-5", system=[], user="u", schema=SCHEMA, max_tokens=900, effort="high")
    p.complete(model="gpt-5", system=[], user="u", schema=SCHEMA, effort="max")
    p.complete(model="gpt-4.1", system=[], user="u", schema=SCHEMA, effort="high")
    first, second, third = (json.loads(c.request.content) for c in route.calls)
    assert first["max_completion_tokens"] == 900 and "max_tokens" not in first
    assert first["reasoning_effort"] == "high"
    assert "reasoning_effort" not in second  # OpenAI has no max effort
    assert "reasoning_effort" not in third  # not a reasoning model
