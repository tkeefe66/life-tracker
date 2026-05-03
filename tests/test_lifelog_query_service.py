"""Tests for lifelog_query_service."""
import json
from unittest.mock import MagicMock

import pytest


def test_when_did_i_last_see_x(temp_db_path, mock_anthropic):
    import database as db
    pid = db.save_person("Megan", [], "dating", "2026-02-15", None)
    eid = db.save_life_log_entry(
        date_start="2026-04-20", date_end=None, categories=["Relationship"],
        description="Dinner with Megan", location="Denver", notes=None,
        status="confirmed", source="manual", source_id=None,
    )
    db.link_entry_to_people(eid, [pid])
    db.update_person_last_seen(pid, "2026-04-20")

    # First call: tool_use to find the person
    # Second call: final answer
    mock_anthropic.messages.create.side_effect = [
        MagicMock(
            stop_reason="tool_use",
            content=[
                MagicMock(type="text", text="Looking up Megan..."),
                MagicMock(type="tool_use", name="find_person", id="t1", input={"name": "Megan"}),
            ],
        ),
        MagicMock(
            stop_reason="end_turn",
            content=[MagicMock(type="text", text="Last seen Megan on 2026-04-20 (Dinner with Megan).")],
        ),
    ]

    from services.lifelog_query_service import answer_query
    answer = answer_query("When did I last see Megan?")
    assert "2026-04-20" in answer or "April 20" in answer
