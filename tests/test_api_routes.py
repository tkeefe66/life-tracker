import datetime

import pytest
from fastapi.testclient import TestClient


def _client(temp_db_path):
    import database as db
    from app.api import create_app
    db.seed_default_targets()
    # https base_url so the login cookie's Secure flag round-trips in TestClient
    client = TestClient(create_app(), base_url="https://testserver")
    client.post("/api/login", json={"password": "test-password"})
    return client


@pytest.fixture
def client(temp_db_path):
    return _client(temp_db_path)


def test_checkin_roundtrip(temp_db_path):
    client = _client(temp_db_path)
    assert client.post("/api/checkins", json={"type": "gym"}).status_code == 200
    assert client.post("/api/checkins", json={"type": "alcohol", "level": 2}).status_code == 200
    snap = client.get("/api/today").json()
    assert snap["gym"] is True and snap["alcohol_level"] == 2
    assert client.delete("/api/checkins/gym").status_code == 200
    assert client.get("/api/today").json()["gym"] is False


def test_substances_checkin_roundtrip(temp_db_path):
    client = _client(temp_db_path)
    assert client.post("/api/checkins", json={"type": "substances"}).status_code == 200
    snap = client.get("/api/today").json()
    assert snap["substances"] is True
    card = client.get("/api/scorecard").json()
    assert card["metrics"]["substances"]["count"] == 1
    assert card["metrics"]["substances"]["hit"] is False  # ceiling 0: any day is a miss
    assert client.delete("/api/checkins/substances").status_code == 200
    assert client.get("/api/today").json()["substances"] is False
    assert client.get("/api/scorecard").json()["metrics"]["substances"]["hit"] is True


def test_checkin_validation(temp_db_path):
    client = _client(temp_db_path)
    assert client.post("/api/checkins", json={"type": "yoga"}).status_code == 422
    assert client.post("/api/checkins", json={"type": "alcohol"}).status_code == 400  # level required
    assert client.post("/api/checkins", json={"type": "alcohol", "level": 5}).status_code == 422


def test_scorecard_and_history_endpoints(temp_db_path):
    client = _client(temp_db_path)
    card = client.get("/api/scorecard").json()
    assert set(card["metrics"].keys()) == {"delivery", "gym", "social", "alcohol", "substances"}
    hist = client.get("/api/history?weeks=4").json()
    assert len(hist["weeks"]) == 4


def test_targets_update(temp_db_path):
    client = _client(temp_db_path)
    assert client.put("/api/targets", json={"gym": 4}).status_code == 200
    assert client.get("/api/targets").json()["gym"]["value"] == 4
    assert client.put("/api/targets", json={"nope": 1}).status_code == 400
    assert client.put("/api/targets", json={"gym": -1}).status_code == 400
    assert client.put("/api/targets", json={"gym": True}).status_code == 400


def test_targets_update_rejects_oversized_value(temp_db_path):
    client = _client(temp_db_path)
    # Bounded pydantic model: an out-of-range value 400s instead of 500ing.
    assert client.put("/api/targets", json={"gym": 100001}).status_code == 400


def test_settings_roundtrip(temp_db_path):
    client = _client(temp_db_path)
    s = client.get("/api/settings").json()
    assert s["telegram_push"] == "off"
    assert "google_configured" in s
    assert "backup_last_run" in s and "backup_last_status" in s
    assert client.put("/api/settings", json={"telegram_push": "on"}).status_code == 200
    assert client.get("/api/settings").json()["telegram_push"] == "on"


def test_checkin_rejects_future_or_malformed_date(temp_db_path):
    client = _client(temp_db_path)
    future = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    assert client.post("/api/checkins", json={"type": "gym", "date": future}).status_code == 400
    assert client.post("/api/checkins", json={"type": "gym", "date": "not-a-date"}).status_code == 400
    assert client.delete(f"/api/checkins/gym?date={future}").status_code == 400
    assert client.delete("/api/checkins/gym?date=garbage").status_code == 400


def test_checkin_past_date_lands_on_that_day(temp_db_path):
    client = _client(temp_db_path)
    past = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    assert client.post("/api/checkins", json={"type": "gym", "date": past}).status_code == 200
    snap = client.get(f"/api/today?date={past}").json()
    assert snap["date"] == past
    assert snap["gym"] is True
    assert client.get("/api/today").json()["gym"] is False
    assert client.delete(f"/api/checkins/gym?date={past}").status_code == 200
    assert client.get(f"/api/today?date={past}").json()["gym"] is False


def test_today_rejects_future_or_malformed_date(temp_db_path):
    client = _client(temp_db_path)
    future = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    assert client.get(f"/api/today?date={future}").status_code == 400
    assert client.get("/api/today?date=garbage").status_code == 400


def test_insights_shape_and_weekday_counts(temp_db_path):
    client = _client(temp_db_path)
    past = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    client.post("/api/checkins", json={"type": "gym", "date": past})
    ins = client.get("/api/insights?weeks=12").json()
    assert set(ins.keys()) == {"weeks", "streaks", "weekday_counts", "noticings"}
    assert len(ins["weeks"]) == 12
    assert sum(ins["weekday_counts"]["gym"]) == 1
    assert isinstance(ins["noticings"], list)


