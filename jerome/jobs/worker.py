"""Background job worker.

A chapter task runs chunk by chunk. Per chunk: a short locked check of the job (cancel and
cost cap), the model calls with no transaction open, then one locked write that commits the
chunk's suggestions, its usage and the task's progress together. A chunk that fails commits
nothing, so paid-for chunks are never thrown away or bought twice.

A stop request (SIGTERM or SIGINT, via the ``stop`` event) is honoured between chunks: the
chunk in flight is committed, then the task and its job go back to ``queued``.

``WorkerDeps.refresh``, when set, brings the deps up to date with the settings before each
task and each chunk, so a change made in the app reaches a running worker.

Known limitation: there is no task lease or heartbeat. After a crash the job stays
``running``; resuming it would double-process a task a live worker may still hold, so it is
refused: cancel it, then resume.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session, aliased, sessionmaker

from jerome.core.auto_apply import AutoApplyPolicy, apply_clear_cut, get_policy
from jerome.core.books import block_texts
from jerome.core.style_sheets import get_style_sheet
from jerome.core.suggestions import add_candidates
from jerome.errors import JeromeError
from jerome.jobs.estimate import included_chapter_ids
from jerome.jobs.webhooks import send_webhook
from jerome.models import ModelRefusal, Registry, Usage, cost
from jerome.models.base import BilledError
from jerome.models.pricing import Price
from jerome.orm import Book, Job, JobTask, ModelUsage
from jerome.proofread.ai_pass import AiPassResult, Chunk, chunk_blocks, run_ai_pass
from jerome.proofread.consistency import consistency_candidates
from jerome.proofread.languagetool import LanguageToolClient, languagetool_candidates
from jerome.proofread.merge import merge_candidates
from jerome.types import BlockText, Candidate, StyleSheet

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
ACTIVE = ("queued", "running")
SessionFactory = sessionmaker[Session]


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class WorkerDeps:
    registry: Registry
    lt: LanguageToolClient
    webhook_secret: str | None
    now: Callable[[], datetime] = utcnow
    sleep_base: float = 10.0
    # Returns the deps for the current settings (see jerome.core.settings_store.LiveRefresh).
    refresh: Callable[["WorkerDeps"], "WorkerDeps"] | None = None


def refreshed(deps: WorkerDeps) -> WorkerDeps:
    """``deps`` brought up to date with the settings; the same deps if that fails."""
    if deps.refresh is None:
        return deps
    try:
        return deps.refresh(deps)
    except Exception as exc:  # noqa: BLE001 - a settings problem must not stop the work
        # only the type: the message could carry a setting, even a key
        log.error("settings refresh failed (%s); keeping the current settings", type(exc).__name__)
        return deps


@dataclass
class Notification:
    url: str
    event: str
    payload: dict[str, Any]


def _notification(job: Job, event: str) -> Notification | None:
    if not job.callback_url:
        return None
    payload = {
        "job_id": job.id,
        "status": job.status,
        "cost_actual": str(job.cost_actual),
        "stats": dict(job.stats or {}),
    }
    return Notification(job.callback_url, event, payload)


def _send(note: Notification | None, deps: WorkerDeps) -> None:
    if note is not None:
        send_webhook(note.url, note.event, note.payload, deps.webhook_secret)


def claim_task(session: Session, *, now: datetime | None = None) -> int | None:
    """Claim the oldest runnable task. Commits before returning: otherwise a second session
    claiming a sibling task would block on the job row this one just updated."""
    now = now or utcnow()
    sibling = aliased(JobTask)
    open_chapter = exists().where(
        sibling.job_id == JobTask.job_id, sibling.kind == "chapter", sibling.status != "done"
    )
    stmt = (
        select(JobTask)
        .join(Job, Job.id == JobTask.job_id)
        .where(
            JobTask.status == "queued",
            (JobTask.not_before.is_(None)) | (JobTask.not_before <= now),
            Job.status.in_(ACTIVE),
            (JobTask.kind == "chapter") | ~open_chapter,
        )
        .order_by(JobTask.id)
        .limit(1)
        .with_for_update(of=JobTask, skip_locked=True)
    )
    task = session.scalars(stmt).first()
    if task is None:
        session.rollback()
        return None
    task.status = "running"
    task.started_at = now
    task_id, job_id = task.id, task.job_id
    session.commit()  # release the task lock first: every other path locks job, then task
    # Guarded: a cancel, pause or failure committed since the select must not be overwritten.
    session.execute(
        update(Job).where(Job.id == job_id, Job.status == "queued").values(status="running")
    )
    session.commit()
    return task_id


@dataclass
class Billed:
    usage: Usage
    model: str
    provider: str


@dataclass
class _Paid:
    """A model call that was billed but whose chunk is not yet committed."""

    result: AiPassResult | None = None
    billed: Billed | None = None


@dataclass
class _Plan:
    job_id: int
    book_id: int
    chapter_id: int | None
    kind: str
    chunks_done: int
    passes: list[str]
    local_only: bool
    style: StyleSheet
    policy: AutoApplyPolicy
    chunks: list[Chunk] = field(default_factory=list)


def _lock_job(session: Session, job_id: int) -> Job:
    stmt = select(Job).where(Job.id == job_id).with_for_update()
    return session.scalars(stmt.execution_options(populate_existing=True)).one()


def _lock_job_task(session: Session, job_id: int, task_id: int) -> tuple[Job, JobTask]:
    """Lock job, then task, always in that order. Both are refreshed from the locked rows."""
    job = _lock_job(session, job_id)
    stmt = select(JobTask).where(JobTask.id == task_id).with_for_update()
    task = session.scalars(stmt.execution_options(populate_existing=True)).one()
    return job, task


def run_task(
    session_factory: SessionFactory,
    task_id: int,
    deps: WorkerDeps,
    stop: threading.Event | None = None,
) -> None:
    paid = _Paid()
    try:
        plan = _start(session_factory, task_id, deps)
        if plan is None:
            return
        if plan.kind == "consistency":
            _run_consistency(session_factory, task_id, plan, deps)
        else:
            _run_chapter(session_factory, task_id, plan, deps, paid, stop)
    except Exception as exc:
        log.exception("task %s failed", task_id)
        _record_failure(session_factory, task_id, exc, paid, deps)


def _start(sf: SessionFactory, task_id: int, deps: WorkerDeps) -> _Plan | None:
    with sf() as s:
        job_id = s.scalar(select(JobTask.job_id).where(JobTask.id == task_id))
        if job_id is None:
            return None
        job, task = _lock_job_task(s, job_id, task_id)
        if task.status == "done":
            return None
        if job.status not in ACTIVE:  # stop case: put the task back on the queue
            task.status = "queued"
            task.not_before = None
            s.commit()
            return None
        task.status = "running"
        task.started_at = task.started_at or deps.now()
        job.status = "running"
        book = s.get(Book, job.book_id)
        plan = _Plan(
            job_id=job.id,
            book_id=job.book_id,
            chapter_id=task.chapter_id,
            kind=task.kind,
            chunks_done=task.chunks_done,
            passes=list(job.passes),
            local_only=book.local_only,
            style=get_style_sheet(s, job.book_id),
            policy=get_policy(s, job.book_id),
        )
        if task.kind == "chapter":
            plan.chunks = chunk_blocks(block_texts(s, job.book_id, task.chapter_id))
        s.commit()
        return plan


def _precheck(sf: SessionFactory, task_id: int, plan: _Plan, deps: WorkerDeps) -> bool:
    """True if the next chunk may start. Otherwise the task goes back to the queue."""
    note = None
    with sf() as s:
        job = _lock_job(s, plan.job_id)
        go = job.status in ACTIVE
        if go and job.cost_actual >= job.max_cost:
            job.status = "paused_cost_cap"
            note = _notification(job, "job.paused")
            go = False
        if not go:
            s.execute(
                update(JobTask)
                .where(JobTask.id == task_id, JobTask.status != "done")
                .values(status="queued", not_before=None)
            )
        s.commit()
    _send(note, deps)
    return go


def _requeue_for_stop(sf: SessionFactory, task_id: int, plan: _Plan) -> None:
    """The worker is stopping between chunks: the task, and the job unless another of its
    tasks is still running elsewhere, go back to the queue. Attempts are not counted."""
    with sf() as s:
        job, task = _lock_job_task(s, plan.job_id, task_id)
        if task.status != "done":
            task.status = "queued"
            task.not_before = None
        others = s.scalar(
            select(func.count())
            .select_from(JobTask)
            .where(JobTask.job_id == job.id, JobTask.id != task_id, JobTask.status == "running")
        )
        if job.status == "running" and not others:
            job.status = "queued"
        s.commit()


def _run_chapter(
    sf: SessionFactory,
    task_id: int,
    plan: _Plan,
    deps: WorkerDeps,
    paid: _Paid,
    stop: threading.Event | None = None,
) -> None:
    index = plan.chunks_done
    if index >= len(plan.chunks):  # every chunk already committed
        _finish(sf, task_id, plan, deps)
        return
    while index < len(plan.chunks):
        if stop is not None and stop.is_set():
            _requeue_for_stop(sf, task_id, plan)
            return
        deps = refreshed(deps)
        if not _precheck(sf, task_id, plan, deps):
            return
        paid.result = paid.billed = None
        if not _run_chunk(sf, task_id, plan, plan.chunks[index], index, deps, paid):
            return
        paid.result = paid.billed = None
        index += 1


def price_for(registry: Registry, model: str, configured: str) -> Price | None:
    try:
        return registry.price(model)
    except JeromeError:
        log.warning("no price for model %r; using the configured model %r", model, configured)
    try:
        return registry.price(configured)
    except JeromeError:
        log.error("no price for configured model %r either; recording zero cost", configured)
        return None


def usage_row(
    session: Session,
    billed: Billed,
    registry: Registry,
    configured: str,
    *,
    job_id: int | None = None,
    api_key_name: str | None = None,
) -> Decimal:
    """Add the ModelUsage row for a billed AI call and return its cost."""
    usage = billed.usage
    price = price_for(registry, billed.model, configured)
    amount = cost(price, usage) if price is not None else Decimal(0)
    session.add(
        ModelUsage(
            job_id=job_id,
            api_key_name=api_key_name,
            pass_name="ai",
            provider=billed.provider,
            model=billed.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cost=amount,
        )
    )
    return amount


def _usage_row(
    session: Session, job: Job, billed: Billed, registry: Registry, configured: str
) -> None:
    job.cost_actual = job.cost_actual + usage_row(
        session, billed, registry, configured, job_id=job.id
    )


def billed_from(exc: Exception) -> Billed | None:
    if isinstance(exc, BilledError) and exc.usage is not None:
        return Billed(exc.usage, exc.model or "", exc.provider or "")
    return None


def _bump(stats: dict[str, Any], **deltas: int) -> dict[str, Any]:
    new = dict(stats or {})
    for key, n in deltas.items():
        new[key] = new.get(key, 0) + n
    return new


def _run_chunk(
    sf: SessionFactory,
    task_id: int,
    plan: _Plan,
    chunk: Chunk,
    index: int,
    deps: WorkerDeps,
    paid: _Paid,
) -> bool:
    """Run one chunk. False means the task should stop (another worker got ahead)."""
    candidates: list[Candidate] = []
    dropped = refusals = skipped = 0
    if "languagetool" in plan.passes:
        found, lt_dropped = languagetool_candidates(chunk.blocks, plan.style, deps.lt)
        candidates += found
        dropped += lt_dropped
    configured = ""
    if "ai" in plan.passes:
        chosen = deps.registry.for_pass("ai", local_only=plan.local_only)
        if chosen is None:
            skipped = 1
        else:
            provider, cfg = chosen
            configured = cfg.model
            try:
                paid.result = run_ai_pass(chunk, plan.style, provider, cfg.model, cfg.effort)
            except ModelRefusal as exc:
                refusals = 1
                paid.billed = billed_from(exc)  # a refusal can still be billed
            else:
                res = paid.result.result
                paid.billed = Billed(res.usage, res.model, res.provider)
                candidates += paid.result.candidates
                dropped += paid.result.dropped
    merged = merge_candidates(candidates)

    note = None
    with sf() as s:
        job, task = _lock_job_task(s, plan.job_id, task_id)
        if paid.billed is not None:
            _usage_row(s, job, paid.billed, deps.registry, configured)
        if task.chunks_done != index:
            s.commit()  # another worker committed this chunk; keep only the spend
            return False
        model = paid.result.result.model if paid.result is not None else None
        added = add_candidates(s, plan.book_id, job.id, merged, model=model)
        applied = apply_clear_cut(s, added.rows, plan.policy)
        dropped += (
            added.dropped_decided_overlap
            + added.dropped_rejected_repeat
            + added.dropped_open_overlap
            + added.dropped_invalid
        )
        deltas = {"dropped": dropped, "auto_applied": applied}
        if refusals:
            deltas["refusals"] = refusals
        if skipped:
            deltas["ai_skipped_local_only"] = skipped
        job.stats = _bump(job.stats, **deltas)
        task.chunks_done = index + 1
        if task.chunks_done >= len(plan.chunks):
            note = _complete_task(s, job, task, deps)
        s.commit()
    _send(note, deps)
    return True


def _complete_task(s: Session, job: Job, task: JobTask, deps: WorkerDeps) -> Notification | None:
    """Mark the task done. The caller holds the job row lock, so two workers finishing the
    last two tasks cannot both miss that every task is done."""
    task.status = "done"
    task.finished_at = deps.now()
    task.error = None
    s.flush()
    remaining = s.scalar(
        select(func.count())
        .select_from(JobTask)
        .where(JobTask.job_id == job.id, JobTask.status != "done")
    )
    if remaining == 0 and job.status in ACTIVE:
        job.status = "completed"
        job.finished_at = deps.now()
        return _notification(job, "job.completed")
    return None


def _finish(sf: SessionFactory, task_id: int, plan: _Plan, deps: WorkerDeps) -> None:
    with sf() as s:
        job, task = _lock_job_task(s, plan.job_id, task_id)
        note = None if task.status == "done" else _complete_task(s, job, task, deps)
        s.commit()
    _send(note, deps)


def _run_consistency(sf: SessionFactory, task_id: int, plan: _Plan, deps: WorkerDeps) -> None:
    note = None
    with sf() as s:
        job, task = _lock_job_task(s, plan.job_id, task_id)
        if task.status == "done":
            return
        blocks: list[BlockText] = []
        for chapter_id in included_chapter_ids(s, plan.book_id, job.chapter_ids):
            blocks += block_texts(s, plan.book_id, chapter_id)
        added = add_candidates(s, plan.book_id, job.id, consistency_candidates(blocks, plan.style))
        job.stats = _bump(
            job.stats,
            dropped=added.dropped_decided_overlap
            + added.dropped_rejected_repeat
            + added.dropped_open_overlap
            + added.dropped_invalid,
            auto_applied=apply_clear_cut(s, added.rows, plan.policy),
        )
        note = _complete_task(s, job, task, deps)
        s.commit()
    _send(note, deps)


def _record_failure(
    sf: SessionFactory, task_id: int, exc: Exception, paid: _Paid, deps: WorkerDeps
) -> None:
    note = None
    deps = refreshed(deps)  # price and sign with the settings as they are now
    with sf() as s:
        job_id = s.scalar(select(JobTask.job_id).where(JobTask.id == task_id))
        job, task = _lock_job_task(s, job_id, task_id)
        billed = paid.billed or billed_from(exc)
        if billed is not None:  # the model call was billed: keep its spend on the books
            configured = deps.registry.settings.models.get("ai", {}).get("model", "")
            _usage_row(s, job, billed, deps.registry, configured)
        task.attempts += 1
        task.error = repr(exc)[:2000]
        if task.attempts >= MAX_ATTEMPTS:
            task.status = "failed"
            task.finished_at = deps.now()
            if job.status in ACTIVE:
                job.status = "failed"
                job.finished_at = deps.now()
                note = _notification(job, "job.failed")
        else:
            task.status = "queued"
            task.not_before = deps.now() + timedelta(seconds=deps.sleep_base * 2**task.attempts)
        s.commit()
    _send(note, deps)


def work_forever(
    session_factory: SessionFactory,
    deps: WorkerDeps,
    poll_interval: float = 1.0,
    stop: threading.Event | None = None,
) -> None:
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            with session_factory() as s:
                task_id = claim_task(s, now=deps.now())
            if task_id is None:
                stop.wait(poll_interval)
            else:
                deps = refreshed(deps)
                run_task(session_factory, task_id, deps, stop)
        except Exception:
            log.exception("worker loop error")
            stop.wait(poll_interval)
