import random

import pytest

from jerome.documents.model import PLACEHOLDER, Mark, MarkKind
from jerome.translate.tags import TagError, decode, encode, normalize_marks

B, IT, L = MarkKind.BOLD, MarkKind.ITALIC, MarkKind.LINK


def test_round_trip_plain_marks_link_and_placeholders():
    text = f"Hello bold world{PLACEHOLDER} see site."
    marks = [Mark(6, 10, B), Mark(6, 16, IT), Mark(21, 25, L, "https://x.test")]
    tagged = encode(text, marks)
    assert "⟦1⟧" in tagged and "<a1>" in tagged
    d = decode(tagged, marks, placeholder_count=1)
    assert d.text == text and d.marks == marks and d.placeholders == [0]
    assert d.marks_dropped == 0


def test_reordered_placeholders_are_recorded():
    d = decode("⟦2⟧ x ⟦1⟧", [], placeholder_count=2)
    assert d.text == f"{PLACEHOLDER} x {PLACEHOLDER}"
    assert d.placeholders == [1, 0]


@pytest.mark.parametrize(
    ("tagged", "count"),
    [("⟦1⟧⟦1⟧", 1), ("", 1), ("x ⟦2⟧", 1), ("⟦0⟧", 1), ("⟦1⟧", 0)],
)
def test_missing_duplicate_or_out_of_range_placeholder_raises(tagged, count):
    with pytest.raises(TagError):
        decode(tagged, [], placeholder_count=count)


@pytest.mark.parametrize("tagged", ["<b>x", "x</b>", "<b><i>x</b></i>", "<a1>x</a2>"])
def test_unbalanced_tags_raise(tagged):
    marks = [Mark(0, 1, B), Mark(0, 1, IT), Mark(0, 1, L, "h1"), Mark(1, 2, L, "h2")]
    with pytest.raises(TagError):
        decode(tagged, marks, placeholder_count=0)


@pytest.mark.parametrize(
    "tagged",
    ["<span>x</span>", "a<br/>b", '<b class="x">y</b>', "<a>z</a>", "<B>x</B>", "<b x"],
)
def test_unknown_or_malformed_tag_raises(tagged):
    with pytest.raises(TagError):
        decode(tagged, [Mark(0, 1, B)], placeholder_count=0)


@pytest.mark.parametrize("tagged", ["a ⟦ b", "a ⟧ b", "⟦x⟧", f"a {PLACEHOLDER} b"])
def test_stray_token_brackets_and_raw_placeholder_raise(tagged):
    with pytest.raises(TagError):
        decode(tagged, [], placeholder_count=0)


def test_unknown_link_index_keeps_text_drops_mark():
    d = decode("<a3>x</a3>", [Mark(0, 1, L, "https://x.test")], placeholder_count=0)
    assert d.text == "x" and d.marks == [] and d.marks_dropped == 1


def test_mark_kind_not_in_source_keeps_text_drops_mark():
    d = decode("<b>Hallo</b> <i>Welt</i>", [Mark(0, 5, IT)], placeholder_count=0)
    assert d.text == "Hallo Welt"
    assert d.marks == [Mark(6, 10, IT)] and d.marks_dropped == 1


def test_links_get_source_href_by_index_even_when_reordered():
    source = [Mark(0, 3, L, "https://one.test"), Mark(4, 7, L, "https://two.test")]
    d = decode("<a2>zwei</a2> <a1>eins</a1>", source, placeholder_count=0)
    assert d.marks == [Mark(0, 4, L, "https://two.test"), Mark(5, 9, L, "https://one.test")]


def test_decode_merges_adjacent_equal_marks():
    d = decode("<b>ab</b><b>cd</b> <i>e</i>", [Mark(0, 1, B), Mark(0, 1, IT)], placeholder_count=0)
    assert d.marks == [Mark(0, 4, B), Mark(5, 6, IT)]


