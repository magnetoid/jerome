"""Word round trip: read a reviewed review copy and record the editor's decisions.

``classify_block`` and ``match_blocks`` are pure. ``import_review`` does the database work.
All offsets are code points.
"""

import difflib
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from jerome.core.books import get_book
from jerome.core.suggestions import decide, lock_blocks, record_editor_change
from jerome.documents.docx_review import (
    BOOKMARK_PREFIX,
    PendingRevision,
    ReviewedBlock,
    read_reviewed_docx,
)
from jerome.documents.docx_xml import is_block_text
from jerome.errors import Conflict, InvalidChangeError, UnsupportedDocumentError
from jerome.orm import Block, ReviewExport, ReviewImport, Suggestion

JEROME = "Jerome"
PROPERTY = "jerome_review_export_id"
Result = Literal["unchanged", "accepted", "rejected", "edited"]


@dataclass
class SuggestionView:
    id: int
    start: int
    end: int
    original: str
    replacement: str  # the text written into the review copy


@dataclass
class EditorChange:
    start: int
    end: int
    original: str
    replacement: str
    pending: bool


@dataclass
class BlockOutcome:
    results: dict[int, tuple[Result, str | None]]
    editor_changes: list[EditorChange]


@dataclass
class ReviewImportReport:
    review_import_id: int
    counts: dict[str, int]
    unmatched_block_ids: list[int]
    conflicts: list[int]
    notes: list[str] = field(default_factory=list)


Opcode = tuple[str, int, int, int, int]


def _belongs(op: Opcode, s: int, e: int) -> bool:
    _, i1, i2, _, _ = op
    return (i1 < e and i2 > s) or (i1 == i2 and s <= i1 <= e)


def _linear(ops: Sequence[Opcode], pos: int, *, back: bool = False) -> int:
    """Map ``pos`` through the equal opcode containing it (``back``: reviewed -> original)."""
    for tag, i1, i2, j1, j2 in ops:
        a1, a2, b1 = (j1, j2, i1) if back else (i1, i2, j1)
        if tag == "equal" and a1 <= pos <= a2:
            return b1 + pos - a1
    return pos


def _map_span(ops: Sequence[Opcode], s: int, e: int) -> tuple[int, int]:
    belonging = [op for op in ops if op[0] != "equal" and _belongs(op, s, e)]
    if belonging and belonging[0][1] <= s:
        ms = belonging[0][3]
    else:
        ms = _linear(ops, s)
    if belonging and belonging[-1][2] >= e:
        me = belonging[-1][4]
    else:
        me = _linear(ops, e)
    return ms, max(ms, me)


def _map_back(ops: Sequence[Opcode], pos: int, *, end: bool) -> int:
    """Base-view offset -> original offset: inside a changed region, start -> i1, end -> i2."""
    for tag, i1, i2, j1, j2 in ops:
        if tag != "equal" and (j1 < pos < j2 or j1 == pos == j2):
            return i2 if end else i1
    return _linear(ops, pos, back=True)


def _intersects(r: PendingRevision, ms: int, me: int) -> bool:
    if r.start == r.end:
        return ms <= r.start <= me
    return r.start < me and r.end > ms or (ms == me and r.start < ms < r.end)


def _editor_regions(
    original: str, text: str, ops: Sequence[Opcode], spans: Sequence[tuple[int, int]]
) -> list[EditorChange]:
    regions: list[list[int]] = []
    for op in ops:
        tag, i1, i2, j1, j2 = op
        if tag == "equal" or any(_belongs(op, s, e) for s, e in spans):
            continue
        lo = max((e for s, e in spans if e <= i1), default=0)
        hi = min((s for s, e in spans if s >= i2), default=len(original))
        first = original[i1:i2][:1] or text[j1:j2][:1]
        last = original[i1:i2][-1:] or text[j1:j2][-1:]
        while i1 > lo and j1 > 0 and original[i1 - 1].isalnum() and first.isalnum():
            i1, j1 = i1 - 1, j1 - 1
        while i2 < hi and j2 < len(text) and original[i2].isalnum() and last.isalnum():
            i2, j2 = i2 + 1, j2 + 1
        if regions and i1 <= regions[-1][1]:
            regions[-1][1] = max(regions[-1][1], i2)
            regions[-1][3] = max(regions[-1][3], j2)
        else:
            regions.append([i1, i2, j1, j2])
    return [EditorChange(a, b, original[a:b], text[c:d], False) for a, b, c, d in regions]


