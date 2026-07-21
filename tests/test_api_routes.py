from fastapi.testclient import TestClient


def _client(temp_db_path):
    import database as db
    from app.api import create_app
    db.seed_default_targets()
    client = TestClient(create_app())
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


def test_settings_roundtrip(temp_db_path):
    client = _client(temp_db_path)
    s = client.get("/api/settings").json()
    assert s["telegram_push"] == "off"
    assert "google_configured" in s
    assert client.put("/api/settings", json={"telegram_push": "on"}).status_code == 200
    assert client.get("/api/settings").json()["telegram_push"] == "on"
