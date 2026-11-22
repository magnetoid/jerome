"""Book tools: the same data as the REST /v1/books routes."""

from mcp.types import ToolAnnotations

from jerome.api.schemas import BookOut
from jerome.core import books
from jerome.mcp.context import ToolContext, call
from jerome.mcp.server import mcp

READ_ONLY = ToolAnnotations(read_only_hint=True)


@mcp.tool(annotations=READ_ONLY)
def list_books() -> list[dict]:
    """List every book, in import order: the same JSON as GET /v1/books."""

    def body(c: ToolContext) -> list[dict]:
        return [
            BookOut.model_validate(b).model_dump(mode="json") for b in books.list_books(c.session)
        ]

    return call(body)
