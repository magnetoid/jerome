from typing import Literal

import pysbd

from jerome.documents.model import BlockKind


def split_sentences(text: str, language: Literal["en", "de"]) -> list[tuple[int, int]]:
    """Return (start, end) offsets of each sentence, without trailing whitespace."""
    segmenter = pysbd.Segmenter(language=language, clean=False, char_span=True)
    spans: list[tuple[int, int]] = []
    for span in segmenter.segment(text):
        sentence = text[span.start : span.end]
        stripped = sentence.rstrip()
        if stripped:
            spans.append((span.start, span.start + len(stripped)))
    return spans


def block_spans(kind: str, text: str, language: Literal["en", "de"]) -> list[tuple[int, int]]:
    """Segment spans of a block: a heading is one segment, other blocks one per sentence."""
    if kind == BlockKind.HEADING:
        return [(0, len(text))] if text else []
    return split_sentences(text, language)
