"""Model-backed translation pass: chunking, prompting and mapping the reply back."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources

from jerome.documents.model import PLACEHOLDER, Mark
from jerome.errors import UnsupportedLanguagePair
from jerome.models.base import CompletionResult, ModelOutputError, Provider, SystemPart
from jerome.translate.tags import Decoded, TagError, decode, encode, escape_text
from jerome.types import StyleSheet


@dataclass(frozen=True)
class TranslateItem:
    block_id: int  # the translated block's id
    key: str
    text: str  # the source block's text and marks
    marks: list[Mark]


@dataclass
class TranslateChunk:
    items: list[TranslateItem]


@dataclass
class TranslatePassResult:
    done: dict[int, Decoded]
    failed: list[int]
    marks_dropped: int
    result: CompletionResult


TRANSLATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"block_id": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["block_id", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["blocks"],
    "additionalProperties": False,
}


def chunk_items(items: Sequence[TranslateItem], target_words: int = 1200) -> list[TranslateChunk]:
    """Group items in order into chunks of about ``target_words``. An item is never split;
    one longer than the target gets a chunk of its own."""
    chunks: list[TranslateChunk] = []
    current: list[TranslateItem] = []
    words = 0
    for it in items:
        n = len(it.text.split())
        if current and words + n > target_words:
            chunks.append(TranslateChunk(current))
            current, words = [], 0
        current.append(it)
        words += n
    if current:
        chunks.append(TranslateChunk(current))
    return chunks


def max_tokens_for(chunk: TranslateChunk) -> int:
    """``min(32000, max(4000, ceil(source_chars / 3.5 * 1.6) + 1000))``, where source_chars
    is the length of the tagged source text the model has to give back."""
    chars = sum(len(encode(it.text, it.marks)) for it in chunk.items)
    return min(32000, max(4000, -(-chars * 16 // 35) + 1000))  # exact integer ceil


_IGNORE_HEADING = {
    "en": "Ignore list (names and words to keep exactly as written, untranslated):",
    "de": "Ignorierliste (Namen und Wörter, die unverändert und unübersetzt bleiben):",
}


def _instructions(source: StyleSheet, target: StyleSheet) -> str:
    if source.language == target.language:
        raise UnsupportedLanguagePair(
            f"cannot translate {source.variant} to {target.variant}: same language"
        )
    template = (
        resources.files("jerome.translate")
        .joinpath("prompts", f"translate_{source.language}_{target.language}.md")
        .read_text(encoding="utf-8")
    )
    prompt = (
        template.replace("{source_variant}", source.variant)
        .replace("{target_variant}", target.variant)
        .replace("{quote_style}", target.quote_style)
    )
    ignore = sorted(set(source.ignore_words) | set(target.ignore_words))
    heading = _IGNORE_HEADING[source.language]
    return f"{prompt.rstrip()}\n\n{heading}\n{json.dumps(ignore, ensure_ascii=False)}\n"


_REMINDER = (
    "Translate every <block> above and return each exactly once, by its id. "
    "Keep every tag and ⟦N⟧ token exactly once. <context> is read-only."
)


def build_translate_request(
    source: StyleSheet,
    target: StyleSheet,
    chunk: TranslateChunk,
    context: list[tuple[str, str]],
) -> tuple[list[SystemPart], str]:
    system = [SystemPart(_instructions(source, target), cache=True)]
    parts = [
        f"<context>\n<source>{escape_text(src)}</source>\n"
        f"<translation>{escape_text(done)}</translation>\n</context>"
        for src, done in context
    ]
    parts.extend(
        f'<block id="{it.block_id}">{encode(it.text, it.marks)}</block>' for it in chunk.items
    )
    parts.append(_REMINDER)
    return system, "\n\n".join(parts)


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def run_translate_pass(
    chunk: TranslateChunk,
    source: StyleSheet,
    target: StyleSheet,
    context: list[tuple[str, str]],
    provider: Provider,
    model: str,
    effort: str | None,
) -> TranslatePassResult:
    """One model call for the chunk. ModelRefusal and ModelOutputError propagate; nothing is
    retried here. An item fails when the reply misses it, repeats it, or its tags or
    placeholders don't come back intact. Ids the chunk does not have are ignored."""
    system, user = build_translate_request(source, target, chunk, context)
    result = provider.complete(
        model=model,
        system=system,
        user=user,
        schema=TRANSLATION_SCHEMA,
        max_tokens=max_tokens_for(chunk),
        effort=effort,
    )
    data = result.data
    if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
        raise ModelOutputError(  # the call was billed even though its output is unusable
            "model output is not an object with a 'blocks' list",
            usage=result.usage,
            model=result.model,
            provider=result.provider,
        )
    items = {it.block_id: it for it in chunk.items}
    replies: dict[int, list[object]] = {}
    for raw in data["blocks"]:
        if isinstance(raw, dict) and _is_int(raw.get("block_id")) and raw["block_id"] in items:
            replies.setdefault(raw["block_id"], []).append(raw.get("text"))
    done: dict[int, Decoded] = {}
    failed: list[int] = []
    for block_id, it in items.items():
        texts = replies.get(block_id, [])
        if len(texts) != 1 or not isinstance(texts[0], str):
            failed.append(block_id)
            continue
        try:
            done[block_id] = decode(texts[0], it.marks, it.text.count(PLACEHOLDER))
        except TagError:
            failed.append(block_id)
    marks_dropped = sum(d.marks_dropped for d in done.values())
    return TranslatePassResult(done, failed, marks_dropped, result)
