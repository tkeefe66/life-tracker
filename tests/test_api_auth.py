from fastapi.testclient import TestClient


def _client(temp_db_path):
    from app.api import create_app
    # https base_url so the login cookie's Secure flag round-trips in TestClient
    return TestClient(create_app(), base_url="https://testserver")


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


def test_login_rejects_oversized_password(temp_db_path):
    client = _client(temp_db_path)
    resp = client.post("/api/login", json={"password": "x" * 201})
    assert resp.status_code == 422


def test_docs_endpoints_are_disabled(temp_db_path):
    client = _client(temp_db_path)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_security_headers_present(temp_db_path):
    client = _client(temp_db_path)
    resp = client.get("/api/health")
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "same-origin"
    assert "max-age=31536000" in resp.headers["strict-transport-security"]
    assert "includesubdomains" in resp.headers["strict-transport-security"].lower()
