from fastapi.testclient import TestClient


def _client(temp_db_path):
    from app.api import create_app
    return TestClient(create_app())


def test_health_is_public(temp_db_path):
    client = _client(temp_db_path)
    assert client.get("/api/health").status_code == 200


def test_login_wrong_password(temp_db_path):
    client = _client(temp_db_path)
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401


def test_login_sets_cookie_and_grants_access(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    db.seed_default_targets()

    assert client.get("/api/targets").status_code == 401  # protected before login
    resp = client.post("/api/login", json={"password": "test-password"})
    assert resp.status_code == 200
    assert "ontrack_session" in resp.cookies
    assert client.get("/api/targets").status_code == 200  # TestClient persists cookies
