"""Ask the AI about LanguageTool's marks in one paragraph: the request and the reply.

Each mark gets one verdict: ``correct`` (LanguageTool's fix stands), ``not_an_error``, or
``better`` (a better text for the same span). Items that cannot be used are dropped."""

from dataclasses import dataclass
from importlib import resources

from jerome.documents.changes import check_replacement_text
from jerome.errors import InvalidChangeError
from jerome.models.base import ModelOutputError, SystemPart
from jerome.proofread.ai_pass import _style_text
from jerome.types import AI_VERDICTS, StyleSheet

CONTEXT_CHARS = 40  # of the paragraph on each side of a mark


@dataclass(frozen=True)
class Mark:
    id: int
    start: int
    end: int
    original: str
    replacement: str  # LanguageTool's fix
    message: str  # LanguageTool's explanation


@dataclass(frozen=True)
class Verdict:
    id: int
    verdict: str
    replacement: str | None  # only for "better"
    reason: str


_ITEM = {
    "id": {"type": "integer"},
    "verdict": {"type": "string", "enum": list(AI_VERDICTS)},
    "replacement": {
        "type": "string",
        "description": "The better text for the mark's span; empty unless verdict is better.",
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
    previous: str | None,
    source: str | None,
) -> tuple[list[SystemPart], str]:
    system = [
        SystemPart(_instructions(style), cache=True),
        SystemPart(_style_text(style), cache=True),
    ]
    parts: list[str] = []
    if previous is not None:
        parts.append(f'<previous_paragraph read_only="true">\n{previous}\n</previous_paragraph>')
    if source is not None:
        parts.append(f'<source_paragraph read_only="true">\n{source}\n</source_paragraph>')
    parts.append(f"<paragraph>\n{text}\n</paragraph>")
    for m in marks:
        parts.append(
            f'<mark id="{m.id}">\n'
            f"marked text: {m.original}\n"
            f"in context: {_in_context(text, m)}\n"
            f"LanguageTool's fix: {m.replacement}\n"
            f"LanguageTool's message: {m.message}\n"
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
    if verdict != "better":
        return Verdict(mark_id, verdict, None, reason.strip())
    if not isinstance(replacement, str) or not replacement or replacement == mark.original:
        return None
    try:
        check_replacement_text(replacement)
    except InvalidChangeError:
        return None
    if replacement == mark.replacement:  # the same fix LanguageTool proposed
        return Verdict(mark_id, "correct", None, reason.strip())
    return Verdict(mark_id, "better", replacement, reason.strip())
