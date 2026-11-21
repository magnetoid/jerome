import re

import pytest

from jerome.core.books import import_book
from jerome.core.exports import ReviewFilters, export_book
from jerome.core.suggestions import decide
from jerome.errors import NotFound, UnsupportedDocumentError
from jerome.orm import Block, Chapter, ReviewExport, Suggestion
from jerome.types import Category, Severity
from tests.fixtures.docx_builders import custom_props, make_docx, part_xml
from tests.fixtures.suggestions import add_one, insert_raw_open


def _book(session, tmp_storage, lines, filename="draft.docx", title=None):
    return import_book(
        session, tmp_storage, filename=filename, data=make_docx(lines), variant="en-US", title=title
    )


def _blocks(session, book):
    return session.query(Block).filter_by(book_id=book.id).order_by(Block.id).all()


def _bookmark_names(data):
    return set(
        re.findall(r'<w:bookmarkStart [^>]*w:name="([^"]+)"', part_xml(data, "word/document.xml"))
    )


@pytest.fixture
def docx_book_with_open_typo(session, tmp_storage):
    book = _book(session, tmp_storage, ["teh cat"])
    add_one(
        session,
        _blocks(session, book)[0],
        0,
        3,
        "teh",
        "the",
        severity=Severity.HIGH,
        explanation="Misspelling.",
    )
    return book


@pytest.fixture
def docx_book_with_low_style(session, tmp_storage):
    book = _book(session, tmp_storage, ["very good cat", "teh cat"])
    first, second = _blocks(session, book)
    low = add_one(session, first, 0, 4, "very", "", category=Category.STYLE, severity=Severity.LOW)
    high = add_one(session, second, 0, 3, "teh", "the", severity=Severity.HIGH)
    return book, low, high


def test_review_copy_has_open_and_recorded_as_revisions(
    session, tmp_storage, docx_book_with_decisions
):
    f = export_book(session, tmp_storage, docx_book_with_decisions.id, "docx-review", actor="a")
    doc = part_xml(f.data, "word/document.xml")
    assert doc.count("<w:ins ") == 3  # 2 recorded + 1 open
    assert 'w:author="Jerome"' in doc


def test_comment_format(session, tmp_storage, docx_book_with_open_typo):
    f = export_book(session, tmp_storage, docx_book_with_open_typo.id, "docx-review", actor="a")
    assert "[typo · high] Misspelling." in part_xml(f.data, "word/comments.xml")


def test_filters_exclude_low(session, tmp_storage, docx_book_with_low_style):
    book, low, high = docx_book_with_low_style
    f = export_book(
        session,
        tmp_storage,
        book.id,
        "docx-review",
        filters=ReviewFilters(min_severity=Severity.MEDIUM),
        actor="a",
    )
    rx = session.query(ReviewExport).one()
    assert rx.suggestion_statuses == {str(high.id): "open"}
    assert part_xml(f.data, "word/document.xml").count("<w:ins ") == 1  # only "the"
    session.refresh(low)
    assert low.status == "open"
    assert rx.filters == {"min_severity": "medium", "categories": None, "chapter_ids": None}


def test_categories_filter(session, tmp_storage, docx_book_with_low_style):
    book, low, high = docx_book_with_low_style
    export_book(
        session,
        tmp_storage,
        book.id,
        "docx-review",
        filters=ReviewFilters(categories=[Category.STYLE]),
        actor="a",
    )
    rx = session.query(ReviewExport).one()
    assert rx.suggestion_statuses == {str(low.id): "open"}
    assert rx.filters["categories"] == ["style"]


def test_categories_filter_does_not_hide_recorded(session, tmp_storage, docx_book_with_decisions):
    export_book(
        session,
        tmp_storage,
        docx_book_with_decisions.id,
        "docx-review",
        filters=ReviewFilters(categories=[Category.STYLE]),
        actor="a",
    )
    rx = session.query(ReviewExport).one()
    assert sorted(rx.suggestion_statuses.values()) == ["accepted", "edited"]


def test_bookmark_on_every_exported_block(session, tmp_storage):
    book = _book(session, tmp_storage, ["# One", "a b", "c d", "# Two", "e f"])
    session.query(Chapter).filter_by(book_id=book.id, ordinal=1).one().proofread = False
    session.flush()
    first = session.query(Chapter).filter_by(book_id=book.id).order_by(Chapter.ordinal).first()
    f = export_book(session, tmp_storage, book.id, "docx-review", actor="a")
    expected = {
        f"_jb{b.id}" for b in session.query(Block).filter_by(book_id=book.id, chapter_id=first.id)
    }
    assert expected and _bookmark_names(f.data) == expected
    rx = session.query(ReviewExport).one()
    assert sorted(rx.block_ids) == sorted(int(n[3:]) for n in expected)


def test_review_export_row_and_property(session, tmp_storage, docx_book_with_open_typo):
    f = export_book(session, tmp_storage, docx_book_with_open_typo.id, "docx-review", actor="a")
    sid = session.query(Suggestion).one().id
    rx = session.query(ReviewExport).one()
    assert custom_props(f.data)["jerome_review_export_id"] == str(rx.id)
    assert rx.suggestion_statuses == {str(sid): "open"}
    assert rx.created_by == "a" and rx.book_id == docx_book_with_open_typo.id


