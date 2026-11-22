import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from jerome.api.deps import SessionDep, current_actor, get_settings_dep, get_worker_deps
from jerome.config import Settings
from jerome.jobs.estimate import PassName
from jerome.jobs.worker import WorkerDeps
from jerome.proofread.text import (
    MAX_TEXT_CHARS,
    TextLimits,
    TextProofreadError,
    proofread_text,
)
from jerome.types import Variant

router = APIRouter()


class TextIn(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_CHARS)]
    variant: Variant
    passes: Annotated[list[PassName], Field(min_length=1)] = ["languagetool", "ai"]  # noqa: RUF012
    quote_style: Annotated[str, Field(min_length=4, max_length=4)] | None = None
    ignore_words: list[str] | None = None
    local_only: bool = False


class SuggestionOut(BaseModel):
    start: int = Field(description="Offset into the text, in Unicode code points.")
    end: int = Field(description="End offset (exclusive), in Unicode code points.")
    original: str
    replacement: str
    category: str
    severity: str
    source: str
    explanation: str


class TextOut(BaseModel):
    suggestions: list[SuggestionOut]
    warnings: list[str]


def _too_many(detail: str, wait: float) -> HTTPException:
    seconds = 1 if math.isinf(wait) else max(1, math.ceil(wait))
    return HTTPException(status_code=429, detail=detail, headers={"Retry-After": str(seconds)})


@router.post("/proofread/text", response_model=TextOut)
def proofread_short_text(
    body: TextIn,
    request: Request,
    session: SessionDep,
    actor: Annotated[str, Depends(current_actor)],
    deps: Annotated[WorkerDeps, Depends(get_worker_deps)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
):
    limits: TextLimits = request.app.state.text_limits
    if (wait := limits.take(actor)) is not None:
        raise _too_many("Rate limit exceeded", wait)
    if (wait := limits.check_daily(session, actor, settings.text_daily_cost_cap)) is not None:
        raise _too_many("Daily cost cap reached", wait)
    try:
        result = proofread_text(
            session,
            deps,
            text=body.text,
            variant=body.variant,
            passes=body.passes,
            quote_style=body.quote_style,
            ignore_words=body.ignore_words,
            local_only=body.local_only,
            actor=actor,
        )
    except TextProofreadError as exc:
        # returned, not raised: the request transaction must still commit any usage row
        return JSONResponse({"detail": str(exc)}, status_code=502)
    return TextOut(
        suggestions=[SuggestionOut(**vars(s)) for s in result.suggestions],
        warnings=result.warnings,
    )
