"""End-to-end happy path: ingest → cluster → sheet → telegram → confirmed."""
from unittest.mock import AsyncMock, MagicMock, patch
import json
import pytest


def _seed_pending_events(database):
    """Three calendar events: two clustered as a trip, one singleton."""
    e1 = database.save_proposal(
        date_start="2024-03-12", date_end=None,
        categories=["Vacation"], description="Vermont arrival",
        location="VT", source="calendar", source_id="evt-1",
    )
    e2 = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Skiing Killington",
        location="VT", source="calendar", source_id="evt-2",
    )
    e3 = database.save_proposal(
        date_start="2024-04-15", date_end=None,
        categories=["Concert"], description="Phish at MSG",
        location="NYC", source="calendar", source_id="evt-3",
    )
    return e1, e2, e3


def _ai_returns(*payloads):
    """Build a side_effect that returns one mocked AI response per call, in order."""
    iter_p = iter(payloads)

    def _next(*a, **kw):
        m = MagicMock()
        m.content = [MagicMock(text=json.dumps(next(iter_p)))]
        return m
    return _next


async def _e2e_run():
    import database
    from handlers.buildstories import buildstories_command
    from handlers.syncstories import syncstories_command
    from handlers.story_review import (
        handle_story_confirming, handle_story_why_mattered,
    )

    e1, e2, e3 = _seed_pending_events(database)

    # Stage 1: /buildstories — AI returns one shape per cluster
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    ctx = MagicMock()
    ctx.args = []

    # Reset cached client + mock the constructor
    import ai_life_log
    ai_life_log._client = None

    with patch("anthropic.Anthropic") as Anthropic:
        client = MagicMock()
        client.messages.create.side_effect = _ai_returns(
            {"story_type": "trip", "summary": "Vermont trip",
             "highlights": ["Arrival", "Skiing"], "event_id_refs": [e1, e2],
             "suggested_extras_questions": [], "location": "VT"},
            {"story_type": "other", "summary": "Phish at MSG",
             "highlights": ["Phish at MSG"], "event_id_refs": [e3],
             "suggested_extras_questions": [], "location": "NYC"},
        )
        Anthropic.return_value = client
        with patch("handlers.buildstories.sync_stories_to_sheet",
                   return_value="https://example/sheet"):
            await buildstories_command(update, ctx)

    stories = database.get_pending_stories_with_children()
    assert len(stories) == 2

    # Stage 2: /syncstories — confirm both
    decisions = [{"id": s["id"], "decision": "confirm"} for s in stories]
    with patch("handlers.syncstories.read_story_decisions", return_value=decisions), \
         patch("handlers.syncstories.present_next_story", new=AsyncMock()):
        await syncstories_command(update, ctx)

    # Stage 3: walk Telegram review for both stories. The queue is set up by
    # syncstories_command; we manually drive the state machine here.
    # present_next_story was mocked above so we set up the state by hand.
    reply = AsyncMock()
    state = database.get_state()
    temp = state.get("temp_data") or {}
    if isinstance(temp, str):
        temp = json.loads(temp)
    queue = list(temp.get("pending_story_ids") or [])

    # Set first story as current
    assert len(queue) == 2
    first = queue[0]
    rest = queue[1:]
    database.set_state(
        state="story_confirming",
        temp_data={"pending_story_ids": rest, "current_story_id": first},
    )

    # Walk first story: yes → why_mattered
    # (no suggested questions → handle_story_why_mattered confirms + advances)
    await handle_story_confirming("yes", reply)
    await handle_story_why_mattered("It mattered.", reply)

    # Walk second story: state should now be story_confirming with the second
    # story as current. Repeat the yes → why_mattered flow.
    state = database.get_state()
    temp = state.get("temp_data") or {}
    if isinstance(temp, str):
        temp = json.loads(temp)
    assert state["state"] == "story_confirming"
    await handle_story_confirming("yes", reply)
    await handle_story_why_mattered("Also mattered.", reply)

    # After the last story's why_mattered, state should be idle
    state = database.get_state()
    assert state["state"] == "idle"

    # All entries (2 parents + 2 children + 1 singleton parent) should be confirmed
    e1_e = database.get_life_log_entry(e1)
    e2_e = database.get_life_log_entry(e2)
    e3_e = database.get_life_log_entry(e3)
    assert e1_e["status"] == "confirmed"
    assert e2_e["status"] == "confirmed"
    assert e3_e["status"] == "confirmed"


@pytest.mark.asyncio
async def test_e2e_sqlite(temp_db_path):
    await _e2e_run()


@pytest.mark.asyncio
async def test_e2e_postgres(postgres_db):
    await _e2e_run()
