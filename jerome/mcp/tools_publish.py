"""Finished-file tools: start a publish job, then get a link to the file it made. The same
handlers and JSON as POST /v1/books/{id}/publish and GET /v1/jobs/{id}/file."""

from datetime import UTC, datetime
from typing import Any

from jerome.api import routes_publish
from jerome.api.routes_publish import PublishIn
from jerome.core import transfer_links
from jerome.core.agent_actions import record_action
from jerome.mcp.context import ToolContext, call
from jerome.mcp.rest import rest_json
from jerome.mcp.server import mcp
from jerome.mcp.tools_files import CREATES, _link_url
from jerome.publish.build import PublishFormat
from jerome.publish.build import finished_file as _finished_file


@mcp.tool()
def publish_book(book_id: int, format: PublishFormat) -> dict[str, Any]:
    """Make a book's finished file for readers and shops (`book_id` from `list_books`):
    `epub-book` (an EPUB 3 ebook) or `pdf-book` (a PDF for digital sale, at the book's
    trim size; not a print-ready PDF). It uses the publication details (`get_publication`;
    an author is required) and the cover, and costs no model money. A translated book must
    be fully translated first.
    It runs as a job, like `start_job`: poll `get_job` until `status` is `completed`, then
    call `finished_file`; its `stats.warnings` lists what to look at (no cover, open
    suggestions, parts left out). A book has at most one active job. Logged in the Janus
    activity log."""

    def body(c: ToolContext) -> dict[str, Any]:
        request = PublishIn(format=format)
        job = rest_json(routes_publish.publish(book_id, request, c.session, c.principal.actor))
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="publish_book",
            summary=f"Started publish job {job['id']} ({format})",
            book_id=job["book_id"],
            job_id=job["id"],
        )
        return job

    return call(body)


@mcp.tool(annotations=CREATES)
def finished_file(job_id: int) -> dict[str, Any]:
    """Get a link to the file a completed publish job made (`job_id` from `publish_book`).
    Returns `download_url`, which takes one HTTP GET (for example
    `curl --fail -o "<filename>" "<download_url>"`; no API key) until `expires_at`
    (15 minutes), and the file's `filename`, `media_type` and `size` in bytes. Call it
    again for a new link. The same file as GET /v1/jobs/{id}/file."""

    def body(c: ToolContext) -> dict[str, Any]:
        f = _finished_file(c.session, c.storage, job_id)
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
