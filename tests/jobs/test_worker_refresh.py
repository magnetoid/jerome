import logging
import threading
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from jerome.config import Settings
from jerome.core.settings_changes import set_secret, set_value
from jerome.core.settings_store import LiveRefresh, SettingsStore
from jerome.jobs.worker import WorkerDeps, run_task, work_forever
from jerome.models import FakeProvider, ModelOutputError, Registry, build_registry
from jerome.orm import Job, ModelUsage
from jerome.proofread.languagetool import LanguageToolClient
from tests.jobs.test_worker import LT, build, job, reply

KEY = Fernet.generate_key().decode()
NEW_PRICE = {"input": 1, "output": 5, "cache_read": 0.1, "cache_write": 1.25}


@pytest.fixture
def lt_route(respx_mock):
    return respx_mock.post(f"{LT}/v2/check").respond(json={"matches": []})


def _registry(test_settings, provider, model):
    settings = test_settings.model_copy(
        update={
            "models": {"ai": {"provider": "fake", "model": model}},
            "prices": {"claude-sonnet-5-5": NEW_PRICE},
        }
    )
    return Registry(settings, {"fake": provider})


def test_refresh_runs_before_each_chunk(session_factory, test_settings, lt_route):
    ids = build(session_factory, [[" ".join(["word"] * 1500)] * 2], passes=["languagetool", "ai"])
    provider = FakeProvider([reply(), reply()])
    old = _registry(test_settings, provider, "claude-opus-5-5")
    new = _registry(test_settings, provider, "claude-sonnet-5-5")
    stop = threading.Event()
    calls = []

    def refresh(deps):
        calls.append(deps)
        # once for the task, once for the first chunk; the model changes before chunk two
        return replace(deps, registry=new if len(calls) > 2 else old)

    def now():
        with session_factory() as s:
            if s.get(Job, ids.job_id).status == "completed":
                stop.set()
        return datetime.now(UTC)

    deps = WorkerDeps(old, LanguageToolClient(LT), None, now=now, refresh=refresh)
    t = threading.Thread(target=work_forever, args=(session_factory, deps, 0.01, stop))
    t.start()
    t.join(timeout=20)
    stop.set()
    assert not t.is_alive()
    assert job(session_factory, ids.job_id).status == "completed"
    assert len(calls) == 3  # once per task, then once per chunk
    with session_factory() as s:
        models = s.scalars(select(ModelUsage.model).order_by(ModelUsage.id)).all()
    assert models == ["claude-opus-5-5", "claude-sonnet-5-5"]


def test_failed_refresh_keeps_the_old_deps(session_factory, test_settings, lt_route, caplog):
    ids = build(session_factory, [["One."]], passes=["languagetool", "ai"])
    provider = FakeProvider([reply()])
    stop = threading.Event()

    def refresh(deps):
        raise RuntimeError("settings unavailable")

    deps = WorkerDeps(
        _registry(test_settings, provider, "claude-opus-5-5"),
        LanguageToolClient(LT),
        None,
        refresh=refresh,
    )
    with caplog.at_level(logging.ERROR):
        run_task(session_factory, ids.task_id, deps, stop)
    assert job(session_factory, ids.job_id).status == "completed"
    assert "settings refresh failed" in caplog.text
    assert "settings unavailable" not in caplog.text  # only the error type is logged


def test_live_refresh_picks_up_webhook_secret(session_factory):
    store = SettingsStore(Settings(_env_file=None, secret_key=KEY))
    deps = WorkerDeps(
        registry=build_registry(store.base), lt=LanguageToolClient(LT), webhook_secret=None
    )
    with session_factory() as s:
        set_secret(s, "webhook_secret", "w1", actor="a", base=store.base)
        s.commit()
    store.invalidate()
    fresh = LiveRefresh(store, session_factory)(deps)
    assert fresh.webhook_secret == "w1" and fresh.registry is deps.registry
    assert fresh.lt is deps.lt


def test_live_refresh_rebuilds_registry_and_lt_when_their_inputs_change(session_factory):
    store = SettingsStore(Settings(_env_file=None, secret_key=KEY))
    deps = WorkerDeps(
        registry=build_registry(store.base), lt=LanguageToolClient(LT), webhook_secret=None
    )
    refresh = LiveRefresh(store, session_factory)
    with session_factory() as s:
        set_value(
            s,
            "models.ai",
            {"provider": "anthropic", "model": "claude-sonnet-5-5"},
            actor="a",
            base=store.base,
        )
        set_value(s, "languagetool_url", "http://lt.example.org", actor="a", base=store.base)
        s.commit()
    store.invalidate()
    fresh = refresh(deps)
    assert fresh.registry is not deps.registry and fresh.lt is not deps.lt
    assert fresh.registry.for_pass("ai", local_only=False)[1].model == "claude-sonnet-5-5"
    # deps handed in again (say, the worker loop's older copy) get the rebuilt objects too
    again = refresh(deps)
    assert again.registry is fresh.registry and again.lt is fresh.lt


def test_failure_webhook_is_signed_with_the_refreshed_secret(
    session_factory, test_settings, lt_route, monkeypatch
):
    from tests.jobs.test_worker import HOOK

    ids = build(session_factory, [[" ".join(["word"] * 1500)]], callback_url=HOOK)
    sent = []
    monkeypatch.setattr(
        "jerome.jobs.worker.send_webhook", lambda url, event, payload, secret: sent.append(secret)
    )
    provider = FakeProvider([RuntimeError("x")] * 3)
    registry = _registry(test_settings, provider, "claude-opus-5-5")
    deps = WorkerDeps(
        registry,
        LanguageToolClient(LT),
        "old-secret",
        refresh=lambda d: replace(d, webhook_secret="new-secret"),
    )
    for _ in range(3):
        run_task(session_factory, ids.task_id, deps)
    assert job(session_factory, ids.job_id).status == "failed"
    assert sent == ["new-secret"]


def test_failed_billed_call_is_priced_with_the_settings_it_ran_under(
    session_factory, test_settings, lt_route
):
    from tests.jobs.test_worker import BIG, BilledErrorProvider, billed

    ids = build(session_factory, [[BIG]])
    error = billed(ModelOutputError)
    error.model = "custom-old"
    provider = BilledErrorProvider(error)
    old_price = {"input": 10, "output": 20, "cache_read": 1, "cache_write": 12.5}
    old = Registry(
        test_settings.model_copy(
            update={
                "models": {"ai": {"provider": "fake", "model": "custom-old"}},
                "prices": {"custom-old": old_price},
            }
        ),
        {"fake": provider},
    )
    new = _registry(test_settings, provider, "claude-sonnet-5-5")  # no price for custom-old
    calls = []

    def refresh(deps):
        calls.append(1)
        # the chunk runs under the old settings; they change before the failure is recorded
        return replace(deps, registry=old if len(calls) == 1 else new, webhook_secret="new")

    deps = WorkerDeps(old, LanguageToolClient(LT), None, refresh=refresh)
    run_task(session_factory, ids.task_id, deps)
    assert len(calls) == 2
    # 1000 input and 150 output tokens at the old custom price
    assert job(session_factory, ids.job_id).cost_actual == Decimal("0.0130")
