"""Asking the AI about LanguageTool marks: the request for one paragraph and its reply."""

import pytest

from jerome.models.base import ModelOutputError
from jerome.proofread.ai_check import VERDICT_SCHEMA, Mark, Verdict, build_request, parse_verdicts
from jerome.types import StyleSheet

STYLE_EN = StyleSheet("en-US", "“”‘’", {"colour": ["color"]}, ["Katharina"])
STYLE_DE = StyleSheet("de-CH", "«»‹›", {}, [])
TEXT = "Katharina said teh cat sat on the mat, and nobody minded at all that evening."
TEH = Mark(5, 15, 18, "teh", "the", "Possible spelling mistake found.")
NAME = Mark(6, 0, 9, "Katharina", "Catherine", "Possible spelling mistake found.")


def reply(*items):
    return {"verdicts": list(items)}


def item(id_, verdict, replacement="", reason="Fine."):
    return {"id": id_, "verdict": verdict, "replacement": replacement, "reason": reason}


def test_request_shows_each_mark_in_its_context():
    system, user = build_request(STYLE_EN, TEXT, [TEH, NAME], previous="Before.", source="Vorher.")
    assert "LanguageTool" in system[0].text and system[0].cache
    assert '"colour"' in system[1].text and "Katharina" in system[1].text
    assert f"<paragraph>\n{TEXT}\n</paragraph>" in user
    assert '<previous_paragraph read_only="true">\nBefore.\n</previous_paragraph>' in user
    assert '<source_paragraph read_only="true">\nVorher.\n</source_paragraph>' in user
    assert '<mark id="5">' in user and '<mark id="6">' in user
    assert "Katharina said ⟦teh⟧ cat sat on the mat, and nobody " in user
    assert "Possible spelling mistake found." in user
    assert "LanguageTool's fix: the" in user
    # a span near the start has no "…" before it; context is at most 40 characters a side
    assert "in context: ⟦Katharina⟧ said teh cat sat on the mat, and nobody…" in user


def test_request_without_previous_or_source():
    system, user = build_request(STYLE_DE, TEXT, [TEH], previous=None, source=None)
    assert "LanguageTool" in system[0].text and "Stilblatt" in system[1].text
    assert "previous_paragraph" not in user and "source_paragraph" not in user


def test_schema_asks_for_the_three_verdicts():
    entry = VERDICT_SCHEMA["properties"]["verdicts"]["items"]
    assert entry["properties"]["verdict"]["enum"] == ["correct", "not_an_error", "better"]
    assert set(entry["required"]) == {"id", "verdict", "replacement", "reason"}


def test_parses_the_three_verdicts():
    marks = [TEH, NAME, Mark(7, 23, 26, "sat", "sits", "Tense.")]
    got = parse_verdicts(
        reply(
            item(5, "better", "the", "Typo."),  # equal to LT's fix: see the next test
            item(6, "not_an_error", "ignored", "A name."),
            item(7, "better", "was sitting", "Keeps the past tense."),
        ),
        marks,
    )
    assert got == [
        Verdict(5, "correct", None, "Typo."),
        Verdict(6, "not_an_error", None, "A name."),
        Verdict(7, "better", "was sitting", "Keeps the past tense."),
    ]
    assert parse_verdicts(reply(item(5, "correct", "x", "Right.")), [TEH]) == [
        Verdict(5, "correct", None, "Right.")
    ]


def test_better_equal_to_lt_fix_is_correct():
    assert parse_verdicts(reply(item(5, "better", "the", "Typo.")), [TEH]) == [
        Verdict(5, "correct", None, "Typo.")
    ]


@pytest.mark.parametrize(
    "raw",
    [
        item(99, "correct"),
        item(5, "maybe"),
        item(5, "correct", reason="  "),
        item(5, "better", ""),
        item(5, "better", "teh"),
        item(5, "better", "th￼e"),
        item(5, "better", "th\x07e"),
        "not a dict",
        {"id": 5, "verdict": "correct"},
        {"id": "5", "verdict": "correct", "replacement": "", "reason": "x"},
    ],
)
def test_unusable_items(raw):
    assert parse_verdicts(reply(raw), [TEH]) == []


def test_a_repeated_id_keeps_the_first():
    got = parse_verdicts(reply(item(5, "not_an_error"), item(5, "correct")), [TEH])
    assert [v.verdict for v in got] == ["not_an_error"]


@pytest.mark.parametrize("data", [{}, {"verdicts": "no"}, {"other": []}])
def test_reply_without_verdicts_list_is_model_output_error(data):
    with pytest.raises(ModelOutputError):
        parse_verdicts(data, [TEH])
