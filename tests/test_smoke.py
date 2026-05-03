"""Smoke test — DB and config load successfully."""

def test_database_initializes(temp_db_path):
    import database
    state = database.get_state()
    assert state.get("state") == "idle"
