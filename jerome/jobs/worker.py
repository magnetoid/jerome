"""Background job worker.

A chapter task runs chunk by chunk. Per chunk: a short locked check of the job (cancel and
cost cap), the model calls with no transaction open, then one locked write that commits the
chunk's suggestions, its usage and the task's progress together. A chunk that fails commits
nothing, so paid-for chunks are never thrown away or bought twice.

A translate job (``Job.type == "translate"``) runs the same way, with translate chunks: per
chunk the model calls (the chunk, then each block that failed, once alone) run with no
transaction open, then one locked write stores every block of the chunk, ``done`` or
``flagged``, with the spend of every call. Blocks no longer ``pending`` are skipped, so a
resumed chunk never buys a block twice. A chapter's first chunk may also carry its title
(``title_item``); it is skipped once the chapter's title is translated, and when it cannot
be translated the source title stays and the chapter goes in the job's
``untranslated_titles``. A chunk whose reply is unusable as a whole (ModelOutputError) is
retried as a task; on the task's last attempt it is treated like a refused chunk instead,
so its blocks end ``done`` or ``flagged``, never stuck ``pending``.

A stop request (SIGTERM or SIGINT, via the ``stop`` event) is honoured between chunks: the
chunk in flight is committed, then the task and its job go back to ``queued``.

An import job (``Job.type == "import"``) runs its ``import_*`` tasks in
``jerome.pdfimport.job``; its model calls are recorded with pass name ``ocr``.

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
from jerome.core.learned_words import rules_for
from jerome.core.style_sheets import get_style_sheet
from jerome.core.suggestions import add_candidates
from jerome.core.translation import (
    NO_TRANSLATION_MODEL,
    attach_sources,
    context_from,
    missing_marks,
    previous_pairs,
    store_title,
    store_translation,
    sync_chapter_title,
    title_pending,
)
from jerome.errors import Conflict, JeromeError
from jerome.jobs.estimate import included_chapter_ids, translation_chunks
from jerome.jobs.webhooks import send_webhook
from jerome.models import ModelOutputError, ModelRefusal, Registry, Usage, cost
from jerome.models.base import BilledError
from jerome.models.pricing import Price
from jerome.orm import Block, Book, Job, JobTask, ModelUsage
from jerome.proofread.ai_pass import AiPassResult, Chunk, chunk_blocks, run_ai_pass
from jerome.proofread.consistency import consistency_candidates
from jerome.proofread.languagetool import (
    LanguageToolClient,
    LearnedRules,
    languagetool_candidates,
)
from jerome.proofread.merge import merge_candidates
from jerome.publish.build import prepare, render
from jerome.publish.model import PublishError
from jerome.storage import Storage
from jerome.translate.pass_ import TranslateChunk, TranslateItem, run_translate_pass
from jerome.translate.tags import Decoded
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
    storage: Storage | None = None  # where publish jobs read books and keep finished files


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
    calls: list[Billed] = field(default_factory=list)  # a translate chunk's billed calls
    deps: WorkerDeps | None = None  # what the chunk ran with: prices a failed call

    def clear(self) -> None:
        self.result = self.billed = None
        self.calls = []

    def bills(self) -> list[Billed]:
        return ([self.billed] if self.billed is not None else []) + self.calls


@dataclass
class _Plan:
    job_id: int
    book_id: int
    chapter_id: int | None
    kind: str
    job_type: str
    chunks_done: int
    passes: list[str]
    local_only: bool
    style: StyleSheet  # the book's; on a translated book, the target language's
    policy: AutoApplyPolicy
    chunks: list[Chunk] | list[TranslateChunk] = field(default_factory=list)
    source_style: StyleSheet | None = None  # translate jobs: the source book's
    learned: LearnedRules = frozenset()  # learned words for the style's language
    attempts: int = 0  # the task's failed attempts before this one


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
        elif plan.kind == "publish":
            _run_publish(session_factory, task_id, plan, deps)
        elif plan.kind.startswith("import_"):
            from jerome.pdfimport.job import run_import_task  # it builds on this module

            run_import_task(session_factory, task_id, plan, deps, paid, stop)
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
            job_type=job.type,
            chunks_done=task.chunks_done,
            passes=list(job.passes),
            local_only=book.local_only,
            style=get_style_sheet(s, job.book_id),
            policy=get_policy(s, job.book_id),
            attempts=task.attempts,
        )
        plan.learned = rules_for(s, plan.style.language)
        if task.kind == "chapter" and job.type == "translate":
            plan.chunks = translation_chunks(s, job.book_id, task.chapter_id)
            plan.source_style = get_style_sheet(s, book.source_book_id)
        elif task.kind == "chapter":
            texts = block_texts(s, job.book_id, task.chapter_id, proofreadable_only=True)
            plan.chunks = attach_sources(s, book, chunk_blocks(texts))
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
        paid.deps = deps
        if not _precheck(sf, task_id, plan, deps):
            return
        paid.clear()
        if not _run_chunk(sf, task_id, plan, plan.chunks[index], index, deps, paid):
            return
        paid.clear()
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
    pass_name: str = "ai",
) -> Decimal:
    """Add the ModelUsage row for a billed model call and return its cost."""
    usage = billed.usage
    price = price_for(registry, billed.model, configured)
    amount = cost(price, usage) if price is not None else Decimal(0)
    session.add(
        ModelUsage(
            job_id=job_id,
            api_key_name=api_key_name,
            pass_name=pass_name,
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
    session: Session,
    job: Job,
    billed: Billed,
    registry: Registry,
    configured: str,
    pass_name: str = "ai",
) -> None:
    job.cost_actual = job.cost_actual + usage_row(
        session, billed, registry, configured, job_id=job.id, pass_name=pass_name
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
    if plan.job_type == "translate":
        return _run_translate_chunk(sf, task_id, plan, chunk, index, deps, paid)
    candidates: list[Candidate] = []
    dropped = refusals = skipped = lt_skipped = 0
    if "languagetool" in plan.passes and plan.local_only and deps.lt.hosted:
        lt_skipped = 1
    elif "languagetool" in plan.passes:
        found, lt_dropped = languagetool_candidates(chunk.blocks, plan.style, deps.lt, plan.learned)
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
        if lt_skipped:
            deltas["languagetool_skipped_local_only"] = lt_skipped
        job.stats = _bump(job.stats, **deltas)
        task.chunks_done = index + 1
        if task.chunks_done >= len(plan.chunks):
            note = _complete_task(s, job, task, deps)
        s.commit()
    _send(note, deps)
    return True


def _translate_calls(
    plan: _Plan,
    chunk: TranslateChunk,
    todo: list[TranslateItem],
    pairs: list[tuple[str, str | None]],
    deps: WorkerDeps,
    paid: _Paid,
) -> tuple[dict[int, Decoded], int]:
    """The model calls of a translate chunk: the pending items together, then each failed
    item once alone. Every billed call goes to ``paid.calls`` before anything can raise.
    ``pairs`` holds the two blocks before the chunk, then one per chunk item, as (source
    text, translation or None); an item's context is the two pairs before it.
    A chunk whose reply is unusable as a whole raises, to be retried as a task, except on
    the task's last attempt, where it is handled like a refusal.
    Returns the decoded translations and the number of refused calls."""
    chosen = deps.registry.for_pass("translate", local_only=plan.local_only)
    if chosen is None:
        raise Conflict(NO_TRANSLATION_MODEL)
    provider, cfg = chosen
    source, target = plan.source_style, plan.style
    assert source is not None
    offset = len(pairs) - len(chunk.items)
    position = {it.block_id: offset + k for k, it in enumerate(chunk.items)}
    done: dict[int, Decoded] = {}
    refusals = 0

    def call(items: list[TranslateItem]):
        context = context_from(pairs[: position[items[0].block_id]])
        try:
            res = run_translate_pass(
                TranslateChunk(items), source, target, context, provider, cfg.model, cfg.effort
            )
        except BilledError as exc:
            billed = billed_from(exc)
            if billed is not None:
                paid.calls.append(billed)
            raise
        paid.calls.append(Billed(res.result.usage, res.result.model, res.result.provider))
        for block_id, decoded in res.done.items():
            done[block_id] = decoded
            pairs[position[block_id]] = (pairs[position[block_id]][0], decoded.text)
        return res

    try:
        failed = call(todo).failed
    except ModelRefusal:
        refusals += 1
        failed = [it.block_id for it in todo]
    except ModelOutputError:
        if plan.attempts + 1 < MAX_ATTEMPTS:
            raise  # billed; the task is tried again
        log.warning("chunk reply unusable on the last attempt; trying each item alone")
        failed = [it.block_id for it in todo]
    if hasattr(provider, "translate_texts"):
        return done, refusals  # a translation service gives the same reply again: no retry
    by_id = {it.block_id: it for it in todo}
    for block_id in failed:
        try:
            call([by_id[block_id]])
        except ModelRefusal:
            refusals += 1
        except ModelOutputError:
            pass  # billed; the block is flagged
    return done, refusals


def _run_translate_chunk(
    sf: SessionFactory,
    task_id: int,
    plan: _Plan,
    chunk: TranslateChunk,
    index: int,
    deps: WorkerDeps,
    paid: _Paid,
) -> bool:
    """Run one translate chunk; see the module docstring. False means the task should stop."""
    title = next((it for it in chunk.items if it.title_of is not None), None)
    ids = [it.block_id for it in chunk.items if it.title_of is None]
    with sf() as s:  # a short read: nothing is held open during the model calls
        stored = {
            block_id: (status, text)
            for block_id, status, text in s.execute(
                select(Block.id, Block.translation_status, Block.text).where(Block.id.in_(ids))
            )
        }
        before = previous_pairs(s, ids[0]) if ids else []
        title_todo = title is not None and title_pending(s, title.title_of)
    status = {i: st for i, (st, _) in stored.items()}
    todo = [
        it
        for it in chunk.items
        if status.get(it.block_id) == "pending" or (it is title and title_todo)
    ]
    pairs = before + [
        (it.text, stored[it.block_id][1] if status.get(it.block_id) == "done" else None)
        for it in chunk.items
    ]
    done: dict[int, Decoded] = {}
    refusals = 0
    if todo:
        done, refusals = _translate_calls(plan, chunk, todo, pairs, deps, paid)
    by_id = {it.block_id: it for it in todo if it.title_of is None}
    dropped = sum(
        d.marks_dropped + missing_marks(by_id[i].marks, d.marks)
        for i, d in done.items()
        if i in by_id
    )
    configured = deps.registry.settings.models.get("translate", {}).get("model", "")
    source = plan.source_style

    note = None
    with sf() as s:
        job, task = _lock_job_task(s, plan.job_id, task_id)
        for billed in paid.calls:
            _usage_row(s, job, billed, deps.registry, configured, "translate")
        if task.chunks_done != index:
            s.commit()  # another worker committed this chunk; keep only the spend
            return False
        translated = flagged = 0
        if by_id:
            blocks = s.scalars(
                select(Block).where(Block.id.in_(list(by_id))).with_for_update()
            ).all()
            for block in sorted(blocks, key=lambda b: b.ordinal):
                if block.translation_status != "pending":
                    continue
                decoded = done.get(block.id)
                store_translation(
                    s,
                    block,
                    by_id[block.id],
                    decoded,
                    language=plan.style.language,
                    source_language=source.language if source else plan.style.language,
                )
                if decoded is None:
                    flagged += 1
                else:
                    translated += 1
        deltas = {"translated": translated, "flagged": flagged, "marks_dropped": dropped}
        if refusals:
            deltas["refusals"] = refusals
        job.stats = _bump(job.stats, **deltas)
        if title_todo and title_pending(s, title.title_of):
            if not store_title(s, title.title_of, done.get(title.block_id)):
                kept = list(job.stats.get("untranslated_titles", []))
                if title.title_of not in kept:
                    job.stats = {**job.stats, "untranslated_titles": [*kept, title.title_of]}
        task.chunks_done = index + 1
        if task.chunks_done >= len(plan.chunks):
            if plan.chapter_id is not None:
                sync_chapter_title(s, plan.chapter_id)
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
            blocks += block_texts(s, plan.book_id, chapter_id, proofreadable_only=True)
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


def _run_publish(sf: SessionFactory, task_id: int, plan: _Plan, deps: WorkerDeps) -> None:
    """Make the finished file: prepare it in a short session, render it with no session
    open, then keep it, named with the job's id so every run keeps its own file. A file that
    cannot be made (PublishError) or a book that may not be published (Conflict) fails the
    job at once: trying again would give the same answer. It spends no model money, so
    there is no cost cap to check."""
    storage = deps.storage
    if storage is None:
        raise RuntimeError("this worker has no storage, so it cannot run publish jobs")
    try:
        with sf() as s:
            prepared = prepare(s, storage, plan.book_id, plan.passes[0])
            s.commit()
        data, _media_type = render(prepared)
    except (PublishError, Conflict) as exc:
        _fail_at_once(sf, task_id, plan, str(exc), deps)
        return
    name = f"{prepared.filename_stem}-{plan.job_id}.{prepared.extension}"
    note = None
    with sf() as s:
        job, task = _lock_job_task(s, plan.job_id, task_id)
        if task.status == "done" or job.status not in ACTIVE:  # cancelled while it rendered
            s.commit()
            return
        storage.save_export(plan.book_id, name, data)
        job.stats = {"file": name, "warnings": prepared.warnings}
        note = _complete_task(s, job, task, deps)
        s.commit()
    _send(note, deps)


def _fail_at_once(sf: SessionFactory, task_id: int, plan: _Plan, message: str, deps: WorkerDeps):
    deps = refreshed(deps)
    note = None
    with sf() as s:
        job, task = _lock_job_task(s, plan.job_id, task_id)
        task.attempts += 1
        task.error = message[:2000]
        task.status = "failed"
        task.finished_at = deps.now()
        if job.status in ACTIVE:
            job.status = "failed"
            job.finished_at = deps.now()
            note = _notification(job, "job.failed")
        s.commit()
    _send(note, deps)


_PASS_OF_JOB = {"translate": "translate", "import": "ocr"}


def _record_failure(
    sf: SessionFactory, task_id: int, exc: Exception, paid: _Paid, deps: WorkerDeps
) -> None:
    note = None
    pricing = paid.deps or deps  # a billed call is priced as it ran, not as settings are now
    deps = refreshed(deps)  # the notification is signed with the current webhook secret
    with sf() as s:
        job_id = s.scalar(select(JobTask.job_id).where(JobTask.id == task_id))
        job, task = _lock_job_task(s, job_id, task_id)
        bills = paid.bills() or [b for b in (billed_from(exc),) if b is not None]
        pass_name = _PASS_OF_JOB.get(job.type, "ai")
        configured = pricing.registry.settings.models.get(pass_name, {}).get("model", "")
        if job.type == "import":
            from jerome.pdfimport.job import configured_ocr_model

            configured = configured_ocr_model(pricing.registry)
        for billed in bills:  # the model calls were billed: keep their spend on the books
            _usage_row(s, job, billed, pricing.registry, configured, pass_name)
        task.attempts += 1
        task.error = repr(exc)[:2000]
        if task.attempts >= MAX_ATTEMPTS:
            task.status = "failed"
            task.finished_at = deps.now()
            if job.status in ACTIVE:
                job.status = "failed"
                job.finished_at = deps.now()
                note = _notification(job, "job.failed")
                if job.type == "import":  # the book cannot become ready: say why
                    book = s.get(Book, job.book_id)
                    if book is not None:
                        book.import_status = "failed"
                        book.import_error = (str(exc) or type(exc).__name__)[:2000]
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
