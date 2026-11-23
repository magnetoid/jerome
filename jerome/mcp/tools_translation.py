"""Translation tools: the REST /v1 translation handlers and their JSON.

``retranslate_block`` calls the route function with ``app.state.text_limits``, the per-key
rate limit REST uses, so a key's REST and MCP calls draw on one budget.
"""

from typing import Any

from mcp.types import ToolAnnotations

from jerome.api import routes_translation
from jerome.core.agent_actions import record_action
from jerome.mcp.context import ToolContext, call
from jerome.mcp.rest import READ_ONLY, rest_json
from jerome.mcp.server import mcp
from jerome.mcp.tools_books import SETTING

# A paid model call each time, with a new result each time.
CALLS_MODEL = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)


@mcp.tool(annotations=READ_ONLY)
def get_translation(book_id: int) -> dict[str, Any]:
    """Get the state of a translated book's translation (`book_id` from `list_books`):
    `counts` of paragraphs per status (pending: not translated yet; done; flagged: the
    model's translation could not be used, so the paragraph still holds its source text;
    kept: a flagged DOCX paragraph accepted untranslated), and the `flagged` paragraphs,
    each with its `block_id`, `chapter_id` and `source` text. A book cannot be exported
    while any paragraph is pending or flagged. Same JSON as GET
    /v1/books/{id}/translation."""

    def body(c: ToolContext) -> dict[str, Any]:
        return rest_json(routes_translation.get_translation(book_id, c.session))

    return call(body)


def _log(c: ToolContext, tool: str, summary: str, block: dict[str, Any]) -> None:
    record_action(
        c.session,
        actor=c.principal.actor,
        tool=tool,
        summary=summary,
        book_id=block["book_id"],
        chapter_id=block["chapter_id"],
        after={"block_id": block["id"], "translation_status": block["translation_status"]},
    )


@mcp.tool(annotations=CALLS_MODEL)
def retranslate_block(block_id: int) -> dict[str, Any]:
    """Translate one paragraph of a translated book again, now (`block_id` from
    `get_translation`'s `flagged` list, or a done paragraph without suggestions). This is a
    paid model call, billed to the book's translation job, under the key's per-minute rate
    limit; it is refused while a job runs on the book. When the reply still cannot be used,
    the paragraph stays flagged. Returns the paragraph: its `text`, `marks` and
    `translation_status`. Same as POST /v1/blocks/{id}/translate. Logged in the Janus
    activity log."""

    def body(c: ToolContext) -> dict[str, Any]:
        block = rest_json(
            routes_translation.translate_block(
                block_id,
                c.session,
                c.registry,
                c.principal.actor,
                c.app.state.text_limits,
            )
        )
        _log(c, "retranslate_block", "Translated a paragraph again", block)
        return block

    return call(body)


@mcp.tool(annotations=SETTING)
def keep_source_block(block_id: int) -> dict[str, Any]:
    """Accept a flagged paragraph of a translated DOCX book untranslated (`block_id` from
    `get_translation`'s `flagged` list), so the book can be exported; fix it later in the
    Word round trip. An EPUB paragraph cannot be kept: translate it again with
    `retranslate_block`. Returns the paragraph, as `retranslate_block` does. Same as POST
    /v1/blocks/{id}/keep-source. Logged in the Janus activity log."""

    def body(c: ToolContext) -> dict[str, Any]:
        block = rest_json(routes_translation.keep_source(block_id, c.session, c.principal.actor))
        _log(c, "keep_source_block", "Kept a paragraph untranslated", block)
        return block

    return call(body)
