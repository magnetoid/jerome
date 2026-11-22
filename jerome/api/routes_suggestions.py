from collections.abc import Sequence
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from jerome.api.deps import Principal, SessionDep, current_principal
from jerome.api.schemas import (
    BlockOut,
    BulkBody,
    BulkOut,
    EditBody,
    SuggestionFilterIn,
    SuggestionOut,
)
from jerome.core import books, suggestions
from jerome.core.suggestions import SuggestionFilter
from jerome.orm import Block, Decision, Suggestion
from jerome.types import Category, Severity, Source, Status

router = APIRouter()

_PLAIN_FIELDS = (
    "id", "book_id", "block_id", "start", "end", "original", "replacement", "category",
    "severity", "source", "explanation", "status", "final_text", "job_id", "model",
    "created_at", "agreed", "disputed", "alternative", "rule",
)  # fmt: skip

PrincipalDep = Annotated[Principal, Depends(current_principal)]


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
        sid: actor
        for sid, actor in session.execute(
            select(Decision.suggestion_id, Decision.actor)
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
            "decided_by": deciders.get(s.id),
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
    n = suggestions.bulk_decide(
        session,
        body.book_id,
        ids=body.ids,
        f=_to_filter(body.filter) if body.filter else None,
        action=body.action,
        actor=principal.actor,
        channel=principal.channel,
    )
    return {"decided": n}


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
