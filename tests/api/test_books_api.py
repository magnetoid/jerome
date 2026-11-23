from typing import Annotated

import pytest
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from jerome.api.app import create_app
from jerome.api.deps import get_session
from jerome.core.auto_apply import DEFAULT_POLICY, OFF_POLICY, policy_to_json
from jerome.core.keys import create_api_key, verify_api_key
from jerome.orm import ApiKey, Book
from tests.fixtures.docx_builders import make_docx


def test_requires_bearer_key(client):
    r = client.get("/v1/books")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert "detail" in r.json()


def test_invalid_key_401(client):
    r = client.get("/v1/books", headers={"Authorization": "Bearer jk_nope"})
    assert r.status_code == 401


def test_revoked_key_401(client, auth, session):
    from datetime import UTC, datetime

    assert client.get("/v1/books", headers=auth).status_code == 200
    session.execute(
        select(ApiKey).where(ApiKey.name == "tester")
    ).scalar_one().revoked_at = datetime.now(UTC)
    session.flush()
    assert client.get("/v1/books", headers=auth).status_code == 401


def test_create_api_key_stores_only_hash(session):
    token = create_api_key(session, "hashcheck")
    assert token.startswith("jk_") and len(token) == 35
    row = session.scalar(select(ApiKey).where(ApiKey.name == "hashcheck"))
    assert row.key_hash != token and token not in row.key_hash
    assert len(row.key_hash) == 64
    assert verify_api_key(session, token) == "hashcheck"
    assert verify_api_key(session, token + "x") is None


def test_upload_and_get_book(client, auth):
    r = client.post(
        "/v1/books",
        headers=auth,
        files={"file": ("m.docx", make_docx(["# One", "x"]))},
        data={"variant": "en-US"},
    )
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {
        "id", "title", "format", "local_only", "original_filename", "created_at",
        "work", "source_book_id", "translated_title",
    }  # fmt: skip
    assert body["format"] == "docx" and body["original_filename"] == "m.docx"
    assert client.get(f"/v1/books/{body['id']}", headers=auth).json()["id"] == body["id"]
    assert [b["id"] for b in client.get("/v1/books", headers=auth).json()] == [body["id"]]
    chapters = client.get(f"/v1/books/{body['id']}/chapters", headers=auth).json()
    assert chapters[0]["title"] == "One"
    assert set(chapters[0]) == {"id", "ordinal", "title", "proofread", "source_ref"}


def test_upload_with_auto_apply_false_stores_off_policy(client, auth, session):
    def upload(**form):
        r = client.post(
            "/v1/books",
            headers=auth,
            files={"file": ("m.docx", make_docx(["# One", "x"]))},
            data={"variant": "en-US", **form},
        )
        assert r.status_code == 201, r.text
        return session.get(Book, r.json()["id"])

    assert upload(auto_apply="false").auto_apply == policy_to_json(OFF_POLICY)
    assert upload(auto_apply="true").auto_apply == policy_to_json(DEFAULT_POLICY)
    assert upload().auto_apply == policy_to_json(DEFAULT_POLICY)  # on unless asked


def test_upload_too_large_413(client, auth, test_settings):
    test_settings.max_upload_mb = 0
    r = client.post(
        "/v1/books",
        headers=auth,
        files={"file": ("m.docx", make_docx(["# One", "x"]))},
        data={"variant": "en-US"},
    )
    assert r.status_code == 413


def test_unsupported_upload_422(client, auth):
    r = client.post(
        "/v1/books",
        headers=auth,
        files={"file": ("m.txt", b"just text")},
        data={"variant": "en-US"},
    )
    assert r.status_code == 422
    assert "DOCX" in r.json()["detail"]


def test_invalid_variant_422(client, auth):
    r = client.post(
        "/v1/books",
        headers=auth,
        files={"file": ("m.docx", make_docx(["# One", "x"]))},
        data={"variant": "fr-FR"},
    )
    assert r.status_code == 422


def test_unknown_book_404(client, auth):
    for path in ("/v1/books/999999", "/v1/books/999999/chapters", "/v1/books/999999/style-sheet"):
        r = client.get(path, headers=auth)
        assert r.status_code == 404 and "detail" in r.json()
    assert client.delete("/v1/books/999999", headers=auth).status_code == 404
    assert (
        client.patch("/v1/chapters/999999", headers=auth, json={"proofread": True}).status_code
        == 404
    )


def test_style_sheet_roundtrip(client, auth, book_id):
    before = client.get(f"/v1/books/{book_id}/style-sheet", headers=auth).json()
    assert before["variant"] == "en-GB"
    r = client.put(
        f"/v1/books/{book_id}/style-sheet", headers=auth, json={"ignore_words": ["Zarathustra"]}
    )
    assert r.status_code == 200
    after = client.get(f"/v1/books/{book_id}/style-sheet", headers=auth).json()
    assert after["ignore_words"] == ["Zarathustra"]
    assert after["variant"] == before["variant"] and after["quote_style"] == before["quote_style"]


def test_style_sheet_invalid_variant_422(client, auth, book_id):
    r = client.put(f"/v1/books/{book_id}/style-sheet", headers=auth, json={"variant": "xx"})
    assert r.status_code == 422


