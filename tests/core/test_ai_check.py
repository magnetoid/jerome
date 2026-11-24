"""Asking the AI about LanguageTool marks in review: which marks, the call, its bill, the save."""

import re
from decimal import Decimal

import pytest
from sqlalchemy import select

from jerome.core.ai_check import CHAPTER_LIMIT, check_chapter, check_suggestion
from jerome.core.books import import_book
from jerome.core.suggestions import decide
from jerome.errors import AiCheckError, Conflict, NotFound
from jerome.models import FakeProvider, ModelRefusal, Registry, Usage
from jerome.orm import Block, Book, Chapter, Job, ModelUsage, Suggestion
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.suggestions import insert_raw_open

CALL_COST = Decimal("0.007")  # one FakeProvider call, Usage(1000, 150), at the default price
_MARK_ID = re.compile(r'<mark id="(\d+)">')


def registry(test_settings, provider):
    models = {"ai": {"provider": "fake", "model": "claude-opus-5-5"}}
    return Registry(test_settings.model_copy(update={"models": models}), {"fake": provider})


def answer(verdict="not_an_error", replacement=""):
    """A reply giving ``verdict`` for every mark the request asks about."""

    def reply(kwargs):
        ids = [int(i) for i in _MARK_ID.findall(kwargs["user"])]
        items = [
            {"id": i, "verdict": verdict, "replacement": replacement, "reason": "Because."}
            for i in ids
        ]
        return {"verdicts": items}

    return reply


def book(sf, storage, paragraphs=("Katharina said teh cat sat.", "The dog barkd.")):
    """A proofread book: its blocks and its proofreading job's id."""
    with sf() as s:
        b = import_book(
            s, storage, filename="b.docx", data=make_docx(list(paragraphs)), variant="en-US"
        )
        job = Job(
            book_id=b.id, type="proofread", status="completed", passes=["languagetool"],
            max_cost=Decimal(1),
        )  # fmt: skip
        s.add(job)
        s.commit()
        book_id, job_id = b.id, job.id
        stmt = select(Block).where(Block.book_id == book_id).order_by(Block.ordinal)
        blocks = list(s.scalars(stmt))
        s.expunge_all()
        return book_id, blocks, job_id


def mark(sf, block, start, end, replacement, **kw):
    with sf() as s:
        blk = s.get(Block, block.id)
        row = insert_raw_open(s, blk, start, end, blk.text[start:end], replacement, **kw)
        s.commit()
        return row.id


def row(sf, suggestion_id):
    with sf() as s:
        r = s.get(Suggestion, suggestion_id)
        s.expunge(r)
        return r


def usage(sf, job_id):
    with sf() as s:
        return list(s.scalars(select(ModelUsage).where(ModelUsage.job_id == job_id)))


def cost(sf, job_id):
    with sf() as s:
        return s.get(Job, job_id).cost_actual


