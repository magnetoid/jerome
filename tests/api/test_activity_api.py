from jerome.core.activity import CHANGED_LATER
from jerome.core.agent_actions import record_action
from jerome.core.suggestions import decide
from jerome.orm import Block, Chapter, Decision, Suggestion
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.suggestions import add_one, insert_raw_open

KEYS = {
    "kind", "id", "at", "actor", "channel", "book_id", "book_title", "chapter_id",
    "chapter_title", "summary", "action", "suggestion_id", "original", "replacement", "tool",
    "job_id", "before", "after", "revertable", "revert_blocked",
}  # fmt: skip


def _items(client, auth, **params):
    r = client.get("/v1/activity", headers=auth, params=params)
    assert r.status_code == 200, r.text
    return r.json(), int(r.headers["X-Total-Count"])


def _decision_id(session, suggestion_id, action):
    return (
        session.query(Decision)
        .filter_by(suggestion_id=suggestion_id, action=action)
        .order_by(Decision.id.desc())
        .first()
        .id
    )


def test_activity_requires_auth(client):
    assert client.get("/v1/activity").status_code == 401
    assert client.post("/v1/activity/decisions/1/revert").status_code == 401


def test_activity_lists_everyone_and_skips_duplicates(client, auth, session, block):
    a = add_one(session, block, 0, 3, "teh", "the")
    b = add_one(session, block, 8, 11, "sat", "sits")
    c = add_one(session, block, 4, 7, "cat", "dog")
    decide(session, a.id, "accept", actor="ana@x.org", channel="web")
    decide(session, b.id, "edit", actor="auto-ai", channel="auto", replacement="sat down")
    decide(session, c.id, "reject", actor="janus", channel="mcp")
    record_action(
        session, actor="janus", tool="decide_suggestion", summary="dup", book_id=block.book_id
    )
    record_action(
        session,
        actor="janus",
        tool="update_style_sheet",
        summary="Changed the style sheet",
        book_id=block.book_id,
    )
    record_action(session, actor="janus", tool="other", summary="elsewhere")

    items, total = _items(client, auth)
    assert total == 5 and all(set(i) == KEYS for i in items)
    summaries = {i["summary"] for i in items}
    assert summaries == {
        "Accepted “teh” → “the”",
        "Edited “sat” → “sat down”",
        "Rejected “cat” → “dog”",
        "Changed the style sheet",
        "elsewhere",
    }
    accept = next(i for i in items if i["action"] == "accept")
    assert (accept["actor"], accept["channel"], accept["kind"]) == ("ana@x.org", "web", "decision")
    assert accept["replacement"] == "the" and accept["original"] == "teh"
    assert accept["book_title"] and accept["chapter_title"] is not None
    assert accept["revertable"] is True and accept["revert_blocked"] is None
    assert next(i for i in items if i["action"] == "reject")["replacement"] is None

    items, total = _items(client, auth, book_id=block.book_id)
    assert total == 4 and "elsewhere" not in {i["summary"] for i in items}
    assert [i["channel"] for i in _items(client, auth, who="people")[0]] == ["web"]
    assert [i["channel"] for i in _items(client, auth, who="automatic")[0]] == ["auto"]
    agent, total = _items(client, auth, who="agent")
    assert total == 3 and {i["kind"] for i in agent} == {"decision", "agent"}
    assert _items(client, auth, who="api") == ([], 0)
    one, total = _items(client, auth, limit=1, offset=1)
    assert len(one) == 1 and total == 5


