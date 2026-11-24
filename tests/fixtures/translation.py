"""Shared helpers for tests of translated books: a registry on a fake provider, worker deps,
and running the worker until nothing is left."""

import httpx

from jerome.jobs.worker import WorkerDeps, claim_task, run_task
from jerome.models import Registry
from jerome.proofread.languagetool import LanguageToolClient
from tests.translate.fakes import echo_translator


def registry_for(test_settings, provider, *, ai=False):
    models = {"translate": {"provider": "fake", "model": "claude-opus-5-5"}}
    if ai:
        models["ai"] = {"provider": "fake", "model": "claude-opus-5-5"}
    return Registry(test_settings.model_copy(update={"models": models}), {"fake": provider})


def quiet_languagetool() -> LanguageToolClient:
    """A LanguageTool client whose server finds nothing in any text."""
    http = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"matches": []}))
    )
    return LanguageToolClient("http://lt.test", http)


def deps_for(registry, lt: LanguageToolClient | None = None):
    """Worker deps; without ``lt`` the LanguageTool client has no server behind it."""
    return WorkerDeps(registry, lt or LanguageToolClient("http://lt.test", httpx.Client()), None)


def echo(n=200, prefix="DE "):
    return [echo_translator(prefix)] * n


def drain(sf, deps, stop=None):
    while True:
        with sf() as s:
            task_id = claim_task(s)
        if task_id is None:
            return
        run_task(sf, task_id, deps, stop)
        if stop is not None and stop.is_set():  # the worker stops, like work_forever
            return
