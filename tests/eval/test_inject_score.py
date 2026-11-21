from decimal import Decimal

import pytest

from eval.fetch_texts import paragraphs_from, strip_gutenberg
from eval.inject import InjectedError, inject_errors
from eval.run import build_sample, run_eval
from eval.score import score
from jerome.config import Settings
from jerome.models import FakeProvider, Registry, Usage
from jerome.types import Candidate, Category, Severity, Source

PARAS_EN = [
    "The colour of the old house was grey, which surprised everyone who walked past it every morning.",
    "However, the neighbours believed that she was happy with the garden and the quiet street outside.",
    "He is certain that the colour of the door will change before the winter arrives in the village.",
    "She has written many letters to friends about the weather and the strange quiet in the valley.",
    "Meanwhile the children played near the river, which was cold and fast after the heavy autumn rain.",
    "It does seem strange that nobody noticed the broken window until the carpenter came on Tuesday.",
    "Indeed, everything about the afternoon seemed ordinary until the visitor arrived with a large brown parcel.",
    "The grey sky hung over the market while merchants carried their baskets towards the crowded square.",
    "Nothing could prevent the travellers from reaching the station before the evening train departed from it.",
    "A careful reader would notice every small mistake hidden inside these simple practice sentences today.",
    "Every colour in the painting seemed brighter after the rain had washed the dust from the window.",
    "The colour of her dress matched the autumn leaves that covered the path behind the old church.",
]
PARAS_DE = [
    "Er sagte dem Mann, dass das Wetter schlecht werden würde und alle zu Hause bleiben sollten.",
    "Sie gingen nicht hinaus, weil der Regen den ganzen Tag gegen die Fenster schlug und niemand froh war.",
    "Er gab einem Kind das Brot, obwohl er selbst noch nichts gegessen hatte an diesem langen Tage.",
    "Die Mutter erzählte dem Vater, daß der Garten im Frühling sehr schön werden würde, wenn es regnet.",
    "Sie wußte nicht, daß der Wagen schon vor der Thür stand und der Kutscher lange auf sie wartete.",
    "Der Förster zeigte den Männern den Weg durch den dichten Wald hinunter zum kleinen Dorf am Fluss.",
    "Am Abend saß die ganze Familie am Tisch und sprach über die Ernte des vergangenen langen Sommers.",
    "Der Lehrer gab einem Schüler das Buch des Dichters und bat ihn, es sorgfältig und langsam zu lesen.",
    "Niemand konnte sagen, warum der Hund so laut bellte, als der Fremde die Straße entlang gegangen kam.",
    "Es war ein kalter Morgen und die Bauern gingen früh auf die Felder um die letzte Arbeit zu beenden.",
    "Der Pfarrer meinte, daß die Gemeinde im nächsten Jahr eine neue Glocke für den Turm bekommen sollte.",
    "Alle wußten längst, daß der Winter in diesem Jahr früh kommen und sehr lange dauern würde im Tal.",
]


def cand(block, start, end, original, replacement, category, source=Source.LANGUAGETOOL):
    return Candidate(
        block, start, end, original, replacement, category, Severity.MEDIUM, source, "x"
    )


def apply_truth(out, truth):
    restored = list(out)
    for t in truth:
        assert restored[t.paragraph][t.start : t.end] == t.wrong
        restored[t.paragraph] = (
            restored[t.paragraph][: t.start] + t.correct + restored[t.paragraph][t.end :]
        )
    return restored


def test_inject_reproducible():
    assert inject_errors(PARAS_EN, "en", seed=1, per_category=2) == inject_errors(
        PARAS_EN, "en", seed=1, per_category=2
    )


def test_each_category_injected():
    _, truth = inject_errors(PARAS_DE, "de", seed=1, per_category=2)
    assert {t.category for t in truth} == {
        Category.TYPO,
        Category.PUNCTUATION,
        Category.GRAMMAR,
        Category.CONSISTENCY,
    }


def test_each_category_injected_en():
    _, truth = inject_errors(PARAS_EN, "en", seed=1, per_category=2)
    assert {t.category for t in truth} == {
        Category.TYPO,
        Category.PUNCTUATION,
        Category.GRAMMAR,
        Category.CONSISTENCY,
    }


@pytest.mark.parametrize("lang,paras", [("en", PARAS_EN), ("de", PARAS_DE)])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_round_trip_nonempty_one_per_paragraph(lang, paras, seed):
    out, truth = inject_errors(paras, lang, seed=seed, per_category=2)
    assert truth
    assert all(t.end > t.start and t.wrong != t.correct for t in truth)
    assert len({t.paragraph for t in truth}) == len(truth)
    assert apply_truth(out, truth) == paras
    touched = {t.paragraph for t in truth}
    assert all(out[i] == paras[i] for i in range(len(paras)) if i not in touched)


