import pytest
from sqlalchemy import select

from jerome.core.books import (
    block_texts,
    delete_book,
    get_book,
    import_book,
    list_books,
    set_chapter_proofread,
)
from jerome.core.style_sheets import get_style_sheet
from jerome.core.translation import (
    ensure_not_source,
    import_for_translation,
    source_of,
    translation_counts,
)
from jerome.errors import Conflict, NotFound, UnsupportedLanguagePair
from jerome.orm import Block, Book, Chapter
from tests.fixtures.docx_builders import make_docx

DOCX = make_docx(["# One", "Hello **bold** world.[^1]", "Second."], footnotes={1: "A note."})


def all_blocks_of(book):
    s = book  # blocks of a book in reading order
    from sqlalchemy.orm import object_session

    return list(
        object_session(s).scalars(
            select(Block)
            .join(Chapter, Block.chapter_id == Chapter.id)
            .where(Block.book_id == s.id)
            .order_by(Chapter.ordinal, Block.ordinal)
        )
    )


def _import(session, storage, **kw):
    return import_for_translation(
        session,
        storage,
        filename="b.docx",
        data=DOCX,
        source_variant="en-GB",
        target_variant="de-DE",
        **kw,
    )


def test_import_creates_hidden_source_and_pending_translated_book(session, tmp_storage):
    t = _import(session, tmp_storage)
    src = source_of(session, t)
    assert src.is_source and not t.is_source and t.work == "translate"
    assert get_style_sheet(session, t.id).variant == "de-DE"
    assert get_style_sheet(session, t.id).quote_style == "„“‚‘"
    s_blocks, t_blocks = all_blocks_of(src), all_blocks_of(t)
    assert [b.source_ref for b in t_blocks] == [b.source_ref for b in s_blocks]
    assert [b.source_block_id for b in t_blocks] == [b.id for b in s_blocks]
    assert {b.translation_status for b in t_blocks} == {"pending"}
    assert all(b.text == "" and b.marks == [] for b in t_blocks)
    assert [b.id for b in list_books(session)] == [t.id]
    assert translation_counts(session, t.id) == {"pending": len(t_blocks)}
    assert tmp_storage.read_original(src.id)
    with pytest.raises(NotFound):
        tmp_storage.read_original(t.id)


def test_same_language_pair_is_refused(session, tmp_storage):
    with pytest.raises(UnsupportedLanguagePair):
        import_for_translation(
            session,
            tmp_storage,
            filename="b.docx",
            data=DOCX,
            source_variant="en-GB",
            target_variant="en-US",
        )


def test_translated_title_is_used_as_book_title(session, tmp_storage):
    t = _import(session, tmp_storage, translated_title="Hallo")
    assert t.title == "Hallo" and t.translated_title == "Hallo"


def test_deleting_translated_book_deletes_source(session, tmp_storage):
    t = _import(session, tmp_storage)
    sid = source_of(session, t).id
    delete_book(session, tmp_storage, t.id)
    assert session.get(Book, sid) is None and session.get(Book, t.id) is None
    assert not tmp_storage._book_dir(sid).exists()


def test_deleting_source_book_is_refused(session, tmp_storage):
    t = _import(session, tmp_storage)
    src = source_of(session, t)
    with pytest.raises(Conflict, match=f"work on book {t.id}"):
        delete_book(session, tmp_storage, src.id)
    with pytest.raises(Conflict):
        ensure_not_source(session, src.id)
    ch = session.scalars(select(Chapter).where(Chapter.book_id == src.id)).first()
    with pytest.raises(Conflict):
        set_chapter_proofread(session, ch.id, True)
    ensure_not_source(session, 10**9)  # a missing book is the caller's NotFound, not ours


def test_proofreadable_only_skips_untranslated_blocks(session, tmp_storage):
    t = _import(session, tmp_storage)
    blocks = all_blocks_of(t)
    blocks[0].text, blocks[0].translation_status = "Eins", "done"
    session.flush()
    assert len(block_texts(session, t.id)) == len(blocks)
    only = block_texts(session, t.id, proofreadable_only=True)
    assert [b.id for b in only] == [blocks[0].id]


def test_proofread_only_import_is_unchanged(session, tmp_storage):
    b = import_book(session, tmp_storage, filename="b.docx", data=DOCX, variant="en-GB")
    assert b.work == "proofread" and b.source_book_id is None and not b.is_source
    assert source_of(session, get_book(session, b.id)) is None
