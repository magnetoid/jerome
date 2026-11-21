from jerome.orm import Block, Suggestion
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.suggestions import add_one

KEYS = {
    "id", "book_id", "block_id", "start", "end", "original", "replacement", "category",
    "severity", "source", "explanation", "status", "final_text", "job_id", "model", "created_at",
}  # fmt: skip


def test_accept_route(client, auth, session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    r = client.post(f"/v1/suggestions/{s.id}/accept", headers=auth)
    assert r.status_code == 200
    assert set(r.json()) == KEYS and r.json()["status"] == "accepted"
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
