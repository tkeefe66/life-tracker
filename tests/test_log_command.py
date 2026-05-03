"""Tests for /log command handler."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_log_command_no_args_prompts_user(temp_db_path, mock_anthropic, mock_bot):
    from handlers.log_command import log_command
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = []
    await log_command(update, context)
    update.message.reply_text.assert_called_once()
    args, _ = update.message.reply_text.call_args
    assert "What happened" in args[0] or "Tell me" in args[0]


@pytest.mark.asyncio
async def test_log_command_with_text_calls_ai_and_shows_preview(temp_db_path, mock_anthropic, mock_bot):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "categories": ["Relationship"],
        "description": "Met Megan at Goldens",
        "location": "Golden, CO",
        "date_start": "2026-05-02",
        "date_end": None,
        "people": ["Megan"],
        "questions": [],
    }))]
    from handlers.log_command import log_command
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = ["Met", "Megan", "at", "Goldens"]

    import database as db
    await log_command(update, context)

    state = db.get_state()
    assert state["state"] == "lifelog_confirming"
    temp = json.loads(state["temp_data"])
    assert temp["parsed"]["description"] == "Met Megan at Goldens"
    update.message.reply_text.assert_called()
