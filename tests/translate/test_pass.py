import pytest

from jerome.documents.model import PLACEHOLDER, Mark, MarkKind
from jerome.errors import UnsupportedLanguagePair
from jerome.models.base import ModelOutputError, ModelRefusal, Usage
from jerome.models.fake import FakeProvider
from jerome.translate.pass_ import (
    TRANSLATION_SCHEMA,
    TranslateChunk,
    TranslateItem,
    build_translate_request,
    chunk_items,
    max_tokens_for,
    run_translate_pass,
)
from jerome.types import StyleSheet

from .fakes import echo_translator, scripted

EN = StyleSheet("en-GB", "‘’“”", {}, [])
DE = StyleSheet("de-DE", "„“‚‘", {}, [])


def item(block_id, text, marks=None):
    return TranslateItem(block_id, f"k{block_id}", text, marks or [])


def chunk_of(*ids):
    return TranslateChunk([item(i, "Hello world", [Mark(0, 5, MarkKind.BOLD)]) for i in ids])


def test_long_block_gets_own_chunk_and_scaled_max_tokens():
    items = [item(1, "w " * 100), item(2, "word " * 3000), item(3, "w " * 100)]
    chunks = chunk_items(items, target_words=1200)
    assert [[i.block_id for i in c.items] for c in chunks] == [[1], [2], [3]]
    assert max_tokens_for(chunks[1]) > max_tokens_for(chunks[0])


def test_chunks_fill_up_to_target_and_never_split_an_item():
    items = [item(i, "w " * 500) for i in range(1, 6)]
    chunks = chunk_items(items, target_words=1200)
    assert [[i.block_id for i in c.items] for c in chunks] == [[1, 2], [3, 4], [5]]
    assert chunk_items([]) == []


def test_max_tokens_formula_bounds():
    assert max_tokens_for(TranslateChunk([item(1, "x")])) == 4000
    # 14000 chars -> ceil(14000 / 3.5 * 1.6) + 1000 = 7400
    assert max_tokens_for(TranslateChunk([item(1, "a" * 14000)])) == 7400
    assert max_tokens_for(TranslateChunk([item(1, "a" * 100_000)])) == 32000


def test_pass_decodes_reply_and_reports_failures():
    provider = FakeProvider([scripted({1: "<b>Hallo</b> Welt", 2: None, 3: "kaputt <i>"})])
    r = run_translate_pass(chunk_of(1, 2, 3), EN, DE, [], provider, "m", None)
    assert r.done[1].text == "Hallo Welt" and r.failed == [2, 3]
    assert r.done[1].marks == [Mark(0, 5, MarkKind.BOLD)]
    assert set(r.done) == {1}


def test_pass_sends_schema_max_tokens_and_effort():
    provider = FakeProvider([echo_translator()])
    chunk = chunk_of(1, 2)
    r = run_translate_pass(chunk, EN, DE, [], provider, "claude-x", "high")
    call = provider.calls[0]
    assert call["schema"] == TRANSLATION_SCHEMA
    assert call["max_tokens"] == max_tokens_for(chunk)
    assert call["model"] == "claude-x" and call["effort"] == "high"
    assert r.done[1].text == "DE Hello world" and r.done[2].marks == [Mark(3, 8, MarkKind.BOLD)]
    assert r.failed == [] and r.result.usage == provider.usage


def test_duplicate_ids_fail_and_unknown_ids_are_ignored():
    def reply(kwargs):
        return {
            "blocks": [
                {"block_id": 1, "text": "eins"},
                {"block_id": 1, "text": "eins wieder"},
                {"block_id": 2, "text": "zwei"},
                {"block_id": 99, "text": "erfunden"},
            ]
        }

    r = run_translate_pass(chunk_of(1, 2), EN, DE, [], FakeProvider([reply]), "m", None)
    assert r.failed == [1] and set(r.done) == {2}


def test_placeholders_are_checked_per_block_and_marks_dropped_summed():
    chunk = TranslateChunk(
        [
            item(1, f"See{PLACEHOLDER} here", [Mark(0, 3, MarkKind.ITALIC)]),
            item(2, f"A{PLACEHOLDER}"),
            item(3, "plain"),
        ]
    )
    provider = FakeProvider([scripted({1: "<i>Siehe</i>⟦1⟧ hier", 2: "B", 3: "<b>schlicht</b>"})])
    r = run_translate_pass(chunk, EN, DE, [], provider, "m", None)
    assert r.done[1].text == f"Siehe{PLACEHOLDER} hier" and r.done[1].placeholders == [0]
    assert r.failed == [2]
    assert r.done[3].text == "schlicht" and r.marks_dropped == 1


def test_malformed_reply_raises_billed_output_error():
    provider = FakeProvider([{"nope": []}], usage=Usage(10, 5))
    with pytest.raises(ModelOutputError) as exc:
        run_translate_pass(chunk_of(1), EN, DE, [], provider, "m", None)
    assert exc.value.usage == Usage(10, 5)


def test_malformed_entries_count_as_failures_for_their_block():
    reply = {"blocks": [{"block_id": 1, "text": 5}, "junk", {"block_id": "2", "text": "x"}]}
    r = run_translate_pass(chunk_of(1, 2), EN, DE, [], FakeProvider([reply]), "m", None)
    assert r.failed == [1, 2] and r.done == {}


def test_refusal_propagates():
    provider = FakeProvider([ModelRefusal("no", usage=Usage(1, 1))])
    with pytest.raises(ModelRefusal):
        run_translate_pass(chunk_of(1), EN, DE, [], provider, "m", None)


def test_request_carries_context_ignore_list_and_direction():
    target = StyleSheet("de-DE", "„“‚‘", {}, ["Usher"])
    chunk = TranslateChunk([item(7, "The <house> & ⟦x⟧", [Mark(4, 7, MarkKind.ITALIC)])])
    system, user = build_translate_request(EN, target, chunk, [("Old <b>.", "Alt.")])
    assert "en-GB" in system[0].text and "de-DE" in system[0].text and "Usher" in system[-1].text
    assert "„“‚‘" in system[0].text and "{" not in system[0].text
    assert all(part.cache for part in system)
    assert "<context>" in user and '<block id="' in user
    assert "Old &lt;b&gt;." in user and "Alt." in user
    assert '<block id="7">The <i>&lt;ho</i>use&gt; &amp; &#x27E6;x&#x27E7;</block>' in user
    assert user.index("<context>") < user.index('<block id="7">')


def test_de_to_en_uses_its_own_prompt():
    chunk = TranslateChunk([item(1, "Hallo")])
    en_de, _ = build_translate_request(EN, DE, chunk, [])
    us = StyleSheet("en-US", "“”‘’", {}, ["Katharina"])
    de_en, user = build_translate_request(DE, us, chunk, [])
    assert de_en[0].text != en_de[0].text
    assert "de-DE" in de_en[0].text and "en-US" in de_en[0].text and "“”‘’" in de_en[0].text
    assert "Katharina" in de_en[-1].text and "<source>" not in user


def test_same_language_pair_is_rejected():
    with pytest.raises(UnsupportedLanguagePair):
        build_translate_request(EN, StyleSheet("en-US", "“”‘’", {}, []), chunk_of(1), [])
