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
from jerome.jobs.service import cancel_job, confirm_import, resume_job, use_tesseract
from jerome.jobs.worker import WorkerDeps, claim_task, run_task
from jerome.models import FakeProvider, ModelOutputError, ModelRefusal, Registry, Usage
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


def start(
    sf, storage, pdf, *, engine="ai", reocr=True, work="proofread", target=None, translated=None
) -> int:
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
            translated_title=translated,
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
    billed = {"usage": Usage(1000, 150), "model": "claude-opus-5-5", "provider": "fake"}
    provider = FakeProvider(
        [
            ModelRefusal("no", **billed),
            ModelOutputError("not JSON", **billed),
            {"blocks": []},
            RuntimeError("Anthropic API key is not set"),
        ]
    )
    # no ocr pass configured: the ai pass's model reads the pages and prices the estimate
    models = {"ai": {"provider": "fake", "model": "claude-opus-5-5"}}
    deps = deps_for(test_settings, tmp_storage, provider, models)
    scans = scan_pdf(text_pdf(TWO + BREAK + THREE + BREAK + ONE + BREAK + TWO))
    book_id = start(session_factory, tmp_storage, joined(text_pdf(ONE), scans))
    drain(session_factory, deps)
    assert Decimal(job_of(session_factory, book_id).estimate["cost"]) > 0
    with session_factory() as s:
        confirm_import(s, book_id, Decimal("5"))
        s.commit()
    drain(session_factory, deps)
    assert len(provider.calls) == 4 and tesseract == [1, 2, 3, 4]
    assert usage_passes(session_factory) == ["ocr"] * 3  # every billed call is on the books
    assert job_of(session_factory, book_id).cost_actual > 0
    book = book_of(session_factory, book_id)
    assert book.import_status is None
    report = book.import_report
    assert report["engine_pages"] == {"ai": 0, "tesseract": 4}
    assert report["ai_fallbacks"] == {"refusal": 1, "billed_error": 1, "empty": 1, "error": 1}
    assert report["failed_pages"] == []
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
    assert report["ai_fallbacks"] == {"cost_cap": 1}


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
    assert book.import_report["failed_pages"] == [2]  # page numbers as a reader counts them
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
        session_factory,
        tmp_storage,
        pdf,
        engine="tesseract",
        work="translate",
        target="de-DE",
        translated="Die Reise",
    )
    drain(session_factory, deps_for(test_settings, tmp_storage))
    book = book_of(session_factory, book_id)
    assert book.import_status is None and book.import_report["pages"] == 2
    assert book.work == "translate" and book.style_sheet.variant == "de-DE"
    assert book.source_book_id is not None
    source = book_of(session_factory, book.source_book_id)
    assert book.title == "Die Reise" and source.title == "Voyage"
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


def _inked_pdf() -> bytes:
    """A one-page scan with lines of ink on it, made with Pillow alone."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (420, 595), "white")
    draw = ImageDraw.Draw(image)
    for y in range(60, 500, 24):
        draw.rectangle([50, y, 370, y + 8], fill="black")
    out = io.BytesIO()
    image.save(out, "PDF", resolution=72)
    return out.getvalue()


def test_confirm_before_the_pause_sets_the_cap_and_the_job_runs_on(
    session_factory, tmp_storage, test_settings, tesseract
):
    provider = FakeProvider([ai_reply("The ship came in on a grey morning.")])
    deps = deps_for(test_settings, tmp_storage, provider)
    book_id = start(session_factory, tmp_storage, _inked_pdf())
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
    book_id = start(session_factory, tmp_storage, _inked_pdf())
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


def _picture_png(width: int, height: int) -> bytes:
    """A colourful picture (not near white), as a PNG."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), (30, 90, 160))
    draw = ImageDraw.Draw(image)
    for i in range(0, width, 40):
        draw.rectangle([i, 0, i + 20, height], fill=(200, 60 + i % 150, 40))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_a_saved_picture_is_a_jpeg_of_at_most_1600_px(
    session_factory, tmp_storage, test_settings, tesseract
):
    import base64

    from PIL import Image

    data = base64.b64encode(_picture_png(600, 400)).decode()
    pdf = text_pdf(f'<img src="data:image/png;base64,{data}" style="width: 120mm">')
    book_id = start(session_factory, tmp_storage, pdf, engine="tesseract")
    drain(session_factory, deps_for(test_settings, tmp_storage))
    assert job_of(session_factory, book_id).stats["kinds"] == ["image"]
    saved = tmp_storage.read_page(book_id, 0)
    (block,) = saved["blocks"]
    assert saved["source"] == "image" and block["image_type"] == "image/jpeg"
    with Image.open(io.BytesIO(base64.b64decode(block["image"]))) as image:
        assert image.format == "JPEG" and max(image.size) <= 1600  # rendered at 2480 x 3508
    assert book_of(session_factory, book_id).import_report["image_pages"] == 1


