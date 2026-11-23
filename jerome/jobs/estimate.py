"""Cost estimates for proofreading and translation jobs."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from jerome.core.books import block_texts, get_book, list_chapters
from jerome.core.style_sheets import get_style_sheet
from jerome.core.translation import NO_TRANSLATION_MODEL, translate_items
from jerome.errors import Conflict, NotFound
from jerome.models import Registry, Usage, cost
from jerome.models.base import SystemPart
from jerome.orm import Block
from jerome.proofread.ai_pass import Chunk, build_request, chunk_blocks
from jerome.translate.pass_ import TranslateChunk, build_translate_request, chunk_items
from jerome.types import StyleSheet

PassName = Literal["languagetool", "ai", "consistency"]
ALL_PASSES: tuple[PassName, ...] = ("languagetool", "ai", "consistency")

_CHARS_PER_TOKEN_X2 = 7  # 3.5 characters per token, as a ratio of integers over 2


@dataclass
class CostRange:
    low: Decimal
    high: Decimal


@dataclass
class Estimate:
    chunks: int
    per_pass: dict[str, CostRange]
    total: CostRange
    chapter_chunks: dict[int, int] = field(default_factory=dict)

    def to_json(self) -> dict:
        def rng(r: CostRange) -> dict[str, str]:
            return {"low": str(r.low), "high": str(r.high)}

        return {
            "chunks": self.chunks,
            "per_pass": {name: rng(r) for name, r in self.per_pass.items()},
            "total": rng(self.total),
            "chapter_chunks": {str(k): v for k, v in self.chapter_chunks.items()},
        }


def included_chapter_ids(
    session: Session, book_id: int, chapter_ids: Sequence[int] | None
) -> list[int]:
    """Chapters a job covers, in book order. Only proofread chapters qualify."""
    chapters = list_chapters(session, book_id)
    if chapter_ids is None:
        return [c.id for c in chapters if c.proofread]
    by_id = {c.id: c for c in chapters}
    for cid in chapter_ids:
        if cid not in by_id:
            raise NotFound(f"chapter {cid} in book {book_id}")
        if not by_id[cid].proofread:
            raise Conflict(f"chapter {cid} is not marked for proofreading")
    wanted = set(chapter_ids)
    return [c.id for c in chapters if c.id in wanted]


def _tokens_from_chars(chars: int) -> int:
    return -(-2 * chars // _CHARS_PER_TOKEN_X2)  # ceil(chars / 3.5)


def _count(provider, model: str, system: list[SystemPart], user: str) -> int:
    counted = provider.count_tokens(model=model, system=system, user=user)
    if counted is not None:
        return counted
    return _tokens_from_chars(sum(len(p.text) for p in system) + len(user))


def _ai_range(session: Session, registry: Registry, book, chunks: list[Chunk]) -> CostRange:
    return ai_cost_range(
        registry, get_style_sheet(session, book.id), chunks, local_only=book.local_only
    )


def ai_cost_range(
    registry: Registry, style: StyleSheet, chunks: list[Chunk], local_only: bool = False
) -> CostRange:
    """Low/high cost of running the AI pass over ``chunks``; needs no database."""
    zero = Decimal(0)
    resolved = registry.for_pass("ai", local_only=local_only)
    if resolved is None or not chunks:
        return CostRange(zero, zero)
    provider, config = resolved
    price = registry.price(config.model)
    low = high = zero
    sys_tokens: int | None = None
    for i, chunk in enumerate(chunks):
        system, user = build_request(style, chunk)
        input_tokens = _count(provider, config.model, system, user)
        output_tokens = -(-input_tokens * 15 // 100)  # ceil(0.15 * input)
        full = cost(price, Usage(input_tokens, output_tokens))
        high += full
        if i == 0:
            low += full
            continue
        if sys_tokens is None:  # the system prefix is identical for every chunk
            sys_tokens = _count(provider, config.model, system, "")
        cached = min(sys_tokens, input_tokens)
        low += cost(price, Usage(input_tokens - cached, output_tokens, cache_read_tokens=cached))
    return CostRange(low, high)


def estimate_job(
    session: Session,
    registry: Registry,
    book_id: int,
    *,
    passes: Sequence[PassName] = ALL_PASSES,
    chapter_ids: Sequence[int] | None = None,
) -> Estimate:
    book = get_book(session, book_id)
    chapter_chunks: dict[int, list[Chunk]] = {
        cid: chunk_blocks(block_texts(session, book_id, cid, proofreadable_only=True))
        for cid in included_chapter_ids(session, book_id, chapter_ids)
    }
    all_chunks = [c for chunks in chapter_chunks.values() for c in chunks]
    zero = CostRange(Decimal(0), Decimal(0))
    per_pass: dict[str, CostRange] = {}
    for name in passes:
        per_pass[name] = _ai_range(session, registry, book, all_chunks) if name == "ai" else zero
    total = CostRange(
        sum((r.low for r in per_pass.values()), Decimal(0)),
        sum((r.high for r in per_pass.values()), Decimal(0)),
    )
    return Estimate(
        len(all_chunks), per_pass, total, {cid: len(c) for cid, c in chapter_chunks.items()}
    )


def pending_chapter_ids(session: Session, book_id: int) -> list[int]:
    """Chapters of a translated book that still have pending blocks, in book order."""
    pending = set(
        session.scalars(
            select(Block.chapter_id).where(
                Block.book_id == book_id, Block.translation_status == "pending"
            )
        )
    )
    return [c.id for c in list_chapters(session, book_id) if c.id in pending]


def translation_chunks(session: Session, book_id: int, chapter_id: int) -> list[TranslateChunk]:
    """The chunks a translate task runs: over every block of the chapter, as the worker
    builds them, so chunk indexes match."""
    return chunk_items(translate_items(session, book_id, chapter_id))


def estimate_translation(session: Session, registry: Registry, book_id: int) -> Estimate:
    """Cost of translating every pending block. Output is ``ceil(1.4 * input)`` tokens: a
    translation runs about as long as its source, German longer than English."""
    book = get_book(session, book_id)
    resolved = registry.for_pass("translate", local_only=book.local_only)
    if resolved is None:
        raise Conflict(NO_TRANSLATION_MODEL)
    provider, config = resolved
    price = registry.price(config.model)
    source = get_style_sheet(session, book.source_book_id)
    target = get_style_sheet(session, book_id)
    pending = set(
        session.scalars(
            select(Block.id).where(Block.book_id == book_id, Block.translation_status == "pending")
        )
    )
    chapter_chunks: dict[int, int] = {}
    low = high = Decimal(0)
    sys_tokens: int | None = None
    first = True
    for cid in pending_chapter_ids(session, book_id):
        called = 0  # chunks the job will call the model for: those with a pending block
        for chunk in translation_chunks(session, book_id, cid):
            items = [it for it in chunk.items if it.block_id in pending]
            if not items:
                continue
            system, user = build_translate_request(source, target, TranslateChunk(items), [])
            input_tokens = _count(provider, config.model, system, user)
            output_tokens = -(-input_tokens * 14 // 10)  # ceil(1.4 * input)
            full = cost(price, Usage(input_tokens, output_tokens))
            high += full
            if first:  # the first call writes the cache
                low += full
                first = False
            else:
                if sys_tokens is None:  # the system prefix is identical for every chunk
                    sys_tokens = _count(provider, config.model, system, "")
                cached = min(sys_tokens, input_tokens)
                low += cost(
                    price, Usage(input_tokens - cached, output_tokens, cache_read_tokens=cached)
                )
            called += 1
        chapter_chunks[cid] = called
    total = CostRange(low, high)
    return Estimate(sum(chapter_chunks.values()), {"translate": total}, total, chapter_chunks)
