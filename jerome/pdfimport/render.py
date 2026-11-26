"""Rendering a PDF page to a PNG."""

import io
import math

from jerome.pdfimport.reader import run_isolated

MAX_PIXELS = 60_000_000  # a larger page is rendered at a lower resolution


def _render(pdf: bytes, page: int, dpi: int) -> bytes:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf)
    try:
        sheet = document[page]
        width, height = sheet.get_size()  # points
        scale = dpi / 72
        pixels = width * scale * height * scale
        if pixels > MAX_PIXELS:
            scale *= math.sqrt(MAX_PIXELS / pixels) * 0.999  # rounding stays under the cap
        image = sheet.render(scale=scale).to_pil().convert("RGB")
    finally:
        document.close()
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def render_page(pdf: bytes, page: int, dpi: int = 300) -> bytes:
    """A PNG of one page (0-based), at ``dpi`` unless that would be more than MAX_PIXELS
    (a poster-sized page). Raises PageReadError if rendering fails or times out."""
    return run_isolated(_render, pdf, page, dpi)
