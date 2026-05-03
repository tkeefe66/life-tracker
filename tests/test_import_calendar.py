"""Tests for calendar history backfill (mocked)."""
import json
from unittest.mock import MagicMock, patch


def test_import_creates_proposals(temp_db_path, mock_anthropic):
    fake_events = [{
        "event_id": "old1",
        "title": "Vermont trip",
        "start_datetime": "2024-07-01",
        "end_datetime": "2024-07-08",
        "description": "",
        "location": "Vermont",
        "is_recurring": False,
        "attendees": [],
    }]
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "high",
        "categories": ["Vacation"],
        "description": "Vermont trip",
        "location": "Vermont",
        "people": [],
        "reason": "Multi-day trip"
    }))]

    with patch(
        "scripts.import_calendar_history._fetch_year_events",
        return_value=fake_events,
    ):
        from scripts.import_calendar_history import import_year
        import database as db
        import_year(2024, dry_run=False)

    proposals = db.get_pending_proposals()
    assert len(proposals) == 1
    assert proposals[0]["categories"] == ["Vacation"]
