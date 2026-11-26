"""Feedback from the web app: anyone signed in sends it, the super admin reads it."""

import io

from PIL import Image

from jerome.core.users import create_user
from tests.api.test_auth_api import PASSWORD, WEB, login


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(out, "PNG")
    return out.getvalue()


def _send(client, *, kind="bug", message="The Save button does nothing.", shot=None, **extra):
    data = {"kind": kind, "message": message, "page": "/projects/3", **extra}
    files = {"screenshot": ("screen.png", shot, "image/png")} if shot is not None else None
    return client.post("/v1/feedback", data=data, files=files, headers=WEB)


def _member(client, session, email="bo@x.org"):
    create_user(session, email, "Bo", PASSWORD, role="member")
    r = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text


def test_a_member_sends_feedback_with_a_screenshot(web_client, session):
    login(web_client, session)  # the first account: super admin
    web_client.post("/v1/auth/logout", headers=WEB)
    _member(web_client, session)
    shot = _png()
    r = _send(web_client, shot=shot, viewport="1440x900", user_agent="Firefox")
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["kind"], body["message"], body["page"], body["status"]) == (
        "bug",
        "The Save button does nothing.",
        "/projects/3",
        "new",
    )
    assert body["created_by"] == "bo@x.org"
    assert body["has_screenshot"] is True
    assert (body["viewport"], body["user_agent"]) == ("1440x900", "Firefox")
    assert "screenshot" not in body

    web_client.post("/v1/auth/logout", headers=WEB)
    web_client.post("/v1/auth/login", json={"email": "ana@x.org", "password": PASSWORD})
    listed = web_client.get("/v1/feedback").json()
    assert [f["id"] for f in listed] == [body["id"]]
    image = web_client.get(f"/v1/feedback/{body['id']}/screenshot")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content == shot


def test_feedback_without_a_screenshot(web_client, session):
    login(web_client, session)
    r = _send(web_client, kind="idea", message="  Show word counts per chapter.  ")
    assert r.status_code == 201, r.text
    assert r.json()["message"] == "Show word counts per chapter."
    assert r.json()["has_screenshot"] is False
    fid = r.json()["id"]
    assert web_client.get(f"/v1/feedback/{fid}/screenshot").status_code == 404


def test_feedback_is_checked(web_client, session):
    login(web_client, session)
    assert _send(web_client, kind="praise").status_code == 422
    assert _send(web_client, message="   ").status_code == 422
    assert _send(web_client, message="x" * 5001).status_code == 422
    assert _send(web_client, shot=b"not an image").status_code == 422
    assert web_client.get("/v1/feedback").json() == []


def test_feedback_kinds(web_client, session):
    login(web_client, session)
    for kind in ("bug", "feature", "idea"):
        assert _send(web_client, kind=kind).status_code == 201
    assert [f["kind"] for f in web_client.get("/v1/feedback").json()] == ["idea", "feature", "bug"]


def test_the_super_admin_marks_done_and_deletes(web_client, session):
    login(web_client, session)
    fid = _send(web_client, shot=_png()).json()["id"]
    r = web_client.patch(f"/v1/feedback/{fid}", json={"status": "done"}, headers=WEB)
    assert r.status_code == 200 and r.json()["status"] == "done"
    bad = web_client.patch(f"/v1/feedback/{fid}", json={"status": "gone"}, headers=WEB)
    assert bad.status_code == 422
    assert web_client.delete(f"/v1/feedback/{fid}", headers=WEB).status_code == 204
    assert web_client.get("/v1/feedback").json() == []
    assert web_client.delete(f"/v1/feedback/{fid}", headers=WEB).status_code == 404
    gone = web_client.patch(f"/v1/feedback/{fid}", json={"status": "new"}, headers=WEB)
    assert gone.status_code == 404


def test_only_the_super_admin_reads_feedback(web_client, session, auth):
    login(web_client, session)
    fid = _send(web_client).json()["id"]
    web_client.post("/v1/auth/logout", headers=WEB)
    _member(web_client, session)
    assert web_client.get("/v1/feedback").status_code == 403
    assert web_client.get(f"/v1/feedback/{fid}/screenshot").status_code == 403
    done = web_client.patch(f"/v1/feedback/{fid}", json={"status": "done"}, headers=WEB)
    assert done.status_code == 403
    assert web_client.delete(f"/v1/feedback/{fid}", headers=WEB).status_code == 403
    assert web_client.get("/v1/feedback", headers=auth).status_code == 403


def test_signing_in_is_needed_to_send(client):
    assert _send(client).status_code == 401
