"""Tests for jobs.lifelog_dayafter."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_dayafter_proposes_matched(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "matched",
        "categories": ["Skiing"],
        "description": "Skied at Killington",
        "location": "Killington, VT",
        "people": ["Justin"],
        "reason": "Single ski outing",
    }))]
    fake = [{
        "event_id": "ski1",
        "title": "Ski Killington",
        "start_datetime": "2026-05-01T09:00:00",
        "end_datetime": "2026-05-01T17:00:00",
        "description": "",
        "location": "Killington",
        "is_recurring": False,
        "attendees": ["Justin"],
    }]
    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("jobs.lifelog_dayafter._fetch_yesterdays_events", return_value=fake):
        from jobs.lifelog_dayafter import run_dayafter_proposals
        await run_dayafter_proposals(bot)

    import database as db
    proposals = db.get_pending_proposals()
    assert len(proposals) == 1
    bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_dayafter_skips_already_promoted(temp_db_path, mock_anthropic):
    """Events already promoted to life log should be skipped."""
    import database as db

    # Record the activity and mark it promoted
    db.record_activity("calendar", "promoted1", "calendar_event",
                       "2026-05-01T09:00:00", {"title": "Already done"})
    rows = db.get_activity_by_source_id("calendar", "promoted1")
    db.mark_activity_promoted(rows[0]["id"])

    bot = MagicMock()
    bot.send_message = AsyncMock()

    fake = [{
        "event_id": "promoted1",
        "title": "Already done",
        "start_datetime": "2026-05-01T09:00:00",
        "end_datetime": "2026-05-01T17:00:00",
        "description": "",
        "location": "",
        "is_recurring": False,
        "attendees": [],
    }]
    with patch("jobs.lifelog_dayafter._fetch_yesterdays_events", return_value=fake):
        from jobs.lifelog_dayafter import run_dayafter_proposals
        await run_dayafter_proposals(bot)

    assert db.get_pending_proposals() == []
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_dayafter_skips_non_matched_confidence(temp_db_path, mock_anthropic):
    """Events with confidence != 'matched' should not become proposals."""
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "maybe",
        "categories": ["Visitors"],
        "description": "Meeting",
        "location": "",
        "people": [],
        "reason": "ambiguous",
    }))]
    fake = [{
        "event_id": "maybe_ev",
        "title": "Meeting with someone",
        "start_datetime": "2026-05-01T10:00:00",
        "end_datetime": "2026-05-01T11:00:00",
        "description": "",
        "location": "",
        "is_recurring": False,
        "attendees": [],
    }]
    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("jobs.lifelog_dayafter._fetch_yesterdays_events", return_value=fake):
        from jobs.lifelog_dayafter import run_dayafter_proposals
        await run_dayafter_proposals(bot)

    import database as db
    assert db.get_pending_proposals() == []
    bot.send_message.assert_not_called()
