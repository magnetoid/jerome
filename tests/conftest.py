import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
TEST_DB_URL = os.environ.get(
    "JEROME_TEST_DATABASE_URL",
    "postgresql+psycopg://jerome:jerome@localhost:5433/jerome_test",
)


def alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB_URL.replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def engine():
    name = make_url(TEST_DB_URL).database or ""
    if not name.endswith("_test"):  # the fixture drops the whole schema: never a real database
        pytest.fail(
            f"refusing to DROP SCHEMA on database {name!r}: the test database name must end "
            "with '_test' (set JEROME_TEST_DATABASE_URL)",
            pytrace=False,
        )
    eng = create_engine(TEST_DB_URL)
    try:
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    except Exception as exc:  # database unreachable: skip DB-backed tests only
        eng.dispose()
        pytest.skip(f"test database unavailable: {type(exc).__name__}")
    command.upgrade(alembic_config(), "head")
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
def session_factory(engine):
    """Real commits, for code that opens its own sessions (the worker). Every application
    table is truncated afterwards, so savepoint-based tests are unaffected."""
    from jerome.orm import Base

    yield sessionmaker(engine)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


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


@pytest.fixture
def tmp_storage(tmp_path):
    from jerome.storage import Storage

    return Storage(tmp_path / "data")


@pytest.fixture
def imported_docx_book(session, tmp_storage):
    from jerome.core.books import import_book
    from tests.fixtures.docx_builders import make_docx

    return import_book(
        session,
        tmp_storage,
        filename="manuscript.docx",
        data=make_docx(["# One", "Teh first paragraph.", "Second one here."]),
        variant="en-GB",
    )


@pytest.fixture
def test_settings(tmp_path):
    from jerome.config import Settings

    return Settings(database_url=TEST_DB_URL, data_dir=tmp_path / "data")


@pytest.fixture
def client(session, tmp_storage, test_settings):
    from fastapi.testclient import TestClient

    from jerome.api.app import create_app
    from jerome.api.deps import get_session, get_storage

    app = create_app(test_settings)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_storage] = lambda: tmp_storage
    with TestClient(app) as c:
        yield c


@pytest.fixture
def web_client(client):
    """The API client over https, so the Secure session cookie is sent back like a browser."""
    client.base_url = "https://testserver"
    return client


@pytest.fixture
def auth(session):
    from jerome.core.keys import create_api_key

    return {"Authorization": f"Bearer {create_api_key(session, 'tester')}"}


@pytest.fixture
def book_id(imported_docx_book):
    return imported_docx_book.id


@pytest.fixture
def cli_runner():
    from typer.testing import CliRunner

    return CliRunner()


@pytest.fixture
def block(session, tmp_storage):
    from jerome.core.books import import_book
    from jerome.orm import Block
    from tests.fixtures.docx_builders import make_docx

    book = import_book(
        session,
        tmp_storage,
        filename="one.docx",
        data=make_docx(["teh cat sat"]),
        variant="en-US",
    )
    return session.query(Block).filter_by(book_id=book.id).one()


@pytest.fixture
def docx_book_with_decisions(session, tmp_storage):
    from jerome.core.books import import_book
    from jerome.core.suggestions import decide
    from jerome.orm import Block
    from tests.fixtures.docx_builders import make_docx
    from tests.fixtures.suggestions import add_one

    book = import_book(
        session,
        tmp_storage,
        filename="draft.docx",
        data=make_docx(["teh cat sat", "an open suggestion stays teh"]),
        variant="en-US",
    )
    first, second = session.query(Block).filter_by(book_id=book.id).order_by(Block.id).all()
    decide(session, add_one(session, first, 0, 3, "teh", "the").id, "accept", actor="a")
    s = add_one(session, first, 8, 11, "sat", "sits")
    decide(session, s.id, "edit", actor="a", replacement="sat down")
    add_one(session, second, 25, 28, "teh", "the")
    return book


