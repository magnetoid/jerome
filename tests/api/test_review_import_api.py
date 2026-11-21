from jerome.core.exports import export_book
from jerome.orm import Decision
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.word_sim import simulate_word_review

MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _upload(client, auth, book_id, data):
    return client.post(
        f"/v1/books/{book_id}/review-imports", headers=auth, files={"file": ("r.docx", data, MIME)}
    )


def test_post_then_get_report(client, auth, session, tmp_storage, docx_book_with_decisions):
    book = docx_book_with_decisions
    copy = export_book(session, tmp_storage, book.id, "docx-review", actor="a").data
    r = _upload(client, auth, book.id, simulate_word_review(copy, reject=["the"]))
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"review_import_id", "counts", "unmatched_block_ids", "conflicts", "notes"}
    url = f"/v1/books/{book.id}/review-imports/{body['review_import_id']}"
    got = client.get(url, headers=auth)
    assert got.status_code == 200 and got.json() == body


def test_get_unknown_or_other_book_404(
    client, auth, session, tmp_storage, docx_book_with_decisions
):
    book = docx_book_with_decisions
    copy = export_book(session, tmp_storage, book.id, "docx-review", actor="a").data
    rid = _upload(client, auth, book.id, copy).json()["review_import_id"]
    assert client.get(f"/v1/books/{book.id}/review-imports/999999", headers=auth).status_code == 404
    other = client.get(f"/v1/books/999999/review-imports/{rid}", headers=auth)
    assert other.status_code == 404


def test_upload_without_property_422(client, auth, book_id):
    assert _upload(client, auth, book_id, make_docx(["teh cat"])).status_code == 422


def test_upload_for_other_book_422(
    client, auth, session, tmp_storage, docx_book_with_decisions, book_id
):
    copy = export_book(session, tmp_storage, docx_book_with_decisions.id, "docx-review").data
    r = _upload(client, auth, book_id, copy)
    assert r.status_code == 422
    assert session.query(Decision).filter_by(channel="word").count() == 0


def test_requires_auth(client, book_id):
    r = client.post(f"/v1/books/{book_id}/review-imports", files={"file": ("r.docx", b"x", MIME)})
    assert r.status_code == 401
