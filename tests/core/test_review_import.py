import pytest

from jerome.core.books import import_book
from jerome.core.exports import export_book
from jerome.core.review_imports import import_review
from jerome.core.suggestions import decide
from jerome.errors import NotFound, UnsupportedDocumentError
from jerome.orm import Block, Decision, ReviewImport, Suggestion
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.suggestions import add_one
from tests.fixtures.word_sim import simulate_word_review


def _blocks(session, book):
    return session.query(Block).filter_by(book_id=book.id).order_by(Block.id).all()


@pytest.fixture
def book_with_three_open(session, tmp_storage):
    book = import_book(
        session,
        tmp_storage,
        filename="draft.docx",
        data=make_docx(["teh cat sat on a dog", "we recieve it"]),
        variant="en-US",
    )
    first, second = _blocks(session, book)
    add_one(session, first, 0, 3, "teh", "the")
    add_one(session, first, 8, 11, "sat", "sits")
    add_one(session, second, 3, 10, "recieve", "receive")
    return book


def first_open_id(session):
    return min(s.id for s in session.query(Suggestion).filter_by(status="open"))


def _copy(session, tmp_storage, book):
    return export_book(session, tmp_storage, book.id, "docx-review", actor="a").data


def _statuses(session):
    rows = session.query(Suggestion).filter_by(source="languagetool")
    return {s.replacement: s.status for s in rows}


def test_full_simulated_review_with_renumbered_ids(session, tmp_storage, book_with_three_open):
    copy = _copy(session, tmp_storage, book_with_three_open)
    reviewed = simulate_word_review(
        copy, accept=["the"], reject=["sits"], untracked_edits={"dog": "hound"}, renumber_ids=True
    )
    r = import_review(session, book_with_three_open.id, reviewed, actor="ed")
    assert r.counts == {
        "accepted": 1,
        "rejected": 1,
        "edited": 0,
        "unchanged": 1,
        "editor_changes": 1,
        "conflicts": 0,
    }
    assert {d.channel for d in session.query(Decision)} == {"word"}
    assert _statuses(session) == {"the": "accepted", "sits": "rejected", "receive": "open"}
    editor = session.query(Suggestion).filter_by(source="editor").one()
    assert (editor.original, editor.replacement, editor.status) == ("dog", "hound", "accepted")
    d = session.query(Decision).filter_by(suggestion_id=editor.id).one()
    assert d.review_import_id == r.review_import_id
    stored = session.get(ReviewImport, r.review_import_id)
    assert stored.report["counts"] == r.counts and stored.created_by == "ed"


def test_reupload_same_file_changes_nothing(session, tmp_storage, book_with_three_open):
    copy = _copy(session, tmp_storage, book_with_three_open)
    reviewed = simulate_word_review(
        copy, accept=["the"], reject=["sits"], untracked_edits={"dog": "hound"}
    )
    import_review(session, book_with_three_open.id, reviewed, actor="ed")
    decisions = session.query(Decision).count()
    suggestions = session.query(Suggestion).count()
    r = import_review(session, book_with_three_open.id, reviewed, actor="ed")
    assert r.counts == {
        "accepted": 0,
        "rejected": 0,
        "edited": 0,
        "unchanged": 3,
        "editor_changes": 0,
        "conflicts": 0,
    }
    assert session.query(Decision).count() == decisions
    assert session.query(Suggestion).count() == suggestions


def test_partial_then_full_review(session, tmp_storage, book_with_three_open):
    copy = _copy(session, tmp_storage, book_with_three_open)
    import_review(
        session, book_with_three_open.id, simulate_word_review(copy, accept=["the"]), actor="ed"
    )
    r = import_review(
        session,
        book_with_three_open.id,
        simulate_word_review(copy, accept=["the"], reject=["sits"]),
        actor="ed",
    )
    assert r.counts["rejected"] == 1 and r.counts["unchanged"] == 2
    assert _statuses(session) == {"the": "accepted", "sits": "rejected", "receive": "open"}


def test_changed_mind_undoes_then_decides(session, tmp_storage, book_with_three_open):
    first = _copy(session, tmp_storage, book_with_three_open)
    import_review(
        session, book_with_three_open.id, simulate_word_review(first, accept=["the"]), actor="ed"
    )
    second = _copy(session, tmp_storage, book_with_three_open)  # exports "the" as accepted
    r = import_review(
        session, book_with_three_open.id, simulate_word_review(second, reject=["the"]), actor="ed"
    )
    assert r.counts["rejected"] == 1 and r.counts["conflicts"] == 0
    assert _statuses(session)["the"] == "rejected"
    actions = [d.action for d in session.query(Decision).order_by(Decision.id)]
    assert actions == ["accept", "undo", "reject"]


def test_edited_suggestion_keeps_final_text(session, tmp_storage, book_with_three_open):
    copy = _copy(session, tmp_storage, book_with_three_open)
    reviewed = simulate_word_review(copy, accept=["the"], untracked_edits={"the": "The"})
    r = import_review(session, book_with_three_open.id, reviewed, actor="ed")
    assert r.counts["edited"] == 1 and r.counts["editor_changes"] == 0
    s = session.query(Suggestion).filter_by(replacement="the").one()
    assert (s.status, s.final_text) == ("edited", "The")


