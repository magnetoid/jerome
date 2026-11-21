from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jerome.errors import Conflict, NotFound
from jerome.jobs.estimate import ALL_PASSES, PassName, estimate_job, included_chapter_ids
from jerome.models import Registry
from jerome.orm import ACTIVE_JOB_STATUSES, Job, JobTask

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


def _active_job_id(session: Session, book_id: int, *, exclude: int | None = None) -> int | None:
    stmt = select(Job.id).where(Job.book_id == book_id, Job.status.in_(ACTIVE_JOB_STATUSES))
    if exclude is not None:
        stmt = stmt.where(Job.id != exclude)
    return session.scalars(stmt).first()


def _already_active(book_id: int) -> Conflict:
    return Conflict(f"book {book_id} already has an active proofreading job")


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
    if _active_job_id(session, book_id) is not None:
        raise _already_active(book_id)
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
    for chapter_id in included_chapter_ids(session, book_id, chapter_ids):
        job.tasks.append(JobTask(kind="chapter", chapter_id=chapter_id))
    if "consistency" in passes:
        job.tasks.append(JobTask(kind="consistency"))
    try:
        with session.begin_nested():
            session.add(job)
    except IntegrityError as exc:  # lost a race for the one-active-job slot
        raise _already_active(book_id) from exc
    session.refresh(job)
    return job


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
    if job.status not in RESUMABLE:
        raise Conflict(f"job {job_id} is {job.status} and cannot be resumed")
    if _active_job_id(session, job.book_id, exclude=job.id) is not None:
        raise _already_active(job.book_id)
    tasks = session.scalars(
        select(JobTask)
        .where(JobTask.job_id == job.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    for task in tasks:
        if task.status != "done":  # queued, failed, or left running by a dead worker
            task.status = "queued"
            task.attempts = 0
            task.not_before = None
            task.error = None
    if max_cost is not None:
        job.max_cost = max_cost
    job.status = "queued"
    job.finished_at = None
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        raise _already_active(job.book_id) from exc
    session.refresh(job)
    return job
