import pytest

from jerome.orm import Chapter
from tests.mcp.conftest import actions, mcp_call, mcp_list_tools, tool_error, tool_json


@pytest.fixture
def chapter(session, imported_docx_book):
    return session.query(Chapter).filter_by(book_id=imported_docx_book.id).first()


def test_list_projects_matches_rest(client, auth, mcp_auth, imported_docx_book):
    rest = client.get("/v1/projects", headers=auth).json()
    assert rest  # one project
    assert tool_json(mcp_call(client, mcp_auth, "list_projects", {})) == rest


def test_get_book_matches_rest(client, auth, mcp_auth, book_id):
    rest = client.get(f"/v1/books/{book_id}", headers=auth).json()
    assert tool_json(mcp_call(client, mcp_auth, "get_book", {"book_id": book_id})) == rest


def test_get_book_unknown_is_not_found(client, auth, mcp_auth):
    assert client.get("/v1/books/999999", headers=auth).status_code == 404
    result = mcp_call(client, mcp_auth, "get_book", {"book_id": 999999})
    assert tool_error(result).startswith("not_found: ")


def test_list_chapters_matches_rest(client, auth, mcp_auth, book_id):
    rest = client.get(f"/v1/books/{book_id}/chapters", headers=auth).json()
    assert rest
    assert tool_json(mcp_call(client, mcp_auth, "list_chapters", {"book_id": book_id})) == rest


def test_get_style_sheet_matches_rest(client, auth, mcp_auth, book_id):
    rest = client.get(f"/v1/books/{book_id}/style-sheet", headers=auth).json()
    result = mcp_call(client, mcp_auth, "get_style_sheet", {"book_id": book_id})
    assert tool_json(result) == rest


def test_get_auto_apply_matches_rest(client, auth, mcp_auth, book_id):
    rest = client.get(f"/v1/books/{book_id}/auto-apply", headers=auth).json()
    result = mcp_call(client, mcp_auth, "get_auto_apply", {"book_id": book_id})
    assert tool_json(result) == rest


def test_set_style_sheet_logs_before_and_after(client, auth, mcp_auth, session, book_id):
    before = client.get(f"/v1/books/{book_id}/style-sheet", headers=auth).json()
    result = mcp_call(
        client,
        mcp_auth,
        "set_style_sheet",
        {"book_id": book_id, "quote_style": "«»‹›", "ignore_words": ["Jerome"]},
    )
    after = client.get(f"/v1/books/{book_id}/style-sheet", headers=auth).json()
    assert tool_json(result) == after
    assert after["quote_style"] == "«»‹›" and after["ignore_words"] == ["Jerome"]
    assert after["variant"] == before["variant"]  # left out: unchanged
    (row,) = actions(session)
    assert (row.tool, row.actor, row.channel, row.summary) == (
        "set_style_sheet",
        "janus-test",
        "mcp",
        "Changed the style sheet",
    )
    assert row.book_id == book_id
    assert (row.before, row.after) == (before, after)


def test_set_style_sheet_rejects_what_rest_rejects(client, auth, mcp_auth, session, book_id):
    bad = {"variant": "xx-XX"}
    assert client.put(f"/v1/books/{book_id}/style-sheet", json=bad, headers=auth).status_code == 422
    result = mcp_call(client, mcp_auth, "set_style_sheet", {"book_id": book_id, **bad})
    assert tool_error(result).startswith("invalid: ")
    short = {"quote_style": "ab"}
    rest = client.put(f"/v1/books/{book_id}/style-sheet", json=short, headers=auth)
    assert rest.status_code == 422
    result = mcp_call(client, mcp_auth, "set_style_sheet", {"book_id": book_id, **short})
    assert tool_error(result) == f"invalid: {rest.json()['detail']}"
    assert actions(session) == []


def _auto_apply(book_id, **changes):
    body = {
        "book_id": book_id,
        "enabled": True,
        "categories": ["typo", "punctuation"],
        "min_severity": "high",
        "house_rules": True,
    }
    return {**body, **changes}


def test_set_auto_apply_summary_on_off_and_change(client, auth, mcp_auth, session, book_id):
    before = client.get(f"/v1/books/{book_id}/auto-apply", headers=auth).json()
    assert before["enabled"] is True
    off = mcp_call(client, mcp_auth, "set_auto_apply", _auto_apply(book_id, enabled=False))
    on = mcp_call(client, mcp_auth, "set_auto_apply", _auto_apply(book_id))
    changed = mcp_call(
        client, mcp_auth, "set_auto_apply", _auto_apply(book_id, min_severity="medium")
    )
    final = client.get(f"/v1/books/{book_id}/auto-apply", headers=auth).json()
    assert tool_json(changed) == final and final["min_severity"] == "medium"
    rows = actions(session)
    assert [r.summary for r in rows] == [
        "Turned auto-apply off",
        "Turned auto-apply on",
        "Changed auto-apply settings",
    ]
    assert {(r.tool, r.actor, r.book_id) for r in rows} == {
        ("set_auto_apply", "janus-test", book_id)
    }
    assert rows[0].before == before and rows[0].after == tool_json(off)
    assert rows[1].before == tool_json(off) and rows[1].after == tool_json(on)
    assert rows[2].after == final


def test_set_auto_apply_rejects_unknown_category(client, mcp_auth, session, book_id):
    result = mcp_call(
        client, mcp_auth, "set_auto_apply", _auto_apply(book_id, categories=["bogus"])
    )
    assert tool_error(result).startswith("invalid: ")
    assert actions(session) == []


def test_set_chapter_proofread_logs(client, auth, mcp_auth, session, book_id, chapter):
    assert chapter.proofread is True
    result = mcp_call(
        client, mcp_auth, "set_chapter_proofread", {"chapter_id": chapter.id, "proofread": False}
    )
    rest = client.get(f"/v1/books/{book_id}/chapters", headers=auth).json()
    assert tool_json(result) == next(c for c in rest if c["id"] == chapter.id)
    assert tool_json(result)["proofread"] is False
    mcp_call(
        client, mcp_auth, "set_chapter_proofread", {"chapter_id": chapter.id, "proofread": True}
    )
    first, second = actions(session)
    assert (first.tool, first.actor, first.summary) == (
        "set_chapter_proofread",
        "janus-test",
        "Marked a chapter as not proofread",
    )
    assert (first.book_id, first.chapter_id) == (book_id, chapter.id)
    assert (first.before, first.after) == ({"proofread": True}, {"proofread": False})
    assert second.summary == "Marked a chapter as proofread"
    assert (second.before, second.after) == ({"proofread": False}, {"proofread": True})


def test_set_chapter_proofread_unknown_chapter(client, mcp_auth, session):
    result = mcp_call(
        client, mcp_auth, "set_chapter_proofread", {"chapter_id": 999999, "proofread": True}
    )
    assert tool_error(result) == "not_found: chapter 999999"
    assert actions(session) == []


def test_delete_book_is_not_a_tool(client, mcp_auth):
    names = mcp_list_tools(client, mcp_auth)
    assert "delete_book" not in names
    assert not [n for n in names if "user" in n]
    assert not [n for n in names if "password" in n or "key" in n or "login" in n]


def test_unknown_tool_is_not_found(client, mcp_auth):
    result = mcp_call(client, mcp_auth, "delete_book", {"book_id": 1})
    assert tool_error(result).startswith("not_found: ")
