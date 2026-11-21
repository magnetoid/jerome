"""Precision and recall of proofreading candidates against injected errors."""

from collections.abc import Sequence

from eval.inject import InjectedError
from jerome.types import Candidate


def _overlaps(c: Candidate, t: InjectedError) -> bool:
    if c.start == c.end:
        return t.start <= c.start <= t.end
    return c.start < t.end and t.start < c.end


def _is_tp(c: Candidate, t: InjectedError, wrong_para: str) -> bool:
    """Applying the candidate gives the same text as applying the truth, on the span they cover."""
    if not _overlaps(c, t):
        return False
    lo, hi = min(c.start, t.start), max(c.end, t.end)
    via_candidate = wrong_para[lo : c.start] + c.replacement + wrong_para[c.end : hi]
    via_truth = wrong_para[lo : t.start] + t.correct + wrong_para[t.end : hi]
    return via_candidate == via_truth


def _ratio(n: int, d: int) -> float:
    return n / d if d else 0.0


def score(
    found: Sequence[Candidate],
    truth: Sequence[InjectedError],
    block_ids: list[int],
    *,
    paragraphs: Sequence[str],
) -> dict[str, dict[str, float]]:
    """Precision and recall overall and per ``"<source>/<category>"``."""
    by_para: dict[int, list[int]] = {}
    for ti, t in enumerate(truth):
        by_para.setdefault(t.paragraph, []).append(ti)
    # for each candidate, the indices of the truths it correctly fixes
    hits: list[set[int]] = []
    for c in found:
        matched: set[int] = set()
        if c.block_id in block_ids:
            para = block_ids.index(c.block_id)
            for ti in by_para.get(para, []):
                if _is_tp(c, truth[ti], paragraphs[para]):
                    matched.add(ti)
        hits.append(matched)

    def stats(cands: list[int], truths: list[int], sources: set[str] | None) -> dict[str, float]:
        tp = sum(1 for ci in cands if hits[ci])
        pool = [ci for ci in range(len(found)) if sources is None or found[ci].source in sources]
        covered = {ti for ci in pool for ti in hits[ci]}
        return {
            "precision": _ratio(tp, len(cands)),
            "recall": _ratio(sum(1 for ti in truths if ti in covered), len(truths)),
        }

    result = {"overall": stats(list(range(len(found))), list(range(len(truth))), None)}
    sources = {c.source for c in found}
    keys = {(c.source, c.category) for c in found}
    keys |= {(s, t.category) for s in sources for t in truth}
    for source, category in sorted(keys, key=lambda k: (k[0].value, k[1].value)):
        cands = [i for i, c in enumerate(found) if c.source == source and c.category == category]
        truths = [i for i, t in enumerate(truth) if t.category == category]
        result[f"{source.value}/{category.value}"] = stats(cands, truths, {source})
    return result
