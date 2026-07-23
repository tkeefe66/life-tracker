"""FastAPI app factory."""
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import COOKIE_NAME, session_token, verify_password

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


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
        if not verify_password(body.password):
            raise HTTPException(status_code=401, detail="Wrong password")
        response.set_cookie(
            COOKIE_NAME, session_token(),
            httponly=True, samesite="lax", secure=True, max_age=365 * 24 * 3600,
        )
        return {"ok": True}

    from app.routes import router  # imported late so routes can import database freely
    app.include_router(router, prefix="/api")

    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="spa")
    return app
