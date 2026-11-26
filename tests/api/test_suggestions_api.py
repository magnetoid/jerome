import pytest

from jerome.core.suggestions import decide
from jerome.orm import Block, Suggestion
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.suggestions import add_one

KEYS = {
    "chapter_id", "agreed", "disputed", "alternative", "rule", "auto_applied", "decided_by",
    "decided_channel", "ai_verdict", "ai_replacement", "ai_reason", "ai_model", "options",
    "lt_rule",
    "id", "book_id", "block_id", "start", "end", "original", "replacement", "category",
    "severity", "source", "explanation", "status", "final_text", "job_id", "model", "created_at",
}  # fmt: skip


def test_accept_route(client, auth, session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    r = client.post(f"/v1/suggestions/{s.id}/accept", headers=auth)
    assert r.status_code == 200
    assert set(r.json()) == KEYS and r.json()["status"] == "accepted"
    assert [r.json()[k] for k in ("ai_verdict", "ai_replacement", "ai_reason", "ai_model")] == [
        None
    ] * 4
    assert client.post(f"/v1/suggestions/{s.id}/accept", headers=auth).status_code == 409
    assert client.post(f"/v1/suggestions/{s.id}/undo", headers=auth).json()["status"] == "open"
    assert (
        client.post(f"/v1/suggestions/{s.id}/reject", headers=auth).json()["status"] == "rejected"
    )
    assert client.post("/v1/suggestions/999999/accept", headers=auth).status_code == 404
    assert client.post(f"/v1/suggestions/{s.id}/accept").status_code == 401


def test_edit_route(client, auth, session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    r = client.post(f"/v1/suggestions/{s.id}/edit", headers=auth, json={"replacement": "The"})
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["final_text"]) == ("edited", "The")
    s2 = add_one(session, block, 4, 7, "cat", "cats")
    assert client.post(f"/v1/suggestions/{s2.id}/edit", headers=auth, json={}).status_code == 422


def test_bulk_route(client, auth, session, block):
    add_one(session, block, 0, 3, "teh", "the", severity="low")
    add_one(session, block, 4, 7, "cat", "cats", severity="high")
    r = client.post(
        "/v1/suggestions/bulk",
        headers=auth,
        json={"book_id": block.book_id, "filter": {"min_severity": "high"}, "action": "accept"},
    )
    assert r.status_code == 200 and r.json() == {"decided": 1}
    ids = [s.id for s in session.query(Suggestion).filter_by(status="open")]
    r = client.post(
        "/v1/suggestions/bulk",
        headers=auth,
        json={"book_id": block.book_id, "ids": ids, "action": "reject"},
    )
    assert r.json() == {"decided": 1}
    r = client.post(
        "/v1/suggestions/bulk", headers=auth, json={"book_id": block.book_id, "action": "reject"}
    )
    assert r.status_code == 422


def test_list_route_filters_and_paging(client, auth, session, block):
    add_one(session, block, 0, 3, "teh", "the", severity="low")
    add_one(session, block, 4, 7, "cat", "cats", severity="high")
    url = f"/v1/books/{block.book_id}/suggestions"
    assert [s["start"] for s in client.get(url, headers=auth).json()] == [0, 4]
    r = client.get(url, headers=auth, params={"min_severity": "medium"})
    assert [s["start"] for s in r.json()] == [4]
    r = client.get(url, headers=auth, params={"limit": 1, "offset": 1})
    assert [s["start"] for s in r.json()] == [4]
    assert client.get(url, headers=auth, params={"limit": 501}).status_code == 422
    assert client.get(url, headers=auth, params={"status": "bogus"}).status_code == 422


def test_block_route_returns_original_current_suggestions(client, auth, session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    client.post(f"/v1/suggestions/{s.id}/accept", headers=auth)
    r = client.get(f"/v1/books/{block.book_id}/blocks/{block.id}", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["original"] == "teh cat sat" and body["current"] == "the cat sat"
    assert [x["id"] for x in body["suggestions"]] == [s.id]


def test_block_route_404_for_block_of_other_book(client, auth, session, block, tmp_storage):
    from jerome.core.books import import_book

    other = import_book(
        session, tmp_storage, filename="o.docx", data=make_docx(["other"]), variant="en-US"
    )
    other_block = session.query(Block).filter_by(book_id=other.id).one()
    r = client.get(f"/v1/books/{block.book_id}/blocks/{other_block.id}", headers=auth)
    assert r.status_code == 404


@pytest.mark.parametrize("bad", ["a￼b", "a\x01b", "a￾b"])
def test_edit_with_unwritable_text_is_422_and_leaves_the_suggestion_open(
    client, auth, session, block, bad
):
    s = add_one(session, block, 0, 3, "teh", "the")
    r = client.post(f"/v1/suggestions/{s.id}/edit", headers=auth, json={"replacement": bad})
    assert r.status_code == 422
    assert client.post(f"/v1/suggestions/{s.id}/accept", headers=auth).status_code == 200


def test_edit_with_tab_and_newline_is_fine(client, auth, session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    r = client.post(f"/v1/suggestions/{s.id}/edit", headers=auth, json={"replacement": "a\tb\nc"})
    assert r.status_code == 200


def _total(client, auth, book_id, **params):
    return client.get(f"/v1/books/{book_id}/suggestions", headers=auth, params=params)


def test_suggestion_out_has_new_fields_and_auto_flag(client, auth, session, block):
    plain = add_one(session, block, 0, 3, "teh", "the")
    rich = add_one(
        session,
        block,
        4,
        7,
        "cat",
        "cats",
        agreed=True,
        disputed=True,
        alternative="kitten",
        rule="house_spelling",
    )
    autos = add_one(session, block, 8, 11, "sat", "sits")
    decide(session, autos.id, "accept", actor="ai", channel="auto")
    rows = {s["id"]: s for s in _total(client, auth, block.book_id).json()}
    chapter_id = block.chapter_id
    assert rows[plain.id]["chapter_id"] == chapter_id
    assert (rows[plain.id]["agreed"], rows[plain.id]["disputed"]) == (False, False)
    assert (rows[plain.id]["alternative"], rows[plain.id]["rule"]) == (None, None)
    assert (rows[plain.id]["auto_applied"], rows[plain.id]["decided_by"]) == (False, None)
    assert rows[rich.id]["agreed"] and rows[rich.id]["disputed"]
    assert (rows[rich.id]["alternative"], rows[rich.id]["rule"]) == ("kitten", "house_spelling")
    assert rows[autos.id]["auto_applied"] is True and rows[autos.id]["decided_by"] == "ai"
    r = client.get(f"/v1/books/{block.book_id}/blocks/{block.id}", headers=auth)
    assert {s["id"]: s["auto_applied"] for s in r.json()["suggestions"]}[autos.id] is True


def test_decided_by_is_the_latest_person_or_ai(client, auth, session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    decide(session, s.id, "accept", actor="ai", channel="auto")
    assert (
        client.get(f"/v1/books/{block.book_id}/suggestions", headers=auth).json()[0]["decided_by"]
        == "ai"
    )
    r = client.post(f"/v1/suggestions/{s.id}/reject", headers=auth)  # a person reverses the AI
    assert r.json()["decided_by"] == "tester" and r.json()["auto_applied"] is False
    r = client.post(f"/v1/suggestions/{s.id}/undo", headers=auth)  # undo is a person's decision
    assert (r.json()["status"], r.json()["decided_by"]) == ("open", "tester")
    other = add_one(session, block, 4, 7, "cat", "cats")
    decide(session, other.id, "accept", actor="ai", channel="auto")
    decide(session, other.id, "undo", actor="x", channel="system")  # bookkeeping is ignored
    got = {x["id"]: x["decided_by"] for x in _total(client, auth, block.book_id).json()}
    assert got[other.id] == "ai"


def test_suggestion_out_uses_a_fixed_number_of_queries(session, block):
    from sqlalchemy import event

    from jerome.api.routes_suggestions import suggestion_out

    rows = [add_one(session, block, 0, 1, "t", "T")]
    more = [add_one(session, block, i, i + 1, block.text[i], "z") for i in range(2, 9)]
    for r in more:
        decide(session, r.id, "accept", actor="ai", channel="auto")

    def count(batch):
        n = []

        def hook(*a):
            n.append(1)

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", hook)
        try:
            out = suggestion_out(session, batch)
        finally:
            event.remove(engine, "before_cursor_execute", hook)
        return len(n), out

    one_n, _ = count(rows)
    many = list(reversed(rows + more))
    many_n, out = count(many)
    assert one_n == many_n and many_n <= 4
    assert [o["id"] for o in out] == [s.id for s in many]  # input order kept


def test_list_sets_total_count_header(client, auth, session, block):
    add_one(session, block, 0, 3, "teh", "the", severity="low")
    add_one(session, block, 4, 7, "cat", "cats", severity="high")
    add_one(session, block, 8, 11, "sat", "sit", severity="high")
    r = _total(client, auth, block.book_id, limit=1)
    assert len(r.json()) == 1 and r.headers["X-Total-Count"] == "3"
    r = _total(client, auth, block.book_id, min_severity="high", limit=1, offset=1)
    assert len(r.json()) == 1 and r.headers["X-Total-Count"] == "2"
    assert _total(client, auth, block.book_id, offset=10).headers["X-Total-Count"] == "3"


def test_decided_channel_is_web_for_a_session_and_api_for_a_key(
    web_client, client, auth, session, block
):
    from tests.api.test_auth_api import WEB, login

    login(web_client, session)
    a = add_one(session, block, 0, 3, "teh", "the")
    b = add_one(session, block, 4, 7, "cat", "cats")
    assert web_client.post(f"/v1/suggestions/{a.id}/accept", headers=WEB).status_code == 200
    assert client.post(f"/v1/suggestions/{b.id}/accept", headers=auth).status_code == 200
    url = f"/v1/books/{block.book_id}/suggestions"
    got = {x["id"]: x for x in client.get(url, headers=auth).json()}
    assert (got[a.id]["decided_by"], got[a.id]["decided_channel"]) == ("ana@x.org", "web")
    assert (got[b.id]["decided_by"], got[b.id]["decided_channel"]) == ("tester", "api")
