from typing import Annotated

from fastapi import APIRouter, Query, Response

from jerome.api.deps import SessionDep
from jerome.api.schemas import AgentActionOut
from jerome.core import agent_actions

router = APIRouter()


@router.get("/agent-actions", response_model=list[AgentActionOut])
def list_agent_actions(
    session: SessionDep,
    response: Response,
    book_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    rows, total = agent_actions.list_actions(session, book_id=book_id, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total)
    return rows
