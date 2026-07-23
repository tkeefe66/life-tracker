# Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development. Implement sections in order; one commit each. This is a prerequisite phase — no SimpleFIN or bank code belongs in this branch.

**Goal:** Make the app safe to hold bank transaction data: real revocable sessions, login throttling, a redaction boundary that prevents credential leakage, backups, and the cheap deployment-surface wins.

**Spec:** `docs/superpowers/specs/2026-07-23-security-hardening-design.md` — read it first, including the verified-findings table. It is the authority.

## Global Constraints

- `database.py` is the only place with SQL; `config.py` the only env reader; `ai_metrics.py` the only Claude caller (`MODEL` unchanged).
- **Never store `str(exception)` where a user can see it.** Ingestion jobs log full detail server-side and store only a value from the closed status set. This is the whole point of the phase — do not weaken it for debuggability.
- New DB table via `CREATE TABLE IF NOT EXISTS` in `_init_v2_tables()`; new columns need the Postgres/SQLite migration pattern already established there.
- No hardcoded calendar dates in tests — derive from `_local_today()` or freeze explicitly.
- Backend `pytest tests/ -v` (baseline 175); frontend `cd frontend && npm test -- --run && npm run build` (baseline 44). No commits with failing checks.
- Do not touch the deployment config beyond what section 6 specifies. **Do not attempt Railway CLI/dashboard changes** — the operator handles the Postgres proxy removal.

---

### 1. Cheap wins first (fast, independently valuable)

**Files:** `app/api.py`, `app/routes.py`, `frontend/src/screens/Insights.tsx`, `tests/test_api_auth.py`, `tests/test_api_routes.py`

