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
