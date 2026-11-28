"""The three states of a chapter's paragraphs: original, translation and proofread."""

import pytest
from sqlalchemy import select

from jerome.core.exports import recorded_changes
from jerome.core.preview import applied_spans, chapter_preview, proofread_changes, shift_marks
from jerome.core.suggestions import decide
from jerome.core.translation import import_for_translation, source_of
from jerome.documents.changes import Change, apply_changes
from jerome.documents.model import Mark, MarkKind
from jerome.errors import Conflict, NotFound
from jerome.orm import Block, Chapter, Suggestion
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.suggestions import add_one


def _sug(id, start, end, status, replacement="R", final_text=None):
    return Suggestion(
        id=id,
        start=start,
        end=end,
        original="x",
        replacement=replacement,
        final_text=final_text,
        status=status,
    )


def _chapters(session, book):
    return list(
        session.scalars(select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.ordinal))
    )


def _blocks(session, chapter):
    return list(
        session.scalars(
            select(Block).where(Block.chapter_id == chapter.id).order_by(Block.ordinal, Block.id)
        )
    )


def test_proofread_changes_keeps_accepted_and_edited_only():
    changes = proofread_changes(
        [
            _sug(4, 9, 10, "edited", final_text="E"),
            _sug(1, 0, 1, "open"),
            _sug(2, 2, 3, "rejected"),
            _sug(3, 4, 5, "accepted", replacement="B"),
        ]
    )
    assert [(c.start, c.end, c.replacement) for c in changes] == [(4, 5, "B"), (9, 10, "E")]


def test_proofread_equals_clean_export_rule(session, tmp_storage):
    from jerome.core.books import import_book

    book = import_book(
        session,
        tmp_storage,
        filename="p.docx",
        data=make_docx(["# Title", "teh cat sat on teh mat", "an open one stays teh"]),
        variant="en-US",
    )
    chapter = _chapters(session, book)[0]
    blocks = _blocks(session, chapter)
    para, other = blocks[1], blocks[2]
    decide(session, add_one(session, para, 0, 3, "teh", "the").id, "accept", actor="a")
    decide(session, add_one(session, para, 3, 4, " ", "  ").id, "accept", actor="a")
    s = add_one(session, para, 8, 11, "sat", "sits")
    decide(session, s.id, "edit", actor="a", replacement="sat down")
    decide(session, add_one(session, para, 15, 18, "teh", "a").id, "reject", actor="a")
    add_one(session, other, 18, 21, "teh", "the")

    preview = chapter_preview(session, chapter.id, 0, 50)
    recorded = recorded_changes(session, book.id)
    for b, out in zip(blocks, preview["blocks"], strict=True):
        assert out["proofread"]["text"] == apply_changes(b.text, recorded.get(b.source_ref, []))
    assert preview["blocks"][1]["proofread"]["text"] == "the  cat sat down on teh mat"


ITALIC = [Mark(3, 7, MarkKind.ITALIC)]  # "aa *bbbb* cc" as "aa bbbb cc"


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (Change(4, 6, "XYZ"), (3, 8)),  # inside: the mark grows
        (Change(0, 2, "A"), (2, 6)),  # before: it shifts
        (Change(7, 8, ""), (3, 7)),  # at the edge after: unchanged
        (Change(3, 7, "q"), (3, 4)),  # the whole span: it wraps the replacement
        (Change(7, 7, "Z"), (3, 7)),  # insertion at the end stays outside
        (Change(3, 3, "Z"), (4, 8)),  # insertion at the start stays outside
    ],
)
def test_marks_follow_changes(change, expected):
    [mark] = shift_marks(ITALIC, [change])
    assert (mark.start, mark.end, mark.kind) == (*expected, MarkKind.ITALIC)


def test_marks_dropped_when_their_text_is_deleted():
    assert shift_marks(ITALIC, [Change(2, 9, "")]) == []


def test_marks_follow_several_changes():
    [mark] = shift_marks(ITALIC, [Change(0, 2, "A"), Change(4, 6, "XYZ")])
    assert (mark.start, mark.end) == (2, 7)


