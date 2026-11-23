"""Translated books over MCP: import for translation, the translate job, the hidden source
book and the translation tools."""

import pytest

from jerome.api.deps import get_registry
from jerome.models import FakeProvider
from jerome.orm import Block
from tests.fixtures.docx_builders import make_docx
from tests.jobs.test_translate_job import registry_for
from tests.mcp.conftest import actions, mcp_call, mcp_post, tool_error, tool_json

DOCX = make_docx(["First para.", "Second para."])


@pytest.fixture(autouse=True)
def _registry_override(client, test_settings):
    reg = registry_for(test_settings, FakeProvider([], token_count=1000))
    client.app.dependency_overrides[get_registry] = lambda: reg


def _import(client, mcp_auth, **extra) -> dict:
    up = tool_json(mcp_call(client, mcp_auth, "request_upload", {}))
    path = up["upload_url"].removeprefix("http://testserver")
    assert client.put(path, content=DOCX).status_code == 204
    args = {
        "upload_id": up["upload_id"],
        "filename": "b.docx",
        "variant": "en-GB",
        "work": "translate",
        "target_variant": "de-DE",
        **extra,
    }
    return mcp_call(client, mcp_auth, "import_book", args)


def test_import_translate_and_start_translate_job_over_mcp(client, auth, mcp_auth, session):
    book = tool_json(_import(client, mcp_auth, translated_title="Zwei Absätze"))
    assert (book["work"], book["translated_title"]) == ("translate", "Zwei Absätze")
    assert client.get(f"/v1/books/{book['id']}", headers=auth).json() == book
    bid = book["id"]

    est = tool_json(
        mcp_call(client, mcp_auth, "estimate_job", {"book_id": bid, "type": "translate"})
    )
    rest = client.post(f"/v1/books/{bid}/estimate", json={"type": "translate"}, headers=auth)
    assert est == rest.json() and set(est["per_pass"]) == {"translate"}

    job = tool_json(
        mcp_call(
            client, mcp_auth, "start_job", {"book_id": bid, "max_cost": 3, "type": "translate"}
        )
    )
    assert (job["type"], job["status"]) == ("translate", "queued")
    assert job == client.get(f"/v1/jobs/{job['id']}", headers=auth).json()

    state = tool_json(mcp_call(client, mcp_auth, "get_translation", {"book_id": bid}))
    assert state == client.get(f"/v1/books/{bid}/translation", headers=auth).json()
    assert state == {"counts": {"pending": 2}, "flagged": []}

    assert [(a.tool, a.book_id) for a in actions(session)] == [
        ("import_book", bid),
        ("start_job", bid),
    ]

    # a block that is still pending can be neither translated again nor kept
    pending = session.query(Block).filter_by(book_id=bid).order_by(Block.ordinal).first()
    result = mcp_call(client, mcp_auth, "retranslate_block", {"block_id": pending.id})
    assert tool_error(result).startswith("conflict: ")
    result = mcp_call(client, mcp_auth, "keep_source_block", {"block_id": pending.id})
    assert tool_error(result) == f"conflict: block {pending.id} is pending, not flagged"


def test_import_translate_same_language_is_invalid(client, mcp_auth):
    result = _import(client, mcp_auth, target_variant="en-US")
    assert tool_error(result).startswith("invalid: cannot translate en-GB to en-US")


def test_import_translate_without_target_variant_is_invalid(client, mcp_auth):
    result = _import(client, mcp_auth, target_variant=None)
    assert tool_error(result).startswith("invalid: ")


def test_source_book_hidden_from_mcp_list_books(client, auth, mcp_auth):
    book = tool_json(_import(client, mcp_auth))
    listed = tool_json(mcp_call(client, mcp_auth, "list_books", {}))
    assert [b["id"] for b in listed] == [book["id"]]
    projects = tool_json(mcp_call(client, mcp_auth, "list_projects", {}))
    assert [p["id"] for p in projects] == [book["id"]]
    source = tool_json(mcp_call(client, mcp_auth, "get_book", {"book_id": book["source_book_id"]}))
    assert source["id"] == book["source_book_id"]  # readable by id
    result = mcp_call(
        client, mcp_auth, "start_job", {"book_id": book["source_book_id"], "max_cost": 3}
    )
    assert f"work on book {book['id']}" in tool_error(result)


def test_translation_tool_annotations(client, mcp_auth):
    tools = {
        t["name"]: t for t in mcp_post(client, mcp_auth, "tools/list").json()["result"]["tools"]
    }
    assert tools["get_translation"]["annotations"]["readOnlyHint"] is True
    assert tools["retranslate_block"]["annotations"]["readOnlyHint"] is False
    assert tools["retranslate_block"]["annotations"]["idempotentHint"] is False
    assert tools["keep_source_block"]["annotations"]["idempotentHint"] is True
