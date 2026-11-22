import pytest

from jerome.orm import AgentAction, Block, Decision
from tests.fixtures.suggestions import add_one
from tests.mcp.conftest import actions, mcp_call, tool_error, tool_json


@pytest.fixture
def two(session, imported_docx_book):
    blocks = session.query(Block).filter_by(book_id=imported_docx_book.id).order_by(Block.id).all()
    para = next(b for b in blocks if b.text.startswith("Teh"))
    a = add_one(session, para, 0, 3, "Teh", "The")
    b = add_one(session, para, 4, 9, "first", "1st", category="style", severity="low")
    return imported_docx_book, a, b


def _latest(session, sid):
    return session.query(Decision).filter_by(suggestion_id=sid).order_by(Decision.id.desc()).first()


def test_accept_records_mcp_channel_and_actor(client, auth, mcp_auth, session, two):
    book, a, _ = two
    out = tool_json(
        mcp_call(client, mcp_auth, "decide_suggestion", {"suggestion_id": a.id, "action": "accept"})
    )
    assert out["status"] == "accepted" and out["decided_channel"] == "mcp"
    d = _latest(session, a.id)
    assert (d.channel, d.actor) == ("mcp", "janus-test")
    rows = client.get(f"/v1/books/{book.id}/suggestions", headers=auth).json()
    row = next(r for r in rows if r["id"] == a.id)
    assert (row["decided_by"], row["decided_channel"]) == ("janus-test", "mcp")
    log = actions(session)
    assert len(log) == 1 and log[0].summary == "Accepted a suggestion"
    assert log[0].book_id == book.id and log[0].after == {"suggestion_ids": [a.id]}
    assert log[0].chapter_id == out["chapter_id"]


def test_reject_and_undo_summaries(client, mcp_auth, session, two):
    _, a, _ = two
    for action in ("reject", "undo"):
        mcp_call(client, mcp_auth, "decide_suggestion", {"suggestion_id": a.id, "action": action})
    assert [x.summary for x in actions(session)] == ["Rejected a suggestion", "Undid a decision"]


def test_mcp_decision_can_be_undone_over_rest(client, auth, mcp_auth, two):
    _, a, _ = two
    mcp_call(client, mcp_auth, "decide_suggestion", {"suggestion_id": a.id, "action": "accept"})
    r = client.post(f"/v1/suggestions/{a.id}/undo", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "open"


def test_bulk_decide_logs_count(client, mcp_auth, session, two):
    book, a, b = two
    out = tool_json(
        mcp_call(
            client,
            mcp_auth,
            "bulk_decide",
            {"book_id": book.id, "action": "accept", "ids": [a.id, b.id]},
        )
    )
    assert out == {"decided": 2}
    log = actions(session)
    assert [x.summary for x in log] == ["Accepted 2 suggestions"]
    assert log[0].book_id == book.id and log[0].after == {"decided": 2}
    assert _latest(session, a.id).channel == "mcp"


def test_bulk_decide_with_filter(client, mcp_auth, session, two):
    book, _, _ = two
    args = {"book_id": book.id, "action": "reject", "filter": {"category": "style"}}
    assert tool_json(mcp_call(client, mcp_auth, "bulk_decide", args)) == {"decided": 1}
    assert actions(session)[0].summary == "Rejected 1 suggestions"


def test_bulk_decide_rejects_what_rest_rejects(client, mcp_auth, session, two):
    book, _, _ = two
    args = {"book_id": book.id, "action": "accept", "filter": {"category": "bogus"}}
    assert tool_error(mcp_call(client, mcp_auth, "bulk_decide", args)).startswith("invalid: ")
    assert actions(session) == []


def test_edit_suggestion_logs(client, mcp_auth, session, two):
    book, a, _ = two
    args = {"suggestion_id": a.id, "replacement": "THE"}
    out = tool_json(mcp_call(client, mcp_auth, "edit_suggestion", args))
    assert out["final_text"] == "THE"
    log = actions(session)
    assert [x.summary for x in log] == ["Edited a suggestion"]
    assert log[0].book_id == book.id and log[0].after == {"suggestion_ids": [a.id]}
    assert _latest(session, a.id).channel == "mcp"


def test_decide_conflict_maps_to_conflict(client, mcp_auth, session, two):
    _, a, _ = two
    args = {"suggestion_id": a.id, "action": "accept"}
    tool_json(mcp_call(client, mcp_auth, "decide_suggestion", args))
    session.query(AgentAction).delete()
    result = mcp_call(client, mcp_auth, "decide_suggestion", args)
    assert tool_error(result).startswith("conflict: ")
    assert actions(session) == []


def test_decide_unknown_suggestion_is_not_found(client, mcp_auth, session):
    args = {"suggestion_id": 999999, "action": "accept"}
    result = mcp_call(client, mcp_auth, "decide_suggestion", args)
    assert tool_error(result).startswith("not_found: ")
    assert actions(session) == []
