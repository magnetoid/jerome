from jerome.proofread.merge import merge_candidates
from jerome.types import Candidate, Category, Severity, Source


def cand(start, end, source, severity=Severity.MEDIUM, repl="x", expl="e", block=1):
    return Candidate(
        block, start, end, "o" * (end - start), repl, Category.STYLE, severity, source, expl
    )


def test_merge_prefers_higher_severity():
    lt = cand(0, 3, Source.LANGUAGETOOL, Severity.HIGH)
    model = cand(0, 7, Source.MODEL, Severity.LOW)
    assert merge_candidates([model, lt]) == [lt]


def test_merge_tie_prefers_model():
    lt = cand(0, 3, Source.LANGUAGETOOL)
    model = cand(2, 5, Source.MODEL)
    cons = cand(1, 4, Source.CONSISTENCY)
    assert merge_candidates([cons, lt, model]) == [model]


def test_merge_identical_keeps_one_with_model_explanation():
    lt = cand(0, 3, Source.LANGUAGETOOL, Severity.HIGH, expl="lt")
    model = cand(0, 3, Source.MODEL, Severity.LOW, expl="model")
    out = merge_candidates([lt, model])
    assert len(out) == 1
    assert out[0].source == Source.LANGUAGETOOL and out[0].severity == Severity.HIGH
    assert out[0].explanation == "model"


def test_merge_non_overlapping_keeps_all_sorted():
    a = cand(4, 7, Source.MODEL)
    b = cand(0, 3, Source.LANGUAGETOOL)
    c = cand(0, 3, Source.MODEL, block=0)
    assert merge_candidates([a, b, c]) == [c, b, a]


def test_merge_overlap_only_within_same_block():
    a = cand(0, 3, Source.MODEL, block=1)
    b = cand(0, 3, Source.LANGUAGETOOL, block=2, repl="y")
    assert merge_candidates([a, b]) == [a, b]


def test_merge_touching_spans_both_kept():
    a = cand(0, 3, Source.MODEL)
    b = cand(3, 5, Source.MODEL)
    assert merge_candidates([b, a]) == [a, b]
