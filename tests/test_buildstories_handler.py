"""Tests for /buildstories command handler."""
from unittest.mock import AsyncMock, MagicMock, patch
import json
import pytest


@pytest.mark.asyncio
async def test_buildstories_runs_clustering_and_pushes_sheet(
    temp_db_path, mock_anthropic
):
    import database
    from handlers.buildstories import buildstories_command

    e1 = database.save_proposal(
        date_start="2024-03-12", date_end=None,
        categories=["Vacation"], description="Vermont arrival",
        location="VT", source="calendar", source_id="evt-1",
    )
    e2 = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Skiing",
        location="VT", source="calendar", source_id="evt-2",
    )

    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "story_type": "trip", "summary": "Vermont weekend",
        "highlights": ["Vermont arrival", "Skiing"], "event_id_refs": [e1, e2],
        "suggested_extras_questions": [], "location": "VT",
    }))]

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = []

    with patch("handlers.buildstories.sync_stories_to_sheet",
               return_value="https://example/sheet"):
        await buildstories_command(update, context)

    # Two messages expected: "running…" then "done"
    assert update.message.reply_text.call_count >= 2
    last_msg = update.message.reply_text.call_args_list[-1].args[0]
    assert "1" in last_msg  # one parent story created
