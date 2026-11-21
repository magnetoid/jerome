"""Deterministic whole-book consistency pass: spelling variants, house style, quotes, names."""

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from jerome.documents.segments import split_sentences
from jerome.types import BlockText, Candidate, Category, Severity, Source, StyleSheet

_TOKEN = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")
_DOUBLE_QUOTES = frozenset('"“”„‟«»')
_OPENERS = frozenset("([{")


@dataclass(frozen=True)
class _Occ:
    block_id: int
    start: int
    end: int
    original: str
    form: str  # surface form, first letter lowercased when sentence-initial
    initial: bool


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _key(form: str) -> str:
    return form.lower().replace("-", "").replace(" ", "")


def _lower_first(word: str) -> str:
    return word[:1].lower() + word[1:]


def _upper_first(word: str) -> str:
    return word[:1].upper() + word[1:]


def _tokens(block: BlockText, language: str) -> list[tuple[re.Match[str], bool]]:
    starts = set()
    for s, e in split_sentences(block.text, language):  # type: ignore[arg-type]
        m = _TOKEN.search(block.text, s, e)
        if m:
            starts.add(m.start())
    return [(m, m.start() in starts) for m in _TOKEN.finditer(block.text)]


def _quote_candidates(block: BlockText, style: StyleSheet) -> list[Candidate]:
    pair = next(
        (
            p
            for p in (style.quote_style[0:2], style.quote_style[2:4])
            if all(c in _DOUBLE_QUOTES for c in p)
        ),
        None,
    )
    if pair is None:
        return []
    out = []
    for i, ch in enumerate(block.text):
        if ch not in _DOUBLE_QUOTES or ch in style.quote_style:
            continue
        opening = i == 0 or block.text[i - 1].isspace() or block.text[i - 1] in _OPENERS
        repl = pair[0] if opening else pair[1]
        out.append(
            Candidate(
                block.id,
                i,
                i + 1,
                ch,
                repl,
                Category.PUNCTUATION,
                Severity.MEDIUM,
                Source.CONSISTENCY,
                f"Use {repl} as {'opening' if opening else 'closing'} quote for {style.variant}.",
            )
        )
    return out


def _house_candidates(block: BlockText, style: StyleSheet) -> list[Candidate]:
    out = []
    for canonical, variants in style.house_spellings.items():
        for variant in variants:
            for m in re.finditer(rf"(?<!\w){re.escape(variant)}(?!\w)", block.text):
                out.append(
                    Candidate(
                        block.id,
                        m.start(),
                        m.end(),
                        variant,
                        canonical,
                        Category.CONSISTENCY,
                        Severity.MEDIUM,
                        Source.CONSISTENCY,
                        f"House style spells this “{canonical}”.",
                    )
                )
    return out


def _variant_candidates(
    per_block: list[tuple[BlockText, list[tuple[re.Match[str], bool]]]], style: StyleSheet
) -> list[Candidate]:
    singles: list[_Occ] = []
    pairs: list[_Occ] = []
    for block, toks in per_block:
        for idx, (m, initial) in enumerate(toks):
            word = m.group()
            singles.append(_Occ(block.id, m.start(), m.end(), word, word, initial))
            if idx + 1 < len(toks):
                n, _ = toks[idx + 1]
                if n.start() == m.end() + 1 and block.text[m.end()] == " ":
                    joined = f"{word} {n.group()}"
                    pairs.append(
                        _Occ(
                            block.id,
                            m.start(),
                            n.end(),
                            joined,
                            joined,
                            initial,
                        )
                    )
    known = {_key(o.form) for o in singles}
    groups: dict[str, list[_Occ]] = defaultdict(list)
    for o in singles:
        groups[_key(o.form)].append(o)
    for o in pairs:
        if _key(o.form) in known:
            groups[_key(o.form)].append(o)

    ignore = set(style.ignore_words)
    out = []
    for occs in groups.values():
        # sentence-initial tokens do not vote: their capital is positional
        counts = Counter(o.form for o in occs if not o.initial)
        if not counts:
            continue
        ranked = counts.most_common()
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            continue
        dominant, dom_n = ranked[0]
        for o in occs:
            if o.original in ignore:
                continue
            if o.initial:
                if _lower_first(o.original) == _lower_first(dominant):
                    continue
                repl, seen = _upper_first(dominant), 1
            else:
                if o.form == dominant:
                    continue
                repl, seen = dominant, counts[o.form]
            out.append(
                Candidate(
                    o.block_id,
                    o.start,
                    o.end,
                    o.original,
                    repl,
                    Category.CONSISTENCY,
                    Severity.MEDIUM,
                    Source.CONSISTENCY,
                    f"“{o.original}” appears {seen}× but “{dominant}” {dom_n}× in this book.",
                )
            )
    return out


def _name_candidates(
    per_block: list[tuple[BlockText, list[tuple[re.Match[str], bool]]]], style: StyleSheet
) -> list[Candidate]:
    occs: list[tuple[int, re.Match[str]]] = []
    for block, toks in per_block:
        for m, initial in toks:
            w = m.group()
            if not initial and len(w) >= 3 and w[0].isupper():
                occs.append((block.id, m))
    counts = Counter(m.group() for _, m in occs)
    ignore = set(style.ignore_words)
    replacement: dict[str, tuple[str, int]] = {}
    for minor, n in counts.items():
        if minor in ignore:
            continue
        best = [
            (major, k)
            for major, k in counts.items()
            if k > n
            and n + k >= 3
            and abs(len(major) - len(minor)) <= 1
            and _levenshtein(major, minor) == 1
        ]
        if not best:
            continue
        top = max(k for _, k in best)
        winners = [b for b in best if b[1] == top]
        if len(winners) == 1:
            replacement[minor] = winners[0]
    out = []
    for block_id, m in occs:
        if m.group() in replacement:
            major, k = replacement[m.group()]
            out.append(
                Candidate(
                    block_id,
                    m.start(),
                    m.end(),
                    m.group(),
                    major,
                    Category.CONSISTENCY,
                    Severity.LOW,
                    Source.CONSISTENCY,
                    f"“{m.group()}” appears {counts[m.group()]}× but “{major}” {k}×: same name?",
                )
            )
    return out


def consistency_candidates(blocks: Sequence[BlockText], style: StyleSheet) -> list[Candidate]:
    per_block = [(b, _tokens(b, style.language)) for b in blocks]
    out = _variant_candidates(per_block, style)
    if style.language == "en":  # German capitalises every noun: Hand/Hans are ordinary words
        out += _name_candidates(per_block, style)
    for b in blocks:
        out += _quote_candidates(b, style) + _house_candidates(b, style)
    return sorted(out, key=lambda c: (c.block_id, c.start))
