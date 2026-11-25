"""The worker runs a PDF import: scan, page and build tasks (jerome.pdfimport.job)."""

import io
from decimal import Decimal

import httpx
import pypdfium2 as pdfium
import pytest
from sqlalchemy import select

from jerome.core.auto_apply import DEFAULT_POLICY
from jerome.core.books import start_pdf_import
from jerome.errors import Conflict
from jerome.jobs.service import confirm_import, use_tesseract
from jerome.jobs.worker import WorkerDeps, claim_task, run_task
from jerome.models import FakeProvider, ModelRefusal, Registry, Usage
from jerome.orm import Block, Book, Chapter, Job, ModelUsage
from jerome.pdfimport import job as import_job
from jerome.pdfimport.model import PageBlock, PageResult, Run
from jerome.pdfimport.reader import PageReadError, read_text_page
from jerome.proofread.languagetool import LanguageToolClient
from tests.fixtures.pdf_builders import scan_pdf, text_pdf

BREAK = '<div style="page-break-after: always"></div>'
ONE = "<h1>Arrival</h1><p>The ship came in on a grey morning and the harbour was quiet.</p>"
TWO = "<h1>Departure</h1><p>They left again before the rain had stopped falling.</p>"
THREE = "<p>A third page of plain text, to close the book.</p>"
OCR = {"ocr": {"provider": "fake", "model": "claude-opus-5-5"}}


def joined(*pdfs: bytes) -> bytes:
    """One PDF made of the pages of ``pdfs``, in order."""
    out = pdfium.PdfDocument.new()
    for data in pdfs:
        out.import_pages(pdfium.PdfDocument(data))
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()


def deps_for(test_settings, storage, provider=None, models=OCR) -> WorkerDeps:
    settings = test_settings.model_copy(update={"models": models})
    registry = Registry(settings, {"fake": provider or FakeProvider([])})
    lt = LanguageToolClient("http://lt.test", httpx.Client())
    return WorkerDeps(registry, lt, None, sleep_base=0, storage=storage)


def start(sf, storage, pdf, *, engine="ai", reocr=True, work="proofread", target=None) -> int:
    with sf() as s:
        book = start_pdf_import(
            s,
            storage,
            filename="Voyage.pdf",
            data=pdf,
            variant="en-US",
            title=None,
            local_only=False,
            auto_apply=DEFAULT_POLICY,
            engine=engine,
            reocr=reocr,
            work=work,
            target_variant=target,
            translated_title=None,
            actor="tester",
        )
        s.commit()
        return book.id


def drain(sf, deps) -> None:
    """Run tasks as the worker loop does, until none is left to claim."""
    for _ in range(100):
        with sf() as s:
            task_id = claim_task(s)
        if task_id is None:
            return
        run_task(sf, task_id, deps)
    raise AssertionError("the tasks never ran out")


def job_of(sf, book_id) -> Job:
    with sf() as s:
        return s.scalars(select(Job).where(Job.book_id == book_id)).one()


def book_of(sf, book_id) -> Book:
    with sf() as s:
        book = s.get(Book, book_id)
        _ = book.style_sheet.variant
        return book


def texts(sf, book_id) -> list[str]:
    with sf() as s:
        return list(
            s.scalars(select(Block.text).where(Block.book_id == book_id).order_by(Block.id))
        )


def chapter_titles(sf, book_id) -> list[str]:
    with sf() as s:
        stmt = select(Chapter.title).where(Chapter.book_id == book_id).order_by(Chapter.ordinal)
        return list(s.scalars(stmt))


def usage_passes(sf) -> list[str]:
    with sf() as s:
        return list(s.scalars(select(ModelUsage.pass_name).order_by(ModelUsage.id)))


def ai_reply(text: str) -> dict:
    block = {"kind": "paragraph", "text": text, "level": 1, "note": None, "box": [0, 0.3, 1, 0.4]}
    return {"blocks": [block]}


@pytest.fixture
def tesseract(monkeypatch):
    """Tesseract replaced by a reader that records the pages it was given."""
    read: list[int] = []

    def fake(png: bytes, page: int, language: str) -> PageResult:
        assert png.startswith(b"\x89PNG") and language == "en"
        read.append(page)
        run = Run(f"Tesseract read page {page + 1} of the book.")
        return PageResult(page, "tesseract", [PageBlock("paragraph", [run], top=0.3, bottom=0.4)])

    monkeypatch.setattr(import_job, "tesseract_page", fake)
    return read


def _fails_on_page_1(pdf: bytes, page: int) -> PageResult:
    """read_text_page, except that page 1 (the second) cannot be read. Module level, so the
    isolated reader can run it."""
    if page == 1:
        raise PageReadError("broken page")
    return read_text_page(pdf, page)


