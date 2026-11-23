"""Translating a book: the hidden source book and the translated book that mirrors it."""

import hashlib
import logging
from collections import Counter
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session, aliased

from jerome.core.auto_apply import DEFAULT_POLICY, AutoApplyPolicy
from jerome.documents.model import BlockKind, Mark, MarkKind
from jerome.documents.segments import block_spans
from jerome.errors import Conflict, NotFound, UnsupportedLanguagePair
from jerome.models import ModelOutputError, ModelRefusal, Registry
from jerome.orm import (
    ACTIVE_JOB_STATUSES,
    Block,
    Book,
    Chapter,
    Job,
    Segment,
    StyleSheetRow,
    Suggestion,
)
from jerome.proofread.ai_pass import Chunk
from jerome.storage import Storage
from jerome.translate.pass_ import TranslateChunk, TranslateItem, run_translate_pass
from jerome.translate.tags import Decoded, normalize_marks
from jerome.types import DEFAULT_QUOTES, Variant

if TYPE_CHECKING:
    from jerome.jobs.worker import Billed

log = logging.getLogger(__name__)

Language = Literal["en", "de"]

_EMPTY_HASH = hashlib.sha256(b"").hexdigest()
CONTEXT_BLOCKS = 2  # earlier paragraphs a chunk or a single block gets as context
NO_TRANSLATION_MODEL = "no translation model is available for this book"


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


def source_texts(session: Session, block_ids: list[int] | set[int]) -> dict[int, str]:
    """Translated block id -> the text of its source block. Blocks without one are absent."""
    if not block_ids:
        return {}
    src = aliased(Block)
    rows = session.execute(
        select(Block.id, src.text)
        .join(src, Block.source_block_id == src.id)
        .where(Block.id.in_(block_ids))
    )
    return {i: text for i, text in rows}


def attach_sources(session: Session, book: Book, chunks: list[Chunk]) -> list[Chunk]:
    """Give the chunks of a translated book the source paragraph of each block, so the
    proofreading pass can compare them. Other books' chunks are returned untouched."""
    if book.work != "translate":
        return chunks
    for chunk in chunks:
        chunk.sources = source_texts(session, [b.id for b in chunk.blocks])
    return chunks


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


def marks_from_json(raw: list[Any]) -> list[Mark]:
    return [Mark(m["start"], m["end"], MarkKind(m["kind"]), m.get("href")) for m in raw]


def marks_to_json(marks: list[Mark]) -> list[dict[str, Any]]:
    """Marks as stored on a block, the same shape import writes."""
    return [{"start": m.start, "end": m.end, "kind": m.kind.value, "href": m.href} for m in marks]


def translate_items(session: Session, book_id: int, chapter_id: int) -> list[TranslateItem]:
    """Every block of the chapter in order, whatever its status, with its source block's
    text and marks. Covering every block keeps chunk indexes stable across resume."""
    src = aliased(Block)
    rows = session.execute(
        select(Block.id, Block.source_ref, src.text, src.marks)
        .join(src, src.id == Block.source_block_id)
        .where(Block.book_id == book_id, Block.chapter_id == chapter_id)
        .order_by(Block.ordinal)
    )
    return [
        TranslateItem(block_id, ref or str(block_id), text, marks_from_json(marks))
        for block_id, ref, text, marks in rows
    ]


def previous_pairs(session: Session, block_id: int) -> list[tuple[str, str | None]]:
    """The ``CONTEXT_BLOCKS`` blocks before ``block_id`` in its chapter, oldest first, as
    (source text, stored translation); the translation is None unless the block is done."""
    block = session.get(Block, block_id)
    if block is None:
        return []
    src = aliased(Block)
    rows = session.execute(
        select(src.text, Block.text, Block.translation_status)
        .join(src, src.id == Block.source_block_id)
        .where(Block.chapter_id == block.chapter_id, Block.ordinal < block.ordinal)
        .order_by(Block.ordinal.desc())
        .limit(CONTEXT_BLOCKS)
    )
    pairs = [(source, text if status == "done" else None) for source, text, status in rows]
    return pairs[::-1]


