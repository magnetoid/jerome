"""Uploading a PDF over REST: the import starts, its limits, and the estimate routes."""

import io

import pypdfium2 as pdfium
import pytest
from sqlalchemy import select

from jerome.api.deps import get_registry
from jerome.orm import Book, Job


def blank_pdf(pages: int = 1) -> bytes:
    """A PDF of empty pages, made without WeasyPrint."""
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(200, 200)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def upload_pdf(client, auth, **form):
    fields = {"variant": "en-US", **form}
    return client.post(
        "/v1/books", headers=auth, files={"file": ("scan.pdf", blank_pdf())}, data=fields
    )


def test_pdf_upload_starts_an_import(client, auth, session):
    r = upload_pdf(client, auth, ocr_engine="tesseract", reocr="false")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["import_status"] == "importing"
    assert body["import_error"] is None and body["import_report"] is None
    job = session.scalar(select(Job).where(Job.book_id == body["id"]))
    assert (job.type, job.passes, job.status) == ("import", ["tesseract"], "queued")
    assert job.stats["reocr"] is False and job.stats["pages"] == 1
    assert session.get(Book, body["id"]).local_only is False


def test_pdf_engine_defaults(client, auth, session, registry):
    client.app.dependency_overrides[get_registry] = lambda: registry
    ai = upload_pdf(client, auth).json()
    local = upload_pdf(client, auth, local_only="true").json()
    jobs = {j.book_id: j for j in session.scalars(select(Job))}
    assert jobs[ai["id"]].passes == ["ai"]
    assert jobs[local["id"]].passes == ["tesseract"]
    assert jobs[ai["id"]].stats["reocr"] is True


def test_pdf_over_its_limit_is_413(client, auth, test_settings):
    test_settings.max_pdf_upload_mb = 0
    assert upload_pdf(client, auth).status_code == 413


def test_pdf_may_exceed_the_docx_limit(client, auth, test_settings):
    test_settings.max_upload_mb = 0  # DOCX and EPUB are capped, a PDF has its own limit
    assert upload_pdf(client, auth, ocr_engine="tesseract").status_code == 201


def test_ocr_ai_refused_for_local_only_is_422(client, auth, session):
    r = upload_pdf(client, auth, ocr_engine="ai", local_only="true")
    assert r.status_code == 422
    assert session.scalar(select(Book.id)) is None


def test_unreadable_pdf_is_422(client, auth):
    r = client.post(
        "/v1/books",
        headers=auth,
        files={"file": ("bad.pdf", b"%PDF-1.4 nonsense")},
        data={"variant": "en-US", "ocr_engine": "tesseract"},
    )
    assert r.status_code == 422


@pytest.fixture
def awaiting(client, auth, session):
    book = upload_pdf(client, auth, ocr_engine="ai").json()
    job = session.scalar(select(Job).where(Job.book_id == book["id"]))
    job.stats = {**job.stats, "awaiting_estimate": True}
    session.flush()
    return book["id"], job


def test_confirm_and_use_tesseract_routes(client, auth, awaiting):
    book_id, job = awaiting
    r = client.post(f"/v1/books/{book_id}/import/confirm", headers=auth, json={"max_cost": "2.5"})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == job.id and r.json()["max_cost"].startswith("2.5")
    assert r.json()["stats"]["awaiting_estimate"] is False
    # answered: nothing is waiting any more
    again = client.post(f"/v1/books/{book_id}/import/use-tesseract", headers=auth)
    assert again.status_code == 409


def test_use_tesseract_switches_the_engine(client, auth, awaiting):
    book_id, _ = awaiting
    r = client.post(f"/v1/books/{book_id}/import/use-tesseract", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["passes"] == ["tesseract"]


def test_estimate_routes_errors(client, auth, awaiting):
    book_id, _ = awaiting
    missing = client.post("/v1/books/99999/import/confirm", headers=auth, json={"max_cost": "1"})
    assert missing.status_code == 404
    assert client.post("/v1/books/99999/import/use-tesseract", headers=auth).status_code == 404
    bad = client.post(f"/v1/books/{book_id}/import/confirm", headers=auth, json={"max_cost": "0"})
    assert bad.status_code == 422
