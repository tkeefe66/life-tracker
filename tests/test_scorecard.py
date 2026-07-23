from datetime import date, datetime, timedelta

import pytz


def _seed_week(db):
    """Data inside week 2026-07-13 (Mon) … 2026-07-19 (Sun) — all in the past."""
    db.seed_default_targets()
    db.record_checkin("2026-07-13", "gym")
    db.record_checkin("2026-07-14", "gym")
    db.record_checkin("2026-07-14", "alcohol", level=2)
    db.add_delivery_order("m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "order")
    db.add_delivery_order("m2", "DoorDash", "2026-07-16T19:30:00-06:00", "order")
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)


def test_scorecard_counts(temp_db_path):
    import database as db
    from app.scorecard import scorecard_for_week
    _seed_week(db)
    card = scorecard_for_week(date(2026, 7, 13))
    m = card["metrics"]
    assert m["gym"]["count"] == 2
    assert m["alcohol"]["count"] == 1
    assert m["delivery"]["count"] == 2
    assert m["social"]["count"] == 1
    assert m["gym"]["hit"] is False      # 2 < floor 3
    assert m["delivery"]["hit"] is False  # 2 > ceiling 1
    assert m["alcohol"]["hit"] is True    # 1 <= ceiling 2


def test_social_excludes_future_events(temp_db_path):
    import database as db
    from app.scorecard import scorecard_for_week
    db.seed_default_targets()
    tz = pytz.timezone("America/Denver")
    future_start = datetime.now(tz) + timedelta(hours=2)
    future_end = future_start + timedelta(hours=2)
    db.upsert_calendar_event("evf", "Party tonight", future_start.isoformat(), future_end.isoformat())
    db.set_event_classification("evf", True, 0.9)
    card = scorecard_for_week(future_start.date())
    assert card["metrics"]["social"]["count"] == 0  # hasn't occurred yet


def test_manual_social_event_counts_before_it_would_have_occurred(temp_db_path):
    """Manual events are user-asserted facts, not calendar predictions — they must
    count toward the week and social_spend immediately, regardless of the synthetic
    12:00-13:00 span the API gives them or what time of day it currently is."""
    import database as db
    from app.scorecard import counts_for_week, scorecard_for_week, _local_today
    db.seed_default_targets()
    today = _local_today()
    day = today.isoformat()
    # end_at far in the future relative to "now" so an occurrence gate would exclude it.
    db.add_manual_social_event("manual:trivia", "Trivia night", f"{day}T23:58:00", f"{day}T23:59:00", amount=15.0)
    assert counts_for_week(today)["social"] == 1
    assert scorecard_for_week(today)["social_spend"] == 15.0


def test_history_excludes_current_week_and_computes_streaks(temp_db_path):
    import database as db
    from app.scorecard import history
    db.seed_default_targets()
    out = history(weeks=4)
    assert len(out["weeks"]) == 4
    assert set(out["streaks"].keys()) == {"delivery", "gym", "social", "alcohol", "substances"}
    import metrics
    from datetime import date as d
    today_week_start = metrics.week_bounds(d.today())[0].isoformat()
    assert all(w["week_start"] != today_week_start for w in out["weeks"])


def test_today_snapshot(temp_db_path, monkeypatch):
    import database as db
    from app import scorecard
    db.seed_default_targets()
    today = scorecard._local_today().isoformat()
    db.record_checkin(today, "gym")
    db.record_checkin(today, "alcohol", level=1)
    snap = scorecard.today_snapshot()
    assert snap["date"] == today
    assert snap["gym"] is True
    assert snap["alcohol_level"] == 1
    assert snap["deliveries"] == []
    assert snap["social_events"] == []