def _near_white_pdf() -> bytes:
    """A scanned blank page: near-white paper with specks of dust, a full-page image."""
    import random

    from PIL import Image, ImageDraw

    image = Image.new("L", (1240, 1754), 250)
    draw = ImageDraw.Draw(image)
    rnd = random.Random(7)
    for _ in range(3000):
        x, y = rnd.randrange(1240), rnd.randrange(1754)
        draw.point((x, y), fill=rnd.randint(80, 200))
    out = io.BytesIO()
    image.convert("RGB").save(out, "PDF", resolution=150)
    return out.getvalue()


@pytest.mark.parametrize("ocr_engine", ["ai", "tesseract"])
def test_a_blank_scanned_page_is_blank_for_both_engines(
    session_factory, tmp_storage, test_settings, tesseract, ocr_engine
):
    provider = FakeProvider([])
    deps = deps_for(test_settings, tmp_storage, provider)
    book_id = start(
        session_factory, tmp_storage, joined(_near_white_pdf(), _image_pdf()), engine=ocr_engine
    )
    drain(session_factory, deps)
    if ocr_engine == "ai":
        assert job_of(session_factory, book_id).stats["kinds"] == ["scan", "scan"]
        with session_factory() as s:
            confirm_import(s, book_id, Decimal("5"))
            s.commit()
        drain(session_factory, deps)
    assert job_of(session_factory, book_id).status == "completed"
    assert provider.calls == [] and tesseract == []
    assert tmp_storage.read_page(book_id, 0) == {
        "page": 0,
        "source": "blank",
        "error": None,
        "blocks": [],
    }
    report = book_of(session_factory, book_id).import_report
    assert report["blank_pages"] == 2 and report["image_pages"] == 0


def _classify_all_but_last(pdf: bytes) -> list:
    """classify_pages, but it misses the last page (pdfminer and pdfium disagree)."""
    from jerome.pdfimport.reader import classify_pages

    return classify_pages(pdf)[:-1]


def _classify_fails(pdf: bytes) -> list:
    raise ValueError("pdfminer cannot parse this PDF")


@pytest.mark.parametrize("classify", [_classify_all_but_last, _classify_fails])
def test_pages_the_sorting_missed_are_read_as_scans(
    session_factory, tmp_storage, test_settings, tesseract, monkeypatch, classify
):
    monkeypatch.setattr(import_job, "classify_pages", classify)
    pdf = text_pdf(ONE + BREAK + TWO + BREAK + THREE)
    book_id = start(session_factory, tmp_storage, pdf, engine="tesseract")
    drain(session_factory, deps_for(test_settings, tmp_storage))
    job = job_of(session_factory, book_id)
    assert job.status == "completed" and len(job.stats["kinds"]) == 3
    assert tesseract == ([2] if classify is _classify_all_but_last else [0, 1, 2])
    report = book_of(session_factory, book_id).import_report
    assert report["pages"] == 3 and report["failed_pages"] == []


def _fail_the_import(sf, storage, deps) -> int:
    book_id = start(sf, storage, _image_pdf(), engine="tesseract")
    source = storage.root / "books" / str(book_id) / "source.pdf"
    kept = source.read_bytes()
    source.unlink()
    drain(sf, deps)
    assert job_of(sf, book_id).status == "failed"
    source.write_bytes(kept)
    return book_id


