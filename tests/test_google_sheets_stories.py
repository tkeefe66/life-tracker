"""Tests for the Stories tab rendering."""
from unittest.mock import MagicMock, patch


def test_build_stories_rows_emits_parent_then_children():
    from google_sheets import _build_stories_rows

    stories = [
        {"id": 100, "story_type": "trip", "date_start": "2024-03-12",
         "date_end": "2024-03-17", "description": "Vermont ski trip",
         "highlights": ["JFK→BTV flight", "Skied Killington"],
         "children": [
             {"id": 101, "date_start": "2024-03-12", "description": "JFK→BTV flight"},
             {"id": 102, "date_start": "2024-03-13", "description": "Skiing"},
         ]},
        {"id": 200, "story_type": "other", "date_start": "2024-04-01",
         "date_end": None, "description": "Phish at MSG",
         "highlights": [],
         "children": []},
    ]
    rows = _build_stories_rows(stories)
    # Row 0 = header; row 1 = parent #100; row 2 = child 101; row 3 = child 102
    # row 4 = parent #200 (singleton, no children)
    assert rows[0][0] == "Type"  # header sentinel
    parent_row = rows[1]
    assert parent_row[0] == "trip"
    assert "Vermont" in parent_row[2]  # Summary col
    assert parent_row[4] == "2"  # # events
    assert parent_row[5] == "100"  # parent id
    assert rows[2][2].startswith("  └")  # indent marker on child desc, now in Summary col
    assert rows[4][0] == "other"
    assert rows[4][4] == "0"  # singleton has 0 children


def test_sync_stories_to_sheet_clears_and_writes():
    from google_sheets import sync_stories_to_sheet
    fake_sheet = MagicMock()
    fake_spreadsheet = MagicMock()
    fake_spreadsheet.worksheet.return_value = fake_sheet
    fake_spreadsheet.url = "https://example/sheet"
    # Make _ensure_sheets see the Stories tab as already-existing
    ws = MagicMock()
    ws.title = "Stories"
    fake_spreadsheet.worksheets.return_value = [ws]

    with patch("google_sheets._get_spreadsheet", return_value=fake_spreadsheet), \
         patch("google_sheets._ensure_sheets"):
        url = sync_stories_to_sheet([
            {"id": 1, "story_type": "trip", "date_start": "2024-03-12",
             "date_end": "2024-03-17", "description": "Trip",
             "highlights": [], "children": []},
        ])
    fake_sheet.clear.assert_called_once()
    fake_sheet.update.assert_called()
    assert url == "https://example/sheet"


def test_read_story_decisions_only_picks_parent_rows():
    from google_sheets import read_story_decisions
    fake_sheet = MagicMock()
    # Row 0 header, row 1 parent (decision yes), row 2 child (ignored even
    # if user wrote in decision col), row 3 parent (decision skip)
    fake_sheet.get_all_values.return_value = [
        ["Type", "Date Range", "Summary", "Highlights", "# Events", "ID", "Decision"],
        ["trip", "2024-03-12 → 2024-03-17", "Vermont", "...", "2", "100", "yes"],
        ["", "2024-03-12", "  └ flight", "", "", "101", "yes"],   # child — ignore
        ["other", "2024-04-01", "Phish", "", "0", "200", "skip"],
    ]
    fake_spreadsheet = MagicMock()
    fake_spreadsheet.worksheet.return_value = fake_sheet
    with patch("google_sheets._get_spreadsheet", return_value=fake_spreadsheet), \
         patch("google_sheets._ensure_sheets"):
        decisions = read_story_decisions()
    assert decisions == [
        {"id": 100, "decision": "confirm"},
        {"id": 200, "decision": "skip"},
    ]
