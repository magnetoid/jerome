import httpx
import pytest
from fastapi.testclient import TestClient

from jerome.api.app import create_app
from jerome.api.deps import get_session, get_storage
from jerome.models import FakeProvider, Registry
from jerome.orm import ModelUsage
from jerome.proofread.languagetool import LanguageToolClient
from tests.api.test_text_api import AI_ITEM, LT, LT_MATCH
from tests.mcp.conftest import actions, mcp_call, tool_error, tool_json

BODY = {"text": "teh", "variant": "en-US"}


@pytest.fixture
def lt_route(respx_mock):
    return respx_mock.post(f"{LT}/v2/check").respond(json=LT_MATCH)


def _fake_models(app, test_settings, responses):
    settings = test_settings.model_copy(
        update={"models": {"ai": {"provider": "fake", "model": "claude-opus-5-5"}}}
    )
    ai = FakeProvider(responses)
    app.state.registry = Registry(settings, {"fake": ai})
    app.state.lt_client = LanguageToolClient(LT, httpx.Client())
    return ai


@pytest.fixture
def ai(client, test_settings, lt_route):
    return _fake_models(client.app, test_settings, [{"suggestions": [AI_ITEM]}] * 2)


def test_proofread_text_matches_rest(client, mcp_auth, session, ai):
    rest = client.post("/v1/proofread/text", json=BODY, headers=mcp_auth)
    assert rest.status_code == 200
    result = mcp_call(client, mcp_auth, "proofread_text", BODY)
    assert tool_json(result) == rest.json()
    assert actions(session) == []  # changes nothing, so nothing is logged
    rows = session.query(ModelUsage).all()
    assert [r.api_key_name for r in rows] == ["janus-test", "janus-test"]


@pytest.mark.parametrize(
    "extra", [{"passes": []}, {"passes": ["bogus"]}, {"text": ""}, {"quote_style": "ab"}]
)
def test_proofread_text_rejects_what_rest_rejects(client, mcp_auth, extra):
    assert (
        client.post("/v1/proofread/text", json={**BODY, **extra}, headers=mcp_auth).status_code
        == 422
    )
    result = mcp_call(client, mcp_auth, "proofread_text", {**BODY, **extra})
    assert tool_error(result).startswith("invalid: ")


@pytest.fixture
def rpm1_client(session, tmp_storage, test_settings):
    app = create_app(test_settings.model_copy(update={"text_rpm": 1}))
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_storage] = lambda: tmp_storage
    with TestClient(app) as c:
        yield c


def test_proofread_text_shares_rest_rate_limit(
    rpm1_client, mcp_auth, test_settings, lt_route, session
):
    _fake_models(rpm1_client.app, test_settings, [{"suggestions": [AI_ITEM]}] * 2)
    assert rpm1_client.post("/v1/proofread/text", json=BODY, headers=mcp_auth).status_code == 200
    result = mcp_call(rpm1_client, mcp_auth, "proofread_text", BODY)
    assert tool_error(result) == "rate_limited: Rate limit exceeded"


def test_proofread_text_shares_rest_daily_cap(client, mcp_auth, session):
    from decimal import Decimal

    session.add(
        ModelUsage(
            api_key_name="janus-test", pass_name="ai", provider="fake", model="m", cost=Decimal(5)
        )
    )
    session.flush()
    result = mcp_call(client, mcp_auth, "proofread_text", BODY)
    assert tool_error(result) == "rate_limited: Daily cost cap reached"


def test_proofread_text_model_failure_keeps_the_usage_row(client, mcp_auth, session, ai):
    ai.responses = [{"nope": 1}]  # billed, then rejected: REST answers 502 and keeps the row
    result = mcp_call(client, mcp_auth, "proofread_text", BODY)
    assert tool_error(result).startswith("error: ")
    assert tool_error(result) != "error: internal error"
    (row,) = session.query(ModelUsage).all()
    assert row.api_key_name == "janus-test" and row.cost > 0
