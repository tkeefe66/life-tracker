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
