"""Tests for /showstory <id>."""
from unittest.mock import AsyncMock, MagicMock
import pytest


@pytest.mark.asyncio
async def test_showstory_dumps_parent_and_children(temp_db_path):
    import database
    from handlers.showstory import showstory_command

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-13",
        story_type="trip", summary="Trip", highlights=["a", "b"], location="VT",
    )
    cid = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Vacation"], description="Day 2",
        location="VT", source="calendar", source_id="e1",
    )
    database.assign_child_to_story(cid, sid)

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = [str(sid)]

    await showstory_command(update, context)
    msg = update.message.reply_text.call_args.args[0]
    assert "trip" in msg.lower()
    assert "Day 2" in msg
    assert "VT" in msg
