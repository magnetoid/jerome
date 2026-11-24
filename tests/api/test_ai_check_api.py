"""The AI check over REST and MCP. The check bills in a transaction of its own, so these
tests use a client on real transactions."""

import pytest
from fastapi.testclient import TestClient

from jerome.api.app import create_app
from jerome.api.deps import get_registry
from jerome.api.routes_text import get_text_limits
from jerome.core.keys import create_api_key
from jerome.models import FakeProvider
from jerome.orm import AgentAction
from tests.core.test_ai_check import answer, book, mark, registry, row
from tests.mcp.conftest import mcp_call, tool_error, tool_json


@pytest.fixture
def api(test_settings, session_factory, tmp_storage):
    """(client, auth, provider, session factory, storage), on real transactions."""
    provider = FakeProvider([])
    with session_factory() as s:
        key = create_api_key(s, "tester")
        s.commit()
    app = create_app(test_settings)
    app.dependency_overrides[get_registry] = lambda: registry(test_settings, provider)
    with TestClient(app) as c:
        yield c, {"Authorization": f"Bearer {key}"}, provider, session_factory, tmp_storage


def test_ai_check_route_returns_the_suggestion(api):
    client, auth, provider, sf, storage = api
    _, (b1, _), job_id = book(sf, storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    provider.responses = [answer("better", "the")]
    r = client.post(f"/v1/suggestions/{sid}/ai-check", headers=auth)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["id"], out["status"], out["ai_verdict"], out["ai_replacement"]) == (
        sid, "open", "better", "the",
    )  # fmt: skip
    assert out["ai_reason"] == "Because." and out["ai_model"] == "claude-opus-5-5"
    again = client.post(f"/v1/suggestions/{sid}/ai-check", headers=auth)
    assert again.status_code == 409 and "already checked" in again.json()["detail"]
    assert client.post("/v1/suggestions/999999/ai-check", headers=auth).status_code == 404
    assert client.post(f"/v1/suggestions/{sid}/ai-check").status_code == 401


def test_ai_check_route_409_when_not_eligible(api):
    client, auth, provider, sf, storage = api
    _, (b1, _), job_id = book(sf, storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id, source="model")
    r = client.post(f"/v1/suggestions/{sid}/ai-check", headers=auth)
    assert r.status_code == 409 and "LanguageTool alone" in r.json()["detail"]
    assert provider.calls == []


def test_ai_check_route_502_on_unusable_reply(api):
    client, auth, provider, sf, storage = api
    _, (b1, _), job_id = book(sf, storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    provider.responses = [{"verdicts": []}]
    r = client.post(f"/v1/suggestions/{sid}/ai-check", headers=auth)
    assert r.status_code == 502 and "could not be used" in r.json()["detail"]
    assert row(sf, sid).ai_verdict is None


class _Spent:
    def take(self, key):
        return 12.5


def test_ai_check_routes_rate_limited(api):
    client, auth, provider, sf, storage = api
    book_id, (b1, _), job_id = book(sf, storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    client.app.dependency_overrides[get_text_limits] = lambda: _Spent()
    for path in (
        f"/v1/suggestions/{sid}/ai-check",
        f"/v1/books/{book_id}/chapters/{b1.chapter_id}/ai-check",
    ):
        r = client.post(path, headers=auth)
        assert r.status_code == 429 and r.headers["Retry-After"] == "13", path
    assert provider.calls == []


def test_chapter_ai_check_route(api):
    client, auth, provider, sf, storage = api
    book_id, (b1, b2), job_id = book(sf, storage)
    first = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    second = mark(sf, b2, 8, 13, "barked", job_id=job_id)
    provider.responses = [answer(), answer("correct")]
    path = f"/v1/books/{book_id}/chapters/{b1.chapter_id}/ai-check"
    r = client.post(path, headers=auth)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["checked"], out["remaining"], out["failed"]) == (2, 0, 0)
    assert [(s["id"], s["ai_verdict"]) for s in out["suggestions"]] == [
        (first, "not_an_error"), (second, "correct"),
    ]  # fmt: skip
    again = client.post(path, headers=auth).json()
    assert (again["checked"], again["remaining"], again["suggestions"]) == (0, 0, [])
    assert (
        client.post(f"/v1/books/999999/chapters/{b1.chapter_id}/ai-check", headers=auth).status_code
        == 404
    )


def test_ai_check_suggestion_tool(api):
    client, auth, provider, sf, storage = api
    _, (b1, _), job_id = book(sf, storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    with sf() as s:
        mcp_auth = {"Authorization": f"Bearer {create_api_key(s, 'janus-test')}"}
        s.commit()
    provider.responses = [answer()]
    out = tool_json(mcp_call(client, mcp_auth, "ai_check_suggestion", {"suggestion_id": sid}))
    assert out == client.get(f"/v1/books/{b1.book_id}/suggestions", headers=auth).json()[0]
    assert out["ai_verdict"] == "not_an_error"
    with sf() as s:
        (action,) = s.query(AgentAction).all()
        assert (action.tool, action.after["ai_verdict"]) == ("ai_check_suggestion", "not_an_error")
    error = tool_error(mcp_call(client, mcp_auth, "ai_check_suggestion", {"suggestion_id": sid}))
    assert error.startswith("conflict: ") and "already checked" in error
