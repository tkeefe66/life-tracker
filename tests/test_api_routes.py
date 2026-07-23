import datetime

from fastapi.testclient import TestClient


def _client(temp_db_path):
    import database as db
    from app.api import create_app
    db.seed_default_targets()
    # https base_url so the login cookie's Secure flag round-trips in TestClient
    client = TestClient(create_app(), base_url="https://testserver")
    client.post("/api/login", json={"password": "test-password"})
    return client


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


def test_settings_roundtrip(temp_db_path):
    client = _client(temp_db_path)
    s = client.get("/api/settings").json()
    assert s["telegram_push"] == "off"
    assert "google_configured" in s
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
    first = client.get("/api/reflection")
    assert first.status_code == 200
    assert first.json()["text"] == "Steady week."
    second = client.get("/api/reflection")
    assert second.json() == first.json()
    assert mock_anthropic.messages.create.call_count == 1


def test_reflection_204_on_generation_failure(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [type("T", (), {"text": "garbage"})()]
    client = _client(temp_db_path)
    assert client.get("/api/reflection").status_code == 204


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
    resp = client.get("/api/reflection")
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
