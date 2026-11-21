from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.requests import Request

from jerome.api.app import create_app
from jerome.api.auth import LoginThrottle, client_ip
from jerome.core.users import create_user, reset_password, update_user
from jerome.orm import Decision, Suggestion, User, UserSession
from tests.fixtures.suggestions import add_one

PASSWORD = "correct-horse-1"
WEB = {"X-Jerome-Client": "web"}


def login(client, session, email="ana@x.org", name="Ana"):
    user, _ = create_user(session, email, name, PASSWORD)
    r = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return user


def _decision(session, suggestion_id):
    return session.scalars(select(Decision).where(Decision.suggestion_id == suggestion_id)).one()


def test_login_sets_secure_httponly_cookie(web_client, session, test_settings):
    user, _ = create_user(session, "Ana@x.org", "Ana", PASSWORD)
    r = web_client.post("/v1/auth/login", json={"email": " ANA@x.org", "password": PASSWORD})
    assert r.status_code == 200
    assert r.json() == {
        "id": user.id,
        "email": "ana@x.org",
        "name": "Ana",
        "must_change_password": False,
    }
    cookie = r.headers["set-cookie"]
    for part in ("jerome_session=", "HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert part in cookie
    assert f"Max-Age={30 * 24 * 3600}" in cookie
    assert web_client.get("/v1/auth/me").json() == r.json()

    web_client.app.state.settings = test_settings.model_copy(update={"cookie_secure": False})
    r = web_client.post("/v1/auth/login", json={"email": "ana@x.org", "password": PASSWORD})
    assert "Secure" not in r.headers["set-cookie"]


def test_cookie_auth_reads_books_and_records_web_channel(web_client, session, block):
    login(web_client, session)
    assert web_client.get("/v1/books").status_code == 200  # safe methods need no header

    s = add_one(session, block, 0, 3, "teh", "the")
    r = web_client.post(f"/v1/suggestions/{s.id}/accept", headers=WEB)
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    d = _decision(session, s.id)
    assert (d.channel, d.actor) == ("web", "ana@x.org")

    other = add_one(session, block, 4, 7, "cat", "cats")
    r = web_client.post(
        "/v1/suggestions/bulk",
        headers=WEB,
        json={"book_id": block.book_id, "ids": [other.id], "action": "reject"},
    )
    assert r.json() == {"decided": 1}
    d = _decision(session, other.id)
    assert (d.channel, d.actor) == ("web", "ana@x.org")


def test_cookie_post_without_client_header_is_forbidden(web_client, session, block):
    login(web_client, session)
    s = add_one(session, block, 0, 3, "teh", "the")
    assert web_client.post(f"/v1/suggestions/{s.id}/accept").status_code == 403
    other = {"X-Jerome-Client": "Web"}
    assert web_client.post(f"/v1/suggestions/{s.id}/accept", headers=other).status_code == 403
    assert web_client.post("/v1/auth/logout").status_code == 403
    assert session.get(Suggestion, s.id).status == "open"
    assert web_client.get("/v1/auth/me").status_code == 200  # still logged in


def test_bearer_key_still_works_and_records_api_channel(client, auth, session, block):
    s = add_one(session, block, 0, 3, "teh", "the")
    r = client.post(f"/v1/suggestions/{s.id}/accept", headers=auth)  # no X-Jerome-Client
    assert r.status_code == 200
    d = _decision(session, s.id)
    assert (d.channel, d.actor) == ("api", "tester")
    assert client.get("/v1/auth/me", headers=auth).json() == {
        "id": None,
        "email": None,
        "name": "tester",
        "must_change_password": False,
    }
    body = {"current": "whatever-123", "new": "whatever-456"}
    assert client.post("/v1/auth/password", headers=auth, json=body).status_code == 403
    assert client.post("/v1/auth/logout", headers=auth).status_code == 204


def test_must_change_password_blocks_other_routes_until_changed(web_client, session):
    _, temporary = create_user(session, "ana@x.org", "Ana")
    r = web_client.post("/v1/auth/login", json={"email": "ana@x.org", "password": temporary})
    assert r.status_code == 200 and r.json()["must_change_password"] is True

    r = web_client.get("/v1/books")
    assert r.status_code == 403 and r.json() == {"detail": "password change required"}
    assert web_client.get("/v1/auth/me").json()["must_change_password"] is True

    wrong = {"current": "not-the-password", "new": "a-new-password-1"}
    assert web_client.post("/v1/auth/password", headers=WEB, json=wrong).status_code == 422
    body = {"current": temporary, "new": "a-new-password-1"}
    assert web_client.post("/v1/auth/password", headers=WEB, json=body).status_code == 204

    assert web_client.get("/v1/books").status_code == 200
    assert web_client.get("/v1/auth/me").json()["must_change_password"] is False


def test_wrong_password_is_401_and_sixth_failure_is_429(web_client, session):
    create_user(session, "ana@x.org", "Ana", PASSWORD)
    wrong = {"email": "ana@x.org", "password": "wrong-password"}
    for _ in range(5):
        r = web_client.post("/v1/auth/login", json=wrong)
        assert r.status_code == 401 and r.json() == {"detail": "Wrong email or password"}

    r = web_client.post("/v1/auth/login", json={"email": " ANA@x.org", "password": PASSWORD})
    assert r.status_code == 429
    assert 0 < int(r.headers["retry-after"]) <= 15 * 60
    assert "set-cookie" not in r.headers

    r = web_client.post("/v1/auth/login", json={"email": "nobody@x.org", "password": PASSWORD})
    assert r.status_code == 401 and r.json() == {"detail": "Wrong email or password"}


def test_successful_login_clears_the_email_failures(web_client, session):
    create_user(session, "ana@x.org", "Ana", PASSWORD)
    wrong = {"email": "ana@x.org", "password": "wrong-password"}
    for _ in range(4):
        assert web_client.post("/v1/auth/login", json=wrong).status_code == 401
    good = {"email": "ana@x.org", "password": PASSWORD}
    assert web_client.post("/v1/auth/login", json=good).status_code == 200
    for _ in range(5):
        assert web_client.post("/v1/auth/login", json=wrong).status_code == 401
    assert web_client.post("/v1/auth/login", json=good).status_code == 429


def test_ip_throttle_applies_only_behind_a_trusted_proxy(web_client, test_settings):
    def attempt(email, forwarded="203.0.113.5"):
        r = web_client.post(
            "/v1/auth/login",
            headers={"X-Forwarded-For": forwarded},
            json={"email": email, "password": "wrong-password"},
        )
        return r.status_code

    # untrusted peer: the forwarded address is ignored, so distinct emails are never blocked
    assert [attempt(f"u{i}@x.org") for i in range(6)] == [401] * 6

    trusted = test_settings.model_copy(update={"trusted_proxies": ["testclient"]})
    web_client.app.state.settings = trusted
    assert [attempt(f"v{i}@x.org") for i in range(6)] == [401] * 5 + [429]
    assert attempt("w@x.org", forwarded="203.0.113.9") == 401


def test_throttle_window_and_block_last_fifteen_minutes():
    now = [1000.0]
    throttle = LoginThrottle(clock=lambda: now[0])
    keys = ["email:ana@x.org", "ip:203.0.113.5"]

    for _ in range(4):
        throttle.fail(keys)
    assert throttle.check(keys) is None
    now[0] += 15 * 60 + 1  # those four fall out of the window
    for _ in range(4):
        throttle.fail(keys)
    assert throttle.check(keys) is None
    throttle.fail(keys)
    assert throttle.check(keys) == 15 * 60
    assert throttle.check(["email:ana@x.org"]) == 15 * 60
    assert throttle.check(["email:bo@x.org"]) is None

    now[0] += 15 * 60 - 1
    assert throttle.check(keys) == 1
    now[0] += 1
    assert throttle.check(keys) is None
    throttle.fail(keys)  # a fresh window starts after the block
    assert throttle.check(keys) is None

    throttle.succeed(["email:ana@x.org"])
    for _ in range(4):
        throttle.fail(["ip:203.0.113.5"])
    assert throttle.check(["ip:203.0.113.5"]) == 15 * 60  # succeed left the ip key alone
    assert throttle.check(["email:ana@x.org"]) is None


def test_reset_password_ends_the_users_sessions(web_client, session):
    ana = login(web_client, session)
    create_user(session, "bo@x.org", "Bo")
    assert web_client.get("/v1/books").status_code == 200
    reset_password(session, ana.id)  # a teammate resets Ana's password
    assert web_client.get("/v1/books").status_code == 401


def test_changing_own_password_keeps_this_session_and_ends_others(web_client, session):
    login(web_client, session)
    laptop = TestClient(web_client.app, base_url="https://testserver")  # Ana's second browser
    good = {"email": "ana@x.org", "password": PASSWORD}
    assert laptop.post("/v1/auth/login", json=good).status_code == 200
    assert laptop.get("/v1/books").status_code == 200

    body = {"current": PASSWORD, "new": "a-new-password-1"}
    assert web_client.post("/v1/auth/password", headers=WEB, json=body).status_code == 204
    assert web_client.get("/v1/books").status_code == 200
    assert laptop.get("/v1/books").status_code == 401


def test_deactivating_a_user_ends_their_sessions(web_client, session):
    ana = login(web_client, session)
    create_user(session, "bo@x.org", "Bo")
    assert web_client.get("/v1/books").status_code == 200
    update_user(session, ana.id, active=False)
    assert web_client.get("/v1/books").status_code == 401


def _request(peer, *forwarded):
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded]
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


