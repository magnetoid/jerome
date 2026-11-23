from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from jerome.core.projects import chapter_review, chapter_summary, derive_stage, list_projects
from jerome.core.suggestions import decide
from jerome.errors import NotFound
from jerome.orm import Block, Chapter, Decision, Job, JobTask
from tests.fixtures.suggestions import add_one

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("active", "latest", "open_n", "completed", "stage"),
    [
        (True, "running", 5, 1, "proofreading"),
        (False, "failed", 5, 1, "needs_attention"),
        (False, "completed", 2, 1, "to_review"),
        (False, "completed", 0, 1, "done"),
        (False, None, 0, 0, "set_up"),
        (False, "cancelled", 0, 0, "set_up"),
        (False, "paused_cost_cap", 3, 0, "needs_attention"),
    ],
)
def test_derive_stage_table(active, latest, open_n, completed, stage):
    assert (
        derive_stage(
            active_job=active, latest_job_status=latest, open_count=open_n, completed_jobs=completed
        )
        == stage
    )


@pytest.mark.parametrize(
    ("active", "latest_type", "latest", "pending", "open_n", "completed", "stage"),
    [
        (True, "translate", "running", 5, 0, 0, "translating"),
        (False, "translate", "failed", 5, 0, 0, "needs_attention"),
        (False, "translate", "paused_cost_cap", 5, 0, 0, "needs_attention"),
        (False, None, None, 5, 0, 0, "set_up"),
        (False, "translate", "cancelled", 2, 0, 0, "set_up"),
        (False, "translate", "completed", 0, 0, 1, "translated"),
        (True, "proofread", "running", 0, 0, 1, "proofreading"),
        (False, "proofread", "completed", 0, 3, 2, "to_review"),
        (False, "proofread", "completed", 0, 0, 2, "done"),
        # translated, then a proofread job ended without completing: proofreading never ran
        (False, "proofread", "cancelled", 0, 0, 0, "translated"),
    ],
)
def test_derive_stage_of_a_translated_book(
    active, latest_type, latest, pending, open_n, completed, stage
):
    assert (
        derive_stage(
            active_job=active,
            latest_job_status=latest,
            open_count=open_n,
            completed_jobs=completed,
            work="translate",
            latest_job_type=latest_type,
            pending_blocks=pending,
        )
        == stage
    )


def test_translation_stages_apply_only_to_translated_books():
    args = {"active_job": False, "latest_job_status": None, "open_count": 0, "completed_jobs": 0}
    assert derive_stage(**args, pending_blocks=5) == "set_up"
    completed = {**args, "latest_job_status": "completed", "completed_jobs": 1}
    assert derive_stage(**completed, latest_job_type="translate") == "done"


def test_projects_list_translated_book_stays_translated_until_proofreading_completes(
    session, tmp_storage
):
    from jerome.core.translation import import_for_translation
    from tests.fixtures.docx_builders import make_docx

    book = import_for_translation(
        session,
        tmp_storage,
        filename="t.docx",
        data=make_docx(["One.", "Two."]),
        source_variant="en-GB",
        target_variant="de-DE",
    )
    for block in session.query(Block).filter_by(book_id=book.id):
        block.text, block.translation_status = "Eins.", "done"
    translate = _job(session, book, "completed", T0)
    translate.type = "translate"
    session.flush()
    assert list_projects(session)[0]["stage"] == "translated"
    _job(session, book, "cancelled", T0 + timedelta(days=1))  # proofreading never completed
    (row,) = list_projects(session)
    assert (row["id"], row["stage"]) == (book.id, "translated")  # not "done"
    _job(session, book, "completed", T0 + timedelta(days=2))
    assert list_projects(session)[0]["stage"] == "done"


