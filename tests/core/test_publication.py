import pytest

from jerome.core.books import import_book
from jerome.core.publication import (
    InvalidPublication,
    delete_cover,
    get_publication,
    image_size,
    isbn13,
    set_cover,
    update_publication,
)
from jerome.core.translation import import_for_translation
from tests.fixtures.docx_builders import make_docx, package_docx
from tests.fixtures.epub_builders import make_epub
from tests.fixtures.images import GIF, make_jpeg_header, make_png


def test_isbn13():
    assert isbn13("978-3-16-148410-0") == "978-3-16-148410-0"
    assert isbn13(" 9783161484100 ") == "9783161484100"
    for bad in ("978-3-16-148410-1", "12345", "978316148410X", ""):
        with pytest.raises(InvalidPublication, match="not a valid ISBN-13"):
            isbn13(bad)


def test_image_size():
    assert image_size(make_png(1400, 2100)) == ("image/png", 1400, 2100)
    assert image_size(make_jpeg_header(1600, 2400)) == ("image/jpeg", 1600, 2400)
    assert image_size(GIF) is None
    assert image_size(make_png(1400, 2100)[:20]) is None


def test_default_rights_and_title(session, tmp_storage):
    book = import_book(
        session, tmp_storage, filename="b.docx", data=make_docx(["x"]), variant="en-GB"
    )
    pub = update_publication(
        session, book.id, {"authors": [" Ana Novak ", "Ben Roe", " "], "year": 2026}
    )
    assert pub.authors == ["Ana Novak", "Ben Roe"]
    assert pub.rights == "© 2026 Ana Novak, Ben Roe"
    assert pub.language == "en-GB" and pub.trim_size == "6x9in" and not pub.has_cover
    pub = update_publication(session, book.id, {"rights": "All rights reserved", "year": None})
    assert pub.rights == "All rights reserved" and pub.year is None
    pub = update_publication(session, book.id, {"rights": None})
    assert pub.rights == ""  # no year: no default line


def test_update_validates(session, tmp_storage):
    book = import_book(
        session, tmp_storage, filename="b.docx", data=make_docx(["x"]), variant="en-GB"
    )
    for values, message in [
        ({"isbn_pdf": "123"}, "ISBN"),
        ({"trim_size": "a4"}, "trim size"),
        ({"year": 99}, "year"),
        ({"publisher": "x" * 501}, "publisher"),
    ]:
        with pytest.raises(InvalidPublication, match=message):
            update_publication(session, book.id, values)
    pub = update_publication(session, book.id, {"publisher": "  ", "translator": " T "})
    assert pub.publisher is None and pub.translator == "T"


def test_import_prefills_from_metadata(session, tmp_storage):
    epub = make_epub(
        [("One", ["a"])], creators=["Ana Novak", "Ben Roe"], publisher="FPP", date="2019-05-01"
    )
    book = import_book(session, tmp_storage, filename="b.epub", data=epub, variant="en-GB")
    pub = get_publication(session, book.id)
    assert (pub.authors, pub.publisher, pub.year) == (["Ana Novak", "Ben Roe"], "FPP", 2019)
    docx = package_docx("<w:p><w:r><w:t>x</w:t></w:r></w:p>", creator="Ana Novak")
    book = import_book(session, tmp_storage, filename="b.docx", data=docx, variant="en-GB")
    assert get_publication(session, book.id).authors == ["Ana Novak"]
    docx = package_docx("<w:p><w:r><w:t>x</w:t></w:r></w:p>", creator="Microsoft Office User")
    book = import_book(session, tmp_storage, filename="b.docx", data=docx, variant="en-GB")
    assert get_publication(session, book.id).authors == []  # Word's placeholder is no author


def test_translation_copies_details_but_not_isbns(session, tmp_storage):
    epub = make_epub([("One", ["a"])], creators=["Ana Novak"], publisher="FPP", date="2019")
    book = import_for_translation(
        session,
        tmp_storage,
        filename="b.epub",
        data=epub,
        source_variant="en-GB",
        target_variant="de-DE",
        translated_title="Eins",
    )
    pub = get_publication(session, book.id)
    assert (pub.title, pub.language, pub.authors, pub.publisher) == (
        "Eins",
        "de-DE",
        ["Ana Novak"],
        "FPP",
    )
    assert pub.isbn_epub is None and pub.isbn_pdf is None


def test_cover_rules(session, tmp_storage):
    book = import_book(
        session, tmp_storage, filename="b.docx", data=make_docx(["x"]), variant="en-GB"
    )
    good = make_png(1400, 2100)
    assert set_cover(session, tmp_storage, book.id, good).has_cover
    for data, message in [
        (GIF, "must be a JPEG or PNG"),
        (good[:30], "must be a JPEG or PNG"),
        (make_jpeg_header(900, 1300), "1300 px on its long side; shops need at least 1400"),
    ]:
        with pytest.raises(InvalidPublication, match=message):
            set_cover(session, tmp_storage, book.id, data)
        assert tmp_storage.read_cover(book.id) == good  # the earlier cover stays
    assert get_publication(session, book.id).has_cover
    assert not delete_cover(session, tmp_storage, book.id).has_cover
    assert tmp_storage.read_cover(book.id) is None
