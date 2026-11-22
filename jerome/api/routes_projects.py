from typing import Annotated

from fastapi import APIRouter, Query

from jerome.api.deps import SessionDep
from jerome.core import projects

router = APIRouter()


@router.get("/projects")
def list_projects(session: SessionDep):
    return projects.list_projects(session)


@router.get("/books/{book_id}/chapter-summary")
def chapter_summary(book_id: int, session: SessionDep):
    return projects.chapter_summary(session, book_id)


@router.get("/chapters/{chapter_id}/review")
def chapter_review(
    chapter_id: int,
    session: SessionDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    return projects.chapter_review(session, chapter_id, offset, limit)
