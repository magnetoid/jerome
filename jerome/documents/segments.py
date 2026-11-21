from typing import Literal

import pysbd


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
