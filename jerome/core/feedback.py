"""Feedback from the people using Jerome: bug reports, feature requests and recommendations.

Anyone signed in sends it from the web app, with a screenshot of the page they were on; the
super admin reads it on the Feedback page, marks it done and deletes it.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from jerome.core.publication import image_size
from jerome.errors import InvalidFeedback, NotFound
from jerome.orm import FEEDBACK_KINDS, FEEDBACK_STATUSES, Feedback

MAX_MESSAGE = 5000
MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024
_SHORT = 500  # page, viewport and user agent are cut to this


def _short(value: str | None) -> str | None:
    value = (value or "").strip()
    return value[:_SHORT] or None


def send(
    session: Session,
    *,
    kind: str,
    message: str,
    actor: str,
    page: str | None = None,
    viewport: str | None = None,
    user_agent: str | None = None,
    screenshot: bytes | None = None,
) -> Feedback:
    if kind not in FEEDBACK_KINDS:
        raise InvalidFeedback(f"kind must be one of {', '.join(FEEDBACK_KINDS)}")
    message = message.strip()
    if not message:
        raise InvalidFeedback("say what the feedback is about")
    if len(message) > MAX_MESSAGE:
        raise InvalidFeedback(f"feedback is at most {MAX_MESSAGE} characters")
    media_type = None
    if screenshot:
        found = image_size(screenshot)
        if found is None:
            raise InvalidFeedback("the screenshot must be a PNG or JPEG image")
        media_type = found[0]
    row = Feedback(
        kind=kind,
        message=message,
        page=_short(page),
        viewport=_short(viewport),
        user_agent=_short(user_agent),
        screenshot=screenshot if media_type else None,
        screenshot_type=media_type,
        status="new",
        created_by=actor,
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def list_feedback(session: Session) -> list[Feedback]:
    """Newest first; the screenshots are not loaded."""
    return list(session.scalars(select(Feedback).order_by(Feedback.id.desc())))


def _get(session: Session, feedback_id: int) -> Feedback:
    row = session.get(Feedback, feedback_id)
    if row is None:
        raise NotFound(f"feedback {feedback_id} not found")
    return row


def screenshot(session: Session, feedback_id: int) -> tuple[bytes, str]:
    row = _get(session, feedback_id)
    if row.screenshot is None or row.screenshot_type is None:
        raise NotFound(f"feedback {feedback_id} has no screenshot")
    return row.screenshot, row.screenshot_type


def set_status(session: Session, feedback_id: int, status: str) -> Feedback:
    if status not in FEEDBACK_STATUSES:
        raise InvalidFeedback(f"status must be one of {', '.join(FEEDBACK_STATUSES)}")
    row = _get(session, feedback_id)
    row.status = status
    session.flush()
    return row


def delete(session: Session, feedback_id: int) -> None:
    session.delete(_get(session, feedback_id))
    session.flush()
