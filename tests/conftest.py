"""Shared pytest fixtures."""
import os
import tempfile
from unittest.mock import MagicMock

# Set required env vars BEFORE any project imports so config.py doesn't raise KeyError.
# These are overridden per-test where needed (e.g., temp_db_path uses monkeypatch).
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

import pytest


@pytest.fixture
def temp_db_path(monkeypatch):
    """Fresh SQLite DB per test, isolated from local.db."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_PATH", path)
    monkeypatch.setenv("DATABASE_URL", "")  # force SQLite
    # Force re-import of config so DATABASE_PATH picks up
    import importlib
    import config
    importlib.reload(config)
    import database
    importlib.reload(database)
    database.initialize_db()
    yield path
    os.unlink(path)


@pytest.fixture
def mock_anthropic(monkeypatch):
    """Replace anthropic.Anthropic with a mock that returns canned JSON."""
    from unittest.mock import MagicMock
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="{}")]
    mock_client.messages.create.return_value = mock_response

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: mock_client)
    # Reset the cached client in ai_life_log if it was already imported
    try:
        import ai_life_log
        ai_life_log._client = None
    except ImportError:
        pass
    return mock_client


@pytest.fixture
def mock_bot():
    """Mock telegram Bot for handler tests."""
    bot = MagicMock()
    bot.send_message = MagicMock(return_value=None)
    return bot


@pytest.fixture
def postgres_db(monkeypatch):
    """Postgres-backed DB. Skipped unless TEST_POSTGRES_URL is set.

    Set TEST_POSTGRES_URL to a connection string pointing at an empty Postgres
    instance the test is allowed to wipe. Each test gets a fresh schema by
    dropping every public-schema table at fixture entry.
    """
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set; skipping Postgres test")

    import psycopg2
    conn = psycopg2.connect(url)
    conn.autocommit = True
    with conn.cursor() as c:
        c.execute(
            "DO $$ DECLARE r RECORD; BEGIN "
            "FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname='public') LOOP "
            "EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE'; "
            "END LOOP; END $$;"
        )
    conn.close()

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_PATH", "")
    import importlib
    import config
    importlib.reload(config)
    import database
    importlib.reload(database)
    database.initialize_db()
    yield url
