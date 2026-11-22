import logging
from datetime import UTC, datetime

import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select

from jerome.api.app import create_app
from jerome.api.deps import get_session, get_storage
from jerome.core.users import create_user
from jerome.errors import NotFound
from jerome.mcp.context import call
from jerome.mcp.server import mcp
from jerome.orm import ApiKey
from tests.mcp.conftest import mcp_call, mcp_list_tools, mcp_post

PASSWORD = "correct-horse-1"


def _text(result: dict) -> str:
    return "".join(c["text"] for c in result["content"] if c["type"] == "text")


def _raises(exc: Exception):
    def body(_ctx):
        raise exc

    return body


@pytest.fixture
def test_tools():
    """Throwaway tools for the error mapping, removed again so no other test sees them."""

    def raise_not_found() -> str:
        return call(_raises(NotFound("book 999 not found")))

    def raise_unexpected() -> str:
        return call(_raises(RuntimeError("secret")))

    def takes_int(n: int) -> int:
        return n

    names = {
        "test_raise_not_found": raise_not_found,
        "test_raise_unexpected": raise_unexpected,
        "test_takes_int": takes_int,
    }
    for name, fn in names.items():
        mcp.add_tool(fn, name=name)
    yield
    for name in names:
        mcp.remove_tool(name)


def test_no_key_is_401(client):
    r = mcp_post(client, {}, "tools/list")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_web_catch_all_does_not_shadow_mcp(test_settings, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html>app</html>")
    with TestClient(create_app(test_settings.model_copy(update={"web_dir": site}))) as c:
        assert c.get("/somewhere").text == "<html>app</html>"
        assert mcp_post(c, {}, "tools/list").status_code == 401


def test_bad_key_is_401(client):
    r = mcp_post(client, {"Authorization": "Bearer jk_not-a-real-key"}, "tools/list")
    assert r.status_code == 401


def test_cookie_only_is_401(web_client, session):
    create_user(session, "ana@x.org", "Ana", PASSWORD)
    r = web_client.post("/v1/auth/login", json={"email": "ana@x.org", "password": PASSWORD})
    assert r.status_code == 200
    assert web_client.get("/v1/books").status_code == 200  # the cookie works for REST

    r = mcp_post(web_client, {"X-Jerome-Client": "web"}, "tools/list")
    assert r.status_code == 401


def test_revoked_key_is_refused_on_next_call(client, session, mcp_auth):
    assert mcp_call(client, mcp_auth, "list_books", {})["isError"] is False

    key = session.scalars(select(ApiKey).where(ApiKey.name == "janus-test")).one()
    key.revoked_at = datetime.now(UTC)
    session.flush()

    r = mcp_post(client, mcp_auth, "tools/call", {"name": "list_books", "arguments": {}})
    assert r.status_code == 401


def test_list_books_matches_rest(client, auth, mcp_auth, imported_docx_book):
    rest = client.get("/v1/books", headers=auth).json()
    assert len(rest) == 1

    result = mcp_call(client, mcp_auth, "list_books", {})
    assert result["isError"] is False
    assert result["structuredContent"]["result"] == rest


def test_list_books_is_read_only(client, mcp_auth):
    r = mcp_post(client, mcp_auth, "tools/list")
    tool = next(t for t in r.json()["result"]["tools"] if t["name"] == "list_books")
    assert tool["annotations"]["readOnlyHint"] is True


def test_not_found_maps_to_tool_error(client, mcp_auth, test_tools):
    result = mcp_call(client, mcp_auth, "test_raise_not_found", {})
    assert result["isError"] is True
    assert _text(result) == "not_found: book 999 not found"


def test_unexpected_error_is_generic(client, mcp_auth, test_tools, caplog):
    with caplog.at_level(logging.ERROR, logger="jerome.mcp.context"):
        r = mcp_post(
            client, mcp_auth, "tools/call", {"name": "test_raise_unexpected", "arguments": {}}
        )
    result = r.json()["result"]
    assert result["isError"] is True
    assert _text(result) == "error: internal error"
    assert "secret" not in r.text
    assert any("secret" in (rec.exc_text or "") for rec in caplog.records)  # logged server-side


def test_bad_arguments_are_invalid(client, mcp_auth, test_tools):
    result = mcp_call(client, mcp_auth, "test_takes_int", {"n": "not a number"})
    assert result["isError"] is True
    assert _text(result).startswith("invalid: n: ")
    assert "not a number" not in _text(result)  # the rejected value is not echoed


def test_test_tools_do_not_leak(client, mcp_auth):
    assert not [n for n in mcp_list_tools(client, mcp_auth) if n.startswith("test_")]


@pytest.mark.anyio
async def test_sdk_client_can_initialize_and_list(session, tmp_storage, test_settings, mcp_auth):
    app = create_app(test_settings)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_storage] = lambda: tmp_storage
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with (
            httpx2.AsyncClient(
                transport=transport, base_url="http://testserver", headers=mcp_auth
            ) as http,
            streamable_http_client("http://testserver/mcp", http_client=http) as (read, write),
            ClientSession(read, write) as mcp_session,
        ):
            await mcp_session.initialize()
            tools = await mcp_session.list_tools()
            assert "list_books" in [t.name for t in tools.tools]
            result = await mcp_session.call_tool("list_books", {})
            assert result.is_error is False
