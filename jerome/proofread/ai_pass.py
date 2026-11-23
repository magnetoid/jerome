"""Model-backed proofreading pass: chunking, prompting and parsing suggestions."""

import difflib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources

from jerome.documents.model import PLACEHOLDER
from jerome.models.base import CompletionResult, ModelOutputError, Provider, SystemPart
from jerome.types import BlockText, Candidate, Category, Severity, Source, StyleSheet


@dataclass
class Chunk:
    blocks: list[BlockText]
    context: BlockText | None
    sources: dict[int, str] | None = None  # block id -> its source paragraph (translated books)


@dataclass
class AiPassResult:
    candidates: list[Candidate]
    dropped: int
    result: CompletionResult


_ITEM_PROPERTIES = {
    "block_id": {"type": "integer"},
    "original": {"type": "string"},
    "replacement": {"type": "string"},
    "category": {"type": "string", "enum": [c.value for c in Category]},
    "severity": {"type": "string", "enum": [s.value for s in Severity]},
    "explanation": {"type": "string"},
    "occurrence": {
        "type": "integer",
        "description": "1-based index of the occurrence of original within the block.",
    },
}

SUGGESTIONS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _ITEM_PROPERTIES,
                "required": list(_ITEM_PROPERTIES),
                "additionalProperties": False,
            },
        }
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}


def chunk_blocks(blocks: Sequence[BlockText], target_words: int = 2500) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: list[BlockText] = []
    words = 0
    start = 0
    for i, block in enumerate(blocks):
        n = len(block.text.split())
        if current and words + n > target_words:
            chunks.append(Chunk(current, blocks[start - 1] if start else None))
            current, words, start = [], 0, i
        current.append(block)
        words += n
    if current:
        chunks.append(Chunk(current, blocks[start - 1] if start else None))
    return chunks


def _instructions(style: StyleSheet) -> str:
    template = (
        resources.files("jerome.proofread")
        .joinpath("prompts", f"proofread_{style.language}.md")
        .read_text(encoding="utf-8")
    )
    return template.replace("{variant}", style.variant).replace("{quote_style}", style.quote_style)


def _style_text(style: StyleSheet) -> str:
    sheet = {
        "variant": style.variant,
        "quote_style": style.quote_style,
        "house_spellings": {k: sorted(v) for k, v in sorted(style.house_spellings.items())},
        "ignore_words": sorted(style.ignore_words),
    }
    heading = "Style sheet" if style.language == "en" else "Stilblatt"
    return f"{heading}:\n" + json.dumps(sheet, ensure_ascii=False, sort_keys=True, indent=2)


def _translation_check(style: StyleSheet) -> str:
    return (
        resources.files("jerome.proofread")
        .joinpath("prompts", f"translation_check_{style.language}.md")
        .read_text(encoding="utf-8")
    )


_REMINDER = (
    "Reminder: U+FFFC marks an inline object. It must never appear in `original` or "
    "`replacement`. Only propose corrections for the <block> elements above."
)


def build_request(style: StyleSheet, chunk: Chunk) -> tuple[list[SystemPart], str]:
    system = [
        SystemPart(_instructions(style), cache=True),
        SystemPart(_style_text(style), cache=True),
    ]
    if chunk.sources is not None:
        system.append(SystemPart(_translation_check(style), cache=True))
    sources = chunk.sources or {}
    parts: list[str] = []
    if chunk.context is not None:
        parts.append(
            f'<context_block id="{chunk.context.id}" read_only="true">\n'
            f"{chunk.context.text}\n</context_block>"
        )
    for b in chunk.blocks:
        if b.id in sources:
            parts.append(
                f'<source_block for="{b.id}" read_only="true">\n{sources[b.id]}\n</source_block>'
            )
        parts.append(f'<block id="{b.id}">\n{b.text}\n</block>')
    parts.append(_REMINDER)
    return system, "\n\n".join(parts)


_FOLD = {
    **dict.fromkeys("“”„«»‹›", '"'),
    **dict.fromkeys("‘’‚", "'"),
    "…": "...",
}


def _normalize(text: str) -> tuple[str, list[int], list[int]]:
    """Fold typography and whitespace runs. Returns the folded text plus, for each folded
    character, the source start index and exclusive source end index it came from."""
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            j = i
            while j < len(text) and text[j].isspace():
                j += 1
            out.append(" ")
            starts.append(i)
            ends.append(j)
            i = j
            continue
        folded = _FOLD.get(ch, ch)
        for f in folded:
            out.append(f)
            starts.append(i)
            ends.append(i + 1)
        i += 1
    return "".join(out), starts, ends


def _nth_exact(text: str, needle: str, n: int) -> int | None:
    pos = -1
    for _ in range(n):
        pos = text.find(needle, pos + 1 if pos < 0 else pos + len(needle))
        if pos < 0:
            return None
    return pos


@dataclass(frozen=True)
class _Fold:
    """How a tolerant match was made: the folded needle sits at ``pos`` of the folded text."""

    pos: int
    starts: list[int]
    ends: list[int]


def _locate(text: str, original: str, n: int) -> tuple[int, int, _Fold | None] | None:
    pos = _nth_exact(text, original, n)
    if pos is not None:
        return pos, pos + len(original), None
    norm, starts, ends = _normalize(text)
    needle = _normalize(original)[0]
    if not needle:
        return None
    pos = _nth_exact(norm, needle, n)
    if pos is None:
        return None
    return starts[pos], ends[pos + len(needle) - 1], _Fold(pos, starts, ends)


