import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from jerome.cli import app
from jerome.config import get_settings
from jerome.orm import ApiKey, Book, Job
from tests.conftest import TEST_DB_URL
from tests.fixtures.docx_builders import all_blocks, make_docx


def test_cli_keys_create(cli_runner, engine, monkeypatch):
    monkeypatch.setenv("JEROME_DATABASE_URL", TEST_DB_URL)
    get_settings.cache_clear()
    try:
        out = cli_runner.invoke(app, ["keys", "create", "editor"])
        assert out.exit_code == 0
        assert out.stdout.strip().startswith("jk_") and len(out.stdout.split()) == 1
    finally:
        get_settings.cache_clear()
        with Session(engine) as s, s.begin():
            s.execute(delete(ApiKey).where(ApiKey.name == "editor"))


@pytest.fixture
def cli_env(monkeypatch, tmp_path, session_factory):
    """Point the CLI at the test database and a temp data dir; truncate afterwards."""
    monkeypatch.setenv("JEROME_DATABASE_URL", TEST_DB_URL)
    monkeypatch.setenv("JEROME_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def fake_registry(monkeypatch, test_settings):
    from jerome import cli
    from jerome.models import FakeProvider, Registry

    settings = test_settings.model_copy(
        update={"models": {"ai": {"provider": "fake", "model": "claude-opus-5-5"}}}
    )
    registry = Registry(settings, {"fake": FakeProvider([], token_count=1000)})
    monkeypatch.setattr(cli, "_registry", lambda: registry)


def _import(cli_runner, tmp_path) -> int:
    path = tmp_path / "m.docx"
    path.write_bytes(make_docx(["# One", "teh cat sat."]))
    out = cli_runner.invoke(app, ["import", str(path), "--variant", "en-US"])
    assert out.exit_code == 0, out.output
    return int(out.stdout.strip())


def _job_count(session_factory) -> int:
    with session_factory() as s:
        return s.scalar(select(func.count()).select_from(Job))


def test_cli_import_prints_book_id(cli_runner, cli_env, session_factory):
    book_id = _import(cli_runner, cli_env)
    with session_factory() as s:
        assert s.get(Book, book_id).format == "docx"


def test_cli_import_rejects_unsupported_file(cli_runner, cli_env, session_factory):
    path = cli_env / "notes.docx"
    path.write_bytes(b"not a document")
    out = cli_runner.invoke(app, ["import", str(path), "--variant", "en-US"])
    assert out.exit_code == 2


def test_cli_proofread_declined_creates_no_job(cli_runner, cli_env, session_factory, fake_registry):
    book_id = _import(cli_runner, cli_env)
    out = cli_runner.invoke(app, ["proofread", str(book_id), "--max-cost", "1"], input="n\n")
    assert out.exit_code == 1
    assert "Cancelled." in out.output
    assert _job_count(session_factory) == 0


def test_cli_proofread_accepted_creates_job(cli_runner, cli_env, session_factory, fake_registry):
    book_id = _import(cli_runner, cli_env)
    out = cli_runner.invoke(
        app, ["proofread", str(book_id), "--max-cost", "1", "--passes", "ai"], input="y\n"
    )
    assert out.exit_code == 0, out.output
    assert "ai" in out.output  # the per-pass estimate is printed
    assert _job_count(session_factory) == 1
    with session_factory() as s:
        job = s.scalars(select(Job)).one()
        assert job.passes == ["ai"] and job.status == "queued"
    assert out.stdout.strip().splitlines()[-1] == str(job.id)


def test_cli_export_writes_file(cli_runner, cli_env, session_factory):
    book_id = _import(cli_runner, cli_env)
    target = cli_env / "out.docx"
    out = cli_runner.invoke(
        app, ["export", str(book_id), "--format", "docx-clean", "-o", str(target)]
    )
    assert out.exit_code == 0, out.output
    assert "teh cat sat." in [b.text for b in all_blocks(target.read_bytes())]


def test_cli_db_upgrade_is_idempotent_at_head(cli_runner, cli_env, engine):
    out = cli_runner.invoke(app, ["db-upgrade"])
    assert out.exit_code == 0, out.output
