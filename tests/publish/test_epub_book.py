import io
import uuid
import zipfile
from datetime import UTC, datetime

from lxml import etree

from jerome.core.publication import Publication
from jerome.publish.epub_book import book_identifier, write_epub_book
from jerome.publish.from_docx import docx_to_pub
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.images import make_png
from tests.publish.epubcheck import run_epubcheck

NOW = datetime(2026, 11, 24, 12, 30, tzinfo=UTC)
NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "x": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
}


def _publication(**kw) -> Publication:
    values = dict(
        title="A Book",
        language="en-US",
        authors=["Ann Author", "Bob Writer"],
        translator="Tina Translator",
        publisher="House Press",
        isbn_epub="978-3-16-148410-0",
        isbn_pdf=None,
        year=2026,
        rights="© 2026 Ann Author",
        description="A short description.",
        trim_size="6x9in",
        has_cover=True,
    )
    values.update(kw)
    return Publication(**values)


LINES = [
    "For my mother.",
    "# One",
    "Text[^1] more, with a list:",
    "- alpha",
    "- beta",
    "|cell one|cell two|",
    "![A picture](png)",
    "# ![Ornament](png)",
    "Untitled chapter text.",
    "# Three & <more>",
    "End.",
]


COVER = (make_png(1600, 2400), "image/png")


def _book(pub=None, cover=COVER, lines=LINES):
    pub_book = docx_to_pub(make_docx(lines, footnotes={1: "A *note*."}), "en-US")
    pub = pub or _publication()
    return write_epub_book(pub_book, pub, identifier=book_identifier(7, pub), cover=cover, now=NOW)


def _zip(data):
    return zipfile.ZipFile(io.BytesIO(data))


def _xml(data, name):
    return etree.fromstring(_zip(data).read(name))


def test_identifier_rule():
    assert book_identifier(7, _publication()) == "urn:isbn:9783161484100"
    plain = _publication(isbn_epub=None)
    assert book_identifier(7, plain, "urn:uuid:theirs") == "urn:uuid:theirs"
    expected = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'jerome-book-7')}"
    assert book_identifier(7, plain) == expected


def test_epub_book_structure():
    data = _book()
    zf = _zip(data)
    first = zf.infolist()[0]
    assert (first.filename, first.compress_type) == ("mimetype", zipfile.ZIP_STORED)
    assert zf.read("mimetype") == b"application/epub+zip"
    for name in zf.namelist():
        if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
            etree.fromstring(zf.read(name))  # well-formed

    opf = _xml(data, "OEBPS/content.opf")
    meta = opf.find("opf:metadata", NS)
    assert meta.findtext("dc:title", None, NS) == "A Book"
    assert [c.text for c in meta.findall("dc:creator", NS)] == ["Ann Author", "Bob Writer"]
    trl = meta.find("dc:contributor", NS)
    assert trl.text == "Tina Translator"
    role = meta.find(f"opf:meta[@refines='#{trl.get('id')}'][@property='role']", NS)
    assert role.text == "trl"
    assert meta.findtext("dc:language", None, NS) == "en-US"
    assert meta.findtext("dc:identifier", None, NS) == "urn:isbn:9783161484100"
    assert meta.findtext("dc:publisher", None, NS) == "House Press"
    assert meta.findtext("dc:date", None, NS) == "2026"
    assert meta.findtext("dc:description", None, NS) == "A short description."
    assert meta.findtext("dc:rights", None, NS) == "© 2026 Ann Author"
    modified = meta.find("opf:meta[@property='dcterms:modified']", NS)
    assert modified.text == "2026-11-24T12:30:00Z"
    cover = opf.find("opf:manifest/opf:item[@properties='cover-image']", NS)
    assert cover is not None and cover.get("media-type") == "image/png"

    manifest = {i.get("id"): i.get("href") for i in opf.iterfind("opf:manifest/opf:item", NS)}
    spine = [manifest[r.get("idref")] for r in opf.iterfind("opf:spine/opf:itemref", NS)]
    assert spine == [
        "cover.xhtml",
        "title.xhtml",
        "copyright.xhtml",
        "nav.xhtml",  # the contents page; its landmark must point into the spine (RSC-011)
        "chapter-001.xhtml",
        "chapter-002.xhtml",
        "chapter-003.xhtml",
        "chapter-004.xhtml",
    ]

    nav = _xml(data, "OEBPS/nav.xhtml")
    toc = nav.find(".//x:nav[@epub:type='toc']", NS)
    labels = ["".join(a.itertext()) for a in toc.iterfind(".//x:a", NS)]
    assert labels == ["One", "Chapter 2", "Three & <more>"]
    ncx = _xml(data, "OEBPS/toc.ncx")
    texts = [t.text for t in ncx.iterfind(".//ncx:navPoint/ncx:navLabel/ncx:text", NS)]
    assert texts == labels


