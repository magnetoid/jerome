"""Inline tags for marks and placeholders, so the model sees and returns plain tagged text.

Syntax:

- a mark is ``<b>``, ``<i>``, ``<u>``, ``<sc>`` (smallcaps), ``<s>`` (strike), ``<sup>`` or
  ``<sub>`` around its text; a link is ``<aN>…</aN>``, N being the 1-based index of the link
  among the source's (normalized) link marks. The href stays in Jerome.
- the N-th U+FFFC of the source is ``⟦N⟧`` (1-based).
- literal ``<``, ``>``, ``&``, ``⟦`` and ``⟧`` are ``&lt;``, ``&gt;``, ``&amp;``, ``&#x27E6;``
  and ``&#x27E7;``.

Normalization (:func:`normalize_marks`) is applied to the marks on both sides: empty marks
are dropped, marks of the same kind and href that touch or overlap are merged into one, and
the result is sorted by ``(start, end, kind, href)``. ``decode(encode(t, m), m, n)`` returns
exactly ``t`` and ``normalize_marks(m)``.
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


class TagError(JeromeError):
    """The model's tagged text cannot be mapped back safely."""


@dataclass
class Decoded:
    text: str
    marks: list[Mark]
    placeholders: list[int]  # per placeholder in the text, the 0-based source placeholder
    marks_dropped: int


def normalize_marks(marks: list[Mark]) -> list[Mark]:
    """Drop empty marks, merge touching or overlapping marks of the same kind and href, and
    sort by ``(start, end, kind, href)``. Returns new Mark objects."""
    merged: list[Mark] = []
    for m in sorted(marks, key=lambda m: (m.kind, m.href or "", m.start, m.end)):
        if m.start >= m.end:
            continue
        last = merged[-1] if merged else None
        if last and (last.kind, last.href) == (m.kind, m.href) and m.start <= last.end:
            last.end = max(last.end, m.end)
        else:
            merged.append(Mark(m.start, m.end, m.kind, m.href))
    return sorted(merged, key=lambda m: (m.start, m.end, m.kind, m.href or ""))


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


def decode(tagged: str, source_marks: list[Mark], placeholder_count: int) -> Decoded:
    """Map the model's tagged text back to text, marks and placeholders.

    Raises :class:`TagError` for a missing, repeated or out-of-range placeholder token, for
    unbalanced or crossed tags, for an unknown or malformed tag (including ``<!``), for an
    unknown entity (``&`` + ``[#A-Za-z][^;\\s]{0,10};``, e.g. ``&quot;``), and for a stray ``⟦``,
    ``⟧`` or raw U+FFFC. A tag whose kind (or link index) the source does not have keeps its
    text and loses its mark, counted in ``marks_dropped``. Links get the source href by index.
    """
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
    return Decoded("".join(parts), normalize_marks(marks), placeholders, dropped)
