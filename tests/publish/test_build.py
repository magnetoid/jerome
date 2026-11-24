"""Preparing and making a finished file: guards, warnings, the image limit, and which writer
each kind of book gets."""

import io
import zipfile
from pathlib import Path

import pytest
from lxml import etree
from sqlalchemy import select

from jerome.core.books import import_book
from jerome.core.exports import EPUB_MEDIA_TYPE
from jerome.core.publication import set_cover, update_publication
from jerome.errors import Conflict
from jerome.orm import Block, Book, Chapter
from jerome.publish import build
from jerome.publish.build import prepare, render
from jerome.publish.model import PublishError
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.images import make_jpeg, make_png
from tests.fixtures.suggestions import add_one

PUBLIC = Path(__file__).resolve().parents[1] / "fixtures" / "public"
NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}


def _docx_book(session, storage, lines=None, *, authors=("Ann Author",)) -> Book:
    book = import_book(
        session,
        storage,
        filename="novel.docx",
        data=make_docx(lines or ["# One", "The first paragraph.", "# Two", "The second one."]),
        variant="en-US",
    )
    update_publication(session, book.id, {"authors": list(authors)})  # [] clears the import's
    return book


def _opf(epub: bytes) -> etree._Element:
    with zipfile.ZipFile(io.BytesIO(epub)) as z:
        container = etree.fromstring(z.read("META-INF/container.xml"))
        path = container.find(".//{*}rootfile").get("full-path")
        return etree.fromstring(z.read(path))


def test_word_book_epub_is_written_by_jerome(session, tmp_storage):
    book = _docx_book(session, tmp_storage)
    prepared = prepare(session, tmp_storage, book.id, "epub-book")
    assert prepared.filename_stem == "novel" and prepared.pub_book is not None
    assert "The book has no cover" in prepared.warnings
    data, media_type = render(prepared)
    assert media_type == EPUB_MEDIA_TYPE
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert "OEBPS/chapter-002.xhtml" in z.namelist()
    opf = _opf(data)
    assert opf.findtext(".//dc:creator", namespaces=NS) == "Ann Author"


def test_no_author_is_refused(session, tmp_storage):
    book = _docx_book(session, tmp_storage, authors=())
    with pytest.raises(Conflict, match="Set the author under Publication first"):
        prepare(session, tmp_storage, book.id, "epub-book")


def test_proofread_book_with_open_suggestion_warns_not_refuses(session, tmp_storage):
    book = _docx_book(session, tmp_storage, ["# One", "Teh first paragraph."])
    block = session.scalars(
        select(Block).where(Block.book_id == book.id, Block.text == "Teh first paragraph.")
    ).one()
    add_one(session, block, 0, 3, "Teh", "The")
    chapter = session.scalars(select(Chapter).where(Chapter.book_id == book.id)).first()
    chapter.proofread = False
    session.flush()
    prepared = prepare(session, tmp_storage, book.id, "epub-book")
    assert "1 suggestions are still open" in prepared.warnings
    assert "1 chapters are not proofread" in prepared.warnings
    data, _ = render(prepared)
    assert data[:2] == b"PK"


def test_left_out_word_contents_is_a_warning(session, tmp_storage):
    book = _docx_book(session, tmp_storage, ["[TOC: One ... 1]", "# One", "Text here."])
    prepared = prepare(session, tmp_storage, book.id, "epub-book")
    assert any("Word table of contents" in w for w in prepared.warnings)


def test_image_limit(session, tmp_storage, monkeypatch):
    book = _docx_book(session, tmp_storage)
    set_cover(session, tmp_storage, book.id, make_png(1400, 2100))
    monkeypatch.setattr(build, "IMAGE_LIMIT", 1024)
    with pytest.raises(PublishError, match="MB of images; the limit is"):
        prepare(session, tmp_storage, book.id, "epub-book")


def test_image_limit_counts_a_kept_epubs_own_images(session, tmp_storage, monkeypatch):
    # an EPUB-born book's EPUB keeps its files: their images count too (pg932: a 192 KB cover)
    book = import_book(
        session,
        tmp_storage,
        filename="usher.epub",
        data=(PUBLIC / "pg932.epub").read_bytes(),
        variant="en-US",
    )
    monkeypatch.setattr(build, "IMAGE_LIMIT", 100_000)
    with pytest.raises(PublishError, match="MB of images; the limit is"):
        prepare(session, tmp_storage, book.id, "epub-book")
    monkeypatch.setattr(build, "IMAGE_LIMIT", 150_000)  # 192 KB + 13 KB would not fit
    set_cover(session, tmp_storage, book.id, make_png(1400, 2100))  # added: the JPEG stays
    with pytest.raises(PublishError, match="MB of images; the limit is"):
        prepare(session, tmp_storage, book.id, "epub-book")
    set_cover(session, tmp_storage, book.id, make_jpeg(1400, 2100))  # replaces the 192 KB one
    assert prepare(session, tmp_storage, book.id, "epub-book").pub_book is None


