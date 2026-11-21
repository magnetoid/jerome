from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

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
from jerome.orm import Suggestion
from jerome.types import Category, Severity, Source, Status

router = APIRouter()

PrincipalDep = Annotated[Principal, Depends(current_principal)]


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
    return suggestions.list_suggestions(session, book_id, _to_filter(f), limit, offset)


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
        "suggestions": list(rows),
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
    return suggestions.decide(
        session,
        suggestion_id,
        "edit",
        actor=principal.actor,
        channel=principal.channel,
        replacement=body.replacement,
    )


@router.post("/suggestions/{suggestion_id}/{action}", response_model=SuggestionOut)
def decide(
    suggestion_id: int,
    action: Literal["accept", "reject", "undo"],
    session: SessionDep,
    principal: PrincipalDep,
):
    return suggestions.decide(
        session, suggestion_id, action, actor=principal.actor, channel=principal.channel
    )
