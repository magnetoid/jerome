import json
import os
from urllib.parse import parse_qs

import httpx
import pytest

from jerome.config import get_settings
from jerome.documents.model import PLACEHOLDER
from jerome.proofread.languagetool import LanguageToolClient, languagetool_candidates
from jerome.types import BlockText, Category, Severity, Source, StyleSheet

LT_URL = os.environ.get("JEROME_LANGUAGETOOL_URL") or get_settings().languagetool_url
BASE = "http://lt.test"

STYLE_EN = StyleSheet("en-US", "“”‘’", {}, [])
STYLE_DE = StyleSheet("de-DE", "„“‚‘", {}, ["Katharina"])


def lt_match(offset, length, repl="x", issue="misspelling", message="msg"):
    return {
        "message": message,
        "offset": offset,
        "length": length,
        "replacements": [{"value": repl}] if repl is not None else [],
        "rule": {"issueType": issue},
    }


def lt_route(respx_mock, matches):
    return respx_mock.post(f"{BASE}/v2/check").respond(json={"matches": matches})


def parse_form(route) -> dict[str, str]:
    body = route.calls.last.request.content.decode()
    return {k: v[0] for k, v in parse_qs(body).items()}


def client(respx_mock) -> LanguageToolClient:
    return LanguageToolClient(BASE)


def run(respx_mock, text, matches, style=STYLE_EN):
    lt_route(respx_mock, matches)
    return languagetool_candidates([BlockText(1, text)], style, client(respx_mock))


def test_placeholders_sent_as_markup(respx_mock):
    route = lt_route(respx_mock, matches=[])
    languagetool_candidates([BlockText(1, f"a{PLACEHOLDER}b")], STYLE_EN, client(respx_mock))
    ann = json.loads(parse_form(route)["data"])["annotation"]
    assert ann == [{"text": "a"}, {"markup": PLACEHOLDER, "interpretAs": " "}, {"text": "b"}]


def test_language_is_variant_and_empty_parts_omitted(respx_mock):
    route = lt_route(respx_mock, matches=[])
    style = StyleSheet("de-CH", "«»‹›", {}, [])
    languagetool_candidates(
        [BlockText(1, f"{PLACEHOLDER}x{PLACEHOLDER}")], style, client(respx_mock)
    )
    form = parse_form(route)
    assert form["language"] == "de-CH"
    ann = json.loads(form["data"])["annotation"]
    assert [list(a) for a in ann] == [
        ["markup", "interpretAs"],
        ["text"],
        ["markup", "interpretAs"],
    ]


def test_issue_types_map(respx_mock):
    # misspelling -> (typo, high); typographical -> (punctuation, medium)
    text = "abcdefghij"
    kinds = ["misspelling", "grammar", "typographical", "locale-violation", "style", "register"]
    matches = [lt_match(i, 1, repl="Z", issue=k) for i, k in enumerate(kinds)]
    cands, dropped = run(respx_mock, text, matches)
    assert dropped == 0
    assert [(c.category, c.severity) for c in cands] == [
        (Category.TYPO, Severity.HIGH),
        (Category.GRAMMAR, Severity.HIGH),
        (Category.PUNCTUATION, Severity.MEDIUM),
        (Category.CONSISTENCY, Severity.MEDIUM),
        (Category.STYLE, Severity.LOW),
        (Category.STYLE, Severity.LOW),
    ]


def test_unknown_issue_type_falls_back_to_grammar_medium(respx_mock):
    cands, _ = run(respx_mock, "abc", [lt_match(0, 1, repl="Z", issue="whitespace")])
    assert (cands[0].category, cands[0].severity) == (Category.GRAMMAR, Severity.MEDIUM)


def test_candidate_fields(respx_mock):
    cands, dropped = run(
        respx_mock, "a teh b", [lt_match(2, 3, repl="the", message="Possible typo")]
    )
    assert dropped == 0
    c = cands[0]
    assert (c.block_id, c.start, c.end, c.original, c.replacement) == (1, 2, 5, "teh", "the")
    assert c.source is Source.LANGUAGETOOL
    assert c.explanation == "Possible typo"


def test_first_replacement_used(respx_mock):
    m = lt_match(0, 3, repl="the")
    m["replacements"].append({"value": "then"})
    cands, _ = run(respx_mock, "teh", [m])
    assert cands[0].replacement == "the"


def test_ignore_words_filter_spelling(respx_mock):
    # "Katharina" flagged misspelling, in ignore_words -> dropped
    text = "Katharina teh"
    cands, dropped = run(
        respx_mock,
        text,
        [lt_match(0, 9, repl="Katarina"), lt_match(10, 3, repl="the")],
        STYLE_DE,
    )
    assert [c.original for c in cands] == ["teh"]
    assert dropped == 1


def test_ignore_words_only_applies_to_misspelling(respx_mock):
    cands, dropped = run(
        respx_mock, "Katharina", [lt_match(0, 9, repl="Katarina", issue="style")], STYLE_DE
    )
    assert len(cands) == 1 and dropped == 0