- [ ] Tests first: `/docs`, `/redoc`, `/openapi.json` all 404; a response carries `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Strict-Transport-Security`; `GET /api/reflection` 405 while `POST` works; `PUT /api/targets` with an oversized value returns 400 not 500; a login body over 200 chars is rejected (422).
- [ ] `create_app`: `FastAPI(title="On Track", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)`.
- [ ] Add an `@app.middleware("http")` that sets the five headers on every response.
- [ ] `LoginBody.password: str = Field(max_length=200)`.
- [ ] `@router.get("/reflection")` → `@router.post("/reflection")`. Update the frontend caller (it lives in `Insights.tsx`'s Behavior view — use `apiSend("POST", "/reflection")`; note `apiGet` currently handles the 204 path, so make sure `apiSend` handles 204 → null the same way, or reuse the existing `handle()` which already does).
- [ ] `put_targets` takes a pydantic model with values bounded `ge=0, le=100000` instead of a raw `dict`.
- [ ] Commit: `fix(security): disable public docs, add security headers, POST reflection, bound inputs`

### 2. The redaction boundary

**Files:** `services/safe_status.py` (new), `jobs/scan_gmail.py`, `jobs/scan_calendar.py`, `jobs/weekly_push.py`, `ai_metrics.py`, `tests/test_safe_status.py` (new), existing job tests

- [ ] Tests first for `safe_status(exc) -> str`:
  - maps auth-ish failures (HTTP 401/403, `google.auth.exceptions.RefreshError`) → `"error: auth"`
  - connection/timeout errors → `"error: unreachable"`
  - HTTP 429 → `"error: rate limited"`
  - anything else → `"error: see logs"`
  - **the critical property test:** given an exception whose message contains `https://user:supersecret@bridge.example.com/path?token=abc`, the returned string contains none of `user`, `supersecret`, `token`, `abc`, `http`. Assert on the returned value, not on a regex of it.
- [ ] Implement `services/safe_status.py`. Keep it dependency-light: inspect exception type and any `status_code`/`resp.status` attribute; never interpolate the message.
- [ ] Rewrite the three jobs' `except` blocks: `logger.exception(...)` for full server-side detail, then `db.set_setting(f"{name}_last_status", safe_status(e))`. Update existing job tests that assert on `"error: ..."` text — they should now assert the closed-set value.
- [ ] `ai_metrics._call_json`: truncate `raw` to 40 chars in the failure log.
- [ ] Commit: `feat(security): closed-set job statuses so exceptions can never leak credentials`

### 3. Real sessions

**Files:** `database.py`, `app/auth.py`, `app/api.py`, `config.py`, `.env.example`, `frontend/src/api.ts`, `frontend/src/App.tsx`, `frontend/src/screens/Settings.tsx`, tests

- [ ] Tests first: login sets a cookie whose value is NOT derivable from `APP_PASSWORD`; a request with an expired session 401s; logout invalidates that session only (seed two sessions, log one out, assert the other still authenticates); an unknown token 401s; expired rows are cleaned on init.
- [ ] `database.py`: `sessions` table (`token` TEXT PRIMARY KEY, `created_at`, `expires_at`) plus `create_session(token, expires_at)`, `get_session(token)`, `delete_session(token)`, `delete_expired_sessions()`.
- [ ] `config.py`: `SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "14"))`; document in `.env.example` and CLAUDE.md's env table.
- [ ] `app/auth.py`: `require_auth` looks up the cookie's token, 401s when missing or `expires_at` has passed. Add sliding renewal — when a valid session is past half its TTL, extend `expires_at`. Delete `session_token()`; nothing may derive a session from the password any more.
- [ ] `app/api.py`: login generates `secrets.token_urlsafe(32)`, stores it, sets the cookie with `max_age = SESSION_TTL_DAYS * 86400` (keep `httponly`, `secure`, `samesite="lax"`). Add `POST /api/logout` — delete the row, clear the cookie, return `{"ok": True}`. It must be reachable with a valid session (put it on the protected router, or check auth inline).
- [ ] `main.py` lifespan: call `db.delete_expired_sessions()` at startup.
- [ ] Frontend: `api.ts` gains `logout()`; `Settings.tsx` gets a "Sign out" row that calls it and then reloads to the login screen; confirm `App.tsx`'s existing `UnauthorizedError` handling returns to login when a session expires mid-use.
- [ ] Commit: `feat(security): server-side sessions with expiry, renewal, and logout`

### 4. Login throttling

**Files:** `app/api.py`, `database.py`, tests

- [ ] Tests first: 5 consecutive failures then a 6th returns 429 even with the CORRECT password; a successful login before the threshold clears the counter; the lockout expires and access is restored (control the clock via a seam — inject a `now` or monkeypatch a small helper rather than sleeping).
- [ ] Store counter state in `app_settings` (`login_fail_count`, `login_locked_until`) so it survives redeploys. Single-user app: a global counter is correct; do not build per-IP tracking.
- [ ] Lockout: after 5 consecutive failures, reject with 429 for a window starting at 60s and doubling per subsequent failure to a 15-minute cap. Success resets both keys.
- [ ] `logger.warning` on every failed attempt, including the current count.
- [ ] Commit: `feat(security): login lockout with logged failed attempts`

### 5. Backups + cleardb guard

**Files:** `jobs/backup_db.py` (new), `main.py`, `config.py`, `.env.example`, `scripts/cleardb.py`, `app/routes.py`, `frontend/src/screens/Settings.tsx`, tests

- [ ] `jobs/backup_db.py`: weekly job that runs `pg_dump` against `DATABASE_URL` and uploads to an off-Railway destination configured by env (`BACKUP_S3_BUCKET`, `BACKUP_S3_ENDPOINT`, `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, all optional). **When any are unset, log a warning and return** — local dev and unconfigured deploys must not fail. Skip entirely when not `USE_POSTGRES`. Retain the last 8 dumps. Record `backup_last_run` / `backup_last_status` via `safe_status`.
- [ ] Wire into the `lifespan()` scheduler in `main.py` (weekly, e.g. Sunday at `BACKUP_HOUR`, default 4).
- [ ] `/api/settings` returns the two backup keys; Settings shows a Backups row beside Gmail and Calendar.
- [ ] Tests: unconfigured → no-op with `"error: not configured"`-style status and no crash; SQLite → skipped. Do not test the actual upload; isolate the uploader behind a small function and assert it is called with the expected key.
- [ ] `scripts/cleardb.py`: print the resolved target (engine, host, database name) BEFORE prompting; when `USE_POSTGRES`, require the operator to type the database host exactly rather than `CONFIRM`.
- [ ] Commit: `feat(ops): weekly database backup job and cleardb production guard`

### 6. Dependency pinning

**Files:** `requirements.txt`, `requirements-dev.txt` (new), `nixpacks.toml`

- [ ] Split dev tooling (`pytest`, `pytest-asyncio`, `ruff`) into `requirements-dev.txt` — they currently ship into the production image.
- [ ] Pin runtime deps to exact versions. Generate from the working venv (`pip freeze`), but keep the file readable: direct dependencies with `==`, and do not hand-edit versions you have not verified install together. Run the full suite after pinning to prove the pinned set works.
- [ ] Confirm the nixpacks build still installs from `requirements.txt` only.
- [ ] Commit: `chore(deps): pin runtime dependencies, split dev tooling`

### Final verification

- Both suites and the build green.
- `curl -I` locally: the five headers present. `/docs` 404.
- Re-read the spec's findings table and confirm every code-side item is addressed (items 1 and 5's Railway-side actions are the operator's, not yours — note them in your report as outstanding).
