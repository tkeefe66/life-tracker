"""ai_metrics with mocked Anthropic client — no live calls."""
from unittest.mock import MagicMock


def _set_response(mock_client, text):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    mock_client.messages.create.return_value = resp


def test_classify_receipt_true(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_order": true}')
    assert ai_metrics.classify_receipt("Uber Eats <noreply@uber.com>", "Your Friday evening order") is True


def test_classify_receipt_parse_failure_defaults_false(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, "not json at all")
    assert ai_metrics.classify_receipt("x@uber.com", "??") is False


def test_classify_social_event(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_social": true, "confidence": 0.92}')
    out = ai_metrics.classify_social_event("Dinner w/ Sam", "", "Bar Dough", ["Sam"])
    assert out == {"is_social": True, "confidence": 0.92}


def test_classify_social_event_failure_defaults(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, "garbage")
    out = ai_metrics.classify_social_event("Dentist", "", "", [])
    assert out == {"is_social": False, "confidence": 0.0}


def test_classify_social_event_non_numeric_confidence(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_social": true, "confidence": "high"}')
    out = ai_metrics.classify_social_event("Dinner w/ friends", "", "Restaurant", ["Alice", "Bob"])
    assert out == {"is_social": True, "confidence": 0.0}


def _card():
    return {
        "week_start": "2026-07-13", "week_end": "2026-07-19",
        "metrics": {
            "gym": {"label": "Gym sessions", "count": 3, "target": 3, "direction": "floor", "hit": True},
            "delivery": {"label": "Delivery orders", "count": 2, "target": 1, "direction": "ceiling", "hit": False},
        },
    }


def test_weekly_reflection_returns_text(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"reflection": "You held the line on gym."}')
    assert ai_metrics.weekly_reflection(_card(), ["a pattern"]) == "You held the line on gym."
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Gym sessions: 3" in prompt and "a pattern" in prompt


def test_weekly_reflection_empty_on_garbage(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, "not json")
    assert ai_metrics.weekly_reflection(_card(), []) == ""


def test_weekly_reflection_empty_on_non_dict_json(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, "[1, 2, 3]")
    assert ai_metrics.weekly_reflection(_card(), []) == ""