def test_single_check_saves_the_verdict_and_bills_the_job(
    session_factory, tmp_storage, test_settings
):
    sf = session_factory
    book_id, (b1, _), job_id = book(sf, tmp_storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    provider = FakeProvider([answer("better", "the")])
    with sf() as s:
        got = check_suggestion(s, registry(test_settings, provider), sid, actor="a")
        s.commit()
        assert got.id == sid
    r = row(sf, sid)
    assert (r.ai_verdict, r.ai_replacement, r.ai_reason, r.ai_model) == (
        "better", "the", "Because.", "claude-opus-5-5",
    )  # fmt: skip
    assert r.status == "open"
    rows = usage(sf, job_id)
    assert [(u.pass_name, u.job_id) for u in rows] == [("ai", job_id)]
    assert cost(sf, job_id) == CALL_COST
    call = provider.calls[0]
    assert "⟦teh⟧" in call["user"] and "LanguageTool's fix: tea" in call["user"]
    assert call["model"] == "claude-opus-5-5"


def test_mark_without_job_bills_the_latest_proofreading_job(
    session_factory, tmp_storage, test_settings
):
    sf = session_factory
    book_id, (b1, _), job_id = book(sf, tmp_storage)
    sid = mark(sf, b1, 15, 18, "tea")  # no job_id
    with sf() as s:
        check_suggestion(s, registry(test_settings, FakeProvider([answer()])), sid, actor="a")
        s.commit()
    assert [u.job_id for u in usage(sf, job_id)] == [job_id]
    assert row(sf, sid).ai_verdict == "not_an_error"


def test_request_has_the_previous_paragraph(session_factory, tmp_storage, test_settings):
    sf = session_factory
    _, (_, b2), job_id = book(sf, tmp_storage)
    sid = mark(sf, b2, 8, 13, "barked", job_id=job_id)
    provider = FakeProvider([answer("correct")])
    with sf() as s:
        check_suggestion(s, registry(test_settings, provider), sid, actor="a")
        s.commit()
    assert "<previous_paragraph" in provider.calls[0]["user"]
    assert "Katharina said teh cat sat." in provider.calls[0]["user"]
    assert row(sf, sid).ai_verdict == "correct"


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"status": "accepted"}, "not open"),
        ({"source": "model"}, "LanguageTool alone"),
        ({"agreed": True}, "LanguageTool alone"),
        ({"ai_verdict": "correct", "ai_reason": "r"}, "already"),
    ],
)
def test_not_eligible_conflicts(session_factory, tmp_storage, test_settings, kw, why):
    sf = session_factory
    _, (b1, _), job_id = book(sf, tmp_storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id, **kw)
    provider = FakeProvider([])
    with sf() as s:
        with pytest.raises(Conflict, match=why):
            check_suggestion(s, registry(test_settings, provider), sid, actor="a")
    assert provider.calls == []


def test_unknown_suggestion_is_not_found(session_factory, test_settings):
    with session_factory() as s:
        with pytest.raises(NotFound):
            check_suggestion(s, registry(test_settings, FakeProvider([])), 99999, actor="a")


