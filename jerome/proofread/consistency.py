"""Deterministic whole-book consistency pass: spelling variants, house style, quotes, names."""

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from jerome.documents.model import PLACEHOLDER
from jerome.documents.segments import split_sentences
from jerome.types import BlockText, Candidate, Category, Severity, Source, StyleSheet

_TOKEN = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")
_DOUBLE_QUOTES = frozenset('"“”„‟«»')
_OPENERS = frozenset("([{")
_URL = re.compile(r"(?:https?://|www\.)\S+|\S+@\S+\.\S+", re.I)
_LINE_BREAKS = frozenset("\n\r\u2028" + PLACEHOLDER)
# a minority spelling is flagged only when the dominant one is common and clearly dominant
MIN_DOMINANT_COUNT = 3
DOMINANCE_RATIO = 4  # count(dominant) >= DOMINANCE_RATIO * count(minority)


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


def _is_shout(word: str) -> bool:
    """All capitals with two or more letters (a heading, an acronym): no spelling to vote on."""
    letters = [c for c in word if c.isalpha()]
    return len(letters) >= 2 and all(c.isupper() for c in letters)


def _only_case(a: str, b: str) -> bool:
    return a != b and a.lower() == b.lower()


def _only_space(a: str, b: str) -> bool:
    """Differ by a space alone ("along"/"a long"); a hyphen on either side is a real variant."""
    if "-" in a or "-" in b:
        return False
    return a.count(" ") != b.count(" ") and a.replace(" ", "").lower() == b.replace(" ", "").lower()


def _tokens(block: BlockText, language: str) -> list[tuple[re.Match[str], bool]]:
    starts = set()
    for s, e in split_sentences(block.text, language):  # type: ignore[arg-type]
        m = _TOKEN.search(block.text, s, e)
        if m:
            starts.add(m.start())
    # a line break (a placeholder or newline) starts a line of verse: its capital is positional
    for m in _TOKEN.finditer(block.text):
        i = m.start()
        while i > 0 and block.text[i - 1].isspace() and block.text[i - 1] not in _LINE_BREAKS:
            i -= 1
        if i > 0 and block.text[i - 1] in _LINE_BREAKS:
            starts.add(m.start())
    urls = [m.span() for m in _URL.finditer(block.text)]  # addresses are not prose
    return [
        (m, m.start() in starts)
        for m in _TOKEN.finditer(block.text)
        if not any(a <= m.start() < b for a, b in urls)
    ]


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
                rule="quote_style",
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
                        rule="house_spelling",
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
            if _is_shout(word):
                continue
            singles.append(_Occ(block.id, m.start(), m.end(), word, word, initial))
            if idx + 1 < len(toks):
                n, _ = toks[idx + 1]
                if (
                    n.start() == m.end() + 1
                    and block.text[m.end()] == " "
                    and not _is_shout(n.group())
                ):
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
        if dom_n < MIN_DOMINANT_COUNT:
            continue
        for o in occs:
            if o.original in ignore:
                continue
            if o.initial:
                if _lower_first(o.original) == _lower_first(dominant):
                    continue
                repl, seen = _upper_first(dominant), max(1, counts[_lower_first(o.original)])
            else:
                if o.form == dominant:
                    continue
                repl, seen = dominant, counts[o.form]
            if dom_n < DOMINANCE_RATIO * seen:
                continue
            if _only_space(o.original, repl if not o.initial else dominant):
                continue
            if _only_case(o.original, repl) and (
                style.language == "de" or o.original[:1].isupper()
            ):
                continue  # German capitals are grammar; elsewhere a capital minority is positional
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
                    rule="variant",
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
            if not initial and len(w) >= 3 and w[0].isupper() and not _is_shout(w):
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
            and major != minor + "s"  # a plural is not a misspelling
            and minor != major + "s"
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
                    rule="name",
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
