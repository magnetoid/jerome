"""The whole slice through the REST API: import, estimate, proofread, review round trip, export.

The shared `client` fixture runs on a savepoint session that never commits, so the worker
(which opens its own sessions) would never see its data. This module builds the app on real
transactions instead and cleans up by truncating.
"""

import re

import httpx
import pytest
from fastapi.testclient import TestClient

from jerome.api.app import create_app
from jerome.core.keys import create_api_key
from jerome.jobs.worker import WorkerDeps, claim_task, run_task
from jerome.models import FakeProvider, Registry
from jerome.proofread.languagetool import LanguageToolClient
from tests.fixtures.docx_builders import all_blocks, make_docx
from tests.fixtures.word_sim import simulate_word_review


def _ai_reply(kwargs: dict) -> dict:
    """Suggest teh -> the in the block whose text contains it (ids come from the prompt)."""
    match = re.search(r'<block id="(\d+)">\s*teh cat', kwargs["user"])
    block_id = int(match.group(1))
    return {
        "suggestions": [
            {
                "block_id": block_id,
                "original": "teh",
                "replacement": "the",
                "category": "typo",
                "severity": "high",
                "explanation": "Misspelling of 'the'.",
                "occurrence": 1,
            }
        ]
    }


@pytest.fixture
def fake_deps(test_settings):
    settings = test_settings.model_copy(
        update={"models": {"ai": {"provider": "fake", "model": "claude-opus-5-5"}}}
    )
    provider = FakeProvider([_ai_reply, _ai_reply], token_count=1000)
    http = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"matches": []}))
    )
    return WorkerDeps(
        Registry(settings, {"fake": provider}),
        LanguageToolClient("http://lt.test", http),
        None,
    )


@pytest.fixture
def e2e_client(test_settings, fake_deps, session_factory):
    app = create_app(test_settings)  # real get_session: every request commits
    app.state.registry = fake_deps.registry
    app.state.lt_client = fake_deps.lt
    with TestClient(app) as c:
        yield c


@pytest.fixture
def e2e_auth(session_factory):
    with session_factory() as s:
        key = create_api_key(s, "e2e")
        s.commit()
    return {"Authorization": f"Bearer {key}"}


def claim_in(session_factory):
    with session_factory() as s:
        return claim_task(s)


def test_end_to_end_with_fakes(e2e_client, e2e_auth, session_factory, fake_deps):
    client, auth = e2e_client, e2e_auth
    created = client.post(
        "/v1/books",
        headers=auth,
        files={"file": ("m.docx", make_docx(["# One", "teh cat sat."]))},
        data={"variant": "en-US"},
    )
    assert created.status_code == 201, created.text
    book = created.json()
    assert client.post(f"/v1/books/{book['id']}/estimate", headers=auth, json={}).status_code == 200
    job = client.post(
        f"/v1/books/{book['id']}/jobs", headers=auth, json={"type": "proofread", "max_cost": "1"}
    ).json()
    while (tid := claim_in(session_factory)) is not None:
        run_task(session_factory, tid, fake_deps)
    assert client.get(f"/v1/jobs/{job['id']}", headers=auth).json()["status"] == "completed"
    copy = client.get(
        f"/v1/books/{book['id']}/export", headers=auth, params={"format": "docx-review"}
    ).content
    reviewed = simulate_word_review(copy, accept=["the"])
    imported = client.post(
        f"/v1/books/{book['id']}/review-imports",
        headers=auth,
        files={"file": ("r.docx", reviewed)},
    )
    assert imported.json()["counts"]["accepted"] == 1
    clean = client.get(
        f"/v1/books/{book['id']}/export", headers=auth, params={"format": "docx-clean"}
    ).content
    assert "the cat sat." in [b.text for b in all_blocks(clean)]
