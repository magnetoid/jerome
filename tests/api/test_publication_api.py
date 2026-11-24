from jerome.orm import Book
from tests.fixtures.images import GIF, make_png


def test_publication_round_trip(client, auth, book_id):
    url = f"/v1/books/{book_id}/publication"
    got = client.get(url, headers=auth).json()
    assert got["trim_size"] == "6x9in" and got["has_cover"] is False
    body = {"authors": ["Ana Novak"], "isbn_pdf": "978-3-16-148410-0", "year": 2026}
    resp = client.put(url, headers=auth, json=body)
    assert resp.status_code == 200
    assert resp.json()["rights"] == "© 2026 Ana Novak"
    assert client.put(url, headers=auth, json={"isbn_pdf": None}).json()["isbn_pdf"] is None
    assert client.get(url, headers=auth).json()["authors"] == ["Ana Novak"]  # untouched


def test_publication_refusals(client, auth, book_id, session):
    url = f"/v1/books/{book_id}/publication"
    resp = client.put(url, headers=auth, json={"trim_size": "a4"})
    assert resp.status_code == 422 and "trim size" in resp.json()["detail"]
    resp = client.put(url, headers=auth, json={"isbn_epub": "123"})
    assert resp.status_code == 422 and "ISBN-13" in resp.json()["detail"]
    assert client.get("/v1/books/999999/publication", headers=auth).status_code == 404
    session.get(Book, book_id).is_source = True
    session.flush()
    assert client.get(url, headers=auth).status_code == 409


def test_cover_upload_and_refusal(client, auth, book_id):
    url = f"/v1/books/{book_id}/cover"
    png = make_png(1400, 2100)
    resp = client.put(url, headers=auth, files={"file": ("c.png", png, "image/png")})
    assert resp.status_code == 200 and resp.json()["has_cover"] is True
    got = client.get(url, headers=auth)
    assert got.content == png and got.headers["content-type"] == "image/png"
    resp = client.put(url, headers=auth, files={"file": ("c.gif", GIF, "image/gif")})
    assert resp.status_code == 422 and "JPEG or PNG" in resp.json()["detail"]
    assert client.get(url, headers=auth).content == png
    assert client.delete(url, headers=auth).json()["has_cover"] is False
    assert client.get(url, headers=auth).status_code == 404
