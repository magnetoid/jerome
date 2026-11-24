"""Translated books over REST: import, jobs, the read-only source book, stages, and the
flagged-block endpoints."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from jerome.api.app import create_app
from jerome.api.deps import get_registry
from jerome.core.keys import create_api_key
from jerome.models import FakeProvider
from tests.fixtures.docx_builders import make_docx
from tests.jobs.test_translate_job import deps_for, drain, echo, registry_for
from tests.translate.fakes import echo_translator

DOCX = make_docx(["First para.", "Second para.", "Third para."])


def upload(client, auth, *, data=DOCX, filename="b.docx", **form):
    fields = {"variant": "en-GB", "work": "translate", "target_variant": "de-DE", **form}
    return client.post("/v1/books", headers=auth, files={"file": (filename, data)}, data=fields)


def translated_book(client, auth, **form) -> dict:
    r = upload(client, auth, **form)
    assert r.status_code == 201, r.text
    return r.json()


def review_blocks(client, auth, book_id) -> list[dict]:
    """The blocks of the book's one chapter, as the review page gets them, with chapter_id."""
    (chapter,) = client.get(f"/v1/books/{book_id}/chapters", headers=auth).json()
    blocks = client.get(f"/v1/chapters/{chapter['id']}/review", headers=auth).json()["blocks"]
    return [{**b, "chapter_id": chapter["id"]} for b in blocks]


def run_worker(client, registry) -> None:
    """Run the worker on the client's database until no task is left."""
    drain(sessionmaker(client.app.state.engine), deps_for(registry))


@pytest.fixture
def reg(test_settings):
    return registry_for(test_settings, FakeProvider([], token_count=1000))


@pytest.fixture
def _registry_override(client, reg):
    client.app.dependency_overrides[get_registry] = lambda: reg


@pytest.fixture
def committing(test_settings, session_factory):
    """A client on real transactions, so the worker (its own sessions) sees the requests'
    writes; the provider is set per test. Yields (client, auth, provider, registry)."""
    provider = FakeProvider([], token_count=1000)
    registry = registry_for(test_settings, provider)
    with session_factory() as s:
        key = create_api_key(s, "tester")
        s.commit()
    app = create_app(test_settings)  # the real get_session: every request commits
    app.dependency_overrides[get_registry] = lambda: registry
    with TestClient(app) as c:
        yield c, {"Authorization": f"Bearer {key}"}, provider, registry


def test_upload_translate_returns_translated_book_and_hides_source(client, auth):
    book = translated_book(client, auth, translated_title="Drei Absätze")
    assert book["work"] == "translate" and book["translated_title"] == "Drei Absätze"
    assert book["title"] == "Drei Absätze" and book["format"] == "docx"
    source_id = book["source_book_id"]
    assert source_id is not None and source_id != book["id"]
    assert [b["id"] for b in client.get("/v1/books", headers=auth).json()] == [book["id"]]
    source = client.get(f"/v1/books/{source_id}", headers=auth).json()  # readable by id
    assert (source["work"], source["source_book_id"]) == ("proofread", None)
    style = client.get(f"/v1/books/{book['id']}/style-sheet", headers=auth).json()
    assert style["variant"] == "de-DE"
    assert client.get(f"/v1/books/{source_id}/style-sheet", headers=auth).json()["variant"] == (
        "en-GB"
    )
    (row,) = client.get("/v1/projects", headers=auth).json()
    assert row["id"] == book["id"]


def test_upload_translate_without_target_variant_is_422(client, auth):
    r = client.post(
        "/v1/books",
        headers=auth,
        files={"file": ("b.docx", DOCX)},
        data={"variant": "en-GB", "work": "translate"},
    )
    assert r.status_code == 422
    assert "target_variant" in r.text
    assert client.get("/v1/books", headers=auth).json() == []


def test_upload_same_language_pair_is_400(client, auth):
    r = upload(client, auth, target_variant="en-US")
    assert r.status_code == 400
    assert "different languages" in r.json()["detail"]


