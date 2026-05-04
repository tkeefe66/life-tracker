"""Tests for /pushstories — retry sheet write."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_pushstories_writes_pending_to_sheet(temp_db_path):
    import database
    from handlers.pushstories import pushstories_command

    database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="X", highlights=[], location=None,
    )

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    with patch("handlers.pushstories.sync_stories_to_sheet",
               return_value="https://example/sheet"):
        await pushstories_command(update, context)
    last_msg = update.message.reply_text.call_args_list[-1].args[0]
    assert "1" in last_msg  # one story written
