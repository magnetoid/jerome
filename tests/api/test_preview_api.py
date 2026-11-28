"""The preview route, the original-file download and the book fonts."""

from jerome.core.books import import_book
from jerome.core.translation import import_for_translation, source_of
from tests.fixtures.docx_builders import make_docx

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _chapter_id(client, auth, book_id):
    (chapter, *_) = client.get(f"/v1/books/{book_id}/chapters", headers=auth).json()
    return chapter["id"]


def test_preview_route(client, auth, book_id):
    chapter_id = _chapter_id(client, auth, book_id)
    r = client.get(f"/v1/chapters/{chapter_id}/preview", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["blocks"][0]["proofread"]["text"]
    assert body["work"] == "proofread"
    assert client.get(f"/v1/chapters/{chapter_id}/preview").status_code == 401
    r = client.get(f"/v1/chapters/{chapter_id}/preview?limit=201", headers=auth)
    assert r.status_code == 422


def test_preview_pages_without_gaps(client, auth, session, tmp_storage):
    book = import_book(
        session,
        tmp_storage,
        filename="long.docx",
        data=make_docx([f"Paragraph {i}." for i in range(230)]),
        variant="en-GB",
    )
    chapter_id = _chapter_id(client, auth, book.id)
    url = f"/v1/chapters/{chapter_id}/preview"
    first = client.get(f"{url}?limit=200", headers=auth).json()
    second = client.get(f"{url}?limit=200&offset=200", headers=auth).json()
    assert first["total_blocks"] == 230
    ordinals = [b["ordinal"] for b in first["blocks"] + second["blocks"]]
    assert sorted(ordinals) == sorted(set(ordinals))
    assert len(ordinals) == 230


def test_original_download_docx(client, auth, session, tmp_storage):
    data = make_docx(["One paragraph."])
    book = import_book(session, tmp_storage, filename="m.docx", data=data, variant="en-GB")
    book.title = "Test Book"
    session.flush()
    r = client.get(f"/v1/books/{book.id}/original", headers=auth)
    assert r.status_code == 200, r.text
    assert r.content == data
    assert r.headers["content-type"] == DOCX_TYPE
    assert 'filename="Test Book.docx"' in r.headers["content-disposition"]
    assert client.get(f"/v1/books/{book.id}/original").status_code == 401


def test_original_download_translated(client, auth, session, tmp_storage):
    data = make_docx(["Hello."])
    book = import_for_translation(
        session,
        tmp_storage,
        filename="b.docx",
        data=data,
        source_variant="en-GB",
        target_variant="de-DE",
    )
    r = client.get(f"/v1/books/{book.id}/original", headers=auth)
    assert r.status_code == 200, r.text
    assert r.content == data
    source = source_of(session, book)
    assert client.get(f"/v1/books/{source.id}/original", headers=auth).status_code == 409


def test_original_download_pdf(client, auth, session, tmp_storage, book_id):
    pdf = b"%PDF-1.4\n% the route serves the stored bytes as they are\n%%EOF\n"
    data = make_docx(["Hello."])
    book = import_book(session, tmp_storage, filename="s.docx", data=data, variant="en-GB")
    tmp_storage.save_source_pdf(book.id, pdf)
    r = client.get(f"/v1/books/{book.id}/original?pdf=true", headers=auth)
    assert r.status_code == 200, r.text
    assert r.content == pdf
    assert r.headers["content-type"] == "application/pdf"
    assert ".pdf" in r.headers["content-disposition"]
    assert client.get(f"/v1/books/{book_id}/original?pdf=true", headers=auth).status_code == 404


def test_original_download_name_non_ascii(client, auth, session, tmp_storage):
    book = import_book(
        session, tmp_storage, filename="c.docx", data=make_docx(["Text."]), variant="en-GB"
    )
    book.title = "Čarobnjak / Ž"
    session.flush()
    header = client.get(f"/v1/books/{book.id}/original", headers=auth).headers[
        "content-disposition"
    ]
    fallback = header.split('filename="', 1)[1].split('"', 1)[0]
    assert fallback.isascii() and "/" not in fallback and fallback.endswith(".docx")
    assert "filename*=UTF-8''%C4%8Carobnjak" in header


def test_fonts_are_served(client):
    r = client.get("/fonts/EBGaramond-Regular.ttf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "font/ttf"
    assert r.content[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO")
    assert client.get("/fonts/../orm.py").status_code == 404
    assert client.get("/fonts/..%2F..%2Form.py").status_code == 404
    assert client.get("/fonts/OFL.txt").status_code == 404
    assert client.get("/fonts/Missing-Font.ttf").status_code == 404


def test_original_download_waits_for_the_import(client, auth, session, tmp_storage):
    book = import_book(
        session, tmp_storage, filename="i.docx", data=make_docx(["Text."]), variant="en-GB"
    )
    tmp_storage.save_source_pdf(book.id, b"%PDF-1.4\n%%EOF\n")
    for status in ("importing", "failed"):
        book.import_status = status
        session.flush()
        for url in (f"/v1/books/{book.id}/original", f"/v1/books/{book.id}/original?pdf=true"):
            assert client.get(url, headers=auth).status_code == 409, (status, url)