def context_from(pairs: list[tuple[str, str | None]]) -> list[tuple[str, str]]:
    """The last ``CONTEXT_BLOCKS`` pairs, leaving out those with no translation."""
    return [(s, t) for s, t in pairs[-CONTEXT_BLOCKS:] if t is not None]


def missing_marks(source: list[Mark], decoded: list[Mark]) -> int:
    """Marks the reply silently left out: per mark kind (a link by its href), how many fewer
    the decoded text has than the source, after normalization."""

    def key(m: Mark) -> tuple[str, str | None]:
        return (m.kind.value, m.href if m.kind == MarkKind.LINK else None)

    want = Counter(key(m) for m in normalize_marks(source))
    have = Counter(key(m) for m in normalize_marks(decoded))
    return sum(max(0, n - have[k]) for k, n in want.items())


def store_translation(
    session: Session,
    block: Block,
    item: TranslateItem,
    decoded: Decoded | None,
    *,
    language: Language,
    source_language: Language,
) -> None:
    """Write ``decoded`` to ``block`` as ``done``, or, when it is None, the source text and
    marks as ``flagged``. Its segments are rebuilt."""
    if decoded is None:
        text, marks, placeholders = item.text, item.marks, None
        block.translation_status = "flagged"
        lang = source_language  # the stored text is still in the source language
    else:
        text, marks = decoded.text, decoded.marks
        identity = decoded.placeholders == list(range(len(decoded.placeholders)))
        placeholders = None if identity else list(decoded.placeholders)
        block.translation_status = "done"
        lang = language
    block.text = text
    block.marks = marks_to_json(marks)
    block.hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    block.placeholders = placeholders
    session.execute(delete(Segment).where(Segment.block_id == block.id))
    spans = block_spans(block.kind, text, lang)
    if spans:
        session.execute(
            insert(Segment),
            [
                {"block_id": block.id, "ordinal": n, "start": s, "end": e}
                for n, (s, e) in enumerate(spans)
            ],
        )
    session.expire(block, ["segments"])


def sync_chapter_title(session: Session, chapter_id: int) -> None:
    """Give the chapter the translated text of its first heading, when that heading's source
    text is the source chapter's title and its translation is done."""
    heading = session.scalars(
        select(Block)
        .where(Block.chapter_id == chapter_id, Block.kind == BlockKind.HEADING.value)
        .order_by(Block.ordinal)
        .limit(1)
    ).first()
    if heading is None or heading.translation_status != "done":
        return
    source = session.get(Block, heading.source_block_id) if heading.source_block_id else None
    if source is None:
        return
    if source.text == session.get(Chapter, source.chapter_id).title:
        session.get(Chapter, chapter_id).title = heading.text


def _active_job(session: Session, book_id: int) -> int | None:
    stmt = select(Job.id).where(Job.book_id == book_id, Job.status.in_(ACTIVE_JOB_STATUSES))
    return session.scalars(stmt).first()


def _translated_block(session: Session, block_id: int) -> tuple[Block, Book]:
    block = session.get(Block, block_id)
    if block is None:
        raise NotFound(f"block {block_id}")
    ensure_not_source(session, block.book_id)
    book = session.get(Book, block.book_id)
    if book.work != "translate" or block.source_block_id is None:
        raise Conflict(f"block {block_id} is not in a translated book")
    return block, book


def _check_retranslatable(session: Session, block: Block, book: Book) -> None:
    if block.translation_status not in ("flagged", "done"):
        raise Conflict(f"block {block.id} is {block.translation_status}; it cannot be translated")
    if _active_job(session, book.id) is not None:
        raise Conflict(f"book {book.id} has an active job; translate the block once it is done")
    has_suggestions = session.scalar(
        select(func.count()).select_from(Suggestion).where(Suggestion.block_id == block.id)
    )
    if has_suggestions:  # their offsets point into the current text
        raise Conflict(f"block {block.id} has proofreading suggestions; it cannot be translated")


