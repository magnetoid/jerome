import itertools
from dataclasses import replace

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


def test_twins_keep_the_ai_rating_and_mark_agreed():
    lt = cand(0, 3, Source.LANGUAGETOOL, Severity.HIGH, expl="lt")
    model = cand(0, 3, Source.MODEL, Severity.MEDIUM, expl="model")
    for order in ([lt, model], [model, lt]):
        (out,) = merge_candidates(order)
        assert out.source == Source.MODEL and out.severity == Severity.MEDIUM
        assert out.explanation == "model" and out.agreed


def test_identical_model_candidates_are_not_agreement():
    a = cand(0, 3, Source.MODEL)
    (out,) = merge_candidates([a, a])
    assert not out.agreed


def test_twins_without_the_ai_keep_the_higher_ranked_and_are_not_agreed():
    lt = cand(0, 3, Source.LANGUAGETOOL, Severity.HIGH)
    cons = cand(0, 3, Source.CONSISTENCY, Severity.HIGH)
    (out,) = merge_candidates([cons, lt])
    assert out.source == Source.LANGUAGETOOL and not out.agreed


def test_twin_keeps_the_consistency_rule():
    cons = replace(cand(0, 3, Source.CONSISTENCY), rule="house_spelling")
    for other in (Source.LANGUAGETOOL, Source.MODEL):
        twin = cand(0, 3, other, Severity.HIGH)
        for order in ([twin, cons], [cons, twin]):
            (out,) = merge_candidates(order)
            assert out.rule == "house_spelling"


def test_three_way_twin_keeps_rule_and_agreed():
    model = cand(0, 3, Source.MODEL, Severity.LOW)
    lt = cand(0, 3, Source.LANGUAGETOOL, Severity.HIGH)
    cons = replace(cand(0, 3, Source.CONSISTENCY, Severity.MEDIUM), rule="variant")
    for order in itertools.permutations([model, lt, cons]):
        (out,) = merge_candidates(list(order))
        assert out.source == Source.MODEL and out.agreed and out.rule == "variant"


def test_same_source_dispute_vanishes_when_a_model_twin_takes_over():
    k = cand(0, 3, Source.LANGUAGETOOL, Severity.HIGH, repl="a")
    d = cand(1, 2, Source.MODEL, Severity.MEDIUM, repl="b")
    m = cand(0, 3, Source.MODEL, Severity.LOW, repl="a")
    for order in itertools.permutations([k, d, m]):
        (out,) = merge_candidates(list(order))
        assert out.source == Source.MODEL and out.agreed
        assert not out.disputed and out.alternative is None


def test_dispute_appears_when_a_model_twin_takes_over_from_same_source_conflict():
    k = cand(0, 3, Source.LANGUAGETOOL, Severity.HIGH, repl="a")
    d = cand(1, 2, Source.LANGUAGETOOL, Severity.MEDIUM, repl="b")
    m = cand(0, 3, Source.MODEL, Severity.LOW, repl="a")
    for order in itertools.permutations([k, d, m]):
        (out,) = merge_candidates(list(order))
        assert out.source == Source.MODEL and out.disputed and out.alternative == "b"


def test_first_dropped_alternative_wins_even_when_empty():
    model = cand(0, 5, Source.MODEL, Severity.HIGH, repl="a")
    deletion = cand(1, 2, Source.LANGUAGETOOL, Severity.MEDIUM, repl="")
    other = cand(2, 3, Source.CONSISTENCY, Severity.MEDIUM, repl="z")
    for order in ([model, deletion, other], [other, deletion, model]):
        (out,) = merge_candidates(order)
        assert out.disputed and out.alternative == ""


def test_equal_priority_candidates_merge_the_same_in_any_order():
    a = cand(0, 3, Source.MODEL, repl="a")
    b = cand(0, 3, Source.MODEL, repl="b")
    lt = cand(0, 3, Source.LANGUAGETOOL, repl="c")
    results = {tuple(merge_candidates(list(o))) for o in itertools.permutations([a, b, lt])}
    assert len(results) == 1


def test_different_fix_for_overlapping_span_marks_disputed_with_alternative():
    model = cand(0, 5, Source.MODEL, Severity.HIGH, repl="a")
    lt = cand(2, 4, Source.LANGUAGETOOL, repl="b")
    cons = cand(1, 3, Source.CONSISTENCY, repl="c")
    (out,) = merge_candidates([cons, lt, model])
    assert out.source == Source.MODEL and out.disputed and out.alternative == "b"


def test_dispute_marks_every_overlapped_kept_candidate():
    a = cand(0, 3, Source.MODEL, Severity.HIGH, repl="a")
    b = cand(4, 7, Source.MODEL, Severity.HIGH, repl="b")
    lt = cand(2, 5, Source.LANGUAGETOOL, repl="z")
    out = merge_candidates([a, b, lt])
    assert [(c.disputed, c.alternative) for c in out] == [(True, "z"), (True, "z")]


def test_same_source_overlap_is_not_a_dispute():
    a = cand(0, 5, Source.MODEL, Severity.HIGH, repl="a")
    b = cand(2, 4, Source.MODEL, repl="b")
    (out,) = merge_candidates([a, b])
    assert not out.disputed and out.alternative is None


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
