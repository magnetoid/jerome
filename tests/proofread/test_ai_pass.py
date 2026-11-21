import pytest

from jerome.documents.model import PLACEHOLDER
from jerome.models.base import ModelOutputError, ModelRefusal
from jerome.models.fake import FakeProvider
from jerome.proofread.ai_pass import (
    SUGGESTIONS_SCHEMA,
    Chunk,
    build_request,
    chunk_blocks,
    parse_suggestions,
    run_ai_pass,
)
from jerome.types import BlockText, Category, Severity, Source, StyleSheet

STYLE_EN = StyleSheet("en-US", "“”‘’", {}, [])
STYLE_DE = StyleSheet("de-CH", "«»‹›", {"Foto": ["Photo"]}, ["Katharina"])


def item(**kw):
    base = {
        "block_id": 1,
        "original": "teh",
        "replacement": "the",
        "category": "typo",
        "severity": "high",
        "explanation": "Typo.",
        "occurrence": 1,
    }
    base.update(kw)
    return base


def reply(**kw):
    return {"suggestions": [item(**kw)]}


def one(text, **kw):
    return parse_suggestions(reply(**kw), Chunk([BlockText(1, text)], None))


def test_chunks_respect_target_and_carry_previous_block():
    blocks = [BlockText(i, " ".join(["w"] * 1000)) for i in range(6)]
    chunks = chunk_blocks(blocks)
    assert [len(c.blocks) for c in chunks] == [2, 2, 2]
    assert chunks[1].context == blocks[1] and chunks[0].context is None


def test_single_oversized_block_forms_its_own_chunk():
    blocks = [
        BlockText(0, "a b"),
        BlockText(1, " ".join(["w"] * 3000)),
        BlockText(2, "c d"),
    ]
    chunks = chunk_blocks(blocks)
    assert [[b.id for b in c.blocks] for c in chunks] == [[0], [1], [2]]
    assert chunks[1].context == blocks[0]
    assert chunk_blocks([]) == []


def test_system_prefix_identical_across_chunks():
    a, _ = build_request(STYLE_EN, Chunk([BlockText(1, "x")], None))
    b, _ = build_request(STYLE_EN, Chunk([BlockText(2, "y")], BlockText(1, "x")))
    assert a == b
    assert len(a) == 2 and all(p.cache for p in a)


def test_german_request_names_variant_and_quotes_in_german():
    system, user = build_request(STYLE_DE, Chunk([BlockText(2, "Hallo")], BlockText(1, "Vor")))
    text = system[0].text
    assert "de-CH" in text and "«»‹›" in text
    assert "Fehler" in text  # German prompt
    assert "Katharina" in system[1].text and "Foto" in system[1].text
    assert system == build_request(STYLE_DE, Chunk([BlockText(9, "z")], None))[0]


def test_user_message_marks_context_read_only_with_ids():
    _, user = build_request(STYLE_EN, Chunk([BlockText(2, "Body")], BlockText(1, "Ctx")))
    assert 'id="1"' in user and 'id="2"' in user
    assert "read_only" in user and "Ctx" in user and "Body" in user
    assert user.index("Ctx") < user.index("Body")
    assert "U+FFFC" in user and "never appear in `original` or `replacement`" in user
    _, no_ctx = build_request(STYLE_EN, Chunk([BlockText(2, "Body")], None))
    assert "read_only" not in no_ctx


def test_prompts_cover_required_points():
    system, _ = build_request(STYLE_EN, Chunk([BlockText(1, "x")], None))
    t = system[0].text
    for needle in (
        "en-US",
        "“”‘’",
        "read-only",
        "exactly",
        "typo",
        "grammar",
        "punctuation",
        "consistency",
        "style",
        "high",
        "medium",
        "low",
        "U+FFFC",
    ):
        assert needle in t