@pytest.fixture
def epub_book_with_decisions(session, tmp_storage):
    from jerome.core.books import import_book
    from jerome.core.suggestions import decide
    from jerome.orm import Block
    from tests.fixtures.epub_builders import make_epub
    from tests.fixtures.suggestions import add_one

    book = import_book(
        session,
        tmp_storage,
        filename="novel.epub",
        data=make_epub([("Chapter One", ["teh cat sat", "an open suggestion stays teh"])]),
        variant="en-US",
    )
    blocks = session.query(Block).filter_by(book_id=book.id).order_by(Block.id).all()
    para, other = blocks[1], blocks[2]
    decide(session, add_one(session, para, 0, 3, "teh", "the").id, "accept", actor="a")
    add_one(session, other, 25, 28, "teh", "the")
    return book


@pytest.fixture
def epub_book_id(epub_book_with_decisions):
    return epub_book_with_decisions.id


def _make_registry(test_settings, provider):
    from jerome.models import Registry

    settings = test_settings.model_copy(
        update={"models": {"ai": {"provider": "fake", "model": "claude-opus-5-5"}}}
    )
    return Registry(settings, {"fake": provider})


@pytest.fixture
def registry(test_settings):
    from jerome.models import FakeProvider

    return _make_registry(test_settings, FakeProvider([], token_count=1000))


@pytest.fixture
def registry_fake_counts_1000(registry):
    return registry


@pytest.fixture
def registry_no_count(test_settings):
    from jerome.models import FakeProvider

    return _make_registry(test_settings, FakeProvider([], token_count=None))


@pytest.fixture
def registry_local_only_unavailable(test_settings):
    """The only AI provider is not local, so a local_only book has no AI pass."""
    from jerome.models import FakeProvider

    return _make_registry(test_settings, FakeProvider([], local=False, token_count=1000))


@pytest.fixture
def add_chapter(session):
    from jerome.orm import Block, Chapter

    def make(book, ordinal, texts, *, proofread=True, title=None):
        chapter = Chapter(
            book_id=book.id, ordinal=ordinal, title=title or f"Ch {ordinal}", proofread=proofread
        )
        session.add(chapter)
        session.flush()
        for i, body in enumerate(texts):
            session.add(
                Block(
                    chapter_id=chapter.id,
                    book_id=book.id,
                    ordinal=i,
                    kind="paragraph",
                    text=body,
                    hash=f"h{chapter.id}-{i}",
                )
            )
        session.flush()
        return chapter

    return make


@pytest.fixture
def book(book_factory, add_chapter):
    b = book_factory()
    add_chapter(b, 0, ["Some short text here."])
    return b


@pytest.fixture
def book_6000_words(book_factory, add_chapter):
    b = book_factory()
    add_chapter(b, 0, [" ".join(["word"] * 1000)] * 6)
    return b


@pytest.fixture
def book_3_chapters(book_factory, add_chapter):
    b = book_factory()
    for i in range(3):
        add_chapter(b, i, [f"Chapter {i} text."])
    return b


@pytest.fixture
def book_with_front_matter(book_factory, add_chapter):
    b = book_factory()
    add_chapter(b, 0, [" ".join(["front"] * 100)], proofread=False, title="Front matter")
    add_chapter(b, 1, ["Body text."])
    return b


# --- WeasyPrint (PDF tests) ---

_WEASY: list = []  # [module] or [None, reason], tried once: a second failed import can crash


def _load():
    if not _WEASY:
        try:
            import weasyprint

            _WEASY.append(weasyprint)
        except (OSError, ImportError) as exc:
            _WEASY.extend([None, str(exc).splitlines()[0]])
    return _WEASY


@pytest.fixture
def weasy():
    """WeasyPrint, or a skip where its system libraries (Pango) are missing.

    Run these tests in the Docker test image: see docker/test.Dockerfile."""
    loaded = _load()
    if loaded[0] is None:
        pytest.skip(f"WeasyPrint needs Pango; run in the Docker test image ({loaded[1]})")
    return loaded[0]