def test_patch_chapter_flag(client, auth, book_id):
    chapter = client.get(f"/v1/books/{book_id}/chapters", headers=auth).json()[0]
    assert chapter["proofread"] is True  # DOCX chapters start flagged
    r = client.patch(f"/v1/chapters/{chapter['id']}", headers=auth, json={"proofread": False})
    assert r.status_code == 200 and r.json()["proofread"] is False
    again = client.get(f"/v1/books/{book_id}/chapters", headers=auth).json()[0]
    assert again["proofread"] is False


def test_delete_book_404_after(client, auth, book_id):
    assert client.delete(f"/v1/books/{book_id}", headers=auth).status_code == 204
    assert client.get(f"/v1/books/{book_id}", headers=auth).status_code == 404


def test_request_error_rolls_back(engine, test_settings):
    """With the real session dependency, an exception undoes the request's writes."""
    app = create_app(test_settings)

    @app.get("/boom")
    def boom(session: Annotated[Session, Depends(get_session)]):
        create_api_key(session, "rollback-probe")
        raise RuntimeError("boom")

    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/boom").status_code == 500
    with engine.connect() as conn:
        n = conn.execute(select(ApiKey.id).where(ApiKey.name == "rollback-probe")).first()
    assert n is None


def _forbid_import(monkeypatch):
    from jerome.api import routes_books

    def fail(*a, **k):
        raise AssertionError("handler reached")

    monkeypatch.setattr(routes_books.books, "import_book", fail)


def test_oversized_content_length_413_without_auth_before_handler(
    client, test_settings, monkeypatch
):
    _forbid_import(monkeypatch)
    test_settings.max_upload_mb = 0
    r = client.post(
        "/v1/books",
        files={"file": ("m.docx", b"x" * (2 * 1024 * 1024))},
        data={"variant": "en-US"},
    )
    assert r.status_code == 413 and "detail" in r.json()


def test_oversized_chunked_body_413_without_content_length(client, test_settings, monkeypatch):
    _forbid_import(monkeypatch)
    test_settings.max_upload_mb = 0

    def chunks():
        yield b'--b\r\nContent-Disposition: form-data; name="file"; filename="m.docx"\r\n\r\n'
        for _ in range(4):
            yield b"x" * (512 * 1024)

    r = client.post(
        "/v1/books", content=chunks(), headers={"content-type": "multipart/form-data; boundary=b"}
    )
    assert r.status_code == 413 and "detail" in r.json()


def test_unexpected_value_error_is_500(engine, test_settings):
    from fastapi.testclient import TestClient

    app = create_app(test_settings)

    @app.get("/bug")
    def bug():
        raise ValueError("internal detail")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/bug")
    assert r.status_code == 500 and "internal detail" not in r.text


def test_style_sheet_variant_change_resets_quotes_unless_given(client, auth, book_id):
    url = f"/v1/books/{book_id}/style-sheet"
    r = client.put(url, headers=auth, json={"variant": "de-DE"})
    assert r.status_code == 200 and r.json()["quote_style"] == "„“‚‘"
    r = client.put(url, headers=auth, json={"variant": "en-US", "quote_style": "«»‹›"})
    assert r.json()["quote_style"] == "«»‹›"
    r = client.put(url, headers=auth, json={"variant": "en-US"})  # same variant: kept
    assert r.json()["quote_style"] == "«»‹›"


@pytest.mark.parametrize("bad", ["", "“”", "“”‘’x"])
def test_style_sheet_quote_style_must_be_four_characters(client, auth, book_id, bad):
    url = f"/v1/books/{book_id}/style-sheet"
    before = client.get(url, headers=auth).json()
    r = client.put(url, headers=auth, json={"quote_style": bad, "variant": "de-DE"})
    assert r.status_code == 422
    assert client.get(url, headers=auth).json() == before


def test_auto_apply_settings_get_put_round_trip(client, auth, book):
    url = f"/v1/books/{book.id}/auto-apply"
    assert client.get(url, headers=auth).json() == policy_to_json(OFF_POLICY)
    body = {
        "enabled": True,
        "categories": ["punctuation", "grammar", "typo"],
        "min_severity": "medium",
        "house_rules": False,
    }
    r = client.put(url, headers=auth, json=body)
    assert r.status_code == 200
    assert r.json() == {**body, "categories": ["grammar", "punctuation", "typo"]}
    assert client.get(url, headers=auth).json() == r.json()
    empty = {**body, "categories": []}
    assert client.put(url, headers=auth, json=empty).json()["categories"] == []
    assert client.put("/v1/books/999999/auto-apply", headers=auth, json=body).status_code == 404
    assert client.get("/v1/books/999999/auto-apply", headers=auth).status_code == 404
    assert client.get(url).status_code == 401


def test_auto_apply_put_rejects_unknown_category_422(client, auth, book):
    url = f"/v1/books/{book.id}/auto-apply"
    good = {"enabled": True, "categories": ["typo"], "min_severity": "high", "house_rules": True}
    assert client.put(url, headers=auth, json={**good, "categories": ["bogus"]}).status_code == 422
    assert client.put(url, headers=auth, json={**good, "min_severity": "huge"}).status_code == 422
    assert client.get(url, headers=auth).json() == policy_to_json(OFF_POLICY)
