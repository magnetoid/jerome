"""Inline tags for marks and placeholders, so the model sees and returns plain tagged text.

Syntax:

- a mark is ``<b>``, ``<i>``, ``<u>``, ``<sc>`` (smallcaps), ``<s>`` (strike), ``<sup>`` or
  ``<sub>`` around its text; a link is ``<aN>…</aN>``, N being the 1-based index of the link
  among the source's (normalized) link marks. The href stays in Jerome.
- the N-th U+FFFC of the source is ``⟦N⟧`` (1-based).
- literal ``<``, ``>``, ``&``, ``⟦`` and ``⟧`` are ``&lt;``, ``&gt;``, ``&amp;``, ``&#x27E6;``
  and ``&#x27E7;``.

Normalization (:func:`normalize_marks`) is applied to the marks on both sides: empty marks
are dropped, marks of the same kind and href that touch or overlap are merged into one,
subscript is cut out where superscript covers the same text, and the result is sorted by
``(start, end, kind, href)``. ``decode(encode(t, m), m, n, source_text=t)`` returns exactly
``t`` and ``normalize_marks(m)``.
"""

import re
from dataclasses import dataclass

from jerome.documents.model import PLACEHOLDER, Mark, MarkKind
from jerome.errors import JeromeError

_TAG_OF: dict[MarkKind, str] = {
    MarkKind.BOLD: "b",
    MarkKind.ITALIC: "i",
    MarkKind.UNDERLINE: "u",
    MarkKind.SMALLCAPS: "sc",
    MarkKind.STRIKE: "s",
    MarkKind.SUPERSCRIPT: "sup",
    MarkKind.SUBSCRIPT: "sub",
}
_KIND_OF = {tag: kind for kind, tag in _TAG_OF.items()}
# outermost first when several tags open at once and end together
_ORDER = {"b": 1, "i": 2, "u": 3, "sc": 4, "s": 5, "sup": 6, "sub": 7}

_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", "⟦": "&#x27E6;", "⟧": "&#x27E7;"}
_ENTITIES = {"lt": "<", "gt": ">", "amp": "&", "#x27e6": "⟦", "#x27e7": "⟧"}

_LINK_TAG = re.compile(r"a([1-9][0-9]*)")
_TOKEN = re.compile(
    r"&(?P<entity>lt|gt|amp|#x27[eE][67]);"
    r"|&(?P<bad_entity>[#A-Za-z][^;\s]{0,10});"
    r"|⟦(?P<placeholder>[0-9]+)⟧"
    r"|<(?P<close>/?)(?P<tag>[A-Za-z][^<>]*)>"
    r"|(?P<stray>[<⟦⟧" + PLACEHOLDER + r"])"
)


# characters XML 1.0 cannot carry: C0 controls but tab, newline and carriage return; the
# non-characters U+FFFE and U+FFFF; lone surrogates (JSON can carry an escaped one)
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]")

_Key = tuple[MarkKind, str | None]


class TagError(JeromeError):
    """The model's tagged text cannot be mapped back safely."""


@dataclass
class Decoded:
    text: str
    marks: list[Mark]
    placeholders: list[int]  # per placeholder in the text, the 0-based source placeholder
    marks_dropped: int


def normalize_marks(marks: list[Mark]) -> list[Mark]:
    """Drop empty marks, merge touching or overlapping marks of the same kind and href, cut
    subscript out where superscript covers it, and sort by ``(start, end, kind, href)``.
    Returns new Mark objects."""
    merged = _merge(marks)
    sups = [m for m in merged if m.kind == MarkKind.SUPERSCRIPT]
    if sups and any(m.kind == MarkKind.SUBSCRIPT for m in merged):
        merged = [m for m in merged if m.kind != MarkKind.SUBSCRIPT] + [
            piece for m in merged if m.kind == MarkKind.SUBSCRIPT for piece in _subtract(m, sups)
        ]
    return sorted(merged, key=lambda m: (m.start, m.end, m.kind, m.href or ""))


def _subtract(mark: Mark, cuts: list[Mark]) -> list[Mark]:
    """The parts of ``mark`` that no mark in ``cuts`` (sorted, disjoint) covers."""
    out: list[Mark] = []
    start = mark.start
    for c in cuts:
        if c.end <= start or c.start >= mark.end:
            continue
        if c.start > start:
            out.append(Mark(start, c.start, mark.kind, mark.href))
        start = max(start, c.end)
    if start < mark.end:
        out.append(Mark(start, mark.end, mark.kind, mark.href))
    return out


