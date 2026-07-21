"""Single-user session auth: password → HMAC session cookie."""
import hashlib
import hmac

from fastapi import HTTPException, Request

from config import APP_PASSWORD

COOKIE_NAME = "ontrack_session"


def session_token() -> str:
    return hmac.new(APP_PASSWORD.encode(), b"on-track-session-v1", hashlib.sha256).hexdigest()


def verify_password(password: str) -> bool:
    return hmac.compare_digest(password, APP_PASSWORD)


def require_auth(request: Request) -> None:
    token = request.cookies.get(COOKIE_NAME, "")
    if not hmac.compare_digest(token, session_token()):
        raise HTTPException(status_code=401, detail="Not authenticated")
