import pytest

from jerome.api.deps import get_registry
from jerome.orm import Chapter
from tests.mcp.conftest import actions, mcp_call, mcp_list_tools, tool_error, tool_json


@pytest.fixture(autouse=True)
def _registry_override(client, registry):
    client.app.dependency_overrides[get_registry] = lambda: registry


def _rest_job(client, auth, book_id, **extra):
    r = client.post(
        f"/v1/books/{book_id}/jobs",
        json={"type": "proofread", "max_cost": 5, **extra},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_estimate_job_matches_rest(client, auth, mcp_auth, book_3_chapters):
    bid = book_3_chapters.id
    rest = client.post(f"/v1/books/{bid}/estimate", json={}, headers=auth).json()
    assert tool_json(mcp_call(client, mcp_auth, "estimate_job", {"book_id": bid})) == rest
    body = {"passes": ["ai"], "chapters": [book_3_chapters.chapters[0].id]}
    rest = client.post(f"/v1/books/{bid}/estimate", json=body, headers=auth).json()
    result = mcp_call(client, mcp_auth, "estimate_job", {"book_id": bid, **body})
    assert tool_json(result) == rest


def test_list_and_get_job_match_rest(client, auth, mcp_auth, imported_docx_book):
    bid = imported_docx_book.id
    job = _rest_job(client, auth, bid)
    rest_list = client.get(f"/v1/books/{bid}/jobs", headers=auth).json()
    assert tool_json(mcp_call(client, mcp_auth, "list_jobs", {"book_id": bid})) == rest_list
    rest_job = client.get(f"/v1/jobs/{job['id']}", headers=auth).json()
    assert tool_json(mcp_call(client, mcp_auth, "get_job", {"job_id": job["id"]})) == rest_job


def test_start_cancel_resume_job_log_job_id(client, auth, mcp_auth, session, imported_docx_book):
    bid = imported_docx_book.id
    started = tool_json(
        mcp_call(client, mcp_auth, "start_job", {"book_id": bid, "max_cost": "2.5"})
    )
    assert started["status"] == "queued" and started["max_cost"] == "2.5000"
    assert started == client.get(f"/v1/jobs/{started['id']}", headers=auth).json()
    jid = started["id"]
    cancelled = tool_json(mcp_call(client, mcp_auth, "cancel_job", {"job_id": jid}))
    assert cancelled["status"] == "cancelled"
    resumed = tool_json(mcp_call(client, mcp_auth, "resume_job", {"job_id": jid, "max_cost": 4}))
    assert resumed["status"] == "queued" and resumed["max_cost"] == "4.0000"
    rows = actions(session)
    assert [(r.tool, r.summary) for r in rows] == [
        ("start_job", f"Started job {jid}"),
        ("cancel_job", f"Cancelled job {jid}"),
        ("resume_job", f"Resumed job {jid}"),
    ]
    assert {(r.actor, r.channel, r.job_id, r.book_id) for r in rows} == {
        ("janus-test", "mcp", jid, bid)
    }


def test_start_job_records_the_key_as_creator(client, mcp_auth, session, imported_docx_book):
    from jerome.orm import Job

    started = tool_json(
        mcp_call(client, mcp_auth, "start_job", {"book_id": imported_docx_book.id, "max_cost": 1})
    )
    assert session.get(Job, started["id"]).created_by == "janus-test"


def test_start_job_rejects_what_rest_rejects(
    client, auth, mcp_auth, session, imported_docx_book, book_3_chapters
):
    bid = imported_docx_book.id
    url = f"/v1/books/{bid}/jobs"
    base = {"type": "proofread", "max_cost": 1}

    # an unknown pass
    assert client.post(url, json={**base, "passes": ["bogus"]}, headers=auth).status_code == 422
    result = mcp_call(
        client, mcp_auth, "start_job", {"book_id": bid, "max_cost": 1, "passes": ["bogus"]}
    )
    assert tool_error(result).startswith("invalid: ")

    # a chapter of another book
    other = session.query(Chapter).filter_by(book_id=book_3_chapters.id).first().id
    rest = client.post(url, json={**base, "chapters": [other]}, headers=auth)
    assert rest.status_code == 404
    result = mcp_call(
        client, mcp_auth, "start_job", {"book_id": bid, "max_cost": 1, "chapters": [other]}
    )
    assert tool_error(result) == f"not_found: {rest.json()['detail']}"

    # a negative max_cost
    assert client.post(url, json={**base, "max_cost": -1}, headers=auth).status_code == 422
    result = mcp_call(client, mcp_auth, "start_job", {"book_id": bid, "max_cost": -1})
    assert tool_error(result).startswith("invalid: ")

    # a callback_url that is not http(s)
    bad_cb = {"callback_url": "ftp://x.org/hook"}
    assert client.post(url, json={**base, **bad_cb}, headers=auth).status_code == 422
    result = mcp_call(client, mcp_auth, "start_job", {"book_id": bid, "max_cost": 1, **bad_cb})
    assert tool_error(result).startswith("invalid: ")

    assert actions(session) == []
    assert client.get(f"/v1/books/{bid}/jobs", headers=auth).json() == []


def test_start_job_conflict_when_active(client, auth, mcp_auth, session, imported_docx_book):
    bid = imported_docx_book.id
    _rest_job(client, auth, bid)
    result = mcp_call(client, mcp_auth, "start_job", {"book_id": bid, "max_cost": 1})
    assert tool_error(result).startswith("conflict: ")
    assert actions(session) == []


def test_cancel_finished_job_is_conflict(client, auth, mcp_auth, session, imported_docx_book):
    job = _rest_job(client, auth, imported_docx_book.id)
    client.post(f"/v1/jobs/{job['id']}/cancel", headers=auth)
    rest = client.post(f"/v1/jobs/{job['id']}/cancel", headers=auth)
    assert rest.status_code == 409
    result = mcp_call(client, mcp_auth, "cancel_job", {"job_id": job["id"]})
    assert tool_error(result) == f"conflict: {rest.json()['detail']}"
    assert actions(session) == []


def test_start_job_description_warns_about_cost(client, mcp_auth):
    from tests.mcp.conftest import mcp_post

    tools = mcp_post(client, mcp_auth, "tools/list").json()["result"]["tools"]
    start = next(t for t in tools if t["name"] == "start_job")
    assert "paid" in start["description"] and "estimate_job" in start["description"]
    assert "start_job" in mcp_list_tools(client, mcp_auth)
