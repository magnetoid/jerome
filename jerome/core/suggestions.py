from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jerome.documents.changes import Change, apply_changes, check_replacement_text
from jerome.documents.model import PLACEHOLDER
from jerome.errors import Conflict, InvalidChangeError, NotFound
from jerome.orm import Block, Chapter, Decision, Suggestion
from jerome.types import Candidate, Severity

DECIDED = ("accepted", "edited")
# Who made a decision: an API client, a Word review import, a person in the browser, or AI
# auto-apply. ("system" is internal: supersede/reopen bookkeeping.)
Channel = Literal["api", "word", "web", "auto"]
EDITOR_EXPLANATION = "Change made by the editor in Word"


@dataclass
class AddResult:
    created: int
    dropped_decided_overlap: int
    dropped_rejected_repeat: int
    dropped_open_overlap: int
    dropped_invalid: int = 0  # replacement the documents cannot carry (placeholder, control)
    rows: list[Suggestion] = field(default_factory=list)  # created by this call, in order


@dataclass
class SuggestionFilter:
    chapter_id: int | None = None
    category: str | None = None
    severity: str | None = None
    min_severity: str | None = None
    source: str | None = None
    status: str | None = None


def spans_overlap(s1: int, e1: int, s2: int, e2: int) -> bool:
    """Same semantics as validate_changes: touching spans do not overlap, an insertion
    overlaps a span only strictly inside it, and two insertions at one point overlap."""
    if s1 == e1 and s2 == e2:
        return s1 == s2
    return s1 < e2 and s2 < e1


def _check_original(block: Block, start: int, end: int, original: str) -> None:
    if not 0 <= start <= end <= len(block.text) or block.text[start:end] != original:
        raise InvalidChangeError(
            f"original text does not match block {block.id} at ({start}, {end})"
        )
    if PLACEHOLDER in original:
        raise InvalidChangeError(f"change ({start}, {end}) covers a placeholder")


def _decided_overlap(siblings: Sequence[Suggestion], s: Suggestion | tuple[int, int]) -> bool:
    start, end = (s.start, s.end) if isinstance(s, Suggestion) else s
    return any(
        d.status in DECIDED
        and (isinstance(s, tuple) or d.id != s.id)
        and spans_overlap(start, end, d.start, d.end)
        for d in siblings
    )


