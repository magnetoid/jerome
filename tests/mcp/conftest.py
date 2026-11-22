"""Helpers for calling the /mcp endpoint with plain JSON-RPC over the test client."""

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
