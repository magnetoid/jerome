"""A provider without its API key says so plainly, and the API answers 422, not 500."""

import pytest

from jerome.errors import MissingApiKey
from jerome.models.base import SystemPart
from jerome.models.claude import ClaudeProvider
from jerome.models.deepl import DeepLProvider
from jerome.models.openai_compat import OpenAICompatProvider


def test_claude_without_a_key():
    with pytest.raises(MissingApiKey, match="Anthropic API key is not set.*Settings"):
        _ = ClaudeProvider(api_key=None).client


def test_hosted_preset_without_a_key():
    provider = OpenAICompatProvider("https://api.x.ai/v1", None, "xai", require_key=True)
    with pytest.raises(MissingApiKey, match="xai API key is not set.*Settings"):
        provider.complete(model="grok-4", system=[SystemPart("s")], user="u", schema={})


def test_deepl_without_a_key():
    with pytest.raises(MissingApiKey, match="deepl API key is not set.*Settings"):
        DeepLProvider(None).translate_texts(["a"], "EN", "DE")


def test_estimate_without_a_key_is_422(client, auth, book_id, monkeypatch):
    import jerome.api.routes_jobs as routes

    def no_key(*_a, **_k):
        raise MissingApiKey("Anthropic")

    monkeypatch.setattr(routes, "estimate_translation", no_key)
    monkeypatch.setattr(routes, "estimate_job", no_key)
    r = client.post(f"/v1/books/{book_id}/estimate", json={"type": "proofread"}, headers=auth)
    assert r.status_code == 422
    assert "Anthropic API key is not set" in r.json()["detail"]
