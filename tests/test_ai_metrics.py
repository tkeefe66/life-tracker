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


def test_classify_receipt_includes_snippet(mock_anthropic):
    _set_response(mock_anthropic, '{"is_order": true}')
    import ai_metrics
    assert ai_metrics.classify_receipt(
        "noreply@uber.com", "Your order", "Thanks for ordering, preview text"
    ) is True
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Thanks for ordering, preview text" in prompt


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


def test_classify_social_event_prompt_includes_examples(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_social": true, "confidence": 0.9}')
    examples = [
        {"title": "Taco Tuesday", "user_is_social": True},
        {"title": "Team standup", "user_is_social": False},
    ]
    ai_metrics.classify_social_event("Dinner", "", "", [], examples=examples)
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "The user has corrected past classifications:" in prompt
    assert '"Taco Tuesday" IS social' in prompt
    assert '"Team standup" IS NOT social' in prompt


def test_classify_social_event_prompt_omits_examples_block_when_empty(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_social": true, "confidence": 0.9}')
    ai_metrics.classify_social_event("Dinner", "", "", [], examples=None)
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "corrected past classifications" not in prompt

    ai_metrics.classify_social_event("Dinner", "", "", [], examples=[])
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "corrected past classifications" not in prompt


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


def test_classify_work_ride_returns_parsed_dict(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_work": true, "confidence": 0.8}')
    out = ai_metrics.classify_work_ride("Uber", "Your Tuesday trip with Uber", "Jul 14, 2026 7:15 AM")
    assert out == {"is_work": True, "confidence": 0.8}


def test_classify_work_ride_prompt_includes_subject_and_snippet(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_work": false, "confidence": 0.5}')
    ai_metrics.classify_work_ride("Lyft", "Your ride with Lyft", "Airport pickup at 6am")
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Your ride with Lyft" in prompt
    assert "Airport pickup at 6am" in prompt


def test_classify_work_ride_garbage_json_defaults(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, "not json at all")
    out = ai_metrics.classify_work_ride("Uber", "Your trip", "")
    assert out == {"is_work": False, "confidence": 0.0}


def test_classify_work_ride_prompt_includes_examples_when_present(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_work": true, "confidence": 0.9}')
    examples = [
        {"subject": "Your trip to the airport", "user_is_work": True},
        {"subject": "Your ride home", "user_is_work": False},
    ]
    ai_metrics.classify_work_ride("Uber", "Your trip", "", examples=examples)
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "The user has corrected past classifications:" in prompt
    assert '"Your trip to the airport" IS work' in prompt
    assert '"Your ride home" IS NOT work' in prompt


def test_classify_work_ride_prompt_omits_examples_block_when_absent(mock_anthropic):
    import ai_metrics
    _set_response(mock_anthropic, '{"is_work": false, "confidence": 0.5}')
    ai_metrics.classify_work_ride("Uber", "Your trip", "", examples=None)
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "corrected past classifications" not in prompt

    ai_metrics.classify_work_ride("Uber", "Your trip", "", examples=[])
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "corrected past classifications" not in prompt
