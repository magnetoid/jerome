import pytest

from jerome.core.books import import_book
from jerome.core.suggestions import (
    SuggestionFilter,
    add_candidates,
    bulk_decide,
    current_text,
    decide,
    list_suggestions,
    record_editor_change,
)
from jerome.errors import Conflict, InvalidChangeError
from jerome.orm import Block, Decision, Suggestion
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.suggestions import add_one, cand, insert_raw_open


@pytest.fixture
def book_with_mixed_suggestions(session, block):
    """block text 'teh cat sat': low, medium and high suggestions on three words."""
    add_one(session, block, 0, 3, "teh", "the", severity="low", category="typo")
    add_one(session, block, 4, 7, "cat", "cats", severity="medium", category="grammar")
    add_one(session, block, 8, 11, "sat", "sits", severity="high", source="model")
    return block.book_id


def test_drops_overlap_with_accepted(session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    decide(session, s.id, "accept", actor="a")
    r = add_candidates(session, block.book_id, None, [cand(block, 0, 7, "teh cat", "The cat")])
    assert r.dropped_decided_overlap == 1 and r.created == 0


def test_drops_repeat_of_rejected(session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    decide(session, s.id, "reject", actor="a")
    r = add_candidates(session, block.book_id, None, [cand(block, 0, 3, "teh", "the")])
    assert r.dropped_rejected_repeat == 1


def test_rejected_with_different_replacement_does_not_block(session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    decide(session, s.id, "reject", actor="a")
    r = add_candidates(session, block.book_id, None, [cand(block, 0, 3, "teh", "ten")])
    assert r.created == 1


def test_keeps_existing_open_on_overlap(session, block):
    add_one(session, block, 0, 3, "teh", "the")
    r = add_candidates(session, block.book_id, None, [cand(block, 0, 7, "teh cat", "the dog")])
    assert r.dropped_open_overlap == 1


def test_overlap_within_one_batch_counts_as_open_overlap(session, block):
    r = add_candidates(
        session,
        block.book_id,
        None,
        [cand(block, 0, 3, "teh", "the"), cand(block, 0, 7, "teh cat", "the dog")],
    )
    assert (r.created, r.dropped_open_overlap) == (1, 1)


def test_decided_overlap_takes_precedence_over_rejected_repeat(session, block):
    a = add_one(session, block, 0, 3, "teh", "the")
    decide(session, a.id, "accept", actor="a")
    r = add_candidates(session, block.book_id, None, [cand(block, 0, 3, "teh", "the")])
    assert (r.dropped_decided_overlap, r.dropped_rejected_repeat) == (1, 0)


def test_insertion_overlap_semantics(session, block):
    add_one(session, block, 4, 7, "cat", "cats")
    # touching spans do not overlap; insertion strictly inside does
    r = add_candidates(
        session,
        block.book_id,
        None,
        [
            cand(block, 4, 4, "", "big "),  # touches start: fine
            cand(block, 7, 7, "", "s"),  # touches end: fine
            cand(block, 5, 5, "", "x"),  # inside [4,7): dropped
        ],
    )
    assert (r.created, r.dropped_open_overlap) == (2, 1)
    # two insertions at the same position overlap
    r = add_candidates(session, block.book_id, None, [cand(block, 4, 4, "", "very ")])
    assert r.dropped_open_overlap == 1
    # an insertion at a different position does not
    r = add_candidates(session, block.book_id, None, [cand(block, 8, 8, "", "x")])
    assert r.created == 1


def test_rejects_wrong_original_and_creates_nothing(session, block):
    with pytest.raises(InvalidChangeError):
        add_candidates(
            session,
            block.book_id,
            None,
            [cand(block, 0, 3, "teh", "the"), cand(block, 4, 7, "xyz", "cats")],
        )
    assert session.query(Suggestion).filter_by(block_id=block.id).count() == 0


def test_rejects_block_of_another_book(session, block, tmp_storage):
    other = import_book(
        session, tmp_storage, filename="o.docx", data=make_docx(["other"]), variant="en-US"
    )
    with pytest.raises(InvalidChangeError):
        add_candidates(session, other.id, None, [cand(block, 0, 3, "teh", "the")])


def test_accept_supersedes_overlapping_open_and_undo_reopens(session, block):
    a = add_one(session, block, 0, 3, "teh", "the")
    b = insert_raw_open(session, block, 0, 7, "teh cat", "the dog")
    decide(session, a.id, "accept", actor="a")
    assert session.get(Suggestion, b.id).status == "superseded"
    assert session.get(Suggestion, b.id).superseded_by == a.id
    sup = session.query(Decision).filter_by(suggestion_id=b.id).one()
    assert (sup.action, sup.channel, sup.actor, sup.new_status) == (
        "supersede",
        "system",
        "a",
        "superseded",
    )
    decide(session, a.id, "undo", actor="a")
    b = session.get(Suggestion, b.id)
    assert b.status == "open" and b.superseded_by is None
    assert session.query(Decision).filter_by(suggestion_id=b.id, action="reopen").one().channel == (
        "system"
    )


def test_undo_skips_reopening_when_blocked_by_another_decided(session, block):
    a = add_one(session, block, 0, 3, "teh", "the")
    b = insert_raw_open(session, block, 0, 7, "teh cat", "the dog")
    decide(session, a.id, "accept", actor="a")  # b superseded by a
    d = insert_raw_open(session, block, 5, 7, "at", "AT")
    decide(session, d.id, "accept", actor="a")  # b already superseded: stays so
    decide(session, a.id, "undo", actor="a")
    b = session.get(Suggestion, b.id)
    assert b.status == "superseded" and b.superseded_by == d.id
    assert not session.query(Decision).filter_by(suggestion_id=b.id, action="reopen").count()
    decide(session, d.id, "undo", actor="a")
    assert session.get(Suggestion, b.id).status == "open"


def test_every_change_logged(session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    decide(session, s.id, "edit", actor="ed", replacement="The")
    d = session.query(Decision).filter_by(suggestion_id=s.id).one()
    assert (d.action, d.previous_status, d.new_status, d.final_text, d.actor, d.channel) == (
        "edit",
        "open",
        "edited",
        "The",
        "ed",
        "api",
    )
    assert d.review_import_id is None and d.created_at is not None


def test_edit_supersedes_and_undo_clears_final_text(session, block):
    a = add_one(session, block, 0, 3, "teh", "the")
    b = insert_raw_open(session, block, 0, 7, "teh cat", "the dog")
    decide(session, a.id, "edit", actor="a", replacement="The")
    assert session.get(Suggestion, b.id).status == "superseded"
    decide(session, a.id, "undo", actor="a")
    a = session.get(Suggestion, a.id)
    assert a.status == "open" and a.final_text is None
    assert [d.action for d in session.query(Decision).filter_by(suggestion_id=a.id)] == [
        "edit",
        "undo",
    ]


def test_edit_requires_replacement(session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    with pytest.raises(InvalidChangeError):
        decide(session, s.id, "edit", actor="a")


@pytest.mark.parametrize(
    ("setup", "action"),
    [
        (None, "undo"),
        ("accept", "accept"),
        ("accept", "reject"),
        ("accept", "edit"),
        ("reject", "accept"),
    ],
)
def test_invalid_transition_conflicts(session, block, setup, action):
    s = add_one(session, block, 0, 3, "teh", "the")
    if setup:
        decide(session, s.id, setup, actor="a")
    with pytest.raises(Conflict):
        decide(session, s.id, action, actor="a", replacement="x")


def test_superseded_cannot_be_decided_or_undone(session, block):
    a = add_one(session, block, 0, 3, "teh", "the")
    b = insert_raw_open(session, block, 0, 7, "teh cat", "the dog")
    decide(session, a.id, "accept", actor="a")
    for action in ("accept", "undo"):
        with pytest.raises(Conflict):
            decide(session, b.id, action, actor="a")


def test_decide_records_word_channel_and_review_import(session, block):
    from jerome.orm import ReviewExport, ReviewImport

    exp = ReviewExport(book_id=block.book_id, suggestion_statuses={}, block_ids=[], filters={})
    session.add(exp)
    session.flush()
    imp = ReviewImport(review_export_id=exp.id, book_id=block.book_id, report={})
    session.add(imp)
    session.flush()
    s = add_one(session, block, 0, 3, "teh", "the")
    decide(session, s.id, "accept", actor="a", channel="word", review_import_id=imp.id)
    d = session.query(Decision).filter_by(suggestion_id=s.id).one()
    assert (d.channel, d.review_import_id) == ("word", imp.id)


def test_current_text_applies_accepted_and_edited(session, block):
    s1 = add_one(session, block, 0, 3, "teh", "the")
    s2 = add_one(session, block, 8, 11, "sat", "sits")
    add_one(session, block, 4, 7, "cat", "dog")  # open: ignored
    decide(session, s1.id, "accept", actor="a")
    decide(session, s2.id, "edit", actor="a", replacement="sat down")
    assert current_text(session, block.id) == "the cat sat down"


def test_list_filters_and_bulk(session, book_with_mixed_suggestions):
    book_id = book_with_mixed_suggestions
    allv = list_suggestions(session, book_id, SuggestionFilter()).rows
    assert [s.start for s in allv] == [0, 4, 8]
    med = list_suggestions(session, book_id, SuggestionFilter(min_severity="medium")).rows
    assert [s.severity for s in med] == ["medium", "high"]
    assert [
        s.start for s in list_suggestions(session, book_id, SuggestionFilter(severity="low")).rows
    ] == [0]
    assert len(list_suggestions(session, book_id, SuggestionFilter(category="grammar")).rows) == 1
    assert len(list_suggestions(session, book_id, SuggestionFilter(source="model")).rows) == 1
    assert [
        s.start
        for s in list_suggestions(session, book_id, SuggestionFilter(), limit=1, offset=1).rows
    ] == [4]
    n = bulk_decide(
        session,
        book_id,
        ids=None,
        f=SuggestionFilter(min_severity="medium"),
        action="reject",
        actor="a",
    )
    assert len(n) == 2
    assert len(list_suggestions(session, book_id, SuggestionFilter(status="rejected")).rows) == 2
    assert len(list_suggestions(session, book_id, SuggestionFilter(status="open")).rows) == 1


def test_list_filters_by_chapter_and_orders_by_document(session, tmp_storage):
    book = import_book(
        session,
        tmp_storage,
        filename="c.docx",
        data=make_docx(["# A", "teh one", "# B", "teh two"]),
        variant="en-US",
    )
    blocks = session.query(Block).filter_by(book_id=book.id).order_by(Block.id).all()
    texts = {b.text: b for b in blocks}
    add_one(session, texts["teh two"], 0, 3, "teh", "the")
    add_one(session, texts["teh one"], 0, 3, "teh", "the")
    got = list_suggestions(session, book.id, SuggestionFilter()).rows
    assert [s.block_id for s in got] == [texts["teh one"].id, texts["teh two"].id]
    only = list_suggestions(
        session, book.id, SuggestionFilter(chapter_id=texts["teh two"].chapter_id)
    ).rows
    assert [s.block_id for s in only] == [texts["teh two"].id]


def test_bulk_by_ids_and_validation(session, block, tmp_storage):
    a = add_one(session, block, 0, 3, "teh", "the")
    b = add_one(session, block, 4, 7, "cat", "cats")
    assert bulk_decide(
        session, block.book_id, ids=[b.id, a.id], f=None, action="accept", actor="x"
    ) == sorted([a.id, b.id])
    # already decided: nothing left to do
    assert bulk_decide(session, block.book_id, ids=[a.id], f=None, action="reject", actor="x") == []
    with pytest.raises(InvalidChangeError):
        bulk_decide(session, block.book_id, ids=None, f=None, action="accept", actor="x")
    with pytest.raises(InvalidChangeError):
        bulk_decide(
            session, block.book_id, ids=[a.id], f=SuggestionFilter(), action="accept", actor="x"
        )
    other = import_book(
        session, tmp_storage, filename="o.docx", data=make_docx(["other"]), variant="en-US"
    )
    with pytest.raises(InvalidChangeError):
        bulk_decide(session, other.id, ids=[a.id], f=None, action="accept", actor="x")


def test_bulk_accept_supersedes_later_ones_in_same_bulk(session, block):
    a = add_one(session, block, 0, 3, "teh", "the")
    b = insert_raw_open(session, block, 0, 7, "teh cat", "the dog")
    n = bulk_decide(session, block.book_id, ids=[a.id, b.id], f=None, action="accept", actor="x")
    assert n == [a.id]
    assert session.get(Suggestion, a.id).status == "accepted"
    assert session.get(Suggestion, b.id).status == "superseded"


def test_record_editor_change_pending_accepted_and_duplicate(session, block):
    p = record_editor_change(
        session, block.id, 0, 3, "teh", "the", pending=True, actor="ed", review_import_id=None
    )
    assert (p.status, p.source, p.category, p.severity) == ("open", "editor", "style", "low")
    assert p.explanation == "Change made by the editor in Word"
    assert session.query(Decision).filter_by(suggestion_id=p.id).count() == 0

    overlapped = insert_raw_open(session, block, 4, 11, "cat sat", "dog sat")
    acc = record_editor_change(
        session, block.id, 4, 7, "cat", "dog", pending=False, actor="ed", review_import_id=None
    )
    assert acc.status == "accepted"
    d = session.query(Decision).filter_by(suggestion_id=acc.id).one()
    assert (d.action, d.channel, d.actor, d.previous_status, d.new_status) == (
        "accept",
        "word",
        "ed",
        "open",
        "accepted",
    )
    assert session.get(Suggestion, overlapped.id).status == "superseded"

    assert (
        record_editor_change(
            session, block.id, 0, 3, "teh", "the", pending=True, actor="ed", review_import_id=None
        )
        is None
    )
    with pytest.raises(InvalidChangeError):
        record_editor_change(
            session, block.id, 0, 3, "xyz", "the", pending=True, actor="ed", review_import_id=None
        )


def test_accept_after_undo_of_reject_conflicts_with_decided_overlap(session, block):
    a = add_one(session, block, 0, 3, "teh", "the")
    decide(session, a.id, "reject", actor="a")
    r = add_candidates(session, block.book_id, None, [cand(block, 0, 7, "teh cat", "The dog")])
    assert r.created == 1
    wide = session.query(Suggestion).filter_by(start=0, end=7).one()
    decide(session, wide.id, "accept", actor="a")
    decide(session, a.id, "undo", actor="a")
    assert session.get(Suggestion, a.id).status == "open"
    for action in ("accept", "edit"):
        with pytest.raises(Conflict):
            decide(session, a.id, action, actor="a", replacement="x")
    assert session.get(Suggestion, a.id).status == "open"
    assert current_text(session, block.id) == "The dog sat"


def test_bulk_accept_skips_conflicting_targets(session, block):
    a = add_one(session, block, 0, 3, "teh", "the")
    decide(session, a.id, "reject", actor="a")
    add_candidates(session, block.book_id, None, [cand(block, 0, 7, "teh cat", "The dog")])
    wide = session.query(Suggestion).filter_by(start=0, end=7).one()
    decide(session, wide.id, "accept", actor="a")
    decide(session, a.id, "undo", actor="a")
    c = add_one(session, block, 8, 11, "sat", "sits")
    n = bulk_decide(session, block.book_id, ids=[a.id, c.id], f=None, action="accept", actor="x")
    assert n == [c.id]
    assert session.get(Suggestion, a.id).status == "open"
    assert session.get(Suggestion, c.id).status == "accepted"
    assert current_text(session, block.id) == "The dog sits"


@pytest.mark.parametrize("pending", [True, False])
def test_record_editor_change_conflicts_with_decided_overlap(session, block, pending):
    a = add_one(session, block, 0, 3, "teh", "the")
    decide(session, a.id, "accept", actor="a")
    with pytest.raises(Conflict):
        record_editor_change(
            session,
            block.id,
            0,
            7,
            "teh cat",
            "x",
            pending=pending,
            actor="e",
            review_import_id=None,
        )
    assert session.query(Suggestion).filter_by(block_id=block.id).count() == 1


def test_placeholder_spans_are_rejected(session, block):
    block.text = "a ￼ b"
    session.flush()
    with pytest.raises(InvalidChangeError):
        add_candidates(session, block.book_id, None, [cand(block, 0, 3, "a ￼", "x")])
    with pytest.raises(InvalidChangeError):
        record_editor_change(
            session, block.id, 2, 3, "￼", "x", pending=True, actor="e", review_import_id=None
        )
    assert session.query(Suggestion).filter_by(block_id=block.id).count() == 0
    # an insertion next to a placeholder is fine
    assert add_candidates(session, block.book_id, None, [cand(block, 3, 3, "", "!")]).created == 1


@pytest.mark.parametrize("bad", ["x￼y", "x\x0by"])
def test_unwritable_replacements_are_refused_or_dropped(session, block, bad):
    s = add_one(session, block, 0, 3, "teh", "the")
    with pytest.raises(InvalidChangeError):
        decide(session, s.id, "edit", actor="a", replacement=bad)
    assert session.get(Suggestion, s.id).status == "open"
    with pytest.raises(InvalidChangeError):
        record_editor_change(
            session, block.id, 4, 7, "cat", bad, pending=False, actor="a", review_import_id=None
        )
    r = add_candidates(
        session,
        block.book_id,
        None,
        [cand(block, 4, 7, "cat", bad), cand(block, 8, 11, "sat", "sits")],
    )
    assert (r.created, r.dropped_invalid) == (1, 1)
    decide(session, s.id, "edit", actor="a", replacement="a\tb")  # tab is fine


def test_add_candidates_stores_flags_and_returns_rows(session, block):
    flagged = cand(
        block, 0, 3, "teh", "the", agreed=True, disputed=True, alternative="ten", rule="variant"
    )
    plain = cand(block, 4, 7, "cat", "cats")
    add_one(session, block, 8, 11, "sat", "sits")  # existing row, not "created"
    r = add_candidates(
        session,
        block.book_id,
        None,
        [flagged, plain, cand(block, 8, 11, "sat", "sat down")],
    )
    assert r.created == 2 and r.dropped_open_overlap == 1
    first, second = r.rows
    assert (first.replacement, second.replacement) == ("the", "cats")
    assert (first.agreed, first.disputed, first.alternative, first.rule) == (
        True,
        True,
        "ten",
        "variant",
    )
    assert (second.agreed, second.disputed, second.alternative, second.rule) == (
        False,
        False,
        None,
        None,
    )


def test_add_candidates_stores_languagetool_options_and_rule(session, block):
    lt = cand(block, 0, 3, "teh", "the", options=("the", "tea"), lt_rule="MORFOLOGIK")
    plain = cand(block, 4, 7, "cat", "cats")
    first, second = add_candidates(session, block.book_id, None, [lt, plain]).rows
    assert (first.options, first.lt_rule) == (["the", "tea"], "MORFOLOGIK")
    assert (second.options, second.lt_rule) == (None, None)  # no options: nothing stored


def _auto_applied(session, block, start=0, end=3, original="teh", replacement="the"):
    s = add_one(session, block, start, end, original, replacement, source="model")
    decide(session, s.id, "accept", actor="ai", channel="auto")
    return s


def _decisions(session, s):
    return session.query(Decision).filter_by(suggestion_id=s.id).order_by(Decision.id).all()


def test_reject_reverts_an_auto_applied_change_in_one_step(session, block):
    s = _auto_applied(session, block)
    assert current_text(session, block.id) == "the cat sat"
    decide(session, s.id, "reject", actor="ed@example.com", channel="web")
    assert session.get(Suggestion, s.id).status == "rejected"
    auto, person = _decisions(session, s)  # exactly one new decision
    assert (auto.actor, auto.channel) == ("ai", "auto")
    assert (
        person.action,
        person.previous_status,
        person.new_status,
        person.actor,
        person.channel,
    ) == ("reject", "accepted", "rejected", "ed@example.com", "web")
    assert current_text(session, block.id) == "teh cat sat"


def test_reject_of_an_auto_applied_change_reopens_what_it_superseded(session, block):
    s = add_one(session, block, 0, 3, "teh", "the", source="model")
    wide = insert_raw_open(session, block, 0, 7, "teh cat", "the dog")
    decide(session, s.id, "accept", actor="ai", channel="auto")
    assert session.get(Suggestion, wide.id).status == "superseded"
    decide(session, s.id, "reject", actor="ed@example.com", channel="web")
    wide = session.get(Suggestion, wide.id)
    assert (wide.status, wide.superseded_by) == ("open", None)
    assert len(_decisions(session, s)) == 2


def test_edit_an_auto_applied_change(session, block):
    s = _auto_applied(session, block)
    decide(session, s.id, "edit", actor="ed@example.com", channel="web", replacement="The")
    s = session.get(Suggestion, s.id)
    assert (s.status, s.final_text) == ("edited", "The")
    _, edit = _decisions(session, s)
    assert (edit.action, edit.previous_status, edit.new_status, edit.final_text) == (
        "edit",
        "accepted",
        "edited",
        "The",
    )
    assert (edit.actor, edit.channel) == ("ed@example.com", "web")
    assert current_text(session, block.id) == "The cat sat"


def test_reject_after_a_persons_accept_is_still_409(session, block):
    s = add_one(session, block, 0, 3, "teh", "the", source="model")
    decide(session, s.id, "accept", actor="ed@example.com", channel="web")
    for action in ("reject", "edit"):
        with pytest.raises(Conflict):
            decide(session, s.id, action, actor="ed@example.com", channel="web", replacement="x")
    # an AI change that a person undid and accepted again is now that person's change
    t = _auto_applied(session, block, 4, 7, "cat", "cats")
    decide(session, t.id, "undo", actor="ed@example.com", channel="web")
    decide(session, t.id, "accept", actor="ed@example.com", channel="web")
    with pytest.raises(Conflict):
        decide(session, t.id, "reject", actor="ed@example.com", channel="web")
    assert session.get(Suggestion, t.id).status == "accepted"


def test_undo_and_accept_of_an_auto_applied_change_work_as_before(session, block):
    s = _auto_applied(session, block)
    with pytest.raises(Conflict):
        decide(session, s.id, "accept", actor="ed@example.com", channel="web")
    decide(session, s.id, "undo", actor="ed@example.com", channel="web")
    assert session.get(Suggestion, s.id).status == "open"
    assert [d.action for d in _decisions(session, s)] == ["accept", "undo"]