def _book(session, book_factory, title, chapters=1, blocks=1, created_at=None):
    book = book_factory(title=title)
    if created_at:
        book.created_at = created_at
    for c in range(chapters):
        ch = Chapter(book_id=book.id, ordinal=c, title=f"Ch {c}")
        session.add(ch)
        session.flush()
        for b in range(blocks):
            session.add(
                Block(
                    chapter_id=ch.id, book_id=book.id, ordinal=b, kind="paragraph",
                    text="teh cat sat", hash=f"h{ch.id}-{b}",
                )
            )  # fmt: skip
    session.flush()
    return book


def _blocks(session, book):
    return session.query(Block).filter_by(book_id=book.id).order_by(Block.id).all()


def _job(session, book, status, created_at, by="ann", **kw):
    job = Job(
        book_id=book.id, type="proofread", status=status, max_cost=5, passes=[],
        created_by=by, created_at=created_at, **kw,
    )  # fmt: skip
    session.add(job)
    session.flush()
    return job


def test_projects_list_one_request_shape_and_order(session, book_factory):
    # ids ascend a, b, c; activity times descend, so ordering by time is what is tested.
    a = _book(session, book_factory, "A", created_at=T0)
    b = _book(session, book_factory, "B", created_at=T0 + timedelta(days=1))
    c = _book(session, book_factory, "C", created_at=T0 + timedelta(days=2))
    _book(session, book_factory, "D", created_at=T0 + timedelta(days=3))

    # a: newest activity (a web decision, later than its job); one open, one auto, one by team
    block = _blocks(session, a)[0]
    s1 = add_one(session, block, 0, 3, "teh", "the")
    s2 = add_one(session, block, 4, 7, "cat", "cats")
    s3 = add_one(session, block, 8, 11, "sat", "sits")
    decide(session, s1.id, "accept", actor="auto", channel="auto")
    decide(session, s2.id, "reject", actor="bob", channel="web")
    session.query(Decision).update({"created_at": T0 + timedelta(days=30)})
    _job(session, a, "completed", T0 + timedelta(days=20), by="ann")
    # b: running job with 2 chapters tasks, one done
    jb = _job(session, b, "running", T0 + timedelta(days=10), by="cy", cost_actual="1.25")
    chs = session.query(Chapter).filter_by(book_id=b.id).all()
    session.add_all(
        [
            JobTask(job_id=jb.id, chapter_id=chs[0].id, kind="chapter", status="done"),
            JobTask(job_id=jb.id, chapter_id=chs[0].id, kind="chapter", status="queued"),
            JobTask(job_id=jb.id, kind="consistency", status="done"),
        ]
    )
    # c: paused at the cost cap: an active_job object, but stage needs_attention
    _job(session, c, "paused_cost_cap", T0 + timedelta(days=5), by="di")
    # d: nothing at all
    session.flush()

    rows = list_projects(session)
    assert [r["title"] for r in rows] == ["A", "B", "C", "D"]
    by = {r["title"]: r for r in rows}
    assert set(by["A"]) == {
        "id", "title", "format", "variant", "stage", "open_count", "auto_applied_count",
        "decided_count", "active_job", "last_activity", "created_at",
        "work", "source_variant", "translation",
    }  # fmt: skip
    assert (by["A"]["open_count"], by["A"]["auto_applied_count"], by["A"]["decided_count"]) == (
        1,
        1,
        1,
    )
    assert by["A"]["stage"] == "to_review" and by["A"]["variant"] == "en-US"
    assert by["A"]["last_activity"] == {
        "actor": "bob",
        "at": T0 + timedelta(days=30),
        "channel": "web",
    }
    assert by["A"]["active_job"] is None
    assert by["B"]["stage"] == "proofreading"
    assert by["B"]["active_job"] == {
        "id": jb.id, "status": "running", "chapters_done": 1, "chapters_total": 2,
        "cost_actual": "1.2500", "max_cost": "5.0000",
    }  # fmt: skip
    assert by["B"]["last_activity"] == {
        "actor": "cy",
        "at": T0 + timedelta(days=10),
        "channel": None,
    }
    assert by["C"]["stage"] == "needs_attention"
    assert by["C"]["active_job"]["status"] == "paused_cost_cap"
    assert by["D"]["last_activity"] is None and by["D"]["stage"] == "set_up"
    assert s3.status == "open"


