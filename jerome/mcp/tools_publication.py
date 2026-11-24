"""Publication tools: a book's details and cover for its finished files. The same handlers
and JSON as the REST routes; writing tools log one ``agent_actions`` row."""

from dataclasses import asdict
from typing import Any

from jerome.core import publication, transfer_links
from jerome.core.agent_actions import record_action
from jerome.core.books import get_book
from jerome.mcp.context import ToolContext, call
from jerome.mcp.rest import READ_ONLY, rest_json
from jerome.mcp.server import mcp
from jerome.mcp.tools_books import SETTING
from jerome.mcp.tools_files import _consume


@mcp.tool(annotations=READ_ONLY)
def get_publication(book_id: int) -> dict[str, Any]:
    """Get a book's publication details, used by its finished EPUB and PDF: title,
    language, authors, translator, publisher, ISBNs (EPUB, PDF), year, rights line,
    description, trim size of the PDF and whether it has a cover. Same JSON as
    GET /v1/books/{id}/publication."""

    def body(c: ToolContext) -> dict[str, Any]:
        return rest_json(asdict(publication.get_publication(c.session, book_id)))

    return call(body)


@mcp.tool(annotations=SETTING)
def update_publication(book_id: int, values: dict[str, Any]) -> dict[str, Any]:
    """Change a book's publication details. `values` holds only the fields to change, as
    in PUT /v1/books/{id}/publication: `authors` (a list of names in display order),
    `translator`, `publisher`, `isbn_epub`, `isbn_pdf` (ISBN-13), `year`, `rights`,
    `description`, `trim_size` (6x9in, 5.5x8.5in, 5x8in or a5). A field set to null is
    cleared. Returns the details."""

    def body(c: ToolContext) -> dict[str, Any]:
        pub = publication.update_publication(c.session, book_id, dict(values))
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="update_publication",
            summary="Changed the publication details",
            book_id=book_id,
        )
        return rest_json(asdict(pub))

    return call(body)


@mcp.tool(annotations=SETTING)
def set_cover(book_id: int, upload_id: int) -> dict[str, Any]:
    """Set a book's cover from an upload (`upload_id` from `request_upload`): a JPEG or
    PNG at least 1400 px on its long side. A refused image keeps the earlier cover.
    Returns the publication details."""

    def body(c: ToolContext) -> dict[str, Any]:
        get_book(c.session, book_id)  # an unknown book leaves the upload unused
        data = _consume(c, upload_id)
        pub = publication.set_cover(c.session, c.storage, book_id, data)
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="set_cover",
            summary="Set the cover",
            book_id=book_id,
        )
        transfer_links.discard_upload(c.session, c.storage, upload_id)
        return rest_json(asdict(pub))

    return call(body)


@mcp.tool(annotations=SETTING)
def remove_cover(book_id: int) -> dict[str, Any]:
    """Remove a book's cover. Returns the publication details."""

    def body(c: ToolContext) -> dict[str, Any]:
        pub = publication.delete_cover(c.session, c.storage, book_id)
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="remove_cover",
            summary="Removed the cover",
            book_id=book_id,
        )
        return rest_json(asdict(pub))

    return call(body)
