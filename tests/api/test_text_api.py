from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from jerome.api.deps import get_session
from jerome.models import FakeProvider, ModelOutputError, ModelRefusal, Registry, Usage
from jerome.orm import ModelUsage, Suggestion
from jerome.proofread.languagetool import LanguageToolClient
from jerome.proofread.text import TextLimits

LT = "http://lt.test"
URL = "/v1/proofread/text"
BODY = {"text": "teh", "variant": "en-US"}
LT_MATCH = {
    "matches": [
        {
            "offset": 0,
            "length": 3,
            "message": "Possible typo.",
            "replacements": [{"value": "the"}],
            "rule": {"issueType": "misspelling"},
        }
    ]
}
AI_ITEM = {
    "block_id": 0,
    "original": "teh",
    "replacement": "the",
    "category": "typo",
    "severity": "high",
    "explanation": "Typo from the model.",
    "occurrence": 1,
}


@pytest.fixture
def ai():
    return FakeProvider([{"suggestions": [AI_ITEM]}])


@pytest.fixture
def lt_route(respx_mock):
    return respx_mock.post(f"{LT}/v2/check").respond(json=LT_MATCH)


@pytest.fixture
def fake_ai_and_lt(client, test_settings, ai, lt_route):
    settings = test_settings.model_copy(
        update={"models": {"ai": {"provider": "fake", "model": "claude-opus-5-5"}}}
    )
    client.app.state.registry = Registry(settings, {"fake": ai})
    client.app.state.lt_client = LanguageToolClient(LT, httpx.Client())
    return ai


@pytest.fixture
def committing(client, session):
    """Behave like the real request transaction: commit on success, roll back on a raise."""

    def scope():
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise

    client.app.dependency_overrides[get_session] = scope


def usage_rows(session):
    return session.query(ModelUsage).order_by(ModelUsage.id).all()


def spend(session, key="tester", cost="5", job_id=None, created_at=None):
    row = ModelUsage(
        api_key_name=key,
        job_id=job_id,
        pass_name="ai",
        provider="fake",
        model="m",
        cost=Decimal(cost),
    )
    if created_at is not None:
        row.created_at = created_at
    session.add(row)
    session.flush()


def test_rejects_over_5000_chars(client, auth):
    big = {"text": "a" * 5001, "variant": "en-US"}
    assert client.post(URL, headers=auth, json=big).status_code == 422


def test_accepts_exactly_5000_chars_counted_in_code_points(client, auth, fake_ai_and_lt):
    ok = client.post(URL, headers=auth, json={"text": "\U0001f600" * 5000, "variant": "en-US"})
    assert ok.status_code == 200


@pytest.mark.parametrize(
    "extra", [{"passes": []}, {"passes": ["bogus"]}, {"text": ""}, {"quote_style": "ab"}]
)
def test_invalid_bodies_are_422(client, auth, extra):
    assert client.post(URL, headers=auth, json={**BODY, **extra}).status_code == 422


def test_requires_auth(client):
    assert client.post(URL, json=BODY).status_code == 401


def test_returns_merged_suggestions(client, auth, fake_ai_and_lt):
    # one LT candidate and one AI candidate on the same span merge into one result
    resp = client.post(URL, headers=auth, json=BODY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["warnings"] == []
    assert len(data["suggestions"]) == 1
    s = data["suggestions"][0]
    assert (s["start"], s["end"], s["original"], s["replacement"]) == (0, 3, "teh", "the")
    assert {"category", "severity", "source", "explanation"} <= s.keys()
    assert "block_id" not in s


def test_offsets_are_code_points(client, auth, fake_ai_and_lt, lt_route):
    lt_route.respond(json={"matches": []})
    fake_ai_and_lt.responses = [{"suggestions": [{**AI_ITEM, "block_id": 0}]}]
    resp = client.post(URL, headers=auth, json={"text": "\U0001f600 teh", "variant": "en-US"})
    s = resp.json()["suggestions"][0]
    assert (s["start"], s["end"]) == (2, 5)  # an emoji is one code point


def test_passes_select_what_runs(client, auth, fake_ai_and_lt, lt_route, session):
    resp = client.post(URL, headers=auth, json={**BODY, "passes": ["languagetool"]})
    assert resp.status_code == 200
    assert fake_ai_and_lt.calls == [] and lt_route.called
    assert usage_rows(session) == []


def test_daily_cap_429(client, auth, session):
    spend(session, cost="5")  # the default cap is 5
    resp = client.post(URL, headers=auth, json=BODY)
    assert resp.status_code == 429
    assert 1 <= int(resp.headers["Retry-After"]) <= 86400


def test_daily_cap_ignores_other_keys_jobs_and_yesterday(
    client, auth, session, fake_ai_and_lt, book_factory
):
    from jerome.orm import Job

    job = Job(book_id=book_factory().id, type="proofread", passes=["ai"], max_cost=Decimal(10))
    session.add(job)
    session.flush()
    yesterday = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        seconds=1
    )
    spend(session, key="someone-else")
    spend(session, job_id=job.id)
    spend(session, created_at=yesterday)
    assert client.post(URL, headers=auth, json=BODY).status_code == 200


def test_daily_cap_sums_several_rows(client, auth, session):
    spend(session, cost="2.5")
    spend(session, cost="2.5")
    assert client.post(URL, headers=auth, json=BODY).status_code == 429


