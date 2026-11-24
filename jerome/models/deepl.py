"""DeepL as a translation provider.

The translate pass hands DeepL each item's tagged text (``jerome.translate.tags``). DeepL's
XML tag handling keeps the tags; placeholders ``⟦N⟧`` travel as ``<x n="N"/>`` and
ignore-list words inside ``<keep>``, both of which DeepL leaves alone. What comes back goes
through the same strict decode as a model's reply, so a changed tag fails only its item.
DeepL bills characters, so the usage records source characters as input tokens."""

import re
from collections.abc import Sequence

import httpx

from jerome.errors import JeromeError
from jerome.models.base import SystemPart, Usage
from jerome.translate.tags import escape_text

_BATCH = 50  # DeepL takes at most 50 texts per request
_TAG = re.compile(r"(<[^<>]*>)")
_PLACEHOLDER = re.compile(r"⟦([0-9]+)⟧")
_X = re.compile(r'<x\s+n="([0-9]+)"\s*/>|<x\s+n="([0-9]+)"\s*>\s*</x>')
_KEEP = re.compile(r"</?keep\s*>")
_BLOCK = re.compile(r'<block id="-?[0-9]+">(.*?)</block>', re.DOTALL)
_ENTITY = re.compile(r"&(?:lt|gt|amp|#x27[eE][67]);")


def endpoint(api_key: str) -> str:
    """DeepL Free keys end in ``:fx`` and have their own host."""
    host = "api-free.deepl.com" if api_key.endswith(":fx") else "api.deepl.com"
    return f"https://{host}/v2/translate"


def target_code(variant: str) -> str:
    """DeepL's target language for a style-sheet variant such as ``de-DE`` or ``en-GB``."""
    lang = variant.split("-")[0].lower()
    if lang == "en":
        return "EN-GB" if variant.lower() == "en-gb" else "EN-US"
    return lang.upper()


def _keep_words(text: str, keep: Sequence[str]) -> str:
    words = sorted({escape_text(w) for w in keep if w.strip()}, key=len, reverse=True)
    if not words:
        return text
    pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in words) + r")(?!\w)")
    return "".join(
        piece if _TAG.fullmatch(piece) else pattern.sub(r"<keep>\1</keep>", piece)
        for piece in _TAG.split(text)
    )


def to_deepl(text: str, keep: Sequence[str] = ()) -> str:
    """Tagged text as DeepL gets it: placeholders as ``<x n="N"/>``, kept words wrapped."""
    return _PLACEHOLDER.sub(r'<x n="\1"/>', _keep_words(text, keep))


def from_deepl(text: str) -> str:
    """DeepL's reply back in the tag syntax: a literal ⟦ or ⟧ DeepL wrote out is escaped
    first, so only ``<x>`` elements become placeholders."""
    text = text.replace("⟦", "&#x27E6;").replace("⟧", "&#x27E7;")
    text = _X.sub(lambda m: f"⟦{m.group(1) or m.group(2)}⟧", text)
    return _KEEP.sub("", text)


def billed_chars(text: str) -> int:
    """Characters DeepL bills for: the text without tags, an entity counting as one."""
    return len(_ENTITY.sub("x", _TAG.sub("", text)))


class DeepLProvider:
    name = "deepl"
    local = False

    def __init__(self, api_key: str | None, http: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self._http = http or httpx.Client(timeout=120)

    def translate_texts(
        self, texts: list[str], source_lang: str, target_lang: str, keep: Sequence[str] = ()
    ) -> tuple[list[str], Usage]:
        """Translate tagged texts, in order. Raises on any HTTP error (the task retries)."""
        if not self.api_key:
            raise JeromeError("deepl API key is not set")
        out: list[str] = []
        for start in range(0, len(texts), _BATCH):
            batch = texts[start : start + _BATCH]
            resp = self._http.post(
                endpoint(self.api_key),
                headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
                json={
                    "text": [to_deepl(t, keep) for t in batch],
                    "source_lang": source_lang.upper(),
                    "target_lang": target_lang,
                    "tag_handling": "xml",
                    "ignore_tags": ["keep", "x"],
                    "split_sentences": "nonewlines",
                    "preserve_formatting": True,
                    "outline_detection": False,
                },
            )
            resp.raise_for_status()
            translations = resp.json().get("translations")
            if not isinstance(translations, list) or len(translations) != len(batch):
                raise JeromeError("DeepL returned a different number of translations")
            out.extend(from_deepl(str(t.get("text", ""))) for t in translations)
        return out, Usage(sum(billed_chars(t) for t in texts), 0)

    def complete(self, **_: object):  # the translate pass calls translate_texts instead
        raise JeromeError("DeepL only translates")

    def count_tokens(self, *, model: str, system: list[SystemPart], user: str) -> int | None:
        """Billed characters of the blocks in a translate request (the estimate's input)."""
        return sum(billed_chars(m.group(1)) for m in _BLOCK.finditer(user))
