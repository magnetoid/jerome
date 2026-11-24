"""Ask the AI about LanguageTool's marks in review, and keep its verdict with the suggestion.

Only an open mark found by LanguageTool alone, not yet checked, is asked about. The call uses
the ``ai`` pass model and is billed to the mark's job (else the book's latest proofreading
job) in a transaction of its own, so the spend is kept even when nothing is saved. No row
lock is held during the call. Then the paragraph is locked (as a decision locks it) and each
verdict is saved on a mark that still has none. The check never decides a mark: what a
teammate did meanwhile stands."""

import logging
from dataclasses import dataclass, field
from itertools import groupby

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from jerome.core.style_sheets import get_style_sheet
from jerome.core.suggestions import lock_blocks
from jerome.core.translation import _bill_apart, source_texts
from jerome.errors import AiCheckError, Conflict, NotFound
from jerome.models import ModelOutputError, ModelRefusal, Registry
from jerome.models.base import Provider
from jerome.models.registry import PassConfig
from jerome.orm import Block, Book, Chapter, Job, Suggestion
from jerome.proofread.ai_check import VERDICT_SCHEMA, Mark, Verdict, build_request, parse_verdicts

log = logging.getLogger(__name__)

CHAPTER_LIMIT = 40  # marks asked about per chapter request
MAX_TOKENS = 4000  # a paragraph's verdicts are short
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
            max_tokens=MAX_TOKENS,
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


def _save(session: Session, block_id: int, verdicts: list[Verdict], model: str) -> None:
    """Keep each verdict on its mark, unless the mark got one meanwhile."""
    lock_blocks(session, [block_id])
    by_id = {v.id: v for v in verdicts}
    rows = session.scalars(
        select(Suggestion).where(Suggestion.id.in_(by_id)).execution_options(populate_existing=True)
    )
    for row in rows:
        if row.ai_verdict is None:
            v = by_id[row.id]
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
    book = session.get(Book, s.book_id)
    block = session.get(Block, s.block_id)
    chosen = _model(registry, book)
    verdicts, model = _ask(session, registry, chosen, book, block, [s])
    if not verdicts:
        raise AiCheckError(UNUSABLE)
    _save(session, block.id, verdicts, model)
    log.info("suggestion %s checked by the AI for %s: %s", s.id, actor, s.ai_verdict)
    return s


def check_chapter(
    session: Session, registry: Registry, book_id: int, chapter_id: int, *, actor: str
) -> ChapterCheck:
    """Ask the AI about the chapter's first ``CHAPTER_LIMIT`` unchecked LanguageTool marks,
    in reading order, one call per paragraph. A paragraph whose reply cannot be used is
    counted in ``failed`` and the others go on."""
    chapter = session.get(Chapter, chapter_id)
    if chapter is None or chapter.book_id != book_id:
        raise NotFound(f"chapter {chapter_id} not found in book {book_id}")
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
    for block, group in groupby(rows[:CHAPTER_LIMIT], key=lambda r: r[1]):
        marks = [s for s, _ in group]
        try:
            verdicts, model = _ask(session, registry, chosen, book, block, marks)
        except AiCheckError:
            out.failed += len(marks)
            continue
        _save(session, block.id, verdicts, model)
        got = {v.id for v in verdicts}
        out.checked += [s for s in marks if s.id in got]
        out.failed += sum(1 for s in marks if s.id not in got)
    log.info(
        "chapter %s: %s marks checked by the AI for %s, %s failed, %s left",
        chapter_id, len(out.checked), actor, out.failed, out.remaining,
    )  # fmt: skip
    return out
