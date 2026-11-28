"""A chapter's paragraphs in their three states, for the preview: the original, the
translation (translated books only) and the proofread text, which is the translation or
original with the accepted and edited suggestions applied, the same rule a clean export
uses."""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from jerome.core.books import ensure_imported
from jerome.core.translation import ensure_not_source, marks_from_json, marks_to_json
from jerome.documents.changes import Change, apply_changes
from jerome.documents.model import Mark
from jerome.errors import NotFound
from jerome.orm import Block, Book, Chapter, Suggestion

APPLIED = ("accepted", "edited")


def proofread_changes(suggestions: Iterable[Suggestion]) -> list[Change]:
    """The accepted and edited suggestions as changes, ordered by (start, end, id)."""
    kept = sorted(
        (s for s in suggestions if s.status in APPLIED), key=lambda s: (s.start, s.end, s.id)
    )
    return [
        Change(s.start, s.end, s.final_text if s.status == "edited" else s.replacement)
        for s in kept
    ]


def _shift(mark: Mark, c: Change) -> tuple[int, int]:
    cs, ce, n = c.start, c.end, len(c.replacement)
    d = n - (ce - cs)
    s, e = mark.start, mark.end
    insertion = cs == ce
    if insertion and s == cs:
        new_s = cs + n
    elif s <= cs:
        new_s = s
    elif s >= ce:
        new_s = s + d
    else:
        new_s = cs + n
    if insertion and e == cs:
        new_e = e
    elif e >= ce:
        new_e = e + d
    elif e <= cs:
        new_e = e
    else:
        new_e = cs
    return new_s, new_e


def shift_marks(marks: list[Mark], changes: list[Change]) -> list[Mark]:
    """The marks moved onto the text with ``changes`` applied. A mark covering a change
    takes its replacement; an insertion at a mark's edge stays outside it; a mark whose
    text is all removed is dropped."""
    ordered = sorted(changes, key=lambda c: (c.start, c.end))
    out: list[Mark] = []
    for mark in marks:
        shifted = mark
        # right to left, so each change's offsets are still in base text coordinates
        for c in reversed(ordered):
            s, e = _shift(shifted, c)
            shifted = Mark(s, e, shifted.kind, shifted.href)
        if shifted.start < shifted.end:
            out.append(shifted)
    return out


def applied_spans(text: str, changes: list[Change]) -> list[dict[str, Any]]:
    """Where each change's replacement sits in the changed text, with the text it removed."""
    spans = []
    shift = 0
    for c in sorted(changes, key=lambda c: (c.start, c.end)):
        start = c.start + shift
        spans.append(
            {"start": start, "end": start + len(c.replacement), "removed": text[c.start : c.end]}
        )
        shift += len(c.replacement) - (c.end - c.start)
    return spans


def _proofread(text: str, marks: list[Any], changes: list[Change]) -> dict[str, Any]:
    return {
        "text": apply_changes(text, changes),
        "marks": marks_to_json(shift_marks(marks_from_json(marks), changes)),
        "changes": applied_spans(text, changes),
    }


def _variant(book: Book | None) -> str | None:
    return book.style_sheet.variant if book is not None and book.style_sheet else None


def chapter_preview(session: Session, chapter_id: int, offset: int, limit: int) -> dict:
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise NotFound(f"chapter {chapter_id}")
    book = chapter.book
    ensure_not_source(session, book.id)
    ensure_imported(book)
    translated = book.work == "translate"

    total = session.scalar(select(func.count()).where(Block.chapter_id == chapter_id)) or 0
    blocks = list(
        session.scalars(
            select(Block)
            .where(Block.chapter_id == chapter_id)
            .order_by(Block.ordinal, Block.id)
            .offset(offset)
            .limit(limit)
        )
    )
    ids = [b.id for b in blocks]
    by_block: dict[int, list[Suggestion]] = {i: [] for i in ids}
    if ids:
        for s in session.scalars(select(Suggestion).where(Suggestion.block_id.in_(ids))):
            by_block[s.block_id].append(s)
    sources: dict[int, tuple[str, list[Any]]] = {}
    if translated and ids:
        src = aliased(Block)
        rows = session.execute(
            select(Block.id, src.text, src.marks)
            .join(src, Block.source_block_id == src.id)
            .where(Block.id.in_(ids))
        )
        sources = {i: (text, marks) for i, text, marks in rows}

    out = []
    for b in blocks:
        proofread = _proofread(b.text, b.marks, proofread_changes(by_block[b.id]))
        if translated:
            source = sources.get(b.id)
            original = {"text": source[0], "marks": source[1]} if source else None
            translation = {"text": b.text, "marks": b.marks, "status": b.translation_status}
        else:
            original = {"text": b.text, "marks": b.marks}
            translation = None
        out.append(
            {
                "id": b.id,
                "ordinal": b.ordinal,
                "kind": b.kind,
                "original": original,
                "translation": translation,
                "proofread": proofread,
            }
        )

    source_book = session.get(Book, book.source_book_id) if book.source_book_id else None
    return {
        "chapter_id": chapter_id,
        "book_id": book.id,
        "title": chapter.title,
        "proofread": chapter.proofread,
        "total_blocks": total,
        "offset": offset,
        "work": book.work,
        "variant": _variant(book),
        "source_variant": _variant(source_book) if translated else None,
        "trim_size": book.trim_size,
        "blocks": out,
    }
