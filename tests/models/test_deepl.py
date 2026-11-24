import json

import httpx
import pytest

from jerome.documents.model import PLACEHOLDER, Mark, MarkKind
from jerome.errors import JeromeError
from jerome.models.base import Usage
from jerome.models.deepl import (
    DeepLProvider,
    billed_chars,
    endpoint,
    from_deepl,
    target_code,
    to_deepl,
)
from jerome.translate.pass_ import (
    TranslateChunk,
    TranslateItem,
    build_translate_request,
    run_translate_pass,
)
from jerome.translate.tags import encode
from jerome.types import StyleSheet

EN = StyleSheet("en-GB", "‘’“”", {}, ["Mr. Usher"])
DE = StyleSheet("de-DE", "„“‚‘", {}, ["A&B"])
PRO = "https://api.deepl.com/v2/translate"


@pytest.mark.parametrize(
    "text",
    [
        "Plain text.",
        "<b>Bold</b> and <a1>a link</a1> ⟦1⟧ then ⟦2⟧.",
        "Escaped &lt;tag&gt; &amp; &#x27E6;not a placeholder&#x27E7;",
        "Mr. Usher met A&amp;B; Mr. Ushers did not.",
    ],
)
def test_round_trip(text):
    assert from_deepl(to_deepl(text, ["Mr. Usher", "A&B"])) == text


def test_to_deepl_shapes():
    out = to_deepl("Mr. Usher ⟦1⟧ <i>Mr. Usher</i> Mr. Ushers", ["Mr. Usher"])
    assert out == ('<keep>Mr. Usher</keep> <x n="1"/> <i><keep>Mr. Usher</keep></i> Mr. Ushers')
    assert to_deepl("<a1>x</a1>", ["a1"]) == "<a1>x</a1>"  # never inside a tag
    # nor inside an entity or a placeholder
    assert to_deepl("a &lt; b lt", ["lt"]) == "a &lt; b <keep>lt</keep>"
    assert to_deepl("⟦1⟧ 1", ["1"]) == '<x n="1"/> <keep>1</keep>'
    assert from_deepl('A <x n="2"></x> literal ⟦9⟧') == "A ⟦2⟧ literal &#x27E6;9&#x27E7;"


def test_codes_and_endpoints():
    assert endpoint("abc:fx") == "https://api-free.deepl.com/v2/translate"
    assert endpoint("abc") == PRO
    assert target_code("de-DE") == "DE" and target_code("de-CH") == "DE"
    assert target_code("en-GB") == "EN-GB" and target_code("en-US") == "EN-US"
    assert billed_chars("<b>Hi</b> &amp; ⟦1⟧") == len("Hi & ⟦1⟧")


def test_translate_texts_request_and_usage(respx_mock):
    route = respx_mock.post(PRO).mock(
        side_effect=lambda req: httpx.Response(
            200,
            json={
                "translations": [
                    {"text": t.replace("Hello", "Hallo")} for t in json.loads(req.content)["text"]
                ]
            },
        )
    )
    p = DeepLProvider("secret-key")
    out, usage = p.translate_texts(
        ["<b>Hello</b> ⟦1⟧", "Hello Mr. Usher"], "en", "DE", ["Mr. Usher"]
    )
    assert out == ["<b>Hallo</b> ⟦1⟧", "Hallo Mr. Usher"]
    assert usage == Usage(len("Hello ") + len("Hello Mr. Usher"), 0)  # placeholders are free
    req = route.calls[0].request
    assert req.headers["authorization"] == "DeepL-Auth-Key secret-key"
    body = json.loads(req.content)
    assert body["text"] == ['<b>Hello</b> <x n="1"/>', "Hello <keep>Mr. Usher</keep>"]
    assert (body["source_lang"], body["target_lang"]) == ("EN", "DE")
    assert body["tag_handling"] == "xml" and body["ignore_tags"] == ["keep", "x"]


def test_translate_texts_batches_by_fifty(respx_mock):
    route = respx_mock.post(PRO).mock(
        side_effect=lambda req: httpx.Response(
            200, json={"translations": [{"text": t} for t in json.loads(req.content)["text"]]}
        )
    )
    out, _ = DeepLProvider("k").translate_texts([str(i) for i in range(120)], "en", "DE")
    assert out == [str(i) for i in range(120)]
    assert [len(json.loads(c.request.content)["text"]) for c in route.calls] == [50, 50, 20]


def test_translate_texts_errors(respx_mock):
    with pytest.raises(JeromeError, match="deepl API key is not set"):
        DeepLProvider(None).translate_texts(["x"], "en", "DE")
    respx_mock.post(PRO).mock(
        side_effect=[
            httpx.Response(456, json={"message": "Quota exceeded"}),
            httpx.Response(200, json={"translations": []}),
        ]
    )
    with pytest.raises(httpx.HTTPStatusError):
        DeepLProvider("k").translate_texts(["x"], "en", "DE")
    with pytest.raises(JeromeError, match="different number"):
        DeepLProvider("k").translate_texts(["x"], "en", "DE")


def test_count_tokens_counts_block_characters():
    chunk = TranslateChunk([TranslateItem(3, "k3", "Hi <you>", [Mark(0, 2, MarkKind.BOLD)])])
    system, user = build_translate_request(EN, DE, chunk, [("Before.", "Vorher.")])
    assert DeepLProvider("k").count_tokens(model="deepl", system=system, user=user) == len(
        "Hi <you>"
    )
    assert DeepLProvider("k").count_tokens(model="deepl", system=system, user="") == 0


class _Scripted:
    """A DeepL stand-in: returns the given replies for the texts it gets."""

    name = "deepl"
    local = False

    def __init__(self, reply):
        self.reply = reply
        self.seen = None

    def translate_texts(self, texts, source_lang, target_lang, keep=()):
        self.seen = (texts, source_lang, target_lang, keep)
        return self.reply(texts), Usage(sum(len(t) for t in texts), 0)


def test_pass_over_deepl_fails_only_the_broken_item():
    good = TranslateItem(1, "k1", f"Hello {PLACEHOLDER}", [Mark(0, 5, MarkKind.BOLD)])
    broken = TranslateItem(2, "k2", "Second one", [Mark(0, 6, MarkKind.ITALIC)])
    provider = _Scripted(lambda texts: ["<b>Hallo</b> ⟦1⟧", "Zweite eins ⟦1⟧"])
    res = run_translate_pass(TranslateChunk([good, broken]), EN, DE, [], provider, "deepl", None)
    assert res.done[1].text == f"Hallo {PLACEHOLDER}" and res.failed == [2]
    assert res.result.provider == "deepl" and res.result.model == "deepl"
    texts, source, target, keep = provider.seen
    assert texts == [encode(good.text, good.marks), encode(broken.text, broken.marks)]
    assert (source, target, keep) == ("en", "DE", ["A&B", "Mr. Usher"])


def test_registry_builds_deepl_for_translation_only():
    from jerome.config import Settings
    from jerome.models.registry import build_registry

    models = {
        "ai": {"provider": "anthropic", "model": "claude-opus-5-5"},
        "translate": {"provider": "deepl", "model": "deepl", "api_key": "k:fx"},
    }
    reg = build_registry(Settings(_env_file=None, models=models))
    provider, cfg = reg.for_pass("translate", local_only=False)
    assert isinstance(provider, DeepLProvider) and provider.api_key == "k:fx"
    assert cfg.model == "deepl"
    assert reg.for_pass("translate", local_only=True) is None
