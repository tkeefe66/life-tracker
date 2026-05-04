"""Tests for Proposals sheet builder + decision parser."""
from unittest.mock import MagicMock, patch


def test_build_proposals_rows_includes_decision_column():
    from google_sheets import _build_proposals_rows
    proposals = [
        {
            "id": 42, "date_start": "2024-07-04", "date_end": "2024-07-08",
            "categories": ["Vacation"], "description": "Vermont with parents",
            "location": "Vermont", "source": "calendar",
        },
    ]
    rows = _build_proposals_rows(proposals)
    assert rows[0] == [
        "Date", "End Date", "Categories", "Description", "Location",
        "Source", "ID", "Decision",
    ]
    assert rows[1][6] == "42"
    assert rows[1][7] == ""  # Decision starts blank


def test_read_proposal_decisions_parses_yes_skip_edit():
    """Mock gspread, verify decision parsing logic."""
    fake_rows = [
        ["Date", "End Date", "Categories", "Description", "Location", "Source", "ID", "Decision"],
        ["2024-07-04", "", "Vacation", "Vermont", "VT", "calendar", "42", "yes"],
        ["2024-08-15", "", "Concert", "Dead & Co", "Vegas", "calendar", "43", "skip"],
        ["2024-09-01", "", "Outdoors", "Hike", "CO", "calendar", "44", "Better description here"],
        ["2024-10-01", "", "Wedding", "Spinkel", "London", "calendar", "45", ""],
    ]
    fake_sheet = MagicMock()
    fake_sheet.get_all_values.return_value = fake_rows
    fake_spreadsheet = MagicMock()
    fake_spreadsheet.worksheet.return_value = fake_sheet

    with patch("google_sheets._get_spreadsheet", return_value=fake_spreadsheet), \
         patch("google_sheets._ensure_sheets"):
        from google_sheets import read_proposal_decisions
        decisions = read_proposal_decisions()

    assert len(decisions) == 3  # blank-decision row is skipped
    assert decisions[0] == {"id": 42, "decision": "confirm", "new_description": None}
    assert decisions[1] == {"id": 43, "decision": "skip", "new_description": None}
    assert decisions[2] == {"id": 44, "decision": "edit", "new_description": "Better description here"}
