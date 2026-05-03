"""Tests for the /ask command handler."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_ask_command_runs_query(temp_db_path):
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = ["When", "did", "I", "last", "see", "Megan?"]

    with patch("handlers.lifelog_queries.answer_query", return_value="Last seen 2026-04-20"):
        from handlers.lifelog_queries import ask_command
        await ask_command(update, context)

    # Two reply_text calls: "🤔 Thinking..." then the answer
    assert update.message.reply_text.call_count == 2
    answer_call = update.message.reply_text.call_args_list[-1]
    args, _ = answer_call
    assert "2026-04-20" in args[0]


@pytest.mark.asyncio
async def test_ask_command_no_args_prompts(temp_db_path):
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = []

    from handlers.lifelog_queries import ask_command
    await ask_command(update, context)

    update.message.reply_text.assert_called_once()
