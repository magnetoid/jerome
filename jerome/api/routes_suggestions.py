from collections.abc import Sequence
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from jerome.api.deps import Principal, SessionDep, current_actor, current_principal, get_registry
from jerome.api.routes_text import get_text_limits, too_many
from jerome.api.schemas import (
    BlockOut,
    BulkBody,
    BulkOut,
    ChapterAiCheckOut,
    EditBody,
    SuggestionFilterIn,
    SuggestionOut,
)
from jerome.core import ai_check, books, suggestions, translation
from jerome.core.suggestions import SuggestionFilter
from jerome.models import Registry
from jerome.orm import Block, Decision, Suggestion
from jerome.proofread.text import TextLimits
from jerome.types import Category, Severity, Source, Status

router = APIRouter()

_PLAIN_FIELDS = (
    "id", "book_id", "block_id", "start", "end", "original", "replacement", "category",
    "severity", "source", "explanation", "status", "final_text", "job_id", "model",
    "created_at", "agreed", "disputed", "alternative", "rule", "ai_verdict", "ai_replacement",
    "ai_reason", "ai_model",
)  # fmt: skip

PrincipalDep = Annotated[Principal, Depends(current_principal)]
RegistryDep = Annotated[Registry, Depends(get_registry)]
ActorDep = Annotated[str, Depends(current_actor)]
LimitsDep = Annotated[TextLimits, Depends(get_text_limits)]


def suggestion_out(session: Session, rows: Sequence[Suggestion]) -> list[dict]:
    """API shape for suggestions, in input order, in a fixed number of queries."""
    ids = [s.id for s in rows]
    if not ids:
        return []
    chapters = {
        sid: chapter_id
        for sid, chapter_id in session.execute(
            select(Suggestion.id, Block.chapter_id)
            .join(Block, Block.id == Suggestion.block_id)
            .where(Suggestion.id.in_(ids))
        )
    }
    auto = suggestions.is_auto_applied(session, ids)
    deciders = {
        sid: (actor, channel)
        for sid, actor, channel in session.execute(
            select(Decision.suggestion_id, Decision.actor, Decision.channel)
            .where(Decision.suggestion_id.in_(ids), Decision.channel != "system")
            .ext(distinct_on(Decision.suggestion_id))
            .order_by(Decision.suggestion_id, Decision.id.desc())
        )
    }
    return [
        {
            **{name: getattr(s, name) for name in _PLAIN_FIELDS},
            "chapter_id": chapters[s.id],
            "auto_applied": s.id in auto,
            "decided_by": deciders.get(s.id, (None, None))[0],
            "decided_channel": deciders.get(s.id, (None, None))[1],
        }
        for s in rows
    ]


def _to_filter(f: SuggestionFilterIn) -> SuggestionFilter:
    return SuggestionFilter(
        chapter_id=f.chapter_id,
        category=f.category,
        severity=f.severity,
        min_severity=f.min_severity,
        source=f.source,
        status=f.status,
    )


@router.get("/books/{book_id}/suggestions", response_model=list[SuggestionOut])
def list_suggestions(
    book_id: int,
    session: SessionDep,
    response: Response,
    chapter_id: int | None = None,
    category: Category | None = None,
    severity: Severity | None = None,
    min_severity: Severity | None = None,
    source: Source | None = None,
    status: Status | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    books.get_book(session, book_id)
    f = SuggestionFilterIn(
        chapter_id=chapter_id,
        category=category,
        severity=severity,
        min_severity=min_severity,
        source=source,
        status=status,
    )
    rows, total = suggestions.list_suggestions(session, book_id, _to_filter(f), limit, offset)
    response.headers["X-Total-Count"] = str(total)
    return suggestion_out(session, rows)


@router.get("/books/{book_id}/blocks/{block_id}", response_model=BlockOut)
def get_block(book_id: int, block_id: int, session: SessionDep):
    block = suggestions.get_block(session, book_id, block_id)
    rows = session.scalars(
        select(Suggestion)
        .where(Suggestion.block_id == block_id)
        .order_by(Suggestion.start, Suggestion.id)
    )
    return {
        "original": block.text,
        "current": suggestions.current_text(session, block_id),
        "suggestions": suggestion_out(session, list(rows)),
    }


@router.post("/suggestions/bulk", response_model=BulkOut)
def bulk(body: BulkBody, session: SessionDep, principal: PrincipalDep):
    decided = suggestions.bulk_decide(
        session,
        body.book_id,
        ids=body.ids,
        f=_to_filter(body.filter) if body.filter else None,
        action=body.action,
        actor=principal.actor,
        channel=principal.channel,
    )
    return {"decided": len(decided)}


@router.post("/suggestions/{suggestion_id}/edit", response_model=SuggestionOut)
def edit(suggestion_id: int, body: EditBody, session: SessionDep, principal: PrincipalDep):
    row = suggestions.decide(
        session,
        suggestion_id,
        "edit",
        actor=principal.actor,
        channel=principal.channel,
        replacement=body.replacement,
    )
    return suggestion_out(session, [row])[0]


@router.post("/suggestions/{suggestion_id}/ai-check", response_model=SuggestionOut)
def ai_check_suggestion(
    suggestion_id: int,
    session: SessionDep,
    registry: RegistryDep,
    actor: ActorDep,
    limits: LimitsDep,
):
    """Ask the AI whether a LanguageTool mark is right, and keep its verdict on the
    suggestion. A paid model call on the ai pass model, under the key's per-minute rate
    limit; billed to the proofreading job. Decides nothing."""
    if (wait := limits.take(actor)) is not None:
        raise too_many("Rate limit exceeded", wait)
    row = ai_check.check_suggestion(session, registry, suggestion_id, actor=actor)
    return suggestion_out(session, [row])[0]


@router.post("/books/{book_id}/chapters/{chapter_id}/ai-check", response_model=ChapterAiCheckOut)
def ai_check_chapter(
    book_id: int,
    chapter_id: int,
    session: SessionDep,
    registry: RegistryDep,
    actor: ActorDep,
    limits: LimitsDep,
):
    """Ask the AI about the chapter's unchecked LanguageTool marks, up to 40 per request, one
    paid call per paragraph; one rate-limit token per request."""
    books.get_book(session, book_id)
    translation.ensure_not_source(session, book_id)
    if (wait := limits.take(actor)) is not None:
        raise too_many("Rate limit exceeded", wait)
    result = ai_check.check_chapter(session, registry, book_id, chapter_id, actor=actor)
    return {
        "checked": len(result.checked),
        "remaining": result.remaining,
        "failed": result.failed,
        "suggestions": suggestion_out(session, result.checked),
    }


@router.post("/suggestions/{suggestion_id}/{action}", response_model=SuggestionOut)
def decide(
    suggestion_id: int,
    action: Literal["accept", "reject", "undo"],
    session: SessionDep,
    principal: PrincipalDep,
):
    row = suggestions.decide(
        session, suggestion_id, action, actor=principal.actor, channel=principal.channel
    )
    return suggestion_out(session, [row])[0]