def _merge(marks: list[Mark]) -> list[Mark]:
    merged: list[Mark] = []
    for m in sorted(marks, key=lambda m: (m.kind, m.href or "", m.start, m.end)):
        if m.start >= m.end:
            continue
        last = merged[-1] if merged else None
        if last and (last.kind, last.href) == (m.kind, m.href) and m.start <= last.end:
            last.end = max(last.end, m.end)
        else:
            merged.append(Mark(m.start, m.end, m.kind, m.href))
    return merged


def _escape(text: str) -> str:
    return "".join(_ESCAPES.get(ch, ch) for ch in text)


def escape_text(text: str) -> str:
    """Escape plain text for the model (used for read-only context); U+FFFC is left out."""
    return _escape(text.replace(PLACEHOLDER, ""))


def _tags(marks: list[Mark]) -> list[tuple[str, Mark]]:
    """Each normalized mark with its tag name; links are numbered in order."""
    out: list[tuple[str, Mark]] = []
    links = 0
    for m in marks:
        if m.kind == MarkKind.LINK:
            links += 1
            out.append((f"a{links}", m))
        else:
            out.append((_TAG_OF[m.kind], m))
    return out


def _rank(tag: str) -> tuple[int, int]:
    link = _LINK_TAG.fullmatch(tag)
    return (0, int(link.group(1))) if link else (_ORDER[tag], 0)


def encode(text: str, marks: list[Mark]) -> str:
    """Tag ``text`` for the model.

    The text is split into runs with a constant set of marks. Tags still open from the
    previous run stay open as long as their mark goes on; newly opened tags are nested with
    the longest-lasting mark outermost (ties in the fixed order a, b, i, u, sc, s, sup, sub),
    so nested formatting is written without closing and reopening tags.
    """
    tagged = _tags(normalize_marks(marks))
    cuts = sorted({0, len(text)} | {m.start for _, m in tagged} | {m.end for _, m in tagged})
    out: list[str] = []
    stack: list[str] = []
    placeholder = 0
    for start, end in zip(cuts, cuts[1:], strict=False):
        active = {tag: m.end for tag, m in tagged if m.start <= start < m.end}
        keep = 0
        while keep < len(stack) and stack[keep] in active:
            keep += 1
        out.extend(f"</{tag}>" for tag in reversed(stack[keep:]))
        del stack[keep:]
        opening = sorted(set(active) - set(stack), key=lambda t: (-active[t], _rank(t)))
        out.extend(f"<{tag}>" for tag in opening)
        stack.extend(opening)
        for ch in text[start:end]:
            if ch == PLACEHOLDER:
                placeholder += 1
                out.append(f"⟦{placeholder}⟧")
            else:
                out.append(_ESCAPES.get(ch, ch))
    out.extend(f"</{tag}>" for tag in reversed(stack))
    return "".join(out)


