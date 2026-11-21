from jerome.core.suggestions import add_candidates
from jerome.orm import Suggestion
from jerome.types import Candidate, Category, Severity, Source


def cand(block, start, end, original, replacement, **kw) -> Candidate:
    fields = {
        "category": Category.TYPO,
        "severity": Severity.MEDIUM,
        "source": Source.LANGUAGETOOL,
        "explanation": "test",
    }
    fields.update(kw)
    return Candidate(block.id, start, end, original, replacement, **fields)


def add_one(session, block, start, end, original, replacement, **kw) -> Suggestion:
    result = add_candidates(
        session, block.book_id, None, [cand(block, start, end, original, replacement, **kw)]
    )
    assert result.created == 1, result
    return (
        session.query(Suggestion)
        .filter_by(block_id=block.id, start=start, end=end, replacement=replacement)
        .order_by(Suggestion.id.desc())
        .first()
    )


def insert_raw_open(session, block, start, end, original, replacement, **kw) -> Suggestion:
    """Insert an open suggestion directly, bypassing the overlap rules."""
    fields = {
        "category": "typo",
        "severity": "medium",
        "source": "languagetool",
        "explanation": "test",
        "status": "open",
    }
    fields.update(kw)
    s = Suggestion(
        book_id=block.book_id,
        block_id=block.id,
        start=start,
        end=end,
        original=original,
        replacement=replacement,
        **fields,
    )
    session.add(s)
    session.flush()
    return s
