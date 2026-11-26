import enum
import signal
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

import jerome
from jerome.config import get_settings
from jerome.core.keys import create_api_key
from jerome.core.users import create_user, get_user_by_email, set_role
from jerome.db import make_engine, session_scope
from jerome.errors import JeromeError, UnsupportedDocumentError

app = typer.Typer(help="Jerome: book proofreading engine", no_args_is_help=True)
keys_app = typer.Typer(help="Manage API keys", no_args_is_help=True)
app.add_typer(keys_app, name="keys")
users_app = typer.Typer(help="Manage team users", no_args_is_help=True)
app.add_typer(users_app, name="users")


class RoleChoice(enum.StrEnum):
    super_admin = "super_admin"
    member = "member"


@keys_app.command("create")
def keys_create(name: str) -> None:
    """Create an API key and print it (shown once)."""
    engine = make_engine(get_settings().database_url)
    try:
        with session_scope(engine) as session:
            key = create_api_key(session, name)
    finally:
        engine.dispose()
    typer.echo(key)


@users_app.command("create")
def users_create(
    email: str,
    name: Annotated[str, typer.Option("--name")],
    role: Annotated[RoleChoice | None, typer.Option("--role", help="The new user's role")] = None,
) -> None:
    """Create a user and print the temporary password (shown once)."""
    engine = make_engine(get_settings().database_url)
    try:
        with session_scope(engine) as session:
            _, password = create_user(session, email, name, role=role.value if role else None)
    except JeromeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc
    finally:
        engine.dispose()
    typer.echo(password)


@users_app.command("set-role")
def users_set_role(email: str, role: RoleChoice) -> None:
    """Make a user a super_admin or a member."""
    engine = make_engine(get_settings().database_url)
    try:
        with session_scope(engine) as session:
            user = set_role(session, get_user_by_email(session, email).id, role.value)
            address, new_role, active = user.email, user.role, user.active
    except JeromeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc
    finally:
        engine.dispose()
    typer.echo(f"{address} is now {new_role}")
    if not active:
        typer.echo(f"Warning: {address} is deactivated and cannot sign in", err=True)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the REST API."""
    uvicorn.run("jerome.api.app:create_app", factory=True, host=host, port=port)


@app.command()
def worker(poll_interval: float = 1.0) -> None:
    """Run the background job worker."""
    from sqlalchemy.orm import sessionmaker

    from jerome.core.settings_store import LiveRefresh, SettingsStore
    from jerome.jobs.worker import WorkerDeps, work_forever
    from jerome.models import build_registry
    from jerome.proofread.languagetool import lt_client
    from jerome.storage import Storage

    settings = get_settings()
    engine = make_engine(settings.database_url)
    session_factory = sessionmaker(engine)
    deps = WorkerDeps(
        registry=build_registry(settings),
        lt=lt_client(settings),
        webhook_secret=settings.webhook_secret,
        # app values from the database reach the worker before each task and chunk
        refresh=LiveRefresh(SettingsStore(settings), session_factory),
        storage=Storage(settings.data_dir),
    )
    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        if stop.is_set():  # a second Ctrl-C (or SIGTERM) stops now
            raise KeyboardInterrupt
        typer.echo("stopping after the current chunk...")
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        work_forever(session_factory, deps, poll_interval, stop)
    except KeyboardInterrupt:
        typer.echo("worker stopped")
    finally:
        engine.dispose()


def _registry():
    """The model registry for CLI commands. A seam: tests replace it with a fake."""
    from jerome.models.registry import build_registry

    return build_registry(get_settings())


@app.command()
def publish(
    book_id: int,
    format: Annotated[str, typer.Option(help="epub-book or pdf-book")],
    output: Annotated[Path | None, typer.Option("-o", "--output")] = None,
) -> None:
    """Make a book's finished EPUB or PDF here, without a job, and write it to a file."""
    from jerome.publish.build import PUBLISH_FORMATS, prepare, render
    from jerome.storage import Storage

    if format not in PUBLISH_FORMATS:
        choices = " or ".join(PUBLISH_FORMATS)
        typer.echo(f"Error: unknown format {format!r}; use {choices}", err=True)
        raise typer.Exit(2)
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        with session_scope(engine) as session:
            prepared = prepare(session, Storage(settings.data_dir), book_id, format)  # type: ignore[arg-type]
        data, _media_type = render(prepared)
    except JeromeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc
    finally:
        engine.dispose()
    for warning in prepared.warnings:
        typer.echo(f"warning: {warning}", err=True)
    # "-book": never the manuscript's own name, which may sit in this directory
    target = output or Path(f"{prepared.filename_stem}-book.{prepared.extension}")
    target.write_bytes(data)
    typer.echo(str(target))


@app.command("db-upgrade")
def db_upgrade() -> None:
    """Apply database migrations (alembic upgrade head)."""
    from alembic import command
    from alembic.config import Config

    root = Path(jerome.__file__).resolve().parent.parent
    cfg = Config()
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    command.upgrade(cfg, "head")


