"""Ask the AI about LanguageTool's marks in review, and keep its verdict with the suggestion.

Only an open mark found by LanguageTool alone, not yet checked, is asked about. The call uses
the ``ai`` pass model and is billed to the mark's job (else the book's latest proofreading
job) in a transaction of its own, so the spend is kept even when nothing is saved. No row
lock is held during any call: after the last call the paragraphs are locked (in id order, as
a decision locks them) and each verdict is saved on a mark that still has none. The check
never decides a mark: what a teammate did meanwhile stands. It is refused while a job runs on
the book, since the job's worker locks the job before the paragraphs."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import groupby

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from jerome.core.style_sheets import get_style_sheet
from jerome.core.suggestions import lock_blocks
from jerome.core.translation import _active_job, _bill_apart, source_texts
from jerome.errors import AiCheckError, Conflict, NotFound
from jerome.models import ModelOutputError, ModelRefusal, Registry
from jerome.models.base import Provider
from jerome.models.registry import PassConfig
from jerome.orm import Block, Book, Chapter, Job, Suggestion
from jerome.proofread.ai_check import VERDICT_SCHEMA, Mark, Verdict, build_request, parse_verdicts

log = logging.getLogger(__name__)

CHAPTER_LIMIT = 40  # marks asked about per chapter request
TIME_BUDGET = 40.0  # seconds: no new paragraph is started after this (proxies time out at 60)
# Advisory locks, held until the request's transaction ends, so no mark is paid for twice:
# a chapter check holds its chapter's lock alone; a single check shares it (two marks of a
# chapter can be checked at once) and holds its mark's lock alone.
_CHAPTER_LOCKS = 0x41494348  # "AICH"
_MARK_LOCKS = 0x41494353  # "AICS"
RUNNING = "a check of chapter {} is already running; try again shortly"
NO_AI_MODEL = "No AI model is set for proofreading (models.ai); set one under Settings"
UNUSABLE = "The AI's reply could not be used; try again"
DECLINED = "The AI declined to check this mark"


@dataclass
class ChapterCheck:
    checked: list[Suggestion] = field(default_factory=list)  # marks that now have a verdict
    remaining: int = 0  # eligible marks not asked about in this request
    failed: int = 0  # marks asked about that got no usable verdict


def _eligible() -> list[ColumnElement[bool]]:
    return [
        Suggestion.status == "open",
        Suggestion.source == "languagetool",
        Suggestion.agreed.is_(False),
        Suggestion.ai_verdict.is_(None),
    ]


def _why_not(s: Suggestion) -> str | None:
    if s.status != "open":
        return f"suggestion {s.id} is {s.status}, not open"
    if s.source != "languagetool" or s.agreed:
        return f"suggestion {s.id} is not from LanguageTool alone; the AI has already seen it"
    if s.ai_verdict is not None:
        return f"suggestion {s.id} was already checked by the AI"
    return None


def _model(registry: Registry, book: Book) -> tuple[Provider, PassConfig]:
    chosen = registry.for_pass("ai", local_only=book.local_only)
    if chosen is None:
        raise Conflict(NO_AI_MODEL)
    return chosen


def _max_tokens(marks: int) -> int:
    """Room for the model's thinking (which counts) and a verdict per mark."""
    return max(16000, 8000 + 400 * marks)


def _no_active_job(session: Session, book_id: int) -> None:
    if _active_job(session, book_id) is not None:
        raise Conflict(f"book {book_id} has an active job; check its marks once it is done")


def _try_lock(session: Session, space: int, key: int, *, shared: bool = False) -> bool:
    fn = "pg_try_advisory_xact_lock_shared" if shared else "pg_try_advisory_xact_lock"
    return bool(session.scalar(text(f"SELECT {fn}(:space, :key)"), {"space": space, "key": key}))


def _job_for(session: Session, book_id: int, marks: list[Suggestion]) -> int:
    """The job a check of ``marks`` is billed to."""
    for s in marks:
        if s.job_id is not None:
            return s.job_id
    job_id = session.scalar(
        select(Job.id)
        .where(Job.book_id == book_id, Job.type == "proofread")
        .order_by(Job.id.desc())
        .limit(1)
    )
    if job_id is None:
        raise Conflict(f"book {book_id} has no proofreading job to bill the check to")
    return job_id


def _ask(
    session: Session,
    registry: Registry,
    chosen: tuple[Provider, PassConfig],
    book: Book,
    block: Block,
    marks: list[Suggestion],
) -> tuple[list[Verdict], str]:
    """One model call about one paragraph's marks: the usable verdicts and the reply's model.
    Raises AiCheckError when the model refuses or its reply cannot be used."""
    from jerome.jobs.worker import Billed, billed_from  # the worker imports core modules

    provider, cfg = chosen
    job_id = _job_for(session, book.id, marks)
    previous = session.scalar(
        select(Block.text)
        .where(Block.chapter_id == block.chapter_id, Block.ordinal < block.ordinal)
        .order_by(Block.ordinal.desc())
        .limit(1)
    )
    asked = [Mark(s.id, s.start, s.end, s.original, s.replacement, s.explanation) for s in marks]
    system, user = build_request(
        get_style_sheet(session, book.id),
        block.text,
        asked,
        previous=previous,
        source=source_texts(session, [block.id]).get(block.id),
    )
    try:
        result = provider.complete(
            model=cfg.model,
            system=system,
            user=user,
            schema=VERDICT_SCHEMA,
            max_tokens=_max_tokens(len(marks)),
            effort=cfg.effort,
        )
    except (ModelRefusal, ModelOutputError) as exc:
        billed = billed_from(exc)
        if billed is not None:
            _bill_apart(session, registry, job_id, billed, cfg.model, pass_name="ai")
        log.warning("AI check of block %s failed: %r", block.id, exc)
        raise AiCheckError(DECLINED if isinstance(exc, ModelRefusal) else UNUSABLE) from exc
    billed = Billed(result.usage, result.model, result.provider)
    _bill_apart(session, registry, job_id, billed, cfg.model, pass_name="ai")
    try:
        return parse_verdicts(result.data, asked), result.model
    except ModelOutputError as exc:
        raise AiCheckError(UNUSABLE) from exc


