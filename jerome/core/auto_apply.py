"""Auto-apply: the AI accepts clear-cut suggestions itself, and a person can reverse them.

A book's policy says which suggestions count as clear-cut. The worker applies them as soon
as they are stored, with the actor ``ai`` on the ``auto`` channel, so every applied change
is in the decision log and ``decide`` lets a person reject or edit it in one step.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.orm import Session

from jerome.core.suggestions import decide, is_auto_applied
from jerome.errors import Conflict, NotFound
from jerome.orm import Book, Suggestion
from jerome.types import Category, Severity, Source

__all__ = [
    "DEFAULT_POLICY",
    "HOUSE_RULES",
    "OFF_POLICY",
    "AutoApplyPolicy",
    "apply_clear_cut",
    "get_policy",
    "is_auto_applied",
    "policy_from_json",
    "policy_to_json",
    "qualifies",
    "set_policy",
]

# consistency rules that come from the book's own style sheet, not from guessing
HOUSE_RULES = ("house_spelling", "quote_style")
# a straight quote has no style of its own; a curly one may be half of a pair the rule
# cannot see whole (in de-DE, “Hallo” would become “Hallo“), so only straight ones apply
_STRAIGHT_QUOTES = frozenset("\"'")


@dataclass(frozen=True)
class AutoApplyPolicy:
    enabled: bool
    categories: frozenset[Category]
    min_severity: Severity
    house_rules: bool


DEFAULT_POLICY = AutoApplyPolicy(
    enabled=True,
    categories=frozenset({Category.TYPO, Category.PUNCTUATION}),
    min_severity=Severity.HIGH,
    house_rules=True,
)
OFF_POLICY = replace(DEFAULT_POLICY, enabled=False)


def policy_to_json(p: AutoApplyPolicy) -> dict[str, Any]:
    """The ``books.auto_apply`` shape. Categories are sorted, so equal policies store equal
    JSON and the column's server default equals ``policy_to_json(OFF_POLICY)``."""
    return {
        "enabled": p.enabled,
        "categories": sorted(c.value for c in p.categories),
        "min_severity": p.min_severity.value,
        "house_rules": p.house_rules,
    }


def policy_from_json(d: dict[str, Any]) -> AutoApplyPolicy:
    return AutoApplyPolicy(
        enabled=bool(d["enabled"]),
        categories=frozenset(Category(c) for c in d["categories"]),
        min_severity=Severity(d["min_severity"]),
        house_rules=bool(d["house_rules"]),
    )


def _book(session: Session, book_id: int) -> Book:
    book = session.get(Book, book_id)
    if book is None:
        raise NotFound(f"book {book_id}")
    return book


def get_policy(session: Session, book_id: int) -> AutoApplyPolicy:
    return policy_from_json(_book(session, book_id).auto_apply)


def set_policy(session: Session, book_id: int, p: AutoApplyPolicy) -> AutoApplyPolicy:
    from jerome.core.translation import ensure_not_source  # it imports this module

    ensure_not_source(session, book_id)
    _book(session, book_id).auto_apply = policy_to_json(p)
    session.flush()
    return p


def qualifies(s: Suggestion, p: AutoApplyPolicy) -> bool:
    """True if ``s`` is clear-cut under ``p``.

    Disputed suggestions never qualify, nor does a quote-style change of a curly quote: only
    straight quotes are clear-cut. A house rule qualifies whichever checker's twin carries
    it. Other consistency findings (variants, names) are guesses about the book and never
    qualify. Otherwise only the AI's suggestions in the chosen categories at or above the
    minimum severity do: LanguageTool alone is never enough.
    """
    if not p.enabled or s.disputed:
        return False
    if s.rule == "quote_style" and not (s.original and set(s.original) <= _STRAIGHT_QUOTES):
        return False
    if s.rule in HOUSE_RULES and p.house_rules:
        return True
    if s.source == Source.CONSISTENCY:
        return False
    return (
        s.source == Source.MODEL
        and Category(s.category) in p.categories
        and Severity(s.severity).rank >= p.min_severity.rank
    )


def apply_clear_cut(session: Session, rows: Sequence[Suggestion], p: AutoApplyPolicy) -> int:
    """Accept each qualifying row as the AI and return how many were applied.

    A row ``decide`` refuses (it is no longer open, or overlaps a change already made) is
    skipped. Each accept runs in a savepoint, as ``bulk_decide`` does, so a refusal leaves
    the caller's transaction as it was and the other rows still apply.
    """
    applied = 0
    for s in rows:
        if not qualifies(s, p):
            continue
        try:
            with session.begin_nested():
                decide(session, s.id, "accept", actor="ai", channel="auto")
        except Conflict:
            continue
        applied += 1
    return applied
