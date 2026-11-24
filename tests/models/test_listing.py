"""Each provider's model list: the request Jerome makes and the models it keeps."""

import httpx
import pytest
import respx

from jerome.models.listing import ProviderListError, list_models

KEY = "k-secret"


def _ids(models):
    return [m["id"] for m in models]


def test_anthropic_follows_pages(respx_mock):
    route = respx_mock.get("https://api.anthropic.com/v1/models")
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "data": [{"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5"}],
                "has_more": True,
                "last_id": "claude-opus-5-5",
            },
        ),
        httpx.Response(
            200,
            json={
                "data": [{"id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5"}],
                "has_more": False,
                "last_id": "claude-haiku-5-5",
            },
        ),
    ]
    models = list_models("anthropic", api_key=KEY)
    assert models == [
        {"id": "claude-haiku-5-5", "name": "Claude Haiku 5.5"},
        {"id": "claude-opus-5-5", "name": "Claude Opus 5.5"},
    ]
    first, second = (c.request for c in route.calls)
    assert first.headers["x-api-key"] == KEY
    assert first.headers["anthropic-version"] == "2023-06-01"
    assert "after_id" not in first.url.params
    assert second.url.params["after_id"] == "claude-opus-5-5"


def test_openai_drops_non_text_models(respx_mock):
    ids = [
        "gpt-5",
        "text-embedding-3-large",
        "tts-1",
        "whisper-1",
        "dall-e-3",
        "omni-moderation-latest",
        "gpt-4o-realtime-preview",
        "gpt-image-1",
        "sora-2",
    ]
    route = respx_mock.get("https://api.openai.com/v1/models").respond(
        200, json={"object": "list", "data": [{"id": i} for i in ids]}
    )
    assert list_models("openai", api_key=KEY) == [{"id": "gpt-5", "name": "gpt-5"}]
    assert route.calls.last.request.headers["authorization"] == f"Bearer {KEY}"


def test_gemini_strips_prefix(respx_mock):
    respx_mock.get("https://generativelanguage.googleapis.com/v1beta/openai/models").respond(
        200, json={"data": [{"id": "models/gemini-2.5-pro"}, {"id": "models/text-embedding-004"}]}
    )
    assert _ids(list_models("gemini", api_key=KEY)) == ["gemini-2.5-pro"]


def test_mistral_uses_capabilities(respx_mock):
    respx_mock.get("https://api.mistral.ai/v1/models").respond(
        200,
        json={
            "data": [
                {"id": "mistral-large-latest", "capabilities": {"completion_chat": True}},
                {"id": "mistral-ocr-latest", "capabilities": {"completion_chat": False}},
            ]
        },
    )
    assert _ids(list_models("mistral", api_key=KEY)) == ["mistral-large-latest"]


def test_openrouter_needs_text_output(respx_mock):
    respx_mock.get("https://openrouter.ai/api/v1/models").respond(
        200,
        json={
            "data": [
                {
                    "id": "qwen/qwen3-235b-a22b",
                    "name": "Qwen3 235B",
                    "architecture": {"output_modalities": ["text"]},
                },
                {
                    "id": "some/picture-maker",
                    "name": "Pictures",
                    "architecture": {"output_modalities": ["image"]},
                },
            ]
        },
    )
    assert list_models("openrouter", api_key=KEY) == [
        {"id": "qwen/qwen3-235b-a22b", "name": "Qwen3 235B"}
    ]


def test_xai(respx_mock):
    respx_mock.get("https://api.x.ai/v1/models").respond(200, json={"data": [{"id": "grok-4"}]})
    assert _ids(list_models("xai", api_key=KEY)) == ["grok-4"]


def test_own_server_without_key(respx_mock):
    route = respx_mock.get("http://localhost:11434/v1/models").respond(
        200, json={"data": [{"id": "qwen3"}, {"id": "llama3.3"}, {"id": "qwen3"}]}
    )
    models = list_models("openai_compat", api_key=None, base_url="http://localhost:11434/v1")
    assert _ids(models) == ["llama3.3", "qwen3"]
    assert "authorization" not in route.calls.last.request.headers


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="<html>login</html>"),
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(302, headers={"location": "http://localhost:11434/login"}),
        httpx.Response(200, content=b"x" * (6 * 1024 * 1024)),
        httpx.TimeoutException("slow"),
    ],
)
def test_failures_are_provider_list_errors(respx_mock, reply):
    route = respx_mock.get("https://api.openai.com/v1/models")
    if isinstance(reply, Exception):
        route.side_effect = reply
    else:
        route.mock(return_value=reply)
    with pytest.raises(ProviderListError) as err:
        list_models("openai", api_key=KEY)
    assert KEY not in str(err.value) and str(err.value).startswith("OpenAI")


def test_refused_key(respx_mock):
    respx_mock.get("https://api.openai.com/v1/models").respond(401, json={"error": KEY})
    with pytest.raises(ProviderListError, match="^OpenAI refused the key$"):
        list_models("openai", api_key=KEY)


def test_unlisted_provider():
    with pytest.raises(ValueError):
        list_models("deepl", api_key=KEY)
    with respx.mock(assert_all_called=False):
        with pytest.raises(ValueError):
            list_models("openai_compat", api_key=None, base_url=None)