def test_projects_list_orders_without_activity_by_created_at(session, book_factory):
    old = _book(session, book_factory, "old", created_at=T0 + timedelta(days=9))
    new = _book(session, book_factory, "new", created_at=T0 + timedelta(days=8))
    active = _book(session, book_factory, "active", created_at=T0)
    _job(session, active, "completed", T0 + timedelta(days=9, hours=1))
    assert [r["id"] for r in list_projects(session)] == [active.id, old.id, new.id]


def test_system_decisions_do_not_count_as_activity(session, book_factory):
    a = _book(session, book_factory, "A", created_at=T0)
    s = add_one(session, _blocks(session, a)[0], 0, 3, "teh", "the")
    decide(session, s.id, "accept", actor="auto", channel="auto")
    session.add(
        Decision(
            suggestion_id=s.id, action="reopen", previous_status="accepted", new_status="open",
            actor="sys", channel="system", created_at=T0 + timedelta(days=50),
        )
    )  # fmt: skip
    session.query(Decision).filter_by(channel="auto").update({"created_at": T0 + timedelta(days=1)})
    session.flush()
    assert list_projects(session)[0]["last_activity"] == {
        "actor": "auto", "at": T0 + timedelta(days=1), "channel": "auto",
    }  # fmt: skip


def _count_statements(session, fn):
    n = []
    engine = session.get_bind()
    listener = lambda *a: n.append(1)  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return len(n)


def test_projects_list_query_count_is_constant(session, book_factory):
    def add_book(i):
        b = _book(session, book_factory, f"B{i}", chapters=2)
        s = add_one(session, _blocks(session, b)[0], 0, 3, "teh", "the")
        decide(session, s.id, "accept", actor="x", channel="web")
        j = _job(session, b, "running", T0 + timedelta(days=i))
        session.add(JobTask(job_id=j.id, kind="chapter", chapter_id=None, status="done"))
        session.flush()

    add_book(0)
    one = _count_statements(session, lambda: list_projects(session))
    for i in range(1, 5):
        add_book(i)
    assert len(list_projects(session)) == 5
    five = _count_statements(session, lambda: list_projects(session))
    assert five == one


def test_chapter_summary_counts(session, book_factory):
    book = _book(session, book_factory, "A", chapters=3)
    c1, c2, c3 = session.query(Chapter).filter_by(book_id=book.id).order_by(Chapter.ordinal).all()
    c1.ordinal, c3.ordinal = 9, 0  # ordering is by ordinal, not id
    c2.proofread = True
    session.flush()
    blk = session.query(Block).filter_by(chapter_id=c2.id).one()
    s = [
        add_one(session, blk, 0, 3, "teh", "the"),
        add_one(session, blk, 4, 7, "cat", "cats"),
        add_one(session, blk, 8, 11, "sat", "sits"),
    ]
    decide(session, s[0].id, "accept", actor="auto", channel="auto")
    decide(session, s[1].id, "reject", actor="bob", channel="web")
    add_one(session, session.query(Block).filter_by(chapter_id=c1.id).one(), 0, 3, "teh", "the")

    rows = chapter_summary(session, book.id)
    assert [r["chapter_id"] for r in rows] == [c3.id, c2.id, c1.id]
    assert set(rows[0]) == {
        "chapter_id", "ordinal", "title", "proofread", "open", "auto_applied", "decided",
    }  # fmt: skip
    assert [(r["open"], r["auto_applied"], r["decided"]) for r in rows] == [
        (0, 0, 0), (1, 1, 1), (1, 0, 0),
    ]  # fmt: skip
    assert [r["proofread"] for r in rows] == [False, True, False]
    # a person reversing the AI's change moves it from auto_applied to decided
    decide(session, s[0].id, "undo", actor="bob", channel="web")
    decide(session, s[0].id, "accept", actor="bob", channel="web")
    mid = chapter_summary(session, book.id)[1]
    assert (mid["auto_applied"], mid["decided"]) == (0, 2)
    with pytest.raises(NotFound):
        chapter_summary(session, 999999)