def test_comma_span_is_word_before():
    out, truth = inject_errors(PARAS_EN, "en", seed=1, per_category=3)
    commas = [t for t in truth if t.category == Category.PUNCTUATION]
    assert commas and all(t.correct == t.wrong + "," for t in commas)


def test_inject_too_few_sites_does_not_crash():
    paras = ["Short text without much to change here at all."]
    out, truth = inject_errors(paras, "en", seed=1, per_category=5)
    assert len(truth) <= 1 and apply_truth(out, truth) == paras


def test_score_precision_recall():
    truth = [InjectedError(0, 0, 3, "teh", "the", Category.TYPO)]
    found = [
        cand(0, 0, 3, "teh", "the", Category.TYPO),
        cand(0, 4, 7, "cat", "dog", Category.STYLE),
    ]
    s = score(found, truth, block_ids=[0], paragraphs=["teh cat"])
    assert s["overall"] == {"precision": 0.5, "recall": 1.0}
    assert s["languagetool/typo"] == {"precision": 1.0, "recall": 1.0}
    assert s["languagetool/style"]["precision"] == 0.0


def test_score_tp_with_wider_span():
    truth = [InjectedError(0, 2, 5, "teh", "the", Category.TYPO)]
    # the candidate rewrites "a teh cat" as "a the cat": wider than the error, same result
    wide = cand(0, 0, 9, "a teh cat", "a the cat", Category.TYPO)
    # ... but this one overlaps and changes something else
    bad = cand(0, 2, 9, "teh cat", "the dog", Category.TYPO)
    paras = ["a teh cat"]
    s = score([wide], truth, [0], paragraphs=paras)
    assert s["overall"] == {"precision": 1.0, "recall": 1.0}
    s = score([bad], truth, [0], paragraphs=paras)
    assert s["overall"] == {"precision": 0.0, "recall": 0.0}


def test_score_maps_block_ids_and_zero_width():
    truth = [InjectedError(1, 0, 5, "house", "house,", Category.PUNCTUATION)]
    paras = ["x", "house which"]
    insert = cand(77, 5, 5, "", ",", Category.PUNCTUATION, Source.MODEL)
    miss = cand(77, 6, 6, "", ",", Category.PUNCTUATION, Source.MODEL)
    s = score([insert, miss], truth, [3, 77], paragraphs=paras)
    assert s["overall"] == {"precision": 0.5, "recall": 1.0}
    assert s["model/punctuation"] == {"precision": 0.5, "recall": 1.0}


def test_score_empty_denominators():
    assert score([], [], [0], paragraphs=["a"])["overall"] == {"precision": 0.0, "recall": 0.0}


GUTENBERG = """Title page junk
*** START OF THE PROJECT GUTENBERG EBOOK PRIDE ***

First paragraph with many words in it that goes on for a while,
over two lines, so that it has at least twenty words in total here.

Short one.

Second   paragraph also has enough words to be kept by the filter because it is long enough to pass the twenty word minimum.
*** END OF THE PROJECT GUTENBERG EBOOK PRIDE ***
License text that must go away.
"""


def test_gutenberg_strip_and_paragraphs():
    body = strip_gutenberg(GUTENBERG)
    assert "junk" not in body and "License" not in body and "***" not in body
    paras = paragraphs_from(body)
    assert len(paras) == 2
    assert "\n" not in paras[0] and "over two lines, so" in paras[0]
    assert "Second paragraph also" in paras[1]


def test_gutenberg_strip_this_marker():
    text = "x\n*** START OF THIS PROJECT GUTENBERG EBOOK X ***\nbody\n*** END OF THIS PROJECT GUTENBERG EBOOK X ***\nz"
    assert strip_gutenberg(text).strip() == "body"


@pytest.mark.parametrize(
    "text",
    ["no markers", "*** START OF THE PROJECT GUTENBERG EBOOK X ***\nbody only"],
)
def test_gutenberg_missing_marker(text):
    with pytest.raises(ValueError):
        strip_gutenberg(text)


def test_build_sample_remaps_truth():
    sample, truth = build_sample(PARAS_EN, "en", seed=1, per_category=2, clean=3)
    assert truth and len(sample) >= len(truth)
    for t in truth:
        assert sample[t.paragraph][t.start : t.end] == t.wrong


class FakeLT:
    def __init__(self, matches):
        self.matches = matches

    def check(self, annotation, language):
        return self.matches


def fake_registry(provider):
    return Registry(Settings(_env_file=None), {"anthropic": provider})


def lt_typo(offset, length, repl):
    return {
        "message": "m",
        "offset": offset,
        "length": length,
        "replacements": [{"value": repl}],
        "rule": {"issueType": "misspelling"},
    }


