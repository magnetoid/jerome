from decimal import Decimal

import pypdfium2 as pdfium
import pytest
from sqlalchemy import select

from jerome.core.auto_apply import DEFAULT_POLICY
from jerome.core.books import ensure_imported, start_pdf_import
from jerome.core.projects import chapter_review, derive_stage, list_projects
from jerome.errors import Conflict, UnsupportedDocumentError, UnsupportedLanguagePair
from jerome.jobs.estimate import estimate_job
from jerome.jobs.service import create_job, create_publish_job, create_translation_job
from jerome.orm import Chapter, Job


def make_pdf(pages: int = 2) -> bytes:
    import io

    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(200, 200)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def start(session, storage, **kw):
    args = dict(
        filename="My Book.pdf",
        data=make_pdf(),
        variant="en-US",
        title=None,
        local_only=False,
        auto_apply=DEFAULT_POLICY,
        engine="tesseract",
        reocr=False,
        work="proofread",
        target_variant=None,
        translated_title=None,
        actor="tester",
    )
    args.update(kw)
    return start_pdf_import(session, storage, **args)


def test_start_pdf_import_makes_an_importing_book_and_a_scan_task(session, tmp_storage):
    book = start(session, tmp_storage)
    assert book.import_status == "importing" and book.format == "docx"
    assert book.title == "My Book" and book.original_filename == "My Book.docx"
    assert book.work == "proofread" and book.style_sheet.variant == "en-US"
    assert tmp_storage.read_source_pdf(book.id).startswith(b"%PDF")
    job = session.scalars(select(Job).where(Job.book_id == book.id)).one()
    assert job.type == "import" and job.status == "queued" and job.passes == ["tesseract"]
    assert job.max_cost == Decimal(0)
    assert job.stats == {
        "pages": 2,
        "pages_done": 0,
        "reocr": False,
        "variant": "en-US",
        "target_variant": None,
    }
    assert [(t.kind, t.status) for t in job.tasks] == [("import_scan", "queued")]


def test_start_pdf_import_for_a_translation_checks_the_pair(session, tmp_storage):
    with pytest.raises(UnsupportedLanguagePair):
        start(session, tmp_storage, work="translate", target_variant="en-GB")
    book = start(
        session,
        tmp_storage,
        work="translate",
        target_variant="de-DE",
        translated_title="Mein Buch",
        engine="ai",
    )
    assert book.work == "translate" and book.translated_title == "Mein Buch"
    assert book.import_status == "importing"


def test_bad_pdf_is_an_unsupported_document(session, tmp_storage):
    with pytest.raises(UnsupportedDocumentError):
        start(session, tmp_storage, data=b"not a pdf")


def test_local_only_refuses_ai(session, tmp_storage):
    with pytest.raises(ValueError):
        start(session, tmp_storage, engine="ai", local_only=True)
    start(session, tmp_storage, engine="tesseract", local_only=True)


def test_jobs_review_and_publish_refuse_an_importing_book(session, tmp_storage, registry):
    book = start(session, tmp_storage)
    # a chapter, so the review refusal is the import guard and nothing else
    chapter = Chapter(book_id=book.id, ordinal=0, title="One", proofread=True)
    session.add(chapter)
    session.flush()
    calls = [
        lambda: ensure_imported(book),
        lambda: create_job(
            session, registry, book.id, max_cost=Decimal(1), callback_url=None, actor="t"
        ),
        lambda: create_translation_job(
            session, registry, book.id, max_cost=Decimal(1), callback_url=None, actor="t"
        ),
        lambda: create_publish_job(session, book.id, fmt="epub", callback_url=None, actor="t"),
        lambda: estimate_job(session, registry, book.id),
        lambda: chapter_review(session, chapter.id, 0, 10),
    ]
    for call in calls:
        with pytest.raises(Conflict, match="still importing"):
            call()


def test_a_failed_import_is_refused_with_its_error(session, tmp_storage):
    book = start(session, tmp_storage)
    book.import_status, book.import_error = "failed", "page 3 broke"
    with pytest.raises(Conflict, match="import failed: page 3 broke"):
        ensure_imported(book)
    book.import_status = None
    ensure_imported(book)


def test_stage_is_importing_then_needs_attention_on_failure():
    kw = dict(active_job=True, latest_job_status="running", open_count=0, completed_jobs=0)
    assert derive_stage(**kw, import_status="importing") == "importing"
    assert derive_stage(**kw, import_status="failed") == "needs_attention"
    assert derive_stage(**kw) == "proofreading"


def test_projects_list_shows_the_import_stage(session, tmp_storage):
    book = start(session, tmp_storage)
    (p,) = [p for p in list_projects(session) if p["id"] == book.id]
    assert p["stage"] == "importing"
    book.import_status = "failed"
    session.flush()
    (p,) = [p for p in list_projects(session) if p["id"] == book.id]
    assert p["stage"] == "needs_attention"


def _kills_the_counter(pdf: bytes):
    import os

    os._exit(1)


def _never_counts(pdf: bytes):
    import time

    time.sleep(30)


def test_the_page_count_runs_in_its_own_process(session, tmp_storage, monkeypatch):
    from jerome.pdfimport import reader

    monkeypatch.setattr(reader, "_count_or_refusal", _kills_the_counter)
    with pytest.raises(UnsupportedDocumentError, match="cannot be read"):
        start(session, tmp_storage)
    monkeypatch.setattr(reader, "_count_or_refusal", _never_counts)
    monkeypatch.setattr(reader, "COUNT_TIMEOUT", 1)
    with pytest.raises(UnsupportedDocumentError, match="cannot be read"):
        start(session, tmp_storage)


def test_a_pdf_over_the_page_limit_says_so(session, tmp_storage):
    with pytest.raises(UnsupportedDocumentError, match="2001 pages; the limit is 2000"):
        start(session, tmp_storage, data=make_pdf(2001))
