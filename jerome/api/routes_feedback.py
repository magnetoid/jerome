"""Feedback from the web app: anyone signed in sends it; the super admin reads it."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from jerome.api.deps import SessionDep, current_actor, read_limited, require_super_admin
from jerome.api.schemas import FeedbackOut, FeedbackStatusIn
from jerome.core import feedback

router = APIRouter()

_admin = [Depends(require_super_admin)]


@router.post("/feedback", response_model=FeedbackOut, status_code=201)
def send_feedback(
    session: SessionDep,
    actor: Annotated[str, Depends(current_actor)],
    kind: Annotated[str, Form()],
    message: Annotated[str, Form()],
    page: Annotated[str | None, Form()] = None,
    viewport: Annotated[str | None, Form()] = None,
    user_agent: Annotated[str | None, Form()] = None,
    screenshot: Annotated[UploadFile | None, File()] = None,
):
    shot = read_limited(screenshot, feedback.MAX_SCREENSHOT_BYTES) if screenshot else None
    return feedback.send(
        session,
        kind=kind,
        message=message,
        actor=actor,
        page=page,
        viewport=viewport,
        user_agent=user_agent,
        screenshot=shot,
    )


@router.get("/feedback", response_model=list[FeedbackOut], dependencies=_admin)
def list_feedback(session: SessionDep):
    return feedback.list_feedback(session)


@router.get("/feedback/{feedback_id}/screenshot", dependencies=_admin)
def get_screenshot(feedback_id: int, session: SessionDep):
    data, media_type = feedback.screenshot(session, feedback_id)
    return Response(data, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})


@router.patch("/feedback/{feedback_id}", response_model=FeedbackOut, dependencies=_admin)
def set_status(feedback_id: int, body: FeedbackStatusIn, session: SessionDep):
    return feedback.set_status(session, feedback_id, body.status)


@router.delete("/feedback/{feedback_id}", status_code=204, dependencies=_admin)
def delete_feedback(feedback_id: int, session: SessionDep):
    feedback.delete(session, feedback_id)
    return Response(status_code=204)
