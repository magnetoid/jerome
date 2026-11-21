from sqlalchemy import delete
from sqlalchemy.orm import Session

from jerome.cli import app
from jerome.config import get_settings
from jerome.orm import ApiKey
from tests.conftest import TEST_DB_URL


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