def test_run_eval_with_fakes():
    paras = ["teh cat sat on the mat and looked at the long road ahead of it " * 2]
    truth = [InjectedError(0, 0, 3, "teh", "the", Category.TYPO)]
    reply = {
        "suggestions": [
            {
                "block_id": 0,
                "original": "teh",
                "replacement": "the",
                "category": "typo",
                "severity": "high",
                "explanation": "typo",
                "occurrence": 1,
            }
        ]
    }
    provider = FakeProvider([reply], usage=Usage(1000, 150), token_count=1000)
    result = run_eval(
        paras,
        truth,
        language="en",
        variant="en-GB",
        passes=["languagetool", "ai", "consistency"],
        registry=fake_registry(provider),
        lt_client=FakeLT([lt_typo(0, 3, "the")]),
        max_cost=Decimal("1"),
    )
    assert len(provider.calls) == 1
    assert provider.calls[0]["model"] == "claude-opus-5-5"
    assert result["model"] == "claude-opus-5-5"
    assert result["stopped_at_cost_cap"] is False
    assert Decimal(result["cost"]) == Decimal("0.007")
    assert result["scores"]["passes"]["languagetool"]["overall"] == {
        "precision": 1.0,
        "recall": 1.0,
    }
    assert result["scores"]["passes"]["ai"]["overall"] == {"precision": 1.0, "recall": 1.0}
    assert result["scores"]["merged"]["overall"] == {"precision": 1.0, "recall": 1.0}


def long_paras():
    return [f"word{i} " * 1500 for i in range(3)]  # three chunks


def test_run_eval_refuses_when_estimate_over_cap():
    provider = FakeProvider([], usage=Usage(1000, 150), token_count=1000)
    with pytest.raises(ValueError, match="estimate"):
        run_eval(
            long_paras(),
            [],
            language="en",
            variant="en-GB",
            passes=["ai"],
            registry=fake_registry(provider),
            lt_client=FakeLT([]),
            max_cost=Decimal("0.01"),
        )
    assert provider.calls == []


def test_run_eval_stops_at_cost_cap():
    reply = {"suggestions": []}
    provider = FakeProvider([reply] * 3, usage=Usage(5000, 750), token_count=1000)
    result = run_eval(
        long_paras(),
        [],
        language="en",
        variant="en-GB",
        passes=["ai"],
        registry=fake_registry(provider),
        lt_client=FakeLT([]),
        max_cost=Decimal("0.03"),
    )
    assert len(provider.calls) == 1
    assert result["stopped_at_cost_cap"] is True


def _consistency_truth(paras, seed=1, per_category=10):
    out, truth = inject_errors(paras, "en", seed=seed, per_category=per_category)
    return out, [t for t in truth if t.category == Category.CONSISTENCY]


def _filler(n):
    return [f"Paragraph number {i} talks about nothing in particular at all." for i in range(n)]


def test_consistency_majority_survives_injection():
    # 5 "colour" and 2 "color": at most k=1 change keeps colour strictly ahead (4 > 3)
    paras = [f"The colour of item {i} was lovely to see." for i in range(5)]
    paras += [f"The color of item {i} was lovely to see." for i in range(2)]
    for seed in range(5):
        out, truth = _consistency_truth(paras, seed=seed)
        assert len(truth) == 1 and truth[0].correct == "colour"
        k = len(truth)
        assert 5 - k > 2 + k  # the original majority is still a strict majority
        assert all(out[t.paragraph][t.start : t.end] == "color" for t in truth)


def test_consistency_tie_yields_no_sites():
    paras = [f"The colour of item {i} was lovely." for i in range(2)]
    paras += [f"The color of item {i} was lovely." for i in range(2)]
    assert _consistency_truth(paras)[1] == []


def test_run_eval_counts_unbilled_provider_errors():
    provider = FakeProvider(
        [RuntimeError("network down")], usage=Usage(1000, 150), token_count=1000
    )
    result = run_eval(
        ["Some text here."],
        [],
        language="en",
        variant="en-GB",
        passes=["ai"],
        registry=fake_registry(provider),
        lt_client=FakeLT([]),
        max_cost=Decimal("1"),
    )
    assert result["ai_errors"] == 1 and "network down" in result["first_error"]
    assert "overall" in result["scores"]["merged"] and result["completed"] is True


def test_main_writes_results_when_run_eval_raises(tmp_path, monkeypatch):
    import json

    import eval.run as run_mod

    monkeypatch.setattr(run_mod, "RESULTS", tmp_path)
    monkeypatch.setattr(run_mod, "load_paragraphs", lambda _id: PARAS_EN)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    def boom(*a, **k):
        raise RuntimeError("lt exploded")

    monkeypatch.setattr(run_mod, "run_eval", boom)
    with pytest.raises(RuntimeError):
        run_mod.main(["--language", "en", "--passes", "consistency", "--max-cost", "1"])
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1 and list(tmp_path.glob("*.md"))
    data = json.loads(files[0].read_text())
    assert data["completed"] is False and "lt exploded" in data["error"]
