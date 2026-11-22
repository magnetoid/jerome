"""The web interface's whole flow over the API with a cookie session, on a public-domain EPUB:
sign in, change the temporary password, upload with auto-apply, run the worker, review.

Like ``test_end_to_end``, this builds the app on real transactions (the worker opens its own
sessions) and relies on the shared ``session_factory`` fixture to truncate afterwards.
"""

import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from jerome.api.app import create_app
from jerome.core.users import create_user
from jerome.documents.epub_reader import read_epub
from jerome.jobs.worker import WorkerDeps, claim_task, run_task
from jerome.models import FakeProvider, Registry
from jerome.proofread.languagetool import LanguageToolClient

EPUB = Path(__file__).parent / "fixtures" / "public" / "pg932.epub"
WEB = {"X-Jerome-Client": "web"}
TEMPORARY = "temporary-pass-1"
CHOSEN = "a-better-pass-2"


def _block_id(user: str, text: str) -> int | None:
    match = re.search(rf'<block id="(\d+)">\s*{re.escape(text)}\s*</block>', user)
    return int(match.group(1)) if match else None


def _ai_reply(kwargs: dict) -> dict:
    """In whichever chunk holds them: a clear-cut typo fix (auto-applied) and a style
    suggestion the AI leaves open."""
    found = []
    for text, replacement, category, severity in (
        ("EDGAR ALLAN POE", "Edgar Allan Poe", "typo", "high"),
        ("BY", "By", "style", "low"),
    ):
        if (block_id := _block_id(kwargs["user"], text)) is not None:
            found.append(
                {
                    "block_id": block_id,
                    "original": text,
                    "replacement": replacement,
                    "category": category,
                    "severity": severity,
                    "explanation": "test",
                    "occurrence": 1,
                }
            )
    return {"suggestions": found}


@pytest.fixture
def fake_deps(test_settings):
    settings = test_settings.model_copy(
        update={"models": {"ai": {"provider": "fake", "model": "claude-opus-5-5"}}}
    )
    provider = FakeProvider([_ai_reply] * 20, token_count=1000)
    http = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"matches": []}))
    )
    return WorkerDeps(
        Registry(settings, {"fake": provider}),
        LanguageToolClient("http://lt.test", http),
        None,
    )


@pytest.fixture
def web(test_settings, fake_deps, session_factory):
    app = create_app(test_settings)  # real get_session: every request commits
    app.state.registry = fake_deps.registry
    app.state.lt_client = fake_deps.lt
    with TestClient(app, base_url="https://testserver") as c:  # https: the cookie is Secure
        yield c


def test_web_flow_from_first_login_to_clean_export(web, session_factory, fake_deps):
    with session_factory() as s:
        user, _ = create_user(s, "editor@example.com", "Editor", TEMPORARY)
        user.must_change_password = True  # as for a generated temporary password
        s.commit()

    login = web.post("/v1/auth/login", json={"email": "editor@example.com", "password": TEMPORARY})
    assert login.status_code == 200 and login.json()["must_change_password"] is True
    assert web.get("/v1/projects").status_code == 403  # the gate: password first
    changed = web.post("/v1/auth/password", headers=WEB, json={"current": TEMPORARY, "new": CHOSEN})
    assert changed.status_code == 204
    assert web.get("/v1/auth/me").json()["must_change_password"] is False

    created = web.post(
        "/v1/books",
        headers=WEB,
        files={"file": ("usher.epub", EPUB.read_bytes())},
        data={"variant": "en-US", "auto_apply": "true"},
    )
    assert created.status_code == 201, created.text
    book_id = created.json()["id"]
    job = web.post(
        f"/v1/books/{book_id}/jobs", headers=WEB, json={"type": "proofread", "max_cost": "5"}
    )
    assert job.status_code in (200, 201), job.text
    while True:
        with session_factory() as s:
            task_id = claim_task(s)
        if task_id is None:
            break
        run_task(session_factory, task_id, fake_deps)
    assert web.get(f"/v1/jobs/{job.json()['id']}").json()["status"] == "completed"

    (project,) = web.get("/v1/projects").json()
    assert project["id"] == book_id and project["stage"] == "to_review"

    listed = web.get(f"/v1/books/{book_id}/suggestions")
    assert listed.status_code == 200
    rows = listed.json()
    assert int(listed.headers["X-Total-Count"]) == len(rows) >= 2
    by_ai = [r for r in rows if r["decided_by"] == "ai"]
    open_rows = [r for r in rows if r["status"] == "open"]
    assert by_ai and all(r["auto_applied"] and r["status"] == "accepted" for r in by_ai)
    assert open_rows

    reverted = web.post(f"/v1/suggestions/{by_ai[0]['id']}/reject", headers=WEB)
    assert reverted.status_code == 200 and reverted.json()["status"] == "rejected"
    assert reverted.json()["decided_by"] == "editor@example.com"
    accepted = web.post(f"/v1/suggestions/{open_rows[0]['id']}/accept", headers=WEB)
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"

    export = web.get(f"/v1/books/{book_id}/export", params={"format": "epub-clean"})
    assert export.status_code == 200
    assert export.headers["content-type"] == "application/epub+zip"
    texts = [b.text for c in read_epub(export.content).chapters for b in c.blocks]
    assert "By" in texts  # the accepted suggestion is in the export
    assert "EDGAR ALLAN POE" in texts  # the reverted one is not
