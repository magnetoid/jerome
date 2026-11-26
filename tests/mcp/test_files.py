from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest

from jerome.core import exports
from jerome.core.users import create_user
from jerome.orm import Decision, TransferLink
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.word_sim import simulate_word_review
from tests.mcp.conftest import actions, mcp_call, mcp_post, tool_error, tool_json

MIB = 1024 * 1024
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PASSWORD = "correct-horse-1"


def _request_upload(client, mcp_auth) -> dict:
    return tool_json(mcp_call(client, mcp_auth, "request_upload", {}))


def _path(url: str) -> str:
    return urlsplit(url).path


def _transfer_files(test_settings) -> list:
    folder = test_settings.data_dir / "transfers"
    return sorted(folder.iterdir()) if folder.exists() else []


def _upload(client, mcp_auth, data: bytes) -> dict:
    up = _request_upload(client, mcp_auth)
    assert client.put(_path(up["upload_url"]), content=data).status_code == 204
    return up


def test_request_upload_returns_an_absolute_link(client, mcp_auth, session, test_settings):
    up = _request_upload(client, mcp_auth)
    assert set(up) == {"upload_id", "upload_url", "expires_at", "max_bytes"}
    assert up["upload_url"].startswith("http://testserver/mcp/files/")
    token = up["upload_url"].rsplit("/", 1)[1]
    assert len(token) >= 43
    assert up["max_bytes"] == test_settings.max_upload_mb * MIB
    link = session.get(TransferLink, up["upload_id"])
    assert datetime.fromisoformat(up["expires_at"]) == link.expires_at
    assert (link.kind, link.actor) == ("upload", "janus-test")
    assert actions(session) == []  # a link changes nothing in a book: not logged


def test_links_use_public_base_url_when_set(client, mcp_auth, test_settings):
    test_settings.public_base_url = "https://jerome.example.com/"
    up = _request_upload(client, mcp_auth)
    assert up["upload_url"].startswith("https://jerome.example.com/mcp/files/")


def test_upload_then_import_book(client, auth, mcp_auth, session):
    data = make_docx(["# One", "Teh first paragraph."])
    up = _upload(client, mcp_auth, data)
    args = {
        "upload_id": up["upload_id"],
        "filename": "story.docx",
        "variant": "en-GB",
        "title": "A Story",
    }
    book = tool_json(mcp_call(client, mcp_auth, "import_book", args))
    assert (book["title"], book["format"], book["original_filename"]) == (
        "A Story",
        "docx",
        "story.docx",
    )
    assert client.get(f"/v1/books/{book['id']}", headers=auth).json() == book
    listed = tool_json(mcp_call(client, mcp_auth, "list_books", {}))
    assert [b["id"] for b in listed] == [book["id"]]
    (row,) = actions(session)
    assert (row.tool, row.actor, row.channel, row.summary, row.book_id) == (
        "import_book",
        "janus-test",
        "mcp",
        'Imported "A Story"',
        book["id"],
    )

    again = mcp_call(client, mcp_auth, "import_book", args)  # the upload is used up
    assert tool_error(again).startswith("not_found: ")
    assert len(actions(session)) == 1


def test_import_book_matches_rest_upload(client, auth, mcp_auth):
    data = make_docx(["# One", "x"])
    rest = client.post(
        "/v1/books",
        headers=auth,
        files={"file": ("m.docx", data)},
        data={"variant": "en-US", "local_only": "true", "auto_apply": "false"},
    ).json()
    up = _upload(client, mcp_auth, data)
    args = {
        "upload_id": up["upload_id"],
        "filename": "m.docx",
        "variant": "en-US",
        "local_only": True,
        "auto_apply": False,
    }
    book = tool_json(mcp_call(client, mcp_auth, "import_book", args))
    same = {k: v for k, v in book.items() if k not in {"id", "created_at"}}
    assert same == {k: v for k, v in rest.items() if k not in {"id", "created_at"}}
    rest_policy = client.get(f"/v1/books/{rest['id']}/auto-apply", headers=auth).json()
    assert client.get(f"/v1/books/{book['id']}/auto-apply", headers=auth).json() == rest_policy