def test_resume_after_a_failed_import_makes_the_book_ready(
    session_factory, tmp_storage, test_settings
):
    deps = deps_for(test_settings, tmp_storage)
    book_id = _fail_the_import(session_factory, tmp_storage, deps)
    assert book_of(session_factory, book_id).import_status == "failed"
    with session_factory() as s:
        resume_job(s, job_of(session_factory, book_id).id)
        s.commit()
    book = book_of(session_factory, book_id)
    assert book.import_status == "importing" and book.import_error is None
    drain(session_factory, deps)
    assert job_of(session_factory, book_id).status == "completed"
    book = book_of(session_factory, book_id)
    assert book.import_status is None and book.import_error is None


def test_a_resumed_import_that_fails_again_says_why(
    session_factory, tmp_storage, test_settings, monkeypatch
):
    deps = deps_for(test_settings, tmp_storage)
    book_id = _fail_the_import(session_factory, tmp_storage, deps)
    assert "source PDF" in book_of(session_factory, book_id).import_error
    with session_factory() as s:
        resume_job(s, job_of(session_factory, book_id).id)
        s.commit()

    def broken(book: int) -> bytes:
        raise RuntimeError("the disk is on fire")

    monkeypatch.setattr(tmp_storage, "read_source_pdf", broken)
    drain(session_factory, deps)
    book = book_of(session_factory, book_id)
    assert book.import_status == "failed" and book.import_error == "the disk is on fire"


def test_cancelling_an_import_fails_the_book_until_it_is_resumed(
    session_factory, tmp_storage, test_settings
):
    book_id = start(session_factory, tmp_storage, _image_pdf(), engine="tesseract")
    job_id = job_of(session_factory, book_id).id
    with session_factory() as s:
        cancel_job(s, job_id)
        s.commit()
    book = book_of(session_factory, book_id)
    assert book.import_status == "failed" and book.import_error == "The import was cancelled"
    with session_factory() as s:
        resume_job(s, job_id)
        s.commit()
    assert book_of(session_factory, book_id).import_status == "importing"
    drain(session_factory, deps_for(test_settings, tmp_storage))
    assert book_of(session_factory, book_id).import_status is None


def _page_png(draw=None, paper: int = 255, dust: int = 0) -> bytes:
    """An A4 page at a true 300 dpi (2480 x 3508), with ``dust`` specks of 1 to 3 px."""
    import random

    from PIL import Image, ImageDraw

    image = Image.new("L", (2480, 3508), paper)
    pen = ImageDraw.Draw(image)
    rnd = random.Random(3)
    for _ in range(dust):
        x, y, size = rnd.randrange(2480), rnd.randrange(3508), rnd.randint(0, 2)
        pen.rectangle([x, y, x + size, y + size], fill=rnd.randint(60, 200))
    if draw:
        draw(pen)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _small_text(pen) -> None:
    from PIL import ImageFont

    font = ImageFont.load_default(size=42)  # 10 pt at 300 dpi
    pen.text((300, 1700), "The end.", fill=90, font=font)  # grey ink, as scans give it


def _hairlines(pen) -> None:
    pen.rectangle([400, 400, 2000, 1400], outline=120, width=1)
    pen.line([400, 400, 2000, 1400], fill=120, width=1)


@pytest.mark.parametrize(
    ("png", "blank"),
    [
        (_page_png(), True),
        (_page_png(dust=3000), True),
        (_page_png(paper=235, dust=3000), True),
        (_page_png(_small_text), False),
        (_page_png(_small_text, dust=3000), False),
        (_page_png(_hairlines), False),
    ],
    ids=["white", "dust", "grey-paper-dust", "10pt-line", "10pt-line-dust", "hairline"],
)
def test_is_blank_ignores_dust_but_never_text_or_lines(png, blank):
    assert import_job.is_blank(png) is blank


def test_one_short_line_scanned_at_300_dpi_is_not_blank():
    from jerome.pdfimport.render import render_page

    pdf = scan_pdf(text_pdf('<p style="font-size: 10pt">The end.</p>'), dpi=300)
    assert import_job.is_blank(render_page(pdf, 0)) is False


