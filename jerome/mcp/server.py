"""The MCP server instance and its mount on the FastAPI app.

Tool modules register on :data:`mcp` with ``@mcp.tool(...)`` and run their bodies through
``jerome.mcp.context.call``. The SDK (``mcp`` 2.x) calls its server class ``MCPServer``;
it was ``FastMCP`` in 1.x.

``mount_mcp`` serves everything under ``/mcp`` (the endpoint itself, and the
``/mcp/files/`` links routes added to this server's Starlette app) behind
``McpAuthMiddleware``. The transport is streamable HTTP, stateless, with JSON responses.
"""

import logging
from typing import Any

from fastapi import FastAPI
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, InputRequiredResult, TextContent
from pydantic import ValidationError
from starlette.routing import Route

from jerome.mcp.auth import McpAuthMiddleware
from jerome.mcp.context import current_mcp_context
from jerome.mcp.errors import INTERNAL, to_tool_error

log = logging.getLogger(__name__)

PATH = "/mcp"


def _error_result(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


class JeromeMCP(MCPServer):
    """MCPServer with two Jerome needs met through its public ``call_tool``.

    - The call's SDK ``Context`` is published in ``current_mcp_context``, so
      ``context.call(fn)`` can find the HTTP request (and the caller) without every tool
      taking a ``ctx`` parameter. (1.x had ``get_context()``; 2.x has no equivalent.)
    - The SDK prefixes a tool's error text with ``Error executing tool <name>: ``. The
      agent gets exactly ``<code>: <message>`` instead, and arguments that fail the tool's
      input schema read ``invalid: ...`` like any other validation error.
    """

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context | None = None
    ) -> CallToolResult | InputRequiredResult:
        token = current_mcp_context.set(context)
        try:
            return await super().call_tool(name, arguments, context)
        except UnexpectedToolError as exc:
            log.error("MCP tool %r crashed", name, exc_info=exc.__cause__ or exc)
            return _error_result(INTERNAL)
        except ToolError as exc:
            cause = exc.__cause__
            if isinstance(cause, ToolError):
                return _error_result(str(cause))
            if isinstance(cause, ValidationError):
                return _error_result(str(to_tool_error(cause)))
            raise  # e.g. an unknown tool name: the SDK's own text
        finally:
            current_mcp_context.reset(token)


# log_level: the SDK calls logging.basicConfig with it; WARNING keeps INFO chatter out.
mcp = JeromeMCP("jerome", log_level="WARNING")

# The tool modules register on `mcp` when imported.
from jerome.mcp import tools_books  # noqa: E402, F401


def mount_mcp(app: FastAPI) -> None:
    """Serve /mcp on ``app``. Call before the web catch-all so it never shadows /mcp.

    Each app gets its own session manager (the SDK allows one ``run()`` per manager);
    it is kept on ``app.state.mcp_session_manager`` for the app's lifespan to run.
    """
    sdk_app = mcp.streamable_http_app(
        streamable_http_path=PATH,
        stateless_http=True,
        json_response=True,
        # The SDK turns on Host/Origin checks for a localhost bind, which would refuse the
        # public hostname. Every request needs a Bearer key, never a cookie, so a
        # rebinding page has nothing to ride on.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    app.state.mcp_session_manager = mcp.session_manager
    handler = McpAuthMiddleware(sdk_app, main_app=app)
    # Route (not Mount) so "/mcp" itself matches without a slash redirect; the SDK app
    # routes on the full path.
    app.router.routes.append(Route(PATH, handler, include_in_schema=False))
    app.router.routes.append(Route(PATH + "/{path:path}", handler, include_in_schema=False))