def test_schema_strict_and_no_numeric_constraints():
    def walk(node):
        if isinstance(node, dict):
            assert "minimum" not in node and "maximum" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert sorted(node["required"]) == sorted(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(SUGGESTIONS_SCHEMA)
    props = SUGGESTIONS_SCHEMA["properties"]["suggestions"]["items"]["properties"]
    assert set(props["category"]["enum"]) == {c.value for c in Category}
    assert set(props["severity"]["enum"]) == {s.value for s in Severity}
    assert "1-based" in props["occurrence"]["description"]


def test_context_block_not_editable():
    c, dropped = parse_suggestions(
        reply(block_id=1, original="x", replacement="X"),
        Chunk([BlockText(2, "y")], BlockText(1, "x")),
    )
    assert c == [] and dropped == 1


def test_drops_missing_original_placeholder_and_noop():
    text = f"abc {PLACEHOLDER} def"
    data = {
        "suggestions": [
            item(original="zzz", replacement="y"),  # not in block
            item(original=f"c {PLACEHOLDER}", replacement="c"),  # covers placeholder
            item(original="abc", replacement="abc"),  # no-op
        ]
    }
    c, dropped = parse_suggestions(data, Chunk([BlockText(1, text)], None))
    assert c == [] and dropped == 3


@pytest.mark.parametrize("data", [[], {}, {"suggestions": "x"}, {"suggestions": {}}, "s"])
def test_bad_shape_raises(data):
    with pytest.raises(ModelOutputError):
        parse_suggestions(data, Chunk([BlockText(1, "x")], None))


def test_malformed_items_dropped_and_counted():
    good = item()
    bad_missing = {k: v for k, v in good.items() if k != "explanation"}
    data = {
        "suggestions": [
            good,
            bad_missing,
            item(category="spelling"),
            item(severity="urgent"),
            item(block_id="1"),
            item(block_id=True),
            item(occurrence=1.5),
            item(original=5),
            "nonsense",
        ]
    }
    c, dropped = parse_suggestions(data, Chunk([BlockText(1, "teh cat")], None))
    assert len(c) == 1 and dropped == 8


def test_occurrence_zero_dropped():
    c, dropped = one("teh", occurrence=0)
    assert c == [] and dropped == 1


def test_occurrence_beyond_count_dropped():
    c, dropped = one("teh cat", occurrence=2)
    assert c == [] and dropped == 1


def test_occurrence_selects_nth_match():
    c, _ = one("the cat and the dog", original="the", replacement="The", occurrence=2)
    assert (c[0].start, c[0].end) == (12, 15)


def test_candidate_fields():
    c, dropped = one("a teh b", explanation="Why")
    assert dropped == 0
    cand = c[0]
    assert (cand.block_id, cand.start, cand.end, cand.original, cand.replacement) == (
        1,
        2,
        5,
        "teh",
        "the",
    )
    assert cand.category == Category.TYPO and cand.severity == Severity.HIGH
    assert cand.source == Source.MODEL and cand.explanation == "Why"


def test_tolerant_match_typographic_quotes():
    text = "He said “hello”… and left."
    c, _ = one(text, original='said "hello"...', replacement="said “Hello”…")
    assert text[c[0].start : c[0].end] == "said “hello”…" and c[0].original == "said “hello”…"


def test_tolerant_match_nbsp_and_whitespace_runs():
    text = "one two  \n three end"
    c, _ = one(text, original="two three", replacement="2 3")
    assert text[c[0].start : c[0].end] == "two  \n three"
    assert c[0].original == "two  \n three"
    # trailing whitespace run is mapped back in full
    c, _ = one(text, original="one ", replacement="1")
    assert text[c[0].start : c[0].end] == "one "


def test_tolerant_nth_occurrence():
    text = "“a” x “a”"
    c, _ = one(text, original='"a"', replacement="b", occurrence=2)
    assert (c[0].start, c[0].end) == (6, 9)


def test_run_ai_pass_uses_provider():
    fake = FakeProvider([reply(block_id=1, original="teh", replacement="the")])
    r = run_ai_pass(Chunk([BlockText(1, "teh")], None), STYLE_EN, fake, "m", "high")
    assert r.candidates[0].source == Source.MODEL and fake.calls[0]["schema"] == SUGGESTIONS_SCHEMA
    assert r.dropped == 0 and r.result.provider == "fake"
    call = fake.calls[0]
    assert call["model"] == "m" and call["effort"] == "high"
    assert call["system"] == build_request(STYLE_EN, Chunk([BlockText(1, "teh")], None))[0]


def test_run_ai_pass_propagates_refusal_and_output_errors():
    chunk = Chunk([BlockText(1, "teh")], None)
    with pytest.raises(ModelRefusal):
        run_ai_pass(chunk, STYLE_EN, FakeProvider([ModelRefusal("no")]), "m", None)
    with pytest.raises(ModelOutputError):
        run_ai_pass(chunk, STYLE_EN, FakeProvider([{"nope": 1}]), "m", None)
