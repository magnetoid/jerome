import pytest

from jerome.api.deps import get_registry


@pytest.fixture(autouse=True)
def _registry_override(client, registry):
    client.app.dependency_overrides[get_registry] = lambda: registry


def test_estimate(client, auth, book_3_chapters):
    r = client.post(f"/v1/books/{book_3_chapters.id}/estimate", json={}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["chunks"] == 3
    assert isinstance(body["total"]["high"], str)
    assert body["per_pass"]["languagetool"] == {"low": "0", "high": "0"}


def test_create_job_requires_max_cost_422(client, auth, book):
    r = client.post(f"/v1/books/{book.id}/jobs", json={"type": "proofread"}, headers=auth)
    assert r.status_code == 422
    r = client.post(
        f"/v1/books/{book.id}/jobs", json={"type": "proofread", "max_cost": 0}, headers=auth
    )
    assert r.status_code == 422
    r = client.post(
        f"/v1/books/{book.id}/jobs",
        json={"type": "proofread", "max_cost": 1, "passes": []},
        headers=auth,
    )
    assert r.status_code == 422


def test_create_job_409_when_active(client, auth, book):
    body = {"type": "proofread", "max_cost": "1.5"}
    first = client.post(f"/v1/books/{book.id}/jobs", json=body, headers=auth)
    assert first.status_code == 201
    assert first.json()["max_cost"] == "1.5000"
    assert client.post(f"/v1/books/{book.id}/jobs", json=body, headers=auth).status_code == 409


def test_create_job_404_unknown_book(client, auth):
    r = client.post(
        "/v1/books/999999/jobs", json={"type": "proofread", "max_cost": 1}, headers=auth
    )
    assert r.status_code == 404


def test_get_job_progress(client, auth, book_3_chapters):
    created = client.post(
        f"/v1/books/{book_3_chapters.id}/jobs",
        json={"type": "proofread", "max_cost": 5},
        headers=auth,
    ).json()
    r = client.get(f"/v1/jobs/{created['id']}", headers=auth)
    assert r.status_code == 200
    job = r.json()
    assert job["status"] == "queued" and job["type"] == "proofread"
    assert job["book_id"] == book_3_chapters.id
    assert job["cost_actual"] == "0.0000" and job["stats"] == {}
    assert job["passes"] == ["languagetool", "ai", "consistency"]
    assert {"id", "chapter_ids", "estimate", "callback_url", "created_at", "finished_at"} <= set(
        job
    )
    tasks = job["tasks"]
    assert [t["kind"] for t in tasks] == ["chapter"] * 3 + ["consistency"]
    assert tasks[0]["chunks_total"] == 1 and tasks[0]["chunks_done"] == 0
    assert tasks[0]["chapter_id"] is not None and tasks[0]["attempts"] == 0
    assert tasks[0]["error"] is None and tasks[0]["status"] == "queued"
    assert tasks[3]["chapter_id"] is None
    assert client.get("/v1/jobs/999999", headers=auth).status_code == 404


def test_cancel_and_resume(client, auth, book):
    job = client.post(
        f"/v1/books/{book.id}/jobs", json={"type": "proofread", "max_cost": 1}, headers=auth
    ).json()
    c = client.post(f"/v1/jobs/{job['id']}/cancel", headers=auth)
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert client.post(f"/v1/jobs/{job['id']}/cancel", headers=auth).status_code == 409
    r = client.post(f"/v1/jobs/{job['id']}/resume", json={"max_cost": 10}, headers=auth)
    assert r.status_code == 200
    assert r.json()["status"] == "queued" and r.json()["max_cost"] == "10.0000"
    assert client.post(f"/v1/jobs/{job['id']}/resume", headers=auth).status_code == 409


def test_jobs_require_auth(client, book):
    assert client.get("/v1/jobs/1").status_code == 401


@pytest.mark.parametrize(
    "url", ["ftp://hook.test/x", "javascript:alert(1)", "http://", "hook.test/cb", "file:///etc/x"]
)
def test_callback_url_must_be_http_with_a_host(client, auth, book, url):
    body = {"type": "proofread", "max_cost": "1", "callback_url": url}
    assert client.post(f"/v1/books/{book.id}/jobs", json=body, headers=auth).status_code == 422


@pytest.mark.parametrize("url", ["http://localhost:9000/hook", "https://10.0.0.5/cb"])
def test_callback_url_allows_private_hosts(client, auth, book, url):
    body = {"type": "proofread", "max_cost": "1", "callback_url": url}
    assert client.post(f"/v1/books/{book.id}/jobs", json=body, headers=auth).status_code == 201


def test_unknown_model_price_is_422_naming_the_model(client, auth, book_3_chapters, test_settings):
    from jerome.models import FakeProvider, Registry

    settings = test_settings.model_copy(
        update={"models": {"ai": {"provider": "fake", "model": "mystery-model"}}}
    )
    unpriced = Registry(settings, {"fake": FakeProvider([])})
    client.app.dependency_overrides[get_registry] = lambda: unpriced
    r = client.post(f"/v1/books/{book_3_chapters.id}/estimate", json={}, headers=auth)
    assert r.status_code == 422 and "mystery-model" in r.json()["detail"]


def test_create_job_with_empty_chapter_list_422(client, auth, book):
    r = client.post(
        f"/v1/books/{book.id}/jobs",
        json={"type": "proofread", "max_cost": 1, "chapters": []},
        headers=auth,
    )
    assert r.status_code == 422


def test_book_jobs_listed_newest_first(client, auth, book, session):
    from jerome.orm import Job

    url = f"/v1/books/{book.id}/jobs"
    assert client.get(url, headers=auth).json() == []
    ids = []
    for _ in range(3):
        j = Job(book_id=book.id, type="proofread", status="completed", passes=[], max_cost=1)
        session.add(j)
        session.flush()
        ids.append(j.id)
    r = client.get(url, headers=auth)
    assert r.status_code == 200
    assert [j["id"] for j in r.json()] == ids[::-1]
    assert {"status", "tasks", "cost_actual"} <= set(r.json()[0])
    assert client.get("/v1/books/999999/jobs", headers=auth).status_code == 404
    assert client.get(url).status_code == 401