def test_text_pdf_imports_without_ocr(session_factory, tmp_storage, test_settings, tesseract):
    provider = FakeProvider([])
    book_id = start(session_factory, tmp_storage, text_pdf(ONE + BREAK + TWO))
    drain(session_factory, deps_for(test_settings, tmp_storage, provider))
    book = book_of(session_factory, book_id)
    assert book.import_status is None and book.import_error is None
    assert book.import_report["pages"] == 2 and book.import_report["text_pages"] == 2
    assert book.import_report["failed_pages"] == []
    assert chapter_titles(session_factory, book_id) == ["Arrival", "Departure"]
    assert any("harbour was quiet" in t for t in texts(session_factory, book_id))
    assert tmp_storage.read_original(book_id).startswith(b"PK")
    job = job_of(session_factory, book_id)
    assert job.status == "completed" and job.estimate is None
    assert job.stats["kinds"] == ["text", "text"] and job.stats["ocr_pages"] == 0
    assert job.stats["pages_done"] == 2 and not job.stats.get("awaiting_estimate")
    assert provider.calls == [] and tesseract == []
    assert usage_passes(session_factory) == []
    with session_factory() as s, pytest.raises(Conflict):
        confirm_import(s, book_id, Decimal("5"))


def test_ai_import_waits_for_the_estimate_then_reads(
    session_factory, tmp_storage, test_settings, tesseract
):
    provider = FakeProvider([ai_reply("They left again before the rain had stopped.")])
    deps = deps_for(test_settings, tmp_storage, provider)
    book_id = start(session_factory, tmp_storage, joined(text_pdf(ONE), scan_pdf(text_pdf(TWO))))
    drain(session_factory, deps)
    job = job_of(session_factory, book_id)
    assert job.status == "paused_cost_cap" and job.stats["awaiting_estimate"] is True
    assert job.stats["kinds"] == ["text", "scan"] and job.stats["ocr_pages"] == 1
    assert job.estimate["ocr_pages"] == 1 and Decimal(job.estimate["cost"]) > 0
    assert job.stats["pages_done"] == 1  # the text page did not wait for the estimate
    assert provider.calls == [] and usage_passes(session_factory) == []
    assert book_of(session_factory, book_id).import_status == "importing"

    with session_factory() as s:
        confirm_import(s, book_id, Decimal("5"))
        s.commit()
    drain(session_factory, deps)
    job = job_of(session_factory, book_id)
    assert job.status == "completed" and job.stats["awaiting_estimate"] is False
    assert job.max_cost == Decimal("5") and job.cost_actual > 0
    assert len(provider.calls) == 1 and provider.calls[0]["images"]
    assert usage_passes(session_factory) == ["ocr"]
    assert tesseract == []
    book = book_of(session_factory, book_id)
    assert book.import_status is None
    assert book.import_report["engine_pages"] == {"ai": 1, "tesseract": 0}
    assert "They left again before the rain had stopped." in texts(session_factory, book_id)


def test_use_tesseract_instead_spends_nothing(
    session_factory, tmp_storage, test_settings, tesseract
):
    provider = FakeProvider([])
    deps = deps_for(test_settings, tmp_storage, provider)
    book_id = start(session_factory, tmp_storage, joined(text_pdf(ONE), scan_pdf(text_pdf(TWO))))
    drain(session_factory, deps)
    assert job_of(session_factory, book_id).status == "paused_cost_cap"
    with session_factory() as s:
        use_tesseract(s, book_id)
        s.commit()
    drain(session_factory, deps)
    job = job_of(session_factory, book_id)
    assert job.status == "completed" and job.passes == ["tesseract"]
    assert job.stats["awaiting_estimate"] is False and job.cost_actual == 0
    assert provider.calls == [] and usage_passes(session_factory) == []
    assert tesseract == [1]
    book = book_of(session_factory, book_id)
    assert book.import_status is None
    assert book.import_report["engine_pages"] == {"ai": 0, "tesseract": 1}
    assert "Tesseract read page 2 of the book." in texts(session_factory, book_id)


