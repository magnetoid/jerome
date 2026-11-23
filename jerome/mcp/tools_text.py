"""Quick text proofreading: the REST /v1/proofread/text handler, with its limits.

The tool calls the route function with ``app.state.text_limits``, the same per-key rate
limit and daily cost cap REST uses, keyed on the API key name, so a key's REST and MCP
calls draw on one budget. It changes nothing in the books, so it is not logged.
"""

import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from starlette.responses import JSONResponse

from jerome.api import routes_text
from jerome.api.routes_text import TextIn, TextOut
from jerome.jobs.estimate import PassName
from jerome.mcp.context import ToolContext, call
from jerome.mcp.rest import rest_json
from jerome.mcp.server import mcp
from jerome.types import Variant


@mcp.tool()
def proofread_text(
    text: str,
    variant: Variant,
    passes: list[PassName] | None = None,
    quote_style: str | None = None,
    ignore_words: list[str] | None = None,
    local_only: bool = False,
) -> dict[str, Any]:
    """Proofread a short text (at most 5000 characters) that is not part of a book, and
    return suggestions without storing them. `variant` is the language; `passes` defaults
    to languagetool and ai (consistency is also available); `quote_style` is four
    characters (primary open/close, then secondary open/close); `ignore_words` are never
    flagged; `local_only` skips the ai pass unless a local model is set up. The ai pass is
    paid model work, under the key's per-minute rate limit and daily cost cap (shared with
    REST). Returns `{"suggestions": [...], "warnings": [...]}`; offsets count Unicode
    code points. The same JSON as POST /v1/proofread/text."""

    def body(c: ToolContext) -> dict[str, Any] | JSONResponse:
        request = TextIn(
            text=text,
            variant=variant,
            quote_style=quote_style,
            ignore_words=ignore_words,
            local_only=local_only,
            **({} if passes is None else {"passes": passes}),
        )
        out = routes_text.proofread_short_text(
            request,
            c.session,
            c.principal.actor,
            c.worker_deps,
            c.settings,
            c.app.state.text_limits,
        )
        if isinstance(out, JSONResponse):
            return out  # REST's 502, returned so the transaction still commits the usage row
        return rest_json(out, TextOut)

    result = call(body)
    if isinstance(result, JSONResponse):
        raise ToolError(f"error: {json.loads(bytes(result.body))['detail']}")
    return result