def lock_blocks(session: Session, block_ids: Sequence[int]) -> dict[int, Block]:
    stmt = (
        select(Block)
        .where(Block.id.in_(set(block_ids)))
        .order_by(Block.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return {b.id: b for b in session.scalars(stmt)}


def _log(
    session: Session,
    s: Suggestion,
    action: str,
    previous: str,
    *,
    actor: str,
    channel: str,
    review_import_id: int | None = None,
    final_text: str | None = None,
) -> None:
    session.add(
        Decision(
            suggestion_id=s.id,
            action=action,
            previous_status=previous,
            new_status=s.status,
            final_text=final_text,
            actor=actor,
            channel=channel,
            review_import_id=review_import_id,
        )
    )


def _block_suggestions(session: Session, block_id: int) -> list[Suggestion]:
    stmt = (
        select(Suggestion)
        .where(Suggestion.block_id == block_id)
        .order_by(Suggestion.id)
        .execution_options(populate_existing=True)
    )
    return list(session.scalars(stmt))


def add_candidates(
    session: Session,
    book_id: int,
    job_id: int | None,
    candidates: Sequence[Candidate],
    model: str | None = None,
) -> AddResult:
    blocks = lock_blocks(session, [c.block_id for c in candidates])
    for c in candidates:
        block = blocks.get(c.block_id)
        if block is None or block.book_id != book_id:
            raise InvalidChangeError(f"block {c.block_id} does not belong to book {book_id}")
        _check_original(block, c.start, c.end, c.original)

    existing: dict[int, list[Suggestion]] = {}
    result = AddResult(0, 0, 0, 0)
    for c in candidates:
        try:
            check_replacement_text(c.replacement)
        except InvalidChangeError:
            result.dropped_invalid += 1
            continue
        known = existing.setdefault(c.block_id, _block_suggestions(session, c.block_id))
        overlapping = [s for s in known if spans_overlap(c.start, c.end, s.start, s.end)]
        if any(s.status in DECIDED for s in overlapping):
            result.dropped_decided_overlap += 1
        elif any(
            s.status == "rejected"
            and (s.start, s.end, s.replacement) == (c.start, c.end, c.replacement)
            for s in known
        ):
            result.dropped_rejected_repeat += 1
        elif any(s.status == "open" for s in overlapping):
            result.dropped_open_overlap += 1
        else:
            row = Suggestion(
                book_id=book_id,
                block_id=c.block_id,
                job_id=job_id,
                start=c.start,
                end=c.end,
                original=c.original,
                replacement=c.replacement,
                category=str(c.category),
                severity=str(c.severity),
                source=str(c.source),
                explanation=c.explanation,
                status="open",
                model=model,
                agreed=c.agreed,
                disputed=c.disputed,
                alternative=c.alternative,
                rule=c.rule,
            )
            session.add(row)
            known.append(row)
            result.rows.append(row)
            result.created += 1
    session.flush()
    return result


def _supersede_overlapping(session: Session, s: Suggestion, *, actor: str) -> None:
    for other in _block_suggestions(session, s.block_id):
        if (
            other.id != s.id
            and other.status == "open"
            and spans_overlap(s.start, s.end, other.start, other.end)
        ):
            other.status = "superseded"
            other.superseded_by = s.id
            _log(session, other, "supersede", "open", actor=actor, channel="system")


def _reopen_superseded(session: Session, s: Suggestion, *, actor: str) -> None:
    siblings = _block_suggestions(session, s.block_id)
    for other in siblings:
        if other.superseded_by != s.id or other.status != "superseded":
            continue
        blocker = next(
            (
                d
                for d in siblings
                if d.id not in (s.id, other.id)
                and d.status in DECIDED
                and spans_overlap(other.start, other.end, d.start, d.end)
            ),
            None,
        )
        if blocker is not None:
            # status unchanged (still superseded), so no decision row is written
            other.superseded_by = blocker.id
            continue
        other.status = "open"
        other.superseded_by = None
        _log(session, other, "reopen", "superseded", actor=actor, channel="system")


def is_auto_applied(session: Session, suggestion_ids: Iterable[int]) -> set[int]:
    """The suggestions that stand as the AI applied them: accepted, and the latest decision
    on them that is not system bookkeeping (supersede, reopen) came from auto-apply."""
    ids = set(suggestion_ids)
    if not ids:
        return set()
    latest = (
        select(func.max(Decision.id))
        .where(Decision.suggestion_id.in_(ids), Decision.channel != "system")
        .group_by(Decision.suggestion_id)
    )
    stmt = (
        select(Decision.suggestion_id)
        .join(Suggestion, Suggestion.id == Decision.suggestion_id)
        .where(Decision.id.in_(latest), Decision.channel == "auto", Suggestion.status == "accepted")
    )
    return set(session.scalars(stmt))


def decide(
    session: Session,
    suggestion_id: int,
    action: Literal["accept", "reject", "edit", "undo"],
    *,
    actor: str,
    channel: Channel = "api",
    replacement: str | None = None,
    review_import_id: int | None = None,
) -> Suggestion:
    s = session.get(Suggestion, suggestion_id)
    if s is None:
        raise NotFound(f"suggestion {suggestion_id} not found")
    lock_blocks(session, [s.block_id])
    session.refresh(s)

    previous = s.status
    # A person may reject or edit a change the AI applied in one step. A person's accept
    # still needs an undo first, so one click cannot overturn a decision someone made.
    reversing = (
        action in ("reject", "edit")
        and previous == "accepted"
        and s.id in is_auto_applied(session, [s.id])
    )
    if action == "undo":
        if previous not in ("accepted", "rejected", "edited"):
            raise Conflict(f"cannot undo a {previous} suggestion")
    elif previous != "open" and not reversing:
        raise Conflict(f"cannot {action} a {previous} suggestion")
    if action == "edit" and replacement is None:
        raise InvalidChangeError("edit requires a replacement")
    if action == "edit":
        check_replacement_text(replacement)  # type: ignore[arg-type]
    if action in ("accept", "edit") and _decided_overlap(
        _block_suggestions(session, s.block_id), s
    ):
        raise Conflict("suggestion overlaps an accepted or edited suggestion")

    final_text: str | None = None
    if action == "accept":
        s.status = "accepted"
    elif action == "reject":
        s.status = "rejected"
    elif action == "edit":
        s.status = "edited"
        s.final_text = final_text = replacement
    else:
        s.status = "open"
        s.final_text = None
    session.flush()

    _log(
        session,
        s,
        action,
        previous,
        actor=actor,
        channel=channel,
        review_import_id=review_import_id,
        final_text=final_text,
    )
    if action in ("accept", "edit"):
        _supersede_overlapping(session, s, actor=actor)
    elif action == "undo" or reversing:  # the change is gone: what it displaced comes back
        _reopen_superseded(session, s, actor=actor)
    session.flush()
    return s


def record_editor_change(
    session: Session,
    block_id: int,
    start: int,
    end: int,
    original: str,
    replacement: str,
    *,
    pending: bool,
    actor: str,
    review_import_id: int | None,
) -> Suggestion | None:
    block = lock_blocks(session, [block_id]).get(block_id)
    if block is None:
        raise NotFound(f"block {block_id} not found")
    _check_original(block, start, end, original)
    check_replacement_text(replacement)
    siblings = _block_suggestions(session, block_id)
    if _decided_overlap(siblings, (start, end)):
        raise Conflict("change overlaps an accepted or edited suggestion")
    for s in siblings:
        if (s.source, s.start, s.end, s.replacement) == ("editor", start, end, replacement):
            return None
    row = Suggestion(
        book_id=block.book_id,
        block_id=block_id,
        start=start,
        end=end,
        original=original,
        replacement=replacement,
        category="style",
        severity="low",
        source="editor",
        explanation=EDITOR_EXPLANATION,
        status="open" if pending else "accepted",
    )
    session.add(row)
    session.flush()
    if not pending:
        _log(
            session,
            row,
            "accept",
            "open",
            actor=actor,
            channel="word",
            review_import_id=review_import_id,
        )
        _supersede_overlapping(session, row, actor=actor)
        session.flush()
    return row


class SuggestionPage(NamedTuple):
    rows: list[Suggestion]
    total: int  # rows matching the filter, ignoring limit and offset


def list_suggestions(
    session: Session, book_id: int, f: SuggestionFilter, limit: int = 100, offset: int = 0
) -> SuggestionPage:
    conditions = [Suggestion.book_id == book_id]
    if f.chapter_id is not None:
        conditions.append(Block.chapter_id == f.chapter_id)
    if f.category is not None:
        conditions.append(Suggestion.category == f.category)
    if f.severity is not None:
        conditions.append(Suggestion.severity == f.severity)
    if f.min_severity is not None:
        floor = Severity(f.min_severity).rank
        conditions.append(Suggestion.severity.in_([s.value for s in Severity if s.rank >= floor]))
    if f.source is not None:
        conditions.append(Suggestion.source == f.source)
    if f.status is not None:
        conditions.append(Suggestion.status == f.status)
    joined = (
        select(Suggestion)
        .join(Block, Block.id == Suggestion.block_id)
        .join(Chapter, Chapter.id == Block.chapter_id)
        .where(*conditions)
    )
    total = session.scalar(select(func.count()).select_from(joined.subquery())) or 0
    stmt = (
        joined.order_by(Chapter.ordinal, Block.ordinal, Suggestion.start, Suggestion.id)
        .limit(limit)
        .offset(offset)
        .execution_options(populate_existing=True)
    )
    return SuggestionPage(list(session.scalars(stmt)), total)


def bulk_decide(
    session: Session,
    book_id: int,
    *,
    ids: list[int] | None,
    f: SuggestionFilter | None,
    action: Literal["accept", "reject"],
    actor: str,
    channel: Channel = "api",
) -> int:
    if (ids is None) == (f is None):
        raise InvalidChangeError("give exactly one of ids or filter")
    if ids is not None:
        wanted = set(ids)
        found = {
            s.id: s
            for s in list_suggestions(session, book_id, SuggestionFilter(), limit=2**31 - 1).rows
            if s.id in wanted
        }
        if wanted - found.keys():
            raise InvalidChangeError("every id must belong to the book")
        targets = list(found.values())
    else:
        assert f is not None
        if f.status not in (None, "open"):
            return 0
        scoped = SuggestionFilter(**{**f.__dict__, "status": "open"})
        targets = list_suggestions(session, book_id, scoped, limit=2**31 - 1).rows
    decided = 0
    for s in targets:
        session.refresh(s)  # an earlier accept in this bulk may have superseded it
        if s.status != "open":
            continue
        try:
            with session.begin_nested():
                decide(session, s.id, action, actor=actor, channel=channel)
        except Conflict:
            continue
        decided += 1
    return decided


def get_block(session: Session, book_id: int, block_id: int) -> Block:
    block = session.get(Block, block_id)
    if block is None or block.book_id != book_id:
        raise NotFound(f"block {block_id} not found in book {book_id}")
    return block


def text_with_changes(text: str, suggestions: Iterable[Suggestion]) -> str:
    """The block text with its accepted and edited suggestions applied; others are ignored."""
    changes = [
        Change(s.start, s.end, s.final_text if s.status == "edited" else s.replacement)
        for s in suggestions
        if s.status in DECIDED
    ]
    return apply_changes(text, changes)


def current_text(session: Session, block_id: int) -> str:
    block = session.get(Block, block_id)
    if block is None:
        raise NotFound(f"block {block_id} not found")
    stmt = select(Suggestion).where(Suggestion.block_id == block_id, Suggestion.status.in_(DECIDED))
    return text_with_changes(block.text, session.scalars(stmt))
