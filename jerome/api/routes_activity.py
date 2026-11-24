from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from jerome.api.deps import SessionDep
from jerome.api.routes_suggestions import PrincipalDep, suggestion_out
from jerome.api.schemas import ActivityItemOut, SuggestionOut
from jerome.core import activity

router = APIRouter()


@router.get("/activity", response_model=list[ActivityItemOut])
def list_activity(
    session: SessionDep,
    response: Response,
    book_id: int | None = None,
    chapter_id: int | None = None,
    who: Literal["people", "agent", "automatic", "api"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Everything done, by people and the agent, newest first (X-Total-Count)."""
    items, total = activity.list_activity(
        session, book_id=book_id, chapter_id=chapter_id, who=who, limit=limit, offset=offset
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.post("/activity/decisions/{decision_id}/revert", response_model=SuggestionOut)
def revert_decision(decision_id: int, session: SessionDep, principal: PrincipalDep):
    """Undo one correction decision, while it is still its suggestion's latest."""
    row = activity.revert_decision(
        session, decision_id, actor=principal.actor, channel=principal.channel
    )
    return suggestion_out(session, [row])[0]