def decode(
    tagged: str,
    source_marks: list[Mark],
    placeholder_count: int,
    *,
    source_text: str | None = None,
) -> Decoded:
    """Map the model's tagged text back to text, marks and placeholders.

    Raises :class:`TagError` for a missing, repeated or out-of-range placeholder token, for
    unbalanced or crossed tags, for an unknown or malformed tag (including ``<!``), for an
    unknown entity (``&`` + ``[#A-Za-z][^;\\s]{0,10};``, e.g. ``&quot;``), for a stray ``⟦``,
    ``⟧`` or raw U+FFFC, and for a character XML 1.0 cannot carry. A tag whose kind (or link
    index) the source does not have keeps its text and loses its mark, counted in
    ``marks_dropped``. Links get the source href by index. Where the reply puts subscript
    under superscript, the subscript is dropped and counted (once per stretch).

    With ``source_text`` (the source block's text, whose U+FFFC are the placeholders):

    - a run of placeholders at the very start or end of the source is put back at the same
      edge of the translation, in source order, wherever the model put it. The tokens move
      with the link marks over them; no text is lost and other marks keep their text.
      Interior placeholders stay where the model put them.
    - each placeholder's non-link marks are exactly those its source placeholder had; the
      marks of the text around it are not changed.
    """
    if bad := _NOT_XML.search(tagged):
        raise TagError(f"character U+{ord(bad.group()):04X} cannot be stored in XML")
    if source_text is not None and source_text.count(PLACEHOLDER) != placeholder_count:
        raise ValueError("placeholder_count does not match the placeholders in source_text")
    source = normalize_marks(source_marks)
    kinds = {m.kind for m in source if m.kind != MarkKind.LINK}
    hrefs = [m.href for m in source if m.kind == MarkKind.LINK]

    parts: list[str] = []
    length = 0
    stack: list[tuple[str, int]] = []
    marks: list[Mark] = []
    placeholders: list[int] = []
    dropped = 0

    def emit(s: str) -> None:
        nonlocal length
        parts.append(s)
        length += len(s)

    pos = 0
    for match in _TOKEN.finditer(tagged):
        emit(tagged[pos : match.start()])
        pos = match.end()
        if entity := match.group("entity"):
            emit(_ENTITIES[entity.lower()])
        elif bad := match.group("bad_entity"):
            raise TagError(f"unknown entity &{bad}; in the reply")
        elif (number := match.group("placeholder")) is not None:
            n = int(number)
            if not 1 <= n <= placeholder_count or n - 1 in placeholders:
                raise TagError(f"placeholder ⟦{number}⟧ is out of range or repeated")
            placeholders.append(n - 1)
            emit(PLACEHOLDER)
        elif (tag := match.group("tag")) is not None:
            if tag not in _KIND_OF and not _LINK_TAG.fullmatch(tag):
                raise TagError(f"unknown tag <{match.group('close')}{tag}>")
            if not match.group("close"):
                stack.append((tag, length))
                continue
            if not stack or stack[-1][0] != tag:
                raise TagError(f"unbalanced or crossed tag </{tag}>")
            _, start = stack.pop()
            if link := _LINK_TAG.fullmatch(tag):
                index = int(link.group(1))
                if index <= len(hrefs):
                    marks.append(Mark(start, length, MarkKind.LINK, hrefs[index - 1]))
                else:
                    dropped += 1
            elif _KIND_OF[tag] in kinds:
                marks.append(Mark(start, length, _KIND_OF[tag]))
            else:
                dropped += 1
        else:
            stray = match.group("stray")
            nxt = tagged[pos : pos + 1]
            if stray != "<" or nxt in ("/", "!") or nxt.isalpha():
                raise TagError(f"stray or malformed {stray!r} in the reply")
            emit(stray)
    emit(tagged[pos:])
    if stack:
        raise TagError(f"unclosed tag <{stack[-1][0]}>")
    if len(placeholders) != placeholder_count:
        raise TagError(f"{placeholder_count - len(placeholders)} placeholder(s) missing")
    return _settle("".join(parts), marks, placeholders, dropped, source, source_text)


def _settle(
    text: str,
    marks: list[Mark],
    placeholders: list[int],
    dropped: int,
    source: list[Mark],
    source_text: str | None,
) -> Decoded:
    """Apply the placeholder rules (with ``source_text``) and the sup/sub rule per character,
    then rebuild the marks."""
    cover: list[set[_Key]] = [set() for _ in text]
    for m in marks:
        for i in range(m.start, m.end):
            cover[i].add((m.kind, m.href))
    order = iter(placeholders)
    units = [
        (ch, next(order) if ch == PLACEHOLDER else None, cover[i]) for i, ch in enumerate(text)
    ]

    if source_text is not None:
        count = len(placeholders)
        lead = len(source_text) - len(source_text.lstrip(PLACEHOLDER))
        trail = 0 if lead == count else len(source_text) - len(source_text.rstrip(PLACEHOLDER))
        edge = set(range(lead)) | set(range(count - trail, count))
        middle = [u for u in units if u[1] not in edge]
        moved = sorted((u for u in units if u[1] in edge), key=lambda u: u[1])
        units = moved[:lead] + middle + moved[lead:]

        at = [i for i, ch in enumerate(source_text) if ch == PLACEHOLDER]
        pinned = [
            {(m.kind, None) for m in source if m.kind != MarkKind.LINK and m.start <= p < m.end}
            for p in at
        ]
        units = [
            (ch, n, cov if n is None else {k for k in cov if k[0] == MarkKind.LINK} | pinned[n])
            for ch, n, cov in units
        ]

    sup, sub = (MarkKind.SUPERSCRIPT, None), (MarkKind.SUBSCRIPT, None)
    cut = False
    for _, _, cov in units:
        both = sup in cov and sub in cov
        if both:
            cov.discard(sub)
            dropped += 0 if cut else 1
        cut = both

    rebuilt: list[Mark] = []
    open_: dict[_Key, int] = {}
    for i, cov in enumerate([u[2] for u in units] + [set()]):
        for key in [k for k in open_ if k not in cov]:
            rebuilt.append(Mark(open_.pop(key), i, key[0], key[1]))
        for key in cov:
            open_.setdefault(key, i)
    return Decoded(
        "".join(u[0] for u in units),
        normalize_marks(rebuilt),
        [u[1] for u in units if u[1] is not None],
        dropped,
    )
