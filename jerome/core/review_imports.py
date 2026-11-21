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
from jerome.orm import Block, Decision, ReviewExport, ReviewImport, Suggestion

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
    notes: list[str] = field(default_factory=list)


@dataclass
class ReviewImportReport:
    review_import_id: int
    counts: dict[str, int]
    unmatched_block_ids: list[int]
    conflicts: list[int]
    notes: list[str] = field(default_factory=list)


Opcode = tuple[str, int, int, int, int]


def _linear(ops: Sequence[Opcode], pos: int, *, back: bool = False) -> int:
    """Map ``pos`` through the equal opcode containing it (``back``: reviewed -> original)."""
    for tag, i1, i2, j1, j2 in ops:
        a1, a2, b1 = (j1, j2, i1) if back else (i1, i2, j1)
        if tag == "equal" and a1 <= pos <= a2:
            return b1 + pos - a1
    return pos


def _map_back(ops: Sequence[Opcode], pos: int, *, end: bool) -> int:
    """Base-view offset -> original offset: inside a changed region, start -> i1, end -> i2."""
    for tag, i1, i2, j1, j2 in ops:
        if tag != "equal" and (j1 < pos < j2 or j1 == pos == j2):
            return i2 if end else i1
    return _linear(ops, pos, back=True)


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


def _ins_at(ops: Sequence[Opcode]) -> dict[int, int]:
    return {i1: k for k, (tag, i1, i2, _, _) in enumerate(ops) if tag == "insert"}


def _pending_hits(pending: Sequence[PendingRevision], ms: int, me: int) -> bool:
    for r in pending:
        if r.start == r.end:
            if ms < r.start < me or ms == me == r.start:
                return True
        elif r.start < me and r.end > ms:
            return True
    return False


