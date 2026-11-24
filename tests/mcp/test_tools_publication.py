from urllib.parse import urlsplit

from tests.fixtures.images import make_png
from tests.mcp.conftest import actions, mcp_call, tool_error, tool_json


def test_publication_tools_match_rest(client, auth, mcp_auth, session, book_id):
    rest = client.get(f"/v1/books/{book_id}/publication", headers=auth).json()
    got = tool_json(mcp_call(client, mcp_auth, "get_publication", {"book_id": book_id}))
    assert got == rest
    values = {"authors": ["Ana Novak"], "publisher": "FPP"}
    got = tool_json(
        mcp_call(client, mcp_auth, "update_publication", {"book_id": book_id, "values": values})
    )
    assert got["authors"] == ["Ana Novak"] and got["publisher"] == "FPP"
    bad = mcp_call(
        client, mcp_auth, "update_publication", {"book_id": book_id, "values": {"year": 1}}
    )
    assert "year" in tool_error(bad)
    assert [a.tool for a in actions(session)][-1] == "update_publication"


def test_set_cover_from_upload(client, auth, mcp_auth, book_id):
    up = tool_json(mcp_call(client, mcp_auth, "request_upload", {}))
    path = urlsplit(up["upload_url"]).path
    assert client.put(path, content=make_png(1400, 2100)).status_code == 204
    args = {"book_id": book_id, "upload_id": up["upload_id"]}
    assert tool_json(mcp_call(client, mcp_auth, "set_cover", args))["has_cover"] is True
    assert client.get(f"/v1/books/{book_id}/cover", headers=auth).status_code == 200
    gone = tool_json(mcp_call(client, mcp_auth, "remove_cover", {"book_id": book_id}))
    assert gone["has_cover"] is False
