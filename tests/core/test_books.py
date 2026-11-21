import time
import zipfile
from datetime import UTC, datetime
from io import BytesIO

import pytest

from jerome.core.books import (
    block_texts,
    delete_book,
    get_book,
    import_book,
    list_books,
    list_chapters,
    set_chapter_proofread,
)
from jerome.core.style_sheets import get_style_sheet, update_style_sheet
from jerome.documents.docx_reader import read_docx
from jerome.documents.docx_writer import DocxWriteOptions, write_docx
from jerome.documents.epub_reader import read_epub
from jerome.errors import NotFound, UnsupportedDocumentError
from jerome.orm import Block, Book, Chapter, Segment
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.epub_builders import make_epub

NOW = datetime(2026, 11, 21, tzinfo=UTC)


def test_import_docx_creates_rows(session, tmp_storage):
    book = import_book(
        session,
        tmp_storage,
        filename="m.docx",
        data=make_docx(["# One", "Das ist gut. Es geht los."]),
        variant="en-GB",
    )
    assert [c.title for c in list_chapters(session, book.id)] == ["One"]
    assert session.query(Segment).count() == 3  # heading = 1, paragraph = 2 sentences


def test_original_stored_under_data_dir(session, tmp_storage):
    book = import_book(
        session, tmp_storage, filename="m.epub", data=make_epub([("C", ["x"])]), variant="de-DE"
    )
    assert tmp_storage.read_original(book.id).startswith(b"PK")
    assert (tmp_storage.root / "books" / str(book.id) / "original.epub").is_file()


def test_rejects_unknown_format(session, tmp_storage):
    with pytest.raises(UnsupportedDocumentError):
        import_book(session, tmp_storage, filename="m.pdf", data=b"%PDF-1.7", variant="en-US")


def test_rejects_extension_content_mismatch(session, tmp_storage):
    epub = make_epub([("C", ["x"])])
    with pytest.raises(UnsupportedDocumentError, match="Only DOCX and EPUB"):
        import_book(session, tmp_storage, filename="m.docx", data=epub, variant="en-US")
    with pytest.raises(UnsupportedDocumentError):
        import_book(session, tmp_storage, filename="m.epub", data=make_docx(["x"]), variant="en-US")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(UnsupportedDocumentError):
        import_book(session, tmp_storage, filename="m.docx", data=buf.getvalue(), variant="en-US")
    assert session.query(Book).count() == 0


def test_style_sheet_defaults_per_variant(session, tmp_storage):
    book = import_book(
        session, tmp_storage, filename="m.docx", data=make_docx(["x"]), variant="de-CH"
    )
    sheet = get_style_sheet(session, book.id)
    assert sheet.quote_style == "«»‹›"
    assert sheet.house_spellings == {}
    assert sheet.ignore_words == []


def test_update_style_sheet(session, imported_docx_book):
    s = update_style_sheet(session, imported_docx_book.id, ignore_words=["Katharina"])
    assert s.ignore_words == ["Katharina"]
    assert get_style_sheet(session, imported_docx_book.id).ignore_words == ["Katharina"]


def test_update_style_sheet_changes_only_given_fields(session, imported_docx_book):
    before = get_style_sheet(session, imported_docx_book.id)
    s = update_style_sheet(session, imported_docx_book.id, variant="de-DE")
    assert s.variant == "de-DE"
    assert s.quote_style == before.quote_style  # not reset by a variant change
    s = update_style_sheet(session, imported_docx_book.id, house_spellings={"colour": ["color"]})
    assert s.house_spellings == {"colour": ["color"]}
    assert s.variant == "de-DE"


def test_update_style_sheet_rejects_unknown_variant(session, imported_docx_book):
    with pytest.raises(ValueError):
        update_style_sheet(session, imported_docx_book.id, variant="fr-FR")  # type: ignore[arg-type]


def test_style_sheet_missing_book(session):
    with pytest.raises(NotFound):
        get_style_sheet(session, 999_999)


