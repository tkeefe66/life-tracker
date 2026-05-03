"""Tests for jobs.lifelog_realtime."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_realtime_proposes_high_confidence(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "high",
        "categories": ["Wedding", "Vacation"],
        "description": "Spinkel Wedding",
        "location": "London",
        "people": ["Sprink", "Emily"],
        "reason": "Multi-day named wedding"
    }))]

    fake_events = [{
        "event_id": "ev1",
        "title": "Spinkel Wedding",
        "start_datetime": "2025-05-05",
        "end_datetime": "2025-05-12",
        "description": "",
        "location": "London",
        "is_recurring": False,
        "attendees": ["Sprink", "Emily"],
    }]

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("jobs.lifelog_realtime._fetch_recent_calendar_events", return_value=fake_events):
        from jobs.lifelog_realtime import run_realtime_proposals
        await run_realtime_proposals(bot)

    import database as db
    proposals = db.get_pending_proposals()
    assert len(proposals) == 1
    assert proposals[0]["categories"] == ["Wedding", "Vacation"]

    bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_realtime_skips_already_seen_events(temp_db_path, mock_anthropic):
    import database as db
    db.record_activity("calendar", "ev_seen", "calendar_event",
                       "2026-05-02T00:00:00", {"title": "Already seen"})

    bot = MagicMock()
    bot.send_message = AsyncMock()

    fake_events = [{
        "event_id": "ev_seen",
        "title": "X",
        "start_datetime": "2026-05-02",
        "end_datetime": "",
        "description": "",
        "location": "",
        "is_recurring": False,
        "attendees": [],
    }]
    with patch("jobs.lifelog_realtime._fetch_recent_calendar_events", return_value=fake_events):
        from jobs.lifelog_realtime import run_realtime_proposals
        await run_realtime_proposals(bot)

    # No new proposal because event was already in activity_log
    assert db.get_pending_proposals() == []
    bot.send_message.assert_not_called()