def test_rate_limit_429(client, auth, fake_ai_and_lt, test_settings):
    client.app.state.text_limits = TextLimits(1)
    fake_ai_and_lt.responses = [{"suggestions": [AI_ITEM]}] * 2
    assert client.post(URL, headers=auth, json=BODY).status_code == 200
    second = client.post(URL, headers=auth, json=BODY)
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1


def test_rate_limit_is_checked_before_the_daily_cap(client, auth, session):
    client.app.state.text_limits = TextLimits(1, clock=lambda: 0.0)
    spend(session, cost="5")
    first = client.post(URL, headers=auth, json=BODY)  # takes the only token, then hits the cap
    second = client.post(URL, headers=auth, json=BODY)  # no token left: rate limited first
    assert first.status_code == second.status_code == 429
    assert "cost cap" in first.json()["detail"]
    assert "rate limit" in second.json()["detail"].lower()


def test_token_bucket_refills_over_time():
    now = [100.0]
    limits = TextLimits(2, clock=lambda: now[0])  # 2 per minute: one token per 30 s
    assert limits.take("k") is None and limits.take("k") is None
    wait = limits.take("k")
    assert wait is not None and wait == pytest.approx(30.0)
    now[0] += 29
    assert limits.take("k") == pytest.approx(1.0)
    now[0] += 1
    assert limits.take("k") is None
    assert limits.take("k") == pytest.approx(30.0)
    assert limits.take("other") is None  # buckets are per key


def test_bucket_never_exceeds_capacity():
    now = [0.0]
    limits = TextLimits(2, clock=lambda: now[0])
    now[0] += 10_000
    assert limits.take("k") is None and limits.take("k") is None
    assert limits.take("k") is not None


def test_nothing_stored_except_usage(client, auth, session, fake_ai_and_lt):
    # the client shares this `session` through the get_session override, so these counts
    # see everything the request wrote
    client.post(URL, headers=auth, json=BODY)
    assert session.query(Suggestion).count() == 0 and session.query(ModelUsage).count() == 1
    row = usage_rows(session)[0]
    assert (row.api_key_name, row.job_id, row.pass_name, row.provider) == (
        "tester",
        None,
        "ai",
        "fake",
    )
    assert row.cost > 0 and row.input_tokens == 1000


def test_ai_refused_warns_and_keeps_other_results(
    client, auth, session, fake_ai_and_lt, committing
):
    fake_ai_and_lt.responses = [
        ModelRefusal("no", usage=Usage(500, 10), model="claude-opus-5-5", provider="fake")
    ]
    resp = client.post(URL, headers=auth, json=BODY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["warnings"] == ["ai_refused"]
    assert [s["source"] for s in data["suggestions"]] == ["languagetool"]
    (row,) = usage_rows(session)
    assert row.input_tokens == 500 and row.api_key_name == "tester" and row.cost > 0


def test_ai_refused_without_usage_writes_no_row(client, auth, session, fake_ai_and_lt):
    fake_ai_and_lt.responses = [ModelRefusal("no")]
    assert client.post(URL, headers=auth, json=BODY).json()["warnings"] == ["ai_refused"]
    assert usage_rows(session) == []


def test_output_error_is_502_and_the_usage_row_is_committed(
    client, auth, session, fake_ai_and_lt, committing
):
    fake_ai_and_lt.responses = [{"nope": 1}]  # parsed by run_ai_pass; billed, then rejected
    resp = client.post(URL, headers=auth, json=BODY)
    assert resp.status_code == 502 and resp.json()["detail"]
    (row,) = usage_rows(session)  # a raised error would have rolled this back
    assert row.api_key_name == "tester" and row.job_id is None and row.cost > 0


def test_unbilled_output_error_is_502_without_a_row(client, auth, session, fake_ai_and_lt):
    fake_ai_and_lt.responses = [ModelOutputError("truncated")]
    assert client.post(URL, headers=auth, json=BODY).status_code == 502
    assert usage_rows(session) == []


def test_provider_error_is_502(client, auth, session, fake_ai_and_lt):
    fake_ai_and_lt.responses = [RuntimeError("provider down")]
    resp = client.post(URL, headers=auth, json=BODY)
    assert resp.status_code == 502
    assert "provider down" not in resp.text  # no internals leak
    assert usage_rows(session) == []


def test_languagetool_error_is_502(client, auth, fake_ai_and_lt, lt_route):
    lt_route.respond(500)
    resp = client.post(URL, headers=auth, json=BODY)
    assert resp.status_code == 502
    assert fake_ai_and_lt.calls == []  # nothing was paid for


def test_local_only_without_local_provider_warns(client, auth, session, fake_ai_and_lt):
    resp = client.post(URL, headers=auth, json={**BODY, "local_only": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["warnings"] == ["ai_skipped_local_only"]
    assert [s["source"] for s in data["suggestions"]] == ["languagetool"]
    assert fake_ai_and_lt.calls == [] and usage_rows(session) == []


def test_consistency_pass_and_style_options(client, auth, fake_ai_and_lt):
    body = {
        "text": 'He said "hi".',
        "variant": "en-US",
        "passes": ["consistency"],
        "quote_style": "«»‹›",
        "ignore_words": ["x"],
    }
    resp = client.post(URL, headers=auth, json=body)
    assert resp.status_code == 200
    assert any(s["source"] == "consistency" for s in resp.json()["suggestions"])