def _pending_changes(
    original: str, ops: Sequence[Opcode], pending: Sequence[PendingRevision]
) -> list[EditorChange]:
    revisions = [r for r in pending if r.author != JEROME]
    used: set[int] = set()
    changes: list[EditorChange] = []
    for k, r in enumerate(revisions):
        if r.kind != "del":
            continue
        pair = next(
            (
                m
                for m, x in enumerate(revisions)
                if m not in used
                and x.kind == "ins"
                and x.author == r.author
                and x.start in (r.end, r.start)
            ),
            None,
        )
        used.add(k)
        text = ""
        if pair is not None:
            used.add(pair)
            text = revisions[pair].text
        a, b = _map_back(ops, r.start, end=False), _map_back(ops, r.end, end=True)
        changes.append(EditorChange(a, b, original[a:b], text, True))
    for k, r in enumerate(revisions):
        if k not in used:
            q = _map_back(ops, r.start, end=False)
            changes.append(EditorChange(q, q, "", r.text, True))
    return sorted(changes, key=lambda c: (c.start, c.end))


def classify_block(
    original: str, reviewed: ReviewedBlock, suggestions: Sequence[SuggestionView]
) -> BlockOutcome:
    text = reviewed.text
    ops = difflib.SequenceMatcher(None, original, text, autojunk=False).get_opcodes()
    results: dict[int, tuple[Result, str | None]] = {}
    for sv in suggestions:
        ms, me = _map_span(ops, sv.start, sv.end)
        if any(_intersects(r, ms, me) for r in reviewed.pending):
            results[sv.id] = ("unchanged", None)
            continue
        final = text[ms:me]
        if final == sv.replacement:
            results[sv.id] = ("accepted", None)
        elif final == sv.original:
            results[sv.id] = ("rejected", None)
        else:
            results[sv.id] = ("edited", final)
    spans = [(sv.start, sv.end) for sv in suggestions]
    changes = _editor_regions(original, text, ops, spans)
    changes += _pending_changes(original, ops, reviewed.pending)
    return BlockOutcome(results, changes)


def _bookmark_id(rb: ReviewedBlock) -> int | None:
    name = rb.bookmark or ""
    tail = name[len(BOOKMARK_PREFIX) :]
    return int(tail) if name.startswith(BOOKMARK_PREFIX) and tail.isdigit() else None


def match_blocks(
    expected: Sequence[tuple[int, str]], reviewed: Sequence[ReviewedBlock], threshold: float = 0.8
) -> dict[int, ReviewedBlock]:
    """Reviewed block for each expected block id: by bookmark, then by position and text."""
    wanted = {bid for bid, _ in expected}
    position: dict[int, int] = {}  # block id -> index in ``reviewed``
    claimed: set[int] = set()
    for idx, rb in enumerate(reviewed):
        bid = _bookmark_id(rb)
        if bid in wanted and bid not in position:
            position[bid] = idx
            claimed.add(idx)
    order = [bid for bid, _ in expected]
    for k, (bid, original) in enumerate(expected):
        if bid in position:
            continue
        lo = next((position[b] for b in reversed(order[:k]) if b in position), -1)
        hi = next((position[b] for b in order[k + 1 :] if b in position), len(reviewed))
        best, best_ratio = None, 0.0
        for idx in range(lo + 1, hi):
            if idx in claimed:
                continue
            ratio = difflib.SequenceMatcher(
                None, original, reviewed[idx].text, autojunk=False
            ).ratio()
            if ratio >= threshold and ratio > best_ratio:
                best, best_ratio = idx, ratio
        if best is not None:
            position[bid] = best
            claimed.add(best)
    return {
        bid: reviewed[idx] for bid, idx in position.items() if is_block_text(reviewed[idx].text)
    }


def _read(data: bytes):
    try:
        return read_reviewed_docx(data)
    except UnsupportedDocumentError:
        raise
    except Exception as exc:
        raise UnsupportedDocumentError(f"cannot read the review file: {exc}") from exc


def _export_for(session: Session, book_id: int, props: dict[str, str]) -> ReviewExport:
    raw = props.get(PROPERTY, "").strip()
    rx = session.get(ReviewExport, int(raw)) if raw.isdigit() else None
    if rx is None or rx.book_id != book_id:
        raise UnsupportedDocumentError(
            "this file is not a Jerome review copy of this book (missing or unknown export id)"
        )
    return rx


def _current(s: Suggestion) -> tuple[str, str | None]:
    return s.status, (s.final_text if s.status == "edited" else None)


