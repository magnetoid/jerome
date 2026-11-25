"""Rendering a PDF page to a PNG."""

import io

from jerome.pdfimport.reader import run_isolated


def _render(pdf: bytes, page: int, dpi: int) -> bytes:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf)
    try:
        image = document[page].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        document.close()
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def render_page(pdf: bytes, page: int, dpi: int = 300) -> bytes:
    """A PNG of one page (0-based). Raises PageReadError if rendering fails or times out."""
    return run_isolated(_render, pdf, page, dpi)