def test_proofread_upload_is_unchanged(client, auth):
    r = client.post(
        "/v1/books", headers=auth, files={"file": ("b.docx", DOCX)}, data={"variant": "en-GB"}
    )
    assert r.status_code == 201
    body = r.json()
    assert (body["work"], body["source_book_id"], body["translated_title"]) == (
        "proofread",
        None,
        None,
    )


@pytest.mark.usefixtures("_registry_override")
def test_translate_job_estimate_and_start_over_rest(client, auth):
    book = translated_book(client, auth)
    bid = book["id"]
    est = client.post(f"/v1/books/{bid}/estimate", json={"type": "translate"}, headers=auth)
    assert est.status_code == 200, est.text
    body = est.json()
    assert set(body["per_pass"]) == {"translate"} and body["chunks"] == 1
    # passes and chapters are ignored for a translation
    ignored = client.post(
        f"/v1/books/{bid}/estimate",
        json={"type": "translate", "passes": ["ai"], "chapters": [999999]},
        headers=auth,
    )
    assert ignored.json() == body
    # proofreading waits for the translation
    proof = client.post(f"/v1/books/{bid}/jobs", json={"type": "proofread", "max_cost": 5},
                        headers=auth)  # fmt: skip
    assert proof.status_code == 409
    r = client.post(
        f"/v1/books/{bid}/jobs", json={"type": "translate", "max_cost": 5}, headers=auth
    )
    assert r.status_code == 201, r.text
    job = r.json()
    assert (job["type"], job["passes"], job["status"]) == ("translate", ["translate"], "queued")
    assert len(job["tasks"]) == 1 and job["estimate"]["per_pass"].keys() == {"translate"}
    bad = client.post(f"/v1/books/{bid}/jobs", json={"type": "rewrite", "max_cost": 5},
                      headers=auth)  # fmt: skip
    assert bad.status_code == 422


@pytest.mark.usefixtures("_registry_override")
def test_source_book_is_read_only_everywhere(client, auth):
    book = translated_book(client, auth)
    tid, sid = book["id"], book["source_book_id"]
    (chapter,) = client.get(f"/v1/books/{sid}/chapters", headers=auth).json()
    calls = {
        "proofread job": client.post(
            f"/v1/books/{sid}/jobs", json={"type": "proofread", "max_cost": 5}, headers=auth
        ),
        "translate job": client.post(
            f"/v1/books/{sid}/jobs", json={"type": "translate", "max_cost": 5}, headers=auth
        ),
        "estimate": client.post(f"/v1/books/{sid}/estimate", json={}, headers=auth),
        "translate estimate": client.post(
            f"/v1/books/{sid}/estimate", json={"type": "translate"}, headers=auth
        ),
        "style sheet": client.put(
            f"/v1/books/{sid}/style-sheet", json={"ignore_words": ["x"]}, headers=auth
        ),
        "auto-apply": client.put(
            f"/v1/books/{sid}/auto-apply",
            json={"enabled": False, "categories": [], "min_severity": "high", "house_rules": False},
            headers=auth,
        ),
        "chapter": client.patch(
            f"/v1/chapters/{chapter['id']}", json={"proofread": False}, headers=auth
        ),
        "export": client.get(f"/v1/books/{sid}/export?format=docx-clean", headers=auth),
        "review copy": client.get(f"/v1/books/{sid}/export?format=docx-review", headers=auth),
        "review import": client.post(
            f"/v1/books/{sid}/review-imports", files={"file": ("r.docx", DOCX)}, headers=auth
        ),
        "delete": client.delete(f"/v1/books/{sid}", headers=auth),
    }
    for name, r in calls.items():
        assert r.status_code == 409, (name, r.status_code, r.text)
        assert f"work on book {tid}" in r.json()["detail"], name
    assert client.get(f"/v1/books/{sid}", headers=auth).status_code == 200  # still there
    assert client.get(f"/v1/books/{sid}/jobs", headers=auth).json() == []


