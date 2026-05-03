"""Tests for ai_life_log."""
import json
from unittest.mock import MagicMock


def test_call_strips_markdown_fences(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text='```json\n{"foo": 1}\n```')
    ]
    import ai_life_log
    result = ai_life_log._call_json("test prompt")
    assert result == {"foo": 1}


def test_call_handles_extra_whitespace(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text='   \n{"foo": 2}\n  ')
    ]
    import ai_life_log
    result = ai_life_log._call_json("test")
    assert result == {"foo": 2}


def test_call_returns_default_on_parse_failure(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text='not json at all')
    ]
    import ai_life_log
    result = ai_life_log._call_json("test", default={"fallback": True})
    assert result == {"fallback": True}


def test_propose_high_confidence_wedding(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "high",
        "categories": ["Wedding", "Vacation"],
        "description": "Spinkel Wedding",
        "location": "London, UK",
        "people": ["Sprink", "Emily"],
        "reason": "Multi-day named wedding event"
    }))]
    import ai_life_log
    result = ai_life_log.propose_from_calendar_event(
        title="Spinkel Wedding",
        start="2025-05-05",
        end="2025-05-12",
        attendees=["Spinkel", "Emily"],
        description="",
        location="London",
        active_categories=["Wedding", "Vacation", "Skiing"],
    )
    assert result["confidence"] == "high"
    assert "Wedding" in result["categories"]
    assert "Vacation" in result["categories"]


def test_propose_returns_skip_for_noise(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "skip",
        "categories": [],
        "description": "",
        "location": "",
        "people": [],
        "reason": "Standup meeting — not Life Log worthy"
    }))]
    import ai_life_log
    result = ai_life_log.propose_from_calendar_event(
        title="Daily standup", start="2026-05-02T09:00:00",
        end="2026-05-02T09:15:00", attendees=[],
        description="", location="", active_categories=["Vacation"],
    )
    assert result["confidence"] == "skip"


def test_parse_log_command_extracts_entry(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "categories": ["Relationship"],
        "description": "Met Megan at Goldens",
        "location": "Golden, CO",
        "date_start": "2026-05-02",
        "date_end": None,
        "people": ["Megan"],
        "questions": []
    }))]
    import ai_life_log
    result = ai_life_log.parse_log_command(
        "Met Megan at Goldens in Golden tonight",
        today="2026-05-02",
        active_categories=["Relationship", "Vacation"],
    )
    assert result["categories"] == ["Relationship"]
    assert result["people"] == ["Megan"]
    assert result["date_start"] == "2026-05-02"


def test_parse_log_command_with_correction(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "categories": ["Skiing"],
        "description": "Skied at Killington",
        "location": "Killington, VT",
        "date_start": "2026-05-01",
        "date_end": None,
        "people": [],
        "questions": []
    }))]
    import ai_life_log
    result = ai_life_log.parse_log_command(
        "Skied at Killington yesterday",
        today="2026-05-02",
        active_categories=["Skiing"],
        correction="Actually it was Vermont not Colorado",
    )
    assert result["location"] == "Killington, VT"


def test_recommend_category_changes(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "recommendations": [
            {"action": "drop", "name": "Pet", "reason": "0 entries in 6 months"},
            {"action": "merge", "from": "Outdoors", "into": "Hiking", "reason": "Hiking now dominates"}
        ]
    }))]
    import ai_life_log
    result = ai_life_log.recommend_category_changes(
        category_usage=[
            {"name": "Vacation", "usage_count": 12},
            {"name": "Pet", "usage_count": 0},
            {"name": "Outdoors", "usage_count": 3},
        ],
        recent_descriptions=["Hiked Mt Quandary", "Hiked Bierstadt"],
    )
    assert any(r["action"] == "drop" for r in result["recommendations"])
