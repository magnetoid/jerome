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


def test_users_create_prints_a_password_once(cli_runner, cli_env, session_factory):
    from jerome.core.users import authenticate

    out = cli_runner.invoke(app, ["users", "create", "ana@x.org", "--name", "Ana"])
    assert out.exit_code == 0, out.output
    assert len(out.stdout.strip().splitlines()) == 1
    with session_factory() as s:
        assert authenticate(s, "ana@x.org", out.stdout.strip()) is not None


def test_users_create_with_a_role_and_set_role(cli_runner, cli_env, session_factory):
    from jerome.core.users import get_user_by_email

    out = cli_runner.invoke(app, ["users", "create", "b@x.org", "--name", "B", "--role", "member"])
    assert out.exit_code == 0, out.output
    with session_factory() as s:
        assert get_user_by_email(s, "b@x.org").role == "member"

    out = cli_runner.invoke(app, ["users", "set-role", "b@x.org", "super_admin"])
    assert out.exit_code == 0, out.output
    assert out.stdout.strip() == "b@x.org is now super_admin"
    with session_factory() as s:
        assert get_user_by_email(s, "b@x.org").role == "super_admin"


def test_users_set_role_errors_exit_2(cli_runner, cli_env):
    out = cli_runner.invoke(app, ["users", "set-role", "nobody@x.org", "member"])
    assert out.exit_code == 2 and "Error:" in out.stderr

    assert cli_runner.invoke(app, ["users", "create", "a@x.org", "--name", "A"]).exit_code == 0
    out = cli_runner.invoke(app, ["users", "set-role", "a@x.org", "member"])
    assert out.exit_code == 2 and "last active super admin" in out.stderr

    out = cli_runner.invoke(app, ["users", "create", "c@x.org", "--name", "C", "--role", "owner"])
    assert out.exit_code == 2
    out = cli_runner.invoke(app, ["users", "set-role", "a@x.org", "owner"])
    assert out.exit_code == 2


def test_users_role_choices_are_listed_in_help(cli_runner):
    for args in (["users", "create", "--help"], ["users", "set-role", "--help"]):
        out = cli_runner.invoke(app, args)
        assert out.exit_code == 0
        assert "super_admin" in out.output and "member" in out.output


def test_set_role_on_a_deactivated_user_warns(cli_runner, cli_env, session_factory):
    from jerome.core.users import get_user_by_email, update_user

    assert cli_runner.invoke(app, ["users", "create", "a@x.org", "--name", "A"]).exit_code == 0
    assert cli_runner.invoke(app, ["users", "create", "b@x.org", "--name", "B"]).exit_code == 0
    with session_factory() as s, s.begin():
        update_user(s, get_user_by_email(s, "b@x.org").id, active=False)
    out = cli_runner.invoke(app, ["users", "set-role", "b@x.org", "super_admin"])
    assert out.exit_code == 0, out.output
    assert out.stdout.strip() == "b@x.org is now super_admin"
    assert "b@x.org is deactivated and cannot sign in" in out.stderr


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


def test_cli_import_rejects_unknown_variant(cli_runner, cli_env, session_factory):
    path = cli_env / "m.docx"
    path.write_bytes(make_docx(["teh cat sat."]))
    out = cli_runner.invoke(app, ["import", str(path), "--variant", "fr-FR"])
    assert out.exit_code == 2
    assert "unknown variant" in out.output and "Traceback" not in out.output
    assert out.exception is None or isinstance(out.exception, SystemExit)


def test_cli_proofread_rejects_unknown_pass(cli_runner, cli_env, session_factory, fake_registry):
    book_id = _import(cli_runner, cli_env)
    out = cli_runner.invoke(
        app, ["proofread", str(book_id), "--max-cost", "1", "--passes", "magic"], input="y\n"
    )
    assert out.exit_code == 2
    assert "unknown pass" in out.output and "languagetool" in out.output
    assert _job_count(session_factory) == 0


def test_cli_publish_writes_file(cli_runner, cli_env, session_factory):
    import io
    import zipfile

    book_id = _import(cli_runner, cli_env)
    target = cli_env / "out.epub"
    with session_factory() as s:
        s.get(Book, book_id).authors = []  # the builder's file names an author
        s.commit()
    refused = cli_runner.invoke(
        app, ["publish", str(book_id), "--format", "epub-book", "-o", str(target)]
    )
    assert refused.exit_code == 2 and "Set the author" in refused.output
    with session_factory() as s:
        s.get(Book, book_id).authors = ["Ann Author"]
        s.commit()
    out = cli_runner.invoke(
        app, ["publish", str(book_id), "--format", "epub-book", "-o", str(target)]
    )
    assert out.exit_code == 0, out.output
    assert "warning: The book has no cover" in out.output
    assert zipfile.ZipFile(io.BytesIO(target.read_bytes())).testzip() is None
    assert _job_count(session_factory) == 0  # made inline, with no job


def test_cli_publish_default_name_never_overwrites_the_manuscript(
    cli_runner, cli_env, session_factory, monkeypatch
):
    book_id = _import(cli_runner, cli_env)  # m.docx
    monkeypatch.chdir(cli_env)
    (cli_env / "m.epub").write_bytes(b"the original manuscript")
    out = cli_runner.invoke(app, ["publish", str(book_id), "--format", "epub-book"])
    assert out.exit_code == 0, out.output
    assert (cli_env / "m.epub").read_bytes() == b"the original manuscript"
    assert (cli_env / "m-book.epub").read_bytes()[:2] == b"PK"


def test_cli_imports_a_pdf(cli_runner, cli_env, session_factory):
    import io

    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 200)
    buf = io.BytesIO()
    doc.save(buf)
    path = cli_env / "scan.pdf"
    path.write_bytes(buf.getvalue())
    out = cli_runner.invoke(app, ["import", str(path), "--variant", "en-GB", "--ocr", "tesseract"])
    assert out.exit_code == 0, out.output
    lines = out.stdout.strip().splitlines()
    with session_factory() as s:
        book = s.get(Book, int(lines[0]))
        job = s.scalar(select(Job).where(Job.book_id == book.id))
        assert book.import_status == "importing"
        assert (job.type, job.passes) == ("import", ["tesseract"])
    assert lines[1] == f"importing in the background (job {job.id})"
