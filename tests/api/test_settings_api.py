import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from jerome.api.runtime import effective_for_app
from jerome.core.users import create_user
from jerome.orm import AppSetting
from tests.api.test_auth_api import PASSWORD, WEB, login


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch):
    for name in ("JEROME_SECRET_KEY", "ANTHROPIC_API_KEY", "JEROME_WEBHOOK_SECRET"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def with_secret_storage(web_client, test_settings):
    web_client.app.state.settings = test_settings.model_copy(
        update={"secret_key": SecretStr(Fernet.generate_key().decode())}
    )


def test_only_super_admin_on_web(web_client, session, auth):
    assert web_client.get("/v1/settings", headers=auth).status_code == 403
    create_user(session, "bo@x.org", "Bo", PASSWORD, role="member")
    web_client.post("/v1/auth/login", json={"email": "bo@x.org", "password": PASSWORD})
    assert web_client.get("/v1/settings").status_code == 403


def test_get_lists_keys_in_order(web_client, session):
    login(web_client, session)
    body = web_client.get("/v1/settings").json()
    assert [s["key"] for s in body["settings"]] == [
        "models.ai",
        "models.translate",
        "prices",
        "text_daily_cost_cap",
        "text_rpm",
        "max_upload_mb",
        "session_days",
        "languagetool_url",
        "languagetool_username",
        "public_base_url",
    ]
    assert [s["key"] for s in body["secrets"]] == [
        "anthropic_api_key",
        "webhook_secret",
        "openai_compat_api_key",
        "openai_api_key",
        "gemini_api_key",
        "mistral_api_key",
        "openrouter_api_key",
        "xai_api_key",
        "deepl_api_key",
        "languagetool_api_key",
    ]
    rpm = next(s for s in body["settings"] if s["key"] == "text_rpm")
    assert rpm["value"] == 30 and rpm["source"] == "default" and rpm["unreadable"] is False
    assert body["system"]["secret_storage"] == "missing"


def test_put_validates_and_resets(web_client, session):
    login(web_client, session)
    assert (
        web_client.put("/v1/settings/text_rpm", headers=WEB, json={"value": 0}).status_code == 400
    )
    r = web_client.put("/v1/settings/text_rpm", headers=WEB, json={"value": 12})
    row = next(s for s in r.json()["settings"] if s["key"] == "text_rpm")
    assert row["value"] == 12 and row["source"] == "app" and row["updated_by"] == "ana@x.org"
    assert web_client.delete("/v1/settings/text_rpm", headers=WEB).status_code == 204
    back = web_client.get("/v1/settings").json()["settings"]
    assert next(s for s in back if s["key"] == "text_rpm")["source"] == "default"
    r = web_client.put("/v1/settings/database_url", headers=WEB, json={"value": "x"})
    assert r.status_code == 404
    assert web_client.delete("/v1/settings/database_url", headers=WEB).status_code == 404


def test_writes_invalidate_the_settings_cache(web_client, session, monkeypatch):
    calls = []
    monkeypatch.setattr(web_client.app.state.settings_store, "invalidate", lambda: calls.append(1))
    login(web_client, session)
    web_client.put("/v1/settings/text_rpm", headers=WEB, json={"value": 7})
    web_client.delete("/v1/settings/text_rpm", headers=WEB)
    assert len(calls) == 2


def test_put_and_reset_apply_without_restart(web_client, session):
    login(web_client, session)
    state = web_client.app.state
    web_client.put("/v1/settings/text_rpm", headers=WEB, json={"value": 12})
    web_client.get("/v1/settings")
    assert state.settings_store.cached().settings.text_rpm == 12
    assert state.text_limits._capacity == 12
    web_client.delete("/v1/settings/text_rpm", headers=WEB)
    web_client.get("/v1/settings")
    assert state.settings_store.cached().settings.text_rpm == 30
    assert state.text_limits._capacity == 30


def test_secret_audit_rows_carry_no_values(web_client, session, with_secret_storage):
    login(web_client, session)
    url = "/v1/settings/anthropic_api_key"

    def latest():
        return web_client.get("/v1/settings/audit").json()[0]

    for body, action in (
        ({"value": "sk-ant-SECRET-1"}, "set"),
        ({"value": "sk-ant-SECRET-2"}, "replaced"),
        ({"value": None}, "cleared"),
    ):
        assert web_client.put(url, headers=WEB, json=body).status_code == 200
        row = latest()
        assert row["key"] == "anthropic_api_key" and row["action"] == action
        assert row["before"] is None and row["after"] is None
    web_client.put(url, headers=WEB, json={"value": "sk-ant-SECRET-3"})
    assert web_client.delete(url, headers=WEB).status_code == 204
    row = latest()
    assert row["action"] == "reset" and row["before"] is None and row["after"] is None


def test_validation_error_does_not_echo_input_on_settings(web_client, session):
    login(web_client, session)
    r = web_client.put(
        "/v1/settings/anthropic_api_key",
        headers=WEB,
        content=b'"sk-SENTINEL-9"',
    )
    assert r.status_code == 422 and "sk-SENTINEL-9" not in r.text
    assert all({"loc", "msg", "type"} <= set(e) for e in r.json()["detail"])
    other = web_client.post("/v1/api-keys", headers=WEB, json={"nope": "echo-me"})
    assert other.status_code == 422 and "echo-me" in other.text


def test_secret_never_returned(web_client, session, with_secret_storage):
    login(web_client, session)
    r = web_client.put(
        "/v1/settings/anthropic_api_key", headers=WEB, json={"value": "sk-ant-SECRET-1"}
    )
    assert r.status_code == 200 and "sk-ant-SECRET-1" not in r.text
    secret = next(s for s in r.json()["secrets"] if s["key"] == "anthropic_api_key")
    assert secret["is_set"] is True and secret["source"] == "app" and "value" not in secret
    for path in ("/v1/settings", "/v1/settings/audit"):
        assert "sk-ant-SECRET-1" not in web_client.get(path).text
    stored = session.get(AppSetting, "anthropic_api_key").secret
    assert b"sk-ant-SECRET-1" not in stored


def test_secret_value_must_be_string_or_null(web_client, session, with_secret_storage):
    login(web_client, session)
    r = web_client.put("/v1/settings/webhook_secret", headers=WEB, json={"value": 5})
    assert r.status_code == 400


def test_secret_without_storage_is_409_but_clear_works(web_client, session):
    login(web_client, session)
    r = web_client.put("/v1/settings/anthropic_api_key", headers=WEB, json={"value": "sk-x"})
    assert r.status_code == 409
    assert r.json()["detail"] == "secret storage is not set up: set JEROME_SECRET_KEY"
    r = web_client.put("/v1/settings/anthropic_api_key", headers=WEB, json={"value": None})
    assert r.status_code == 200


def test_api_keys_lifecycle(web_client, session):
    login(web_client, session)
    r = web_client.post("/v1/api-keys", headers=WEB, json={"name": "janus"})
    assert r.status_code == 201 and r.json()["token"].startswith("jk_")
    token = r.json()["token"]
    listing = web_client.get("/v1/api-keys")
    assert token not in listing.text and "key_hash" not in listing.text
    kid = r.json()["key"]["id"]
    assert web_client.delete(f"/v1/api-keys/{kid}", headers=WEB).json()["revoked_at"]
    assert (
        web_client.get("/v1/books", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    )
    again = web_client.post("/v1/api-keys", headers=WEB, json={"name": "janus"})
    assert again.status_code == 409
    assert again.json()["detail"] == "a key with this name existed; choose another name"
    actions = [a["action"] for a in web_client.get("/v1/settings/audit").json()]
    assert actions[:2] == ["key_revoked", "key_created"]


def test_masked_database_url(web_client, session):
    login(web_client, session)
    system = web_client.get("/v1/settings").json()["system"]
    assert "jerome:jerome@" not in system["database_url"] and "***" in system["database_url"]


def test_put_without_value_or_with_typo_is_422_and_keeps_secret(
    web_client, session, with_secret_storage
):
    login(web_client, session)
    url = "/v1/settings/anthropic_api_key"
    assert web_client.put(url, headers=WEB, json={"value": "sk-ant-KEEP"}).status_code == 200
    for body in ({}, {"secret": "x"}, {"value": "sk-ant-NEW", "extra": 1}):
        assert web_client.put(url, headers=WEB, json=body).status_code == 422
    secret = next(
        s
        for s in web_client.get("/v1/settings").json()["secrets"]
        if s["key"] == "anthropic_api_key"
    )
    assert secret["is_set"] is True and secret["source"] == "app"
    assert web_client.put("/v1/settings/public_base_url", headers=WEB, json={}).status_code == 422


def test_put_models_translate_applies_to_registry(web_client, session):
    login(web_client, session)
    app = web_client.app
    before = app.state.registry
    value = {"provider": "anthropic", "model": "claude-sonnet-5-5"}
    r = web_client.put("/v1/settings/models.translate", headers=WEB, json={"value": value})
    assert r.status_code == 200
    row = next(s for s in r.json()["settings"] if s["key"] == "models.translate")
    assert row["value"] == value and row["source"] == "app"
    ai = next(s for s in r.json()["settings"] if s["key"] == "models.ai")
    assert ai["source"] != "app"
    bad = web_client.put(
        "/v1/settings/models.translate",
        headers=WEB,
        json={"value": {"provider": "anthropic", "model": ""}},
    )
    assert bad.status_code == 400
    effective_for_app(app, session)
    assert app.state.registry is not before
    assert (
        app.state.registry.for_pass("translate", local_only=False)[1].model == "claude-sonnet-5-5"
    )
    assert app.state.registry.for_pass("ai", local_only=False)[1].model == "claude-opus-5-5"


MODELS_URL = "/v1/settings/provider-models"


def test_provider_models_super_admin_only(web_client, session, auth):
    r = web_client.get(MODELS_URL, params={"provider": "openai"}, headers=auth)
    assert r.status_code == 403
    create_user(session, "bo@x.org", "Bo", PASSWORD, role="member")
    web_client.post("/v1/auth/login", json={"email": "bo@x.org", "password": PASSWORD})
    assert web_client.get(MODELS_URL, params={"provider": "openai"}).status_code == 403


def test_provider_models_with_app_key(web_client, session, with_secret_storage, respx_mock):
    login(web_client, session)
    web_client.put("/v1/settings/openai_api_key", headers=WEB, json={"value": "sk-SECRET-9"})
    route = respx_mock.get("https://api.openai.com/v1/models").respond(
        200, json={"data": [{"id": "gpt-5"}, {"id": "text-embedding-3-small"}]}
    )
    r = web_client.get(MODELS_URL, params={"provider": "openai"})
    assert r.status_code == 200, r.text
    assert r.json() == {"provider": "openai", "models": [{"id": "gpt-5", "name": "gpt-5"}]}
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-SECRET-9"
    assert "sk-SECRET-9" not in r.text


def test_provider_models_with_environment_key(web_client, session, test_settings, respx_mock):
    web_client.app.state.settings = test_settings.model_copy(
        update={"anthropic_api_key": SecretStr("sk-ant-ENV")}
    )
    login(web_client, session)
    route = respx_mock.get("https://api.anthropic.com/v1/models").respond(
        200, json={"data": [{"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5"}]}
    )
    r = web_client.get(MODELS_URL, params={"provider": "anthropic"})
    assert r.status_code == 200 and r.json()["models"][0]["id"] == "claude-opus-5-5"
    assert route.calls.last.request.headers["x-api-key"] == "sk-ant-ENV"


def test_provider_models_without_key_is_409(web_client, session):
    login(web_client, session)
    r = web_client.get(MODELS_URL, params={"provider": "xai"})
    assert r.status_code == 409 and r.json()["detail"] == "Set the xAI key under Secrets first"


def test_provider_models_own_server_needs_no_key(web_client, session, respx_mock):
    login(web_client, session)
    reply = {"data": [{"id": "qwen3"}]}
    respx_mock.get("http://localhost:11434/v1/models").respond(200, json=reply)
    r = web_client.get(
        MODELS_URL, params={"provider": "openai_compat", "base_url": "http://localhost:11434/v1"}
    )
    assert r.status_code == 200 and r.json()["models"] == [{"id": "qwen3", "name": "qwen3"}]


@pytest.mark.parametrize(
    "params",
    [
        {"provider": "deepl"},
        {"provider": "nope"},
        {"provider": "openai_compat"},
        {"provider": "openai_compat", "base_url": "ftp://x"},
        {"provider": "openai", "base_url": "http://localhost:1/v1"},
    ],
)
def test_provider_models_bad_input_is_400(web_client, session, params):
    login(web_client, session)
    assert web_client.get(MODELS_URL, params=params).status_code == 400


def test_provider_models_provider_failure_is_502(
    web_client, session, with_secret_storage, respx_mock
):
    login(web_client, session)
    web_client.put("/v1/settings/openai_api_key", headers=WEB, json={"value": "sk-SECRET-9"})
    respx_mock.get("https://api.openai.com/v1/models").respond(401)
    r = web_client.get(MODELS_URL, params={"provider": "openai"})
    assert r.status_code == 502 and r.json()["detail"] == "OpenAI refused the key"


def test_provider_models_unreadable_key_is_409(web_client, session, test_settings):
    web_client.app.state.settings = test_settings.model_copy(
        update={"secret_key": SecretStr(Fernet.generate_key().decode())}
    )
    login(web_client, session)
    web_client.put("/v1/settings/mistral_api_key", headers=WEB, json={"value": "m-SECRET"})
    web_client.app.state.settings = test_settings.model_copy(  # the key was rotated
        update={"secret_key": SecretStr(Fernet.generate_key().decode())}
    )
    r = web_client.get(MODELS_URL, params={"provider": "mistral"})
    assert r.status_code == 409 and "Mistral" in r.json()["detail"]