def test_forwarded_ip_used_only_from_trusted_proxy():
    trusted = ["172.17.0.1"]
    # Apache appends the real peer on the right; anything to its left is client-supplied.
    spoofed = _request("172.17.0.1", "6.6.6.6, 203.0.113.9")
    assert client_ip(spoofed, trusted) == "203.0.113.9"
    assert client_ip(spoofed, []) is None
    assert client_ip(_request("198.51.100.7", "203.0.113.9"), trusted) is None  # untrusted peer
    assert client_ip(_request("172.17.0.1"), trusted) is None  # missing header
    assert client_ip(_request("172.17.0.1", ""), trusted) is None  # empty header
    assert client_ip(Request({"type": "http", "client": None, "headers": []}), trusted) is None

    # a chain of trusted proxies is skipped from the right
    chain = ["172.17.0.1", "10.0.0.2"]
    assert client_ip(_request("172.17.0.1", "6.6.6.6, 203.0.113.9, 10.0.0.2"), chain) == (
        "203.0.113.9"
    )
    assert client_ip(_request("172.17.0.1", "10.0.0.2, 172.17.0.1"), chain) is None  # all trusted
    # repeated headers count as one list, in order
    assert client_ip(_request("172.17.0.1", "6.6.6.6", "203.0.113.9"), trusted) == "203.0.113.9"


