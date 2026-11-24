from typing import get_args

from sqlalchemy.orm import Session

from jerome.core.translation import ensure_not_source
from jerome.errors import InvalidChangeError, NotFound
from jerome.orm import StyleSheetRow
from jerome.types import DEFAULT_QUOTES, StyleSheet, Variant


def _to_dataclass(row: StyleSheetRow) -> StyleSheet:
    return StyleSheet(
        variant=row.variant,  # type: ignore[arg-type]
        quote_style=row.quote_style,
        house_spellings=dict(row.house_spellings),
        ignore_words=list(row.ignore_words),
    )


def _row(session: Session, book_id: int) -> StyleSheetRow:
    row = session.get(StyleSheetRow, book_id)
    if row is None:
        raise NotFound(f"style sheet of book {book_id}")
    return row


def get_style_sheet(session: Session, book_id: int) -> StyleSheet:
    return _to_dataclass(_row(session, book_id))


def update_style_sheet(
    session: Session,
    book_id: int,
    *,
    variant: Variant | None = None,
    quote_style: str | None = None,
    house_spellings: dict[str, list[str]] | None = None,
    ignore_words: list[str] | None = None,
) -> StyleSheet:
    if variant is not None and variant not in get_args(Variant):
        raise ValueError(f"unknown variant {variant!r}")
    if quote_style is not None and len(quote_style) != 4:
        raise InvalidChangeError(
            "quote_style must be exactly 4 characters: opening and closing double quote, "
            "then opening and closing single quote"
        )
    ensure_not_source(session, book_id)
    row = _row(session, book_id)
    if variant is not None:
        if variant != row.variant and quote_style is None:
            row.quote_style = DEFAULT_QUOTES[variant]  # the old variant's quotes do not carry over
        row.variant = variant
    if quote_style is not None:
        row.quote_style = quote_style
    if house_spellings is not None:
        row.house_spellings = house_spellings
    if ignore_words is not None:
        row.ignore_words = ignore_words
    session.flush()
    return _to_dataclass(row)