def test_unusable_reply_saves_nothing_but_is_billed(session_factory, tmp_storage, test_settings):
    sf = session_factory
    _, (b1, _), job_id = book(sf, tmp_storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    for bad in ({"verdicts": []}, {"nothing": True}):
        with sf() as s:
            with pytest.raises(AiCheckError):
                check_suggestion(s, registry(test_settings, FakeProvider([bad])), sid, actor="a")
            s.commit()
    assert row(sf, sid).ai_verdict is None
    assert len(usage(sf, job_id)) == 2


def test_refusal_is_ai_check_error(session_factory, tmp_storage, test_settings):
    sf = session_factory
    _, (b1, _), job_id = book(sf, tmp_storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    refusal = ModelRefusal("no", usage=Usage(10, 0), model="claude-opus-5-5", provider="fake")
    with sf() as s:
        with pytest.raises(AiCheckError, match="declined"):
            check_suggestion(s, registry(test_settings, FakeProvider([refusal])), sid, actor="a")
    assert len(usage(sf, job_id)) == 1


def test_no_ai_model_conflicts(session_factory, tmp_storage, test_settings):
    sf = session_factory
    _, (b1, _), job_id = book(sf, tmp_storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    empty = Registry(test_settings.model_copy(update={"models": {}}), {})
    with sf() as s:
        with pytest.raises(Conflict, match="models.ai"):
            check_suggestion(s, empty, sid, actor="a")


def test_local_only_book_with_hosted_model_conflicts(session_factory, tmp_storage, test_settings):
    sf = session_factory
    book_id, (b1, _), job_id = book(sf, tmp_storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    with sf() as s:
        s.get(Book, book_id).local_only = True
        s.commit()
    hosted = FakeProvider([answer()], local=False)
    with sf() as s:
        with pytest.raises(Conflict):
            check_suggestion(s, registry(test_settings, hosted), sid, actor="a")
    assert hosted.calls == []


def test_mark_decided_during_the_call_keeps_its_status(session_factory, tmp_storage, test_settings):
    sf = session_factory
    _, (b1, _), job_id = book(sf, tmp_storage)
    sid = mark(sf, b1, 15, 18, "the", job_id=job_id)
    reply = answer("correct")

    def accepted_meanwhile(kwargs):
        with sf() as other:  # a teammate accepts the mark while the model thinks
            decide(other, sid, "accept", actor="b", channel="web")
            other.commit()
        return reply(kwargs)

    with sf() as s:
        got = check_suggestion(
            s, registry(test_settings, FakeProvider([accepted_meanwhile])), sid, actor="a"
        )
        s.commit()
        assert got.status == "accepted"
    r = row(sf, sid)
    assert (r.status, r.ai_verdict) == ("accepted", "correct")


def test_chapter_groups_by_paragraph(session_factory, tmp_storage, test_settings):
    sf = session_factory
    book_id, (b1, b2), job_id = book(sf, tmp_storage)
    ids = [
        mark(sf, b1, 0, 9, "Catherine", job_id=job_id),
        mark(sf, b1, 15, 18, "tea", job_id=job_id),
        mark(sf, b2, 8, 13, "barked", job_id=job_id),
    ]
    model_mark = mark(sf, b1, 23, 26, "sits", job_id=job_id, source="model")
    provider = FakeProvider([answer(), answer()])
    with sf() as s:
        got = check_chapter(s, registry(test_settings, provider), book_id, b1.chapter_id, actor="a")
        s.commit()
        checked = [r.id for r in got.checked]
    assert checked == ids
    assert (got.remaining, got.failed) == (0, 0)
    assert len(provider.calls) == 2
    assert all(row(sf, i).ai_verdict == "not_an_error" for i in ids)
    assert row(sf, model_mark).ai_verdict is None
    assert cost(sf, job_id) == 2 * CALL_COST


def test_chapter_checks_40_then_the_rest(session_factory, tmp_storage, test_settings):
    sf = session_factory
    text = " ".join(f"w{i:02d}" for i in range(45))  # 45 words of 3 letters
    book_id, (b1,), job_id = book(sf, tmp_storage, paragraphs=(text,))
    for i in range(45):
        mark(sf, b1, 4 * i, 4 * i + 3, "x", job_id=job_id)
    provider = FakeProvider([answer(), answer()])
    reg = registry(test_settings, provider)
    with sf() as s:
        first = check_chapter(s, reg, book_id, b1.chapter_id, actor="a")
        s.commit()
    assert (len(first.checked), first.remaining) == (CHAPTER_LIMIT, 5)
    with sf() as s:
        second = check_chapter(s, reg, book_id, b1.chapter_id, actor="a")
        s.commit()
    assert (len(second.checked), second.remaining) == (5, 0)
    with sf() as s:
        third = check_chapter(s, reg, book_id, b1.chapter_id, actor="a")
    assert (third.checked, third.remaining, third.failed) == ([], 0, 0)
    assert len(provider.calls) == 2


def test_chapter_counts_failed_marks(session_factory, tmp_storage, test_settings):
    sf = session_factory
    book_id, (b1, b2), job_id = book(sf, tmp_storage)
    bad = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    good = mark(sf, b2, 8, 13, "barked", job_id=job_id)
    provider = FakeProvider([{"verdicts": []}, answer("correct")])
    with sf() as s:
        got = check_chapter(s, registry(test_settings, provider), book_id, b1.chapter_id, actor="a")
        s.commit()
        checked = [r.id for r in got.checked]
    assert checked == [good]
    assert (got.remaining, got.failed) == (0, 1)
    assert row(sf, bad).ai_verdict is None and row(sf, good).ai_verdict == "correct"
    assert len(usage(sf, job_id)) == 2


def test_chapter_of_another_book_is_not_found(session_factory, tmp_storage, test_settings):
    sf = session_factory
    book_id, (b1, _), _ = book(sf, tmp_storage)
    other_id, _, _ = book(sf, tmp_storage)
    with sf() as s:
        chapter = s.scalars(select(Chapter.id).where(Chapter.book_id == other_id)).first()
        with pytest.raises(NotFound):
            check_chapter(s, registry(test_settings, FakeProvider([])), book_id, chapter, actor="a")
