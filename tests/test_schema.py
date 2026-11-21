import pytest
from alembic.migration import MigrationContext
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from jerome.orm import (
    Base,
    Block,
    Book,
    Chapter,
    Decision,
    Job,
    JobTask,
    ReviewExport,
    Segment,
    StyleSheetRow,
    Suggestion,
)


def test_migration_matches_models(engine):
    from alembic.autogenerate import compare_metadata

    with engine.connect() as c:
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []


def test_one_active_job_per_book(session, book_factory):
    b = book_factory()
    session.add_all(
        [
            Job(book_id=b.id, type="proofread", status="queued", max_cost=1, passes=[]),
            Job(book_id=b.id, type="proofread", status="running", max_cost=1, passes=[]),
        ]
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_finished_jobs_do_not_block_a_new_one(session, book_factory):
    b = book_factory()
    session.add_all(
        [
            Job(book_id=b.id, type="proofread", status="completed", max_cost=1, passes=[]),
            Job(book_id=b.id, type="proofread", status="cancelled", max_cost=1, passes=[]),
            Job(book_id=b.id, type="proofread", status="paused_cost_cap", max_cost=1, passes=[]),
        ]
    )
    session.flush()


def _block(session, book):
    ch = Chapter(book_id=book.id, ordinal=0, title="One")
    session.add(ch)
    session.flush()
    blk = Block(
        chapter_id=ch.id, book_id=book.id, ordinal=0, kind="paragraph", text="Hello", hash="h"
    )
    session.add(blk)
    session.flush()
    return ch, blk


def test_check_constraints_reject_bad_status(session, book_factory):
    b = book_factory()
    _, blk = _block(session, b)
    sp = session.begin_nested()
    session.add(Job(book_id=b.id, type="proofread", status="bogus", max_cost=1, passes=[]))
    with pytest.raises(IntegrityError):
        session.flush()
    sp.rollback()

    sp = session.begin_nested()
    session.add(
        Suggestion(
            book_id=b.id,
            block_id=blk.id,
            start=0,
            end=1,
            original="H",
            replacement="h",
            category="typo",
            severity="low",
            source="model",
            explanation="x",
            status="bogus",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    sp.rollback()

    sp = session.begin_nested()
    job = Job(book_id=b.id, type="proofread", status="queued", max_cost=1, passes=[])
    session.add(job)
    session.flush()
    session.add(JobTask(job_id=job.id, kind="chapter", status="bogus"))
    with pytest.raises(IntegrityError):
        session.flush()
    sp.rollback()

    sp = session.begin_nested()
    s = Suggestion(
        book_id=b.id,
        block_id=blk.id,
        start=0,
        end=1,
        original="H",
        replacement="h",
        category="typo",
        severity="low",
        source="model",
        explanation="x",
    )
    session.add(s)
    session.flush()
    session.add(
        Decision(
            suggestion_id=s.id,
            action="accept",
            previous_status="open",
            new_status="accepted",
            actor="a",
            channel="bogus",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    sp.rollback()


def test_defaults_applied(session, book_factory):
    b = book_factory()
    _, blk = _block(session, b)
    job = Job(book_id=b.id, type="proofread", status="queued", max_cost=1, passes=[])
    s = Suggestion(
        book_id=b.id,
        block_id=blk.id,
        start=0,
        end=1,
        original="H",
        replacement="h",
        category="typo",
        severity="low",
        source="model",
        explanation="x",
    )
    session.add_all([job, s])
    session.flush()
    session.refresh(job)
    session.refresh(s)
    assert b.created_at is not None and b.created_at.tzinfo is not None
    assert b.local_only is False
    assert job.cost_actual == 0 and job.stats == {}
    assert s.status == "open"


def test_cascade_delete_from_book(session, book_factory):
    b = book_factory()
    ch, blk = _block(session, b)
    session.add(Segment(block_id=blk.id, ordinal=0, start=0, end=5))
    session.flush()
    session.expire_all()
    session.delete(session.get(Book, b.id))
    session.flush()
    for model in (Chapter, Block, Segment, StyleSheetRow):
        assert session.scalars(select(model)).first() is None


def test_jsonb_roundtrip(session, book_factory):
    b = book_factory()
    export = ReviewExport(
        book_id=b.id,
        suggestion_statuses={"12": "open", "13": "accepted"},
        block_ids=[1, 2],
        filters={"severity": ["high"], "nested": {"a": [1, None]}},
        created_by="t",
    )
    session.add(export)
    session.flush()
    session.expire_all()
    got = session.get(ReviewExport, export.id)
    assert got.suggestion_statuses == {"12": "open", "13": "accepted"}
    assert got.filters == {"severity": ["high"], "nested": {"a": [1, None]}}
