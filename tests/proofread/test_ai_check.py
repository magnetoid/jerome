"""Asking the AI about LanguageTool marks: the request for one paragraph and its reply."""

import pytest

from jerome.models.base import ModelOutputError
from jerome.proofread.ai_check import (
    VERDICT_SCHEMA,
    Mark,
    Verdict,
    build_request,
    count_sentences,
    parse_verdicts,
    pick_context,
    room_around,
)
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
    system, user = build_request(
        STYLE_EN, TEXT, [TEH, NAME], before=["First.", "Before."], after=["After."],
        source="Vorher.",
    )  # fmt: skip
    assert "LanguageTool" in system[0].text and system[0].cache
    assert '"colour"' in system[1].text and "Katharina" in system[1].text
    assert f"<paragraph>\n{TEXT}\n</paragraph>" in user
    assert (
        '<previous_paragraphs read_only="true">\nFirst.\n\nBefore.\n</previous_paragraphs>' in user
    )
    assert '<following_paragraphs read_only="true">\nAfter.\n</following_paragraphs>' in user
    assert (
        user.index("<previous_paragraphs")
        < user.index("<paragraph>")
        < user.index("<following_paragraphs")
    )
    assert '<source_paragraph read_only="true">\nVorher.\n</source_paragraph>' in user
    assert '<mark id="5">' in user and '<mark id="6">' in user
    assert "Katharina said ⟦teh⟧ cat sat on the mat, and nobody " in user
    assert "Possible spelling mistake found." in user
    assert "LanguageTool's options:\n1. the\n" in user
    # a span near the start has no "…" before it; context is at most 40 characters a side
    assert "in context: ⟦Katharina⟧ said teh cat sat on the mat, and nobody…" in user


def test_request_without_previous_or_source():
    system, user = build_request(STYLE_DE, TEXT, [TEH], before=[], after=[], source=None)
    assert "LanguageTool" in system[0].text and "Stilblatt" in system[1].text
    assert "previous_paragraphs" not in user and "following_paragraphs" not in user
    assert "source_paragraph" not in user


@pytest.mark.parametrize("language", ["en", "de"])
def test_prompts_explain_the_surrounding_paragraphs(language):
    style = STYLE_EN if language == "en" else STYLE_DE
    system, _ = build_request(style, TEXT, [TEH], before=[], after=[], source=None)
    assert "<previous_paragraphs" in system[0].text and "<following_paragraphs" in system[0].text


@pytest.mark.parametrize(
    ("text", "n"),
    [
        ("", 0),
        ("   ", 0),
        ("No end", 1),
        ("One. Two! Three? Four", 4),
        ("“Go,” she said. “Now.” Then she left…", 3),
        ("Wait... what?! Fine.", 2),  # a lowercase word goes on with the sentence
        ("„Komm!“ rief er. «Jetzt.»", 2),
        ("It cost 3.50 today, e.g. bread. Then", 2),
    ],
)
def test_count_sentences(text, n):
    assert count_sentences(text) == n


def test_room_around_counts_the_whole_sentences_beside_the_marks():
    text = "A one. B two. C teh three, D dogg. E five. F six"
    teh = Mark(1, 16, 19, "teh", "the", "")
    dogg = Mark(2, 29, 33, "dogg", "dog", "")
    assert room_around(text, [teh]) == (2, 2)
    assert room_around(text, [dogg, teh]) == (2, 2)  # same sentence, either order
    assert room_around(text, [Mark(3, 0, 1, "A", "a", "")]) == (0, 4)
    assert room_around(text, [Mark(4, 45, 48, "six", "6", "")]) == (4, 0)
    assert room_around(TEXT, [TEH]) == (0, 0)


def test_pick_context_takes_neighbours_until_three_sentences():
    near_first = ["One.", "Two. Three.", "Four.", "Five."]
    assert pick_context(near_first, have=0) == ["One.", "Two. Three."]
    assert pick_context(near_first, have=2) == ["One."]
    assert pick_context(near_first, have=5) == ["One."]  # the nearest always comes
    assert pick_context([], have=0) == []


def test_pick_context_stays_within_the_character_limit():
    long = "x" * 2000 + "."
    assert pick_context([long, "Two."], have=0) == [long]  # the nearest always comes
    assert pick_context(["One.", "y" * 1600 + ".", "Three."], have=0) == ["One."]


def test_schema_asks_for_the_three_ratings():
    entry = VERDICT_SCHEMA["properties"]["verdicts"]["items"]
    assert entry["properties"]["verdict"]["enum"] == ["error", "maybe_error", "not_an_error"]
    assert set(entry["required"]) == {"id", "verdict", "replacement", "reason"}


def test_request_lists_languagetool_options():
    teh = Mark(5, 15, 18, "teh", "the", "Spelling.", options=("the", "tea", "ten"))
    _, user = build_request(STYLE_EN, TEXT, [teh], before=[], after=[], source=None)
    assert "LanguageTool's options:\n1. the\n2. tea\n3. ten\n" in user
    _, old = build_request(STYLE_EN, TEXT, [TEH], before=[], after=[], source=None)
    assert "LanguageTool's options:\n1. the\n" in old  # a mark from before options


def test_parses_the_three_ratings():
    marks = [TEH, NAME, Mark(7, 23, 26, "sat", "sits", "Tense.")]
    got = parse_verdicts(
        reply(
            item(5, "error", "the", "Typo."),
            item(6, "not_an_error", "ignored", "A name."),
            item(7, "maybe_error", "was sitting", "Keeps the past tense."),
        ),
        marks,
    )
    assert got == [
        Verdict(5, "error", "the", "Typo."),
        Verdict(6, "not_an_error", None, "A name."),
        Verdict(7, "maybe_error", "was sitting", "Keeps the past tense."),
    ]


@pytest.mark.parametrize("rating", ["error", "maybe_error"])
def test_an_empty_fix_takes_languagetools_first_option(rating):
    teh = Mark(5, 15, 18, "teh", "the", "Spelling.", options=("the", "tea"))
    assert parse_verdicts(reply(item(5, rating, "", "Typo.")), [teh]) == [
        Verdict(5, rating, "the", "Typo.")
    ]


def test_a_fix_can_be_another_option_or_the_ais_own():
    teh = Mark(5, 15, 18, "teh", "the", "Spelling.", options=("the", "tea"))
    assert parse_verdicts(reply(item(5, "error", "tea", "Tea.")), [teh])[0].replacement == "tea"
    assert parse_verdicts(reply(item(5, "error", "The", "Own.")), [teh])[0].replacement == "The"


@pytest.mark.parametrize(
    "raw",
    [
        item(99, "error"),
        item(5, "correct"),
        item(5, "better", "the"),
        item(5, "error", reason="  "),
        item(5, "error", "teh"),
        item(5, "maybe_error", "teh"),
        item(5, "error", "th￼e"),
        item(5, "error", "th\x07e"),
        "not a dict",
        {"id": 5, "verdict": "error"},
        {"id": "5", "verdict": "error", "replacement": "", "reason": "x"},
    ],
)
def test_unusable_items(raw):
    assert parse_verdicts(reply(raw), [TEH]) == []


def test_a_repeated_id_keeps_the_first():
    got = parse_verdicts(reply(item(5, "not_an_error"), item(5, "error")), [TEH])
    assert [v.verdict for v in got] == ["not_an_error"]


@pytest.mark.parametrize("data", [{}, {"verdicts": "no"}, {"other": []}])
def test_reply_without_verdicts_list_is_model_output_error(data):
    with pytest.raises(ModelOutputError):
        parse_verdicts(data, [TEH])