def test_edited_suggestion_uses_final_text(session, tmp_storage, docx_book_with_decisions):
    f = export_book(session, tmp_storage, docx_book_with_decisions.id, "docx-review", actor="a")
    doc = part_xml(f.data, "word/document.xml")
    assert "sat down" in doc and "sits" not in doc


def test_chapters_filter(session, tmp_storage):
    book = _book(session, tmp_storage, ["# One", "teh a", "# Two", "teh b"])
    one, two = session.query(Chapter).filter_by(book_id=book.id).order_by(Chapter.ordinal).all()
    for ch in (one, two):
        b = session.query(Block).filter_by(chapter_id=ch.id, kind="paragraph").one()
        add_one(session, b, 0, 3, "teh", "the")
    f = export_book(
        session,
        tmp_storage,
        book.id,
        "docx-review",
        filters=ReviewFilters(chapter_ids=[two.id]),
        actor="a",
    )
    rx = session.query(ReviewExport).one()
    assert len(rx.suggestion_statuses) == 1
    assert rx.filters["chapter_ids"] == [two.id]
    assert part_xml(f.data, "word/document.xml").count("<w:ins ") == 1
    ids = {b.id for b in session.query(Block).filter_by(chapter_id=two.id)}
    assert set(rx.block_ids) == ids


def test_foreign_chapter_not_found(session, tmp_storage, docx_book_with_decisions):
    other = _book(session, tmp_storage, ["# X", "y"], filename="other.docx")
    foreign = session.query(Chapter).filter_by(book_id=other.id).first()
    with pytest.raises(NotFound):
        export_book(
            session,
            tmp_storage,
            docx_book_with_decisions.id,
            "docx-review",
            filters=ReviewFilters(chapter_ids=[foreign.id]),
        )
    assert session.query(ReviewExport).count() == 0


def test_overlap_keeps_recorded_over_open(session, tmp_storage):
    book = _book(session, tmp_storage, ["abcdef ghi"])
    b = _blocks(session, book)[0]
    rec = add_one(session, b, 0, 3, "abc", "X")
    decide(session, rec.id, "accept", actor="a")
    open_ = insert_raw_open(session, b, 2, 5, "cde", "Y")
    export_book(session, tmp_storage, book.id, "docx-review", actor="a")
    rx = session.query(ReviewExport).one()
    assert rx.suggestion_statuses == {str(rec.id): "accepted"}
    assert str(open_.id) not in rx.suggestion_statuses


def test_filename_shape_sanitises_title(session, tmp_storage):
    book = _book(session, tmp_storage, ["x"], title="A/B\\C")
    f = export_book(session, tmp_storage, book.id, "docx-review", actor="a")
    rx = session.query(ReviewExport).one()
    assert f.filename == f"A_B_C-review-{rx.id}.docx"
    assert (tmp_storage.root / "books" / str(book.id) / "exports" / f.filename).exists()


def test_each_export_gets_its_own_row(session, tmp_storage, docx_book_with_open_typo):
    a = export_book(session, tmp_storage, docx_book_with_open_typo.id, "docx-review", actor="a")
    b = export_book(session, tmp_storage, docx_book_with_open_typo.id, "docx-review", actor="a")
    assert a.filename != b.filename and session.query(ReviewExport).count() == 2


def test_docx_review_on_epub_unsupported(session, tmp_storage, epub_book_with_decisions):
    with pytest.raises(UnsupportedDocumentError, match="DOCX"):
        export_book(session, tmp_storage, epub_book_with_decisions.id, "docx-review")
    assert session.query(ReviewExport).count() == 0


def test_unknown_format_fails_closed(session, tmp_storage, docx_book_with_decisions):
    with pytest.raises(UnsupportedDocumentError):
        export_book(session, tmp_storage, docx_book_with_decisions.id, "pdf")  # type: ignore[arg-type]


def test_review_copy_only_for_docx(client, auth, epub_book_id):
    r = client.get(f"/v1/books/{epub_book_id}/export?format=docx-review", headers=auth)
    assert r.status_code == 422


def test_review_route_with_filters(client, auth, session, docx_book_with_low_style):
    book, low, high = docx_book_with_low_style
    url = f"/v1/books/{book.id}/export?format=docx-review"
    r = client.get(f"{url}&min_severity=medium&categories=typo&categories=grammar", headers=auth)
    assert r.status_code == 200
    rx = session.query(ReviewExport).one()
    assert rx.created_by == "tester"
    assert rx.suggestion_statuses == {str(high.id): "open"}
    assert rx.filters == {
        "min_severity": "medium",
        "categories": ["typo", "grammar"],
        "chapter_ids": None,
    }
    assert custom_props(r.content)["jerome_review_export_id"] == str(rx.id)
    assert f"-review-{rx.id}.docx" in r.headers["content-disposition"]


def test_review_route_foreign_chapter_404(
    client, auth, session, tmp_storage, docx_book_with_decisions
):
    other = _book(session, tmp_storage, ["# X", "y"], filename="other.docx")
    foreign = session.query(Chapter).filter_by(book_id=other.id).first()
    r = client.get(
        f"/v1/books/{docx_book_with_decisions.id}/export?format=docx-review&chapters={foreign.id}",
        headers=auth,
    )
    assert r.status_code == 404