def test_upload_of_another_key_cannot_be_imported(client, mcp_auth, session):
    from jerome.core.keys import create_api_key

    other = {"Authorization": f"Bearer {create_api_key(session, 'janus-other')}"}
    up = _upload(client, mcp_auth, make_docx(["x"]))
    args = {"upload_id": up["upload_id"], "filename": "m.docx", "variant": "en-US"}
    assert tool_error(mcp_call(client, other, "import_book", args)).startswith("not_found: ")
    assert tool_json(mcp_call(client, mcp_auth, "import_book", args))["format"] == "docx"


def test_upload_too_large_leaves_nothing(client, mcp_auth, session, test_settings):
    test_settings.max_upload_mb = 1
    up = _request_upload(client, mcp_auth)
    assert up["max_bytes"] == MIB
    r = client.put(_path(up["upload_url"]), content=b"x" * (MIB + 1))
    assert r.status_code == 413
    assert _transfer_files(test_settings) == []
    link = session.get(TransferLink, up["upload_id"])
    assert link.received_at is None and link.size is None

    def chunks():  # no Content-Length: refused while streaming, still nothing written
        for _ in range(3):
            yield b"x" * (MIB // 2)

    r = client.put(_path(up["upload_url"]), content=chunks())
    assert r.status_code == 413
    assert _transfer_files(test_settings) == []
    assert client.put(_path(up["upload_url"]), content=b"x" * MIB).status_code == 204


def test_upload_link_single_use_and_404(client, mcp_auth, test_settings):
    up = _upload(client, mcp_auth, b"first")
    second = client.put(_path(up["upload_url"]), content=b"second")
    assert second.status_code == 404
    assert client.put("/mcp/files/not-a-real-token", content=b"x").status_code == 404
    assert client.get(_path(up["upload_url"])).status_code == 404  # an upload link is no download
    (stored,) = _transfer_files(test_settings)
    assert stored.read_bytes() == b"first"


def _freeze_export_time(monkeypatch):
    frozen = datetime(2026, 11, 22, 9, 0, tzinfo=UTC)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(exports, "datetime", Frozen)


def test_export_book_download_single_use(
    client, auth, mcp_auth, session, docx_book_with_decisions, monkeypatch
):
    _freeze_export_time(monkeypatch)
    book_id = docx_book_with_decisions.id
    rest = client.get(f"/v1/books/{book_id}/export?format=docx-tracked", headers=auth)
    assert rest.status_code == 200

    args = {"book_id": book_id, "format": "docx-tracked"}
    out = tool_json(mcp_call(client, mcp_auth, "export_book", args))
    assert set(out) == {"download_url", "filename", "media_type", "size", "expires_at"}
    assert out["download_url"].startswith("http://testserver/mcp/files/")
    assert (out["filename"], out["media_type"], out["size"]) == (
        "draft-docx-tracked.docx",
        DOCX,
        len(rest.content),
    )

    got = client.get(_path(out["download_url"]))
    assert got.status_code == 200
    assert got.content == rest.content
    assert got.headers["content-type"] == rest.headers["content-type"]
    assert got.headers["content-disposition"] == rest.headers["content-disposition"]
    assert got.headers["cache-control"] == "no-store"
    assert client.get(_path(out["download_url"])).status_code == 404
    assert client.put(_path(out["download_url"]), content=b"x").status_code == 404
    assert actions(session) == []  # an export changes nothing in the book


def test_export_book_refuses_what_rest_refuses(client, auth, mcp_auth, epub_book_id):
    url = f"/v1/books/{epub_book_id}/export?format=docx-clean"
    rest = client.get(url, headers=auth)
    assert rest.status_code == 422
    args = {"book_id": epub_book_id, "format": "docx-clean"}
    result = mcp_call(client, mcp_auth, "export_book", args)
    assert tool_error(result) == f"invalid: {rest.json()['detail']}"
    bad = mcp_call(client, mcp_auth, "export_book", {"book_id": epub_book_id, "format": "pdf"})
    assert tool_error(bad).startswith("invalid: ")
    missing = mcp_call(client, mcp_auth, "export_book", {"book_id": 999999, "format": "docx-clean"})
    assert tool_error(missing).startswith("not_found: ")


def test_files_routes_need_no_bearer_and_ignore_cookie(web_client, mcp_auth, session):
    create_user(session, "ana@x.org", "Ana", PASSWORD)
    r = web_client.post("/v1/auth/login", json={"email": "ana@x.org", "password": PASSWORD})
    assert r.status_code == 200

    up = _request_upload(web_client, mcp_auth)
    path = _path(up["upload_url"])
    # No Authorization (only the session cookie, which is not looked at), and a bogus
    # Bearer value is not looked at either: the token in the link is the credential.
    assert web_client.put(path, content=b"abc").status_code == 204
    up = _request_upload(web_client, mcp_auth)
    r = web_client.put(
        _path(up["upload_url"]), content=b"abc", headers={"Authorization": "Bearer x"}
    )
    assert r.status_code == 204

    assert (
        mcp_post(web_client, {}, "tools/list").status_code == 401
    )  # /mcp itself still needs a key
    assert mcp_post(web_client, {"X-Jerome-Client": "web"}, "tools/list").status_code == 401


def test_other_methods_never_use_a_link_or_reach_the_mcp_app(
    client, mcp_auth, docx_book_with_decisions
):
    args = {"book_id": docx_book_with_decisions.id, "format": "docx-clean"}
    path = _path(tool_json(mcp_call(client, mcp_auth, "export_book", args))["download_url"])
    assert client.head(path).status_code == 404  # a HEAD (link preview) does not spend it
    assert client.post(path, content=b"x").json() == {"detail": "Not Found"}
    assert client.get(path + "/more").status_code == 404
    assert client.get(path).status_code == 200


def test_import_review_copy_round_trip(client, auth, mcp_auth, session, docx_book_with_decisions):
    book_id = docx_book_with_decisions.id
    out = tool_json(
        mcp_call(client, mcp_auth, "export_book", {"book_id": book_id, "format": "docx-review"})
    )
    copy = client.get(_path(out["download_url"])).content
    assert copy[:2] == b"PK" and len(copy) == out["size"]
    up = _upload(client, mcp_auth, simulate_word_review(copy, accept=["the"]))

    args = {"book_id": book_id, "upload_id": up["upload_id"]}
    report = tool_json(mcp_call(client, mcp_auth, "import_review_copy", args))
    assert set(report) == {
        "review_import_id",
        "counts",
        "unmatched_block_ids",
        "conflicts",
        "notes",
    }
    rest = client.get(
        f"/v1/books/{book_id}/review-imports/{report['review_import_id']}", headers=auth
    ).json()
    assert report == rest
    got = mcp_call(
        client,
        mcp_auth,
        "get_review_import",
        {"book_id": book_id, "import_id": report["review_import_id"]},
    )
    assert tool_json(got) == rest

    decisions = session.query(Decision).filter_by(review_import_id=report["review_import_id"]).all()
    assert decisions
    assert {(d.channel, d.actor) for d in decisions} == {("word", "janus-test")}
    (row,) = actions(session)
    assert (row.tool, row.summary, row.book_id) == (
        "import_review_copy",
        "Imported a review copy",
        book_id,
    )

    again = mcp_call(client, mcp_auth, "import_review_copy", args)
    assert tool_error(again).startswith("not_found: ")


def test_get_review_import_unknown_is_not_found(client, auth, mcp_auth, book_id):
    rest = client.get(f"/v1/books/{book_id}/review-imports/999999", headers=auth)
    assert rest.status_code == 404
    got = mcp_call(client, mcp_auth, "get_review_import", {"book_id": book_id, "import_id": 999999})
    assert tool_error(got) == f"not_found: {rest.json()['detail']}"


@pytest.mark.parametrize("tool", ["request_upload", "import_book", "import_review_copy"])
def test_file_tools_that_write_are_not_read_only(client, mcp_auth, tool):
    listed = mcp_post(client, mcp_auth, "tools/list").json()["result"]["tools"]
    (spec,) = [t for t in listed if t["name"] == tool]
    assert spec["annotations"]["readOnlyHint"] is False


def test_links_with_real_transactions(session_factory, test_settings):
    """Without the test session override: each request commits on its own, so the PUT's
    write is seen by the next tool call, and a used link stays used."""
    from fastapi.testclient import TestClient

    from jerome.api.app import create_app
    from jerome.core.keys import create_api_key

    with session_factory.begin() as s:
        key = {"Authorization": f"Bearer {create_api_key(s, 'janus-test')}"}
    with TestClient(create_app(test_settings)) as c:
        up = _upload(c, key, make_docx(["# One", "Hello."]))
        assert c.put(_path(up["upload_url"]), content=b"again").status_code == 404
        args = {"upload_id": up["upload_id"], "filename": "m.docx", "variant": "en-US"}
        book = tool_json(mcp_call(c, key, "import_book", args))
        assert tool_error(mcp_call(c, key, "import_book", args)).startswith("not_found: ")
        out = tool_json(
            mcp_call(c, key, "export_book", {"book_id": book["id"], "format": "docx-clean"})
        )
        assert c.get(_path(out["download_url"])).status_code == 200
        assert c.get(_path(out["download_url"])).status_code == 404
    assert _transfer_files(test_settings) == []


def test_failed_import_book_leaves_the_upload_for_a_retry(client, mcp_auth, session, test_settings):
    up = _upload(client, mcp_auth, make_docx(["# One", "Hello."]))
    bad = {"upload_id": up["upload_id"], "filename": "book.txt", "variant": "en-US"}
    assert tool_error(mcp_call(client, mcp_auth, "import_book", bad)).startswith("invalid: ")
    assert len(_transfer_files(test_settings)) == 1
    assert actions(session) == []

    good = {**bad, "filename": "book.docx"}
    book = tool_json(mcp_call(client, mcp_auth, "import_book", good))
    assert book["original_filename"] == "book.docx"
    assert _transfer_files(test_settings) == []  # used: the file is gone
    assert [a.summary for a in actions(session)] == [f'Imported "{book["title"]}"']
    again = tool_error(mcp_call(client, mcp_auth, "import_book", good))
    assert (
        again == f"not_found: upload {up['upload_id']} was already used; call request_upload again"
    )


def test_failed_review_import_leaves_the_upload_for_a_retry(
    client, mcp_auth, session, test_settings, docx_book_with_decisions
):
    book_id = docx_book_with_decisions.id
    junk = _upload(client, mcp_auth, b"not a docx at all")
    args = {"book_id": book_id, "upload_id": junk["upload_id"]}
    assert tool_error(mcp_call(client, mcp_auth, "import_review_copy", args)).startswith(
        "invalid: "
    )
    assert tool_error(mcp_call(client, mcp_auth, "import_review_copy", args)).startswith(
        "invalid: "
    )
    assert actions(session) == []

    out = tool_json(
        mcp_call(client, mcp_auth, "export_book", {"book_id": book_id, "format": "docx-review"})
    )
    copy = client.get(_path(out["download_url"])).content
    up = _upload(client, mcp_auth, simulate_word_review(copy, accept=["the"]))
    args = {"book_id": book_id, "upload_id": up["upload_id"]}
    wrong_book = mcp_call(client, mcp_auth, "import_review_copy", {**args, "book_id": 999999})
    assert tool_error(wrong_book).startswith("not_found: ")
    report = tool_json(mcp_call(client, mcp_auth, "import_review_copy", args))
    assert report["review_import_id"]
    assert [a.summary for a in actions(session)] == ["Imported a review copy"]
    left = {f.name for f in _transfer_files(test_settings)}
    assert str(up["upload_id"]) not in left and str(junk["upload_id"]) in left


def test_mcp_refuses_pdf(client, mcp_auth, session):
    up = _upload(client, mcp_auth, b"%PDF-1.4 x")
    out = mcp_call(
        client,
        mcp_auth,
        "import_book",
        {"upload_id": up["upload_id"], "filename": "b.pdf", "variant": "en-US"},
    )
    assert "PDF import is not available through Janus yet" in tool_error(out)
    assert "upload it on the New project page" in tool_error(out)
    assert session.get(TransferLink, up["upload_id"]) is not None  # the upload is not spent
