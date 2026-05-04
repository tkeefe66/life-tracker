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


def test_save_story_parent_returns_id(temp_db_path):
    sid = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip",
        summary="Vermont ski trip with Sarah and Tom",
        highlights=["JFK→BTV flight", "Skied Killington"],
        location="Killington, VT",
    )
    assert isinstance(sid, int) and sid > 0

    entry = database.get_life_log_entry(sid)
    assert entry["status"] == "proposed"
    assert entry["parent_id"] is None
    assert entry["story_type"] == "trip"
    assert entry["description"] == "Vermont ski trip with Sarah and Tom"
    assert entry["highlights"] == ["JFK→BTV flight", "Skied Killington"]
    assert entry["date_start"] == "2024-03-12"


def test_assign_child_to_story(temp_db_path):
    parent_id = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    child_id = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Skiing day",
        location="Killington", source="calendar", source_id="evt-1",
    )
    database.assign_child_to_story(child_id, parent_id)

    child = database.get_life_log_entry(child_id)
    assert child["parent_id"] == parent_id
    assert child["status"] == "proposed"
