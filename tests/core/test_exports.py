from datetime import UTC, datetime

import pytest

from jerome.core.exports import export_book, recorded_changes
from jerome.core.suggestions import decide
from jerome.documents.changes import Change
from jerome.documents.epub_reader import read_epub
from jerome.errors import UnsupportedDocumentError
from jerome.orm import Block
from tests.fixtures.docx_builders import all_blocks, part_xml
from tests.fixtures.suggestions import add_one

NOW = datetime(2026, 11, 21, tzinfo=UTC)


def test_docx_clean_applies_only_recorded(session, tmp_storage, docx_book_with_decisions):
    f = export_book(session, tmp_storage, docx_book_with_decisions.id, "docx-clean")
    texts = [b.text for b in all_blocks(f.data)]
    assert "the cat sat down" in texts and "an open suggestion stays teh" in texts


def test_docx_tracked_has_revisions_for_recorded(session, tmp_storage, docx_book_with_decisions):
    f = export_book(session, tmp_storage, docx_book_with_decisions.id, "docx-tracked", now=NOW)
    doc = part_xml(f.data, "word/document.xml")
    assert doc.count("<w:ins ") == 2
    assert 'w:author="Jerome"' in doc and "<w:comment" not in doc


def test_epub_clean(session, tmp_storage, epub_book_with_decisions):
    f = export_book(session, tmp_storage, epub_book_with_decisions.id, "epub-clean")
    texts = [b.text for c in read_epub(f.data).chapters for b in c.blocks]
    assert "the cat sat" in texts and "an open suggestion stays teh" in texts
    assert f.filename == "novel-epub-clean.epub" and f.media_type == "application/epub+zip"


def test_recorded_changes_keys_refs_and_order(session, tmp_storage, docx_book_with_decisions):
    blocks = session.query(Block).filter_by(book_id=docx_book_with_decisions.id).all()
    first = min(blocks, key=lambda b: b.id)
    got = recorded_changes(session, docx_book_with_decisions.id)
    assert list(got) == [first.source_ref]  # the block with only an open suggestion is left out
    assert [(c.start, c.end, c.replacement) for c in got[first.source_ref]] == [
        (0, 3, "the"),
        (8, 11, "sat down"),
    ]
    assert all(isinstance(c, Change) and c.ref.isdigit() for c in got[first.source_ref])


def test_rejected_and_superseded_not_applied(session, tmp_storage, docx_book_with_decisions):
    first, second = sorted(
        session.query(Block).filter_by(book_id=docx_book_with_decisions.id), key=lambda b: b.id
    )
    rejected = add_one(session, second, 0, 2, "an", "An")
    decide(session, rejected.id, "reject", actor="a")
    assert second.source_ref not in recorded_changes(session, docx_book_with_decisions.id)
    wide = add_one(session, second, 3, 7, "open", "closed")
    decide(session, wide.id, "accept", actor="a")
    inner = add_one(session, second, 8, 18, "suggestion", "idea")
    decide(session, inner.id, "accept", actor="a")
    got = recorded_changes(session, docx_book_with_decisions.id)
    assert [c.replacement for c in got[second.source_ref]] == ["closed", "idea"]
    f = export_book(session, tmp_storage, docx_book_with_decisions.id, "docx-clean")
    assert "an closed idea stays teh" in [b.text for b in all_blocks(f.data)]


def test_export_saved_under_data_dir(session, tmp_storage, docx_book_with_decisions):
    bid = docx_book_with_decisions.id
    f = export_book(session, tmp_storage, bid, "docx-clean")
    saved = tmp_storage.root / "books" / str(bid) / "exports" / "draft-docx-clean.docx"
    assert saved.read_bytes() == f.data


def test_format_mismatch(session, tmp_storage, docx_book_with_decisions, epub_book_with_decisions):
    with pytest.raises(UnsupportedDocumentError, match="EPUB"):
        export_book(session, tmp_storage, docx_book_with_decisions.id, "epub-clean")
    for fmt in ("docx-clean", "docx-tracked"):
        with pytest.raises(UnsupportedDocumentError, match="DOCX"):
            export_book(session, tmp_storage, epub_book_with_decisions.id, fmt)
