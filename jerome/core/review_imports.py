"""Word round trip: read a reviewed review copy and record the editor's decisions.

``classify_block`` and ``match_blocks`` are pure. ``import_review`` does the database work.
All offsets are code points.
"""

import bisect
import difflib
import itertools
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from jerome.core.books import get_book
from jerome.core.suggestions import decide, lock_blocks, record_editor_change
from jerome.core.translation import ensure_not_source
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


def _pending_hits(pending: Sequence[PendingRevision], ms: int, me: int, sv: SuggestionView) -> bool:
    zero = sv.start == sv.end
    for r in pending:
        if zero:
            # a zero-width suggestion owns only an insertion that carries its own text
            if r.start == r.end and ms <= r.start <= me and sv.replacement in r.text:
                return True
        elif r.start == r.end:
            if ms < r.start < me:
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
    skipped: list[int] = field(default_factory=list)
    extra: list[EditorChange] = field(default_factory=list)
    results: dict[int, tuple[Result, str | None]] = field(default_factory=dict)

    def ins_text(self, pos: int) -> str | None:
        k = self.ins.get(pos)
        if k is None or k in self.claimed or k in self.consumed:
            return None
        return self.text[self.ops[k][3] : self.ops[k][4]]


def _bounds(pl: _Plan, sv: SuggestionView) -> tuple[list[int], int, int, int | None, int | None]:
    """Opcodes overlapping the span, and the reviewed range ``[ms, me)`` they map it to."""
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
    return over, ms, max(ms, me), ins_s, ins_e


def _plan_suggestion(pl: _Plan, sv: SuggestionView, pending: bool) -> None:
    ops, s, e = pl.ops, sv.start, sv.end
    zero = s == e
    over, ms, me, ins_s, ins_e = _bounds(pl, sv)
    pl.consumed.update(over)
    solid = [k for k in over if ops[k][1] != ops[k][2]]
    left = any(ops[k][1] < s for k in solid)
    right = any(ops[k][2] > e for k in solid)
    lo = min([s] + [ops[k][1] for k in solid])
    hi = max([e] + [ops[k][2] for k in solid])
    core = pl.text[ms:me]
    pl.blocked.append((lo, hi))
    if pending:
        pl.results[sv.id] = ("unchanged", None)
        return
    if left and right or (zero and solid):
        pl.results[sv.id] = ("unchanged", None)
        pl.skipped.append(sv.id)
        return
    if left or right:
        fits = [
            (name, f)
            for name, f in (("accepted", sv.replacement), ("rejected", sv.original))
            if (core.endswith(f) if left else core.startswith(f))
        ]
        if not fits:
            pl.results[sv.id] = ("unchanged", None)
            pl.skipped.append(sv.id)
            return
        name, f = max(fits, key=lambda t: len(t[1]))
        pl.results[sv.id] = (name, None)  # type: ignore[assignment]
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
                return
    pl.results[sv.id] = ("edited", core)


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


