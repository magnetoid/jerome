"""Remember for all books over REST, and the Settings list of learned words."""

from jerome.core.users import create_user
from jerome.orm import LearnedWord
from tests.api.test_auth_api import PASSWORD, WEB, login
from tests.fixtures.suggestions import insert_raw_open

NAME = {"ai_verdict": "not_an_error", "ai_reason": "A name.", "lt_rule": "SP"}


def test_remember_route(client, auth, session, block):
    s = insert_raw_open(session, block, 0, 3, "Teh", "The", **NAME)
    r = client.post(f"/v1/suggestions/{s.id}/remember", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rejected"] == 1
    word = body["word"]
    assert (word["language"], word["word"], word["rule"], word["reason"]) == (
        "en",
        "Teh",
        "*",
        "A name.",
    )
    assert set(word) == {
        "id", "language", "word", "rule", "reason", "book_id", "created_by", "created_at",
    }  # fmt: skip
    session.refresh(s)
    assert s.status == "rejected"
    again = client.post(f"/v1/suggestions/{s.id}/remember", headers=auth)
    assert again.status_code == 409
    assert client.post("/v1/suggestions/999999/remember", headers=auth).status_code == 404
    assert client.post(f"/v1/suggestions/{s.id}/remember").status_code == 401


def test_remember_refuses_a_mark_not_rated_fine(client, auth, session, block):
    s = insert_raw_open(session, block, 0, 3, "Teh", "The", **{**NAME, "ai_verdict": "maybe_error"})
    r = client.post(f"/v1/suggestions/{s.id}/remember", headers=auth)
    assert r.status_code == 409 and "not_an_error" in r.json()["detail"]
    assert session.query(LearnedWord).count() == 0


def test_super_admin_lists_and_deletes(web_client, session, block):
    login(web_client, session)
    s = insert_raw_open(session, block, 0, 3, "Teh", "The", **NAME)
    assert web_client.post(f"/v1/suggestions/{s.id}/remember", headers=WEB).status_code == 200
    listed = web_client.get("/v1/learned-words").json()
    assert [(w["word"], w["created_by"]) for w in listed] == [("Teh", "ana@x.org")]
    gone = web_client.delete(f"/v1/learned-words/{listed[0]['id']}", headers=WEB)
    assert gone.status_code == 204
    assert web_client.get("/v1/learned-words").json() == []
    assert web_client.delete(f"/v1/learned-words/{listed[0]['id']}", headers=WEB).status_code == 404


def test_learned_words_are_for_the_super_admin(web_client, session, auth):
    assert web_client.get("/v1/learned-words", headers=auth).status_code == 403
    create_user(session, "bo@x.org", "Bo", PASSWORD, role="member")
    web_client.post("/v1/auth/login", json={"email": "bo@x.org", "password": PASSWORD})
    assert web_client.get("/v1/learned-words").status_code == 403
    assert web_client.delete("/v1/learned-words/1", headers=WEB).status_code == 403


def test_remember_returns_every_mark_it_rejected(client, auth, session, block):
    s = insert_raw_open(session, block, 0, 3, "Teh", "The", **NAME)
    other = insert_raw_open(session, block, 0, 3, "Teh", "Tea", **NAME)
    r = client.post(f"/v1/suggestions/{s.id}/remember", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rejected"] == 2
    assert body["marks"] == [
        {"id": s.id, "chapter_id": block.chapter_id},
        {"id": other.id, "chapter_id": block.chapter_id},
    ]
