import pytest
from alembic.migration import MigrationContext
from sqlalchemy import delete, inspect, select, text
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


def test_check_constraints_reject_bad_vocabulary(session, book_factory):
    b = book_factory()
    _, blk = _block(session, b)
    good = dict(
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
    for field in ("category", "severity", "source"):
        sp = session.begin_nested()
        session.add(Suggestion(**{**good, field: "bogus"}))
        with pytest.raises(IntegrityError):
            session.flush()
        sp.rollback()

    s = Suggestion(**good)
    session.add(s)
    session.flush()
    for field in ("previous_status", "new_status"):
        args = dict(
            suggestion_id=s.id,
            action="accept",
            previous_status="open",
            new_status="accepted",
            actor="a",
            channel="api",
        )
        args[field] = "bogus"
        sp = session.begin_nested()
        session.add(Decision(**args))
        with pytest.raises(IntegrityError):
            session.flush()
        sp.rollback()


def test_db_level_cascade_on_bulk_delete(session, book_factory):
    b = book_factory()
    _, blk = _block(session, b)
    session.add(Segment(block_id=blk.id, ordinal=0, start=0, end=5))
    job = Job(book_id=b.id, type="proofread", status="queued", max_cost=1, passes=[])
    session.add(job)
    session.flush()
    session.add(JobTask(job_id=job.id, kind="consistency"))
    session.flush()
    session.execute(delete(Book).where(Book.id == b.id))
    session.expire_all()
    for model in (Chapter, Block, Segment, StyleSheetRow, Job, JobTask):
        assert session.scalars(select(model)).first() is None


def test_book_delete_does_not_load_children(session, book_factory):
    b = book_factory()
    _, blk = _block(session, b)
    session.add(Segment(block_id=blk.id, ordinal=0, start=0, end=5))
    session.flush()
    session.expire_all()
    book = session.get(Book, b.id)
    session.delete(book)
    assert "chapters" not in book.__dict__  # passive_deletes: children stay unloaded
    session.flush()
    assert session.scalars(select(Segment)).first() is None


def test_migration_0003_adds_web_interface_columns(engine):
    insp = inspect(engine)
    assert {"users", "user_sessions"} <= set(insp.get_table_names())
    cols = {c["name"] for c in insp.get_columns("suggestions")}
    assert {"agreed", "disputed", "alternative", "rule"} <= cols
    assert "auto_apply" in {c["name"] for c in insp.get_columns("books")}


def test_books_without_auto_apply_get_it_off(session):
    book_id = session.execute(
        text("INSERT INTO books (title, format) VALUES ('Legacy', 'docx') RETURNING id")
    ).scalar_one()
    stored = session.execute(
        text("SELECT auto_apply FROM books WHERE id = :id"), {"id": book_id}
    ).scalar_one()
    assert stored == {
        "enabled": False,
        "categories": ["punctuation", "typo"],
        "min_severity": "high",
        "house_rules": True,
    }


def test_decision_channel_allows_web_and_auto(session, book_factory):
    b = book_factory()
    _, blk = _block(session, b)
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
    for channel in ("web", "auto"):
        session.add(
            Decision(
                suggestion_id=s.id,
                action="accept",
                previous_status="open",
                new_status="accepted",
                actor="a",
                channel=channel,
            )
        )
        session.flush()


def test_migration_0005_makes_existing_users_super_admins(engine):
    from alembic import command

    from tests.conftest import alembic_config

    cfg = alembic_config()
    try:
        command.downgrade(cfg, "0004")
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO users (email, name, password_hash) "
                    "VALUES ('old@x.org', 'Old', 'h')"
                )
            )
        command.upgrade(cfg, "head")
        with engine.begin() as c:
            assert c.scalar(text("SELECT role FROM users WHERE email = 'old@x.org'")) == (
                "super_admin"
            )
            c.execute(
                text(
                    "INSERT INTO users (email, name, password_hash) "
                    "VALUES ('new@x.org', 'New', 'h')"
                )
            )
            assert c.scalar(text("SELECT role FROM users WHERE email = 'new@x.org'")) == "member"
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO users (email, name, password_hash, role) "
                    "VALUES ('bad@x.org', 'Bad', 'h', 'owner')"
                )
            )
    finally:
        command.upgrade(cfg, "head")
        with engine.begin() as c:
            c.execute(text("DELETE FROM users WHERE email IN ('old@x.org', 'new@x.org')"))


def _committed_suggestion(session_factory) -> int:
    with session_factory() as s, s.begin():
        b = Book(title="T", format="docx")
        b.style_sheet = StyleSheetRow(
            variant="en-US", quote_style="“”‘’", house_spellings={}, ignore_words=[]
        )
        s.add(b)
        s.flush()
        _, blk = _block(s, b)
        sug = Suggestion(
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
        s.add(sug)
        s.flush()
        return sug.id


_INSERT_DECISION = (
    "INSERT INTO decisions (suggestion_id, action, previous_status, new_status, actor, channel) "
    "VALUES (:sid, 'accept', 'open', 'accepted', 'a', :channel)"
)
_INSERT_ACTION = (
    "INSERT INTO agent_actions (actor, channel, tool, summary) "
    "VALUES ('janus', 'mcp', 'list_books', 'listed')"
)


def test_migration_0006_adds_mcp_channel_and_tables(engine, session_factory):
    sid = _committed_suggestion(session_factory)
    with engine.begin() as c:
        c.execute(text(_INSERT_DECISION), {"sid": sid, "channel": "mcp"})
        c.execute(text(_INSERT_ACTION))
        c.execute(
            text(
                "INSERT INTO transfer_links (token_hash, kind, actor, expires_at) "
                "VALUES ('abc', 'upload', 'janus', now())"
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(text(_INSERT_DECISION), {"sid": sid, "channel": "bogus"})
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO transfer_links (token_hash, kind, actor, expires_at) "
                "VALUES ('def', 'sideways', 'janus', now())"
            )
        )


def test_migration_0006_downgrade_refuses_with_mcp_data(engine, session_factory):
    from alembic import command

    from tests.conftest import alembic_config

    sid = _committed_suggestion(session_factory)
    cfg = alembic_config()
    try:
        with engine.begin() as c:
            c.execute(text(_INSERT_DECISION), {"sid": sid, "channel": "mcp"})
        with pytest.raises(Exception, match="mcp"):
            command.downgrade(cfg, "0005")
        with engine.begin() as c:
            c.execute(text("DELETE FROM decisions WHERE channel = 'mcp'"))
            c.execute(text(_INSERT_ACTION))
        with pytest.raises(Exception, match="mcp"):
            command.downgrade(cfg, "0005")
        with engine.begin() as c:
            c.execute(text("DELETE FROM agent_actions"))
        command.downgrade(cfg, "0005")
        tables = set(inspect(engine).get_table_names())
        assert "agent_actions" not in tables
        assert "transfer_links" not in tables
    finally:
        command.upgrade(cfg, "head")


def test_publish_task_kind_and_trim_size(session, book_factory):
    b = book_factory()
    assert b.trim_size == "6x9in" and b.authors == []
    job = Job(book_id=b.id, type="publish", status="queued", max_cost=0, passes=["pdf-book"])
    job.tasks.append(JobTask(kind="publish"))
    session.add(job)
    session.flush()
    b.trim_size = "a4"
    with pytest.raises(IntegrityError):
        session.flush()
