"""PDFs generated in code for the PDF-import tests (never real books)."""

import base64
import io

_CSS = """
@page { size: A4; margin: 25mm 20mm; %s }
body { font-family: serif; font-size: 12pt; line-height: 1.5; }
h1 { font-size: 24pt; }
"""


def text_pdf(
    html_body: str, *, header: str | None = None, page_numbers: bool = False, css: str = ""
) -> bytes:
    """A text PDF made with WeasyPrint. ``<div style="page-break-after: always">`` splits
    pages; ``header`` repeats at the top of each page, ``page_numbers`` at the bottom;
    ``css`` is added after the default style (e.g. ``@page :left`` headers)."""
    import weasyprint

    margin_boxes = ""
    if header:
        margin_boxes += f'@top-center {{ content: "{header}"; font-size: 9pt; }}'
    if page_numbers:
        margin_boxes += "@bottom-center { content: counter(page); font-size: 9pt; }"
    style = _CSS % margin_boxes + css
    html = f"<html><head><style>{style}</style></head><body>{html_body}</body></html>"
    return weasyprint.HTML(string=html).write_pdf()


def scan_pdf(text_pdf_bytes: bytes, *, with_layer: bool = False, dpi: float = 108) -> bytes:
    """What a scanner makes of ``text_pdf_bytes``: each page rendered to an image and the
    PDF rebuilt from full-page images. With ``with_layer``, the text is kept invisible
    over each image, as OCR software leaves it. ``dpi`` is the scan's resolution."""
    import pypdfium2 as pdfium
    import weasyprint

    document = pdfium.PdfDocument(text_pdf_bytes)
    pages = []
    texts = []
    for page in document:
        pages.append(page.render(scale=dpi / 72).to_pil().convert("RGB"))
        texts.append(page.get_textpage().get_text_range())
    if not with_layer:
        out = io.BytesIO()
        pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=dpi)
        return out.getvalue()
    sections = []
    for image, text in zip(pages, texts, strict=True):
        png = io.BytesIO()
        image.save(png, "PNG")
        data = base64.b64encode(png.getvalue()).decode()
        sections.append(
            '<div style="page-break-after: always">'
            f'<img src="data:image/png;base64,{data}" '
            'style="position: absolute; top: 0; left: 0; width: 210mm; height: 297mm">'
            f'<p style="position: absolute; top: 25mm; left: 20mm; color: transparent">{text}</p>'
            "</div>"
        )
    html = (
        "<html><head><style>@page { size: A4; margin: 0 }</style></head><body>"
        + "".join(sections)
        + "</body></html>"
    )
    return weasyprint.HTML(string=html).write_pdf()
