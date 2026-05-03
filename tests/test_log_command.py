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


@pytest.mark.asyncio
async def test_confirm_yes_saves_entry_and_links_people(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "categories": ["Relationship"],
        "description": "Met Megan at Goldens",
        "location": "Golden, CO",
        "date_start": "2026-05-02",
        "date_end": None,
        "people": ["Megan"],
        "questions": [],
    }))]

    import database as db
    from handlers.log_command import handle_confirm_response

    db.set_state(
        "lifelog_confirming",
        temp_data={
            "original_text": "Met Megan at Goldens",
            "parsed": {
                "categories": ["Relationship"],
                "description": "Met Megan at Goldens",
                "location": "Golden, CO",
                "date_start": "2026-05-02",
                "date_end": None,
                "people": ["Megan"],
                "questions": [],
            },
        },
    )

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot = MagicMock()

    handled = await handle_confirm_response(update, context, "yes")
    assert handled is True

    entries = db.get_all_life_log_entries()
    assert len(entries) == 1
    assert entries[0]["description"] == "Met Megan at Goldens"

    people = db.get_all_people()
    assert any(p["name"] == "Megan" for p in people)

    state = db.get_state()
    # State should advance to person-onboarding (new person), not idle
    assert state["state"] in ("lifelog_new_person", "idle")


@pytest.mark.asyncio
async def test_confirm_no_cancels(temp_db_path, mock_anthropic):
    import database as db
    from handlers.log_command import handle_confirm_response

    db.set_state(
        "lifelog_confirming",
        temp_data={"original_text": "x", "parsed": {
            "categories": [], "description": "x", "location": None,
            "date_start": "2026-05-02", "date_end": None, "people": [], "questions": [],
        }},
    )

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    handled = await handle_confirm_response(update, context, "no")
    assert handled is True
    assert db.get_state()["state"] == "idle"
    assert db.get_all_life_log_entries() == []


@pytest.mark.asyncio
async def test_new_person_onboarding_sets_relationship_type(temp_db_path):
    import database as db
    from handlers.log_command import handle_new_person_response

    pid = db.save_person(
        name="Megan", aliases=[], relationship_type=None,
        first_seen="2026-05-02", notes=None,
    )
    db.set_state(
        "lifelog_new_person",
        temp_data={"current_person_id": pid, "pending_person_ids": []},
    )

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    handled = await handle_new_person_response(update, context, "dating prospect")
    assert handled is True

    p = db.get_person(pid)
    assert p["relationship_type"] == "dating_prospect"
    assert db.get_state()["state"] == "idle"


@pytest.mark.asyncio
async def test_confirm_breakup_sets_person_status_ended(temp_db_path):
    import database as db
    from handlers.log_command import handle_confirm_response

    pid = db.save_person(
        name="Megan", aliases=[], relationship_type="dating",
        first_seen="2026-02-15", notes=None,
    )
    db.set_state(
        "lifelog_confirming",
        temp_data={
            "original_text": "Broke up with Megan today",
            "parsed": {
                "categories": ["Relationship"],
                "description": "Broke up with Megan",
                "location": None,
                "date_start": "2026-05-02",
                "date_end": None,
                "people": ["Megan"],
                "questions": [],
                "relationship_event": {"action": "end", "person": "Megan"},
            },
        },
    )
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await handle_confirm_response(update, context, "yes")

    p = db.get_person(pid)
    assert p["status"] == "ended"
    assert p["end_date"] == "2026-05-02"