def test_activity_filters_by_chapter(client, auth, session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    decide(session, s.id, "accept", actor="ana@x.org", channel="web")
    assert _items(client, auth, chapter_id=block.chapter_id)[1] == 1
    assert _items(client, auth, chapter_id=block.chapter_id + 999)[1] == 0


def test_activity_hides_system_decisions(client, auth, session, block):
    a = add_one(session, block, 0, 3, "teh", "the")
    b = insert_raw_open(session, block, 0, 7, "teh cat", "the cat")
    decide(session, a.id, "accept", actor="ana@x.org", channel="web")
    assert session.get(Suggestion, b.id).status == "superseded"
    items, total = _items(client, auth)
    assert total == 1 and items[0]["suggestion_id"] == a.id


def test_revert_undoes_one_correction(client, auth, session, block):
    a = add_one(session, block, 0, 3, "teh", "the")
    b = add_one(session, block, 8, 11, "sat", "sits")
    decide(session, a.id, "accept", actor="ana@x.org", channel="web")
    decide(session, b.id, "accept", actor="ana@x.org", channel="web")
    accepted = _decision_id(session, a.id, "accept")

    r = client.post(f"/v1/activity/decisions/{accepted}/revert", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "open" and r.json()["id"] == a.id
    assert session.get(Suggestion, b.id).status == "accepted"  # only that one correction

    items, _ = _items(client, auth)
    undo = next(i for i in items if i["action"] == "undo")
    assert undo["summary"] == "Undid the decision on “teh” → “the”"
    assert (undo["revertable"], undo["revert_blocked"]) == (False, None)
    old = next(i for i in items if i["id"] == accepted)
    assert (old["revertable"], old["revert_blocked"]) == (False, CHANGED_LATER)

    again = client.post(f"/v1/activity/decisions/{accepted}/revert", headers=auth)
    assert again.status_code == 409
    undo_id = _decision_id(session, a.id, "undo")
    assert client.post(f"/v1/activity/decisions/{undo_id}/revert", headers=auth).status_code == 409


def test_revert_refuses_a_decision_changed_later(client, auth, session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    decide(session, s.id, "reject", actor="ana@x.org", channel="web")
    rejected = _decision_id(session, s.id, "reject")
    decide(session, s.id, "undo", actor="ana@x.org", channel="web")
    decide(session, s.id, "accept", actor="ana@x.org", channel="web")
    r = client.post(f"/v1/activity/decisions/{rejected}/revert", headers=auth)
    assert r.status_code == 409 and r.json()["detail"] == CHANGED_LATER
    assert session.get(Suggestion, s.id).status == "accepted"


def test_revert_brings_back_what_it_displaced(client, auth, session, block):
    a = add_one(session, block, 0, 3, "teh", "the")
    b = insert_raw_open(session, block, 0, 7, "teh cat", "the cat")
    decide(session, a.id, "accept", actor="ana@x.org", channel="web")
    accepted = _decision_id(session, a.id, "accept")
    assert client.post(f"/v1/activity/decisions/{accepted}/revert", headers=auth).status_code == 200
    assert session.get(Suggestion, b.id).status == "open"


def test_revert_an_automatic_change(client, auth, session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    decide(session, s.id, "accept", actor="auto-ai", channel="auto")
    d = _decision_id(session, s.id, "accept")
    r = client.post(f"/v1/activity/decisions/{d}/revert", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "open"
    undo = session.get(Decision, _decision_id(session, s.id, "undo"))
    assert undo.channel == "api"  # the reverter, not the original author


def test_revert_unknown_or_system_decision_is_404(client, auth, session, block):
    assert client.post("/v1/activity/decisions/999999/revert", headers=auth).status_code == 404
    a = add_one(session, block, 0, 3, "teh", "the")
    b = insert_raw_open(session, block, 0, 7, "teh cat", "the cat")
    decide(session, a.id, "accept", actor="ana@x.org", channel="web")
    system = _decision_id(session, b.id, "supersede")
    assert client.post(f"/v1/activity/decisions/{system}/revert", headers=auth).status_code == 404


def test_activity_shows_the_chapter_of_each_book(client, auth, session, tmp_storage):
    from jerome.core.books import import_book

    book = import_book(
        session, tmp_storage, filename="two.docx", data=make_docx(["teh one"]), variant="en-US"
    )
    blk = session.query(Block).filter_by(book_id=book.id).one()
    s = add_one(session, blk, 0, 3, "teh", "the")
    decide(session, s.id, "accept", actor="ana@x.org", channel="web")
    (item,), _ = _items(client, auth, book_id=book.id)
    chapter = session.get(Chapter, blk.chapter_id)
    assert (item["chapter_id"], item["chapter_title"]) == (chapter.id, chapter.title)
    assert item["book_title"] == book.title
