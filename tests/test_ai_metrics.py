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
