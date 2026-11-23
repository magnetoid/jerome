from jerome.api.runtime import effective_for_app
from jerome.core.settings_changes import set_value

MIB = 1024 * 1024


def test_app_value_reaches_readers(web_client, session, auth):
    store = web_client.app.state.settings_store
    set_value(session, "max_upload_mb", 1, actor="a", base=store.base)
    set_value(session, "public_base_url", "https://files.example.org", actor="a", base=store.base)
    store.invalidate()
    eff = effective_for_app(web_client.app, session)
    assert eff.max_upload_mb == 1 and eff.public_base_url == "https://files.example.org"
    r = web_client.post("/v1/books", headers=auth, files={"file": ("a.docx", b"x" * (2 * MIB))})
    assert r.status_code == 413


def test_registry_rebuilt_only_when_its_inputs_change(client, session):
    app = client.app
    before = app.state.registry
    set_value(session, "text_rpm", 7, actor="a", base=app.state.settings)
    app.state.settings_store.invalidate()
    effective_for_app(app, session)
    assert app.state.registry is before  # text_rpm is not a registry input
    set_value(
        session,
        "models.ai",
        {"provider": "anthropic", "model": "claude-sonnet-5-5"},
        actor="a",
        base=app.state.settings,
    )
    app.state.settings_store.invalidate()
    effective_for_app(app, session)
    assert app.state.registry is not before
    assert app.state.registry.for_pass("ai", local_only=False)[1].model == "claude-sonnet-5-5"


def test_text_rpm_and_languagetool_url_follow_the_app_value(client, session):
    app = client.app
    limits, lt = app.state.text_limits, app.state.lt_client
    rpms = []
    limits.set_rpm = rpms.append
    set_value(
        session, "languagetool_url", "http://lt.example.org", actor="a", base=app.state.settings
    )
    app.state.settings_store.invalidate()
    effective_for_app(app, session)
    assert app.state.lt_client is not lt and rpms == []  # text_rpm did not change
    lt = app.state.lt_client
    set_value(session, "text_rpm", 7, actor="a", base=app.state.settings)
    app.state.settings_store.invalidate()
    effective_for_app(app, session)
    assert rpms == [7] and app.state.lt_client is lt and app.state.text_limits is limits


def test_session_days_reaches_the_login_cookie(web_client, session):
    from jerome.core.users import create_user

    create_user(session, "ana@x.org", "Ana", "correct-horse-1")
    set_value(session, "session_days", 2, actor="a", base=web_client.app.state.settings)
    web_client.app.state.settings_store.invalidate()
    r = web_client.post(
        "/v1/auth/login", json={"email": "ana@x.org", "password": "correct-horse-1"}
    )
    assert r.status_code == 200 and f"Max-Age={2 * 24 * 3600}" in r.headers["set-cookie"]
    # the cookie slides with the effective value on every authenticated request too
    assert f"Max-Age={2 * 24 * 3600}" in web_client.get("/v1/auth/me").headers["set-cookie"]


def test_assigned_base_settings_keep_assigned_objects(client, session, test_settings):
    app = client.app
    registry, lt, limits = app.state.registry, app.state.lt_client, app.state.text_limits
    app.state.settings = test_settings.model_copy(update={"cookie_secure": False})
    eff = effective_for_app(app, session)
    assert eff.cookie_secure is False
    assert app.state.registry is registry and app.state.lt_client is lt
    assert app.state.text_limits is limits


def test_raised_upload_limit_applies_before_the_body_check(
    web_client, session, auth, test_settings
):
    # Another process (or a restart) left this one's cache on the old, lower limit.
    test_settings.max_upload_mb = 1
    store = web_client.app.state.settings_store
    set_value(session, "max_upload_mb", 3, actor="a", base=store.base)
    store.invalidate()
    r = web_client.post("/v1/books", headers=auth, files={"file": ("a.docx", b"x" * (2 * MIB))})
    assert r.status_code != 413
