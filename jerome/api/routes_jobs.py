from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from jerome.api.deps import SessionDep, current_actor, get_registry
from jerome.core import books
from jerome.jobs import service
from jerome.jobs.estimate import ALL_PASSES, PassName, estimate_job, estimate_translation
from jerome.models import Registry
from jerome.orm import Job

router = APIRouter()

RegistryDep = Annotated[Registry, Depends(get_registry)]
Passes = Annotated[list[PassName], Field(min_length=1)]
MaxCost = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=4)]


class EstimateIn(BaseModel):
    """``passes`` and ``chapters`` are for proofreading; a translation covers every pending
    block and ignores them."""

    type: Literal["proofread", "translate"] = "proofread"
    passes: Passes = list(ALL_PASSES)
    chapters: Annotated[list[int], Field(min_length=1)] | None = None


class JobIn(EstimateIn):
    type: Annotated[str, Field(pattern="^(proofread|translate)$")]
    max_cost: MaxCost
    callback_url: str | None = None

    @field_validator("callback_url")
    @classmethod
    def _http_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("callback_url must be an http or https URL with a host")
        return v


class ResumeIn(BaseModel):
    max_cost: MaxCost | None = None


def _job_json(job: Job) -> dict:
    chunks = (job.estimate or {}).get("chapter_chunks", {})
    return {
        "id": job.id,
        "book_id": job.book_id,
        "type": job.type,
        "status": job.status,
        "passes": job.passes,
        "chapter_ids": job.chapter_ids,
        "max_cost": str(job.max_cost),
        "cost_actual": str(job.cost_actual),
        "estimate": job.estimate,
        "stats": job.stats,
        "callback_url": job.callback_url,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "tasks": [
            {
                "id": t.id,
                "kind": t.kind,
                "chapter_id": t.chapter_id,
                "status": t.status,
                "chunks_done": t.chunks_done,
                "chunks_total": chunks.get(str(t.chapter_id)) if t.kind == "chapter" else None,
                "attempts": t.attempts,
                "error": t.error,
            }
            for t in job.tasks
        ],
    }


@router.post("/books/{book_id}/estimate")
def estimate(book_id: int, body: EstimateIn, session: SessionDep, registry: RegistryDep):
    if body.type == "translate":
        return estimate_translation(session, registry, book_id).to_json()
    return estimate_job(
        session, registry, book_id, passes=body.passes, chapter_ids=body.chapters
    ).to_json()


@router.get("/books/{book_id}/jobs")
def list_for_book(book_id: int, session: SessionDep):
    books.get_book(session, book_id)
    stmt = select(Job).where(Job.book_id == book_id).order_by(Job.created_at.desc(), Job.id.desc())
    return [_job_json(j) for j in session.scalars(stmt)]


@router.post("/books/{book_id}/jobs", status_code=201)
def create(
    book_id: int,
    body: JobIn,
    session: SessionDep,
    registry: RegistryDep,
    actor: Annotated[str, Depends(current_actor)],
):
    if body.type == "translate":
        job = service.create_translation_job(
            session,
            registry,
            book_id,
            max_cost=body.max_cost,
            callback_url=body.callback_url,
            actor=actor,
        )
        return _job_json(job)
    job = service.create_job(
        session,
        registry,
        book_id,
        passes=body.passes,
        chapter_ids=body.chapters,
        max_cost=body.max_cost,
        callback_url=body.callback_url,
        actor=actor,
    )
    return _job_json(job)


@router.get("/jobs/{job_id}")
def get(job_id: int, session: SessionDep):
    return _job_json(service.get_job(session, job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: int, session: SessionDep):
    return _job_json(service.cancel_job(session, job_id))


@router.post("/jobs/{job_id}/resume")
def resume(job_id: int, session: SessionDep, body: ResumeIn | None = None):
    return _job_json(service.resume_job(session, job_id, max_cost=body.max_cost if body else None))