def test_deleted_paragraph_unmatched_and_untouched(session, tmp_storage, book_with_three_open):
    copy = _copy(session, tmp_storage, book_with_three_open)
    reviewed = simulate_word_review(copy, delete_paragraphs_containing=["sat"])
    r = import_review(session, book_with_three_open.id, reviewed, actor="ed")
    first = _blocks(session, book_with_three_open)[0]
    assert r.unmatched_block_ids == [first.id]
    assert _statuses(session)["the"] == "open" and _statuses(session)["sits"] == "open"
    assert r.counts["unchanged"] == 1  # only the surviving block's suggestion


def test_tracked_editor_edit_recorded_open(session, tmp_storage, book_with_three_open):
    copy = _copy(session, tmp_storage, book_with_three_open)
    reviewed = simulate_word_review(copy, tracked_edits={"dog": "hound"})
    r = import_review(session, book_with_three_open.id, reviewed, actor="ed")
    assert r.counts["editor_changes"] == 1
    editor = session.query(Suggestion).filter_by(source="editor").one()
    assert editor.status == "open" and editor.replacement == "hound"
    again = import_review(session, book_with_three_open.id, reviewed, actor="ed")
    assert again.counts["editor_changes"] == 0
    assert session.query(Suggestion).filter_by(source="editor").count() == 1


def test_open_editor_change_accepted_when_applied(session, tmp_storage, book_with_three_open):
    copy = _copy(session, tmp_storage, book_with_three_open)
    import_review(
        session,
        book_with_three_open.id,
        simulate_word_review(copy, tracked_edits={"dog": "hound"}),
        actor="ed",
    )
    r = import_review(
        session,
        book_with_three_open.id,
        simulate_word_review(copy, untracked_edits={"dog": "hound"}),
        actor="ed",
    )
    assert r.counts["editor_changes"] == 1
    assert session.query(Suggestion).filter_by(source="editor").one().status == "accepted"


def test_stale_copy_conflict_not_overwritten(session, tmp_storage, book_with_three_open):
    copy = _copy(session, tmp_storage, book_with_three_open)
    sid = first_open_id(session)
    decide(session, sid, "accept", actor="api-user")
    reviewed = simulate_word_review(copy, reject=["the"])
    r = import_review(session, book_with_three_open.id, reviewed, actor="ed")
    assert r.counts["conflicts"] == 1 and r.conflicts == [sid]
    assert session.get(Suggestion, sid).status == "accepted"


def test_noop_checked_before_conflict(session, tmp_storage, book_with_three_open):
    copy = _copy(session, tmp_storage, book_with_three_open)
    decide(session, first_open_id(session), "accept", actor="api-user")
    r = import_review(
        session, book_with_three_open.id, simulate_word_review(copy, accept=["the"]), actor="ed"
    )
    assert r.counts["conflicts"] == 0 and r.counts["accepted"] == 0


def test_missing_property_or_other_book_rejected(session, tmp_storage, book_with_three_open):
    with pytest.raises(UnsupportedDocumentError):
        import_review(session, book_with_three_open.id, make_docx(["teh cat"]), actor="ed")
    with pytest.raises(UnsupportedDocumentError):
        import_review(session, book_with_three_open.id, b"not a zip", actor="ed")
    other = import_book(
        session, tmp_storage, filename="o.docx", data=make_docx(["x y"]), variant="en-US"
    )
    copy = _copy(session, tmp_storage, book_with_three_open)
    with pytest.raises(UnsupportedDocumentError):
        import_review(session, other.id, copy, actor="ed")
    with pytest.raises(NotFound):
        import_review(session, 999999, copy, actor="ed")
    assert session.query(Decision).count() == 0 and session.query(ReviewImport).count() == 0


def test_untouched_copy_reports_nothing_odd(session, tmp_storage, book_with_three_open):
    copy = _copy(session, tmp_storage, book_with_three_open)
    r = import_review(session, book_with_three_open.id, copy, actor="ed")
    assert r.notes == [] and r.unmatched_block_ids == [] and r.counts["unchanged"] == 3


def test_misspelling_fixes_are_exact_and_clean_export_is_correct(session, tmp_storage):
    from tests.fixtures.docx_builders import all_blocks

    book = import_book(
        session,
        tmp_storage,
        filename="m.docx",
        data=make_docx(["teh wierd cat.", "I recieved it and beleive it."]),
        variant="en-US",
    )
    first = _blocks(session, book)[0]
    add_one(session, first, 0, 3, "teh", "the")
    reviewed = simulate_word_review(
        _copy(session, tmp_storage, book),
        untracked_edits={"wierd": "weird", "recieved": "received", "beleive": "believe"},
    )
    r = import_review(session, book.id, reviewed, actor="ed")
    assert r.counts["editor_changes"] == 3 and r.notes == []
    got = {
        (s.original, s.replacement) for s in session.query(Suggestion).filter_by(source="editor")
    }
    assert got == {("wierd", "weird"), ("recieved", "received"), ("beleive", "believe")}
    clean = export_book(session, tmp_storage, book.id, "docx-clean").data
    assert [b.text for b in all_blocks(clean)] == [
        "teh weird cat.",
        "I received it and believe it.",
    ]


