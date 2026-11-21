"""Merge candidates from the model, LanguageTool and the consistency pass."""

from collections.abc import Sequence
from dataclasses import replace

from jerome.core.suggestions import spans_overlap
from jerome.types import Candidate, Source

_SOURCE_RANK = {Source.MODEL: 0, Source.LANGUAGETOOL: 1, Source.CONSISTENCY: 2}


def _priority(c: Candidate) -> tuple[int, int, int]:
    return (-c.severity.rank, _SOURCE_RANK.get(c.source, len(_SOURCE_RANK)), c.start)


def merge_candidates(candidates: Sequence[Candidate]) -> list[Candidate]:
    kept: list[Candidate] = []
    for c in sorted(candidates, key=_priority):
        twin = next(
            (
                i
                for i, k in enumerate(kept)
                if (k.block_id, k.start, k.end, k.replacement)
                == (c.block_id, c.start, c.end, c.replacement)
            ),
            None,
        )
        if twin is not None:
            if c.source == Source.MODEL and kept[twin].source != Source.MODEL:
                kept[twin] = replace(kept[twin], explanation=c.explanation)
            continue
        if any(
            k.block_id == c.block_id and spans_overlap(k.start, k.end, c.start, c.end) for k in kept
        ):
            continue
        kept.append(c)
    return sorted(kept, key=lambda c: (c.block_id, c.start, c.end))