@app.command("import")
def import_cmd(
    path: Path,
    variant: Annotated[str, typer.Option(help="en-US, en-GB, de-DE, de-AT or de-CH")],
    local_only: Annotated[
        bool, typer.Option(help="Never send this book to a remote model")
    ] = False,
    ocr: Annotated[
        str | None,
        typer.Option(
            help="For a PDF: read scanned pages with ai or tesseract (default: ai if set up)"
        ),
    ] = None,
) -> None:
    """Import a DOCX, EPUB or PDF and print the new book id. A PDF is read in the background."""
    from typing import get_args

    from jerome.core.books import import_book
    from jerome.storage import Storage
    from jerome.types import Variant

    if variant not in get_args(Variant):
        typer.echo(
            f"Error: unknown variant {variant!r}; use one of {', '.join(get_args(Variant))}",
            err=True,
        )
        raise typer.Exit(2)

    if ocr not in (None, "ai", "tesseract"):
        typer.echo("Error: --ocr must be ai or tesseract", err=True)
        raise typer.Exit(2)

    settings = get_settings()
    from jerome.api.routes_books import is_pdf

    with path.open("rb") as f:
        head = f.read(5)
    if is_pdf(path.name, head):
        _import_pdf(path, variant, local_only, ocr, settings)
        return
    engine = make_engine(settings.database_url)
    try:
        with session_scope(engine) as session:
            book = import_book(
                session,
                Storage(settings.data_dir),
                filename=path.name,
                data=path.read_bytes(),
                variant=variant,  # type: ignore[arg-type]
                local_only=local_only,
            )
            book_id = book.id
    except UnsupportedDocumentError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc
    finally:
        engine.dispose()
    typer.echo(book_id)


def _import_pdf(path: Path, variant: str, local_only: bool, ocr: str | None, settings) -> None:
    from sqlalchemy import select

    from jerome.api.routes_books import default_ocr_engine
    from jerome.core.auto_apply import DEFAULT_POLICY
    from jerome.core.books import start_pdf_import
    from jerome.orm import Job
    from jerome.storage import Storage

    if ocr == "ai" and local_only:
        typer.echo("Error: a local-only book cannot be read with AI; use --ocr tesseract", err=True)
        raise typer.Exit(2)
    engine_name = ocr or default_ocr_engine(_registry(), local_only)
    engine = make_engine(settings.database_url)
    try:
        with session_scope(engine) as session:
            book = start_pdf_import(
                session,
                Storage(settings.data_dir),
                filename=path.name,
                data=path.read_bytes(),
                variant=variant,  # type: ignore[arg-type]
                title=None,
                local_only=local_only,
                auto_apply=DEFAULT_POLICY,
                engine=engine_name,  # type: ignore[arg-type]
                reocr=True,
                work="proofread",
                target_variant=None,
                translated_title=None,
                actor="cli",
            )
            book_id = book.id
            job_id = session.scalar(
                select(Job.id).where(Job.book_id == book_id, Job.type == "import")
            )
    except UnsupportedDocumentError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc
    finally:
        engine.dispose()
    typer.echo(book_id)
    typer.echo(f"importing in the background (job {job_id})")


@app.command()
def proofread(
    book_id: int,
    max_cost: Annotated[str, typer.Option(help="Cost cap in USD")],
    passes: Annotated[
        list[str] | None, typer.Option("--passes", help="languagetool, ai, consistency")
    ] = None,
) -> None:
    """Show the cost estimate, ask for confirmation, then queue a proofreading job."""
    from jerome.jobs.estimate import ALL_PASSES, estimate_job
    from jerome.jobs.service import create_job

    try:
        cap = Decimal(max_cost)
    except InvalidOperation as exc:
        raise typer.BadParameter(f"{max_cost!r} is not a number", param_hint="--max-cost") from exc
    chosen = tuple(passes) if passes else tuple(ALL_PASSES)
    if unknown := [p for p in chosen if p not in ALL_PASSES]:
        typer.echo(
            f"Error: unknown pass {', '.join(map(repr, unknown))}; use {', '.join(ALL_PASSES)}",
            err=True,
        )
        raise typer.Exit(2)
    engine = make_engine(get_settings().database_url)
    try:
        with session_scope(engine) as session:
            registry = _registry()
            est = estimate_job(session, registry, book_id, passes=chosen)  # type: ignore[arg-type]
            typer.echo(f"Estimated cost: ${est.total.low} to ${est.total.high}")
            for name, rng in est.per_pass.items():
                typer.echo(f"  {name}: ${rng.low} to ${rng.high}")
            if not typer.confirm("Create the job?", default=False):
                typer.echo("Cancelled.")
                raise typer.Exit(1)
            job = create_job(
                session,
                registry,
                book_id,
                passes=chosen,  # type: ignore[arg-type]
                max_cost=cap,
                callback_url=None,
                actor="cli",
            )
            job_id = job.id
    except JeromeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc
    finally:
        engine.dispose()
    typer.echo(job_id)


@app.command()
def export(
    book_id: int,
    format: Annotated[str, typer.Option(help="epub-clean, docx-clean, docx-tracked, docx-review")],
    output: Annotated[Path, typer.Option("-o", "--output")],
) -> None:
    """Export a book to a file."""
    from jerome.core.exports import export_book
    from jerome.storage import Storage

    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        with session_scope(engine) as session:
            result = export_book(
                session,
                Storage(settings.data_dir),
                book_id,
                format,  # type: ignore[arg-type]
                actor="cli",
            )
    except JeromeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc
    finally:
        engine.dispose()
    output.write_bytes(result.data)
    typer.echo(str(output))