def test_no_cover_no_cover_page():
    data = _book(cover=None)
    opf = _xml(data, "OEBPS/content.opf")
    assert opf.find("opf:manifest/opf:item[@properties='cover-image']", NS) is None
    assert "OEBPS/cover.xhtml" not in _zip(data).namelist()


def test_notes_are_epub3_footnotes():
    data = _book()
    chapter = _zip(data).read("OEBPS/chapter-002.xhtml").decode()
    assert '<a epub:type="noteref" href="#note-1">1</a>' in chapter
    root = etree.fromstring(chapter.encode())
    body = root.find("x:body", NS)
    aside = list(body.iter(f"{{{NS['x']}}}aside"))
    assert len(aside) == 1 and aside[0].get(f"{{{NS['epub']}}}type") == "footnote"
    assert aside[0].get("id") == "note-1"
    assert "A note." in " ".join("".join(aside[0].itertext()).split())
    assert "jr-note" not in chapter


def test_escaping():  # Review Focus 1
    title = "Tom & Jerry <Teil 2> – „Straße“"
    data = _book(_publication(title=title, authors=["Ö & Ä"]))
    opf = _xml(data, "OEBPS/content.opf")
    assert opf.findtext(".//dc:title", None, NS) == title
    assert opf.findtext(".//dc:creator", None, NS) == "Ö & Ä"
    nav = _xml(data, "OEBPS/nav.xhtml")
    assert nav.findtext("x:head/x:title", None, NS) == title
    page = _xml(data, "OEBPS/title.xhtml")
    text = " ".join("".join(page.find("x:body", NS).itertext()).split())
    assert title in text and "Ö & Ä" in text


def test_internal_links_point_into_the_right_file():
    pub_book = docx_to_pub(make_docx(["# One", "a", "# Two", "b"]), "en-US")
    pub_book.chapters[1].html = pub_book.chapters[1].html.replace(
        "<p>b</p>", '<p><a id="target"/>b <a href="#target">self</a> <a href="#gone">x</a></p>'
    )
    pub_book.chapters[0].html += '<p><a href="#target">there</a></p>'
    data = write_epub_book(pub_book, _publication(), identifier="urn:uuid:x", cover=None, now=NOW)
    one = _zip(data).read("OEBPS/chapter-001.xhtml").decode()
    two = _zip(data).read("OEBPS/chapter-002.xhtml").decode()
    assert 'href="chapter-002.xhtml#target"' in one
    assert 'href="#target"' in two and "#gone" not in two


def test_epubcheck_clean():
    errors = run_epubcheck(_book())
    assert errors == []


def test_identifier_differs_between_installations():
    from datetime import UTC, datetime

    plain = _publication(isbn_epub=None)
    here = datetime(2026, 1, 5, 10, 0, 1, 123456, tzinfo=UTC)
    there = datetime(2026, 3, 9, 8, 30, 0, 7, tzinfo=UTC)
    assert book_identifier(1, plain, created=here) == book_identifier(1, plain, created=here)
    assert book_identifier(1, plain, created=here) != book_identifier(1, plain, created=there)
    assert book_identifier(1, plain, created=here) != book_identifier(1, plain)