def _anchored_opcodes(a: str, b: str) -> list[tuple[str, int, int, int, int]]:
    """``difflib`` opcodes for ``a`` -> ``b`` with the common prefix and suffix pinned in
    place first, so repeated text ("'teh'teh") cannot pull an unchanged stretch out of
    position. Adjacent equal stretches are merged."""
    p = 0
    while p < min(len(a), len(b)) and a[p] == b[p]:
        p += 1
    s = 0
    while s < min(len(a), len(b)) - p and a[len(a) - 1 - s] == b[len(b) - 1 - s]:
        s += 1
    ops: list[tuple[str, int, int, int, int]] = []
    if p:
        ops.append(("equal", 0, p, 0, p))
    middle = difflib.SequenceMatcher(None, a[p : len(a) - s], b[p : len(b) - s], autojunk=False)
    ops.extend((t, i1 + p, i2 + p, j1 + p, j2 + p) for t, i1, i2, j1, j2 in middle.get_opcodes())
    if s:
        ops.append(("equal", len(a) - s, len(a), len(b) - s, len(b)))
    merged: list[tuple[str, int, int, int, int]] = []
    for op in ops:
        if op[1] == op[2] and op[3] == op[4]:
            continue  # an empty middle
        if merged and op[0] == "equal" and merged[-1][0] == "equal":
            _, i1, _, j1, _ = merged[-1]
            merged[-1] = ("equal", i1, op[2], j1, op[4])
        else:
            merged.append(op)
    return merged


def _rebuild_replacement(text: str, echoed: str, replacement: str, fold: _Fold) -> str:
    """The model's replacement with the typography of the book where it kept the text as is.

    ``echoed`` is what the model sent as the original (folded quotes, plain spaces); the
    book has something else there. Diff the echoed text against the replacement: stretches
    that are equal take the book's characters, the rest is the model's. If the folding
    cannot be undone cleanly, the model's replacement is returned unchanged.
    """
    folded, se, ee = _normalize(echoed)
    # echoed index -> every folded index it produced ("…" folds to three dots)
    ks_of: dict[int, list[int]] = {}
    for k in range(len(folded)):
        for i in range(se[k], ee[k]):
            ks_of.setdefault(i, []).append(k)
    out: list[str] = []
    for tag, i1, i2, j1, j2 in _anchored_opcodes(echoed, replacement):
        if tag == "equal":
            ks = sorted({k for i in range(i1, i2) for k in ks_of[i]})
            # every folded character must lie wholly inside the region ...
            if any(se[k] < i1 or ee[k] > i2 for k in ks) or ks != list(range(ks[0], ks[-1] + 1)):
                return replacement
            lo, hi = fold.pos + ks[0], fold.pos + ks[-1]
            # ... and so must every character of the book that several folded ones came from
            a, b = fold.starts[lo], fold.ends[hi]
            if (lo > 0 and fold.ends[lo - 1] > a) or (
                hi + 1 < len(fold.starts) and fold.starts[hi + 1] < b
            ):
                return replacement
            out.append(text[a:b])
        elif tag != "delete":
            out.append(replacement[j1:j2])
    return "".join(out)


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_suggestions(data: dict, chunk: Chunk) -> tuple[list[Candidate], int]:
    if not isinstance(data, dict) or not isinstance(data.get("suggestions"), list):
        raise ModelOutputError("model output is not an object with a 'suggestions' list")
    blocks = {b.id: b for b in chunk.blocks}
    candidates: list[Candidate] = []
    dropped = 0
    for raw in data["suggestions"]:
        cand = _parse_item(raw, blocks)
        if cand is None:
            dropped += 1
        else:
            candidates.append(cand)
    return candidates, dropped


def _parse_item(raw: object, blocks: dict[int, BlockText]) -> Candidate | None:
    if not isinstance(raw, dict):
        return None
    try:
        block_id, occurrence = raw["block_id"], raw["occurrence"]
        original, replacement, explanation = raw["original"], raw["replacement"], raw["explanation"]
        category, severity = Category(raw["category"]), Severity(raw["severity"])
    except (KeyError, ValueError, TypeError):
        return None
    if not (_is_int(block_id) and _is_int(occurrence)):
        return None
    if not all(isinstance(s, str) for s in (original, replacement, explanation)):
        return None
    block = blocks.get(block_id)
    if block is None or occurrence < 1 or not original or PLACEHOLDER in replacement:
        return None
    found = _locate(block.text, original, occurrence)
    if found is None:
        return None
    start, end, fold = found
    actual = block.text[start:end]
    if PLACEHOLDER in actual:
        return None
    if fold is not None:  # the model echoed folded text: keep the book's typography
        replacement = _rebuild_replacement(block.text, original, replacement, fold)
    if replacement == actual:
        return None
    if category == Category.STYLE:
        severity = Severity.LOW  # spec: style suggestions are low by default
    return Candidate(
        block_id, start, end, actual, replacement, category, severity, Source.MODEL, explanation
    )


def run_ai_pass(
    chunk: Chunk, style: StyleSheet, provider: Provider, model: str, effort: str | None
) -> AiPassResult:
    system, user = build_request(style, chunk)
    result = provider.complete(
        model=model, system=system, user=user, schema=SUGGESTIONS_SCHEMA, effort=effort
    )
    try:
        candidates, dropped = parse_suggestions(result.data, chunk)
        if chunk.sources is None:  # nothing to compare against: a translation flag is unfounded
            kept = [c for c in candidates if c.category != Category.TRANSLATION]
            dropped += len(candidates) - len(kept)
            candidates = kept
    except ModelOutputError as exc:  # the call was billed even though its output is unusable
        raise ModelOutputError(
            str(exc), usage=result.usage, model=result.model, provider=result.provider
        ) from exc
    return AiPassResult(candidates, dropped, result)
