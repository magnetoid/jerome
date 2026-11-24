"""Everything done on a project, by people and the agent, newest first, with revert of a
single correction. Correction decisions come from ``decisions``; the agent's other changes
come from ``agent_actions``."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.orm import Session

from jerome.core.suggestions import decide, lock_blocks
from jerome.errors import Conflict, NotFound
from jerome.orm import AgentAction, Block, Book, Chapter, Decision, Suggestion

Who = Literal["people", "agent", "automatic", "api"]
WHO_CHANNELS: dict[str, tuple[str, ...]] = {
    "people": ("web", "word"),
    "agent": ("mcp",),
    "automatic": ("auto",),
    "api": ("api",),
}
# The agent's decision tools: their decisions are listed one by one from ``decisions``.
DECISION_TOOLS = ("decide_suggestion", "edit_suggestion", "bulk_decide", "revert_decision")
_REVERTABLE_STATUSES = ("accepted", "rejected", "edited")
CHANGED_LATER = "Changed again after this; revert the later change first."


@dataclass
class ActivityItem:
    kind: Literal["decision", "agent"]
    id: int
    at: datetime
    actor: str
    channel: str
    book_id: int | None
    book_title: str | None
    chapter_id: int | None
    chapter_title: str | None
    summary: str
    action: str | None = None  # decision: accept / reject / edit / undo
    suggestion_id: int | None = None
    original: str | None = None
    replacement: str | None = None  # what the decision put in place; None for reject/undo
    tool: str | None = None  # agent action
    job_id: int | None = None
    before: Any = None
    after: Any = None
    revertable: bool = False
    revert_blocked: str | None = None  # why a correction cannot be reverted now


def _quoted(t: str) -> str:
    return f"“{t}”" if t else "nothing"


def _decision_summary(d: Decision, s: Suggestion) -> str:
    change = f"{_quoted(s.original)} → {_quoted(d.final_text or s.replacement)}"
    return {
        "accept": f"Accepted {change}",
        "reject": f"Rejected {_quoted(s.original)} → {_quoted(s.replacement)}",
        "edit": f"Edited {change}",
        "undo": f"Undid the decision on {_quoted(s.original)} → {_quoted(s.replacement)}",
    }.get(d.action, f"{d.action.capitalize()} {change}")


def list_activity(
    session: Session,
    *,
    book_id: int | None = None,
    chapter_id: int | None = None,
    who: Who | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ActivityItem], int]:
    """One page of activity, newest first, with the total before paging."""
    dec = (
        select(
            literal("decision").label("kind"),
            Decision.id.label("id"),
            Decision.created_at.label("at"),
        )
        .join(Suggestion, Suggestion.id == Decision.suggestion_id)
        .join(Block, Block.id == Suggestion.block_id)
        .where(Decision.channel != "system")
    )
    agent = select(
        literal("agent").label("kind"), AgentAction.id.label("id"), AgentAction.at.label("at")
    ).where(AgentAction.tool.notin_(DECISION_TOOLS))
    if book_id is not None:
        dec = dec.where(Suggestion.book_id == book_id)
        agent = agent.where(AgentAction.book_id == book_id)
    if chapter_id is not None:
        dec = dec.where(Block.chapter_id == chapter_id)
        agent = agent.where(AgentAction.chapter_id == chapter_id)
    if who is not None:
        dec = dec.where(Decision.channel.in_(WHO_CHANNELS[who]))
    # Every agent action is the agent's; other filters leave them out.
    parts = [dec, agent] if who in (None, "agent") else [dec]
    both = union_all(*parts).subquery()
    total = session.scalar(select(func.count()).select_from(both)) or 0
    page = session.execute(
        select(both.c.kind, both.c.id)
        .order_by(both.c.at.desc(), both.c.kind, both.c.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    dec_ids = [i for k, i in page if k == "decision"]
    agent_ids = [i for k, i in page if k == "agent"]
    items = {("decision", it.id): it for it in _decisions(session, dec_ids)}
    items |= {("agent", it.id): it for it in _agent_actions(session, agent_ids)}
    return [items[(k, i)] for k, i in page], total


def _latest_decision_ids(session: Session, suggestion_ids: set[int]) -> dict[int, int]:
    if not suggestion_ids:
        return {}
    rows = session.execute(
        select(Decision.suggestion_id, func.max(Decision.id))
        .where(Decision.suggestion_id.in_(suggestion_ids))
        .group_by(Decision.suggestion_id)
    )
    return dict(rows.all())


def _revert_state(d: Decision, s: Suggestion, latest: int | None) -> tuple[bool, str | None]:
    if d.action == "undo":
        return False, None
    if latest != d.id or s.status not in _REVERTABLE_STATUSES:
        return False, CHANGED_LATER
    return True, None


def _decisions(session: Session, ids: list[int]) -> list[ActivityItem]:
    if not ids:
        return []
    rows = session.execute(
        select(Decision, Suggestion, Chapter.id, Chapter.title, Book.title)
        .join(Suggestion, Suggestion.id == Decision.suggestion_id)
        .join(Block, Block.id == Suggestion.block_id)
        .join(Chapter, Chapter.id == Block.chapter_id)
        .join(Book, Book.id == Suggestion.book_id)
        .where(Decision.id.in_(ids))
    ).all()
    latest = _latest_decision_ids(session, {s.id for _, s, *_ in rows})
    out = []
    for d, s, chapter_id, chapter_title, book_title in rows:
        revertable, blocked = _revert_state(d, s, latest.get(s.id))
        out.append(
            ActivityItem(
                kind="decision",
                id=d.id,
                at=d.created_at,
                actor=d.actor,
                channel=d.channel,
                book_id=s.book_id,
                book_title=book_title,
                chapter_id=chapter_id,
                chapter_title=chapter_title,
                summary=_decision_summary(d, s),
                action=d.action,
                suggestion_id=s.id,
                original=s.original,
                replacement=(d.final_text or s.replacement)
                if d.action in ("accept", "edit")
                else None,
                revertable=revertable,
                revert_blocked=blocked,
            )
        )
    return out


def _agent_actions(session: Session, ids: list[int]) -> list[ActivityItem]:
    if not ids:
        return []
    rows = session.execute(
        select(AgentAction, Book.title, Chapter.title)
        .outerjoin(Book, Book.id == AgentAction.book_id)
        .outerjoin(Chapter, Chapter.id == AgentAction.chapter_id)
        .where(AgentAction.id.in_(ids))
    ).all()
    return [
        ActivityItem(
            kind="agent",
            id=a.id,
            at=a.at,
            actor=a.actor,
            channel=a.channel,
            book_id=a.book_id,
            book_title=book_title,
            chapter_id=a.chapter_id,
            chapter_title=chapter_title,
            summary=a.summary,
            tool=a.tool,
            job_id=a.job_id,
            before=a.before,
            after=a.after,
        )
        for a, book_title, chapter_title in rows
    ]


def revert_decision(session: Session, decision_id: int, *, actor: str, channel: str) -> Suggestion:
    """Undo one correction decision, if it is still its suggestion's latest decision."""
    d = session.get(Decision, decision_id)
    if d is None or d.channel == "system":
        raise NotFound(f"decision {decision_id} not found")
    if d.action == "undo":
        raise Conflict("an undo cannot be reverted")
    s = session.get(Suggestion, d.suggestion_id)
    assert s is not None  # decisions cascade with their suggestion
    lock_blocks(session, [s.block_id])  # no decision can land between the check and the undo
    session.refresh(s)
    latest = _latest_decision_ids(session, {s.id}).get(s.id)
    revertable, blocked = _revert_state(d, s, latest)
    if not revertable:
        raise Conflict(blocked or "this decision cannot be reverted")
    return decide(session, s.id, "undo", actor=actor, channel=channel)  # type: ignore[arg-type]
