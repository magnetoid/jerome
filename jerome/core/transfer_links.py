"""Single-use links that move a file in or out of Jerome without passing through the agent.

An MCP client hands every tool argument and result to its model, so a book never travels
inside a tool call. Instead a tool returns a link; the agent PUTs or GETs the raw bytes
with its own tools (``curl``). The link's token is the only credential for that one
transfer, so the agent never puts its API key into a shell command.

- Only the token's sha256 is stored; the raw token is returned once, to the caller.
- A link expires :data:`LINK_TTL` after it is made and works once.
- An upload is received by one PUT, then consumed by one tool call of the key that asked
  for it; its file is deleted only once that call's work has succeeded. A download is
  served by one GET.
- Any link that is unknown, of the other kind, expired or used is :class:`NotFound`.
- Files wait under ``<data_dir>/transfers/<link id>``. Expired rows and their files are
  purged whenever a new link is made.

Lookups lock the row (``FOR UPDATE``), so two requests racing for the same link are served
one after the other and the second finds it used.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from jerome.errors import InvalidChangeError, NotFound
from jerome.orm import TransferLink
from jerome.storage import Storage

LINK_TTL = timedelta(minutes=15)

# One message for every bad token: a 404 tells the holder nothing about why.
_BAD_LINK = "link not found"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_link(
    session: Session, kind: str, actor: str, now: datetime, **fields
) -> tuple[TransferLink, str]:
    token = secrets.token_urlsafe(32)
    link = TransferLink(
        token_hash=_hash(token),
        kind=kind,
        actor=actor,
        created_at=now,
        expires_at=now + LINK_TTL,
        **fields,
    )
    session.add(link)
    session.flush()
    return link, token


def _live_by_token(session: Session, token: str, kind: str, now: datetime) -> TransferLink:
    link = session.scalars(
        select(TransferLink)
        .where(TransferLink.token_hash == _hash(token), TransferLink.kind == kind)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if link is None or link.expires_at <= now or link.used_at is not None:
        raise NotFound(_BAD_LINK)
    return link


def purge_expired(session: Session, storage: Storage, now: datetime) -> int:
    """Delete expired links and their files; returns how many. Rows another request holds
    locked right now are left for the next purge."""
    rows = session.scalars(
        select(TransferLink).where(TransferLink.expires_at <= now).with_for_update(skip_locked=True)
    ).all()
    for link in rows:
        if link.path:
            storage.delete_transfer(link.path)
        session.delete(link)
    session.flush()
    return len(rows)


def create_upload(
    session: Session, storage: Storage, actor: str, now: datetime
) -> tuple[TransferLink, str]:
    """A new upload link for ``actor``: the row, and the raw token (shown only here)."""
    purge_expired(session, storage, now)
    return _new_link(session, "upload", actor, now)


def receive_upload(
    session: Session, storage: Storage, token: str, data: bytes, limit: int, now: datetime
) -> None:
    """Store the one PUT an upload link takes. Over ``limit`` bytes nothing is written."""
    link = _live_by_token(session, token, "upload", now)
    if link.received_at is not None:
        raise NotFound(_BAD_LINK)
    if len(data) > limit:
        raise InvalidChangeError("file too large")
    link.path = storage.save_transfer(link.id, data)
    link.size = len(data)
    link.received_at = now
    session.flush()


def consume_upload(
    session: Session, storage: Storage, upload_id: int, actor: str, now: datetime
) -> bytes:
    """The bytes of a received upload, once, for the key that asked for it.

    The row is marked used but the file is kept: if the work that uses the bytes fails,
    the rollback makes the upload usable again with the same id. Call
    :func:`discard_upload` as the last step once that work has succeeded; an expired
    row's file is purged anyway.
    """
    link = session.get(TransferLink, upload_id, with_for_update=True, populate_existing=True)
    if link is None or link.kind != "upload" or link.actor != actor:
        raise NotFound(f"upload {upload_id} not found")
    if link.expires_at <= now:
        raise NotFound(f"upload {upload_id} has expired; call request_upload again")
    if link.used_at is not None:
        raise NotFound(f"upload {upload_id} was already used; call request_upload again")
    if link.received_at is None:
        raise NotFound(f"upload {upload_id} has not been received; PUT the file first")
    try:
        data = storage.read_transfer(link.path or "")
    except (NotFound, ValueError):
        raise NotFound(
            f"upload {upload_id} is no longer available; call request_upload again"
        ) from None
    link.used_at = now
    session.flush()
    return data


def discard_upload(session: Session, storage: Storage, upload_id: int) -> None:
    """Delete a consumed upload's file. Call it only after the work using it succeeded."""
    link = session.get(TransferLink, upload_id)
    if link is None or link.kind != "upload" or link.used_at is None or link.path is None:
        return
    storage.delete_transfer(link.path)
    link.path = None
    session.flush()


def create_download(
    session: Session,
    storage: Storage,
    actor: str,
    filename: str,
    media_type: str,
    data: bytes,
    now: datetime,
) -> tuple[TransferLink, str]:
    """A download link serving ``data`` once: the row, and the raw token (shown only here)."""
    purge_expired(session, storage, now)
    link, token = _new_link(
        session, "download", actor, now, filename=filename, media_type=media_type, size=len(data)
    )
    link.path = storage.save_transfer(link.id, data)
    session.flush()
    return link, token


def serve_download(
    session: Session, storage: Storage, token: str, now: datetime
) -> tuple[TransferLink, bytes]:
    """The file behind a download link, once: the link is marked used and its file deleted."""
    link = _live_by_token(session, token, "download", now)
    if link.path is None:
        raise NotFound(_BAD_LINK)
    try:
        data = storage.read_transfer(link.path)
    except NotFound:
        raise NotFound(_BAD_LINK) from None
    storage.delete_transfer(link.path)
    link.used_at = now
    link.path = None
    session.flush()
    return link, data
