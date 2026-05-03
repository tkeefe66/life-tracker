"""Tests for jobs.lifelog_sunday."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_sunday_digest_batches_maybes(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "maybe",
        "categories": ["Visitors"],
        "description": "Brunch with Alex",
        "location": "Snooze",
        "people": ["Alex"],
        "reason": "ambiguous personal event",
    }))]
    fake = [
        {
            "event_id": f"ev{i}",
            "title": f"Brunch {i}",
            "start_datetime": f"2026-04-{20+i:02d}T11:00:00",
            "end_datetime": f"2026-04-{20+i:02d}T12:00:00",
            "description": "",
            "location": "Snooze",
            "is_recurring": False,
            "attendees": ["Alex"],
        }
        for i in range(3)
    ]
    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("jobs.lifelog_sunday._fetch_past_week_events", return_value=fake):
        from jobs.lifelog_sunday import run_sunday_digest
        await run_sunday_digest(bot)

    import database as db
    proposals = db.get_pending_proposals()
    assert len(proposals) == 3
    # One digest message, not 3 separate messages
    assert bot.send_message.call_count == 1


@pytest.mark.asyncio
async def test_sunday_digest_skips_no_maybes(temp_db_path, mock_anthropic):
    """When no events classify as 'maybe', no message is sent."""
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "skip",
        "categories": [],
        "description": "",
        "location": "",
        "people": [],
        "reason": "routine recurring event",
    }))]
    fake = [{
        "event_id": "daily_standup",
        "title": "Daily Standup",
        "start_datetime": "2026-04-28T09:00:00",
        "end_datetime": "2026-04-28T09:15:00",
        "description": "",
        "location": "",
        "is_recurring": True,
        "attendees": [],
    }]
    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("jobs.lifelog_sunday._fetch_past_week_events", return_value=fake):
        from jobs.lifelog_sunday import run_sunday_digest
        await run_sunday_digest(bot)

    import database as db
    assert db.get_pending_proposals() == []
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_sunday_digest_skips_already_promoted(temp_db_path, mock_anthropic):
    """Events already promoted to life log are excluded from the digest."""
    import database as db

    db.record_activity("calendar", "promoted_ev", "calendar_event",
                       "2026-04-28T10:00:00", {"title": "Already in log"})
    rows = db.get_activity_by_source_id("calendar", "promoted_ev")
    db.mark_activity_promoted(rows[0]["id"])

    bot = MagicMock()
    bot.send_message = AsyncMock()

    fake = [{
        "event_id": "promoted_ev",
        "title": "Already in log",
        "start_datetime": "2026-04-28T10:00:00",
        "end_datetime": "2026-04-28T11:00:00",
        "description": "",
        "location": "",
        "is_recurring": False,
        "attendees": [],
    }]
    with patch("jobs.lifelog_sunday._fetch_past_week_events", return_value=fake):
        from jobs.lifelog_sunday import run_sunday_digest
        await run_sunday_digest(bot)

    assert db.get_pending_proposals() == []
    bot.send_message.assert_not_called()
