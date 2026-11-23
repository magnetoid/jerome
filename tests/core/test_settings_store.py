from contextlib import nullcontext

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from jerome.config import Settings
from jerome.core.settings_changes import set_value
from jerome.core.settings_store import SettingsStore, registry_inputs
from jerome.proofread.text import TextLimits


class Broken:
    def begin_nested(self):
        return nullcontext()

    def scalars(self, *a, **k):
        raise OperationalError("x", {}, Exception())


def test_store_sees_change_after_interval(session_factory):
    now = [0.0]
    store = SettingsStore(Settings(_env_file=None), clock=lambda: now[0])
    with session_factory() as s:
        assert store.current(s).settings.text_rpm == 30 and store.version == 0
        set_value(s, "text_rpm", 9, actor="a", base=store.base)
        s.commit()
        assert store.current(s).settings.text_rpm == 30  # not re-read yet
        now[0] = 5.1
        assert store.current(s).settings.text_rpm == 9 and store.version == 1
        now[0] = 11.0
        store.current(s)
        assert store.version == 1  # same rows: no new version


def test_invalidate_reads_at_once(session_factory):
    now = [0.0]
    store = SettingsStore(Settings(_env_file=None), clock=lambda: now[0])
    with session_factory() as s:
        assert store.current(s).settings.text_rpm == 30 and store.version == 0
        set_value(s, "text_rpm", 9, actor="a", base=store.base)
        s.commit()
        assert store.current(s).settings.text_rpm == 30  # not re-read yet
        store.invalidate()
        assert store.current(s).settings.text_rpm == 9 and store.version == 1
        store.current(s)
        assert store.version == 1


def test_store_keeps_last_settings_when_read_fails():
    store = SettingsStore(Settings(_env_file=None, text_rpm=40))
    store.invalidate()
    assert store.current(Broken()).settings.text_rpm == 40


def test_failed_read_is_retried_on_the_next_call(session_factory):
    now = [0.0]
    store = SettingsStore(Settings(_env_file=None), clock=lambda: now[0])
    with session_factory() as s:
        store.current(s)
        set_value(s, "text_rpm", 9, actor="a", base=store.base)
        s.commit()
        now[0] = 6.0
        assert store.current(Broken()).settings.text_rpm == 30
        now[0] = 6.1  # well inside a fresh interval, but the failure left it due
        assert store.current(s).settings.text_rpm == 9


def test_base_swap_recomputes_without_db():
    store = SettingsStore(Settings(_env_file=None))
    store.base = Settings(_env_file=None, cookie_secure=False)
    assert store.cached().settings.cookie_secure is False and store.version == 1


def test_same_base_assignment_does_nothing():
    base = Settings(_env_file=None)
    store = SettingsStore(base)
    store.base = base
    assert store.version == 0


def test_registry_inputs_compare_by_value():
    a = Settings(_env_file=None, anthropic_api_key="k1")
    b = Settings(_env_file=None, anthropic_api_key="k1")
    assert registry_inputs(a) == registry_inputs(b)
    assert registry_inputs(a) != registry_inputs(Settings(_env_file=None, anthropic_api_key="k2"))
    assert "k1" not in repr(registry_inputs(a))


def test_registry_inputs_hide_and_track_the_compat_key():
    def make(key):
        return Settings(
            _env_file=None, models={"ai": {"provider": "openai_compat", "api_key": key}}
        )

    assert "sentinel" not in repr(registry_inputs(make("sentinel")))
    assert registry_inputs(make("sentinel")) == registry_inputs(make("sentinel"))
    assert registry_inputs(make("sentinel")) != registry_inputs(make("other"))


def test_invalidate_during_a_read_is_not_lost(monkeypatch):
    import jerome.core.settings_store as mod

    store = SettingsStore(Settings(_env_file=None))

    class Sess:
        def begin_nested(self):
            return nullcontext()

    def slow_read(session):
        store.invalidate()  # another thread asks for a re-read mid-read
        return []

    monkeypatch.setattr(mod, "load_rows", slow_read)
    store.current(Sess())
    assert store._due is True


def test_failed_read_leaves_the_callers_session_usable(session, monkeypatch):
    import jerome.core.settings_store as mod

    monkeypatch.setattr(mod, "load_rows", lambda s: s.execute(text("select * from no_such_table")))
    store = SettingsStore(Settings(_env_file=None, text_rpm=40))
    assert store.current(session).settings.text_rpm == 40
    assert session.execute(text("select 1")).scalar() == 1


def test_set_rpm_keeps_counters():
    limits = TextLimits(2, clock=lambda: 0.0)
    assert limits.take("k") is None and limits.take("k") is None
    limits.set_rpm(10)
    assert limits.take("k") is not None  # the spent bucket was not refilled by the change


def test_base_swap_after_session_closed_uses_saved_rows(session_factory):
    store = SettingsStore(Settings(_env_file=None))
    with session_factory() as s:
        set_value(s, "text_rpm", 9, actor="a", base=store.base)
        s.commit()
        store.invalidate()
        store.current(s)
    store.base = Settings(_env_file=None, cookie_secure=False)
    assert store.cached().settings.text_rpm == 9
