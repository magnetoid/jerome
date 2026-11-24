from datetime import UTC, datetime
from urllib.parse import urlsplit

from jerome.core.publication import update_publication
from jerome.orm import Job
from tests.mcp.conftest import actions, mcp_call, tool_error, tool_json


def test_mcp_publish_and_finished_file(client, auth, mcp_auth, session, tmp_storage, book_id):
    update_publication(session, book_id, {"authors": ["Ann Author"]})
    job = tool_json(
        mcp_call(client, mcp_auth, "publish_book", {"book_id": book_id, "format": "epub-book"})
    )
    assert job == client.get(f"/v1/jobs/{job['id']}", headers=auth).json()
    assert job["type"] == "publish" and job["passes"] == ["epub-book"]
    assert [a.tool for a in actions(session)][-1] == "publish_book"

    early = mcp_call(client, mcp_auth, "finished_file", {"job_id": job["id"]})
    assert "the job has not finished" in tool_error(early)

    # as the worker leaves it
    row = session.get(Job, job["id"])
    row.status, row.finished_at = "completed", datetime.now(UTC)
    row.stats = {"file": f"manuscript-{row.id}.epub", "warnings": []}
    tmp_storage.save_export(book_id, row.stats["file"], b"PK finished")
    session.flush()

    got = tool_json(mcp_call(client, mcp_auth, "finished_file", {"job_id": job["id"]}))
    assert got["filename"] == f"manuscript-{row.id}.epub"
    assert got["media_type"] == "application/epub+zip" and got["size"] == len(b"PK finished")
    assert set(got) == {"download_url", "filename", "media_type", "size", "expires_at"}
    served = client.get(urlsplit(got["download_url"]).path)
    assert served.status_code == 200 and served.content == b"PK finished"


def test_mcp_publish_refuses_a_book_without_author(client, mcp_auth, session, book_id):
    update_publication(session, book_id, {"authors": []})
    bad = mcp_call(client, mcp_auth, "publish_book", {"book_id": book_id, "format": "pdf-book"})
    assert "Set the author under Publication first" in tool_error(bad)
