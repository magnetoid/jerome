"""Translated books: the state of the translation, and what an editor does with a block the
translate job flagged (translate it again, or, on a DOCX book, keep its source text)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from jerome.api.deps import SessionDep, current_actor, get_registry
from jerome.api.routes_text import get_text_limits, too_many
from jerome.core import books, translation
from jerome.models import Registry
from jerome.orm import Block
from jerome.proofread.text import TextLimits

router = APIRouter()

RegistryDep = Annotated[Registry, Depends(get_registry)]
ActorDep = Annotated[str, Depends(current_actor)]


def block_json(block: Block) -> dict[str, Any]:
    return {
        "id": block.id,
        "book_id": block.book_id,
        "chapter_id": block.chapter_id,
        "ordinal": block.ordinal,
        "kind": block.kind,
        "text": block.text,
        "marks": block.marks,
        "translation_status": block.translation_status,
    }


@router.get("/books/{book_id}/translation")
def get_translation(book_id: int, session: SessionDep):
    books.get_book(session, book_id)
    return {
        "counts": translation.translation_counts(session, book_id),
        "flagged": translation.flagged_blocks(session, book_id),
    }


@router.post("/blocks/{block_id}/translate")
def translate_block(
    block_id: int,
    session: SessionDep,
    registry: RegistryDep,
    actor: ActorDep,
    limits: Annotated[TextLimits, Depends(get_text_limits)],
):
    """Translate one flagged or done block again, now. A paid model call, under the key's
    per-minute rate limit (the one /v1/proofread/text uses); billed to the translate job."""
    if (wait := limits.take(actor)) is not None:
        raise too_many("Rate limit exceeded", wait)
    return block_json(translation.retranslate_block(session, registry, block_id, actor=actor))


@router.post("/blocks/{block_id}/keep-source")
def keep_source(block_id: int, session: SessionDep, actor: ActorDep):
    return block_json(translation.keep_source_block(session, block_id, actor=actor))
