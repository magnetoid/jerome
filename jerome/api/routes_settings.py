"""The super admin Settings page: runtime values, secrets, the audit and API keys."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.engine import make_url

from jerome.api.deps import CLIENT_HEADER, Principal, SessionDep, require_super_admin
from jerome.api.schemas import (
    ApiKeyCreatedOut,
    ApiKeyCreateIn,
    ApiKeyOut,
    ProviderModelsOut,
    SecretSettingOut,
    SettingOut,
    SettingPutIn,
    SettingsAuditOut,
    SettingsOut,
    SystemSettingsOut,
)
from jerome.config import Settings
from jerome.core import keys, settings_changes
from jerome.core.app_settings import (
    RUNTIME_KEYS,
    SECRET_KEYS,
    InvalidSetting,
    check_url,
    overlay,
    value_of,
)
from jerome.core.secretbox import secret_box, secret_storage_state
from jerome.errors import Conflict
from jerome.models.listing import LABELS, LISTABLE, list_models

router = APIRouter(dependencies=[Depends(require_super_admin)])

Admin = Annotated[Principal, Depends(require_super_admin)]


def _snapshot(request: Request, session: SessionDep) -> SettingsOut:
    base = request.app.state.settings
    effective = overlay(base, settings_changes.load_rows(session), secret_box(base))
    runtime = []
    for key in RUNTIME_KEYS:
        meta = effective.meta[key]
        runtime.append(
            SettingOut(
                key=key,
                value=value_of(effective.settings, key),
                source=meta.source,
                updated_by=meta.updated_by,
                updated_at=meta.updated_at,
                unreadable=meta.unreadable,
            )
        )
    secrets = []
    for key in SECRET_KEYS:
        meta = effective.meta[key]
        secrets.append(
            SecretSettingOut(
                key=key,
                is_set=bool(meta.is_set),
                source=meta.source,
                updated_by=meta.updated_by,
                updated_at=meta.updated_at,
                unreadable=meta.unreadable,
            )
        )
    system = SystemSettingsOut(
        database_url=make_url(base.database_url).render_as_string(hide_password=True),
        data_dir=str(base.data_dir),
        web_dir=str(base.web_dir) if base.web_dir is not None else None,
        trusted_proxies=list(base.trusted_proxies),
        cookie_secure=base.cookie_secure,
        secret_storage=secret_storage_state(base),
    )
    return SettingsOut(settings=runtime, secrets=secrets, system=system)


@router.get("/settings", response_model=SettingsOut)
def get_settings_page(request: Request, session: SessionDep):
    return _snapshot(request, session)


@router.get("/settings/audit", response_model=list[SettingsAuditOut])
def settings_audit(session: SessionDep):
    return settings_changes.list_audit(session, 50)


@router.put("/settings/{key}", response_model=SettingsOut)
def put_setting(key: str, body: SettingPutIn, request: Request, session: SessionDep, who: Admin):
    base = request.app.state.settings
    if key in SECRET_KEYS:
        if body.value is not None and not isinstance(body.value, str):
            raise InvalidSetting("a secret must be a string, or null to clear it")
        settings_changes.set_secret(session, key, body.value, actor=who.actor, base=base)
    else:
        settings_changes.set_value(session, key, body.value, actor=who.actor, base=base)
    session.flush()
    request.app.state.settings_store.invalidate()
    return _snapshot(request, session)


@router.delete("/settings/{key}", status_code=204)
def reset_setting(key: str, request: Request, session: SessionDep, who: Admin):
    settings_changes.reset(session, key, actor=who.actor, base=request.app.state.settings)
    session.flush()
    request.app.state.settings_store.invalidate()
    return Response(status_code=204)


def _from_our_pages(request: Request) -> None:
    """This GET sends a stored key out, so it takes the cross-site guard the session gives
    only to other methods: a link on another site brings the cookie but not this header."""
    if request.headers.get(CLIENT_HEADER) != "web":
        raise HTTPException(status_code=403, detail="missing X-Jerome-Client header")


@router.get(
    "/settings/provider-models",
    response_model=ProviderModelsOut,
    dependencies=[Depends(_from_our_pages)],
)
def provider_models(
    request: Request, session: SessionDep, provider: str, base_url: str | None = None
):
    """The provider's text models, fetched now with the stored key (app, else environment).
    409 when the key is not set; 502 when the provider fails. The own-server key goes only
    to a base URL saved under Models; another URL is asked without a key."""
    if provider not in LISTABLE:
        raise InvalidSetting(f"provider must be one of {', '.join(LISTABLE)}")
    if provider == "openai_compat":
        if not base_url:
            raise InvalidSetting("base_url is required for openai_compat")
        base_url = check_url(base_url, "base_url")
    elif base_url:
        raise InvalidSetting("base_url applies only to openai_compat")
    base = request.app.state.settings
    effective = overlay(base, settings_changes.load_rows(session), secret_box(base))
    key = effective.secrets.get(f"{provider}_api_key")
    if provider == "openai_compat" and base_url not in _saved_own_urls(effective.settings):
        key = None
    if not key and provider != "openai_compat":
        raise Conflict(f"Set the {LABELS[provider]} key under Secrets first")
    models = list_models(provider, api_key=key, base_url=base_url)
    return ProviderModelsOut(provider=provider, models=models)


def _saved_own_urls(settings: Settings) -> set[str]:
    """The own-server base URLs saved under Models, as check_url writes them."""
    urls = set()
    for cfg in settings.models.values():
        if isinstance(cfg, dict) and cfg.get("provider") == "openai_compat" and cfg.get("base_url"):
            try:
                urls.add(check_url(cfg["base_url"], "base_url"))
            except InvalidSetting:
                continue
    return urls


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_keys(session: SessionDep):
    return keys.list_api_keys(session)


@router.post("/api-keys", status_code=201, response_model=ApiKeyCreatedOut)
def create_key(body: ApiKeyCreateIn, session: SessionDep, who: Admin):
    key, token = keys.create_named_key(session, body.name, actor=who.actor)
    return ApiKeyCreatedOut(key=ApiKeyOut.model_validate(key), token=token)


@router.delete("/api-keys/{key_id}", response_model=ApiKeyOut)
def revoke_key(key_id: int, session: SessionDep, who: Admin):
    return keys.revoke_api_key(session, key_id, actor=who.actor)
