"""FastAPI app factory."""
import datetime
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import database as db
from app.auth import create_session, set_session_cookie, verify_password

logger = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}

# ── Login throttling ──────────────────────────────────────────────────────────
# State lives in app_settings (not memory) so it survives a redeploy. Single-user
# app: a global counter is correct — per-IP tracking would be trivially bypassed
# anyway and adds complexity for no real benefit here.
LOGIN_FAIL_COUNT_KEY = "login_fail_count"
LOGIN_LOCKED_UNTIL_KEY = "login_locked_until"
LOCKOUT_THRESHOLD = 5
LOCKOUT_BASE_SECONDS = 60
LOCKOUT_MAX_SECONDS = 15 * 60


def _utcnow() -> datetime.datetime:
    """Seam for tests — monkeypatch this, not the clock itself."""
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat(timespec="microseconds")


def _parse(value: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _check_login_lockout() -> None:
    locked_until = db.get_setting(LOGIN_LOCKED_UNTIL_KEY)
    if locked_until and _utcnow() < _parse(locked_until):
        raise HTTPException(status_code=429, detail="Too many failed attempts — try again later")


def _record_login_failure() -> None:
    count = int(db.get_setting(LOGIN_FAIL_COUNT_KEY, "0") or "0") + 1
    db.set_setting(LOGIN_FAIL_COUNT_KEY, str(count))
    logger.warning("Failed login attempt (count=%d)", count)
    if count >= LOCKOUT_THRESHOLD:
        extra = count - LOCKOUT_THRESHOLD
        seconds = min(LOCKOUT_BASE_SECONDS * (2 ** extra), LOCKOUT_MAX_SECONDS)
        db.set_setting(LOGIN_LOCKED_UNTIL_KEY, _iso(_utcnow() + datetime.timedelta(seconds=seconds)))


def _record_login_success() -> None:
    db.set_setting(LOGIN_FAIL_COUNT_KEY, "0")
    db.set_setting(LOGIN_LOCKED_UNTIL_KEY, "")


class LoginBody(BaseModel):
    password: str = Field(max_length=200)


def create_app(lifespan=None) -> FastAPI:
    app = FastAPI(title="On Track", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/login")
    def login(body: LoginBody, response: Response):
        _check_login_lockout()
        if not verify_password(body.password):
            _record_login_failure()
            raise HTTPException(status_code=401, detail="Wrong password")
        _record_login_success()
        token = create_session()
        set_session_cookie(response, token)
        return {"ok": True}

    from app.routes import router  # imported late so routes can import database freely
    app.include_router(router, prefix="/api")

    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="spa")
    return app