def test_a_pale_picture_stays_a_picture(session_factory, tmp_storage, test_settings, tesseract):
    import base64

    from PIL import Image, ImageDraw

    pale = Image.new("RGB", (600, 400), (235, 235, 230))
    ImageDraw.Draw(pale).ellipse([100, 50, 500, 350], outline=(205, 210, 215), width=2)
    out = io.BytesIO()
    pale.save(out, "PNG")
    data = base64.b64encode(out.getvalue()).decode()
    pdf = text_pdf(f'<img src="data:image/png;base64,{data}" style="width: 120mm">')
    book_id = start(session_factory, tmp_storage, pdf, engine="tesseract")
    drain(session_factory, deps_for(test_settings, tmp_storage))
    assert job_of(session_factory, book_id).stats["kinds"] == ["image"]
    saved = tmp_storage.read_page(book_id, 0)
    assert saved["source"] == "image" and len(saved["blocks"]) == 1
    report = book_of(session_factory, book_id).import_report
    assert report["image_pages"] == 1 and report["blank_pages"] == 0


def _dark_title_page(pen) -> None:
    """A black part-title page with a white 24 pt title, as a 300 dpi scan gives it."""
    from PIL import ImageFont

    pen.rectangle([0, 0, 2480, 3508], fill=12)
    pen.text((700, 1600), "Part Two", fill=245, font=ImageFont.load_default(size=100))


def test_a_dark_page_with_a_light_title_is_not_blank():
    assert import_job.is_blank(_page_png(_dark_title_page)) is False


def test_a_dark_title_page_is_read_not_dropped(
    session_factory, tmp_storage, test_settings, tesseract
):
    from PIL import Image

    out = io.BytesIO()
    Image.open(io.BytesIO(_page_png(_dark_title_page))).convert("RGB").save(
        out, "PDF", resolution=300
    )
    book_id = start(session_factory, tmp_storage, out.getvalue(), engine="tesseract")
    drain(session_factory, deps_for(test_settings, tmp_storage))
    assert job_of(session_factory, book_id).stats["kinds"] == ["scan"]
    assert tesseract == [0]
    assert book_of(session_factory, book_id).import_report["blank_pages"] == 0


def test_the_cap_reached_after_confirm_never_pauses_tesseract_reads_the_rest(
    session_factory, tmp_storage, test_settings, tesseract, monkeypatch
):
    monkeypatch.setattr(import_job, "PAGES_PER_TASK", 1)  # every AI page is a task's first
    provider = FakeProvider([ai_reply("The ship came in on a grey morning.")])
    deps = deps_for(test_settings, tmp_storage, provider)
    book_id = start(
        session_factory, tmp_storage, scan_pdf(text_pdf(ONE + BREAK + TWO + BREAK + THREE))
    )
    drain(session_factory, deps)
    with session_factory() as s:
        confirm_import(s, book_id, Decimal("0.0001"))  # covers the first page only
        s.commit()
    drain(session_factory, deps)
    job = job_of(session_factory, book_id)
    assert job.status == "completed"
    assert len(provider.calls) == 1 and len(tesseract) == 2  # which page depends on the queue
    report = book_of(session_factory, book_id).import_report
    assert report["engine_pages"] == {"ai": 1, "tesseract": 2}
    assert report["ai_fallbacks"] == {"cost_cap": 2}


def test_an_import_paused_at_the_cap_after_confirm_can_be_raised_or_given_to_tesseract(
    session_factory, tmp_storage, test_settings, tesseract
):
    """An import paused at its cap after the owner confirmed (as older jobs could be)."""
    provider = FakeProvider([ai_reply("The ship came in on a grey morning.")])
    deps = deps_for(test_settings, tmp_storage, provider)
    for answer in ("confirm", "tesseract"):
        book_id = start(session_factory, tmp_storage, _inked_pdf())
        drain(session_factory, deps)
        with session_factory() as s:
            job = s.scalars(select(Job).where(Job.book_id == book_id)).one()
            job.stats = {**job.stats, "awaiting_estimate": False}
            s.commit()
        with session_factory() as s:
            if answer == "confirm":
                resumed = confirm_import(s, book_id, Decimal("5"))
                assert resumed.max_cost == Decimal("5")
            else:
                resumed = use_tesseract(s, book_id)
                assert resumed.passes == ["tesseract"]
            assert resumed.status == "queued"
            s.commit()
        drain(session_factory, deps)
        assert job_of(session_factory, book_id).status == "completed"
    assert len(provider.calls) == 1 and tesseract == [0]