def test_reflection_generates_once_then_caches(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        type("T", (), {"text": '{"reflection": "Steady week."}'})()
    ]
    client = _client(temp_db_path)
    first = client.post("/api/reflection")
    assert first.status_code == 200
    assert first.json()["text"] == "Steady week."
    second = client.post("/api/reflection")
    assert second.json() == first.json()
    assert mock_anthropic.messages.create.call_count == 1


def test_reflection_204_on_generation_failure(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [type("T", (), {"text": "garbage"})()]
    client = _client(temp_db_path)
    assert client.post("/api/reflection").status_code == 204


def test_reflection_get_is_rejected(temp_db_path, mock_anthropic):
    # GET must no longer trigger a write / paid AI call from a top-level cross-site nav.
    # When frontend/dist exists, the SPA catch-all mount claims any unmatched path and
    # this surfaces as a plain 404; on a clean clone with no built frontend, FastAPI's
    # own routing returns 405 instead. Either way, GET performs no write and makes no
    # Claude call — that's the actual property under test, not the exact status code.
    client = _client(temp_db_path)
    assert client.get("/api/reflection").status_code in (404, 405)
    assert mock_anthropic.messages.create.call_count == 0


def test_reflection_excludes_private_metrics(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        type("T", (), {"text": '{"reflection": "Steady week."}'})()
    ]
    client = _client(temp_db_path)
    # Land the check-in mid-way through the exact week /reflection reflects on,
    # using the route's own week math — a fixed day offset goes vacuous on Mondays.
    import metrics as metrics_mod
    from app.scorecard import _local_today
    last_wed = metrics_mod.week_bounds(_local_today())[0] - datetime.timedelta(weeks=1) + datetime.timedelta(days=2)
    client.post("/api/checkins", json={"type": "substances", "date": last_wed.isoformat()})
    resp = client.post("/api/reflection")
    assert resp.status_code == 200
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Substances" not in prompt
    assert "Gym" in prompt  # non-private metrics still present — filter, not an empty card


def test_deliveries_list_shape_and_order(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    d1 = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    d2 = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    db.add_delivery_order("m1", "DoorDash", f"{d1}T18:00:00", "Your order")
    db.add_delivery_order("m2", "Uber Eats", f"{d2}T19:30:00", "Your receipt", 16.31)
    body = client.get("/api/deliveries").json()
    assert [o["service"] for o in body["orders"]] == ["Uber Eats", "DoorDash"]
    assert set(body["orders"][0].keys()) == {"service", "subject", "ordered_at", "amount"}
    assert body["orders"][0]["amount"] == 16.31
    assert body["orders"][1]["amount"] is None
    # days clamp: 0 -> 1; a 1-day window excludes both seeded orders
    assert client.get("/api/deliveries?days=0").json()["orders"] == []
    # settings gains the result field (None when never written)
    assert "gmail_last_result" in client.get("/api/settings").json()


def test_scorecard_delivery_spend_sums_amounts_null_as_zero(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    db.add_delivery_order("m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "order", 16.31)
    db.add_delivery_order("m2", "DoorDash", "2026-07-16T19:30:00-06:00", "order")  # amount NULL
    card = client.get("/api/scorecard?week_start=2026-07-13").json()
    assert card["delivery_spend"] == 16.31


# ── Social: manual events, overrides, delete ─────────────────────────────────

def test_post_social_creates_manual_event(temp_db_path):
    client = _client(temp_db_path)
    resp = client.post("/api/social", json={"name": "Trivia night", "amount": 12.5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Trivia night"
    assert body["source"] == "manual"
    assert body["amount"] == 12.5

    snap = client.get("/api/today").json()
    ev = next(e for e in snap["social_events"] if e["title"] == "Trivia night")
    assert ev["source"] == "manual"
    assert ev["amount"] == 12.5
    assert ev["gcal_event_id"] == body["gcal_event_id"]
    assert ev["is_social"] is True  # editor initializes its checkbox from this, not a hardcoded guess


def test_post_social_increments_scorecard_social_count(temp_db_path):
    """Plan-required test: creating a manual event must count toward the week
    immediately, not just once its synthetic 12:00-13:00 span has 'occurred' —
    robust to whatever time of day the suite runs."""
    client = _client(temp_db_path)
    before = client.get("/api/scorecard").json()["metrics"]["social"]["count"]
    resp = client.post("/api/social", json={"name": "Trivia night"})
    assert resp.status_code == 200
    after = client.get("/api/scorecard").json()["metrics"]["social"]["count"]
    assert after == before + 1


def test_post_social_rejects_future_date(temp_db_path):
    client = _client(temp_db_path)
    future = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    resp = client.post("/api/social", json={"name": "Party", "date": future})
    assert resp.status_code == 400


def test_post_social_rejects_negative_amount(temp_db_path):
    client = _client(temp_db_path)
    resp = client.post("/api/social", json={"name": "Party", "amount": -5})
    assert resp.status_code == 422


def test_post_social_past_date_lands_on_that_day(temp_db_path):
    client = _client(temp_db_path)
    past = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    resp = client.post("/api/social", json={"name": "Dinner", "date": past})
    assert resp.status_code == 200
    snap = client.get(f"/api/today?date={past}").json()
    assert any(e["title"] == "Dinner" for e in snap["social_events"])


def test_patch_social_renames_event(temp_db_path):
    client = _client(temp_db_path)
    created = client.post("/api/social", json={"name": "Old name"}).json()
    event_id = created["gcal_event_id"]
    resp = client.patch(f"/api/social/{event_id}", json={"title": "New name"})
    assert resp.status_code == 200
    snap = client.get("/api/today").json()
    assert any(e["title"] == "New name" for e in snap["social_events"])


def test_patch_social_is_social_false_drops_from_count(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    before = client.get("/api/scorecard?week_start=2026-07-13").json()
    assert before["metrics"]["social"]["count"] == 1
    resp = client.patch("/api/social/ev1", json={"is_social": False})
    assert resp.status_code == 200
    after = client.get("/api/scorecard?week_start=2026-07-13").json()
    assert after["metrics"]["social"]["count"] == 0


def test_patch_social_unknown_id_404(temp_db_path):
    client = _client(temp_db_path)
    resp = client.patch("/api/social/nope", json={"title": "x"})
    assert resp.status_code == 404


def test_patch_social_rejects_negative_amount(temp_db_path):
    client = _client(temp_db_path)
    created = client.post("/api/social", json={"name": "Party"}).json()
    resp = client.patch(f"/api/social/{created['gcal_event_id']}", json={"amount": -1})
    assert resp.status_code == 422


def test_patch_social_clears_amount_with_explicit_null(temp_db_path):
    client = _client(temp_db_path)
    created = client.post("/api/social", json={"name": "Party", "amount": 300}).json()
    event_id = created["gcal_event_id"]
    resp = client.patch(f"/api/social/{event_id}", json={"amount": None})
    assert resp.status_code == 200
    import database as db
    assert db.get_event(event_id)["amount"] is None


def test_patch_social_omitted_amount_leaves_it_untouched(temp_db_path):
    client = _client(temp_db_path)
    created = client.post("/api/social", json={"name": "Party", "amount": 300}).json()
    event_id = created["gcal_event_id"]
    resp = client.patch(f"/api/social/{event_id}", json={"title": "Party v2"})
    assert resp.status_code == 200
    import database as db
    assert db.get_event(event_id)["amount"] == 300


def test_patch_social_clears_title_override_with_explicit_null(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    db.set_event_overrides("ev1", {"user_title": "Sam's birthday dinner"})
    resp = client.patch("/api/social/ev1", json={"title": None})
    assert resp.status_code == 200
    row = db.get_event("ev1")
    assert row["user_title"] is None


def test_delete_social_manual_event(temp_db_path):
    client = _client(temp_db_path)
    created = client.post("/api/social", json={"name": "Cancelled plan"}).json()
    event_id = created["gcal_event_id"]
    resp = client.delete(f"/api/social/{event_id}")
    assert resp.status_code == 200
    snap = client.get("/api/today").json()
    assert not any(e["title"] == "Cancelled plan" for e in snap["social_events"])


def test_delete_social_gcal_event_rejected(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    resp = client.delete("/api/social/ev1")
    assert resp.status_code == 400


def test_delete_social_unknown_id_404(temp_db_path):
    client = _client(temp_db_path)
    resp = client.delete("/api/social/nope")
    assert resp.status_code == 404


def test_social_spend_sums_amounts_on_scorecard(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    db.add_manual_social_event("manual:1", "Dinner", "2026-07-15T12:00:00", "2026-07-15T13:00:00", amount=30.0)
    db.add_manual_social_event("manual:2", "Coffee", "2026-07-16T12:00:00", "2026-07-16T13:00:00")  # NULL amount
    card = client.get("/api/scorecard?week_start=2026-07-13").json()
    assert card["social_spend"] == 30.0


# ── Rides ─────────────────────────────────────────────────────────────────────

def test_get_rides_shape_order_and_days_clamp(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    d1 = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    d2 = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    db.add_ride("r1", "Uber", f"{d1}T08:00:00", f"{d1}T08:00", "Your trip", 12.0)
    db.add_ride("r2", "Lyft", f"{d2}T18:00:00", f"{d2}T18:00", "Your ride", 20.0)
    body = client.get("/api/rides").json()
    assert [r["service"] for r in body["rides"]] == ["Lyft", "Uber"]  # newest-first
    row = body["rides"][0]
    assert set(row.keys()) == {"id", "service", "ride_at", "subject", "amount",
                                "ai_is_work", "user_is_work", "is_work"}  # ai_confidence not exposed
    assert row["is_work"] is False  # unresolved verdict defaults to not-work


def test_get_rides_days_lower_bound_clamps_to_1(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    db.add_ride("r1", "Uber", f"{yesterday}T08:00:00", f"{yesterday}T08:00", "Yesterday ride", 12.0)

    # A literal 0-day window would be [today, today] and exclude yesterday's ride;
    # days=0 clamping to 1 reaches back to yesterday and includes it.
    body = client.get("/api/rides?days=0").json()
    assert [r["subject"] for r in body["rides"]] == ["Yesterday ride"]


def test_get_rides_days_upper_bound_clamps_to_365(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    within = (datetime.date.today() - datetime.timedelta(days=200)).isoformat()
    beyond = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
    db.add_ride("r1", "Uber", f"{within}T08:00:00", f"{within}T08:00", "Within 365", 12.0)
    db.add_ride("r2", "Lyft", f"{beyond}T08:00:00", f"{beyond}T08:00", "Beyond 365", 20.0)

    # days=1000 clamps to 365: the 400-day-old ride is excluded even though 400 < 1000,
    # proving the clamp actually caps the window rather than passing days through raw.
    body = client.get("/api/rides?days=1000").json()
    assert [r["subject"] for r in body["rides"]] == ["Within 365"]


def test_get_rides_resolved_is_work_reflects_user_override(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    db.add_ride("r1", "Uber", "2026-07-15T08:00:00-06:00", "2026-07-15T08:00", "Your trip", 12.0)
    ride = db.get_rides_range("2026-07-14", "2026-07-20")[0]
    db.set_ride_classification(ride["id"], True, 0.9)  # AI flags work but never excludes alone
    body = client.get("/api/rides").json()
    assert body["rides"][0]["ai_is_work"] is True
    assert body["rides"][0]["is_work"] is False  # unconfirmed AI flag does not resolve to work
    db.set_ride_work_override(ride["id"], True)
    body = client.get("/api/rides").json()
    assert body["rides"][0]["is_work"] is True  # confirmed user verdict resolves to work


def test_patch_ride_sets_override(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    db.add_ride("r1", "Uber", "2026-07-15T08:00:00-06:00", "2026-07-15T08:00", "Your trip", 12.0)
    ride_id = db.get_rides_range("2026-07-14", "2026-07-20")[0]["id"]
    resp = client.patch(f"/api/rides/{ride_id}", json={"is_work": True})
    assert resp.status_code == 200
    row = db.get_rides_range("2026-07-14", "2026-07-20")[0]
    assert bool(row["user_is_work"]) is True


def test_patch_ride_unknown_id_404(temp_db_path):
    client = _client(temp_db_path)
    resp = client.patch("/api/rides/999999", json={"is_work": True})
    assert resp.status_code == 404


# ── /api/week-days: the day-by-day view's data source ────────────────────────
#
# Seeded relative to app.scorecard._local_today(), never a hardcoded calendar
# date — three tests on this repo already rotted that way.

def test_week_days_shape_grouping_and_work_ride_exclusion(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    import metrics
    from app.scorecard import _local_today
    this_monday = metrics.week_bounds(_local_today())[0]
    last_monday = this_monday - datetime.timedelta(weeks=1)
    mon = last_monday
    tue = last_monday + datetime.timedelta(days=1)
    wed = last_monday + datetime.timedelta(days=2)

    db.record_checkin(mon.isoformat(), "gym")
    db.record_checkin(tue.isoformat(), "alcohol", level=2)
    db.add_delivery_order("m1", "Uber Eats", f"{wed.isoformat()}T12:00:00-06:00", "Lunch order", 12.0)
    db.add_delivery_order("m2", "DoorDash", f"{wed.isoformat()}T19:00:00-06:00", "Dinner order", 18.0)
    db.add_ride("r1", "Uber", f"{mon.isoformat()}T08:00:00-06:00", f"{mon.isoformat()}T08:00", "Personal trip", 10.0)
    db.add_ride("r2", "Lyft", f"{tue.isoformat()}T09:00:00-06:00", f"{tue.isoformat()}T09:00", "Work trip", 40.0)
    rides = db.get_rides_range(last_monday.isoformat(), (last_monday + datetime.timedelta(days=6)).isoformat())
    by_subject = {r["subject"]: r["id"] for r in rides}
    db.set_ride_classification(by_subject["Personal trip"], True, 0.7)  # AI flags work, unconfirmed — still counted
    db.set_ride_work_override(by_subject["Work trip"], True)  # confirmed work — excluded from totals
    db.add_manual_social_event(
        "manual:dinner", "Dinner out", f"{wed.isoformat()}T18:00:00", f"{wed.isoformat()}T19:00:00", amount=25.0
    )

    body = client.get(f"/api/week-days?week_start={last_monday.isoformat()}").json()
    assert body["week_start"] == last_monday.isoformat()
    assert body["week_end"] == (last_monday + datetime.timedelta(days=6)).isoformat()

    days = body["days"]
    assert len(days) == 7
    expected_dates = [(last_monday + datetime.timedelta(days=i)).isoformat() for i in range(7)]
    assert [d["date"] for d in days] == expected_dates  # Monday-first, contiguous

    by_date = {d["date"]: d for d in days}
    empty_days = [d for iso, d in by_date.items() if iso not in {mon.isoformat(), tue.isoformat(), wed.isoformat()}]
    assert len(empty_days) == 4
    for d in empty_days:
        assert d["items"] == []
        assert d["total"] == 0
        assert d["gym"] is False
        assert d["alcohol_level"] is None
        assert d["substances"] is False

    mon_day = by_date[mon.isoformat()]
    assert mon_day["gym"] is True
    ride_item = next(i for i in mon_day["items"] if i["kind"] == "ride")
    assert ride_item["label"] == "Personal trip"
    assert ride_item["is_work"] is False  # AI flag alone never excludes
    assert mon_day["total"] == 10.0  # AI-flagged-but-unconfirmed ride IS counted

    tue_day = by_date[tue.isoformat()]
    assert tue_day["alcohol_level"] == 2
    work_item = next(i for i in tue_day["items"] if i["kind"] == "ride")
    assert work_item["label"] == "Work trip"
    assert work_item["is_work"] is True  # present in items, labeled work
    assert tue_day["total"] == 0  # but excluded from the day total

    wed_day = by_date[wed.isoformat()]
    assert len([i for i in wed_day["items"] if i["kind"] == "delivery"]) == 2
    social_item = next(i for i in wed_day["items"] if i["kind"] == "social")
    assert social_item["label"] == "Dinner out"
    assert wed_day["total"] == 12.0 + 18.0 + 25.0

    assert body["week_total"] == mon_day["total"] + tue_day["total"] + wed_day["total"]
    assert body["week_total"] == 10.0 + 0 + 55.0


def test_week_days_malformed_start_400(temp_db_path):
    client = _client(temp_db_path)
    assert client.get("/api/week-days?week_start=not-a-date").status_code == 400


def test_week_days_midweek_start_resolves_to_its_monday(temp_db_path):
    client = _client(temp_db_path)
    import metrics
    from app.scorecard import _local_today
    this_monday = metrics.week_bounds(_local_today())[0]
    mid_week = this_monday + datetime.timedelta(days=3)  # a Thursday
    body = client.get(f"/api/week-days?week_start={mid_week.isoformat()}").json()
    assert body["week_start"] == this_monday.isoformat()


def test_scorecard_rides_count_and_spend_exclude_confirmed_work_include_ai_flagged(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    db.add_ride("r1", "Uber", "2026-07-15T08:00:00-06:00", "2026-07-15T08:00", "Personal trip", 12.0)
    db.add_ride("r2", "Uber", "2026-07-16T08:00:00-06:00", "2026-07-16T08:00", "AI-flagged trip", 30.0)
    db.add_ride("r3", "Lyft", "2026-07-17T08:00:00-06:00", "2026-07-17T08:00", "Confirmed work trip", 50.0)
    rides = db.get_rides_range("2026-07-13", "2026-07-19")
    by_subject = {r["subject"]: r["id"] for r in rides}
    db.set_ride_classification(by_subject["AI-flagged trip"], True, 0.8)  # flagged, not confirmed
    db.set_ride_work_override(by_subject["Confirmed work trip"], True)  # confirmed — excluded

    card = client.get("/api/scorecard?week_start=2026-07-13").json()
    assert card["rides_count"] == 2  # r1 + r2 count; r3 (confirmed work) excluded
    assert card["rides_spend"] == 42.0  # 12.0 + 30.0; r3's 50.0 excluded
    assert "rides" not in card["metrics"]  # tracking-only — never a scored metric


# ── /api/spend: the money view's data source ─────────────────────────────────
#
# All dates below are seeded relative to app.scorecard._local_today() (the
# pattern tests/test_scorecard.py uses) so these hold on any date, not just
# during the week they were written — a fixed-date seed plus an unanchored
# ?weeks= query is clock-dependent and will fail once the real week rolls
# past whatever was hardcoded.

def test_spend_weeks_series_oldest_first_including_zero_weeks(temp_db_path):
    """A 4-week window with spend seeded in only two of the weeks still returns
    one dense entry per week, oldest-first, the untouched weeks reading zero."""
    client = _client(temp_db_path)
    import database as db
    import metrics
    from app.scorecard import _local_today
    this_monday = metrics.week_bounds(_local_today())[0]
    oldest = this_monday - datetime.timedelta(weeks=3)  # oldest week in a weeks=4 window
    third = this_monday - datetime.timedelta(weeks=1)
    db.add_delivery_order("m1", "Uber Eats", f"{oldest.isoformat()}T19:00:00-06:00", "order", 15.0)
    db.add_delivery_order("m2", "DoorDash", f"{third.isoformat()}T19:00:00-06:00", "order", 20.0)
    body = client.get("/api/spend?weeks=4").json()
    weeks = body["weeks"]
    expected_starts = [(this_monday - datetime.timedelta(weeks=i)).isoformat() for i in (3, 2, 1, 0)]
    assert [w["week_start"] for w in weeks] == expected_starts
    assert weeks[0] == {"week_start": expected_starts[0], "delivery": 15.0, "rides": 0, "social": 0}
    assert weeks[1] == {"week_start": expected_starts[1], "delivery": 0, "rides": 0, "social": 0}
    assert weeks[2]["delivery"] == 20.0
    assert weeks[3]["week_start"] == expected_starts[3]  # in-progress current week, still present


def test_spend_by_service_matches_aggregate_across_the_window(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    import metrics
    from app.scorecard import _local_today
    this_monday = metrics.week_bounds(_local_today())[0]
    w3 = this_monday - datetime.timedelta(weeks=3)
    w1 = this_monday - datetime.timedelta(weeks=1)
    db.add_delivery_order("m1", "Uber Eats", f"{w3.isoformat()}T19:00:00-06:00", "order", 15.0)
    db.add_delivery_order("m2", "Uber Eats", f"{w1.isoformat()}T19:00:00-06:00", "order", 10.0)
    db.add_delivery_order("m3", "DoorDash", f"{w1.isoformat()}T19:00:00-06:00", "order", 8.0)
    body = client.get("/api/spend?weeks=4").json()
    by_service = {(r["kind"], r["service"]): r["amount"] for r in body["by_service"]}
    assert by_service[("delivery", "Uber Eats")] == 25.0
    assert by_service[("delivery", "DoorDash")] == 8.0
    # sorted by amount descending
    assert body["by_service"][0]["service"] == "Uber Eats"


def test_spend_includes_social_spend_in_weeks_by_service_and_items(temp_db_path):
    """Social is the one category whose aggregation path differs from delivery
    and rides (the _social_counts filter, the ("social", "Social") aggregation
    branch, and title -> items.label) — must be exercised directly, not just
    implied by delivery/ride coverage."""
    client = _client(temp_db_path)
    import database as db
    import metrics
    from app.scorecard import _local_today
    this_monday = metrics.week_bounds(_local_today())[0]
    w1 = this_monday - datetime.timedelta(weeks=1)
    db.add_manual_social_event(
        "manual:dinner", "Dinner out", f"{w1.isoformat()}T12:00:00", f"{w1.isoformat()}T13:00:00", amount=25.0
    )
    body = client.get("/api/spend?weeks=4").json()
    week_row = next(w for w in body["weeks"] if w["week_start"] == w1.isoformat())
    assert week_row["social"] == 25.0
    assert {"kind": "social", "service": "Social", "amount": 25.0} in body["by_service"]
    assert any(
        i["kind"] == "social" and i["service"] == "Social" and i["label"] == "Dinner out" and i["amount"] == 25.0
        for i in body["items"]
    )


def test_spend_excludes_confirmed_work_ride_from_weeks_by_service_and_items(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    import metrics
    from app.scorecard import _local_today
    this_monday = metrics.week_bounds(_local_today())[0]
    w1 = this_monday - datetime.timedelta(weeks=1)
    db.add_ride("r1", "Uber", f"{w1.isoformat()}T08:00:00-06:00", f"{w1.isoformat()}T08:00", "Personal trip", 12.0)
    db.add_ride("r2", "Lyft", f"{w1.isoformat()}T09:00:00-06:00", f"{w1.isoformat()}T09:00", "Work trip", 50.0)
    week_end = w1 + datetime.timedelta(days=6)
    rides = db.get_rides_range(w1.isoformat(), week_end.isoformat())
    by_subject = {r["subject"]: r["id"] for r in rides}
    db.set_ride_work_override(by_subject["Work trip"], True)  # confirmed work — excluded everywhere

    body = client.get("/api/spend?weeks=4").json()
    week_row = next(w for w in body["weeks"] if w["week_start"] == w1.isoformat())
    assert week_row["rides"] == 12.0
    assert not any(r["service"] == "Lyft" for r in body["by_service"])
    assert not any(i["service"] == "Lyft" for i in body["items"])
    assert any(i["service"] == "Uber" and i["label"] == "Personal trip" for i in body["items"])


def test_spend_items_newest_first_and_capped_at_100(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    import metrics
    from app.scorecard import _local_today
    this_monday = metrics.week_bounds(_local_today())[0]
    w1 = this_monday - datetime.timedelta(weeks=1)
    base = datetime.datetime.combine(w1, datetime.time(12, 0, 0))
    for i in range(105):
        at = (base + datetime.timedelta(minutes=i)).isoformat()
        db.add_delivery_order(f"m{i}", "Uber Eats", at, f"order {i}", 1.0)
    body = client.get("/api/spend?weeks=4").json()
    items = body["items"]
    assert len(items) == 100
    assert items[0]["label"] == "order 104"  # newest first
    assert items[1]["label"] == "order 103"


def test_spend_weeks_param_clamps_1_to_52(temp_db_path):
    client = _client(temp_db_path)
    assert len(client.get("/api/spend?weeks=0").json()["weeks"]) == 1
    assert len(client.get("/api/spend?weeks=999").json()["weeks"]) == 52


def test_spend_includes_the_in_progress_current_week(temp_db_path):
    """The window's final entry must be the current, still-in-progress week —
    not the last completed one. history()'s "completed weeks only" rationale
    (a partial week corrupts streaks) doesn't transfer to money: an order
    placed today has to be visible in Money immediately, same as it already
    is in Today's "Spent today" and Week's "Spent this week"."""
    client = _client(temp_db_path)
    import database as db
    import metrics
    from app.scorecard import _local_today
    today = _local_today()
    this_monday = metrics.week_bounds(today)[0]
    db.add_delivery_order("m-today", "Uber Eats", f"{today.isoformat()}T10:00:00-06:00", "order", 9.5)
    body = client.get("/api/spend?weeks=4").json()
    current_week = body["weeks"][-1]
    assert current_week["week_start"] == this_monday.isoformat()
    assert current_week["delivery"] == 9.5
    assert {"kind": "delivery", "service": "Uber Eats", "amount": 9.5} in body["by_service"]
    assert any(i["label"] == "order" and i["amount"] == 9.5 for i in body["items"])


def test_bank_debug_returns_accounts_and_flow_totals(client, temp_db_path):
    import database as db
    db.upsert_bank_account("chk", "Checking", "Wells Fargo", "checking")
    db.set_bank_account_role("chk", "spending")
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "chk")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -14.20, "COFFEE", "Coffee", "", "5814")
    db.set_bank_transaction_derived("t1", "spending", None, False)

    r = client.get("/api/bank/debug?start=2026-06-01&end=2026-08-01")
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["spending"] == pytest.approx(14.20)
    assert body["accounts"][0]["role"] == "spending"
    assert body["counts"]["spending"] == 1


def test_bank_debug_never_exposes_the_access_url(client, temp_db_path, monkeypatch):
    """The route never references `config` at all, so this monkeypatch exercises
    nothing — the protection is structural (the route has no path to the access
    URL), not behavioral. Kept as a guard against a future edit wiring the route
    to config; don't over-trust the test's name as proof of live enforcement."""
    import config
    monkeypatch.setattr(config, "SIMPLEFIN_ACCESS_URL",
                        "https://user:sup3rsecret@bridge.example.com/simplefin")
    r = client.get("/api/bank/debug?start=2026-06-01&end=2026-08-01")
    assert "sup3rsecret" not in r.text and "bridge.example.com" not in r.text


def test_bank_debug_rejects_a_malformed_date(client, temp_db_path):
    """This route's entire purpose in this phase is being the surface for
    eyeballing correctness — a typo'd date must 400, not silently return an
    empty result set."""
    r = client.get("/api/bank/debug?start=2026-06-01&end=not-a-date")
    assert r.status_code == 400

    r = client.get("/api/bank/debug?start=not-a-date&end=2026-08-01")
    assert r.status_code == 400


def test_set_account_role(client, temp_db_path):
    import database as db
    db.upsert_bank_account("chk", "Checking", "Wells Fargo", "checking")
    r = client.post("/api/bank/accounts/chk/role", json={"role": "spending"})
    assert r.status_code == 200
    assert next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "chk")["role"] == "spending"


def test_set_account_role_rejects_an_unknown_role(client, temp_db_path):
    import database as db
    db.upsert_bank_account("chk", "Checking", "Wells Fargo", "checking")
    r = client.post("/api/bank/accounts/chk/role", json={"role": "yacht"})
    assert r.status_code == 400


def test_set_account_role_404s_for_an_unknown_account(client, temp_db_path):
    r = client.post("/api/bank/accounts/nope/role", json={"role": "spending"})
    assert r.status_code == 404


# ── Bank: summary, triage, flow overrides, accounts (Task 5) ──────────────────

def _bank_account(db, sfid="acct-1", role="spending"):
    db.upsert_bank_account(sfid, "Everyday Checking", "Test Bank", "checking")
    db.set_bank_account_role(sfid, role)
    return next(a for a in db.get_bank_accounts() if a["simplefin_id"] == sfid)


def test_bank_summary_empty_db_returns_shaped_200(client, temp_db_path):
    resp = client.get("/api/bank/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "covered_from", "covered_to", "weeks", "totals", "spent", "tracked", "triage_counts",
    }
    assert body["weeks"] == []
    assert body["covered_from"] is None
    assert body["triage_counts"] == {"ambiguous": 0, "inflow_unknown": 0}


def test_bank_summary_weeks_param_clamps_to_52(client, temp_db_path):
    import database as db
    import metrics
    from app.scorecard import _local_today

    acct = _bank_account(db)
    this_monday = metrics.week_bounds(_local_today())[0]
    far_back = (this_monday - datetime.timedelta(weeks=60)).isoformat()
    today = _local_today().isoformat()
    db.upsert_bank_transaction("ancient", acct["id"], far_back, far_back, -10.0, "OLD", "", "", None)
    db.set_bank_transaction_derived("ancient", "spending", None, False)
    db.upsert_bank_transaction("recent", acct["id"], today, today, -5.0, "NEW", "", "", None)
    db.set_bank_transaction_derived("recent", "spending", None, False)

    body = client.get("/api/bank/summary?weeks=999").json()
    assert len(body["weeks"]) == 52  # not 999 -- proves the route clamps, not just money.summary in isolation


def test_bank_triage_shape(client, temp_db_path):
    resp = client.get("/api/bank/triage")
    assert resp.status_code == 200
    assert set(resp.json().keys()) == {"ambiguous", "inflow_unknown", "recent"}


def test_bank_triage_limit_caps_each_bucket(client, temp_db_path):
    import database as db

    acct = _bank_account(db)
    for i in range(3):
        sfid = f"ambig{i}"
        db.upsert_bank_transaction(sfid, acct["id"], "2026-07-01", "2026-07-01", -10.0 - i, "AMBIG", "", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, True)
    for i in range(3):
        sfid = f"inflow{i}"
        db.upsert_bank_transaction(sfid, acct["id"], "2026-07-01", "2026-07-01", 10.0 + i, "INFLOW", "", "", None)
        db.set_bank_transaction_derived(sfid, "inflow_unknown", None, False)

    body = client.get("/api/bank/triage?limit=1").json()
    assert len(body["ambiguous"]) == 1
    assert len(body["inflow_unknown"]) == 1


def test_patch_bank_transaction_flow_sets_and_clears_override(client, temp_db_path):
    import database as db

    acct = _bank_account(db)
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01", -20.0, "AMBIG", "", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, True)

    before = client.get("/api/bank/triage").json()
    assert any(r["simplefin_id"] == "t1" for r in before["ambiguous"])

    resp = client.post("/api/bank/transactions/t1/flow", json={"flow": "transfer"})
    assert resp.status_code == 200

    after = client.get("/api/bank/triage").json()
    assert not any(r["simplefin_id"] == "t1" for r in after["ambiguous"])  # left the queue

    resp = client.post("/api/bank/transactions/t1/flow", json={"flow": None})
    assert resp.status_code == 200

    restored = client.get("/api/bank/triage").json()
    assert any(r["simplefin_id"] == "t1" for r in restored["ambiguous"])  # clearing returns it


def test_patch_bank_transaction_flow_rejects_unknown_flow(client, temp_db_path):
    import database as db

    acct = _bank_account(db)
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01", -20.0, "X", "", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, False)
    resp = client.post("/api/bank/transactions/t1/flow", json={"flow": "nonsense"})
    assert resp.status_code == 400


def test_patch_bank_transaction_flow_unknown_id_404s(client, temp_db_path):
    resp = client.post("/api/bank/transactions/nope/flow", json={"flow": "transfer"})
    assert resp.status_code == 404


def test_bulk_flow_override_updates_count(client, temp_db_path):
    import database as db

    acct = _bank_account(db)
    db.upsert_bank_transaction("b1", acct["id"], "2026-07-01", "2026-07-01", -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("b1", "spending", None, False)
    db.upsert_bank_transaction("b2", acct["id"], "2026-07-01", "2026-07-01", -20.0, "Y", "", "", None)
    db.set_bank_transaction_derived("b2", "spending", None, False)

    resp = client.post("/api/bank/transactions/flow", json={"simplefin_ids": ["b1", "b2"], "flow": "transfer"})
    assert resp.status_code == 200
    assert resp.json() == {"updated": 2}


def test_bulk_flow_override_caps_at_200_ids(client, temp_db_path):
    ids = [f"id{i}" for i in range(201)]
    resp = client.post("/api/bank/transactions/flow", json={"simplefin_ids": ids, "flow": "transfer"})
    assert resp.status_code == 400


def test_bulk_flow_override_unknown_flow_writes_nothing(client, temp_db_path):
    import database as db

    acct = _bank_account(db)
    db.upsert_bank_transaction("b1", acct["id"], "2026-07-01", "2026-07-01", -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("b1", "spending", None, False)

    resp = client.post("/api/bank/transactions/flow", json={"simplefin_ids": ["b1"], "flow": "yacht"})
    assert resp.status_code == 400

    row = next(r for r in db.get_all_bank_transactions() if r["simplefin_id"] == "b1")
    assert row["user_flow"] is None  # nothing written


def test_get_bank_accounts_no_balance_key_and_no_url_leak(client, temp_db_path):
    import database as db

    db.upsert_bank_account("chk", "Checking", "Wells Fargo", "checking")
    resp = client.get("/api/bank/accounts")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list) and len(body) == 1
    for row in body:
        for key, value in row.items():
            assert "balance" not in key.lower()
            if isinstance(value, str):
                assert "http" not in value.lower()


# ── Auth: every protected route rejects an unauthenticated request ────────────
#
# No such enumeration existed before this task -- the plan's step 5 says to add
# to "whichever existing parametrized auth test enumerates protected routes",
# but no test in this suite does that (verified by grep across tests/). This is
# the enumeration; new protected routes should be appended here going forward.

PROTECTED_ROUTES = [
    ("get", "/api/bank/summary"),
    ("get", "/api/bank/triage"),
    ("post", "/api/bank/transactions/some-id/flow"),
    ("post", "/api/bank/transactions/flow"),
    ("get", "/api/bank/accounts"),
]


@pytest.mark.parametrize("method,path", PROTECTED_ROUTES)
def test_bank_routes_require_auth(temp_db_path, method, path):
    from app.api import create_app
    client = TestClient(create_app(), base_url="https://testserver")  # no login
    if method == "post":
        resp = client.post(path, json={})
    else:
        resp = client.get(path)
    assert resp.status_code == 401
