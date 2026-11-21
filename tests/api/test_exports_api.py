from urllib.parse import quote

from jerome.core.books import import_book
from jerome.documents.epub_reader import read_epub
from tests.fixtures.docx_builders import all_blocks, make_docx

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_format_mismatch_422(client, auth, epub_book_id):
    r = client.get(f"/v1/books/{epub_book_id}/export?format=docx-clean", headers=auth)
    assert r.status_code == 422


def test_docx_clean_route(client, auth, docx_book_with_decisions):
    r = client.get(
        f"/v1/books/{docx_book_with_decisions.id}/export?format=docx-clean", headers=auth
    )
    assert r.status_code == 200 and r.headers["content-type"] == DOCX
    assert r.headers["content-disposition"] == 'attachment; filename="draft-docx-clean.docx"'
    assert "the cat sat down" in [b.text for b in all_blocks(r.content)]


def test_epub_route_round_trip(client, auth, epub_book_id):
    r = client.get(f"/v1/books/{epub_book_id}/export?format=epub-clean", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"] == "application/epub+zip"
    texts = [b.text for c in read_epub(r.content).chapters for b in c.blocks]
    assert "the cat sat" in texts and "an open suggestion stays teh" in texts


def test_review_501_unknown_422_unknown_book_404_auth(client, auth, docx_book_with_decisions):
    base = f"/v1/books/{docx_book_with_decisions.id}/export"
    assert client.get(f"{base}?format=docx-review", headers=auth).status_code == 501
    assert client.get(f"{base}?format=pdf", headers=auth).status_code == 422
    assert client.get(f"{base}", headers=auth).status_code == 422
    assert client.get("/v1/books/999999/export?format=docx-clean", headers=auth).status_code == 404
    assert client.get(f"{base}?format=docx-clean").status_code == 401


def test_non_ascii_filename_header(client, auth, session, tmp_storage):
    book = import_book(
        session, tmp_storage, filename="Café Ünï.docx", data=make_docx(["x"]), variant="de-DE"
    )
    r = client.get(f"/v1/books/{book.id}/export?format=docx-tracked", headers=auth)
    assert r.status_code == 200
    name = "Café Ünï-docx-tracked.docx"
    assert r.headers["content-disposition"] == (
        'attachment; filename="Caf_ _n_-docx-tracked.docx"; '
        f"filename*=UTF-8''{quote(name, safe='')}"
    )
