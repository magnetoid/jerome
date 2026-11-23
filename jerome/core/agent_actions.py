from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jerome.orm import AgentAction


def record_action(
    session: Session,
    *,
    actor: str,
    tool: str,
    summary: str,
    book_id: int | None = None,
    chapter_id: int | None = None,
    job_id: int | None = None,
    before: Any = None,
    after: Any = None,
) -> AgentAction:
    """Log one change made through MCP. Flushes; the caller owns the transaction."""
    action = AgentAction(
        actor=actor,
        channel="mcp",
        tool=tool,
        book_id=book_id,
        chapter_id=chapter_id,
        job_id=job_id,
        summary=summary,
        before=before,
        after=after,
    )
    session.add(action)
    session.flush()
    return action


def list_actions(
    session: Session, *, book_id: int | None, limit: int, offset: int
) -> tuple[list[AgentAction], int]:
    """Newest first, optionally for one book, with the total before paging."""
    stmt = select(AgentAction)
    if book_id is not None:
        stmt = stmt.where(AgentAction.book_id == book_id)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(AgentAction.at.desc(), AgentAction.id.desc()).limit(limit).offset(offset)
    )
    return list(rows), total