def test_match_without_replacement_dropped(respx_mock):
    cands, dropped = run(respx_mock, "abc", [lt_match(0, 3, repl=None)])
    assert (cands, dropped) == ([], 1)


def test_match_spanning_placeholder_dropped(respx_mock):
    text = f"ab{PLACEHOLDER}cd"
    cands, dropped = run(respx_mock, text, [lt_match(1, 3, repl="X"), lt_match(3, 2, repl="Y")])
    assert [c.original for c in cands] == ["cd"]
    assert dropped == 1


def test_replacement_equal_to_original_dropped(respx_mock):
    cands, dropped = run(respx_mock, "abc", [lt_match(0, 3, repl="abc")])
    assert (cands, dropped) == ([], 1)


def test_utf16_offsets_converted(respx_mock):
    text = "😀 teh"  # LanguageTool reports offset 3 (UTF-16), Python index 2
    cands, _ = run(respx_mock, text, [lt_match(3, 3, repl="the", issue="misspelling")])
    assert (cands[0].start, cands[0].end, cands[0].original) == (2, 5, "teh")


def test_blank_blocks_skipped_without_request(respx_mock):
    cands, dropped = languagetool_candidates(
        [BlockText(1, ""), BlockText(2, "  \n\t")], STYLE_EN, client(respx_mock)
    )
    assert (cands, dropped) == ([], 0)
    assert respx_mock.calls.call_count == 0


def test_one_request_per_block_and_block_ids(respx_mock):
    route = lt_route(respx_mock, [lt_match(0, 3, repl="the")])
    cands, _ = languagetool_candidates(
        [BlockText(5, "teh"), BlockText(6, "teh")], STYLE_EN, client(respx_mock)
    )
    assert route.call_count == 2
    assert [c.block_id for c in cands] == [5, 6]


def test_http_error_propagates(respx_mock):
    respx_mock.post(f"{BASE}/v2/check").respond(status_code=500)
    with pytest.raises(httpx.HTTPStatusError):
        languagetool_candidates([BlockText(1, "abc")], STYLE_EN, client(respx_mock))


def test_default_client_has_60s_timeout():
    assert LanguageToolClient(BASE)._http.timeout.read == 60


def _lt_up() -> bool:
    try:
        return httpx.get(f"{LT_URL}/v2/languages", timeout=3).is_success
    except httpx.HTTPError:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _lt_up(), reason="LanguageTool server is not reachable")
def test_real_languagetool_finds_german_typo():  # needs docker compose languagetool
    c, _ = languagetool_candidates(
        [BlockText(1, "Das ist ein Fehlr.")], STYLE_DE, LanguageToolClient(LT_URL)
    )
    assert any(x.original == "Fehlr" for x in c)


def test_premium_credentials_only_when_both_are_set(respx_mock):
    route = lt_route(respx_mock, [])
    LanguageToolClient(BASE, username="ana@x.org", api_key="lt-key").check([{"text": "x"}], "en-US")
    form = parse_form(route)
    assert (form["username"], form["apiKey"]) == ("ana@x.org", "lt-key")
    LanguageToolClient(BASE, username="ana@x.org").check([{"text": "x"}], "en-US")
    assert "username" not in parse_form(route) and "apiKey" not in parse_form(route)


def test_429_waits_for_retry_after_then_succeeds(respx_mock):
    respx_mock.post(f"{BASE}/v2/check").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(429, headers={"Retry-After": "999"}),
            httpx.Response(429, headers={"Retry-After": "soon"}),
            httpx.Response(200, json={"matches": [lt_match(0, 1)]}),
        ]
    )
    waits = []
    c = LanguageToolClient(BASE, sleep=waits.append)
    assert len(c.check([{"text": "x"}], "en-US")) == 1
    assert waits == [7.0, 60.0, 5.0]


def test_429_gives_up_after_three_retries(respx_mock):
    respx_mock.post(f"{BASE}/v2/check").respond(429)
    waits = []
    with pytest.raises(httpx.HTTPStatusError):
        LanguageToolClient(BASE, sleep=waits.append).check([{"text": "x"}], "en-US")
    assert len(waits) == 3


def test_lt_inputs_hide_the_key_and_lt_client_uses_settings(respx_mock):
    from pydantic import SecretStr

    from jerome.config import Settings
    from jerome.proofread.languagetool import lt_client, lt_inputs

    s = Settings(
        _env_file=None,
        languagetool_url=BASE,
        languagetool_username="ana@x.org",
        languagetool_api_key=SecretStr("lt-key"),
    )
    assert "lt-key" not in repr(lt_inputs(s))
    assert lt_inputs(s) != lt_inputs(s.model_copy(update={"languagetool_api_key": None}))
    route = lt_route(respx_mock, [])
    lt_client(s).check([{"text": "x"}], "en-US")
    assert parse_form(route)["apiKey"] == "lt-key"