def test_logout_clears_cookie(web_client, session):
    ana = login(web_client, session)
    r = web_client.post("/v1/auth/logout", headers=WEB)
    assert r.status_code == 204
    cookie = r.headers["set-cookie"]
    assert 'jerome_session=""' in cookie and "Max-Age=0" in cookie
    assert "jerome_session" not in web_client.cookies
    assert session.scalars(select(UserSession).where(UserSession.user_id == ana.id)).first() is None
    assert web_client.get("/v1/books").status_code == 401


def test_openapi_declares_bearer_scheme(client):
    spec = client.get("/openapi.json").json()
    schemes = spec["components"]["securitySchemes"]
    [name] = [k for k, v in schemes.items() if v == {"type": "http", "scheme": "bearer"}]
    assert {name: []} in spec["paths"]["/v1/books"]["get"]["security"]
    assert {name: []} in spec["paths"]["/v1/auth/me"]["get"]["security"]
    assert "security" not in spec["paths"]["/v1/auth/login"]["post"]


class _SnapshotOnResponseStart:
    """ASGI wrapper: when a response starts, read what is *committed* through a separate
    connection. The TestClient only returns once the app has finished, so back-to-back
    requests alone cannot show whether the commit happened before the response was sent."""

    def __init__(self, app, engine):
        self.app, self.engine, self.seen = app, engine, {}

    async def __call__(self, scope, receive, send):
        async def checked_send(message):
            if message["type"] == "http.response.start":
                with self.engine.connect() as conn:
                    self.seen[(scope["method"], scope["path"])] = (
                        conn.scalar(select(func.count()).select_from(UserSession)),
                        conn.scalar(select(User.must_change_password)),
                    )
            await send(message)

        await self.app(scope, receive, checked_send)


def test_auth_writes_are_committed_before_the_response_is_sent(
    engine, session_factory, test_settings
):
    with session_factory() as s:
        _, temporary = create_user(s, "ana@x.org", "Ana")
        s.commit()
    app = create_app(test_settings)  # the real get_session: each request commits for real
    probe = _SnapshotOnResponseStart(app, engine)
    try:
        c = TestClient(probe, base_url="https://testserver")
        r = c.post("/v1/auth/login", json={"email": "ana@x.org", "password": temporary})
        assert r.status_code == 200
        assert probe.seen[("POST", "/v1/auth/login")] == (1, True)  # session row already durable
        assert c.get("/v1/auth/me").status_code == 200  # the new cookie works at once

        body = {"current": temporary, "new": "a-new-password-1"}
        assert c.post("/v1/auth/password", headers=WEB, json=body).status_code == 204
        assert probe.seen[("POST", "/v1/auth/password")] == (1, False)
        assert c.get("/v1/books").status_code == 200

        assert c.post("/v1/auth/logout", headers=WEB).status_code == 204
        assert probe.seen[("POST", "/v1/auth/logout")] == (0, False)
    finally:
        app.state.engine.dispose()
