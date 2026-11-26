"""Asking the AI about LanguageTool marks in review: which marks, the call, its bill, the save."""

import re
from decimal import Decimal

import pytest
from sqlalchemy import select, text

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
    provider = FakeProvider([answer("error", "the")])
    with sf() as s:
        got = check_suggestion(s, registry(test_settings, provider), sid, actor="a")
        s.commit()
        assert got.id == sid
    r = row(sf, sid)
    assert (r.ai_verdict, r.ai_replacement, r.ai_reason, r.ai_model) == (
        "error", "the", "Because.", "claude-opus-5-5",
    )  # fmt: skip
    assert r.status == "open"
    rows = usage(sf, job_id)
    assert [(u.pass_name, u.job_id) for u in rows] == [("ai", job_id)]
    assert cost(sf, job_id) == CALL_COST
    call = provider.calls[0]
    assert "⟦teh⟧" in call["user"] and "LanguageTool's options:\n1. tea\n" in call["user"]
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


def _context_of(user: str, tag: str) -> str | None:
    m = re.search(f'<{tag} read_only="true">\\n(.*?)\\n</{tag}>', user, re.S)
    return m.group(1) if m else None


def test_request_has_the_neighbouring_paragraphs(session_factory, tmp_storage, test_settings):
    sf = session_factory
    _, (b1, b2), job_id = book(sf, tmp_storage)
    sid = mark(sf, b2, 8, 13, "barked", job_id=job_id)
    provider = FakeProvider([answer("error")])
    with sf() as s:
        check_suggestion(s, registry(test_settings, provider), sid, actor="a")
        s.commit()
    user = provider.calls[0]["user"]
    assert _context_of(user, "previous_paragraphs") == "Katharina said teh cat sat."
    assert _context_of(user, "following_paragraphs") is None  # the last paragraph
    assert row(sf, sid).ai_verdict == "error"


DIALOGUE = (
    "# One", "Far away.", "# Two", "“Hello.”", "“Hi.”", "“Tea?”", "“Yes, teh tea.”",
    "“Sure.”", "“Thanks.”", "“Hot.”", "“Bye.”",
)  # fmt: skip


def test_short_paragraphs_give_three_sentences_each_side_within_the_chapter(
    session_factory, tmp_storage, test_settings
):
    sf = session_factory
    _, blocks, job_id = book(sf, tmp_storage, DIALOGUE)
    target = next(b for b in blocks if "teh" in b.text)
    sid = mark(sf, target, 7, 10, "the", job_id=job_id)
    provider = FakeProvider([answer()])
    with sf() as s:
        check_suggestion(s, registry(test_settings, provider), sid, actor="a")
        s.commit()
    user = provider.calls[0]["user"]
    before = _context_of(user, "previous_paragraphs")
    after = _context_of(user, "following_paragraphs")
    assert before == "“Hello.”\n\n“Hi.”\n\n“Tea?”"  # reading order; not the heading "Two"
    assert after == "“Sure.”\n\n“Thanks.”\n\n“Hot.”"
    assert "Far away." not in user  # another chapter


def test_chapter_check_gives_each_paragraph_its_own_context(
    session_factory, tmp_storage, test_settings
):
    sf = session_factory
    book_id, blocks, job_id = book(sf, tmp_storage, DIALOGUE)
    first = next(b for b in blocks if b.text == "“Hi.”")
    second = next(b for b in blocks if "teh" in b.text)
    mark(sf, first, 1, 3, "Hey", job_id=job_id)
    mark(sf, second, 7, 10, "the", job_id=job_id)
    provider = FakeProvider([answer(), answer()])
    with sf() as s:
        check_chapter(s, registry(test_settings, provider), book_id, first.chapter_id, actor="a")
        s.commit()
    one, two = (c["user"] for c in provider.calls)
    assert _context_of(one, "previous_paragraphs") == "Two\n\n“Hello.”"
    assert _context_of(one, "following_paragraphs") == "“Tea?”\n\n“Yes, teh tea.”\n\n“Sure.”"
    assert _context_of(two, "previous_paragraphs") == "“Hello.”\n\n“Hi.”\n\n“Tea?”"


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"status": "accepted"}, "not open"),
        ({"source": "model"}, "LanguageTool alone"),
        ({"agreed": True}, "LanguageTool alone"),
        ({"ai_verdict": "error", "ai_reason": "r"}, "already"),
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
    reply = answer("error")

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
    assert (r.status, r.ai_verdict) == ("accepted", "error")


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
    provider = FakeProvider([{"verdicts": []}, answer("error")])
    with sf() as s:
        got = check_chapter(s, registry(test_settings, provider), book_id, b1.chapter_id, actor="a")
        s.commit()
        checked = [r.id for r in got.checked]
    assert checked == [good]
    assert (got.remaining, got.failed) == (0, 1)
    assert row(sf, bad).ai_verdict is None and row(sf, good).ai_verdict == "error"
    assert len(usage(sf, job_id)) == 2


