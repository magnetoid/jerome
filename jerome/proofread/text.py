"""Short-text proofreading: the same passes as a job, on one block, with nothing stored but
the model usage row."""

import logging
import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jerome.jobs.estimate import PassName
from jerome.jobs.worker import Billed, WorkerDeps, billed_from, usage_row
from jerome.models import ModelOutputError, ModelRefusal
from jerome.orm import ModelUsage
from jerome.proofread.ai_pass import Chunk, run_ai_pass
from jerome.proofread.consistency import consistency_candidates
from jerome.proofread.languagetool import languagetool_candidates
from jerome.proofread.merge import merge_candidates
from jerome.types import DEFAULT_QUOTES, BlockText, Candidate, StyleSheet, Variant

log = logging.getLogger(__name__)

MAX_TEXT_CHARS = 5000


@dataclass(frozen=True)
class TextSuggestion:
    start: int  # Unicode code points
    end: int
    original: str
    replacement: str
    category: str
    severity: str
    source: str
    explanation: str


@dataclass
class TextResult:
    suggestions: list[TextSuggestion]
    warnings: list[str] = field(default_factory=list)


class TextProofreadError(Exception):
    """The request cannot be answered (a 502). Any usage row it paid for is already in the
    session, so the caller must return a response rather than raise, to let it commit."""


def _suggestion(c: Candidate) -> TextSuggestion:
    return TextSuggestion(
        c.start,
        c.end,
        c.original,
        c.replacement,
        c.category.value,
        c.severity.value,
        c.source.value,
        c.explanation,
    )


def proofread_text(
    session: Session,
    deps: WorkerDeps,
    *,
    text: str,
    variant: Variant,
    passes: Sequence[PassName] = ("languagetool", "ai"),
    quote_style: str | None = None,
    ignore_words: list[str] | None = None,
    local_only: bool = False,
    actor: str,
) -> TextResult:
    style = StyleSheet(variant, quote_style or DEFAULT_QUOTES[variant], {}, ignore_words or [])
    block = BlockText(0, text)
    candidates: list[Candidate] = []
    warnings: list[str] = []

    if "languagetool" in passes:
        try:
            found, _ = languagetool_candidates([block], style, deps.lt)
        except httpx.HTTPError as exc:
            log.warning("LanguageTool request failed: %r", exc)
            raise TextProofreadError("LanguageTool is unavailable") from exc
        candidates += found

    if "ai" in passes:
        chosen = deps.registry.for_pass("ai", local_only=local_only)
        if chosen is None:
            warnings.append("ai_skipped_local_only")
        else:
            provider, cfg = chosen
            billed: Billed | None = None
            failure: str | None = None
            try:
                res = run_ai_pass(Chunk([block], None), style, provider, cfg.model, cfg.effort)
            except ModelRefusal as exc:
                billed = billed_from(exc)
                warnings.append("ai_refused")
            except ModelOutputError as exc:
                billed = billed_from(exc)
                failure = "The AI model returned unusable output"
                log.warning("AI output error: %r", exc)
            except Exception:
                log.exception("AI provider call failed")
                failure = "The AI provider failed"
            else:
                billed = Billed(res.result.usage, res.result.model, res.result.provider)
                candidates += res.candidates
            if billed is not None:  # a billed call is recorded even when its output is not used
                usage_row(session, billed, deps.registry, cfg.model, api_key_name=actor)
            if failure is not None:
                raise TextProofreadError(failure)

    if "consistency" in passes:
        candidates += consistency_candidates([block], style)

    return TextResult([_suggestion(c) for c in merge_candidates(candidates)], warnings)


class TextLimits:
    """Per-key limits for the short-text endpoint. Both checks return the seconds to wait, or
    None when the call may go ahead."""

    def __init__(self, rpm: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._capacity = float(rpm)
        self._rate = rpm / 60  # tokens per second
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last refill)
        self._lock = threading.Lock()

    def set_rpm(self, rpm: int) -> None:
        """Change the rate; the buckets keep their counters (take clamps them to the new cap)."""
        with self._lock:
            self._capacity = float(rpm)
            self._rate = rpm / 60

    def take(self, key: str) -> float | None:
        """Token bucket: take one token, or say how long until there is one."""
        with self._lock:
            now = self._clock()
            tokens, last = self._buckets.get(key, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last) * self._rate)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                return None
            self._buckets[key] = (tokens, now)
            return (1 - tokens) / self._rate if self._rate > 0 else math.inf

    @staticmethod
    def check_daily(
        session: Session, key: str, cap: Decimal, now: datetime | None = None
    ) -> float | None:
        """Today's (UTC) spend of this key on short texts, against the cap."""
        now = now or datetime.now(UTC)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        spent = session.scalar(
            select(func.coalesce(func.sum(ModelUsage.cost), 0)).where(
                ModelUsage.api_key_name == key,
                ModelUsage.job_id.is_(None),
                ModelUsage.created_at >= midnight,
            )
        )
        if spent < cap:
            return None
        return (midnight + timedelta(days=1) - now).total_seconds()