def test_ai_failure_falls_back_to_tesseract(session_factory, tmp_storage, test_settings, tesseract):
    refusal = ModelRefusal("no", usage=Usage(1000, 150), model="claude-opus-5-5", provider="fake")
    provider = FakeProvider([refusal])
    # no ocr pass configured: the ai pass's model reads the pages and prices the estimate
    models = {"ai": {"provider": "fake", "model": "claude-opus-5-5"}}
    deps = deps_for(test_settings, tmp_storage, provider, models)
    book_id = start(session_factory, tmp_storage, joined(text_pdf(ONE), scan_pdf(text_pdf(TWO))))
    drain(session_factory, deps)
    assert Decimal(job_of(session_factory, book_id).estimate["cost"]) > 0
    with session_factory() as s:
        confirm_import(s, book_id, Decimal("5"))
        s.commit()
    drain(session_factory, deps)
    assert len(provider.calls) == 1 and tesseract == [1]
    assert usage_passes(session_factory) == ["ocr"]  # the refused call was billed
    assert job_of(session_factory, book_id).cost_actual > 0
    book = book_of(session_factory, book_id)
    assert book.import_status is None
    assert book.import_report["engine_pages"] == {"ai": 0, "tesseract": 1}
    assert book.import_report["failed_pages"] == []
    assert "Tesseract read page 2 of the book." in texts(session_factory, book_id)


def test_cost_cap_reached_mid_task_falls_back_to_tesseract(
    session_factory, tmp_storage, test_settings, tesseract
):
    provider = FakeProvider([ai_reply("The ship came in on a grey morning.")])
    deps = deps_for(test_settings, tmp_storage, provider)
    scans = scan_pdf(text_pdf(ONE + BREAK + TWO))
    book_id = start(session_factory, tmp_storage, scans)
    drain(session_factory, deps)
    with session_factory() as s:
        confirm_import(s, book_id, Decimal("0.0001"))  # less than one page costs
        s.commit()
    drain(session_factory, deps)
    assert len(provider.calls) == 1 and tesseract == [1]
    job = job_of(session_factory, book_id)
    assert job.status == "completed"
    report = book_of(session_factory, book_id).import_report
    assert report["engine_pages"] == {"ai": 1, "tesseract": 1}


def test_reocr_off_uses_the_existing_layer(session_factory, tmp_storage, test_settings, tesseract):
    provider = FakeProvider([])
    pdf = scan_pdf(text_pdf(ONE), with_layer=True)
    book_id = start(session_factory, tmp_storage, pdf, reocr=False)
    drain(session_factory, deps_for(test_settings, tmp_storage, provider))
    job = job_of(session_factory, book_id)
    assert job.status == "completed" and job.estimate is None
    assert job.stats["kinds"] == ["scan_with_layer"] and job.stats["ocr_pages"] == 0
    assert provider.calls == [] and tesseract == []
    assert any("harbour was quiet" in t for t in texts(session_factory, book_id))
    assert book_of(session_factory, book_id).import_report["text_pages"] == 1


def test_failed_page_is_reported_and_the_book_is_ready(
    session_factory, tmp_storage, test_settings, monkeypatch
):
    monkeypatch.setattr(import_job, "read_text_page", _fails_on_page_1)
    pdf = text_pdf(ONE + BREAK + TWO + BREAK + THREE)
    book_id = start(session_factory, tmp_storage, pdf, engine="tesseract")
    drain(session_factory, deps_for(test_settings, tmp_storage))
    book = book_of(session_factory, book_id)
    assert book.import_status is None
    assert book.import_report["failed_pages"] == [1]
    saved = tmp_storage.read_page(book_id, 1)
    assert saved["source"] == "failed" and "broken page" in saved["error"]
    found = " ".join(texts(session_factory, book_id))
    assert "harbour was quiet" in found and "close the book" in found
    assert "rain had stopped" not in found
    assert job_of(session_factory, book_id).status == "completed"


def test_translate_from_pdf_makes_source_and_translation(
    session_factory, tmp_storage, test_settings
):
    pdf = text_pdf(ONE + BREAK + TWO)
    book_id = start(
        session_factory, tmp_storage, pdf, engine="tesseract", work="translate", target="de-DE"
    )
    drain(session_factory, deps_for(test_settings, tmp_storage))
    book = book_of(session_factory, book_id)
    assert book.import_status is None and book.import_report["pages"] == 2
    assert book.work == "translate" and book.style_sheet.variant == "de-DE"
    assert book.source_book_id is not None
    source = book_of(session_factory, book.source_book_id)
    assert source.is_source and source.style_sheet.variant == "en-US"
    assert source.original_filename == "Voyage.docx" and source.import_status is None
    assert chapter_titles(session_factory, source.id) == ["Arrival", "Departure"]
    assert any("harbour was quiet" in t for t in texts(session_factory, source.id))
    with session_factory() as s:
        statuses = set(s.scalars(select(Block.translation_status).where(Block.book_id == book_id)))
    assert statuses == {"pending"}
    assert len(chapter_titles(session_factory, book_id)) == 2
    assert tmp_storage.read_original(source.id).startswith(b"PK")


