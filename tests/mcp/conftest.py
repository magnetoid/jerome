"""Helpers for calling the /mcp endpoint with plain JSON-RPC over the test client."""

import json

import pytest

ACCEPT = {"Accept": "application/json, text/event-stream"}


def mcp_post(client, headers: dict, method: str, params: dict | None = None):
    """POST one JSON-RPC request to /mcp and return the raw HTTP response."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post("/mcp", json=body, headers={**ACCEPT, **headers})


def _result(response) -> dict:
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, payload
    return payload["result"]


def mcp_call(client, headers: dict, tool: str, arguments: dict) -> dict:
    """Call a tool through JSON-RPC `tools/call` and return the JSON-RPC `result`."""
    return _result(mcp_post(client, headers, "tools/call", {"name": tool, "arguments": arguments}))


def mcp_list_tools(client, headers: dict) -> list[str]:
    return [t["name"] for t in _result(mcp_post(client, headers, "tools/list"))["tools"]]


@pytest.fixture
def mcp_auth(session):
    from jerome.core.keys import create_api_key

    return {"Authorization": f"Bearer {create_api_key(session, 'janus-test')}"}


def tool_text(result: dict) -> str:
    """The text of a tool result (for an error: `<code>: <message>`)."""
    return "".join(c["text"] for c in result["content"] if c["type"] == "text")


def tool_json(result: dict):
    """The JSON a successful tool returned, from its structured content.

    The SDK wraps a list (or other non-object) return as ``{"result": ...}``; no Jerome
    tool returns an object whose only key is ``result``, so that wrapper is unwrapped.
    """
    assert result["isError"] is False, tool_text(result)
    structured = result["structuredContent"]
    if set(structured) == {"result"}:
        return structured["result"]
    assert json.loads(tool_text(result)) == structured  # the text block carries the same JSON
    return structured


def tool_error(result: dict) -> str:
    assert result["isError"] is True, tool_text(result)
    return tool_text(result)


def actions(session) -> list:
    from jerome.orm import AgentAction

    return session.query(AgentAction).order_by(AgentAction.id).all()
