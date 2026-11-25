"""Learned words: words LanguageTool must not mark again, in every book of their language.

A reviewer saves one from a LanguageTool mark the AI rated not_an_error ("Remember for all
books"). A misspelling is learned for any spelling rule ("*"); any other mark only for its
own LanguageTool rule. Matching is exact and case-sensitive.
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from jerome.core.style_sheets import get_style_sheet
from jerome.core.suggestions import Channel, decide
from jerome.errors import Conflict, NotFound
from jerome.orm import LearnedWord, Suggestion

ANY_SPELLING = "*"

LearnedRules = frozenset[tuple[str, str]]  # (word, rule) pairs for one language


def covers(rules: LearnedRules, word: str, rule_id: str | None, *, misspelling: bool) -> bool:
    """True if a LanguageTool match of ``word`` under ``rule_id`` is a learned word."""
    if misspelling and (word, ANY_SPELLING) in rules:
        return True
    return rule_id is not None and (word, rule_id) in rules


def rules_for(session: Session, language: str) -> LearnedRules:
    stmt = select(LearnedWord.word, LearnedWord.rule).where(LearnedWord.language == language)
    return frozenset((w, r) for w, r in session.execute(stmt))


def _rule_of(s: Suggestion) -> str:
    if s.source != "languagetool":
        raise Conflict("only a LanguageTool mark can be remembered")
    if s.status != "open":
        raise Conflict(f"cannot remember a {s.status} suggestion")
    if s.ai_verdict != "not_an_error":
        raise Conflict("only a mark the AI rated not_an_error can be remembered")
    if s.category == "typo":
        return ANY_SPELLING
    if not s.lt_rule:
        raise Conflict("this mark was made before Jerome kept LanguageTool's rule")
    return s.lt_rule


def remember(
    session: Session, suggestion_id: int, *, actor: str, channel: Channel = "api"
) -> tuple[LearnedWord, list[int]]:
    """Save the mark's word for all books of its language, then reject the mark and the
    book's other open LanguageTool marks the word covers. The learned word and the ids
    rejected; a word already learned is kept as it was."""
    s = session.get(Suggestion, suggestion_id)
    if s is None:
        raise NotFound(f"suggestion {suggestion_id} not found")
    rule = _rule_of(s)
    language = get_style_sheet(session, s.book_id).language
    session.execute(
        insert(LearnedWord)
        .values(
            language=language,
            word=s.original,
            rule=rule,
            reason=s.ai_reason,
            book_id=s.book_id,
            created_by=actor,
        )
        .on_conflict_do_nothing(index_elements=["language", "word", "rule"])
    )
    word = session.scalars(
        select(LearnedWord).where(
            LearnedWord.language == language,
            LearnedWord.word == s.original,
            LearnedWord.rule == rule,
        )
    ).one()

    others = select(Suggestion).where(
        Suggestion.book_id == s.book_id,
        Suggestion.source == "languagetool",
        Suggestion.status == "open",
        Suggestion.original == s.original,
        Suggestion.id != s.id,
    )
    rules = frozenset({(s.original, rule)})
    targets = [s] + [
        o
        for o in session.scalars(others.order_by(Suggestion.id))
        if covers(rules, o.original, o.lt_rule, misspelling=o.category == "typo")
    ]
    rejected: list[int] = []
    for t in targets:
        try:
            with session.begin_nested():
                decide(session, t.id, "reject", actor=actor, channel=channel)
        except Conflict:
            continue
        rejected.append(t.id)
    return word, rejected


def list_words(session: Session) -> list[LearnedWord]:
    stmt = select(LearnedWord).order_by(LearnedWord.created_at.desc(), LearnedWord.id.desc())
    return list(session.scalars(stmt))


def delete_word(session: Session, word_id: int) -> None:
    """Forget a learned word. The marks it rejected stay rejected."""
    word = session.get(LearnedWord, word_id)
    if word is None:
        raise NotFound(f"learned word {word_id} not found")
    session.delete(word)
    session.flush()
