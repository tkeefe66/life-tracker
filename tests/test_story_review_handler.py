"""Tests for the Telegram narrative state machine."""
from unittest.mock import AsyncMock, MagicMock, patch
import json
import pytest


def _setup_queue(database, story_ids: list, current_id=None):
    temp = {"pending_story_ids": story_ids, "current_story_id": current_id}
    database.set_state(state="story_confirming", temp_data=temp)


@pytest.mark.asyncio
async def test_present_next_story_sends_narrative_card(temp_db_path):
    import database
    from handlers.story_review import present_next_story

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont ski trip",
        highlights=["JFK→BTV flight", "Skied Killington"], location="VT",
    )
    cid = database.save_proposal(
        date_start="2024-03-12", date_end=None,
        categories=["Vacation"], description="JFK→BTV flight",
        location="VT", source="calendar", source_id="e1",
    )
    database.assign_child_to_story(cid, sid)
    _setup_queue(database, [sid])

    reply = AsyncMock()
    await present_next_story(reply)
    reply.assert_called_once()
    msg = reply.call_args.args[0]
    assert "TRIP" in msg.upper()
    assert "Vermont" in msg
    assert "#1" in msg  # numbered events list
    state = database.get_state()
    temp = state["temp_data"]
    if isinstance(temp, str):
        temp = json.loads(temp)
    assert temp["current_story_id"] == sid
    assert state["state"] == "story_confirming"


@pytest.mark.asyncio
async def test_handle_confirming_yes_advances_to_why_mattered(temp_db_path):
    import database
    from handlers.story_review import handle_story_confirming

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    _setup_queue(database, [sid], current_id=sid)

    reply = AsyncMock()
    await handle_story_confirming("yes", reply)

    state = database.get_state()
    assert state["state"] == "story_why_mattered"
    reply.assert_called_with("Why did this matter? (one sentence)")


@pytest.mark.asyncio
async def test_handle_confirming_skip_dismisses_and_advances(temp_db_path):
    import database
    from handlers.story_review import handle_story_confirming

    sid1 = database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="A", highlights=[], location=None,
    )
    sid2 = database.save_story_parent(
        date_start="2024-04-01", date_end=None,
        story_type="other", summary="B", highlights=[], location=None,
    )
    _setup_queue(database, [sid1, sid2], current_id=sid1)

    reply = AsyncMock()
    await handle_story_confirming("skip", reply)

    assert database.get_life_log_entry(sid1)["status"] == "dismissed"
    state = database.get_state()
    temp = state["temp_data"]
    if isinstance(temp, str):
        temp = json.loads(temp)
    assert temp["current_story_id"] == sid2  # advanced to next
    assert state["state"] == "story_confirming"


@pytest.mark.asyncio
async def test_handle_why_mattered_records_text_and_advances_to_extras_optin(
    temp_db_path
):
    import database
    from handlers.story_review import handle_story_why_mattered

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="Vermont", highlights=[], location=None,
        extras={"_suggested_extras_questions": ["mode of travel?"]},
    )
    _setup_queue(database, [sid], current_id=sid)
    database.set_state(state="story_why_mattered",
                       temp_data={"pending_story_ids": [sid], "current_story_id": sid})

    reply = AsyncMock()
    await handle_story_why_mattered(
        "First trip after my surgery — meant a lot.", reply
    )

    e = database.get_life_log_entry(sid)
    assert "surgery" in e["why_mattered"]
    state = database.get_state()
    assert state["state"] == "story_extras_optin"


@pytest.mark.asyncio
async def test_drop_event_returns_to_unclustered_and_re_renders(temp_db_path):
    import database
    from handlers.story_review import handle_story_confirming

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-13",
        story_type="trip", summary="Trip",
        highlights=["A", "B"], location="VT",
    )
    c1 = database.save_proposal(
        date_start="2024-03-12", date_end=None,
        categories=["Vacation"], description="Day 1",
        location="VT", source="calendar", source_id="e1",
    )
    c2 = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Vacation"], description="Day 2",
        location="VT", source="calendar", source_id="e2",
    )
    database.assign_child_to_story(c1, sid)
    database.assign_child_to_story(c2, sid)
    _setup_queue(database, [sid], current_id=sid)

    reply = AsyncMock()
    await handle_story_confirming("drop #1", reply)

    # c1 returned to unclustered pool
    assert database.get_life_log_entry(c1)["parent_id"] is None
    # c2 still attached
    assert database.get_life_log_entry(c2)["parent_id"] == sid
    # Story re-rendered with 1 event remaining
    assert any("1 events" in c.args[0] for c in reply.call_args_list)


@pytest.mark.asyncio
async def test_drop_out_of_range_polite_error(temp_db_path):
    import database
    from handlers.story_review import handle_story_confirming

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="Trip", highlights=[], location=None,
    )
    c1 = database.save_proposal(
        date_start="2024-03-12", date_end=None,
        categories=["Vacation"], description="Only event",
        location=None, source="calendar", source_id="e1",
    )
    database.assign_child_to_story(c1, sid)
    _setup_queue(database, [sid], current_id=sid)

    reply = AsyncMock()
    await handle_story_confirming("drop #5", reply)
    msg = reply.call_args.args[0]
    assert "1" in msg and "drop" in msg.lower()
    # Nothing changed
    assert database.get_life_log_entry(c1)["parent_id"] == sid


@pytest.mark.asyncio
async def test_extras_optin_skip_confirms_and_advances(temp_db_path):
    import database
    from handlers.story_review import handle_story_extras_optin

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="Trip", highlights=[], location=None,
        extras={"_suggested_extras_questions": ["mode?"]},
    )
    _setup_queue(database, [sid], current_id=sid)
    database.set_state(state="story_extras_optin",
                       temp_data={"pending_story_ids": [sid],
                                  "current_story_id": sid})
    reply = AsyncMock()
    await handle_story_extras_optin("skip", reply)
    assert database.get_life_log_entry(sid)["status"] == "confirmed"


@pytest.mark.asyncio
async def test_extras_optin_yes_starts_qa_loop(temp_db_path, mock_anthropic):
    import database
    from handlers.story_review import (
        handle_story_extras_optin, handle_story_extras_qa,
    )
    from unittest.mock import MagicMock as MM

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="Trip", highlights=[], location=None,
        extras={"_suggested_extras_questions": ["mode of travel?", "who came?"]},
    )
    _setup_queue(database, [sid], current_id=sid)
    database.set_state(state="story_extras_optin",
                       temp_data={"pending_story_ids": [sid],
                                  "current_story_id": sid})
    reply = AsyncMock()
    await handle_story_extras_optin("yes", reply)
    state = database.get_state()
    assert state["state"] == "story_extras_qa"

    # Mock parse_extras_answer
    mock_anthropic.messages.create.return_value.content = [
        MM(text=json.dumps({"travel_mode": "flight"}))
    ]
    await handle_story_extras_qa("flight", reply)

    e = database.get_life_log_entry(sid)
    assert e["extras"].get("travel_mode") == "flight"
