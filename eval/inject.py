"""Seeded-error injection: put known mistakes into clean text, remembering where."""

import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from jerome.types import Category


@dataclass(frozen=True)
class InjectedError:
    paragraph: int
    start: int  # span in the *modified* paragraph
    end: int
    wrong: str  # out[paragraph][start:end]
    correct: str  # replacing the span with this restores the original paragraph
    category: Category


# (paragraph, start, end) in the original text, and the possible wrong forms of that span
# optional extras: a group key and the most changes allowed within that group
_Site = tuple

_WORD = re.compile(r"(?<!\w)[^\W\d_]{4,}(?!\w)")
_EN_COMMA_WHICH = re.compile(r"(\w+), which\b")
_EN_INTRO = re.compile(r"(?:^|(?<=[.!?] ))(?:However|Therefore|Indeed|Meanwhile),")
_DE_COMMA = re.compile(r"(\w+), (?:dass|daß|weil|obwohl)\b")
_EN_SUBJ = re.compile(r"\b(?:[Hh]e|[Ss]he|[Ii]t|[Tt]hey|[Ww]e) (is|was|has|does|are|were|have)\b")
_EN_PLURAL_PRONOUN = re.compile(r"[Tt]hey|[Ww]e")
_EN_VERB = {
    "singular": {"is": "are", "was": "were", "has": "have", "does": "do"},
    "plural": {"are": "is", "were": "was", "have": "has"},
}
_DE_ARTICLE = {"dem": "den", "den": "dem", "einem": "einen", "einen": "einem", "des": "dem"}
_DE_ARTICLE_RE = re.compile(r"(?<!\w)(dem|den|einem|einen|des)(?!\w)")
_VARIANTS = {
    "en": [
        ("colour", "color"),
        ("honour", "honor"),
        ("favour", "favor"),
        ("grey", "gray"),
        ("to-morrow", "tomorrow"),
        ("e-mail", "email"),
    ],
    "de": [("daß", "dass"), ("muß", "muss"), ("Thür", "Tür")],
}


def _typo_forms(word: str) -> list[str]:
    forms: set[str] = set()
    for i in range(1, len(word) - 2):  # swap adjacent interior letters
        if word[i] != word[i + 1]:
            forms.add(word[:i] + word[i + 1] + word[i] + word[i + 2 :])
    for i in range(1, len(word) - 1):
        forms.add(word[:i] + word[i + 1 :])  # drop an interior letter
        forms.add(word[: i + 1] + word[i] + word[i + 1 :])  # double it
    forms.discard(word)
    return sorted(forms)


def _typo_sites(paras: list[str], language: str) -> list[_Site]:
    sites = []
    for i, p in enumerate(paras):
        for m in _WORD.finditer(p):
            forms = _typo_forms(m.group())
            if forms:
                sites.append((i, m.start(), m.end(), forms))
    return sites


def _punctuation_sites(paras: list[str], language: str) -> list[_Site]:
    sites = []
    for i, p in enumerate(paras):
        if language == "en":
            for m in _EN_COMMA_WHICH.finditer(p):
                sites.append((i, m.start(1), m.end(1) + 1, [m.group(1)]))
            for m in _EN_INTRO.finditer(p):
                sites.append((i, m.start(), m.end(), [m.group()[:-1]]))
        else:
            for m in _DE_COMMA.finditer(p):
                sites.append((i, m.start(1), m.end(1) + 1, [m.group(1)]))
    return sites


def _grammar_sites(paras: list[str], language: str) -> list[_Site]:
    sites = []
    for i, p in enumerate(paras):
        if language == "en":
            for m in _EN_SUBJ.finditer(p):
                plural = bool(_EN_PLURAL_PRONOUN.match(m.group()))
                table = _EN_VERB["plural" if plural else "singular"]
                verb = m.group(1)
                if verb in table:
                    sites.append((i, m.start(1), m.end(1), [table[verb]]))
        else:
            for m in _DE_ARTICLE_RE.finditer(p):
                sites.append((i, m.start(1), m.end(1), [_DE_ARTICLE[m.group(1)]]))
    return sites


def _match_case(model: str, word: str) -> str:
    return word[:1].upper() + word[1:] if model[:1].isupper() else word


def _consistency_sites(paras: list[str], language: str) -> list[_Site]:
    sites = []
    for a, b in _VARIANTS[language]:
        for form, other in ((a, b), (b, a)):
            pat = re.compile(rf"(?<![\w-]){re.escape(form)}(?![\w-])", re.I)
            hits = [
                (i, m.start(), m.end(), m.group())
                for i, p in enumerate(paras)
                for m in pat.finditer(p)
                if m.group() in (form, form.capitalize())
            ]
            n_other = sum(
                1
                for p in paras
                for m in re.finditer(rf"(?<![\w-]){re.escape(other)}(?![\w-])", p, re.I)
                if m.group() in (other, other.capitalize())
            )
            # the majority must survive k changes: hits - k > n_other + k (a tie yields nothing)
            budget = (len(hits) - n_other - 1) // 2
            if len(hits) >= 2 and budget >= 1:
                sites += [(i, s, e, [_match_case(w, other)], form, budget) for i, s, e, w in hits]
    return sites


_CATEGORIES: list[tuple[Category, Callable[[list[str], str], list[_Site]]]] = [
    # scarcest first, so the plentiful typos do not use up the paragraphs
    (Category.CONSISTENCY, _consistency_sites),
    (Category.PUNCTUATION, _punctuation_sites),
    (Category.GRAMMAR, _grammar_sites),
    (Category.TYPO, _typo_sites),
]


def inject_errors(
    paragraphs: list[str], language: Literal["en", "de"], *, seed: int, per_category: int
) -> tuple[list[str], list[InjectedError]]:
    """Inject up to ``per_category`` errors of each category, at most one per paragraph.

    Returns the modified paragraphs and the truth list, sorted by position."""
    rng = random.Random(seed)
    out = list(paragraphs)
    used: set[int] = set()
    truth: list[InjectedError] = []
    for category, find in _CATEGORIES:
        sites = sorted(find(paragraphs, language), key=lambda s: s[:3])
        rng.shuffle(sites)
        taken = 0
        group_taken: dict[str, int] = {}
        for para, start, end, wrongs, *extra in sites:
            if taken >= per_category:
                break
            if para in used:
                continue
            if extra:
                group, budget = extra
                if group_taken.get(group, 0) >= budget:
                    continue
                group_taken[group] = group_taken.get(group, 0) + 1
            wrong = rng.choice(wrongs)
            original = paragraphs[para]
            out[para] = original[:start] + wrong + original[end:]
            truth.append(
                InjectedError(para, start, start + len(wrong), wrong, original[start:end], category)
            )
            used.add(para)
            taken += 1
    truth.sort(key=lambda t: (t.paragraph, t.start))
    return out, truth
