from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jerome.core.books import get_book
from jerome.core.translation import ensure_not_source
from jerome.errors import Conflict, NotFound
from jerome.jobs.estimate import (
    ALL_PASSES,
    PassName,
    estimate_job,
    estimate_translation,
    included_chapter_ids,
    pending_chapter_ids,
)
from jerome.models import Registry
from jerome.orm import ACTIVE_JOB_STATUSES, Block, Job, JobTask
from jerome.publish.build import PublishFormat, check_publishable

RESUMABLE = ("cancelled", "failed", "paused_cost_cap")


def get_job(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise NotFound(f"job {job_id}")
    return job


def _lock_job(session: Session, job_id: int) -> Job:
    """The job row, locked and refreshed, so status changes serialize with the worker."""
    stmt = select(Job).where(Job.id == job_id).with_for_update()
    job = session.scalars(stmt.execution_options(populate_existing=True)).one_or_none()
    if job is None:
        raise NotFound(f"job {job_id}")
    return job


def _already_active(book_id: int, what: str = "an active proofreading job") -> Conflict:
    return Conflict(f"book {book_id} already has {what}")


_ACTIVE_WHAT = {
    "proofread": "an active proofreading job",
    "translate": "an active job",
    "publish": "an active job making a finished file",
}


def _active_conflict(session: Session, book_id: int, *, exclude: int | None = None):
    """The Conflict for the book's one active job slot, naming the job that holds it."""
    stmt = select(Job.type).where(Job.book_id == book_id, Job.status.in_(ACTIVE_JOB_STATUSES))
    if exclude is not None:
        stmt = stmt.where(Job.id != exclude)
    kind = session.scalars(stmt).first()
    return (
        None if kind is None else _already_active(book_id, _ACTIVE_WHAT.get(kind, "an active job"))
    )


def create_job(
    session: Session,
    registry: Registry,
    book_id: int,
    *,
    passes: Sequence[PassName] = ALL_PASSES,
    chapter_ids: Sequence[int] | None = None,
    max_cost: Decimal,
    callback_url: str | None,
    actor: str,
) -> Job:
    ensure_not_source(session, book_id)
    if get_book(session, book_id).work == "translate" and _has_pending(session, book_id):
        raise Conflict("translate the book before proofreading it")
    if (conflict := _active_conflict(session, book_id)) is not None:
        raise conflict
    included = included_chapter_ids(session, book_id, chapter_ids)
    if not included:  # no task to claim: the job would sit queued and hold the book's slot
        raise Conflict(f"book {book_id} has no chapters to proofread in this job")
    estimate = estimate_job(session, registry, book_id, passes=passes, chapter_ids=chapter_ids)
    job = Job(
        book_id=book_id,
        type="proofread",
        status="queued",
        passes=list(passes),
        chapter_ids=None if chapter_ids is None else list(chapter_ids),
        max_cost=max_cost,
        estimate=estimate.to_json(),
        callback_url=callback_url,
        created_by=actor,
    )
    for chapter_id in included:
        job.tasks.append(JobTask(kind="chapter", chapter_id=chapter_id))
    if "consistency" in passes:
        job.tasks.append(JobTask(kind="consistency"))
    return _add(session, job)


def _add(session: Session, job: Job) -> Job:
    try:
        with session.begin_nested():
            session.add(job)
    except IntegrityError as exc:  # lost a race for the one-active-job slot
        what = "an active proofreading job" if job.type == "proofread" else "an active job"
        raise _already_active(job.book_id, what) from exc
    session.refresh(job)
    return job


def _has_pending(session: Session, book_id: int) -> bool:
    stmt = select(Block.id).where(Block.book_id == book_id, Block.translation_status == "pending")
    return session.scalars(stmt.limit(1)).first() is not None


def create_translation_job(
    session: Session,
    registry: Registry,
    book_id: int,
    *,
    max_cost: Decimal,
    callback_url: str | None,
    actor: str,
) -> Job:
    """A job that translates every pending block, one task per chapter that has any."""
    ensure_not_source(session, book_id)
    if get_book(session, book_id).work != "translate":
        raise Conflict(f"book {book_id} is not a translation project")
    chapters = pending_chapter_ids(session, book_id)
    if not chapters:
        raise Conflict(f"book {book_id} has nothing left to translate")
    if (conflict := _active_conflict(session, book_id)) is not None:
        raise conflict
    estimate = estimate_translation(session, registry, book_id)
    job = Job(
        book_id=book_id,
        type="translate",
        status="queued",
        passes=["translate"],
        chapter_ids=None,
        max_cost=max_cost,
        estimate=estimate.to_json(),
        callback_url=callback_url,
        created_by=actor,
    )
    for chapter_id in chapters:
        job.tasks.append(JobTask(kind="chapter", chapter_id=chapter_id))
    return _add(session, job)


def create_publish_job(
    session: Session,
    book_id: int,
    *,
    fmt: PublishFormat,
    callback_url: str | None,
    actor: str,
) -> Job:
    """A job that makes one finished file (``fmt``). It costs no model money."""
    check_publishable(session, book_id)
    if (conflict := _active_conflict(session, book_id)) is not None:
        raise conflict
    job = Job(
        book_id=book_id,
        type="publish",
        status="queued",
        passes=[fmt],
        chapter_ids=None,
        max_cost=Decimal(0),
        estimate=None,
        callback_url=callback_url,
        created_by=actor,
    )
    job.tasks.append(JobTask(kind="publish"))
    return _add(session, job)


def cancel_job(session: Session, job_id: int) -> Job:
    job = _lock_job(session, job_id)
    if job.status not in ACTIVE_JOB_STATUSES:
        raise Conflict(f"job {job_id} is {job.status} and cannot be cancelled")
    job.status = "cancelled"
    job.finished_at = datetime.now(UTC)
    session.flush()
    return job


def resume_job(session: Session, job_id: int, *, max_cost: Decimal | None = None) -> Job:
    job = _lock_job(session, job_id)
    if job.status == "running":
        raise Conflict(
            "job is running; if no worker is processing it (e.g. after a crash), "
            "cancel it first, then resume"
        )
    if job.status not in RESUMABLE:
        raise Conflict(f"job {job_id} is {job.status} and cannot be resumed")
    if (conflict := _active_conflict(session, job.book_id, exclude=job.id)) is not None:
        raise conflict
    tasks = session.scalars(
        select(JobTask)
        .where(JobTask.job_id == job.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    pending = 0
    for task in tasks:
        if task.status != "done":  # queued, failed, or left running by a cancelled job's worker
            task.status = "queued"
            task.attempts = 0
            task.not_before = None
            task.error = None
            pending += 1
    if max_cost is not None:
        job.max_cost = max_cost
    if pending:
        job.status = "queued"
        job.finished_at = None
    else:  # e.g. cancelled while its last chunk was in flight: nothing is left to claim
        job.status = "completed"
        job.finished_at = datetime.now(UTC)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        raise _already_active(job.book_id) from exc
    session.refresh(job)
    return job