def test_projects_list_shows_translating_then_translated_stage(committing):
    client, auth, provider, registry = committing
    book = translated_book(client, auth)
    bid = book["id"]

    def project() -> dict:
        (row,) = client.get("/v1/projects", headers=auth).json()
        assert row["id"] == bid
        return row

    row = project()
    assert (row["work"], row["source_variant"], row["variant"]) == ("translate", "en-GB", "de-DE")
    assert (row["stage"], row["translation"]) == ("set_up", {"pending": 3})
    r = client.post(
        f"/v1/books/{bid}/jobs", json={"type": "translate", "max_cost": 5}, headers=auth
    )
    assert r.status_code == 201, r.text
    assert project()["stage"] == "translating"
    provider.responses = echo()
    run_worker(client, registry)
    row = project()
    assert (row["stage"], row["translation"]) == ("translated", {"done": 3})
    blocks = review_blocks(client, auth, bid)
    assert [(b["source"], b["current"], b["translation_status"]) for b in blocks] == [
        ("First para.", "DE First para.", "done"),
        ("Second para.", "DE Second para.", "done"),
        ("Third para.", "DE Third para.", "done"),
    ]


def test_retranslate_and_keep_source_endpoints(committing):
    client, auth, provider, registry = committing
    bid = translated_book(client, auth)["id"]
    r = client.post(
        f"/v1/books/{bid}/jobs", json={"type": "translate", "max_cost": 5}, headers=auth
    )
    assert r.status_code == 201, r.text

    def breaking(kwargs):  # "Second" and "Third" come back with broken tags, every time
        out = echo_translator()(kwargs)
        for b in out["blocks"]:
            if "Second" in b["text"] or "Third" in b["text"]:
                b["text"] = "kaputt <i>"
        return out

    provider.responses = [breaking] * 10
    run_worker(client, registry)
    first, second, third = review_blocks(client, auth, bid)
    state = client.get(f"/v1/books/{bid}/translation", headers=auth).json()
    assert state == {
        "counts": {"done": 1, "flagged": 2},
        "flagged": [
            {
                "block_id": second["id"],
                "chapter_id": second["chapter_id"],
                "source": "Second para.",
            },
            {"block_id": third["id"], "chapter_id": third["chapter_id"], "source": "Third para."},
        ],
    }
    export = client.get(f"/v1/books/{bid}/export?format=docx-clean", headers=auth)
    assert export.status_code == 409
    assert f"blocks [{second['id']}, {third['id']}]" in export.json()["detail"]

    provider.responses = [echo_translator()]
    r = client.post(f"/v1/blocks/{second['id']}/translate", headers=auth)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["id"], out["text"], out["translation_status"]) == (
        second["id"],
        "DE Second para.",
        "done",
    )
    r = client.post(f"/v1/blocks/{third['id']}/keep-source", headers=auth)
    assert r.status_code == 200, r.text
    assert (r.json()["text"], r.json()["translation_status"]) == ("Third para.", "kept")
    assert client.post(f"/v1/blocks/{third['id']}/keep-source", headers=auth).status_code == 409
    assert client.post("/v1/blocks/999999/translate", headers=auth).status_code == 404
    state = client.get(f"/v1/books/{bid}/translation", headers=auth).json()
    assert state == {"counts": {"done": 2, "kept": 1}, "flagged": []}
    assert client.get(f"/v1/books/{bid}/export?format=docx-clean", headers=auth).status_code == (
        200
    )
    # a block of a book that is not translated
    plain = client.post(
        "/v1/books", headers=auth, files={"file": ("p.docx", DOCX)}, data={"variant": "en-GB"}
    ).json()
    (plain_block, *_) = review_blocks(client, auth, plain["id"])
    assert plain_block["source"] is None and plain_block["translation_status"] is None
    r = client.post(f"/v1/blocks/{plain_block['id']}/translate", headers=auth)
    assert r.status_code == 409
