import os

import pytest

from jerome.errors import UnsupportedDocumentError
from jerome.pdfimport.reader import (
    PageReadError,
    classify_pages,
    count_pages,
    read_text_page,
    run_isolated,
)
from tests.fixtures.pdf_builders import scan_pdf, text_pdf

_BREAK = '<div style="page-break-after: always"></div>'
_PAGE = "<p>The ship came in on a grey morning and the harbour was quiet.</p>"


def test_count_pages():
    pdf = text_pdf(_PAGE + _BREAK + _PAGE + _BREAK + _PAGE)
    assert count_pages(pdf) == 3
    with pytest.raises(UnsupportedDocumentError):
        count_pages(b"not a pdf")


def test_classify_text_scan_and_layer():
    pdf = text_pdf(_PAGE)
    assert classify_pages(pdf) == ["text"]
    assert classify_pages(scan_pdf(pdf)) == ["scan"]
    assert classify_pages(scan_pdf(pdf, with_layer=True)) == ["scan_with_layer"]
    assert classify_pages(text_pdf("<p></p>")) == ["blank"]


def test_read_text_page_finds_heading_italic_and_paragraphs():
    pdf = text_pdf("<h1>Arrival</h1><p>The <em>ship</em> came in.</p><p>It rained.</p>")
    result = read_text_page(pdf, 0)
    assert result.source == "text"
    heading, first, second = result.blocks
    assert heading.kind == "heading" and heading.level == 1
    assert "".join(r.text for r in heading.runs) == "Arrival"
    assert first.kind == "paragraph"
    assert [(r.text, r.italic) for r in first.runs] == [
        ("The ", False),
        ("ship", True),
        (" came in.", False),
    ]
    assert second.kind == "paragraph"
    assert "".join(r.text for r in second.runs) == "It rained."


def _die():
    os._exit(1)


def _double(n):
    return n * 2


def test_a_page_that_kills_the_reader_fails_alone():
    with pytest.raises(PageReadError):
        run_isolated(_die)
    assert run_isolated(_double, 21) == 42


def test_a_page_the_pdf_does_not_have_is_a_page_read_error():
    pdf = text_pdf(_PAGE)
    with pytest.raises(PageReadError):
        read_text_page(pdf, 5)


def _picture(width_mm: int, height_mm: int) -> str:
    import base64
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (400, 400), (40, 90, 160)).save(out, "PNG")
    data = base64.b64encode(out.getvalue()).decode()
    style = f"width: {width_mm}mm; height: {height_mm}mm; display: block"
    return f'<img src="data:image/png;base64,{data}" style="{style}">'


def test_a_page_mostly_covered_by_a_picture_and_no_text_is_a_scan():
    assert classify_pages(text_pdf(_picture(170, 200))) == ["scan"]  # 55% of the page
    assert classify_pages(text_pdf(_picture(120, 80))) == ["image"]  # 15%
