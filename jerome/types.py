from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

Variant = Literal["en-US", "en-GB", "de-DE", "de-AT", "de-CH"]
Role = Literal["super_admin", "member"]


class Category(StrEnum):
    TYPO = "typo"
    GRAMMAR = "grammar"
    PUNCTUATION = "punctuation"
    CONSISTENCY = "consistency"
    STYLE = "style"
    TRANSLATION = "translation"


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return {"high": 3, "medium": 2, "low": 1}[self.value]


AI_VERDICTS = ("correct", "not_an_error", "better")  # the AI's view of a LanguageTool mark


class Source(StrEnum):
    LANGUAGETOOL = "languagetool"
    MODEL = "model"
    CONSISTENCY = "consistency"
    EDITOR = "editor"


class Status(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EDITED = "edited"
    SUPERSEDED = "superseded"


# primary open/close, then secondary open/close
DEFAULT_QUOTES: dict[Variant, str] = {
    "en-US": "“”‘’",
    "en-GB": "‘’“”",
    "de-DE": "„“‚‘",
    "de-AT": "„“‚‘",
    "de-CH": "«»‹›",
}


@dataclass(frozen=True)
class BlockText:
    id: int
    text: str


@dataclass(frozen=True)
class Candidate:
    block_id: int
    start: int
    end: int
    original: str
    replacement: str
    category: Category
    severity: Severity
    source: Source
    explanation: str
    agreed: bool = False  # the model and another checker proposed this same fix
    disputed: bool = False  # another checker proposed a different fix for this span
    alternative: str | None = None  # that other fix's replacement
    rule: str | None = None  # which house-style rule a consistency candidate came from


@dataclass(frozen=True)
class StyleSheet:
    variant: Variant
    quote_style: str
    house_spellings: dict[str, list[str]]
    ignore_words: list[str]

    @property
    def language(self) -> Literal["en", "de"]:
        return "en" if self.variant.startswith("en") else "de"
