"""Tests for Life Log Sheets sync (mocked gspread)."""
from unittest.mock import MagicMock


def test_build_life_log_rows_basic():
    from google_sheets import _build_life_log_rows
    entries = [
        {
            "id": 1, "date_start": "2025-05-05", "date_end": "2025-05-12",
            "categories": ["Wedding", "Vacation"], "description": "Spinkel Wedding",
            "location": "London → Spain", "notes": None, "status": "confirmed",
            "source": "manual",
        },
    ]
    people_by_entry = {1: ["Sprink", "Emily"]}
    rows = _build_life_log_rows(entries, people_by_entry)
    # Header row + entry row
    assert len(rows) == 2
    assert rows[0][0] == "Date"
    assert "Wedding, Vacation" in rows[1][2]
    assert "Sprink, Emily" in rows[1][4]


def test_build_life_log_empty():
    from google_sheets import _build_life_log_rows
    rows = _build_life_log_rows([], {})
    assert len(rows) >= 1  # at least the header


def test_build_life_log_header_columns():
    from google_sheets import _build_life_log_rows
    rows = _build_life_log_rows([], {})
    header = rows[0]
    assert header[0] == "Date"
    assert header[1] == "End Date"
    assert header[2] == "Categories"
    assert header[3] == "Description"
    assert header[4] == "People"
    assert header[5] == "Location"
    assert header[6] == "Notes"
    assert header[7] == "Status"
    assert header[8] == "Source"
    assert header[9] == "ID"


def test_build_life_log_rows_no_people():
    from google_sheets import _build_life_log_rows
    entries = [
        {
            "id": 2, "date_start": "2025-06-01", "date_end": None,
            "categories": ["Hike"], "description": "Solo hike",
            "location": "Alps", "notes": "Great day", "status": "confirmed",
            "source": "manual",
        },
    ]
    rows = _build_life_log_rows(entries, {})
    assert len(rows) == 2
    assert rows[1][4] == ""  # no people
    assert rows[1][1] == ""  # no end date
    assert rows[1][6] == "Great day"


def test_build_people_rows_basic():
    from google_sheets import _build_people_rows
    people = [
        {
            "id": 1, "name": "Alice", "aliases": ["Al"],
            "relationship_type": "friend", "status": "active",
            "first_seen": "2024-01-01", "last_seen": "2025-04-01",
            "start_date": None, "end_date": None, "notes": "Met at work",
        }
    ]
    rows = _build_people_rows(people)
    assert len(rows) == 2
    assert rows[0][0] == "Name"
    assert rows[1][0] == "Alice"
    assert rows[1][1] == "Al"
    assert rows[1][2] == "friend"
    assert rows[1][9] == "1"


def test_build_people_rows_empty():
    from google_sheets import _build_people_rows
    rows = _build_people_rows([])
    assert len(rows) == 1  # header only
    assert rows[0][0] == "Name"


def test_build_people_rows_multiple_aliases():
    from google_sheets import _build_people_rows
    people = [
        {
            "id": 5, "name": "Bob", "aliases": ["Bobby", "Robert", "Rob"],
            "relationship_type": "family", "status": "active",
            "first_seen": None, "last_seen": None,
            "start_date": None, "end_date": None, "notes": None,
        }
    ]
    rows = _build_people_rows(people)
    assert "Bobby, Robert, Rob" in rows[1][1]
