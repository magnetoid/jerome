"""The running app's effective settings, with the objects built from them kept in step.

``app.state.settings`` stays the environment base; ``app.state.settings_store`` lays the
app values on it. When the store's version moves, the model registry, the LanguageTool
client and the text rate limit on ``app.state`` are rebuilt or updated, each only when what
it is made from changed. Tests put their own objects there, so nothing else is replaced.
"""

import logging
import threading

from fastapi import FastAPI
from sqlalchemy.orm import Session

from jerome.config import Settings
from jerome.core.settings_store import SettingsStore, registry_inputs
from jerome.models.registry import build_registry
from jerome.proofread.languagetool import LanguageToolClient

log = logging.getLogger(__name__)


def install(app: FastAPI, settings: Settings) -> None:
    """Give the app its settings store. app.state.registry, lt_client and text_limits are
    taken to be built from ``settings``."""
    state = app.state
    state.settings_store = SettingsStore(settings)
    state.settings_lock = threading.Lock()  # sync routes run on several threads
    state.settings_synced_version = state.settings_store.version
    state.synced_registry_inputs = registry_inputs(settings)
    state.synced_languagetool_url = settings.languagetool_url
    state.synced_text_rpm = settings.text_rpm


def _sync(app: FastAPI) -> Settings:
    state = app.state
    store: SettingsStore = state.settings_store
    with state.settings_lock:
        # The version first: the store replaces its settings before bumping the version, so
        # these settings are at least as new as the version recorded for them.
        version = store.version
        settings = store.cached().settings
        if version == state.settings_synced_version:
            return settings
        inputs = registry_inputs(settings)
        if inputs != state.synced_registry_inputs:
            try:
                state.registry = build_registry(settings)
            except Exception as exc:  # noqa: BLE001 - keep serving with the registry we have
                # only the type: the message could carry a setting, even a key
                log.error(
                    "model registry rebuild failed (%s); keeping the old one", type(exc).__name__
                )
            else:
                state.synced_registry_inputs = inputs
        if settings.languagetool_url != state.synced_languagetool_url:
            state.lt_client = LanguageToolClient(settings.languagetool_url)
            state.synced_languagetool_url = settings.languagetool_url
        if settings.text_rpm != state.synced_text_rpm:
            state.text_limits.set_rpm(settings.text_rpm)
            state.synced_text_rpm = settings.text_rpm
        state.settings_synced_version = version
        return settings


def effective_for_app(app: FastAPI, session: Session) -> Settings:
    """The effective settings for a request, read through its own session."""
    store: SettingsStore = app.state.settings_store
    store.base = app.state.settings  # a no-op unless it was replaced or changed
    store.current(session)
    return _sync(app)


def refresh_if_due(app: FastAPI) -> None:
    """Read the settings table now if the cache is due, in a session of its own.

    For code that runs before any route, such as the body-size check: without it a raised
    limit would be refused against the old cached one until some other request refreshed.
    A failure keeps the cached settings.
    """
    if not app.state.settings_store.due:
        return
    from jerome.mcp.context import open_session  # the MCP context imports the API

    try:
        with open_session(app) as session:
            effective_for_app(app, session)
    except Exception:  # noqa: BLE001 - the cached settings still apply
        log.exception("settings refresh failed; keeping the cached settings")


def cached_for_app(app: FastAPI) -> Settings:
    """The effective settings as last read, without touching the database."""
    store: SettingsStore = app.state.settings_store
    store.base = app.state.settings
    return _sync(app)