def test_applied_spans():
    text, changes = "the cat", [Change(4, 7, "dog")]
    assert apply_changes(text, changes) == "the dog"
    assert applied_spans(text, changes) == [{"start": 4, "end": 7, "removed": "cat"}]


def test_applied_spans_shift_with_earlier_changes():
    changes = [Change(0, 3, "a"), Change(4, 7, "dog")]
    assert apply_changes("the cat", changes) == "a dog"
    assert applied_spans("the cat", changes) == [
        {"start": 0, "end": 1, "removed": "the"},
        {"start": 2, "end": 5, "removed": "cat"},
    ]


def _translated(session, storage):
    return import_for_translation(
        session,
        storage,
        filename="b.docx",
        data=make_docx(["# One", "Hello **bold** world.", "Second."]),
        source_variant="en-GB",
        target_variant="de-DE",
    )


def test_translated_block_states(session, tmp_storage):
    book = _translated(session, tmp_storage)
    chapter = _chapters(session, book)[0]
    block = _blocks(session, chapter)[1]
    block.text, block.marks, block.translation_status = "Hallo Welt", [], "done"
    session.flush()
    decide(session, add_one(session, block, 0, 5, "Hallo", "Servus").id, "accept", actor="a")
    source_block = session.get(Block, block.source_block_id)

    preview = chapter_preview(session, chapter.id, 0, 50)
    out = preview["blocks"][1]
    assert out["original"]["text"] == source_block.text == "Hello bold world."
    assert out["original"]["marks"] == [{"start": 6, "end": 10, "kind": "bold", "href": None}]
    assert out["translation"] == {"text": "Hallo Welt", "marks": [], "status": "done"}
    assert out["proofread"]["text"] == "Servus Welt"
    assert out["proofread"]["changes"] == [{"start": 0, "end": 6, "removed": "Hallo"}]
    assert preview["work"] == "translate"
    assert preview["source_variant"] == "en-GB"
    assert preview["variant"] == "de-DE"
    assert preview["book_id"] == book.id
    assert preview["total_blocks"] == 3


def test_pending_translation_block(session, tmp_storage):
    book = _translated(session, tmp_storage)
    chapter = _chapters(session, book)[0]
    out = chapter_preview(session, chapter.id, 0, 50)["blocks"][2]
    assert out["translation"]["status"] == "pending"
    assert out["proofread"]["text"] == ""
    assert out["original"]["text"] == "Second."


def test_proofread_only_book(session, docx_book_with_decisions):
    chapter = _chapters(session, docx_book_with_decisions)[0]
    preview = chapter_preview(session, chapter.id, 0, 50)
    first = preview["blocks"][0]
    assert first["translation"] is None
    assert first["original"] == {"text": "teh cat sat", "marks": []}
    assert first["proofread"]["text"] == "the cat sat down"
    assert preview["work"] == "proofread"
    assert preview["source_variant"] is None
    assert preview["variant"] == "en-US"
    assert preview["title"] == chapter.title
    assert preview["proofread"] is chapter.proofread
    assert preview["offset"] == 0


def test_paging(session, docx_book_with_decisions):
    chapter = _chapters(session, docx_book_with_decisions)[0]
    preview = chapter_preview(session, chapter.id, 1, 1)
    assert preview["total_blocks"] == 2
    assert [b["ordinal"] for b in preview["blocks"]] == [_blocks(session, chapter)[1].ordinal]


def test_hidden_source_and_importing_are_refused(session, tmp_storage, docx_book_with_decisions):
    book = _translated(session, tmp_storage)
    source_chapter = _chapters(session, source_of(session, book))[0]
    with pytest.raises(Conflict):
        chapter_preview(session, source_chapter.id, 0, 50)

    docx_book_with_decisions.import_status = "importing"
    session.flush()
    with pytest.raises(Conflict):
        chapter_preview(session, _chapters(session, docx_book_with_decisions)[0].id, 0, 50)

    with pytest.raises(NotFound):
        chapter_preview(session, 987654, 0, 50)