def import_review(session: Session, book_id: int, data: bytes, actor: str) -> ReviewImportReport:
    get_book(session, book_id)
    doc = _read(data)
    rx = _export_for(session, book_id, doc.properties)
    blocks = {b.id: b for b in session.scalars(select(Block).where(Block.id.in_(rx.block_ids)))}
    expected = [(bid, blocks[bid].text) for bid in rx.block_ids if bid in blocks]
    matched = match_blocks(expected, doc.blocks)
    ri = ReviewImport(review_export_id=rx.id, book_id=book_id, report={}, created_by=actor)
    session.add(ri)
    session.flush()

    notes: list[str] = []
    seen = {id(rb) for rb in matched.values()}
    extra = sum(1 for rb in doc.blocks if id(rb) not in seen and is_block_text(rb.text))
    if extra:
        notes.append(
            f"{extra} paragraph(s) in the file match no exported block "
            "(added, split or merged in Word); not imported."
        )

    locked = lock_blocks(session, list(matched))
    exported = {int(k): v for k, v in rx.suggestion_statuses.items()}
    rows = session.scalars(
        select(Suggestion)
        .where(Suggestion.id.in_(exported), Suggestion.block_id.in_(list(matched)))
        .order_by(Suggestion.id)
        .execution_options(populate_existing=True)
    ).all()
    existing_ids = set(session.scalars(select(Suggestion.id).where(Suggestion.id.in_(exported))))
    notes += [f"suggestion {i} no longer exists" for i in sorted(exported.keys() - existing_ids)]

    by_block: dict[int, list[Suggestion]] = {}
    for s in rows:
        by_block.setdefault(s.block_id, []).append(s)
    counts = dict.fromkeys(
        ("accepted", "rejected", "edited", "unchanged", "editor_changes", "conflicts"), 0
    )
    conflicts: list[int] = []
    todo: list[tuple[Suggestion, Result, str | None]] = []
    outcomes: dict[int, BlockOutcome] = {}
    for bid in sorted(matched):
        group = by_block.get(bid, [])
        views = [
            SuggestionView(
                s.id,
                s.start,
                s.end,
                s.original,
                s.final_text
                if exported[s.id] == "edited" and s.status == "edited"
                else s.replacement,
            )
            for s in group
        ]
        out = outcomes[bid] = classify_block(locked[bid].text, matched[bid], views)
        for s, v in zip(group, views, strict=True):
            result, final = out.results[s.id]
            if result == "unchanged":
                counts["unchanged"] += 1
                continue
            target = (
                ("rejected", None)
                if result == "rejected"
                else ("edited", v.replacement)
                if result == "accepted" and exported[s.id] == "edited"
                else ("accepted", None)
                if result == "accepted"
                else ("edited", final)
            )
            if target == _current(s):
                counts["unchanged"] += 1
            elif s.status != exported[s.id]:
                conflicts.append(s.id)
            else:
                todo.append((s, result, target[1]))
    todo.sort(key=lambda t: t[1] != "rejected")  # a reject frees an overlap first
    for s, result, final in todo:
        action = {"accepted": "accept", "rejected": "reject", "edited": "edit"}[result]
        if result == "accepted" and final is not None:
            action = "edit"
        try:
            with session.begin_nested():
                if s.status in ("accepted", "rejected", "edited"):
                    decide(
                        session, s.id, "undo", actor=actor, channel="word", review_import_id=ri.id
                    )
                decide(
                    session,
                    s.id,
                    action,  # type: ignore[arg-type]
                    actor=actor,
                    channel="word",
                    review_import_id=ri.id,
                    replacement=final,
                )
            counts[result] += 1
        except Conflict:
            conflicts.append(s.id)
        except InvalidChangeError as exc:
            notes.append(f"suggestion {s.id} not applied: {exc}")

    for bid in sorted(matched):
        for ch in outcomes[bid].editor_changes:
            _record(session, bid, ch, actor, ri.id, counts, notes)

    counts["conflicts"] = len(conflicts)
    report = ReviewImportReport(
        ri.id, counts, [bid for bid, _ in expected if bid not in matched], conflicts, notes
    )
    ri.report = asdict(report)
    session.flush()
    return report


def _record(
    session: Session,
    block_id: int,
    ch: EditorChange,
    actor: str,
    import_id: int,
    counts: dict[str, int],
    notes: list[str],
) -> None:
    key = ("editor", ch.start, ch.end, ch.replacement)
    existing = session.scalars(
        select(Suggestion)
        .where(Suggestion.block_id == block_id, Suggestion.source == "editor")
        .execution_options(populate_existing=True)
    ).all()
    match = next((s for s in existing if (s.source, s.start, s.end, s.replacement) == key), None)
    try:
        with session.begin_nested():
            if match is not None:
                if match.status != "open" or ch.pending:
                    return
                decide(
                    session,
                    match.id,
                    "accept",
                    actor=actor,
                    channel="word",
                    review_import_id=import_id,
                )
                counts["editor_changes"] += 1
                return
            row = record_editor_change(
                session,
                block_id,
                ch.start,
                ch.end,
                ch.original,
                ch.replacement,
                pending=ch.pending,
                actor=actor,
                review_import_id=import_id,
            )
            if row is not None:
                counts["editor_changes"] += 1
    except (Conflict, InvalidChangeError) as exc:
        notes.append(f"block {block_id}: editor change {ch.start}-{ch.end} not recorded: {exc}")