def test_text_pages_go_ahead_while_the_estimate_waits(
    session_factory, tmp_storage, test_settings, tesseract, monkeypatch
):
    monkeypatch.setattr(import_job, "PAGES_PER_TASK", 1)  # one task per page
    provider = FakeProvider([ai_reply("The ship came in on a grey morning.")])
    deps = deps_for(test_settings, tmp_storage, provider)
    pdf = joined(scan_pdf(text_pdf(ONE)), text_pdf(TWO + BREAK + THREE))
    book_id = start(session_factory, tmp_storage, pdf)
    drain(session_factory, deps)
    job = job_of(session_factory, book_id)
    assert job.status == "paused_cost_cap" and job.stats["kinds"] == ["scan", "text", "text"]
    assert job.stats["pages_done"] == 2 and provider.calls == []
    assert tmp_storage.read_page(book_id, 0) is None
    assert tmp_storage.read_page(book_id, 1) is not None
    assert tmp_storage.read_page(book_id, 2) is not None
    with session_factory() as s:
        confirm_import(s, book_id, Decimal("5"))
        s.commit()
    drain(session_factory, deps)
    assert job_of(session_factory, book_id).status == "completed"
    assert len(provider.calls) == 1 and tesseract == []
    assert book_of(session_factory, book_id).import_report["engine_pages"]["ai"] == 1


def test_no_ocr_model_reads_scans_with_tesseract_and_makes_no_estimate(
    session_factory, tmp_storage, test_settings, tesseract
):
    deps = deps_for(test_settings, tmp_storage, models={})
    book_id = start(session_factory, tmp_storage, joined(text_pdf(ONE), scan_pdf(text_pdf(TWO))))
    drain(session_factory, deps)
    job = job_of(session_factory, book_id)
    assert job.status == "completed" and job.estimate is None
    assert not job.stats.get("awaiting_estimate")
    assert tesseract == [1]
    assert book_of(session_factory, book_id).import_report["engine_pages"]["tesseract"] == 1


def test_an_import_task_that_keeps_failing_fails_the_import(
    session_factory, tmp_storage, test_settings
):
    book_id = start(session_factory, tmp_storage, text_pdf(ONE), engine="tesseract")
    (tmp_storage.root / "books" / str(book_id) / "source.pdf").unlink()
    drain(session_factory, deps_for(test_settings, tmp_storage))
    job = job_of(session_factory, book_id)
    assert job.status == "failed"
    book = book_of(session_factory, book_id)
    assert book.import_status == "failed" and "source PDF" in book.import_error


def _image_pdf() -> bytes:
    """A one-page scan made with Pillow alone: a full-page image with no text layer."""
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (420, 595), "white").save(out, "PDF", resolution=72)
    return out.getvalue()


def test_confirm_before_the_pause_sets_the_cap_and_the_job_runs_on(
    session_factory, tmp_storage, test_settings, tesseract
):
    provider = FakeProvider([ai_reply("The ship came in on a grey morning.")])
    deps = deps_for(test_settings, tmp_storage, provider)
    book_id = start(session_factory, tmp_storage, _image_pdf())
    with session_factory() as s:
        scan_task = claim_task(s)
    run_task(session_factory, scan_task, deps)  # the scan alone: no page is read yet
    job = job_of(session_factory, book_id)
    assert job.stats["awaiting_estimate"] is True and job.status == "running"
    with session_factory() as s:
        confirmed = confirm_import(s, book_id, Decimal("5"))
        assert confirmed.status == "running" and confirmed.max_cost == Decimal("5")
        s.commit()
    drain(session_factory, deps)
    assert job_of(session_factory, book_id).status == "completed"
    assert len(provider.calls) == 1 and tesseract == []
    assert book_of(session_factory, book_id).import_report["engine_pages"]["ai"] == 1


def test_a_billed_page_whose_save_fails_is_recorded_as_ocr(
    session_factory, tmp_storage, test_settings, tesseract, monkeypatch
):
    reply = ai_reply("The ship came in on a grey morning.")
    provider = FakeProvider([reply, reply])
    deps = deps_for(test_settings, tmp_storage, provider)
    book_id = start(session_factory, tmp_storage, _image_pdf())
    drain(session_factory, deps)
    with session_factory() as s:
        confirm_import(s, book_id, Decimal("5"))
        s.commit()
    real_save = tmp_storage.save_page
    failures = [OSError("disk full")]

    def save_once_failing(book, page, d):
        if failures:
            raise failures.pop()
        return real_save(book, page, d)

    monkeypatch.setattr(tmp_storage, "save_page", save_once_failing)
    drain(session_factory, deps)
    assert job_of(session_factory, book_id).status == "completed"
    assert len(provider.calls) == 2  # the failed attempt's page is read again
    assert usage_passes(session_factory) == ["ocr", "ocr"]  # and both calls are on the books