def _outlined_text_pdf() -> bytes:
    """A page whose "text" is vector shapes only (as text converted to outlines): no text
    object and no image on it."""
    bars = "".join(
        f'<rect x="10" y="{y}" width="{150 - y % 40}" height="3" fill="black"/>'
        for y in range(10, 200, 8)
    )
    svg = f'<svg width="170mm" height="210mm" viewBox="0 0 170 210">{bars}</svg>'
    return text_pdf(svg)


@pytest.mark.parametrize("ocr_engine", ["ai", "tesseract"])
def test_a_page_of_outlined_text_is_read_as_a_scan(
    session_factory, tmp_storage, test_settings, tesseract, ocr_engine
):
    provider = FakeProvider([ai_reply("Outlined words, read by the AI.")])
    deps = deps_for(test_settings, tmp_storage, provider)
    pdf = joined(_outlined_text_pdf(), text_pdf("<p></p>"))
    book_id = start(session_factory, tmp_storage, pdf, engine=ocr_engine)
    drain(session_factory, deps)
    job = job_of(session_factory, book_id)
    assert job.stats["kinds"] == ["scan", "blank"] and job.stats["ocr_pages"] == 1
    if ocr_engine == "ai":
        assert job.estimate["ocr_pages"] == 1
        with session_factory() as s:
            confirm_import(s, book_id, Decimal("5"))
            s.commit()
        drain(session_factory, deps)
        assert len(provider.calls) == 1
    else:
        assert tesseract == [0]
    report = book_of(session_factory, book_id).import_report
    assert report["ocr_pages"] == 1 and report["blank_pages"] == 1
    assert report["no_text"] is False


def test_a_book_with_no_text_says_so(session_factory, tmp_storage, test_settings, tesseract):
    book_id = start(session_factory, tmp_storage, _image_pdf(), engine="tesseract")
    drain(session_factory, deps_for(test_settings, tmp_storage))
    report = book_of(session_factory, book_id).import_report
    assert report["blank_pages"] == 1 and report["no_text"] is True


@pytest.mark.parametrize("work", ["proofread", "translate"])
def test_details_set_while_importing_survive_the_build(
    session_factory, tmp_storage, test_settings, work
):
    target = "de-DE" if work == "translate" else None
    book_id = start(
        session_factory, tmp_storage, text_pdf(ONE), engine="tesseract", work=work, target=target
    )
    with session_factory() as s:
        book = s.get(Book, book_id)
        book.style_sheet.quote_style = "»«›‹"
        book.style_sheet.house_spellings = {"harbor": "harbour"}
        book.publisher = "Harbour Press"
        book.authors = ["Jane Author"]
        book.year = 1999
        book.description = "A voyage."
        book.trim_size = "5x8in"
        s.commit()
    drain(session_factory, deps_for(test_settings, tmp_storage))
    book = book_of(session_factory, book_id)
    assert book.import_status is None
    assert book.style_sheet.quote_style == "»«›‹"
    assert book.style_sheet.house_spellings == {"harbor": "harbour"}
    assert (book.publisher, book.authors, book.year) == ("Harbour Press", ["Jane Author"], 1999)
    assert (book.description, book.trim_size) == ("A voyage.", "5x8in")


def test_a_reply_that_breaks_the_parser_is_still_on_the_books(
    session_factory, tmp_storage, test_settings, tesseract, monkeypatch
):
    from jerome.pdfimport import ocr_ai

    def broken(raw, png):
        raise RuntimeError("cannot read this block")

    monkeypatch.setattr(ocr_ai, "_block", broken)
    provider = FakeProvider([ai_reply("The ship came in on a grey morning.")])
    deps = deps_for(test_settings, tmp_storage, provider)
    book_id = start(session_factory, tmp_storage, _inked_pdf())
    drain(session_factory, deps)
    with session_factory() as s:
        confirm_import(s, book_id, Decimal("5"))
        s.commit()
    drain(session_factory, deps)
    assert len(provider.calls) == 1 and tesseract == [0]
    assert usage_passes(session_factory) == ["ocr"]
    assert book_of(session_factory, book_id).import_report["ai_fallbacks"] == {"billed_error": 1}
