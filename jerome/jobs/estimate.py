"""Cost estimate for a proofreading job."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from sqlalchemy.orm import Session

from jerome.core.books import block_texts, get_book, list_chapters
from jerome.core.style_sheets import get_style_sheet
from jerome.errors import Conflict, NotFound
from jerome.models import Registry, Usage, cost
from jerome.models.base import SystemPart
from jerome.proofread.ai_pass import Chunk, build_request, chunk_blocks
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
        cid: chunk_blocks(block_texts(session, book_id, cid))
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
