from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from jerome.api.routes_suggestions import suggestion_out
from jerome.core.suggestions import text_with_changes
from jerome.errors import NotFound
from jerome.orm import (
    ACTIVE_JOB_STATUSES,
    Block,
    Book,
    Chapter,
    Decision,
    Job,
    JobTask,
    StyleSheetRow,
    Suggestion,
)

Stage = Literal["set_up", "proofreading", "needs_attention", "to_review", "done"]
_DECIDED_BY_TEAM = ("accepted", "rejected", "edited")


def derive_stage(
    *, active_job: bool, latest_job_status: str | None, open_count: int, completed_jobs: int
) -> Stage:
    """The book's stage, from the first row that applies. A job paused at its cost cap is in
    ACTIVE_JOB_STATUSES (it holds the book's one active slot) but nothing is running."""
    if active_job:
        return "proofreading"
    if latest_job_status in ("failed", "paused_cost_cap"):
        return "needs_attention"
    if open_count > 0:
        return "to_review"
    if completed_jobs > 0:
        return "done"
    return "set_up"


def _latest_channel() -> Select:
    """(suggestion_id, channel) of the latest decision on each suggestion that is not system
    bookkeeping: the same rule as is_auto_applied."""
    return (
        select(Decision.suggestion_id, Decision.channel)
        .where(Decision.channel != "system")
        .ext(distinct_on(Decision.suggestion_id))
        .order_by(Decision.suggestion_id, Decision.id.desc())
    )


def _count_columns(latest) -> tuple[Any, Any, Any]:
    """open, auto-applied and decided-by-the-team counts; superseded counts nowhere."""
    is_auto = (Suggestion.status == "accepted") & (latest.c.channel == "auto")
    return (
        func.count().filter(Suggestion.status == "open"),
        func.count().filter(is_auto),
        func.count().filter(Suggestion.status.in_(_DECIDED_BY_TEAM) & ~is_auto),
    )


def _activity(actor: str | None, at: datetime | None, channel: str | None) -> dict | None:
    """``channel`` is where a decision came from ("mcp" for Janus); None for a job start."""
    return None if at is None else {"actor": actor, "at": at, "channel": channel}


def list_projects(session: Session) -> list[dict]:
    """Every book with its stage and progress, newest activity first, in a fixed number of
    queries however many books there are."""
    books = session.execute(
        select(Book.id, Book.title, Book.format, StyleSheetRow.variant, Book.created_at)
        .outerjoin(StyleSheetRow, StyleSheetRow.book_id == Book.id)
        .where(Book.is_source.is_(False))
    ).all()  # fmt: skip

    latest = _latest_channel().subquery()
    o, a, d = _count_columns(latest)
    counts = {
        r.book_id: (r[1], r[2], r[3])
        for r in session.execute(
            select(Suggestion.book_id, o, a, d)
            .outerjoin(latest, latest.c.suggestion_id == Suggestion.id)
            .group_by(Suggestion.book_id)
        )
    }

    last_decision = {
        r.book_id: (r.actor, r.created_at, r.channel)
        for r in session.execute(
            select(Suggestion.book_id, Decision.actor, Decision.created_at, Decision.channel)
            .join(Suggestion, Suggestion.id == Decision.suggestion_id)
            .where(Decision.channel != "system")
            .ext(distinct_on(Suggestion.book_id))
            .order_by(Suggestion.book_id, Decision.created_at.desc(), Decision.id.desc())
        )
    }

    latest_job = {
        r.book_id: r
        for r in session.execute(
            select(Job.book_id, Job.status, Job.created_by, Job.created_at)
            .ext(distinct_on(Job.book_id))
            .order_by(Job.book_id, Job.created_at.desc(), Job.id.desc())
        )
    }
    completed = {
        book_id: n
        for book_id, n in session.execute(
            select(Job.book_id, func.count()).where(Job.status == "completed").group_by(Job.book_id)
        )
    }

    newest_active = (
        select(Job.id, Job.book_id, Job.status, Job.cost_actual, Job.max_cost)
        .where(Job.status.in_(ACTIVE_JOB_STATUSES))
        .ext(distinct_on(Job.book_id))
        .order_by(Job.book_id, Job.created_at.desc(), Job.id.desc())
        .subquery()
    )
    active = {
        r.book_id: r
        for r in session.execute(
            select(
                newest_active,
                func.count(JobTask.id).filter(JobTask.status == "done").label("done"),
                func.count(JobTask.id).label("total"),
            )
            .outerjoin(
                JobTask, (JobTask.job_id == newest_active.c.id) & (JobTask.kind == "chapter")
            )
            .group_by(*newest_active.c)
        )
    }

    out = []
    for b in books:
        open_n, auto_n, decided_n = counts.get(b.id, (0, 0, 0))
        job = latest_job.get(b.id)
        act = active.get(b.id)
        candidates = []
        if b.id in last_decision:
            candidates.append(last_decision[b.id])
        if job is not None:
            candidates.append((job.created_by, job.created_at, None))
        last = max(candidates, key=lambda c: c[1]) if candidates else None
        out.append(
            {
                "id": b.id,
                "title": b.title,
                "format": b.format,
                "variant": b.variant,
                "stage": derive_stage(
                    active_job=act is not None and act.status in ("queued", "running"),
                    latest_job_status=job.status if job else None,
                    open_count=open_n,
                    completed_jobs=completed.get(b.id, 0),
                ),
                "open_count": open_n,
                "auto_applied_count": auto_n,
                "decided_count": decided_n,
                "active_job": None
                if act is None
                else {
                    "id": act.id,
                    "status": act.status,
                    "chapters_done": act.done,
                    "chapters_total": act.total,
                    "cost_actual": str(act.cost_actual),
                    "max_cost": str(act.max_cost),
                },
                "last_activity": _activity(*last) if last else None,
                "created_at": b.created_at,
            }
        )
    out.sort(
        key=lambda p: (
            p["last_activity"]["at"] if p["last_activity"] else p["created_at"],
            p["id"],
        ),
        reverse=True,
    )
    return out


