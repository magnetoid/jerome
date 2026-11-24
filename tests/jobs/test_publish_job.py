"""The publish job: the worker makes the finished file, keeps it with the job's id in its
name, and fails at once when the file cannot be made."""

import io
import json
import zipfile

import pytest
from lxml import etree
from sqlalchemy import select

from jerome.core.books import import_book
from jerome.core.publication import update_publication
from jerome.errors import Conflict
from jerome.jobs.service import create_publish_job
from jerome.jobs.worker import WorkerDeps
from jerome.orm import Job, JobTask
from jerome.publish import build
from jerome.storage import Storage
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.translation import deps_for, drain, quiet_languagetool, registry_for
from tests.jobs.test_translate_job import imported
from tests.jobs.test_translate_job import start as start_translation
from tests.translate.fakes import echo_translator

HOOK = "https://hooks.example.com/jerome"
NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}


@pytest.fixture
def storage(test_settings):
    return Storage(test_settings.data_dir)


@pytest.fixture
def deps(test_settings, storage):
    registry = registry_for(test_settings, None)
    return WorkerDeps(registry, quiet_languagetool(), None, storage=storage)


def _book(sf, storage, *, authors=("Ann Author",)) -> int:
    with sf() as s:
        book = import_book(
            s,
            storage,
            filename="novel.docx",
            data=make_docx(["# One", "The first paragraph.", "# Two", "The second one."]),
            variant="en-US",
        )
        update_publication(s, book.id, {"authors": list(authors)})  # [] clears the import's
        s.commit()
        return book.id


def _publish(sf, book_id, fmt="epub-book", callback_url=None) -> int:
    with sf() as s:
        job = create_publish_job(s, book_id, fmt=fmt, callback_url=callback_url, actor="tester")
        s.commit()
        return job.id


def _job(sf, job_id) -> Job:
    with sf() as s:
        job = s.get(Job, job_id)
        job.tasks  # noqa: B018 - loaded before the session closes
        s.expunge_all()
        return job


def test_publish_job_makes_epub(session_factory, storage, deps, respx_mock):
    hook = respx_mock.post(HOOK).respond(200)
    book_id = _book(session_factory, storage)
    job_id = _publish(session_factory, book_id, callback_url=HOOK)
    queued = _job(session_factory, job_id)
    assert queued.type == "publish" and queued.passes == ["epub-book"]
    assert [t.kind for t in queued.tasks] == ["publish"]
    drain(session_factory, deps)
    job = _job(session_factory, job_id)
    assert job.status == "completed", job.tasks[0].error
    assert job.stats["file"] == f"novel-{job_id}.epub"
    assert "The book has no cover" in job.stats["warnings"]
    data = storage.read_export(book_id, job.stats["file"])
    assert zipfile.ZipFile(io.BytesIO(data)).testzip() is None
    assert [json.loads(c.request.content)["event"] for c in hook.calls] == ["job.completed"]


def test_publish_job_makes_pdf(session_factory, storage, deps, weasy):
    book_id = _book(session_factory, storage)
    job_id = _publish(session_factory, book_id, fmt="pdf-book")
    drain(session_factory, deps)
    job = _job(session_factory, job_id)
    assert job.status == "completed", job.tasks[0].error
    assert storage.read_export(book_id, job.stats["file"])[:5] == b"%PDF-"


def test_text_difference_fails_job_at_once(session_factory, storage, deps, monkeypatch):
    def differ(*_a, **_kw):
        raise build.PublishTextError("The finished file's text differs from the reviewed text")

    monkeypatch.setattr(build, "check_text", differ)
    book_id = _book(session_factory, storage)
    job_id = _publish(session_factory, book_id)
    drain(session_factory, deps)
    job = _job(session_factory, job_id)
    assert job.status == "failed" and job.finished_at is not None
    task = job.tasks[0]
    assert task.status == "failed" and task.attempts == 1
    assert "differs from the reviewed text" in task.error


