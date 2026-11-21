import typer
import uvicorn

from jerome.config import get_settings
from jerome.core.keys import create_api_key
from jerome.db import make_engine, session_scope

app = typer.Typer(help="Jerome: book proofreading engine", no_args_is_help=True)
keys_app = typer.Typer(help="Manage API keys", no_args_is_help=True)
app.add_typer(keys_app, name="keys")


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


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the REST API."""
    uvicorn.run("jerome.api.app:create_app", factory=True, host=host, port=port)
