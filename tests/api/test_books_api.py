from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from jerome.api.app import create_app
from jerome.api.deps import get_session
from jerome.core.keys import create_api_key, verify_api_key
from jerome.orm import ApiKey
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
    assert set(body) == {"id", "title", "format", "local_only", "original_filename", "created_at"}
    assert body["format"] == "docx" and body["original_filename"] == "m.docx"
    assert client.get(f"/v1/books/{body['id']}", headers=auth).json()["id"] == body["id"]
    assert [b["id"] for b in client.get("/v1/books", headers=auth).json()] == [body["id"]]
    chapters = client.get(f"/v1/books/{body['id']}/chapters", headers=auth).json()
    assert chapters[0]["title"] == "One"
    assert set(chapters[0]) == {"id", "ordinal", "title", "proofread", "source_ref"}


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