def _union(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for a, b in sorted(spans):
        if a == b:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _jerome_model(
    revisions: Sequence[PendingRevision],
) -> tuple[list[tuple[int, int]], dict[int, str]]:
    """Jerome's pending deletions (as a union of spans) and insertion text per offset."""
    dels = _union([(r.start, r.end) for r in revisions if r.kind == "del" and r.author == JEROME])
    ins: dict[int, str] = {}
    for r in revisions:
        if r.kind == "ins" and r.author == JEROME:
            ins[r.start] = ins.get(r.start, "") + r.text
    return dels, ins


def _held(
    original: str,
    suggestions: Sequence[SuggestionView],
    results: dict[int, tuple[Result, str | None]],
    changes: Sequence[EditorChange],
) -> tuple[str, dict[int, int]] | None:
    """What Jerome will hold (unchanged = original) plus the editor changes, and where each
    suggestion starts in that text; None if they overlap."""
    segs: list[tuple[int, int, str, int | None]] = [
        (c.start, c.end, c.replacement, None) for c in changes if not c.pending
    ]
    for sv in suggestions:
        result, final = results[sv.id]
        written = {
            "unchanged": sv.original,
            "accepted": sv.replacement,
            "rejected": sv.original,
            "edited": final or "",
        }[result]
        segs.append((sv.start, sv.end, written, sv.id))
    out: list[str] = []
    starts: dict[int, int] = {}
    pos = n = 0
    for a, b, t, sid in sorted(segs, key=lambda x: (x[0], x[1])):
        if a < pos:
            return None
        gap = original[pos:a]
        out += [gap, t]
        n += len(gap)
        if sid is not None:
            starts[sid] = n
        n += len(t)
        pos = b
    out.append(original[pos:])
    return "".join(out), starts


def _pending_explained(
    suggestions: Sequence[SuggestionView],
    results: dict[int, tuple[Result, str | None]],
    starts: dict[int, int],
    revisions: Sequence[PendingRevision],
) -> bool:
    """Jerome's pending revisions are exactly those of the suggestions left unchanged."""
    dels, ins = _jerome_model(revisions)
    spans: list[tuple[int, int]] = []
    used: dict[int, int] = {}
    for sv in suggestions:
        if results[sv.id][0] != "unchanged":
            continue
        t = starts[sv.id]
        spans.append((t, t + len(sv.original)))
        if sv.replacement:
            q = t + len(sv.original)
            at = used.get(q, 0)
            if not ins.get(q, "").startswith(sv.replacement, at):
                return False
            used[q] = at + len(sv.replacement)
    return _union(spans) == dels and all(used.get(q, 0) == len(x) for q, x in ins.items())


def _trimmed_opcodes(a: str, b: str) -> list[Opcode]:
    """Opcodes of a diff that skips the common prefix and suffix (the cost of big blocks)."""
    p = 0
    limit = min(len(a), len(b))
    while p < limit and a[p] == b[p]:
        p += 1
    q = 0
    while q < limit - p and a[len(a) - 1 - q] == b[len(b) - 1 - q]:
        q += 1
    inner = difflib.SequenceMatcher(None, a[p : len(a) - q], b[p : len(b) - q], autojunk=False)
    ops: list[Opcode] = [("equal", 0, p, 0, p)] if p else []
    ops += [(tag, i1 + p, i2 + p, j1 + p, j2 + p) for tag, i1, i2, j1, j2 in inner.get_opcodes()]
    if q:
        ops.append(("equal", len(a) - q, len(a), len(b) - q, len(b)))
    merged: list[Opcode] = []
    for op in ops:
        if merged and op[0] == "equal" and merged[-1][0] == "equal":
            last = merged.pop()
            op = ("equal", last[1], op[2], last[3], op[4])
        merged.append(op)
    return merged


def _candidate(
    original: str, svs: Sequence[SuggestionView], accepted: set[int]
) -> tuple[str, list[tuple[int, int, int, int]]] | None:
    """The original with the accepted suggestions applied, and each span as (cs, ce, s, e)."""
    out: list[str] = []
    spans: list[tuple[int, int, int, int]] = []
    pos = n = 0
    for sv in svs:
        if sv.start < pos:
            return None
        out.append(original[pos : sv.start])
        n += sv.start - pos
        piece = sv.replacement if sv.id in accepted else original[sv.start : sv.end]
        out.append(piece)
        spans.append((n, n + len(piece), sv.start, sv.end))
        n += len(piece)
        pos = sv.end
    out.append(original[pos:])
    return "".join(out), spans


def _exact_assignments(
    original: str,
    text: str,
    svs: Sequence[SuggestionView],
    revisions: Sequence[PendingRevision],
    stats: dict[str, int] | None = None,
) -> list[tuple[set[int], set[int]]]:
    """(accepted, pending) id sets that reproduce ``text`` and Jerome's revisions exactly.

    A suggestion is pending only as a whole: its original lies under Jerome's pending
    deletions and its replacement is consumed, in document order, from the Jerome insertion
    text at its anchor (revisions the reader merged are fine). A branch stops as soon as it
    contradicts that model: text it shows as plain (a gap, an accepted replacement, a
    rejected original) must lie outside the deletions, a pending original wholly inside
    them, and an anchor the search has moved past must be fully consumed. Stops after two
    solutions; ``stats["nodes"]`` is the budget used.
    """
    dels, ins = _jerome_model(revisions)
    ends = [y for _, y in dels]
    anchors = sorted(ins)
    found: list[tuple[set[int], set[int]]] = []
    budget = [20000]

    def cover(a: int, b: int) -> str:
        """How much of base view [a, b) Jerome's pending deletions cover: none, all or part."""
        k = bisect.bisect_right(ends, a)  # the first deletion ending after a
        if a >= b or k == len(dels) or dels[k][0] >= b:
            return "none"
        return "all" if dels[k][0] <= a and b <= dels[k][1] else "part"

    def walk(i, o, t, acc, pend, spans, used, k) -> None:  # noqa: ANN001
        if len(found) > 1 or budget[0] <= 0:
            return
        budget[0] -= 1
        if i == len(svs):
            if (
                text[t:] == original[o:]
                and _union(spans) == dels
                and all(used.get(q, 0) == len(x) for q, x in ins.items())
            ):
                found.append((set(acc), set(pend)))
            return
        sv = svs[i]
        if sv.start < o:
            budget[0] = 0  # overlapping spans: no clear answer
            return
        gap = original[o : sv.start]
        if not text.startswith(gap, t) or cover(t, t + len(gap)) != "none":
            return
        t += len(gap)
        while k < len(anchors) and anchors[k] < t:  # no later suggestion anchors before t
            if used.get(anchors[k], 0) != len(ins[anchors[k]]):
                return
            k += 1
        end = t + len(sv.original)
        if text.startswith(sv.replacement, t) and cover(t, t + len(sv.replacement)) == "none":
            walk(i + 1, sv.end, t + len(sv.replacement), (*acc, sv.id), pend, spans, used, k)
        if not text.startswith(sv.original, t):
            return
        shown = cover(t, end)
        if shown == "none":  # rejected
            walk(i + 1, sv.end, end, acc, pend, spans, used, k)
        if (shown == "all") if sv.original else bool(sv.replacement):  # pending
            q, nxt = end, used
            if sv.replacement:
                at = used.get(q, 0)
                if not ins.get(q, "").startswith(sv.replacement, at):
                    return
                nxt = {**used, q: at + len(sv.replacement)}
            walk(i + 1, sv.end, end, acc, (*pend, sv.id), [*spans, (t, end)], nxt, k)

    walk(0, 0, 0, (), (), [], {}, 0)
    if stats is not None:
        stats["nodes"] = 20000 - budget[0]
    return found if budget[0] > 0 else []


def _assign(
    original: str,
    text: str,
    svs: Sequence[SuggestionView],
    pending: set[int],
    revisions: Sequence[PendingRevision],
    pl: _Plan,
) -> tuple[dict[int, tuple[Result, str | None]], list[EditorChange]] | None:
    """Accepted/rejected assignment of the decided suggestions; None if it is not clear."""

    def results(accepted: set[int], pend: set[int]) -> dict[int, tuple[Result, str | None]]:
        return {
            sv.id: ("unchanged", None)
            if sv.id in pend
            else (("accepted" if sv.id in accepted else "rejected"), None)
            for sv in svs
        }

    exact = _exact_assignments(original, text, svs, revisions)
    if len(exact) == 1:
        return results(*exact[0]), []
    free = [sv for sv in svs if sv.id not in pending]
    if exact or not free:
        return None
    # A suggestion with room around it is read from the aligned text alone; only those close
    # to a neighbour (where alignment is unreliable) are tried both ways.
    options: list[list[bool]] = []
    for sv in free:
        k = svs.index(sv)
        near = (k > 0 and sv.start - svs[k - 1].end <= 2) or (
            k + 1 < len(svs) and svs[k + 1].start - sv.end <= 2
        )
        if near:
            options.append([False, True])
            continue
        _, ms, me, _, _ = _bounds(pl, sv)
        core = text[ms:me]
        options.append(
            [a for a, piece in ((True, sv.replacement), (False, sv.original)) if core == piece]
        )
        if not options[-1]:
            return None
    total = 1
    for o in options:
        total *= len(o)
    if total > 256:
        return None
    cands = []
    for combo in itertools.product(*options):
        accepted = {sv.id for sv, on in zip(free, combo, strict=True) if on}
        built = _candidate(original, svs, accepted)
        if built is None:
            return None
        cands.append((accepted, *built))
    kept = []
    for accepted, cand, spans in cands:
        full = _trimmed_opcodes(cand, text)
        rops = [op for op in full if op[0] != "equal"]
        if all(i2 < cs or i1 > ce for _, i1, i2, _, _ in rops for cs, ce, _, _ in spans):
            size = sum(max(i2 - i1, j2 - j1) for _, i1, i2, j1, j2 in rops)
            kept.append((size, accepted, cand, spans, full))
    if not kept:
        return None
    kept.sort(key=lambda k: k[0])
    if len(kept) > 1 and kept[0][0] == kept[1][0]:
        return None
    _, accepted, cand, spans, full = kept[0]
    pl = _Plan(cand, text, full, _ins_at(full))
    pl.blocked = [(cs, ce) for cs, ce, _, _ in spans]

    def to_original(c: int, *, end: bool) -> int:
        delta = sum(
            (e - s) - (ce - cs)
            for cs, ce, s, e in spans
            if ce < c or (ce == c and (not end or cs < ce))
        )
        return c + delta

    changes = []
    for ch in _expand_regions(pl):
        a = to_original(ch.start, end=False)
        b = to_original(ch.end, end=True) if ch.end > ch.start else a
        changes.append(EditorChange(a, b, original[a:b], ch.replacement, False))
    return results(accepted, pending), changes


def classify_block(
    original: str,
    reviewed: ReviewedBlock,
    suggestions: Sequence[SuggestionView],
    block_id: int | None = None,
) -> BlockOutcome:
    text = reviewed.text
    label = f"block {block_id}" if block_id is not None else "block"
    svs = sorted(suggestions, key=lambda v: (v.start, v.end, v.id))
    ops = difflib.SequenceMatcher(None, original, text, autojunk=False).get_opcodes()
    pl = _Plan(original, text, ops, _ins_at(ops))
    pending = {sv.id for sv in svs if _pending_hits(reviewed.pending, *_bounds(pl, sv)[1:3], sv)}
    found = _assign(original, text, svs, pending, reviewed.pending, pl)
    if found is not None:
        results, changes = found
    else:
        for sv in svs:
            _plan_suggestion(pl, sv, sv.id in pending)
        if pl.skipped:
            ids = ", ".join(map(str, pl.skipped))
            return _fallback(
                suggestions,
                f"{label}: the editor's change runs across the edges of suggestion(s) {ids}; "
                "left as it was",
            )
        results = pl.results
        changes = sorted(_expand_regions(pl) + pl.extra, key=lambda c: (c.start, c.end))
    held = _held(original, svs, results, changes)
    if (
        held is None
        or held[0] != text
        or not _pending_explained(svs, results, held[1], reviewed.pending)
    ):
        return _fallback(
            suggestions, f"{label}: could not align the editor's changes reliably; left as it was"
        )
    return BlockOutcome(results, changes + _pending_changes(original, ops, reviewed.pending))


def _fallback(suggestions: Sequence[SuggestionView], note: str) -> BlockOutcome:
    return BlockOutcome({sv.id: ("unchanged", None) for sv in suggestions}, [], [note])


def _bookmark_ids(rb: ReviewedBlock) -> list[int]:
    ids = []
    for name in rb.bookmarks:
        tail = name[len(BOOKMARK_PREFIX) :]
        if name.startswith(BOOKMARK_PREFIX) and tail.isdecimal() and tail.isascii():
            ids.append(int(tail))
    return ids


@dataclass
class BlockMatch:
    matched: dict[int, ReviewedBlock]  # block id -> its paragraph, safe to classify
    structural: list[int]  # block ids whose paragraph was split or merged in Word
    paragraphs: dict[int, ReviewedBlock]  # matched and structural: every paragraph found


def match_structure(
    expected: Sequence[tuple[int, str]], reviewed: Sequence[ReviewedBlock], threshold: float = 0.8
) -> BlockMatch:
    """Reviewed block for each expected block id: by bookmark, then by position and text.

    A block whose paragraph the editor split or merged (untracked) is *structural*: it is
    reported apart and left out of ``matched``, so nothing is imported for it.
    """
    wanted = {bid: text for bid, text in expected}
    position: dict[int, int] = {}  # block id -> index in ``reviewed``
    claimed: set[int] = set()
    for idx, rb in enumerate(reviewed):
        for bid in _bookmark_ids(rb):
            if bid in wanted and bid not in position:
                position[bid] = idx
                claimed.add(idx)
    by_bookmark = set(position)
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
    found = {bid: idx for bid, idx in position.items() if is_block_text(reviewed[idx].text)}
    structural = [
        bid
        for bid in order
        if bid in found and _is_structural(bid, found, by_bookmark, wanted, reviewed, claimed)
    ]
    paragraphs = {bid: reviewed[idx] for bid, idx in found.items()}
    return BlockMatch(
        {bid: rb for bid, rb in paragraphs.items() if bid not in structural}, structural, paragraphs
    )


def _is_structural(
    bid: int,
    found: dict[int, int],
    by_bookmark: set[int],
    wanted: dict[int, str],
    reviewed: Sequence[ReviewedBlock],
    claimed: set[int],
) -> bool:
    idx = found[bid]
    rb = reviewed[idx]
    if bid in by_bookmark:
        # (a) merge: another exported block starts in the same paragraph
        if sum(1 for b in _bookmark_ids(rb) if b in wanted) > 1:
            return True
        # (b) split: the bookmark starts here but does not end here
        if f"{BOOKMARK_PREFIX}{bid}" not in rb.bookmark_ends:
            return True
    # (c) split by adjacency: the next paragraph is an unclaimed tail of this block's text
    nxt = reviewed[idx + 1] if idx + 1 < len(reviewed) else None
    if nxt is None or idx + 1 in claimed or not is_block_text(nxt.text):
        return False
    if any(b in wanted for b in _bookmark_ids(nxt)):
        return False
    original = wanted[bid]
    return original.endswith(nxt.text) or original.endswith(nxt.text.strip())


def match_blocks(
    expected: Sequence[tuple[int, str]], reviewed: Sequence[ReviewedBlock], threshold: float = 0.8
) -> dict[int, ReviewedBlock]:
    """The reviewed block for each expected block id that can be imported safely."""
    return match_structure(expected, reviewed, threshold).matched


def _read(data: bytes):
    try:
        return read_reviewed_docx(data)
    except UnsupportedDocumentError:
        raise
    except Exception as exc:
        raise UnsupportedDocumentError(f"cannot read the review file: {exc}") from exc


def _export_for(session: Session, book_id: int, props: dict[str, str]) -> ReviewExport:
    raw = props.get(PROPERTY, "").strip()
    rx = session.get(ReviewExport, int(raw)) if raw.isdecimal() and raw.isascii() else None
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
    ensure_not_source(session, book_id)
    get_book(session, book_id)
    doc = _read(data)
    rx = _export_for(session, book_id, doc.properties)
    blocks = {b.id: b for b in session.scalars(select(Block).where(Block.id.in_(rx.block_ids)))}
    expected = [(bid, blocks[bid].text) for bid in rx.block_ids if bid in blocks]
    found = match_structure(expected, doc.blocks)
    matched = found.matched
    ri = ReviewImport(review_export_id=rx.id, book_id=book_id, report={}, created_by=actor)
    session.add(ri)
    session.flush()

    notes: list[str] = [
        f"block {bid}: paragraph split or merged in Word; left as it was."
        for bid in found.structural
    ]
    extra = _unmatched_paragraphs(doc.blocks, found.paragraphs, expected, blocks)
    if extra:
        notes.append(
            f"{extra} paragraph(s) in the file match no exported block "
            "(added, split or merged in Word); not imported."
        )

    exported = {int(k): v for k, v in rx.suggestion_statuses.items()}

    def load() -> dict[int, list[Suggestion]]:
        rows = session.scalars(
            select(Suggestion)
            .where(Suggestion.id.in_(exported), Suggestion.block_id.in_(list(matched)))
            .order_by(Suggestion.id)
            .execution_options(populate_existing=True)
        ).all()
        grouped: dict[int, list[Suggestion]] = {}
        for s in rows:
            grouped.setdefault(s.block_id, []).append(s)
        return grouped

    def views_of(group: list[Suggestion]) -> list[SuggestionView]:
        return [
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

    # classification is pure and can be slow: do it before taking the block locks
    by_block = load()
    classified = {
        bid: (
            v := views_of(by_block.get(bid, [])),
            classify_block(blocks[bid].text, matched[bid], v, bid),
        )
        for bid in sorted(matched)
    }
    lock_blocks(session, list(matched))
    by_block = load()  # statuses and decisions as they are now
    rows = [s for g in by_block.values() for s in g]
    existing_ids = set(session.scalars(select(Suggestion.id).where(Suggestion.id.in_(exported))))
    notes += [f"suggestion {i} no longer exists" for i in sorted(exported.keys() - existing_ids)]
    counts = dict.fromkeys(
        ("accepted", "rejected", "edited", "unchanged", "editor_changes", "conflicts"), 0
    )
    conflicts: list[int] = []
    later, import_exports = _later_decisions(session, rx, [s.id for s in rows])
    todo: list[tuple[Suggestion, Result, str | None]] = []
    outcomes: dict[int, BlockOutcome] = {}
    for bid in sorted(matched):
        group = by_block.get(bid, [])
        views = views_of(group)
        out = outcomes[bid] = classified[bid][1]
        if views != classified[bid][0]:  # changed while we were classifying
            out = outcomes[bid] = classify_block(blocks[bid].text, matched[bid], views, bid)
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
