"""Tests for handlers/lifelog_proposals.py — yes/skip/edit proposal replies."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_yes_n_confirms_proposal(temp_db_path):
    import database as db
    pid = db.save_proposal(
        date_start="2026-05-02", date_end=None, categories=["Concert"],
        description="Test", location=None, source="calendar", source_id="ev1",
    )
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    from handlers.lifelog_proposals import handle_proposal_reply
    handled = await handle_proposal_reply(update, context, f"yes #{pid}")
    assert handled is True
    assert db.get_life_log_entry(pid)["status"] == "confirmed"


@pytest.mark.asyncio
async def test_skip_n_dismisses(temp_db_path):
    import database as db
    pid = db.save_proposal(
        date_start="2026-05-02", date_end=None, categories=["Concert"],
        description="X", location=None, source="calendar", source_id="ev2",
    )
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    from handlers.lifelog_proposals import handle_proposal_reply
    handled = await handle_proposal_reply(update, context, f"skip #{pid}")
    assert handled is True
    assert db.get_life_log_entry(pid)["status"] == "dismissed"


@pytest.mark.asyncio
async def test_yes_all_confirms_all_pending(temp_db_path):
    import database as db
    p1 = db.save_proposal(
        date_start="2026-05-01", date_end=None, categories=["Concert"],
        description="A", location=None, source="calendar", source_id="ev1",
    )
    p2 = db.save_proposal(
        date_start="2026-05-02", date_end=None, categories=["Visitors"],
        description="B", location=None, source="calendar", source_id="ev2",
    )
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    from handlers.lifelog_proposals import handle_proposal_reply
    handled = await handle_proposal_reply(update, context, "yes all")
    assert handled is True
    assert db.get_life_log_entry(p1)["status"] == "confirmed"
    assert db.get_life_log_entry(p2)["status"] == "confirmed"


@pytest.mark.asyncio
async def test_unrelated_text_returns_false(temp_db_path):
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    from handlers.lifelog_proposals import handle_proposal_reply
    handled = await handle_proposal_reply(update, context, "some random message")
    assert handled is False
