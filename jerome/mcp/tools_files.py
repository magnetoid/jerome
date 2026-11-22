"""File tools: import a book or a Word review copy, and export a book, through file links.

Files never travel inside a tool call (the client would hand them to the model). An upload
goes ``request_upload`` -> one ``PUT`` of the raw bytes to ``upload_url`` (for example
``curl --upload-file book.docx "<upload_url>"``) -> ``import_book`` or
``import_review_copy`` with the ``upload_id``. An export returns a ``download_url`` that
takes one ``GET``. Links are single use and expire after 15 minutes; see
``jerome.core.transfer_links``.

Each tool calls the core function its REST route calls, with the same arguments.
"""

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from mcp.types import ToolAnnotations

from jerome.api import routes_exports
from jerome.api.schemas import BookOut
from jerome.core import books, exports, transfer_links
from jerome.core.agent_actions import record_action
from jerome.core.auto_apply import DEFAULT_POLICY, OFF_POLICY
from jerome.core.exports import ExportFormat, ReviewFilters
from jerome.core.review_imports import import_review
from jerome.mcp.context import ToolContext, call, current_request
from jerome.mcp.files import MIB, PREFIX
from jerome.mcp.rest import READ_ONLY, rest_json
from jerome.mcp.server import mcp
from jerome.types import Category, Severity, Variant

# They create something new each time (a link, a book, a review import, an export).
CREATES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)


def _link_url(c: ToolContext, token: str) -> str:
    base = c.settings.public_base_url or str(current_request().base_url)
    return f"{base.rstrip('/')}{PREFIX}/{token}"


def _consume(c: ToolContext, upload_id: int) -> bytes:
    return transfer_links.consume_upload(
        c.session, c.storage, upload_id, c.principal.actor, datetime.now(UTC)
    )


@mcp.tool(annotations=CREATES)
def request_upload() -> dict[str, Any]:
    """Get a link to upload one file (a DOCX or EPUB book, or a Word review copy) to.
    Send the raw file bytes in one HTTP PUT to `upload_url`, for example
    `curl --fail --upload-file book.docx "<upload_url>"` (no API key: the link is the
    credential; it answers 204). Then pass `upload_id` to `import_book` or
    `import_review_copy`. The link takes one PUT of at most `max_bytes` bytes and expires
    at `expires_at` (15 minutes); the upload can be used once, only with this API key."""

    def body(c: ToolContext) -> dict[str, Any]:
        link, token = transfer_links.create_upload(
            c.session, c.storage, c.principal.actor, datetime.now(UTC)
        )
        return {
            "upload_id": link.id,
            "upload_url": _link_url(c, token),
            "expires_at": link.expires_at.isoformat(),
            "max_bytes": c.settings.max_upload_mb * MIB,
        }

    return call(body)


@mcp.tool(annotations=CREATES)
def import_book(
    upload_id: int,
    filename: str,
    variant: Variant,
    title: str | None = None,
    local_only: bool = False,
    auto_apply: bool = True,
) -> dict[str, Any]:
    """Import a manuscript as a new book from an upload (`upload_id` from
    `request_upload`, after the file was PUT to its link). `filename` is the file's name;
    its extension (.docx or .epub) says the format. `variant` is the language variant
    (en-US, en-GB, de-DE, de-AT, de-CH). `title` defaults to the one in the file.
    `local_only` keeps the book away from remote models. `auto_apply` (on by default) lets
    jobs accept clear-cut AI suggestions themselves. Returns the book, as `get_book` shows
    it; its `id` is the `book_id` other tools take. Same as POST /v1/books. Logged in the
    Janus activity log."""

    def body(c: ToolContext) -> dict[str, Any]:
        data = _consume(c, upload_id)
        book = books.import_book(
            c.session,
            c.storage,
            filename=filename or "upload",
            data=data,
            variant=variant,
            title=title,
            local_only=local_only,
            auto_apply=DEFAULT_POLICY if auto_apply else OFF_POLICY,
        )
        out = rest_json(book, BookOut)
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="import_book",
            summary=f'Imported "{out["title"]}"',
            book_id=book.id,
        )
        transfer_links.discard_upload(c.session, c.storage, upload_id)  # last: all went well
        return out

    return call(body)


@mcp.tool(annotations=CREATES)
def import_review_copy(book_id: int, upload_id: int) -> dict[str, Any]:
    """Import a Word review copy that an editor worked on (a `docx-review` export of this
    book, from `export_book`, then edited in Word and uploaded: `upload_id` from
    `request_upload`). Jerome records the editor's accepted and rejected changes and edits
    as decisions made in Word. Returns the import report: `review_import_id` (for
    `get_review_import`), counts per outcome, unmatched block ids, conflicts and notes.
    Same as POST /v1/books/{id}/review-imports. Logged in the Janus activity log."""

    def body(c: ToolContext) -> dict[str, Any]:
        books.get_book(c.session, book_id)  # an unknown book leaves the upload unused
        data = _consume(c, upload_id)
        report = rest_json(asdict(import_review(c.session, book_id, data, c.principal.actor)))
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="import_review_copy",
            summary="Imported a review copy",
            book_id=book_id,
        )
        transfer_links.discard_upload(c.session, c.storage, upload_id)  # last: all went well
        return report

    return call(body)


@mcp.tool(annotations=READ_ONLY)
def get_review_import(book_id: int, import_id: int) -> dict[str, Any]:
    """Get the report of an earlier review-copy import (`import_id` is the
    `review_import_id` that `import_review_copy` returned). Same JSON as
    GET /v1/books/{id}/review-imports/{import_id}."""

    def body(c: ToolContext) -> dict[str, Any]:
        return rest_json(routes_exports.get_review_import(book_id, import_id, c.session))

    return call(body)


@mcp.tool(annotations=CREATES)
def export_book(
    book_id: int,
    format: ExportFormat,
    min_severity: Severity | None = None,
    categories: list[Category] | None = None,
    chapters: list[int] | None = None,
) -> dict[str, Any]:
    """Export a book (`book_id` from `list_books`) as a file and get a link to download
    it. `format`: `docx-review` (Jerome's open suggestions as tracked changes and comments,
    for an editor in Word; bring it back with `import_review_copy`), `docx-tracked` (the
    accepted changes as tracked changes), `docx-clean` (the corrected text) or `epub-clean`
    (for an EPUB book). For `docx-review` only, `min_severity`, `categories` and
    `chapters` (chapter ids from `list_chapters`) narrow which suggestions it carries.
    Returns `download_url`, which takes one HTTP GET (for example
    `curl --fail -o "<filename>" "<download_url>"`; no API key) until `expires_at`
    (15 minutes), and the file's `filename`, `media_type` and `size` in bytes. The same
    file as GET /v1/books/{id}/export."""

    def body(c: ToolContext) -> dict[str, Any]:
        f = exports.export_book(
            c.session,
            c.storage,
            book_id,
            format,
            filters=ReviewFilters(min_severity, categories, chapters),
            actor=c.principal.actor,
        )
        link, token = transfer_links.create_download(
            c.session,
            c.storage,
            c.principal.actor,
            f.filename,
            f.media_type,
            f.data,
            datetime.now(UTC),
        )
        return {
            "download_url": _link_url(c, token),
            "filename": f.filename,
            "media_type": f.media_type,
            "size": len(f.data),
            "expires_at": link.expires_at.isoformat(),
        }

    return call(body)