Answer = tuple[int, list[Verdict], str]  # block id, its verdicts, the model that gave them


def _save(session: Session, answers: list[Answer]) -> None:
    """Keep each verdict on its mark, unless the mark got one meanwhile. All the paragraphs
    are locked at once, after the last model call."""
    if not answers:
        return
    lock_blocks(session, [block_id for block_id, _, _ in answers])
    by_id = {v.id: (v, model) for _, verdicts, model in answers for v in verdicts}
    rows = session.scalars(
        select(Suggestion).where(Suggestion.id.in_(by_id)).execution_options(populate_existing=True)
    )
    for row in rows:
        if row.ai_verdict is None:
            v, model = by_id[row.id]
            row.ai_verdict, row.ai_replacement, row.ai_reason = v.verdict, v.replacement, v.reason
            row.ai_model = model
    session.flush()


def check_suggestion(
    session: Session, registry: Registry, suggestion_id: int, *, actor: str
) -> Suggestion:
    """Ask the AI about one LanguageTool mark and keep its verdict. Conflict when the mark
    is not one to ask about; AiCheckError when no usable verdict came back."""
    s = session.get(Suggestion, suggestion_id)
    if s is None:
        raise NotFound(f"suggestion {suggestion_id} not found")
    if (why := _why_not(s)) is not None:
        raise Conflict(why)
    block = session.get(Block, s.block_id)
    if not _try_lock(session, _CHAPTER_LOCKS, block.chapter_id, shared=True):
        raise Conflict(RUNNING.format(block.chapter_id))
    if not _try_lock(session, _MARK_LOCKS, s.id):
        raise Conflict(f"suggestion {s.id} is already being checked by the AI")
    session.refresh(s)  # what another check committed before the locks were taken
    if (why := _why_not(s)) is not None:
        raise Conflict(why)
    book = session.get(Book, s.book_id)
    _no_active_job(session, book.id)
    chosen = _model(registry, book)
    verdicts, model = _ask(session, registry, chosen, book, block, [s])
    if not verdicts:
        raise AiCheckError(UNUSABLE)
    _save(session, [(block.id, verdicts, model)])
    log.info("suggestion %s checked by the AI for %s: %s", s.id, actor, s.ai_verdict)
    return s


def check_chapter(
    session: Session,
    registry: Registry,
    book_id: int,
    chapter_id: int,
    *,
    actor: str,
    clock: Callable[[], float] = time.monotonic,
) -> ChapterCheck:
    """Ask the AI about the chapter's first ``CHAPTER_LIMIT`` unchecked LanguageTool marks,
    in reading order, one call per paragraph, starting no paragraph after ``TIME_BUDGET``
    (what is left counts in ``remaining``). A paragraph whose call fails is counted in
    ``failed`` and the others go on. A second check of the same chapter while one runs is
    refused."""
    chapter = session.get(Chapter, chapter_id)
    if chapter is None or chapter.book_id != book_id:
        raise NotFound(f"chapter {chapter_id} not found in book {book_id}")
    _no_active_job(session, book_id)
    if not _try_lock(session, _CHAPTER_LOCKS, chapter_id):
        raise Conflict(RUNNING.format(chapter_id))
    rows = session.execute(
        select(Suggestion, Block)
        .join(Block, Block.id == Suggestion.block_id)
        .where(Block.chapter_id == chapter_id, *_eligible())
        .order_by(Block.ordinal, Suggestion.start, Suggestion.id)
    ).all()
    if not rows:
        return ChapterCheck()
    book = session.get(Book, book_id)
    chosen = _model(registry, book)
    out = ChapterCheck(remaining=max(0, len(rows) - CHAPTER_LIMIT))
    groups = [(b, [s for s, _ in g]) for b, g in groupby(rows[:CHAPTER_LIMIT], key=lambda r: r[1])]
    answers: list[Answer] = []
    start = clock()
    for i, (block, marks) in enumerate(groups):
        if clock() - start > TIME_BUDGET and i > 0:
            out.remaining += sum(len(m) for _, m in groups[i:])
            break
        try:
            verdicts, model = _ask(session, registry, chosen, book, block, marks)
        except AiCheckError:
            out.failed += len(marks)
            continue
        except Exception:  # a provider or network error: keep what the other paragraphs paid for
            log.exception("AI check of block %s failed", block.id)
            out.failed += len(marks)
            continue
        answers.append((block.id, verdicts, model))
        got = {v.id for v in verdicts}
        out.checked += [s for s in marks if s.id in got]
        out.failed += sum(1 for s in marks if s.id not in got)
    _save(session, answers)
    log.info(
        "chapter %s: %s marks checked by the AI for %s, %s failed, %s left",
        chapter_id, len(out.checked), actor, out.failed, out.remaining,
    )  # fmt: skip
    return out