def test_chapter_of_another_book_is_not_found(session_factory, tmp_storage, test_settings):
    sf = session_factory
    book_id, (b1, _), _ = book(sf, tmp_storage)
    other_id, _, _ = book(sf, tmp_storage)
    with sf() as s:
        chapter = s.scalars(select(Chapter.id).where(Chapter.book_id == other_id)).first()
        with pytest.raises(NotFound):
            check_chapter(s, registry(test_settings, FakeProvider([])), book_id, chapter, actor="a")


def running_job(sf, book_id):
    with sf() as s:
        s.add(
            Job(
                book_id=book_id, type="proofread", status="running", passes=["ai"],
                max_cost=Decimal(1),
            )
        )  # fmt: skip
        s.commit()


def test_refused_while_a_job_runs_on_the_book(session_factory, tmp_storage, test_settings):
    sf = session_factory
    book_id, (b1, _), job_id = book(sf, tmp_storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    running_job(sf, book_id)
    provider = FakeProvider([])
    reg = registry(test_settings, provider)
    with sf() as s:
        with pytest.raises(Conflict, match="active job"):
            check_suggestion(s, reg, sid, actor="a")
        with pytest.raises(Conflict, match="active job"):
            check_chapter(s, reg, book_id, b1.chapter_id, actor="a")
    assert provider.calls == []


def test_room_for_the_models_thinking(session_factory, tmp_storage, test_settings):
    sf = session_factory
    _, (b1, _), job_id = book(sf, tmp_storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    provider = FakeProvider([answer()])
    with sf() as s:
        check_suggestion(s, registry(test_settings, provider), sid, actor="a")
    assert provider.calls[0]["max_tokens"] >= 16000


def test_chapter_locks_no_paragraph_during_later_calls(session_factory, tmp_storage, test_settings):
    sf = session_factory
    book_id, (b1, b2), job_id = book(sf, tmp_storage)
    first = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    mark(sf, b2, 8, 13, "barked", job_id=job_id)
    errors = []
    reply = answer()

    def teammate_accepts_the_first(kwargs):
        try:
            with sf() as other:  # would wait on paragraph 1's lock if the check held it
                other.execute(text("SET LOCAL lock_timeout = '2s'"))
                decide(other, first, "accept", actor="b", channel="web")
                other.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        return reply(kwargs)

    provider = FakeProvider([answer(), teammate_accepts_the_first])
    with sf() as s:
        got = check_chapter(s, registry(test_settings, provider), book_id, b1.chapter_id, actor="a")
        s.commit()
        assert len(got.checked) == 2
    assert errors == []
    r = row(sf, first)
    assert (r.status, r.ai_verdict) == ("accepted", "not_an_error")


def test_chapter_keeps_verdicts_when_a_later_call_errors(
    session_factory, tmp_storage, test_settings
):
    sf = session_factory
    book_id, (b1, b2), job_id = book(sf, tmp_storage)
    good = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    lost = mark(sf, b2, 8, 13, "barked", job_id=job_id)
    provider = FakeProvider([answer(), RuntimeError("connection reset")])
    with sf() as s:
        got = check_chapter(s, registry(test_settings, provider), book_id, b1.chapter_id, actor="a")
        s.commit()
        assert ([r.id for r in got.checked], got.failed) == ([good], 1)
    assert row(sf, good).ai_verdict == "not_an_error" and row(sf, lost).ai_verdict is None


def test_chapter_stops_at_its_time_budget(session_factory, tmp_storage, test_settings):
    sf = session_factory
    book_id, (b1, b2), job_id = book(sf, tmp_storage)
    mark(sf, b1, 15, 18, "tea", job_id=job_id)
    mark(sf, b2, 8, 13, "barked", job_id=job_id)
    ticks = iter([0.0, 0.0])
    provider = FakeProvider([answer()])
    with sf() as s:
        got = check_chapter(
            s, registry(test_settings, provider), book_id, b1.chapter_id, actor="a",
            clock=lambda: next(ticks, 1000.0),
        )  # fmt: skip
        s.commit()
        assert (len(got.checked), got.remaining, got.failed) == (1, 1, 0)
    assert len(provider.calls) == 1


def test_second_check_of_a_chapter_at_once_conflicts(session_factory, tmp_storage, test_settings):
    sf = session_factory
    book_id, (b1, _), job_id = book(sf, tmp_storage)
    mark(sf, b1, 15, 18, "tea", job_id=job_id)
    seen = []
    reply = answer()
    reg = registry(test_settings, FakeProvider([]))

    def another_click(kwargs):
        with sf() as other:
            try:
                check_chapter(other, reg, book_id, b1.chapter_id, actor="b")
            except Conflict as exc:
                seen.append(str(exc))
        return reply(kwargs)

    reg.providers["fake"].responses = [another_click]
    with sf() as s:
        check_chapter(s, reg, book_id, b1.chapter_id, actor="a")
        s.commit()
    assert len(seen) == 1 and "already running" in seen[0]


def test_a_mark_already_being_checked_is_not_asked_twice(
    session_factory, tmp_storage, test_settings
):
    sf = session_factory
    book_id, (b1, _), job_id = book(sf, tmp_storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    other = mark(sf, b1, 0, 9, "Catherine", job_id=job_id)
    seen = {}
    reply = answer()
    reg = registry(test_settings, FakeProvider([]))

    def others_click(kwargs):
        with sf() as s2:
            for name, fn in (
                ("same mark", lambda: check_suggestion(s2, reg, sid, actor="b")),
                ("chapter", lambda: check_chapter(s2, reg, book_id, b1.chapter_id, actor="b")),
            ):
                try:
                    fn()
                except Conflict as exc:
                    seen[name] = str(exc)
        with sf() as s3:  # another mark of the chapter may be checked at the same time
            check_suggestion(s3, reg, other, actor="c")
            s3.commit()
        return reply(kwargs)

    reg.providers["fake"].responses = [others_click, answer()]
    with sf() as s:
        check_suggestion(s, reg, sid, actor="a")
        s.commit()
    assert "already being checked" in seen["same mark"]
    assert "already running" in seen["chapter"]
    assert row(sf, sid).ai_verdict == "not_an_error" and row(sf, other).ai_verdict
    assert len(usage(sf, job_id)) == 2


def test_a_mark_is_not_asked_alone_while_its_chapter_is_checked(
    session_factory, tmp_storage, test_settings
):
    sf = session_factory
    book_id, (b1, _), job_id = book(sf, tmp_storage)
    sid = mark(sf, b1, 15, 18, "tea", job_id=job_id)
    seen = []
    reply = answer()
    reg = registry(test_settings, FakeProvider([]))

    def single_click(kwargs):
        with sf() as s2:
            try:
                check_suggestion(s2, reg, sid, actor="b")
            except Conflict as exc:
                seen.append(str(exc))
        return reply(kwargs)

    reg.providers["fake"].responses = [single_click]
    with sf() as s:
        check_chapter(s, reg, book_id, b1.chapter_id, actor="a")
        s.commit()
    assert len(seen) == 1 and "already running" in seen[0]
    assert len(usage(sf, job_id)) == 1
