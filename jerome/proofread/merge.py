"""Merge candidates from the model, LanguageTool and the consistency pass."""

from collections.abc import Sequence
from dataclasses import replace

from jerome.core.suggestions import spans_overlap
from jerome.types import Candidate, Source

_SOURCE_RANK = {Source.MODEL: 0, Source.LANGUAGETOOL: 1, Source.CONSISTENCY: 2}


def _priority(c: Candidate) -> tuple[int, int, int]:
    return (-c.severity.rank, _SOURCE_RANK.get(c.source, len(_SOURCE_RANK)), c.start)


def _combine_twins(kept: Candidate, c: Candidate) -> Candidate:
    """Fold twin `c` into `kept`: the model's version wins, and a model plus another checker
    is agreement. The consistency rule survives whichever twin carried it."""
    rule = kept.rule or c.rule
    if Source.MODEL in (kept.source, c.source) and kept.source != c.source:
        model_twin = c if c.source == Source.MODEL else kept
        return replace(model_twin, agreed=True, rule=rule)
    return replace(kept, rule=rule)


def _record_disputes(kept: list[Candidate], dropped: Sequence[Candidate]) -> None:
    """Mark kept candidates that a dropped one of another source disputes with another fix.
    Runs after twins are settled, so sources are those of the final kept candidates."""
    for d in dropped:
        for i, k in enumerate(kept):
            if (
                k.block_id == d.block_id
                and spans_overlap(k.start, k.end, d.start, d.end)
                and k.source != d.source
                and k.replacement != d.replacement
            ):
                kept[i] = replace(
                    k,
                    disputed=True,
                    alternative=d.replacement if k.alternative is None else k.alternative,
                )


def merge_candidates(candidates: Sequence[Candidate]) -> list[Candidate]:
    kept: list[Candidate] = []
    dropped: list[Candidate] = []
    # end and replacement break ties so the result does not depend on input order
    for c in sorted(candidates, key=lambda c: (*_priority(c), c.end, c.replacement)):
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
            kept[twin] = _combine_twins(kept[twin], c)
        elif any(
            k.block_id == c.block_id and spans_overlap(k.start, k.end, c.start, c.end) for k in kept
        ):
            dropped.append(c)
        else:
            kept.append(c)
    _record_disputes(kept, dropped)
    return sorted(kept, key=lambda c: (c.block_id, c.start, c.end))
