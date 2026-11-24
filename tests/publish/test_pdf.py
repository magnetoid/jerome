import io

import pytest

from jerome.core.publication import Publication
from jerome.publish.from_docx import docx_to_pub
from jerome.publish.model import PublishError
from jerome.publish.pdf import local_fetcher, pdf_html, render_pdf
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.images import make_png


def _publication(**kw) -> Publication:
    values = dict(
        title="Tom & Jerry <Teil 2> – „Straße“",
        language="en-US",
        authors=["Ö & Ä", "Bob Writer"],
        translator="Tina Translator",
        publisher="House Press",
        isbn_epub=None,
        isbn_pdf="978-3-16-148410-0",
        year=2026,
        rights="© 2026 Ö & Ä",
        description=None,
        trim_size="6x9in",
        has_cover=False,
    )
    values.update(kw)
    return Publication(**values)


LINES = [
    "# One",
    "Text[^1] more words in the first chapter.",
    "![A picture](png)",
    "# ![Ornament](png)",
    "Second chapter words.",
    "# Three",
    "Third chapter words.",
]


def _pub_book(lines=LINES, language="en-US"):
    return docx_to_pub(make_docx(lines, footnotes={1: "Note *it*"}), language)


def _pdf(pub=None, pub_book=None, cover=None):
    from pypdf import PdfReader

    data = render_pdf(pub_book or _pub_book(), pub or _publication(), cover)
    assert data.startswith(b"%PDF-")
    return PdfReader(io.BytesIO(data))


def _pages(reader) -> list[str]:
    return [" ".join((page.extract_text() or "").split()) for page in reader.pages]


def test_html_holds_front_pages_and_notes():  # no WeasyPrint needed
    html = pdf_html(_pub_book(), _publication(), (make_png(1600, 2400), "image/png"))
    assert '<html lang="en-US">' in html
    assert "<title>Tom &amp; Jerry &lt;Teil 2&gt; – „Straße“</title>" in html
    assert 'class="cover"' in html and 'src="images/cover.png"' in html
    assert "ISBN 978-3-16-148410-0" in html and "Translated by Tina Translator" in html
    assert html.index('class="titlepage"') < html.index('class="copyright"')
    assert html.index('class="copyright"') < html.index('class="contents"')
    assert html.index('class="contents"') < html.index('id="ch-1"')
    assert 'href="#ch-2">Chapter 2</a>' in html
    assert '<span class="footnote">Note <em>it</em></span>' in html
    assert "target-counter(attr(href), page)" in html


@pytest.mark.parametrize(("trim", "size"), [("6x9in", (432, 648)), ("a5", (419.5, 595.3))])
def test_page_size_per_trim(weasy, trim, size):
    page = _pdf(_publication(trim_size=trim)).pages[1]
    assert float(page.mediabox.width) == pytest.approx(size[0], abs=0.5)
    assert float(page.mediabox.height) == pytest.approx(size[1], abs=0.5)


def test_text_order_and_front_pages(weasy):
    text = " | ".join(_pages(_pdf()))
    title = text.index("Tom & Jerry <Teil 2> – „Straße“")
    isbn = text.index("ISBN 978-3-16-148410-0")
    contents = text.index("Contents")
    first = text.index("Text more words")
    assert title < isbn < contents < first
    assert text.index("Second chapter words") < text.index("Third chapter words")
    toc = text[contents:first]
    assert "One" in toc and "Chapter 2" in toc and "Three" in toc
    assert any(ch.isdigit() for ch in toc)


def test_fonts_embedded(weasy):
    reader = _pdf()
    names = set()
    for page in reader.pages:
        fonts = page["/Resources"].get("/Font", {})
        for ref in fonts.values():
            font = ref.get_object()
            descriptor = font.get("/FontDescriptor")
            if descriptor is None:  # a Type 0 font: the descendant has it
                descriptor = font["/DescendantFonts"][0].get_object()["/FontDescriptor"]
            descriptor = descriptor.get_object()
            assert "/FontFile2" in descriptor or "/FontFile3" in descriptor
            names.add(str(font["/BaseFont"]))
    assert names and all("EBGaramond" in n for n in names), names


def test_bookmarks_and_metadata(weasy):
    reader = _pdf()
    assert [item.title for item in reader.outline] == ["One", "Chapter 2", "Three"]
    assert reader.metadata.title == "Tom & Jerry <Teil 2> – „Straße“"
    assert reader.metadata.author == "Ö & Ä, Bob Writer"


def test_footnote_on_reference_page(weasy):
    pages = _pages(_pdf())
    [page] = [p for p in pages if "Text" in p and "more words" in p]
    assert "Note it" in page


def test_cover_page_first(weasy):
    reader = _pdf(cover=(make_png(1600, 2400), "image/png"))
    assert "/XObject" in reader.pages[0]["/Resources"]
    assert not (reader.pages[0].extract_text() or "").strip()


def test_german_hyphenation(weasy):
    word = "Donaudampfschifffahrtsgesellschaftskapitänsmütze"
    lines = ["# Eins", " ".join(f"Die {word} und das Wort" for _ in range(40))]
    reader = _pdf(_publication(language="de-DE", trim_size="5x8in"), _pub_book(lines, "de-DE"))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert any(line.rstrip().endswith("-") for line in text.splitlines())


def test_url_fetcher_refuses_outside_files(weasy, tmp_path, respx_mock):
    (tmp_path / "ok.png").write_bytes(make_png(4, 4))
    fetcher = local_fetcher(tmp_path)
    html = (
        '<p><img src="ok.png"/><img src="file:///etc/hosts"/>'
        '<img src="https://example.com/x.png"/><img src="../outside.png"/></p>'
    )
    pdf = weasy.HTML(string=html, base_url=tmp_path.as_uri() + "/", url_fetcher=fetcher)
    assert pdf.write_pdf().startswith(b"%PDF-")
    assert "file:///etc/hosts" in fetcher.refused
    assert "https://example.com/x.png" in fetcher.refused
    assert any(url.endswith("/outside.png") for url in fetcher.refused)
    assert not any(url.endswith("/ok.png") for url in fetcher.refused)
    assert not respx_mock.calls


def test_timeout_raises():  # killed before WeasyPrint loads, so it runs everywhere
    with pytest.raises(PublishError, match="15 minutes"):
        render_pdf(_pub_book(), _publication(), None, timeout=0.001)