def test_literal_angle_brackets_survive():
    text = "a < b & c > d ⟦1⟧ &lt; &amp;"
    tagged = encode(text, [])
    assert "<" not in tagged and ">" not in tagged and "⟦" not in tagged
    assert tagged.startswith("a &lt; b &amp; c &gt; d &#x27E6;1&#x27E7; &amp;lt;")
    d = decode(tagged, [], placeholder_count=0)
    assert d.text == text and d.placeholders == []


def test_decode_tolerates_unescaped_gt_lone_lt_and_lowercase_hex_entities():
    d = decode("a > b < c &#x27e6;1&#x27e7; & d", [], placeholder_count=0)
    assert d.text == "a > b < c ⟦1⟧ & d"


def test_nested_formatting_has_no_reopen_churn():
    text = "one two three four"
    tagged = encode(text, [Mark(0, 18, IT), Mark(4, 7, B)])
    assert tagged == "<i>one <b>two</b> three four</i>"
    # nested marks starting together: the longer one goes outside
    assert encode(text, [Mark(4, 7, B), Mark(4, 13, IT)]) == "one <i><b>two</b> three</i> four"
    # same span: the fixed order decides (a, b, i, u, sc, s, sup, sub)
    marks = [
        Mark(0, 3, MarkKind.SUBSCRIPT),
        Mark(0, 3, MarkKind.SUPERSCRIPT),
        Mark(0, 3, MarkKind.STRIKE),
        Mark(0, 3, MarkKind.SMALLCAPS),
        Mark(0, 3, MarkKind.UNDERLINE),
        Mark(0, 3, IT),
        Mark(0, 3, B),
        Mark(0, 3, L, "h"),
    ]
    assert encode(text, marks) == (
        "<a1><b><i><u><sc><s><sup><sub>one</sub></sup></s></sc></u></i></b></a1> two three four"
    )


def test_crossing_marks_round_trip():
    text = "abcdefghij"
    marks = [Mark(0, 6, B), Mark(3, 9, IT)]
    tagged = encode(text, marks)
    assert tagged == "<b>abc<i>def</i></b><i>ghi</i>j"
    assert decode(tagged, marks, 0).marks == marks


def test_normalize_marks_merges_drops_empty_and_sorts():
    marks = [
        Mark(5, 8, IT),
        Mark(0, 3, B),
        Mark(3, 5, B),
        Mark(2, 2, IT),
        Mark(4, 9, B),
        Mark(0, 2, L, "h2"),
        Mark(0, 2, L, "h1"),
        Mark(2, 4, L, "h1"),
    ]
    assert normalize_marks(marks) == [
        Mark(0, 2, L, "h2"),
        Mark(0, 4, L, "h1"),
        Mark(0, 9, B),
        Mark(5, 8, IT),
    ]


def test_round_trip_normalizes_unnormalized_input():
    text = "abcdef"
    marks = [Mark(3, 6, B), Mark(0, 3, B), Mark(1, 1, IT)]
    d = decode(encode(text, marks), marks, 0)
    assert d.marks == normalize_marks(marks) == [Mark(0, 6, B)]


def test_round_trip_is_lossless_for_random_mark_sets():
    rng = random.Random(932)
    alphabet = "ab <>&⟦⟧;#x" + PLACEHOLDER
    kinds = list(MarkKind)
    for _ in range(500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        marks = []
        for _ in range(rng.randint(0, 8)):
            s = rng.randint(0, len(text))
            e = rng.randint(s, len(text))
            kind = rng.choice(kinds)
            marks.append(Mark(s, e, kind, rng.choice(["h1", "h2"]) if kind == L else None))
        count = text.count(PLACEHOLDER)
        d = decode(encode(text, marks), marks, count)
        assert (d.text, d.marks) == (text, normalize_marks(marks))
        assert d.placeholders == list(range(count)) and d.marks_dropped == 0
