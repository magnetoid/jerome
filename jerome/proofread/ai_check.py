"""Ask the AI about LanguageTool's marks in one paragraph: the request and the reply.

Each mark gets one rating: ``error`` (definitely an error), ``maybe_error`` or
``not_an_error`` (definitely fine). With either error rating comes the AI's fix: one of
LanguageTool's options, or its own text. Items that cannot be used are dropped."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources

from jerome.documents.changes import check_replacement_text
from jerome.errors import InvalidChangeError
from jerome.models.base import ModelOutputError, SystemPart
from jerome.proofread.ai_pass import _style_text
from jerome.types import AI_VERDICTS, StyleSheet

CONTEXT_CHARS = 40  # of the paragraph on each side of a mark
CONTEXT_SENTENCES = 3  # whole sentences the AI reads on each side of the marks, at least
CONTEXT_LIMIT = 1500  # characters of neighbouring paragraphs on each side, at most

# A run of sentence-ending marks with any closing quotes or brackets after it
_END = re.compile(r"[.!?…]+[\"'”’»«“‘›‹)\]]*")


@dataclass(frozen=True)
class Mark:
    id: int
    start: int
    end: int
    original: str
    replacement: str  # LanguageTool's fix
    message: str  # LanguageTool's explanation
    options: tuple[str, ...] = ()  # all of LanguageTool's replacements; () for an older mark

    @property
    def choices(self) -> tuple[str, ...]:
        return self.options or (self.replacement,)


@dataclass(frozen=True)
class Verdict:
    id: int
    verdict: str
    replacement: str | None  # the AI's fix; None for not_an_error
    reason: str


_ITEM = {
    "id": {"type": "integer"},
    "verdict": {"type": "string", "enum": list(AI_VERDICTS)},
    "replacement": {
        "type": "string",
        "description": (
            "Your fix for the mark's span: one of LanguageTool's options word for word, or "
            "your own text. Empty for not_an_error."
        ),
    },
    "reason": {"type": "string", "description": "One short sentence, in English."},
}
VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _ITEM,
                "required": list(_ITEM),
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def _instructions(style: StyleSheet) -> str:
    template = (
        resources.files("jerome.proofread")
        .joinpath("prompts", f"ai_check_{style.language}.md")
        .read_text(encoding="utf-8")
    )
    return template.replace("{variant}", style.variant).replace("{quote_style}", style.quote_style)


def _sentences(text: str) -> list[tuple[int, int]]:
    """The (start, end) span of each sentence. A sentence ends at an end mark followed by a
    space (or the text's end) and a word that is not lowercase, so "e.g. this", "3.50" and
    "„Komm!“ rief er" do not end one."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _END.finditer(text):
        rest = text[m.end() :]
        if rest and not rest[0].isspace():
            continue
        following = rest.lstrip()
        if following and following[0].islower():
            continue
        if text[start : m.end()].strip():
            spans.append((start, m.end()))
        start = m.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def count_sentences(text: str) -> int:
    return len(_sentences(text))


def room_around(text: str, marks: Sequence[Mark]) -> tuple[int, int]:
    """The whole sentences of the paragraph before the first mark's sentence and after the
    last mark's sentence."""
    spans = _sentences(text)
    if not spans or not marks:
        return 0, 0
    first = min(m.start for m in marks)
    last = max(max(m.end, m.start + 1) for m in marks) - 1
    i0 = next((i for i, (_, end) in enumerate(spans) if first < end), len(spans) - 1)
    i1 = next((i for i, (_, end) in enumerate(spans) if last < end), len(spans) - 1)
    return i0, len(spans) - 1 - i1


def pick_context(near_first: Sequence[str], have: int) -> list[str]:
    """The neighbouring paragraphs (nearest first) to show on one side: always the nearest,
    then more until ``CONTEXT_SENTENCES`` whole sentences are there, within
    ``CONTEXT_LIMIT`` characters."""
    picked: list[str] = []
    chars = 0
    for text in near_first:
        if picked and (have >= CONTEXT_SENTENCES or chars + len(text) > CONTEXT_LIMIT):
            break
        picked.append(text)
        chars += len(text)
        have += count_sentences(text)
    return picked


def _in_context(text: str, mark: Mark) -> str:
    lo, hi = max(0, mark.start - CONTEXT_CHARS), min(len(text), mark.end + CONTEXT_CHARS)
    before = ("…" if lo else "") + text[lo : mark.start]
    after = text[mark.end : hi] + ("…" if hi < len(text) else "")
    return f"{before}⟦{text[mark.start : mark.end]}⟧{after}"


def build_request(
    style: StyleSheet,
    text: str,
    marks: list[Mark],
    *,
    before: Sequence[str],
    after: Sequence[str],
    source: str | None,
) -> tuple[list[SystemPart], str]:
    """``before`` and ``after`` are the neighbouring paragraphs, in reading order."""
    system = [
        SystemPart(_instructions(style), cache=True),
        SystemPart(_style_text(style), cache=True),
    ]
    parts: list[str] = []
    if before:
        joined = "\n\n".join(before)
        parts.append(f'<previous_paragraphs read_only="true">\n{joined}\n</previous_paragraphs>')
    if source is not None:
        parts.append(f'<source_paragraph read_only="true">\n{source}\n</source_paragraph>')
    parts.append(f"<paragraph>\n{text}\n</paragraph>")
    if after:
        joined = "\n\n".join(after)
        parts.append(f'<following_paragraphs read_only="true">\n{joined}\n</following_paragraphs>')
    for m in marks:
        parts.append(
            f'<mark id="{m.id}">\n'
            f"marked text: {m.original}\n"
            f"in context: {_in_context(text, m)}\n"
            "LanguageTool's options:\n"
            + "".join(f"{i}. {option}\n" for i, option in enumerate(m.choices, 1))
            + f"LanguageTool's message: {m.message}\n"
            "</mark>"
        )
    parts.append("Give one verdict for each <mark> above, by its id.")
    return system, "\n\n".join(parts)


def parse_verdicts(data: dict, marks: list[Mark]) -> list[Verdict]:
    """The usable verdicts, in reply order, at most one per mark. Raises ModelOutputError when
    the reply has no ``verdicts`` list."""
    raw_items = data.get("verdicts") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        raise ModelOutputError("model output is not an object with a 'verdicts' list")
    by_id = {m.id: m for m in marks}
    out: dict[int, Verdict] = {}
    for raw in raw_items:
        verdict = _parse_item(raw, by_id)
        if verdict is not None and verdict.id not in out:
            out[verdict.id] = verdict
    return list(out.values())


def _parse_item(raw: object, marks: dict[int, Mark]) -> Verdict | None:
    if not isinstance(raw, dict):
        return None
    mark_id, verdict = raw.get("id"), raw.get("verdict")
    replacement, reason = raw.get("replacement"), raw.get("reason")
    if type(mark_id) is not int or mark_id not in marks or verdict not in AI_VERDICTS:
        return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    mark = marks[mark_id]
    if verdict == "not_an_error":
        return Verdict(mark_id, verdict, None, reason.strip())
    if not isinstance(replacement, str):
        return None
    fix = replacement or mark.choices[0]  # no fix named: LanguageTool's first option
    if fix == mark.original:
        return None
    try:
        check_replacement_text(fix)
    except InvalidChangeError:
        return None
    return Verdict(mark_id, verdict, fix, reason.strip())
