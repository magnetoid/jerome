import io
import uuid
import zipfile
from pathlib import Path

from lxml import etree

from jerome.core.publication import Publication
from jerome.documents.epub_reader import read_epub
from jerome.publish.epub_book import book_identifier
from jerome.publish.epub_patch import epub_identifier, patch_epub
from tests.fixtures.epub_builders import all_entries_except, make_epub, unchanged_entries
from tests.fixtures.images import make_jpeg_header, make_png
from tests.publish.epubcheck import run_epubcheck

PUBLIC = Path(__file__).resolve().parents[1] / "fixtures" / "public"
OPF = "OEBPS/content.opf"
NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}


def _publication(**kw) -> Publication:
    values = dict(
        title="Test Book",
        language="en",
        authors=["Ann Author", "Bob Writer"],
        translator="Tina Translator",
        publisher="House Press",
        isbn_epub=None,
        isbn_pdf=None,
        year=2026,
        rights="© 2026 Ann Author",
        description="About it.",
        trim_size="6x9in",
        has_cover=False,
    )
    values.update(kw)
    return Publication(**values)


def _book(**kw) -> bytes:
    return make_epub(
        [("One", ["a"])], creators=["Old Author"], publisher="Old House", date="1999", **kw
    )


def _read(data: bytes, name: str) -> bytes:
    return zipfile.ZipFile(io.BytesIO(data)).read(name)


def _opf(data: bytes) -> etree._Element:
    return etree.fromstring(_read(data, OPF))


def test_patch_sets_metadata_and_keeps_other_entries():
    original = _book()
    out = patch_epub(original, _publication(), identifier="urn:uuid:new", cover=None)
    meta = _opf(out).find("opf:metadata", NS)
    assert [c.text for c in meta.findall("dc:creator", NS)] == ["Ann Author", "Bob Writer"]
    trl = meta.find("dc:contributor", NS)
    assert trl.text == "Tina Translator"
    assert meta.find(f"opf:meta[@refines='#{trl.get('id')}']", NS).text == "trl"
    assert meta.findtext("dc:publisher", None, NS) == "House Press"
    assert meta.findtext("dc:date", None, NS) == "2026"
    assert meta.findtext("dc:description", None, NS) == "About it."
    assert meta.findtext("dc:rights", None, NS) == "© 2026 Ann Author"
    assert unchanged_entries(original, out) == all_entries_except(original, OPF)
    assert [b.text for c in read_epub(out).chapters for b in c.blocks] == ["One", "a"]


def test_patch_none_fields():
    # the import fills the publisher and year in, so None there means they were cleared;
    # the other fields the import leaves empty keep the book's own
    original = _book()
    pub = _publication(authors=[], translator=None, publisher=None, year=None, description=None)
    pub.rights = ""
    out = patch_epub(original, pub, identifier="urn:uuid:jerome-test", cover=None)
    meta = _opf(out).find("opf:metadata", NS)
    assert [c.text for c in meta.findall("dc:creator", NS)] == ["Old Author"]
    assert meta.find("dc:publisher", NS) is None
    assert meta.find("dc:date", NS) is None
    assert meta.find("dc:contributor", NS) is None
    assert meta.find("dc:rights", NS) is None


def test_patch_replaces_cover_of_the_same_type():
    original = _book(cover=make_png(1400, 2000))
    new = make_png(1600, 2400, (0, 0, 255))
    out = patch_epub(original, _publication(), identifier="urn:uuid:x", cover=(new, "image/png"))
    assert _read(out, "OEBPS/images/cover.png") == new
    items = _opf(out).findall(".//opf:item[@properties='cover-image']", NS)
    assert [i.get("href") for i in items] == ["images/cover.png"]
    assert unchanged_entries(original, out) == all_entries_except(original, OPF) - {
        "OEBPS/images/cover.png"
    }


def test_patch_adds_cover_of_another_type():
    original = _book(cover=make_png(1400, 2000))
    jpeg = make_jpeg_header(1600, 2400)
    out = patch_epub(original, _publication(), identifier="urn:uuid:x", cover=(jpeg, "image/jpeg"))
    opf = _opf(out)
    [item] = opf.findall(".//opf:item[@properties='cover-image']", NS)
    assert item.get("media-type") == "image/jpeg"
    path = "OEBPS/" + item.get("href")
    assert _read(out, path) == jpeg
    assert opf.find(".//opf:meta[@name='cover']", NS).get("content") == item.get("id")
    assert item.get("href").encode() in _read(out, "OEBPS/cover.xhtml")  # the page shows it


def test_patch_adds_cover_to_a_book_without_one():
    out = patch_epub(
        _book(), _publication(), identifier="urn:uuid:x", cover=(make_png(1400, 2000), "image/png")
    )
    opf = _opf(out)
    [item] = opf.findall(".//opf:item[@properties='cover-image']", NS)
    assert opf.find(".//opf:meta[@name='cover']", NS).get("content") == item.get("id")
    assert _read(out, "OEBPS/" + item.get("href")).startswith(b"\x89PNG")


def test_patch_identifier_rules():
    original = _book()
    assert epub_identifier(original) == "urn:uuid:jerome-test"
    isbn = _publication(isbn_epub="978-3-16-148410-0")
    out = patch_epub(
        original, isbn, identifier=book_identifier(7, isbn, epub_identifier(original)), cover=None
    )
    assert epub_identifier(out) == "urn:isbn:9783161484100"
    plain = _publication()
    kept = book_identifier(7, plain, epub_identifier(original))
    assert epub_identifier(patch_epub(original, plain, identifier=kept, cover=None)) == (
        "urn:uuid:jerome-test"
    )
    translated = book_identifier(8, plain)  # the caller passes no existing identifier
    out = patch_epub(original, plain, identifier=translated, cover=None)
    assert epub_identifier(out) == f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'jerome-book-8')}"
    ident = _opf(out).find("opf:metadata/dc:identifier", NS)
    assert ident.get("id") == _opf(out).get("unique-identifier")


def test_patch_adds_no_epubcheck_errors():
    original = (PUBLIC / "pg932.epub").read_bytes()
    before = set(run_epubcheck(original))
    out = patch_epub(
        original, _publication(), identifier="urn:uuid:x", cover=(make_png(1600, 2400), "image/png")
    )
    assert set(run_epubcheck(out)) <= before


def test_new_identifier_reaches_the_ncx():
    # EPUBCheck NCX-001: the NCX's dtb:uid must match the OPF's identifier
    original = (PUBLIC / "pg932.epub").read_bytes()
    out = patch_epub(original, _publication(), identifier="urn:uuid:x", cover=None)
    ncx = etree.fromstring(zipfile.ZipFile(io.BytesIO(out)).read("OEBPS/toc.ncx"))
    ns = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}
    [uid] = ncx.findall("ncx:head/ncx:meta[@name='dtb:uid']", ns)
    assert uid.get("content") == "urn:uuid:x"
