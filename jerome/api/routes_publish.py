"""Finished files: start a publish job, and download the file it made."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel

from jerome.api.deps import SessionDep, current_actor, get_storage
from jerome.api.routes_exports import content_disposition
from jerome.api.routes_jobs import JobIn, _job_json
from jerome.jobs import service
from jerome.publish.build import PublishFormat, finished_file
from jerome.storage import Storage

router = APIRouter()


class PublishIn(BaseModel):
    format: PublishFormat
    callback_url: str | None = None

    _http_url = JobIn._http_url  # the same check as a job's callback_url


@router.post("/books/{book_id}/publish", status_code=201)
def publish(
    book_id: int,
    body: PublishIn,
    session: SessionDep,
    actor: Annotated[str, Depends(current_actor)],
):
    job = service.create_publish_job(
        session, book_id, fmt=body.format, callback_url=body.callback_url, actor=actor
    )
    return _job_json(job)


@router.get("/jobs/{job_id}/file")
def job_file(job_id: int, session: SessionDep, storage: Annotated[Storage, Depends(get_storage)]):
    f = finished_file(session, storage, job_id)
    return Response(
        f.data,
        media_type=f.media_type,
        headers={"Content-Disposition": content_disposition(f.filename)},
    )