def _bill_apart(
    session: Session, registry: Registry, job_id: int, billed: "Billed", configured: str
) -> None:
    """Record a billed call on the job in a transaction of its own, committed now, so the
    spend stays on the books whatever happens to the caller's transaction."""
    from jerome.jobs.worker import usage_row

    with Session(bind=session.get_bind()) as other:
        stmt = select(Job).where(Job.id == job_id).with_for_update()
        job = other.scalars(stmt).one()
        job.cost_actual = job.cost_actual + usage_row(
            other, billed, registry, configured, job_id=job_id, pass_name="translate"
        )
        other.commit()


def retranslate_block(session: Session, registry: Registry, block_id: int, *, actor: str) -> Block:
    """Translate one flagged or done block again, now, with the context a chunk gets.

    No row lock is held during the model call. The call is billed to the book's latest
    translate job in a transaction of its own, so the spend is kept even when the write is
    refused. Then the job and the block are locked (job first, like the worker), the checks
    run again, and the block is written. When the reply cannot be used, the block keeps what
    it had: a flagged block stays flagged."""
    # the worker imports this module (through books), so its billing helpers load late
    from jerome.core.style_sheets import get_style_sheet
    from jerome.jobs.worker import Billed, billed_from

    block, book = _translated_block(session, block_id)
    _check_retranslatable(session, block, book)
    job_id = session.scalar(
        select(Job.id)
        .where(Job.book_id == book.id, Job.type == "translate")
        .order_by(Job.id.desc())
        .limit(1)
    )
    if job_id is None:
        raise Conflict(f"book {book.id} has no translation job")
    chosen = registry.for_pass("translate", local_only=book.local_only)
    if chosen is None:
        raise Conflict(NO_TRANSLATION_MODEL)
    provider, cfg = chosen

    source_style = get_style_sheet(session, book.source_book_id)
    target_style = get_style_sheet(session, book.id)
    src = session.get(Block, block.source_block_id)
    item = TranslateItem(
        block.id, block.source_ref or str(block.id), src.text, marks_from_json(src.marks)
    )
    context = context_from(previous_pairs(session, block.id))
    decoded: Decoded | None = None
    try:
        res = run_translate_pass(
            TranslateChunk([item]),
            source_style,
            target_style,
            context,
            provider,
            cfg.model,
            cfg.effort,
        )
    except (ModelRefusal, ModelOutputError) as exc:
        billed = billed_from(exc)
        log.warning("translating block %s again failed: %r", block_id, exc)
    else:
        billed = Billed(res.result.usage, res.result.model, res.result.provider)
        decoded = res.done.get(block.id)
    if billed is not None:
        _bill_apart(session, registry, job_id, billed, cfg.model)

    lock = {"populate_existing": True}
    session.scalars(
        select(Job).where(Job.id == job_id).with_for_update().execution_options(**lock)
    ).one()
    block = session.scalars(
        select(Block).where(Block.id == block_id).with_for_update().execution_options(**lock)
    ).one()
    _check_retranslatable(session, block, book)  # something may have changed during the call
    if decoded is not None:
        store_translation(
            session,
            block,
            item,
            decoded,
            language=target_style.language,
            source_language=source_style.language,
        )
        sync_chapter_title(session, block.chapter_id)
    log.info("block %s translated again by %s: %s", block_id, actor, block.translation_status)
    session.flush()
    return block


def keep_source_block(session: Session, block_id: int, *, actor: str) -> Block:
    """Accept a flagged block's source text as-is, to be fixed in the Word round trip."""
    block, book = _translated_block(session, block_id)
    if book.format != "docx":
        raise Conflict("only DOCX books can keep a paragraph untranslated; translate it again")
    if block.translation_status != "flagged":
        raise Conflict(f"block {block_id} is {block.translation_status}, not flagged")
    block.translation_status = "kept"
    log.info("block %s kept untranslated by %s", block_id, actor)
    session.flush()
    return block