def chapter_summary(session: Session, book_id: int) -> list[dict]:
    if session.get(Book, book_id) is None:
        raise NotFound(f"book {book_id}")
    latest = _latest_channel().subquery()
    o, a, d = _count_columns(latest)
    stmt = (
        select(Chapter.id, Chapter.ordinal, Chapter.title, Chapter.proofread, o, a, d)
        .where(Chapter.book_id == book_id)
        .outerjoin(Block, Block.chapter_id == Chapter.id)
        .outerjoin(Suggestion, Suggestion.block_id == Block.id)
        .outerjoin(latest, latest.c.suggestion_id == Suggestion.id)
        .group_by(Chapter.id)
        .order_by(Chapter.ordinal, Chapter.id)
    )
    return [
        {
            "chapter_id": r.id,
            "ordinal": r.ordinal,
            "title": r.title,
            "proofread": r.proofread,
            "open": r[4],
            "auto_applied": r[5],
            "decided": r[6],
        }
        for r in session.execute(stmt)
    ]


def chapter_review(session: Session, chapter_id: int, offset: int, limit: int) -> dict:
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise NotFound(f"chapter {chapter_id}")
    total = session.scalar(select(func.count()).where(Block.chapter_id == chapter_id)) or 0
    blocks = list(
        session.scalars(
            select(Block)
            .where(Block.chapter_id == chapter_id)
            .order_by(Block.ordinal, Block.id)
            .offset(offset)
            .limit(limit)
        )
    )
    rows = list(
        session.scalars(
            select(Suggestion)
            .where(Suggestion.block_id.in_([b.id for b in blocks]))
            .order_by(Suggestion.start, Suggestion.id)
        )
    )
    by_block: dict[int, list[Suggestion]] = {b.id: [] for b in blocks}
    for s in rows:
        by_block[s.block_id].append(s)
    out = {s.id: o for s, o in zip(rows, suggestion_out(session, rows), strict=True)}
    return {
        "chapter_id": chapter_id,
        "book_id": chapter.book_id,
        "total_blocks": total,
        "offset": offset,
        "blocks": [
            {
                "id": b.id,
                "ordinal": b.ordinal,
                "kind": b.kind,
                "original": b.text,
                "current": text_with_changes(b.text, by_block[b.id]),
                "suggestions": [out[s.id] for s in by_block[b.id]],
            }
            for b in blocks
        ],
    }
