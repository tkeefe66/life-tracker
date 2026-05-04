"""Tests for /syncstories — apply sheet decisions, enqueue survivors."""
from unittest.mock import AsyncMock, MagicMock, patch
import json
import pytest


@pytest.mark.asyncio
async def test_syncstories_dismisses_skips_and_enqueues_yeses(temp_db_path):
    import database
    from handlers.syncstories import syncstories_command

    s1 = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    s2 = database.save_story_parent(
        date_start="2024-04-01", date_end=None,
        story_type="other", summary="Phish", highlights=[], location=None,
    )

    decisions = [
        {"id": s1, "decision": "confirm"},  # survives → enqueued
        {"id": s2, "decision": "skip"},      # dismissed
    ]

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    with patch("handlers.syncstories.read_story_decisions", return_value=decisions), \
         patch("handlers.syncstories.present_next_story", new=AsyncMock()):
        await syncstories_command(update, context)

    # s2 should be dismissed
    assert database.get_life_log_entry(s2)["status"] == "dismissed"
    # s1 should still be 'proposed' (it'll flip to confirmed inside Telegram review)
    assert database.get_life_log_entry(s1)["status"] == "proposed"

    # Queue contains s1 only
    state = database.get_state()
    temp = state.get("temp_data") or {}
    if isinstance(temp, str):
        temp = json.loads(temp)
    assert s1 in temp.get("pending_story_ids", [])
    assert s2 not in temp.get("pending_story_ids", [])
