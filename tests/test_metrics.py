from datetime import date

from metrics import METRICS, build_scorecard, is_hit, streaks, week_bounds


def test_week_bounds_monday_start():
    assert week_bounds(date(2026, 7, 20)) == (date(2026, 7, 20), date(2026, 7, 26))  # a Monday
    assert week_bounds(date(2026, 7, 22)) == (date(2026, 7, 20), date(2026, 7, 26))  # Wednesday
    assert week_bounds(date(2026, 7, 26)) == (date(2026, 7, 20), date(2026, 7, 26))  # Sunday


def test_substances_metric_defined():
    m = METRICS["substances"]
    assert (m["label"], m["direction"], m["default_target"], m.get("private")) == \
        ("Substances", "ceiling", 0, True)


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
        _card({"gym": True, "delivery": True, "social": False, "alcohol": True, "substances": True}),
        _card({"gym": True, "delivery": False, "social": True, "alcohol": True, "substances": True}),
        _card({"gym": True, "delivery": True, "social": True, "alcohol": False, "substances": True}),
    ]
    s = streaks(history)
    assert s == {"gym": 3, "delivery": 1, "social": 2, "alcohol": 0, "substances": 3}


def test_streaks_empty_history():
    assert streaks([]) == {"gym": 0, "delivery": 0, "social": 0, "alcohol": 0, "substances": 0}


from metrics import (
    co_occurrence, noticings, trend_direction, weekday_counts, weekday_skew,
)


def test_weekday_counts_monday_first():
    # 2026-07-20 is a Monday, 2026-07-26 a Sunday
    assert weekday_counts(["2026-07-20", "2026-07-20", "2026-07-26"]) == [2, 0, 0, 0, 0, 0, 1]
    assert weekday_counts([]) == [0] * 7


def test_trend_direction():
    assert trend_direction([1, 1]) is None
    assert trend_direction([0, 0, 0, 2, 2, 2]) == "up"
    assert trend_direction([3, 3, 3, 1, 1, 1]) == "down"
    assert trend_direction([2, 2, 2, 2, 2, 2]) == "flat"
    assert trend_direction([9, 9, 0, 0, 0, 2, 2, 2]) == "up"  # only last 6 count


def test_weekday_skew():
    sundays = ["2026-07-05", "2026-07-12", "2026-07-19"]
    assert weekday_skew(sundays + ["2026-07-20"]) == (6, 0.75)
    assert weekday_skew(sundays) is None                     # < 4 events
    assert weekday_skew(["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23"]) is None  # no cluster


def test_co_occurrence():
    a = ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"]
    b = ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-05"]
    assert co_occurrence(a, b) == 0.6  # 3 shared / 5 union
    assert co_occurrence(a[:3], b) is None


def test_noticings_caps_at_three_and_prioritizes():
    shared = ["2026-07-04", "2026-07-11", "2026-07-18", "2026-07-25"]
    date_lists = {"alcohol": shared, "delivery": shared, "gym": [], "social": []}
    series = {"gym": [0, 0, 0, 2, 2, 2], "social": [3, 3, 3, 1, 1, 1],
              "delivery": [1] * 6, "alcohol": [1] * 6}
    out = noticings(date_lists, series)
    assert len(out) == 3
    assert "same day" in out[0]          # co-occurrence first
    assert out[1].startswith("Delivery") or out[1].startswith("Alcohol")  # skew next


def test_noticings_silent_on_sparse_data():
    assert noticings({"gym": ["2026-07-20"]}, {"gym": [1, 1]}) == []