def test_delete_book_removes_rows_and_files(session, tmp_storage, imported_docx_book):
    book_id = imported_docx_book.id
    tmp_storage.save_export(book_id, "out.docx", b"x")
    delete_book(session, tmp_storage, book_id)
    assert session.get(Book, book_id) is None
    assert session.query(Block).filter_by(book_id=book_id).count() == 0
    assert session.query(Chapter).filter_by(book_id=book_id).count() == 0
    assert not (tmp_storage.root / "books" / str(book_id)).exists()


def test_delete_missing_book(session, tmp_storage):
    with pytest.raises(NotFound):
        delete_book(session, tmp_storage, 999_999)


def test_storage_delete_missing_is_noop_and_read_missing_raises(tmp_storage):
    tmp_storage.delete_book(42)
    with pytest.raises(NotFound):
        tmp_storage.read_original(42)


def test_get_and_list_books(session, tmp_storage):
    a = import_book(session, tmp_storage, filename="a.docx", data=make_docx(["x"]), variant="en-US")
    b = import_book(session, tmp_storage, filename="b.docx", data=make_docx(["y"]), variant="en-US")
    assert get_book(session, a.id) is a
    assert [x.id for x in list_books(session)] == [a.id, b.id]
    with pytest.raises(NotFound):
        get_book(session, 999_999)
    with pytest.raises(NotFound):
        list_chapters(session, 999_999)


def test_title_precedence_and_filename(session, tmp_storage):
    data = make_epub([("C", ["x"])], title="Parsed Title")
    given = import_book(
        session, tmp_storage, filename="f.epub", data=data, variant="en-US", title="Given"
    )
    parsed = import_book(session, tmp_storage, filename="f.epub", data=data, variant="en-US")
    assert (given.title, parsed.title) == ("Given", "Parsed Title")
    assert parsed.original_filename == "f.epub"
    assert parsed.format == "epub"
    stem = import_book(
        session, tmp_storage, filename="My Novel.docx", data=make_docx(["x"]), variant="en-US"
    )
    assert read_docx(make_docx(["x"])).title is None
    assert stem.title == "My Novel"


def test_local_only_stored(session, tmp_storage):
    book = import_book(
        session,
        tmp_storage,
        filename="a.docx",
        data=make_docx(["x"]),
        variant="en-US",
        local_only=True,
    )
    assert book.local_only is True


def test_epub_non_proofread_chapters_stored(session, tmp_storage):
    data = make_epub([("One", ["a"])], front_matter=True)
    book = import_book(session, tmp_storage, filename="m.epub", data=data, variant="en-US")
    assert [c.proofread for c in list_chapters(session, book.id)] == [False, True]


def test_source_refs_match_parsed_keys(session, tmp_storage):
    for name, data in (
        ("m.docx", make_docx(["# One", "a b.", "# Two", "c d."])),
        ("m.epub", make_epub([("One", ["a"]), ("Two", ["b", "c"])])),
    ):
        parsed = read_docx(data) if name.endswith("docx") else read_epub(data)
        book = import_book(session, tmp_storage, filename=name, data=data, variant="en-US")
        chapters = list_chapters(session, book.id)
        assert [c.source_ref for c in chapters] == [c.source_ref for c in parsed.chapters]
        for chapter, pc in zip(chapters, parsed.chapters, strict=True):
            assert [b.source_ref for b in chapter.blocks] == [b.key for b in pc.blocks]
            assert [b.kind for b in chapter.blocks] == [b.kind.value for b in pc.blocks]
            assert [b.ordinal for b in chapter.blocks] == list(range(len(pc.blocks)))
            assert [b.hash for b in chapter.blocks] == [b.hash for b in pc.blocks]
            assert all(b.book_id == book.id for b in chapter.blocks)