@dataclass
class _Plan:
    """Working state of one block: what each suggestion and editor change accounts for."""

    original: str
    text: str
    ops: list[Opcode]
    ins: dict[int, int]
    claimed: set[int] = field(default_factory=set)  # insertion opcodes claimed by a suggestion
    consumed: set[int] = field(default_factory=set)  # opcodes accounted for by a suggestion
    blocked: list[tuple[int, int]] = field(default_factory=list)
    segs: list[tuple[int, int, str]] = field(default_factory=list)
    extra: list[EditorChange] = field(default_factory=list)
    results: dict[int, tuple[Result, str | None]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def ins_text(self, pos: int) -> str | None:
        k = self.ins.get(pos)
        if k is None or k in self.claimed or k in self.consumed:
            return None
        return self.text[self.ops[k][3] : self.ops[k][4]]


def _plan_suggestion(pl: _Plan, sv: SuggestionView, pending: Sequence[PendingRevision]) -> None:
    ops, s, e = pl.ops, sv.start, sv.end
    zero = s == e
    over = [
        k
        for k, (tag, i1, i2, _, _) in enumerate(ops)
        if tag != "equal"
        and ((i1 != i2 and i1 < e and i2 > s) or (i1 == i2 and (s < i1 < e or (zero and i1 == s))))
    ]
    ins_s, ins_e = (None, None) if zero else (pl.ins.get(s), pl.ins.get(e))
    if over and ops[over[0]][1] <= s:
        ms = ops[over[0]][3]
    elif ins_s is not None:
        ms = ops[ins_s][4]
    else:
        ms = _linear(ops, s)
    if over and ops[over[-1]][2] >= e:
        me = ops[over[-1]][4]
    elif ins_e is not None:
        me = ops[ins_e][3]
    else:
        me = _linear(ops, e)
    me = max(ms, me)
    pl.consumed.update(over)
    solid = [k for k in over if ops[k][1] != ops[k][2]]
    left = any(ops[k][1] < s for k in solid)
    right = any(ops[k][2] > e for k in solid)
    lo = min([s] + [ops[k][1] for k in solid])
    hi = max([e] + [ops[k][2] for k in solid])
    core = pl.text[ms:me]
    pl.blocked.append((lo, hi))
    if _pending_hits(pending, ms, me):
        pl.results[sv.id] = ("unchanged", None)
        pl.segs.append((lo, hi, core))
        return
    if left and right or (zero and solid):
        pl.results[sv.id] = ("unchanged", None)
        pl.segs.append((lo, hi, core))
        pl.notes.append(
            f"suggestion {sv.id}: the editor's change runs across its edges; left as it was"
        )
        return
    if left or right:
        fits = [
            (name, f)
            for name, f in (("accepted", sv.replacement), ("rejected", sv.original))
            if (core.endswith(f) if left else core.startswith(f))
        ]
        if not fits:
            pl.results[sv.id] = ("unchanged", None)
            pl.segs.append((lo, hi, core))
            pl.notes.append(
                f"suggestion {sv.id}: the editor's change runs across its edges; left as it was"
            )
            return
        name, f = max(fits, key=lambda t: len(t[1]))
        pl.results[sv.id] = (name, None)  # type: ignore[assignment]
        pl.segs.append((s, e, f))
        if right:
            rest = core[len(f) :]
            pl.extra.append(EditorChange(e, hi, pl.original[e:hi], rest, False))
        else:
            rest = core[: len(core) - len(f)]
            pl.extra.append(EditorChange(lo, s, pl.original[lo:s], rest, False))
        return
    left_t, right_t = pl.ins_text(s) if not zero else None, pl.ins_text(e) if not zero else None
    options = [(core, False, False)]
    if right_t is not None:
        options.append((core + right_t, False, True))
    if left_t is not None:
        options.append(((left_t or "") + core, True, False))
        if right_t is not None:
            options.append((left_t + core + right_t, True, True))
    for name, target in (("accepted", sv.replacement), ("rejected", sv.original)):
        for cand, use_l, use_r in options:
            if cand == target:
                if use_l and ins_s is not None:
                    pl.claimed.add(ins_s)
                if use_r and ins_e is not None:
                    pl.claimed.add(ins_e)
                pl.results[sv.id] = (name, None)  # type: ignore[assignment]
                pl.segs.append((s, e, target))
                return
    pl.results[sv.id] = ("edited", core)
    pl.segs.append((s, e, core))


def _expand_regions(pl: _Plan) -> list[EditorChange]:
    original, text, ops = pl.original, pl.text, pl.ops
    free = [k for k, op in enumerate(ops) if op[0] != "equal" and k not in pl.consumed]
    free = [k for k in free if k not in pl.claimed]
    taken: set[int] = set()
    changes: list[EditorChange] = []
    for seed in free:
        if seed in taken:
            continue
        a, b = ops[seed][1], ops[seed][2]
        inc = {seed}
        while True:
            lo = max((e for _s, e in pl.blocked if e <= a), default=0)
            hi = min((s for s, _e in pl.blocked if s >= b), default=len(original))
            first = original[a : a + 1] if a < b else _first_text(pl, inc, a)
            last = original[b - 1 : b] if a < b else _last_text(pl, inc, a)
            grown = False
            while a > lo and original[a - 1].isalnum() and first.isalnum():
                a, grown = a - 1, True
                first = original[a]
            while b < hi and original[b].isalnum() and last.isalnum():
                b, grown = b + 1, True
                last = original[b - 1]
            for k in free:
                if k not in inc and ops[k][1] <= b and ops[k][2] >= a:
                    a, b = min(a, ops[k][1]), max(b, ops[k][2])
                    inc.add(k)
                    grown = True
            if not grown:
                break
        taken |= inc
        ja = _bound_start(pl, a, inc)
        jb = _bound_end(pl, b, inc)
        if original[a:b] != text[ja:jb]:
            changes.append(EditorChange(a, b, original[a:b], text[ja:jb], False))
    return changes


def _first_text(pl: _Plan, inc: set[int], pos: int) -> str:
    k = pl.ins.get(pos)
    return pl.text[pl.ops[k][3] : pl.ops[k][4]][:1] if k in inc and k is not None else ""


def _last_text(pl: _Plan, inc: set[int], pos: int) -> str:
    k = pl.ins.get(pos)
    return pl.text[pl.ops[k][3] : pl.ops[k][4]][-1:] if k in inc and k is not None else ""


def _bound_start(pl: _Plan, a: int, inc: set[int]) -> int:
    k = pl.ins.get(a)
    if k is not None:
        return pl.ops[k][3] if k in inc else pl.ops[k][4]
    for k in inc:
        if pl.ops[k][1] == a:
            return pl.ops[k][3]
    return _linear(pl.ops, a)


def _bound_end(pl: _Plan, b: int, inc: set[int]) -> int:
    k = pl.ins.get(b)
    if k is not None:
        return pl.ops[k][4] if k in inc else pl.ops[k][3]
    for k in inc:
        if pl.ops[k][2] == b:
            return pl.ops[k][4]
    return _linear(pl.ops, b)


def _reconstructs(pl: _Plan, changes: Sequence[EditorChange]) -> bool:
    segs = pl.segs + [(c.start, c.end, c.replacement) for c in changes]
    out: list[str] = []
    pos = 0
    for a, b, t in sorted(segs, key=lambda x: (x[0], x[1])):
        if a < pos:
            return False
        out += [pl.original[pos:a], t]
        pos = b
    out.append(pl.original[pos:])
    return "".join(out) == pl.text


def classify_block(
    original: str,
    reviewed: ReviewedBlock,
    suggestions: Sequence[SuggestionView],
    block_id: int | None = None,
) -> BlockOutcome:
    text = reviewed.text
    ops = difflib.SequenceMatcher(None, original, text, autojunk=False).get_opcodes()
    pl = _Plan(original, text, ops, _ins_at(ops))
    for sv in sorted(suggestions, key=lambda v: (v.start, v.end, v.id)):
        _plan_suggestion(pl, sv, reviewed.pending)
    changes = _expand_regions(pl)
    if not _reconstructs(pl, changes + pl.extra):
        label = f"block {block_id}" if block_id is not None else "block"
        return BlockOutcome(
            {sv.id: ("unchanged", None) for sv in suggestions},
            [],
            [f"{label}: could not align the editor's changes reliably; left as it was"],
        )
    changes = sorted(changes + pl.extra, key=lambda c: (c.start, c.end))
    changes += _pending_changes(original, ops, reviewed.pending)
    return BlockOutcome(pl.results, changes, pl.notes)


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


def _unmatched_paragraphs(
    reviewed: Sequence[ReviewedBlock],
    matched: dict[int, ReviewedBlock],
    expected: Sequence[tuple[int, str]],
    blocks: dict[int, Block],
) -> int:
    """Unclaimed block-text paragraphs between matched blocks that are export neighbours."""
    position = {id(rb): i for i, rb in enumerate(reviewed)}
    taken = {id(rb) for rb in matched.values()}
    count = 0
    for (a, _), (b, _) in zip(expected, expected[1:], strict=False):
        if a not in matched or b not in matched or blocks[a].chapter_id != blocks[b].chapter_id:
            continue
        lo, hi = position[id(matched[a])], position[id(matched[b])]
        count += sum(
            1
            for i in range(lo + 1, hi)
            if id(reviewed[i]) not in taken and is_block_text(reviewed[i].text)
        )
    return count


def _later_decisions(
    session: Session, rx: ReviewExport, ids: list[int]
) -> tuple[dict[int, list[int | None]], dict[int, int]]:
    """Decisions made on the suggestions after the export, and which export each import was of."""
    later: dict[int, list[int | None]] = {}
    if rx.last_decision_id is None or not ids:
        return later, {}
    rows = session.execute(
        select(Decision.suggestion_id, Decision.review_import_id).where(
            Decision.suggestion_id.in_(ids), Decision.id > rx.last_decision_id
        )
    )
    for sid, import_id in rows:
        later.setdefault(sid, []).append(import_id)
    wanted = {i for v in later.values() for i in v if i is not None}
    imports = {
        i: x
        for i, x in session.execute(
            select(ReviewImport.id, ReviewImport.review_export_id).where(
                ReviewImport.id.in_(wanted)
            )
        )
    }
    return later, imports


def _is_conflict(
    s: Suggestion,
    exported_status: str,
    rx: ReviewExport,
    later: list[int | None],
    import_exports: dict[int, int],
) -> bool:
    """Changed since the export by anyone but an earlier upload of this same copy."""
    if rx.last_decision_id is None:
        return s.status != exported_status
    if any(i is None or import_exports.get(i) != rx.id for i in later):
        return True
    return not later and s.status != exported_status


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
    extra = _unmatched_paragraphs(doc.blocks, matched, expected, blocks)
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
    later, import_exports = _later_decisions(session, rx, [s.id for s in rows])
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
        out = outcomes[bid] = classify_block(locked[bid].text, matched[bid], views, bid)
        notes += out.notes
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
            elif _is_conflict(s, exported[s.id], rx, later.get(s.id, []), import_exports):
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