def test_touching_suggestions_reject_a_accept_b(session, tmp_storage):
    from tests.fixtures.docx_builders import all_blocks

    book = import_book(
        session, tmp_storage, filename="t.docx", data=make_docx(["teh cat sat."]), variant="en-US"
    )
    block = _blocks(session, book)[0]
    a = add_one(session, block, 0, 3, "teh", "the")
    b = add_one(session, block, 3, 4, " ", ", ")
    reviewed = simulate_word_review(
        _copy(session, tmp_storage, book), reject=["the"], accept=[", "]
    )
    r = import_review(session, book.id, reviewed, actor="ed")
    assert r.counts["rejected"] == 1 and r.counts["accepted"] == 1
    assert (session.get(Suggestion, a.id).status, session.get(Suggestion, b.id).status) == (
        "rejected",
        "accepted",
    )
    clean = export_book(session, tmp_storage, book.id, "docx-clean").data
    assert [x.text for x in all_blocks(clean)] == ["teh, cat sat."]


def test_api_redecision_after_export_is_a_conflict(session, tmp_storage, book_with_three_open):
    sid = first_open_id(session)
    decide(session, sid, "edit", actor="a", replacement="The")
    copy = _copy(session, tmp_storage, book_with_three_open)
    decide(session, sid, "undo", actor="api-user")
    decide(session, sid, "edit", actor="api-user", replacement="THE")
    r = import_review(
        session, book_with_three_open.id, simulate_word_review(copy, accept=["The"]), actor="ed"
    )
    assert r.conflicts == [sid]
    s = session.get(Suggestion, sid)
    assert (s.status, s.final_text) == ("edited", "THE")


def test_accept_undo_accept_after_export_then_word_reject_conflicts(
    session, tmp_storage, book_with_three_open
):
    sid = first_open_id(session)
    decide(session, sid, "accept", actor="a")
    copy = _copy(session, tmp_storage, book_with_three_open)
    decide(session, sid, "undo", actor="api-user")
    decide(session, sid, "accept", actor="api-user")
    r = import_review(
        session, book_with_three_open.id, simulate_word_review(copy, reject=["the"]), actor="ed"
    )
    assert r.conflicts == [sid] and session.get(Suggestion, sid).status == "accepted"


def test_same_copy_twice_with_changed_mind_applies(session, tmp_storage, book_with_three_open):
    copy = _copy(session, tmp_storage, book_with_three_open)
    import_review(
        session, book_with_three_open.id, simulate_word_review(copy, accept=["the"]), actor="ed"
    )
    r = import_review(
        session, book_with_three_open.id, simulate_word_review(copy, reject=["the"]), actor="ed"
    )
    assert r.counts["rejected"] == 1 and r.counts["conflicts"] == 0
    assert _statuses(session)["the"] == "rejected"


def test_filtered_copy_has_no_structural_note(session, tmp_storage):
    from jerome.core.exports import ReviewFilters
    from jerome.orm import Chapter

    book = import_book(
        session,
        tmp_storage,
        filename="c.docx",
        data=make_docx(["# One", "teh a", "teh a2", "# Two", "teh b", "teh b2"]),
        variant="en-US",
    )
    two = session.query(Chapter).filter_by(book_id=book.id).order_by(Chapter.ordinal).all()[1]
    for b in session.query(Block).filter_by(chapter_id=two.id, kind="paragraph"):
        add_one(session, b, 0, 3, "teh", "the")
    copy = export_book(
        session,
        tmp_storage,
        book.id,
        "docx-review",
        filters=ReviewFilters(chapter_ids=[two.id]),
        actor="a",
    ).data
    r = import_review(session, book.id, copy, actor="ed")
    assert r.notes == [] and r.counts["unchanged"] == 2


def test_neighbouring_accepted_suggestions_write_clean_text(session, tmp_storage):
    from tests.fixtures.docx_builders import all_blocks

    book = import_book(
        session,
        tmp_storage,
        filename="n.docx",
        data=make_docx(["teh hte fox.", "teh cat sat."]),
        variant="en-US",
    )
    first, second = _blocks(session, book)
    add_one(session, first, 0, 3, "teh", "the")
    add_one(session, first, 4, 7, "hte", "the")
    add_one(session, second, 0, 3, "teh", "the")
    add_one(session, second, 3, 4, " ", ", ")
    copy = _copy(session, tmp_storage, book)
    r = import_review(
        session,
        book.id,
        simulate_word_review(copy, accept=["the", ", "]),
        actor="ed",
    )
    assert r.counts["accepted"] == 4 and r.counts["edited"] == 0 and r.notes == []
    clean = export_book(session, tmp_storage, book.id, "docx-clean").data
    assert [b.text for b in all_blocks(clean)] == ["the the fox.", "the, cat sat."]
