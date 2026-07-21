from datetime import date

from metrics import METRICS, build_scorecard, is_hit, streaks, week_bounds


def test_week_bounds_monday_start():
    assert week_bounds(date(2026, 7, 20)) == (date(2026, 7, 20), date(2026, 7, 26))  # a Monday
    assert week_bounds(date(2026, 7, 22)) == (date(2026, 7, 20), date(2026, 7, 26))  # Wednesday
    assert week_bounds(date(2026, 7, 26)) == (date(2026, 7, 20), date(2026, 7, 26))  # Sunday


def test_is_hit_directions():
    assert is_hit("ceiling", 1, 1) is True
    assert is_hit("ceiling", 2, 1) is False
    assert is_hit("floor", 3, 3) is True
    assert is_hit("floor", 2, 3) is False


def test_build_scorecard_shape():
    targets = {k: {"direction": m["direction"], "value": m["default_target"]} for k, m in METRICS.items()}
    card = build_scorecard(date(2026, 7, 22), {"gym": 3, "delivery": 2}, targets)
    assert card["week_start"] == "2026-07-20"
    assert card["week_end"] == "2026-07-26"
    assert card["metrics"]["gym"]["hit"] is True
    assert card["metrics"]["delivery"]["hit"] is False
    assert card["metrics"]["social"]["count"] == 0  # missing count defaults to 0
    assert card["metrics"]["alcohol"]["label"] == "Alcohol days"


def _card(hits: dict):
    return {"metrics": {k: {"hit": v} for k, v in hits.items()}}


def test_streaks_counts_backward_from_latest():
    history = [
        _card({"gym": True, "delivery": True, "social": False, "alcohol": True}),
        _card({"gym": True, "delivery": False, "social": True, "alcohol": True}),
        _card({"gym": True, "delivery": True, "social": True, "alcohol": False}),
    ]
    s = streaks(history)
    assert s == {"gym": 3, "delivery": 1, "social": 2, "alcohol": 0}


def test_streaks_empty_history():
    assert streaks([]) == {"gym": 0, "delivery": 0, "social": 0, "alcohol": 0}
