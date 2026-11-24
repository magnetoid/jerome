"""POST /v1/books/{id}/publish starts a publish job; GET /v1/jobs/{id}/file serves its file.

The app runs on real transactions, because the worker opens its own sessions; the shared
``session_factory`` fixture truncates afterwards."""

import pytest
from fastapi.testclient import TestClient

from jerome.api.app import create_app
from jerome.core.books import import_book
from jerome.core.keys import create_api_key
from jerome.core.publication import update_publication
from jerome.jobs.worker import WorkerDeps
from jerome.orm import Book, Job
from jerome.storage import Storage
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.translation import drain, quiet_languagetool, registry_for
from tests.jobs.test_translate_job import imported


@pytest.fixture
def api(test_settings, session_factory):
    with session_factory() as s:
        key = create_api_key(s, "tester")
        s.commit()
    app = create_app(test_settings)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {key}"
        yield c


@pytest.fixture
def storage(test_settings):
    return Storage(test_settings.data_dir)


def _book(sf, storage, *, authors=("Ann Author",)) -> int:
    with sf() as s:
        book = import_book(
            s,
            storage,
            filename="novel.docx",
            data=make_docx(["# One", "The first paragraph."]),
            variant="en-US",
        )
        update_publication(s, book.id, {"authors": list(authors)})  # [] clears the import's
        s.commit()
        return book.id


def test_publish_and_download(api, session_factory, storage, test_settings):
    book_id = _book(session_factory, storage)
    started = api.post(f"/v1/books/{book_id}/publish", json={"format": "epub-book"})
    assert started.status_code == 201, started.text
    job = started.json()
    assert job["type"] == "publish" and job["status"] == "queued"
    assert job["tasks"][0]["kind"] == "publish"
    early = api.get(f"/v1/jobs/{job['id']}/file")
    assert early.status_code == 409 and "the job has not finished" in early.text

    registry = registry_for(test_settings, None)
    deps = WorkerDeps(registry, quiet_languagetool(), None, storage=storage)
    drain(session_factory, deps)
    done = api.get(f"/v1/jobs/{job['id']}")
    assert done.json()["status"] == "completed"
    assert done.json()["stats"]["file"] == f"novel-{job['id']}.epub"
    got = api.get(f"/v1/jobs/{job['id']}/file")
    assert got.status_code == 200
    assert got.headers["content-type"] == "application/epub+zip"
    assert f'filename="novel-{job["id"]}.epub"' in got.headers["content-disposition"]
    assert got.content[:2] == b"PK"


def test_publish_guards(api, session_factory, storage):
    no_author = _book(session_factory, storage, authors=())
    refused = api.post(f"/v1/books/{no_author}/publish", json={"format": "pdf-book"})
    assert refused.status_code == 409
    assert "Set the author under Publication first" in refused.text

    translated = imported(session_factory, storage)
    with session_factory() as s:
        update_publication(s, translated, {"authors": ["Ann Author"]})
        s.commit()
    pending = api.post(f"/v1/books/{translated}/publish", json={"format": "epub-book"})
    assert pending.status_code == 409 and "not translated yet" in pending.text
    assert api.get(f"/v1/books/{translated}/jobs").json() == []

    with session_factory() as s:
        source = s.get(Book, translated).source_book_id
    from_source = api.post(f"/v1/books/{source}/publish", json={"format": "epub-book"})
    assert from_source.status_code == 409 and "is the source of" in from_source.text

    book_id = _book(session_factory, storage)
    first = api.post(f"/v1/books/{book_id}/publish", json={"format": "epub-book"})
    assert first.status_code == 201
    again = api.post(f"/v1/books/{book_id}/publish", json={"format": "pdf-book"})
    assert again.status_code == 409 and "already has an active job" in again.text

    bad = api.post(f"/v1/books/{book_id}/publish", json={"format": "docx-clean"})
    assert bad.status_code == 422


def test_file_of_another_job_type_is_404(api, session_factory, storage):
    book_id = _book(session_factory, storage)
    with session_factory() as s:
        job = Job(
            book_id=book_id,
            type="proofread",
            status="completed",
            passes=["ai"],
            max_cost=1,
            created_by="t",
        )
        s.add(job)
        s.commit()
        job_id = job.id
    assert api.get(f"/v1/jobs/{job_id}/file").status_code == 404
    assert api.get("/v1/jobs/999999/file").status_code == 404