def test_cleared_year_warns_that_the_epubs_own_date_goes(session, tmp_storage):
    book = import_book(
        session,
        tmp_storage,
        filename="usher.epub",
        data=(PUBLIC / "pg932.epub").read_bytes(),
        variant="en-US",
    )
    assert book.year == 1997  # from its dc:date, 1997-06-01
    assert not [w for w in prepare(session, tmp_storage, book.id, "epub-book").warnings
                if "left out" in w]  # fmt: skip
    update_publication(session, book.id, {"year": None})
    prepared = prepare(session, tmp_storage, book.id, "epub-book")
    assert "The EPUB's own date (1997-06-01) is left out: no year is set under Publication" in (
        prepared.warnings
    )


def test_epub_born_book_epub_book_is_patched_clean_export(session, tmp_storage):
    book = import_book(
        session,
        tmp_storage,
        filename="usher.epub",
        data=(PUBLIC / "pg932.epub").read_bytes(),
        variant="en-US",
    )
    update_publication(session, book.id, {"publisher": "House Press"})
    prepared = prepare(session, tmp_storage, book.id, "epub-book")
    assert prepared.pub_book is None  # the publisher's own files are kept
    data, media_type = render(prepared)
    assert media_type == EPUB_MEDIA_TYPE
    opf = _opf(data)
    assert opf.findtext(".//dc:publisher", namespaces=NS) == "House Press"
    # the proofread EPUB keeps its own identifier
    assert prepared.identifier == build.epub_identifier(prepared.clean)
    clean = zipfile.ZipFile(io.BytesIO(prepared.clean))
    done = zipfile.ZipFile(io.BytesIO(data))
    opf_path = next(n for n in clean.namelist() if n.endswith(".opf"))
    for name in clean.namelist():
        if name != opf_path:
            assert done.read(name) == clean.read(name), name


def test_epub_born_book_pdf_is_made_from_its_chapters(session, tmp_storage):
    book = import_book(
        session,
        tmp_storage,
        filename="usher.epub",
        data=(PUBLIC / "pg932.epub").read_bytes(),
        variant="en-US",
    )
    prepared = prepare(session, tmp_storage, book.id, "pdf-book")
    assert prepared.pub_book is not None and prepared.pub_book.chapters
    assert any(w.startswith("Left out of the finished file") for w in prepared.warnings)


def test_text_difference_is_refused(session, tmp_storage, monkeypatch):
    book = _docx_book(session, tmp_storage)

    def differ(*_a, **_kw):
        raise build.PublishTextError("The finished file's text differs")

    monkeypatch.setattr(build, "check_text", differ)
    with pytest.raises(PublishError, match="differs"):
        prepare(session, tmp_storage, book.id, "epub-book")


def test_pdf_book(session, tmp_storage, weasy):
    from pypdf import PdfReader

    book = _docx_book(session, tmp_storage)
    prepared = prepare(session, tmp_storage, book.id, "pdf-book")
    data, media_type = render(prepared)
    assert media_type == "application/pdf"
    assert PdfReader(io.BytesIO(data)).metadata.title == prepared.pub.title


def test_epub_born_book_keeps_its_own_rights_line(session, tmp_storage):
    book = import_book(
        session,
        tmp_storage,
        filename="usher.epub",
        data=(PUBLIC / "pg932.epub").read_bytes(),
        variant="en-US",
    )
    assert book.rights == "Public domain in the USA."  # prefilled from dc:rights
    book.rights = None  # a book imported before the prefill: no computed © line either
    session.flush()
    prepared = prepare(session, tmp_storage, book.id, "epub-book")
    assert prepared.pub.rights == "Public domain in the USA."
    data, _ = render(prepared)
    assert _opf(data).findtext(".//dc:rights", namespaces=NS) == "Public domain in the USA."
    assert prepare(session, tmp_storage, book.id, "pdf-book").pub.rights == (
        "Public domain in the USA."
    )


def test_word_book_identifier_uses_the_book_row(session, tmp_storage):
    book = _docx_book(session, tmp_storage)
    first = prepare(session, tmp_storage, book.id, "epub-book").identifier
    assert first == prepare(session, tmp_storage, book.id, "epub-book").identifier
    assert first == build.book_identifier(book.id, build.publication_of(session, book),
                                          created=book.created_at)  # fmt: skip


def _which(found: dict[str, str]):
    return lambda name: found.get(name)


def test_epubcheck_command_runs_a_jar_through_java(tmp_path, monkeypatch):
    # Debian's /usr/bin/epubcheck is a link to a .jar, which the kernel cannot run itself
    jar = tmp_path / "epubcheck.jar"
    jar.write_bytes(b"PK")
    link = tmp_path / "epubcheck"
    link.symlink_to(jar)
    found = {"epubcheck": str(link), "java": "/usr/bin/java"}
    monkeypatch.setattr(build.shutil, "which", _which(found))
    assert build.epubcheck_command() == ["/usr/bin/java", "-jar", str(jar.resolve())]

    del found["java"]
    assert build.epubcheck_command() is None  # a jar without Java cannot run: not installed


def test_epubcheck_command_runs_a_script_as_it_is(tmp_path, monkeypatch):
    script = tmp_path / "epubcheck"
    script.write_text("#!/bin/sh\n")
    monkeypatch.setattr(build.shutil, "which", _which({"epubcheck": str(script)}))
    assert build.epubcheck_command() == [str(script)]
    monkeypatch.setattr(build.shutil, "which", _which({}))
    assert build.epubcheck_command() is None
