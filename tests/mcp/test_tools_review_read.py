import pytest

from jerome.orm import Block, Chapter
from tests.fixtures.suggestions import add_one
from tests.mcp.conftest import actions, mcp_call, mcp_post, tool_error, tool_json


@pytest.fixture
def with_suggestions(session, imported_docx_book):
    blocks = session.query(Block).filter_by(book_id=imported_docx_book.id).order_by(Block.id).all()
    para = next(b for b in blocks if b.text.startswith("Teh"))
    add_one(session, para, 0, 3, "Teh", "The")
    add_one(session, para, 4, 9, "first", "1st", category="style", severity="low")
    return imported_docx_book


@pytest.fixture
def chapter_id(session, with_suggestions):
    return session.query(Chapter).filter_by(book_id=with_suggestions.id).first().id


def test_get_chapter_summary_matches_rest(client, auth, mcp_auth, with_suggestions):
    bid = with_suggestions.id
    rest = client.get(f"/v1/books/{bid}/chapter-summary", headers=auth).json()
    result = mcp_call(client, mcp_auth, "get_chapter_summary", {"book_id": bid})
    assert tool_json(result) == rest


def test_get_chapter_review_matches_rest(client, auth, mcp_auth, chapter_id):
    rest = client.get(f"/v1/chapters/{chapter_id}/review", headers=auth).json()
    assert any(b["suggestions"] for b in rest["blocks"])
    result = mcp_call(client, mcp_auth, "get_chapter_review", {"chapter_id": chapter_id})
    assert tool_json(result) == rest
    params = {"offset": 1, "limit": 1}
    rest = client.get(f"/v1/chapters/{chapter_id}/review", headers=auth, params=params).json()
    result = mcp_call(client, mcp_auth, "get_chapter_review", {"chapter_id": chapter_id, **params})
    assert tool_json(result) == rest


@pytest.mark.parametrize("bad", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_get_chapter_review_rejects_what_rest_rejects(client, auth, mcp_auth, chapter_id, bad):
    url = f"/v1/chapters/{chapter_id}/review"
    assert client.get(url, headers=auth, params=bad).status_code == 422
    result = mcp_call(client, mcp_auth, "get_chapter_review", {"chapter_id": chapter_id, **bad})
    assert tool_error(result).startswith("invalid: ")


def test_list_suggestions_matches_rest(client, auth, mcp_auth, with_suggestions, chapter_id):
    bid = with_suggestions.id
    r = client.get(f"/v1/books/{bid}/suggestions", headers=auth)
    result = tool_json(mcp_call(client, mcp_auth, "list_suggestions", {"book_id": bid}))
    assert result == {"suggestions": r.json(), "total": int(r.headers["X-Total-Count"])}
    assert result["total"] == 2

    params = {"chapter_id": chapter_id, "category": "style", "min_severity": "low", "limit": 1}
    r = client.get(f"/v1/books/{bid}/suggestions", headers=auth, params=params)
    result = tool_json(mcp_call(client, mcp_auth, "list_suggestions", {"book_id": bid, **params}))
    assert result == {"suggestions": r.json(), "total": int(r.headers["X-Total-Count"])}
    assert result["total"] == 1

    r = client.get(f"/v1/books/{bid}/suggestions", headers=auth, params={"offset": 1, "limit": 1})
    result = tool_json(
        mcp_call(client, mcp_auth, "list_suggestions", {"book_id": bid, "offset": 1, "limit": 1})
    )
    assert result == {"suggestions": r.json(), "total": 2}


@pytest.mark.parametrize("bad", [{"category": "bogus"}, {"limit": 0}, {"limit": 501}])
def test_list_suggestions_rejects_what_rest_rejects(client, auth, mcp_auth, book_id, bad):
    url = f"/v1/books/{book_id}/suggestions"
    assert client.get(url, headers=auth, params=bad).status_code == 422
    result = mcp_call(client, mcp_auth, "list_suggestions", {"book_id": book_id, **bad})
    assert tool_error(result).startswith("invalid: ")


def test_list_suggestions_unknown_book(client, mcp_auth):
    result = mcp_call(client, mcp_auth, "list_suggestions", {"book_id": 999999})
    assert tool_error(result).startswith("not_found: ")


def test_get_block_matches_rest(client, auth, mcp_auth, session, with_suggestions):
    bid = with_suggestions.id
    block = session.query(Block).filter(Block.text.startswith("Teh")).one()
    rest = client.get(f"/v1/books/{bid}/blocks/{block.id}", headers=auth).json()
    assert len(rest["suggestions"]) == 2
    result = mcp_call(client, mcp_auth, "get_block", {"book_id": bid, "block_id": block.id})
    assert tool_json(result) == rest


def test_read_tools_write_no_activity(
    client, auth, mcp_auth, session, registry, with_suggestions, chapter_id
):
    from jerome.api.deps import get_registry

    client.app.dependency_overrides[get_registry] = lambda: registry
    bid = with_suggestions.id
    block_id = session.query(Block).filter_by(book_id=bid).first().id
    job = client.post(
        f"/v1/books/{bid}/jobs", json={"type": "proofread", "max_cost": 5}, headers=auth
    ).json()
    calls = {
        "list_projects": {},
        "list_books": {},
        "get_book": {"book_id": bid},
        "list_chapters": {"book_id": bid},
        "get_style_sheet": {"book_id": bid},
        "get_auto_apply": {"book_id": bid},
        "estimate_job": {"book_id": bid},
        "list_jobs": {"book_id": bid},
        "get_job": {"job_id": job["id"]},
        "get_chapter_summary": {"book_id": bid},
        "get_chapter_review": {"chapter_id": chapter_id},
        "list_suggestions": {"book_id": bid},
        "get_block": {"book_id": bid, "block_id": block_id},
    }
    tools = {
        t["name"]: t for t in mcp_post(client, mcp_auth, "tools/list").json()["result"]["tools"]
    }
    for name, arguments in calls.items():
        tool_json(mcp_call(client, mcp_auth, name, arguments))
        assert tools[name]["annotations"]["readOnlyHint"] is True, name
    assert actions(session) == []
