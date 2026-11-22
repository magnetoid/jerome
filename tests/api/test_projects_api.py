from tests.core.test_projects import _book


def test_projects_route(client, auth, session, book_factory):
    _book(session, book_factory, "A")
    assert client.get("/v1/projects").status_code == 401
    r = client.get("/v1/projects", headers=auth)
    assert r.status_code == 200
    (p,) = r.json()
    assert p["title"] == "A" and p["stage"] == "set_up" and p["active_job"] is None


def test_chapter_summary_route(client, auth, session, book_factory):
    book = _book(session, book_factory, "A", chapters=2)
    r = client.get(f"/v1/books/{book.id}/chapter-summary", headers=auth)
    assert [c["ordinal"] for c in r.json()] == [0, 1]
    assert client.get("/v1/books/999999/chapter-summary", headers=auth).status_code == 404
    assert client.get(f"/v1/books/{book.id}/chapter-summary").status_code == 401


def test_chapter_review_route(client, auth, session, book_factory):
    from jerome.orm import Chapter

    book = _book(session, book_factory, "A", blocks=3)
    ch = session.query(Chapter).filter_by(book_id=book.id).one()
    url = f"/v1/chapters/{ch.id}/review"
    r = client.get(url, headers=auth, params={"offset": 1, "limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["total_blocks"] == 3 and [b["ordinal"] for b in body["blocks"]] == [1]
    assert client.get("/v1/chapters/999999/review", headers=auth).status_code == 404
    for bad in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
        assert client.get(url, headers=auth, params=bad).status_code == 422
    assert client.get(url, headers=auth, params={"limit": 200}).status_code == 200
    assert client.get(url).status_code == 401
