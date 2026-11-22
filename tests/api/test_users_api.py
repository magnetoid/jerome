from jerome.core.users import create_user
from tests.api.test_auth_api import PASSWORD, WEB, login

USER_KEYS = {"id", "email", "name", "active", "must_change_password", "last_login_at", "created_at"}


def test_add_member_returns_temporary_password_once(client, auth, session):
    r = client.post("/v1/users", headers=auth, json={"email": "Bo@x.org", "name": "Bo"})
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"user", "temporary_password"}
    assert set(body["user"]) == USER_KEYS
    assert body["user"]["email"] == "bo@x.org"
    assert body["user"]["must_change_password"] is True
    assert len(body["temporary_password"]) >= 10

    # the temporary password works to log in, and is never shown again
    login_r = client.post(
        "/v1/auth/login", json={"email": "bo@x.org", "password": body["temporary_password"]}
    )
    assert login_r.status_code == 200
    listing = client.get("/v1/users", headers=auth)
    assert listing.status_code == 200
    rows = listing.json()
    assert [row["email"] for row in rows] == ["bo@x.org"]
    assert all(set(row) == USER_KEYS for row in rows)
    assert "temporary_password" not in listing.text and "password_hash" not in listing.text

    dup = client.post("/v1/users", headers=auth, json={"email": "BO@x.org", "name": "Other"})
    assert dup.status_code == 409


def test_add_member_validates_body(client, auth):
    for payload in (
        {"email": "", "name": "Bo"},
        {"email": "no-at-sign", "name": "Bo"},
        {"email": "bo@x.org", "name": "  "},
        {"email": "bo@x.org"},
    ):
        assert client.post("/v1/users", headers=auth, json=payload).status_code == 422


def test_reset_password_returns_new_temporary_password(client, auth, session):
    user, _ = create_user(session, "bo@x.org", "Bo", PASSWORD)
    r = client.patch(f"/v1/users/{user.id}", headers=auth, json={"reset_password": True})
    assert r.status_code == 200
    temp = r.json()["temporary_password"]
    assert temp and r.json()["user"]["must_change_password"] is True
    old = client.post("/v1/auth/login", json={"email": "bo@x.org", "password": PASSWORD})
    assert old.status_code == 401
    new = client.post("/v1/auth/login", json={"email": "bo@x.org", "password": temp})
    assert new.status_code == 200

    plain = client.patch(f"/v1/users/{user.id}", headers=auth, json={"name": "Robert"})
    assert plain.json()["temporary_password"] is None
    assert plain.json()["user"]["name"] == "Robert"
    assert client.patch("/v1/users/999999", headers=auth, json={"name": "X"}).status_code == 404


def test_deactivate_last_active_member_is_409(client, auth, session):
    only, _ = create_user(session, "ana@x.org", "Ana", PASSWORD)
    r = client.patch(f"/v1/users/{only.id}", headers=auth, json={"active": False})
    assert r.status_code == 409
    assert client.get("/v1/users", headers=auth).json()[0]["active"] is True

    other, _ = create_user(session, "bo@x.org", "Bo", PASSWORD)
    r = client.patch(f"/v1/users/{other.id}", headers=auth, json={"active": False})
    assert r.status_code == 200 and r.json()["user"]["active"] is False
    # Ana is now the last active member again
    assert (
        client.patch(f"/v1/users/{only.id}", headers=auth, json={"active": False}).status_code
        == 409
    )


def test_team_routes_work_with_a_cookie_session(web_client, session):
    login(web_client, session)
    assert [u["email"] for u in web_client.get("/v1/users").json()] == ["ana@x.org"]
    r = web_client.post("/v1/users", headers=WEB, json={"email": "bo@x.org", "name": "Bo"})
    assert r.status_code == 201


def test_team_routes_need_auth(client, session, web_client):
    assert client.get("/v1/users").status_code == 401
    assert client.post("/v1/users", json={"email": "a@x.org", "name": "A"}).status_code == 401
    assert client.patch("/v1/users/1", json={"name": "A"}).status_code == 401

    login(web_client, session)  # cookie session without the web header: the guard applies
    r = web_client.post("/v1/users", json={"email": "bo@x.org", "name": "Bo"})
    assert r.status_code == 403
    assert web_client.patch("/v1/users/1", json={"name": "A"}).status_code == 403