def test_other_errors_are_retried(session_factory, storage, deps, monkeypatch):
    def broken(*_a, **_kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(build, "write_epub_book", broken)
    book_id = _book(session_factory, storage)
    job_id = _publish(session_factory, book_id)
    drain(session_factory, deps)
    job = _job(session_factory, job_id)
    assert job.tasks[0].attempts == 1 and job.tasks[0].status == "queued"
    assert job.status == "running"


def test_publish_guards(session_factory, storage, test_settings):
    no_author = _book(session_factory, storage, authors=())
    with session_factory() as s, pytest.raises(Conflict, match="Set the author"):
        create_publish_job(s, no_author, fmt="epub-book", callback_url=None, actor="t")

    translated = imported(session_factory, storage)  # every paragraph still pending
    with session_factory() as s:
        update_publication(s, translated, {"authors": ["Ann Author"]})
        s.commit()
    with session_factory() as s, pytest.raises(Conflict, match="not translated yet"):
        create_publish_job(s, translated, fmt="epub-book", callback_url=None, actor="t")
    with session_factory() as s:
        assert s.scalars(select(Job)).all() == []

    book_id = _book(session_factory, storage)
    _publish(session_factory, book_id)
    with session_factory() as s, pytest.raises(Conflict, match="already has an active job"):
        create_publish_job(s, book_id, fmt="pdf-book", callback_url=None, actor="t")


def test_source_book_is_refused(session_factory, storage):
    translated = imported(session_factory, storage)
    with session_factory() as s:
        from jerome.orm import Book

        source_id = s.get(Book, translated).source_book_id
    with session_factory() as s, pytest.raises(Conflict, match="is the source of"):
        create_publish_job(s, source_id, fmt="epub-book", callback_url=None, actor="t")


def test_two_runs_keep_two_files(session_factory, storage, deps):
    book_id = _book(session_factory, storage)
    first = _publish(session_factory, book_id)
    drain(session_factory, deps)
    second = _publish(session_factory, book_id)
    drain(session_factory, deps)
    names = [_job(session_factory, j).stats["file"] for j in (first, second)]
    assert names == [f"novel-{first}.epub", f"novel-{second}.epub"]
    for name in names:
        assert storage.read_export(book_id, name)[:2] == b"PK"


def _translated(session_factory, storage, test_settings) -> int:
    book_id = imported(session_factory, storage, translated_title="Der Roman")
    registry = registry_for(test_settings, _provider())
    start_translation(session_factory, registry, book_id)
    drain(session_factory, deps_for(registry))
    with session_factory() as s:
        update_publication(s, book_id, {"authors": ["Ann Author"]})
        s.commit()
    return book_id


def _provider():
    from jerome.models import FakeProvider

    return FakeProvider([echo_translator("DE ")] * 20)


def test_translated_book_uses_translated_title_and_language(
    session_factory, storage, deps, test_settings
):
    book_id = _translated(session_factory, storage, test_settings)
    job_id = _publish(session_factory, book_id)
    drain(session_factory, deps)
    job = _job(session_factory, job_id)
    assert job.status == "completed", job.tasks[0].error
    with zipfile.ZipFile(io.BytesIO(storage.read_export(book_id, job.stats["file"]))) as z:
        opf = etree.fromstring(z.read("OEBPS/content.opf"))
        chapter = z.read("OEBPS/chapter-001.xhtml").decode()
    assert opf.findtext(".//dc:title", namespaces=NS) == "Der Roman"
    assert opf.findtext(".//dc:language", namespaces=NS) == "de-DE"
    assert "DE First para." in chapter


def test_translated_book_pdf_metadata(session_factory, storage, deps, test_settings, weasy):
    from pypdf import PdfReader

    book_id = _translated(session_factory, storage, test_settings)
    job_id = _publish(session_factory, book_id, fmt="pdf-book")
    drain(session_factory, deps)
    job = _job(session_factory, job_id)
    assert job.status == "completed", job.tasks[0].error
    reader = PdfReader(io.BytesIO(storage.read_export(book_id, job.stats["file"])))
    assert reader.metadata.title == "Der Roman"
    assert reader.trailer["/Root"].get("/Lang") == "de-DE"


def test_publish_task_is_not_a_chapter_task(session_factory, storage):
    book_id = _book(session_factory, storage)
    job_id = _publish(session_factory, book_id)
    with session_factory() as s:
        task = s.scalars(select(JobTask).where(JobTask.job_id == job_id)).one()
        assert task.chapter_id is None and task.kind == "publish"
