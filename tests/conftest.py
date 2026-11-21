import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
TEST_DB_URL = os.environ.get(
    "JEROME_TEST_DATABASE_URL",
    "postgresql+psycopg://jerome:jerome@localhost:5433/jerome_test",
)


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DB_URL)
    try:
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    except Exception as exc:  # database unreachable: skip DB-backed tests only
        eng.dispose()
        pytest.skip(f"test database unavailable: {type(exc).__name__}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB_URL.replace("%", "%%"))
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    conn = engine.connect()
    outer = conn.begin()
    sess = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield sess
    finally:
        sess.close()
        outer.rollback()
        conn.close()


@pytest.fixture
def book_factory(session):
    from jerome.orm import Book, StyleSheetRow

    def make(title="Test Book", format="docx", variant="en-US", **kw):
        book = Book(title=title, format=format, **kw)
        book.style_sheet = StyleSheetRow(
            variant=variant, quote_style="“”‘’", house_spellings={}, ignore_words=[]
        )
        session.add(book)
        session.flush()
        return book

    return make
