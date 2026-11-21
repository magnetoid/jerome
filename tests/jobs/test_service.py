from decimal import Decimal
from math import ceil

import pytest

from jerome.core.books import block_texts
from jerome.core.style_sheets import get_style_sheet
from jerome.errors import Conflict, NotFound
from jerome.jobs.estimate import ALL_PASSES, CostRange, estimate_job
from jerome.jobs.service import cancel_job, create_job, resume_job
from jerome.models import DEFAULT_PRICES, Usage, cost
from jerome.orm import Chapter
from jerome.proofread.ai_pass import build_request, chunk_blocks

PRICE = DEFAULT_PRICES["claude-opus-5-5"]


def make(session, registry, book, **kw):
    args = dict(
        passes=ALL_PASSES, chapter_ids=None, max_cost=Decimal(5), callback_url=None, actor="a"
    )
    args.update(kw)
    return create_job(session, registry, book.id, **args)


def test_estimate_counts_chunks_and_15pct_output(
    session, registry_fake_counts_1000, book_6000_words
):
    e = estimate_job(session, registry_fake_counts_1000, book_6000_words.id)
    assert e.chunks == 3 and e.per_pass["languagetool"] == CostRange(Decimal(0), Decimal(0))
    assert e.per_pass["consistency"] == CostRange(Decimal(0), Decimal(0))
    assert e.per_pass["ai"].high == 3 * cost(PRICE, Usage(1000, 150))
    assert e.total == e.per_pass["ai"]


def test_estimate_low_is_below_high_for_multi_chunk(
    session, registry_fake_counts_1000, book_6000_words
):
    e = estimate_job(session, registry_fake_counts_1000, book_6000_words.id)
    # fake count is 1000 for system alone too, so sys is clamped to input: later chunks
    # price all input at the cache-read rate.
    first = cost(PRICE, Usage(1000, 150))
    later = cost(PRICE, Usage(0, 150, cache_read_tokens=1000))
    assert e.per_pass["ai"].low == first + 2 * later
    assert e.per_pass["ai"].low < e.per_pass["ai"].high


def test_estimate_falls_back_to_chars_per_token(session, registry_no_count, book):
    e = estimate_job(session, registry_no_count, book.id, passes=["ai"])
    chunk = chunk_blocks(block_texts(session, book.id))[0]
    system, user = build_request(get_style_sheet(session, book.id), chunk)
    chars = sum(len(p.text) for p in system) + len(user)
    tokens = ceil(chars / 3.5)
    assert e.chunks == 1
    assert e.per_pass["ai"].high == cost(PRICE, Usage(tokens, ceil(tokens * 0.15)))
    assert e.per_pass["ai"].low == e.per_pass["ai"].high  # single chunk: nothing cached yet


def test_estimate_fallback_known_chars(session, registry_no_count, book_factory, add_chapter):
    # 7,000 chars of request text -> 2,000 tokens priced
    b = book_factory()
    add_chapter(b, 0, ["x"])
    chunk = chunk_blocks(block_texts(session, b.id))[0]
    system, user = build_request(get_style_sheet(session, b.id), chunk)
    overhead = sum(len(p.text) for p in system) + len(user) - 1
    from jerome.orm import Block

    session.query(Block).filter_by(book_id=b.id).one().text = "x" * (7000 - overhead)
    session.flush()
    e = estimate_job(session, registry_no_count, b.id, passes=["ai"])
    assert e.per_pass["ai"].high == cost(PRICE, Usage(2000, 300))


def test_ai_cost_zero_when_local_only_has_no_local_provider(
    session, registry_local_only_unavailable, book_factory, add_chapter
):
    b = book_factory(local_only=True)
    add_chapter(b, 0, ["Some text."])
    e = estimate_job(session, registry_local_only_unavailable, b.id)
    assert e.per_pass["ai"] == CostRange(Decimal(0), Decimal(0))
    assert e.chunks == 1


