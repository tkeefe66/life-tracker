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


def test_get_pending_stories_with_children_sqlite(temp_db_path):
    _run_pending_stories_check()


def test_get_pending_stories_with_children_postgres(postgres_db):
    _run_pending_stories_check()


def _run_pending_stories_check():
    parent_id = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    child_id = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Ski day",
        location="Killington", source="calendar", source_id="evt-1",
    )
    database.assign_child_to_story(child_id, parent_id)
    singleton_id = database.save_story_parent(
        date_start="2024-04-01", date_end=None,
        story_type="other", summary="Random", highlights=[], location=None,
    )
    stories = database.get_pending_stories_with_children()
    by_id = {s["id"]: s for s in stories}
    assert parent_id in by_id and singleton_id in by_id
    assert child_id not in by_id
    assert [c["id"] for c in by_id[parent_id]["children"]] == [child_id]
    assert by_id[singleton_id]["children"] == []


def test_confirm_story_flips_parent_and_children(temp_db_path):
    parent_id = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    child_id = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Ski",
        location="VT", source="calendar", source_id="evt-x",
    )
    database.assign_child_to_story(child_id, parent_id)

    database.confirm_story(parent_id)

    parent = database.get_life_log_entry(parent_id)
    child = database.get_life_log_entry(child_id)
    assert parent["status"] == "confirmed"
    assert child["status"] == "confirmed"


def test_dismiss_story_flips_parent_and_children(temp_db_path):
    parent_id = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    child_id = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Ski",
        location="VT", source="calendar", source_id="evt-y",
    )
    database.assign_child_to_story(child_id, parent_id)

    database.dismiss_story(parent_id)

    parent = database.get_life_log_entry(parent_id)
    child = database.get_life_log_entry(child_id)
    assert parent["status"] == "dismissed"
    assert child["status"] == "dismissed"


def test_drop_event_from_story_returns_child_to_unclustered(temp_db_path):
    parent_id = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    child_id = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Ski",
        location="VT", source="calendar", source_id="evt-z",
    )
    database.assign_child_to_story(child_id, parent_id)

    database.drop_event_from_story(child_id)

    child = database.get_life_log_entry(child_id)
    assert child["parent_id"] is None
    assert child["status"] == "proposed"  # back in the unclustered pool


def test_update_story_metadata_writes_all_fields(temp_db_path):
    parent_id = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=["one"], location="VT",
    )
    database.update_story_metadata(
        parent_id,
        summary="Vermont ski trip — knee surgery comeback",
        why_mattered="First trip after surgery — felt like reclaiming something.",
        highlights=["JFK→BTV flight", "Pow day at Killington"],
        extras={"travel_mode": "flight", "who_came": ["Sarah", "Tom"]},
    )
    e = database.get_life_log_entry(parent_id)
    assert "knee surgery" in e["description"]
    assert "reclaiming" in e["why_mattered"]
    assert e["highlights"] == ["JFK→BTV flight", "Pow day at Killington"]
    assert e["extras"]["travel_mode"] == "flight"
