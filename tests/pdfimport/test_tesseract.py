import io
import shutil

import pytest
from PIL import Image, ImageDraw

from jerome.pdfimport import ocr_tesseract
from jerome.pdfimport.ocr_tesseract import TesseractMissing, tesseract_page
from jerome.pdfimport.render import render_page
from tests.fixtures.pdf_builders import scan_pdf, text_pdf

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="Tesseract is not installed"
)


def _png_pdf(width_in: float, height_in: float, draw=None) -> bytes:
    """A one-page PDF made of one image, ``width_in`` by ``height_in`` inches."""
    image = Image.new("RGB", (round(width_in * 72), round(height_in * 72)), "white")
    if draw:
        draw(ImageDraw.Draw(image), image.size)
    out = io.BytesIO()
    image.save(out, "PDF", resolution=72)
    return out.getvalue()


def test_render_page_gives_a_png_at_300_dpi():
    png = render_page(_png_pdf(6, 9), 0)
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert abs(image.width - 1800) <= 2
    assert abs(image.height - 2700) <= 2


@pytest.mark.integration
@needs_tesseract
def test_tesseract_reads_a_scanned_page():
    pdf = scan_pdf(text_pdf("<p>The quick brown fox jumps over the lazy dog.</p>"))
    result = tesseract_page(render_page(pdf, 0), 0, "en")
    assert result.source == "tesseract"
    text = " ".join("".join(r.text for r in b.runs) for b in result.blocks)
    assert "quick brown fox" in text
    assert all(0 <= b.top <= b.bottom <= 1 for b in result.blocks)


def test_tesseract_missing_is_reported(monkeypatch):
    monkeypatch.setattr(ocr_tesseract.shutil, "which", lambda name: None)
    png = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(png, "PNG")
    with pytest.raises(TesseractMissing):
        tesseract_page(png.getvalue(), 0, "en")


@pytest.mark.integration
@needs_tesseract
def test_a_picture_only_page_becomes_an_image_block():
    def draw(d, size):
        w, h = size
        d.ellipse([w * 0.2, h * 0.2, w * 0.8, h * 0.8], fill="black")
        d.rectangle([w * 0.4, h * 0.4, w * 0.6, h * 0.6], fill="grey")

    png = render_page(_png_pdf(6, 9, draw), 0)
    result = tesseract_page(png, 3, "de")
    assert result.page == 3
    assert result.source == "image"
    assert [b.kind for b in result.blocks] == ["image"]
    assert result.blocks[0].image == png
    assert result.blocks[0].image_type == "image/png"