def test_marks_stored_as_json(session, tmp_storage):
    data = make_docx(["Some **bold** text and *it*."])
    parsed = read_docx(data)
    book = import_book(session, tmp_storage, filename="m.docx", data=data, variant="en-US")
    stored = list_chapters(session, book.id)[0].blocks[0]
    assert stored.marks, "fixture should produce marks"
    assert stored.marks == [
        {"start": m.start, "end": m.end, "kind": m.kind.value, "href": m.href}
        for m in parsed.chapters[0].blocks[0].marks
    ]


def test_segments_use_variant_language_and_offsets(session, tmp_storage):
    book = import_book(
        session,
        tmp_storage,
        filename="m.docx",
        data=make_docx(["# Head. Ing", "Das ist gut. Es geht los."]),
        variant="de-DE",
    )
    heading, para = list_chapters(session, book.id)[0].blocks
    assert [(s.ordinal, s.start, s.end) for s in heading.segments] == [(0, 0, len(heading.text))]
    assert [(s.ordinal, s.start, s.end) for s in para.segments] == [(0, 0, 12), (1, 13, 25)]


def test_set_chapter_proofread(session, imported_docx_book):
    chapter = list_chapters(session, imported_docx_book.id)[0]
    assert chapter.proofread is True
    assert set_chapter_proofread(session, chapter.id, False).proofread is False
    assert list_chapters(session, imported_docx_book.id)[0].proofread is False
    with pytest.raises(NotFound):
        set_chapter_proofread(session, 999_999, True)


def test_block_texts_order_and_chapter_filter(session, tmp_storage):
    data = make_docx(["# One", "a1.", "a2.", "# Two", "b1.", "b2."])
    book = import_book(session, tmp_storage, filename="m.docx", data=data, variant="en-US")
    one, two = list_chapters(session, book.id)
    assert [b.text for b in block_texts(session, book.id)] == [
        "One",
        "a1.",
        "a2.",
        "Two",
        "b1.",
        "b2.",
    ]
    assert [b.text for b in block_texts(session, book.id, two.id)] == ["Two", "b1.", "b2."]
    assert [b.id for b in block_texts(session, book.id, one.id)] == [b.id for b in one.blocks]
    set_chapter_proofread(session, one.id, False)  # not filtered by proofread
    assert len(block_texts(session, book.id)) == 6


@pytest.mark.slow
def test_large_book_import_and_export_under_20s(session, tmp_storage):
    data = make_docx(
        [f"# Chapter {i}" if i % 100 == 0 else "Sentence one. Sentence two." for i in range(3000)]
    )
    t = time.monotonic()
    book = import_book(session, tmp_storage, filename="big.docx", data=data, variant="en-US")
    write_docx(tmp_storage.read_original(book.id), {}, DocxWriteOptions("clean", NOW))
    elapsed = time.monotonic() - t
    print(f"large book: {elapsed:.2f}s")
    assert elapsed < 20
    assert len(block_texts(session, book.id)) == 3000


@pytest.mark.slow
def test_multi_batch_import_maps_blocks_and_segments_correctly(session, tmp_storage):
    lines = [
        f"# Chapter {i}" if i % 100 == 0 else f"Para {i} one. Para {i} two." for i in range(2500)
    ]
    data = make_docx(lines)
    parsed = read_docx(data)
    book = import_book(session, tmp_storage, filename="big.docx", data=data, variant="en-US")
    chapters = list_chapters(session, book.id)
    assert len(chapters) == len(parsed.chapters) == 25
    for chapter, pc in zip(chapters, parsed.chapters, strict=True):
        assert chapter.title == pc.title
        assert [b.text for b in chapter.blocks] == [b.text for b in pc.blocks]
        for block in chapter.blocks:
            assert block.segments
            for seg in block.segments:
                assert 0 <= seg.start < seg.end <= len(block.text)
            if block.kind != "heading":
                n = block.text.split()[1]
                assert all(
                    block.text[s.start : s.end].startswith(f"Para {n}") for s in block.segments
                )
