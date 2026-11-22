from jerome.core.agent_actions import record_action
from tests.api.test_auth_api import login

KEYS = {
    "id", "at", "actor", "channel", "tool", "book_id", "chapter_id", "job_id",
    "summary", "before", "after",
}  # fmt: skip


def test_agent_actions_requires_auth(client):
    assert client.get("/v1/agent-actions").status_code == 401


def test_agent_actions_lists_with_total_header(client, auth, web_client, session, book_id):
    record_action(session, actor="janus", tool="a", summary="one", book_id=book_id)
    record_action(session, actor="janus", tool="b", summary="two", after={"k": 1})
    r = client.get("/v1/agent-actions", headers=auth, params={"limit": 1})
    assert r.status_code == 200
    assert r.headers["X-Total-Count"] == "2"
    body = r.json()
    assert len(body) == 1 and set(body[0]) == KEYS
    assert body[0]["summary"] == "two" and body[0]["after"] == {"k": 1}
    assert body[0]["channel"] == "mcp"
    r = client.get("/v1/agent-actions", headers=auth, params={"book_id": book_id})
    assert [x["summary"] for x in r.json()] == ["one"]
    assert r.headers["X-Total-Count"] == "1"


def test_agent_actions_works_for_web_session(web_client, session):
    login(web_client, session)
    record_action(session, actor="janus", tool="a", summary="one")
    r = web_client.get("/v1/agent-actions")
    assert r.status_code == 200 and len(r.json()) == 1


def test_agent_actions_limit_bounds(client, auth):
    for limit in (0, 201):
        r = client.get("/v1/agent-actions", headers=auth, params={"limit": limit})
        assert r.status_code == 422
    assert client.get("/v1/agent-actions", headers=auth, params={"offset": -1}).status_code == 422
