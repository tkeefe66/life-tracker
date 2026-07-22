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


def test_checkin_validation(temp_db_path):
    client = _client(temp_db_path)
    assert client.post("/api/checkins", json={"type": "yoga"}).status_code == 422
    assert client.post("/api/checkins", json={"type": "alcohol"}).status_code == 400  # level required
    assert client.post("/api/checkins", json={"type": "alcohol", "level": 5}).status_code == 422


def test_scorecard_and_history_endpoints(temp_db_path):
    client = _client(temp_db_path)
    card = client.get("/api/scorecard").json()
    assert set(card["metrics"].keys()) == {"delivery", "gym", "social", "alcohol"}
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
