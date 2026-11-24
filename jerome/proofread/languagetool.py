"""Rule-based proofreading pass backed by a self-hosted LanguageTool server."""

import hashlib
import json
import time
from collections.abc import Callable, Sequence

import httpx

from jerome.documents.model import PLACEHOLDER
from jerome.types import BlockText, Candidate, Category, Severity, Source, StyleSheet

_ISSUE_MAP: dict[str, tuple[Category, Severity]] = {
    "misspelling": (Category.TYPO, Severity.HIGH),
    "grammar": (Category.GRAMMAR, Severity.HIGH),
    "typographical": (Category.PUNCTUATION, Severity.MEDIUM),
    "locale-violation": (Category.CONSISTENCY, Severity.MEDIUM),
    "style": (Category.STYLE, Severity.LOW),
    "register": (Category.STYLE, Severity.LOW),
}
_FALLBACK = (Category.GRAMMAR, Severity.MEDIUM)


_RETRIES = 3  # after a 429 (LanguageTool Premium limits requests per minute)
_MAX_WAIT = 60.0
_DEFAULT_WAIT = 5.0


class LanguageToolClient:
    def __init__(
        self,
        base_url: str,
        http: httpx.Client | None = None,
        *,
        username: str | None = None,
        api_key: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=60)
        # Premium credentials go with every check, and only when both are set
        self._auth = {"username": username, "apiKey": api_key} if username and api_key else {}
        self._sleep = sleep

    def check(self, annotation: list[dict], language: str) -> list[dict]:
        data = {"data": json.dumps({"annotation": annotation}), "language": language, **self._auth}
        for attempt in range(_RETRIES + 1):
            resp = self._http.post(f"{self._base_url}/v2/check", data=data)
            if resp.status_code != 429 or attempt == _RETRIES:
                break
            self._sleep(_retry_after(resp))
        resp.raise_for_status()
        return resp.json()["matches"]


def _retry_after(resp: httpx.Response) -> float:
    try:
        wait = float(resp.headers.get("Retry-After", _DEFAULT_WAIT))
    except ValueError:
        wait = _DEFAULT_WAIT
    return min(max(wait, 0.0), _MAX_WAIT)


def lt_inputs(settings) -> tuple:
    """What a LanguageTool client is built from, comparable with ==. The key appears only
    as a hash, so the tuple is safe to keep and to print."""
    key = settings.languagetool_api_key
    digest = hashlib.sha256(key.get_secret_value().encode()).hexdigest() if key else None
    return (settings.languagetool_url, settings.languagetool_username, digest)


def lt_client(settings) -> LanguageToolClient:
    key = settings.languagetool_api_key
    return LanguageToolClient(
        settings.languagetool_url,
        username=settings.languagetool_username,
        api_key=key.get_secret_value() if key else None,
    )


def _annotation(text: str) -> list[dict]:
    parts: list[dict] = []
    for i, piece in enumerate(text.split(PLACEHOLDER)):
        if i:
            parts.append({"markup": PLACEHOLDER, "interpretAs": " "})
        if piece:
            parts.append({"text": piece})
    return parts


def _utf16_to_index(text: str) -> list[int]:
    """table[u] = Python index for UTF-16 offset u (astral chars span two units)."""
    table: list[int] = []
    for i, ch in enumerate(text):
        table.append(i)
        if ord(ch) > 0xFFFF:
            table.append(i)
    table.append(len(text))
    return table


def languagetool_candidates(
    blocks: Sequence[BlockText], style: StyleSheet, client: LanguageToolClient
) -> tuple[list[Candidate], int]:
    candidates: list[Candidate] = []
    dropped = 0
    ignore = set(style.ignore_words)
    for block in blocks:
        text = block.text
        if not text.strip():
            continue
        table = _utf16_to_index(text)
        for m in client.check(_annotation(text), style.variant):
            if not m.get("replacements"):
                dropped += 1
                continue
            start = table[m["offset"]]
            end = table[m["offset"] + m["length"]]
            original = text[start:end]
            replacement = m["replacements"][0]["value"]
            issue = m.get("rule", {}).get("issueType", "")
            if (
                (issue == "misspelling" and original in ignore)
                or PLACEHOLDER in original
                or replacement == original
            ):
                dropped += 1
                continue
            category, severity = _ISSUE_MAP.get(issue, _FALLBACK)
            candidates.append(
                Candidate(
                    block.id,
                    start,
                    end,
                    original,
                    replacement,
                    category,
                    severity,
                    Source.LANGUAGETOOL,
                    m.get("message", ""),
                )
            )
    return candidates, dropped
