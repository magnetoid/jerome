"""Translating a book: the hidden source book and the translated book that mirrors it."""

import hashlib

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from jerome.core.auto_apply import DEFAULT_POLICY, AutoApplyPolicy
from jerome.errors import Conflict, UnsupportedLanguagePair
from jerome.orm import Block, Book, Chapter, StyleSheetRow
from jerome.storage import Storage
from jerome.types import DEFAULT_QUOTES, Variant

_EMPTY_HASH = hashlib.sha256(b"").hexdigest()


def source_of(session: Session, book: Book) -> Book | None:
    """The source book of a translated book, else None."""
    if book.source_book_id is None:
        return None
    return session.get(Book, book.source_book_id)


def ensure_not_source(session: Session, book_id: int) -> None:
    """Refuse work on a hidden source book; point at the translated book instead."""
    book = session.get(Book, book_id)
    if book is None or not book.is_source:
        return
    translated_id = session.scalar(select(Book.id).where(Book.source_book_id == book_id))
    raise Conflict(
        f"book {book_id} is the source of translated book {translated_id}; "
        f"work on book {translated_id}"
    )


def translation_counts(session: Session, book_id: int) -> dict[str, int]:
    """Number of blocks per translation status."""
    rows = session.execute(
        select(Block.translation_status, func.count())
        .where(Block.book_id == book_id, Block.translation_status.is_not(None))
        .group_by(Block.translation_status)
    )
    return {status: n for status, n in rows}


def import_for_translation(
    session: Session,
    storage: Storage,
    *,
    filename: str,
    data: bytes,
    source_variant: Variant,
    target_variant: Variant,
    translated_title: str | None = None,
    title: str | None = None,
    local_only: bool = False,
    auto_apply: AutoApplyPolicy = DEFAULT_POLICY,
) -> Book:
    """Import ``data`` as a hidden source book and return the empty translated book."""
    if source_variant[:2] == target_variant[:2]:
        raise UnsupportedLanguagePair(
            f"cannot translate {source_variant} to {target_variant}: "
            "source and target must be different languages"
        )
    from jerome.core.books import import_book  # books imports this module for its guards

    source = import_book(
        session,
        storage,
        filename=filename,
        data=data,
        variant=source_variant,
        title=title,
        local_only=local_only,
        auto_apply=auto_apply,
    )
    source.is_source = True

    book = Book(
        title=translated_title or source.title,
        format=source.format,
        local_only=local_only,
        original_filename=source.original_filename,
        auto_apply=source.auto_apply,
        work="translate",
        source_book_id=source.id,
        translated_title=translated_title,
    )
    book.style_sheet = StyleSheetRow(
        variant=target_variant,
        quote_style=DEFAULT_QUOTES[target_variant],
        house_spellings={},
        ignore_words=[],
    )
    session.add(book)
    session.flush()

    chapters = list(
        session.scalars(
            select(Chapter).where(Chapter.book_id == source.id).order_by(Chapter.ordinal)
        )
    )
    if chapters:
        ids = list(
            session.scalars(
                insert(Chapter).returning(Chapter.id, sort_by_parameter_order=True),
                [
                    {
                        "book_id": book.id,
                        "ordinal": c.ordinal,
                        "title": c.title,
                        "proofread": c.proofread,
                        "source_ref": c.source_ref,
                    }
                    for c in chapters
                ],
            )
        )
        new_chapter = {c.id: nid for c, nid in zip(chapters, ids, strict=True)}
        blocks = list(
            session.execute(
                select(Block.id, Block.chapter_id, Block.ordinal, Block.kind, Block.source_ref)
                .where(Block.book_id == source.id)
                .order_by(Block.id)
            )
        )
        if blocks:
            session.execute(
                insert(Block),
                [
                    {
                        "book_id": book.id,
                        "chapter_id": new_chapter[b.chapter_id],
                        "ordinal": b.ordinal,
                        "kind": b.kind,
                        "source_ref": b.source_ref,
                        "text": "",
                        "marks": [],
                        "hash": _EMPTY_HASH,
                        "source_block_id": b.id,
                        "translation_status": "pending",
                    }
                    for b in blocks
                ],
            )
    session.expire(book)
    return book
