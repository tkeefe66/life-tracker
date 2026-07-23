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


# ── Sessions: expiry, revocation, logout ──────────────────────────────────────

def test_session_cookie_is_not_derivable_from_password(temp_db_path):
    """The old scheme was hmac(APP_PASSWORD, fixed-string) — the same password
    always produced the same cookie, and anyone who learned APP_PASSWORD could
    compute a valid session offline without ever calling /api/login. Two logins
    with the identical correct password must now yield two different, random
    tokens."""
    import hashlib
    import hmac as hmac_mod

    client_a = _client(temp_db_path)
    client_b = _client(temp_db_path)
    resp_a = client_a.post("/api/login", json={"password": "test-password"})
    resp_b = client_b.post("/api/login", json={"password": "test-password"})

    token_a = resp_a.cookies["ontrack_session"]
    token_b = resp_b.cookies["ontrack_session"]
    assert token_a != token_b  # random per-login, not a deterministic function of the password

    old_style_token = hmac_mod.new(b"test-password", b"on-track-session-v1", hashlib.sha256).hexdigest()
    assert token_a != old_style_token
    assert token_b != old_style_token


def test_unknown_session_token_401s(temp_db_path):
    client = _client(temp_db_path)
    client.cookies.set("ontrack_session", "not-a-real-token")
    assert client.get("/api/targets").status_code == 401


def test_expired_session_401s_and_is_swept(temp_db_path):
    import datetime

    import database as db
    client = _client(temp_db_path)

    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    created = past - datetime.timedelta(days=14)
    db.create_session("expired-token", created.isoformat(timespec="microseconds"), past.isoformat(timespec="microseconds"))
    client.cookies.set("ontrack_session", "expired-token")

    assert client.get("/api/targets").status_code == 401
    # require_auth deletes an expired session the moment it's presented.
    assert db.get_session("expired-token") is None


def test_logout_invalidates_only_that_session(temp_db_path):
    """Seed two independent sessions (two devices/browsers) and confirm logging
    one out never touches the other."""
    client_a = _client(temp_db_path)
    client_b = _client(temp_db_path)
    client_a.post("/api/login", json={"password": "test-password"})
    client_b.post("/api/login", json={"password": "test-password"})

    assert client_a.post("/api/logout").status_code == 200
    assert client_a.get("/api/targets").status_code == 401
    assert client_b.get("/api/targets").status_code == 200  # untouched


def test_sliding_renewal_extends_a_session_past_its_halfway_point(temp_db_path, monkeypatch):
    """A session more than halfway to expiry gets its expires_at pushed out on the
    next authenticated request — bounds a stolen cookie's life while keeping
    normal, regular use from ever hitting the original expiry."""
    import datetime

    import database as db
    from app import auth as auth_mod

    client = _client(temp_db_path)
    client.post("/api/login", json={"password": "test-password"})
    token = client.cookies["ontrack_session"]
    original_expiry = auth_mod._parse(db.get_session(token)["expires_at"])

    # Jump the clock to just past the session's halfway point.
    created_at = auth_mod._parse(db.get_session(token)["created_at"])
    past_halfway = created_at + (original_expiry - created_at) * 0.6
    monkeypatch.setattr(auth_mod, "_utcnow", lambda: past_halfway)

    assert client.get("/api/targets").status_code == 200
    renewed_expiry = auth_mod._parse(db.get_session(token)["expires_at"])
    assert renewed_expiry > original_expiry


def test_session_not_yet_halfway_is_not_renewed(temp_db_path, monkeypatch):
    import datetime

    import database as db
    from app import auth as auth_mod

    client = _client(temp_db_path)
    client.post("/api/login", json={"password": "test-password"})
    token = client.cookies["ontrack_session"]
    original_expiry = db.get_session(token)["expires_at"]

    created_at = auth_mod._parse(db.get_session(token)["created_at"])
    just_after_login = created_at + datetime.timedelta(seconds=1)
    monkeypatch.setattr(auth_mod, "_utcnow", lambda: just_after_login)

    assert client.get("/api/targets").status_code == 200
    assert db.get_session(token)["expires_at"] == original_expiry


def test_logout_clears_the_cookie(temp_db_path):
    client = _client(temp_db_path)
    client.post("/api/login", json={"password": "test-password"})
    resp = client.post("/api/logout")
    assert resp.status_code == 200
    # httpx/starlette expresses "clear this cookie" as an immediately-expired Set-Cookie.
    set_cookie = resp.headers.get("set-cookie", "")
    assert "ontrack_session=" in set_cookie


# ── Login throttling ──────────────────────────────────────────────────────────

def test_lockout_triggers_after_five_failures_even_with_correct_password(temp_db_path):
    client = _client(temp_db_path)
    for _ in range(5):
        resp = client.post("/api/login", json={"password": "wrong"})
        assert resp.status_code == 401
    # The 6th attempt is rejected outright — even with the CORRECT password —
    # because the lockout is checked before verify_password runs at all.
    resp = client.post("/api/login", json={"password": "test-password"})
    assert resp.status_code == 429


def test_successful_login_before_threshold_clears_the_counter(temp_db_path):
    client = _client(temp_db_path)
    for _ in range(4):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 200
    # Counter reset — a fresh run of 4 more wrong attempts must not trip the lockout.
    for _ in range(4):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 200


def test_every_failed_attempt_is_logged(temp_db_path, caplog):
    import logging
    client = _client(temp_db_path)
    with caplog.at_level(logging.WARNING):
        client.post("/api/login", json={"password": "wrong"})
    assert any("Failed login attempt" in r.message for r in caplog.records)


def test_lockout_expires_and_access_is_restored(temp_db_path, monkeypatch):
    """Clock-controlled — no real sleeping. The lockout window (60s the first
    time) must pass and then a correct password must succeed again."""
    import datetime

    from app import api as api_mod

    client = _client(temp_db_path)
    for _ in range(5):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 429

    future = api_mod._utcnow() + datetime.timedelta(seconds=61)
    monkeypatch.setattr(api_mod, "_utcnow", lambda: future)
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 200


def test_lockout_window_doubles_on_repeated_failure_after_expiry(temp_db_path, monkeypatch):
    """First lockout is 60s; if the attacker keeps failing after it expires, the
    next lockout window must be longer (doubling), not reset back to 60s."""
    import datetime

    from app import api as api_mod
    import database as db

    client = _client(temp_db_path)
    for _ in range(5):
        client.post("/api/login", json={"password": "wrong"})
    first_locked_until = api_mod._parse(db.get_setting(api_mod.LOGIN_LOCKED_UNTIL_KEY))

    # Jump past the first lockout window and fail once more.
    just_after_first = first_locked_until + datetime.timedelta(seconds=1)
    monkeypatch.setattr(api_mod, "_utcnow", lambda: just_after_first)
    resp = client.post("/api/login", json={"password": "wrong"})
    assert resp.status_code == 401  # lockout had expired, so this attempt is processed normally

    second_locked_until = api_mod._parse(db.get_setting(api_mod.LOGIN_LOCKED_UNTIL_KEY))
    second_window = second_locked_until - just_after_first
    assert second_window > datetime.timedelta(seconds=60)  # longer than the base window — it doubled, not reset