def test_chapter_review_returns_current_text_and_suggestions(session, book_factory):
    book = _book(session, book_factory, "A", blocks=2)
    ch = session.query(Chapter).filter_by(book_id=book.id).one()
    b1, b2 = session.query(Block).filter_by(chapter_id=ch.id).order_by(Block.ordinal).all()
    late = add_one(session, b1, 8, 11, "sat", "sits")
    early = add_one(session, b1, 0, 3, "teh", "the")
    rej = add_one(session, b1, 4, 7, "cat", "cats")
    decide(session, early.id, "accept", actor="a")
    decide(session, late.id, "edit", actor="a", replacement="sat down")
    decide(session, rej.id, "reject", actor="a")
    out = chapter_review(session, ch.id, 0, 50)
    assert (out["chapter_id"], out["book_id"], out["total_blocks"], out["offset"]) == (
        ch.id, book.id, 2, 0,
    )  # fmt: skip
    first, second = out["blocks"]
    assert (first["id"], first["ordinal"], first["kind"]) == (b1.id, 0, "paragraph")
    assert first["original"] == "teh cat sat" and first["current"] == "the cat sat down"
    assert [s["id"] for s in first["suggestions"]] == [early.id, rej.id, late.id]
    assert {s["status"] for s in first["suggestions"]} == {"accepted", "rejected", "edited"}
    assert "decided_by" in first["suggestions"][0]
    assert second["id"] == b2.id and second["current"] == second["original"]
    assert second["suggestions"] == []
    with pytest.raises(NotFound):
        chapter_review(session, 999999, 0, 50)


def test_chapter_review_pages_by_paragraph(session, book_factory):
    book = _book(session, book_factory, "A", blocks=120)
    ch = session.query(Chapter).filter_by(book_id=book.id).one()
    out = chapter_review(session, ch.id, 100, 50)
    assert len(out["blocks"]) == 20 and out["total_blocks"] == 120
    assert [b["ordinal"] for b in out["blocks"]] == list(range(100, 120))


def _decide_at(session, book, n, actor, at_by_order):
    """n suggestions on the book's block, each decided by `actor` at the given time (ids
    ascend with the call order, times are whatever the caller passes)."""
    block = _blocks(session, book)[0]
    spans = [(0, 3, "teh", "the"), (4, 7, "cat", "cats"), (8, 11, "sat", "sits")]
    ids = []
    for (start, end, o, r), (who, at) in zip(spans, at_by_order, strict=False):
        s = add_one(session, block, start, end, o, r)
        decide(session, s.id, "accept", actor=who, channel="web")
        ids.append(s.id)
        session.query(Decision).filter_by(suggestion_id=s.id).update({"created_at": at})
    session.flush()
    return ids


def test_last_activity_is_latest_decision_by_time_not_id(session, book_factory):
    book = _book(session, book_factory, "A", created_at=T0)
    # the higher decision id has the older time
    _decide_at(
        session, book, 2, "", [("newer", T0 + timedelta(days=5)), ("older", T0 + timedelta(days=2))]
    )
    assert list_projects(session)[0]["last_activity"] == {
        "actor": "newer",
        "at": T0 + timedelta(days=5),
        "channel": "web",
    }


