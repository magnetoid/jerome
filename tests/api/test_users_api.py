from jerome.core import users
from jerome.core.users import create_user
from tests.api.test_auth_api import PASSWORD, WEB, login

USER_KEYS = {
    "id",
    "email",
    "name",
    "active",
    "role",
    "must_change_password",
    "last_login_at",
    "created_at",
}


def test_add_member_returns_temporary_password_once(web_client, session):
    login(web_client, session)
    r = web_client.post("/v1/users", headers=WEB, json={"email": "Bo@x.org", "name": "Bo"})
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"user", "temporary_password"}
    assert set(body["user"]) == USER_KEYS
    assert body["user"]["email"] == "bo@x.org"
    assert body["user"]["must_change_password"] is True
    assert len(body["temporary_password"]) >= 10

    # the temporary password works, and is never shown again
    assert users.authenticate(session, "bo@x.org", body["temporary_password"]) is not None
    listing = web_client.get("/v1/users")
    assert listing.status_code == 200
    rows = listing.json()
    assert [row["email"] for row in rows] == ["ana@x.org", "bo@x.org"]
    assert all(set(row) == USER_KEYS for row in rows)
    assert "temporary_password" not in listing.text and "password_hash" not in listing.text

    dup = web_client.post("/v1/users", headers=WEB, json={"email": "BO@x.org", "name": "Other"})
    assert dup.status_code == 409


def test_add_member_validates_body(web_client, session):
    login(web_client, session)
    for payload in (
        {"email": "", "name": "Bo"},
        {"email": "no-at-sign", "name": "Bo"},
        {"email": "bo@x.org", "name": "  "},
        {"email": "bo@x.org"},
    ):
        assert web_client.post("/v1/users", headers=WEB, json=payload).status_code == 422


def test_reset_password_returns_new_temporary_password(web_client, session):
    login(web_client, session)
    user, _ = create_user(session, "bo@x.org", "Bo", PASSWORD)
    r = web_client.patch(f"/v1/users/{user.id}", headers=WEB, json={"reset_password": True})
    assert r.status_code == 200
    temp = r.json()["temporary_password"]
    assert temp and r.json()["user"]["must_change_password"] is True
    assert users.authenticate(session, "bo@x.org", PASSWORD) is None
    assert users.authenticate(session, "bo@x.org", temp) is not None

    plain = web_client.patch(f"/v1/users/{user.id}", headers=WEB, json={"name": "Robert"})
    assert plain.json()["temporary_password"] is None
    assert plain.json()["user"]["name"] == "Robert"
    assert web_client.patch("/v1/users/999999", headers=WEB, json={"name": "X"}).status_code == 404


def test_deactivate_last_active_member_is_409(web_client, session):
    only = login(web_client, session)
    r = web_client.patch(f"/v1/users/{only.id}", headers=WEB, json={"active": False})
    assert r.status_code == 409
    assert web_client.get("/v1/users").json()[0]["active"] is True

    other, _ = create_user(session, "bo@x.org", "Bo", PASSWORD)
    r = web_client.patch(f"/v1/users/{other.id}", headers=WEB, json={"active": False})
    assert r.status_code == 200 and r.json()["user"]["active"] is False
    # Ana is now the last active member again
    assert (
        web_client.patch(f"/v1/users/{only.id}", headers=WEB, json={"active": False}).status_code
        == 409
    )


def test_team_routes_are_for_super_admins_only(web_client, session, client, auth):
    login(web_client, session)  # Ana: super admin
    login(web_client, session, "bo@x.org", "Bo")  # Bo: member, now signed in
    for r in (
        web_client.get("/v1/users"),
        web_client.post("/v1/users", headers=WEB, json={"email": "c@x.org", "name": "C"}),
        web_client.patch("/v1/users/1", headers=WEB, json={"name": "X"}),
    ):
        assert r.status_code == 403 and r.json()["detail"] == "super admin only"
    keyed = client.get("/v1/users", headers=auth)  # API keys can't manage the team
    assert keyed.status_code == 403 and keyed.json()["detail"] == "super admin only"
    assert client.get("/v1/projects", headers=auth).status_code == 200  # ...but keep other routes


def test_add_member_with_a_role_and_change_roles(web_client, session):
    ana = login(web_client, session)
    r = web_client.post("/v1/users", headers=WEB, json={"email": "bo@x.org", "name": "Bo"})
    assert r.json()["user"]["role"] == "member"
    r = web_client.post(
        "/v1/users",
        headers=WEB,
        json={"email": "cy@x.org", "name": "Cy", "role": "super_admin"},
    )
    assert r.json()["user"]["role"] == "super_admin"
    bad = web_client.post(
        "/v1/users", headers=WEB, json={"email": "d@x.org", "name": "D", "role": "owner"}
    )
    assert bad.status_code == 422
    cy = r.json()["user"]["id"]
    patched = web_client.patch(f"/v1/users/{cy}", headers=WEB, json={"role": "member"})
    assert patched.json()["user"]["role"] == "member"
    last = web_client.patch(f"/v1/users/{ana.id}", headers=WEB, json={"role": "member"})
    assert last.status_code == 409
    assert last.json()["detail"] == "cannot remove the last active super admin"


def test_team_routes_need_auth(client, session, web_client):
    assert client.get("/v1/users").status_code == 401
    assert client.post("/v1/users", json={"email": "a@x.org", "name": "A"}).status_code == 401
    assert client.patch("/v1/users/1", json={"name": "A"}).status_code == 401

    login(web_client, session)  # cookie session without the web header: the guard applies
    r = web_client.post("/v1/users", json={"email": "bo@x.org", "name": "Bo"})
    assert r.status_code == 403
    assert web_client.patch("/v1/users/1", json={"name": "A"}).status_code == 403
