"""Rule-based proofreading pass backed by a self-hosted LanguageTool server."""

import json
from collections.abc import Sequence

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


class LanguageToolClient:
    def __init__(self, base_url: str, http: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=60)

    def check(self, annotation: list[dict], language: str) -> list[dict]:
        resp = self._http.post(
            f"{self._base_url}/v2/check",
            data={"data": json.dumps({"annotation": annotation}), "language": language},
        )
        resp.raise_for_status()
        return resp.json()["matches"]


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