def test_last_activity_job_newer_than_decision_wins_and_vice_versa(session, book_factory):
    jobwins = _book(session, book_factory, "J", created_at=T0)
    _decide_at(session, jobwins, 1, "", [("dec", T0 + timedelta(days=2))])
    _job(session, jobwins, "completed", T0 + timedelta(days=3), by="jobber")
    declwins = _book(session, book_factory, "D", created_at=T0)
    _decide_at(session, declwins, 1, "", [("dec", T0 + timedelta(days=9))])
    _job(session, declwins, "completed", T0 + timedelta(days=4), by="jobber")
    by = {r["title"]: r["last_activity"] for r in list_projects(session)}
    assert by["J"] == {"actor": "jobber", "at": T0 + timedelta(days=3), "channel": None}
    assert by["D"] == {"actor": "dec", "at": T0 + timedelta(days=9), "channel": "web"}


def test_latest_job_and_active_job_follow_created_at_not_id(session, book_factory):
    book = _book(session, book_factory, "A", created_at=T0)
    # higher id, older time: a failed job; lower id, newer time: a completed job
    _job(session, book, "completed", T0 + timedelta(days=5), by="new")
    _job(session, book, "failed", T0 + timedelta(days=1), by="old")
    (p,) = list_projects(session)
    assert p["stage"] == "done"  # the newer job (completed) decides, not the failed higher id
    assert p["last_activity"] == {"actor": "new", "at": T0 + timedelta(days=5), "channel": None}

    # the active job is the active one even when a newer-by-time job is finished
    other = _book(session, book_factory, "B", created_at=T0)
    older_active = _job(session, other, "queued", T0 + timedelta(days=1))
    session.add(JobTask(job_id=older_active.id, kind="chapter", status="done"))
    _job(session, other, "cancelled", T0 + timedelta(days=6))
    session.flush()
    b = next(r for r in list_projects(session) if r["title"] == "B")
    assert b["active_job"]["id"] == older_active.id and b["active_job"]["chapters_done"] == 1
    # the newest job is cancelled, but a job is still active, so the book is proofreading
    assert b["stage"] == "proofreading"


def test_newer_cancelled_job_after_failed_is_not_needs_attention(session, book_factory):
    for title, open_suggestion, stage in (("todo", True, "to_review"), ("none", False, "set_up")):
        book = _book(session, book_factory, title, created_at=T0)
        _job(session, book, "failed", T0 + timedelta(days=1))
        _job(session, book, "cancelled", T0 + timedelta(days=2))
        if open_suggestion:
            add_one(session, _blocks(session, book)[0], 0, 3, "teh", "the")
        got = next(r for r in list_projects(session) if r["title"] == title)
        assert got["stage"] == stage
    done = _book(session, book_factory, "done", created_at=T0)
    _job(session, done, "completed", T0 + timedelta(days=0, hours=1))
    _job(session, done, "failed", T0 + timedelta(days=1))
    _job(session, done, "cancelled", T0 + timedelta(days=2))
    assert next(r for r in list_projects(session) if r["title"] == "done")["stage"] == "done"


def test_project_order_is_by_activity_time_opposite_to_id(session, book_factory):
    low = _book(session, book_factory, "low", created_at=T0)  # lower id, newest activity
    high = _book(session, book_factory, "high", created_at=T0 + timedelta(days=1))
    _job(session, low, "completed", T0 + timedelta(days=10))
    _job(session, high, "completed", T0 + timedelta(days=3))
    quiet_new = _book(session, book_factory, "quiet", created_at=T0 + timedelta(days=5))
    assert low.id < high.id < quiet_new.id
    assert [r["title"] for r in list_projects(session)] == ["low", "quiet", "high"]


def test_projects_list_carries_the_channel_of_a_janus_decision(session, book_factory):
    book = _book(session, book_factory, "J", created_at=T0)
    s1 = add_one(session, _blocks(session, book)[0], 0, 3, "teh", "the")
    decide(session, s1.id, "accept", actor="janus-desktop", channel="mcp")
    session.flush()
    assert list_projects(session)[0]["last_activity"]["channel"] == "mcp"
    assert list_projects(session)[0]["last_activity"]["actor"] == "janus-desktop"
