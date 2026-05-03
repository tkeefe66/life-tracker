"""Tests for spreadsheet backfill."""
import json
from unittest.mock import MagicMock, patch


def test_parse_spreadsheet_row_basic(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "categories": ["Vacation"],
        "description": "Vermont for July 4th with Mom and Dad",
        "location": "Vermont",
        "people": ["Mom", "Dad"],
    }))]
    from scripts.import_life_log_spreadsheet import _import_one_row
    import database as db
    _import_one_row(
        year="2025", month="07 - July",
        category="Vacation",
        description="Vermont for July 4th with Mom and Dad",
        active_categories=["Vacation"],
    )
    entries = db.get_all_life_log_entries()
    assert len(entries) == 1
    assert entries[0]["date_start"].startswith("2025-07")
    assert "Vacation" in entries[0]["categories"]
    people = db.get_all_people()
    assert {p["name"] for p in people} == {"Mom", "Dad"}
