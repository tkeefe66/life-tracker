"""Tests for story-related schema and helpers."""
import database


def _columns(c, table: str) -> set:
    """Return the set of column names on a table, engine-agnostic."""
    if database.USE_POSTGRES:
        c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s",
            (table,),
        )
    else:
        c.execute(f"PRAGMA table_info({table})")
    rows = c.fetchall()
    if database.USE_POSTGRES:
        return {r["column_name"] for r in rows}
    return {r["name"] for r in rows}


def test_life_log_entries_has_story_columns_sqlite(temp_db_path):
    with database._cursor() as c:
        cols = _columns(c, "life_log_entries")
    assert {"parent_id", "story_type", "why_mattered", "highlights", "extras"} <= cols


def test_life_log_entries_has_story_columns_postgres(postgres_db):
    with database._cursor() as c:
        cols = _columns(c, "life_log_entries")
    assert {"parent_id", "story_type", "why_mattered", "highlights", "extras"} <= cols


def test_migration_is_idempotent_sqlite(temp_db_path):
    # initialize_db was called once by the fixture; call again
    database.initialize_db()
    database.initialize_db()
    with database._cursor() as c:
        cols = _columns(c, "life_log_entries")
    assert "parent_id" in cols  # no exceptions raised, columns still present