def test_skipped_chapters_not_estimated_or_tasked(session, registry, book_with_front_matter):
    b = book_with_front_matter
    e = estimate_job(session, registry, b.id)
    assert e.chunks == 1
    job = make(session, registry, b)
    assert [t.kind for t in job.tasks] == ["chapter", "consistency"]
    body = session.query(Chapter).filter_by(book_id=b.id, proofread=True).one()
    assert job.tasks[0].chapter_id == body.id


def test_chapter_ids_validation(session, registry, book_with_front_matter, book_3_chapters):
    b = book_with_front_matter
    front = session.query(Chapter).filter_by(book_id=b.id, proofread=False).one()
    with pytest.raises(Conflict):
        estimate_job(session, registry, b.id, chapter_ids=[front.id])
    other = session.query(Chapter).filter_by(book_id=book_3_chapters.id).first()
    with pytest.raises(NotFound):
        estimate_job(session, registry, b.id, chapter_ids=[other.id])
    with pytest.raises(NotFound):
        estimate_job(session, registry, 999999)


def test_tasks_per_chapter_plus_consistency(session, registry, book_3_chapters):
    job = make(session, registry, book_3_chapters)
    assert sorted(t.kind for t in job.tasks) == ["chapter"] * 3 + ["consistency"]
    assert job.status == "queued" and job.max_cost == Decimal(5)
    assert job.created_by == "a"
    assert set(job.estimate["chapter_chunks"].values()) == {1}
    assert len(job.estimate["chapter_chunks"]) == 3
    assert job.estimate["per_pass"]["ai"]["high"] == str(3 * cost(PRICE, Usage(1000, 150)))


def test_one_active_job_per_book(session, registry, book):
    make(session, registry, book, max_cost=Decimal(1))
    with pytest.raises(Conflict):
        make(session, registry, book, max_cost=Decimal(1))


def test_cancel_then_resume(session, registry, book):
    job = make(session, registry, book)
    job.tasks[0].status = "failed"
    job.tasks[0].attempts = 3
    job.tasks[0].error = "boom"
    session.flush()
    assert cancel_job(session, job.id).status == "cancelled"
    assert job.finished_at is not None
    resumed = resume_job(session, job.id, max_cost=Decimal(10))
    assert resumed.status == "queued" and resumed.max_cost == Decimal(10)
    assert resumed.finished_at is None
    assert resumed.tasks[0].status == "queued"
    assert resumed.tasks[0].attempts == 0 and resumed.tasks[0].error is None


def test_cancel_from_terminal_status_conflicts(session, registry, book):
    job = make(session, registry, book)
    cancel_job(session, job.id)
    with pytest.raises(Conflict):
        cancel_job(session, job.id)
    with pytest.raises(NotFound):
        cancel_job(session, 999999)


def test_resume_completed_conflicts(session, registry, book):
    job = make(session, registry, book)
    job.status = "completed"
    session.flush()
    with pytest.raises(Conflict):
        resume_job(session, job.id)


def test_resume_while_another_job_active_conflicts(session, registry, book):
    first = make(session, registry, book)
    cancel_job(session, first.id)
    make(session, registry, book)
    with pytest.raises(Conflict):
        resume_job(session, first.id)
    assert first.status == "cancelled"


def test_resume_requeues_running_and_keeps_done(session, registry, book_3_chapters):
    job = make(session, registry, book_3_chapters)
    job.status = "failed"
    job.tasks[0].status = "done"
    job.tasks[1].status = "running"
    job.tasks[1].attempts = 2
    job.tasks[1].error = "worker died"
    job.tasks[2].status = "failed"
    session.flush()
    resumed = resume_job(session, job.id)
    assert [t.status for t in resumed.tasks] == ["done", "queued", "queued", "queued"]
    assert resumed.tasks[1].attempts == 0 and resumed.tasks[1].error is None
    assert resumed.tasks[1].not_before is None


def test_resume_keeps_max_cost_and_done_tasks(session, registry, book_3_chapters):
    job = make(session, registry, book_3_chapters)
    job.status = "paused_cost_cap"
    job.tasks[0].status = "done"
    session.flush()
    resumed = resume_job(session, job.id)
    assert resumed.max_cost == Decimal(5) and resumed.status == "queued"
    assert resumed.tasks[0].status == "done"
