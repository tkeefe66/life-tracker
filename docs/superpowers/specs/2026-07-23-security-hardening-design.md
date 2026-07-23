# Security Hardening — Prerequisite to Bank Data

**Date:** 2026-07-23
**Status:** Approved

## Problem

The app is about to ingest full bank transaction data via SimpleFIN. Four
independent audits (auth/session, secrets/exposure, web/API surface,
infrastructure/dependencies) found the application *code* sound — no SQL
injection across all of `database.py`, no SSRF, no path traversal, no XSS
sinks, uniform router-level auth — but the surrounding configuration and
session model inadequate for financial data. Several findings were verified
against the live deployment, not merely read from source.

This spec covers only what must change before any SimpleFIN code is written.
It is a prerequisite phase, not a general security rewrite.

## Verified findings driving this work

| # | Finding | How verified |
|---|---|---|
| 1 | Postgres has a public TCP proxy (`crossover.proxy.rlwy.net:16946`) the app never uses — it connects via `postgres.railway.internal` | `railway variables` on both services |
| 2 | No brute-force protection on `/api/login` | 8 wrong passwords in <1s, all 401 at ~70ms, no throttling |
| 3 | Session token is static (`HMAC(APP_PASSWORD, "on-track-session-v1")`), never expires, cannot be revoked; no logout endpoint exists | `app/auth.py:12-13`, `app/api.py:30`, grep for logout |
| 4 | Exception text is stored and rendered: `except Exception as e: db.set_setting(..., f"error: {e}")` → `/api/settings` → Settings screen | `jobs/*.py`, `app/routes.py`, `Settings.tsx`; a Gmail URL already leaked this way |
| 5 | No database backups anywhere in repo or platform config | repo scan; `scripts/cleardb.py` has no environment guard |
| 6 | `/docs`, `/redoc`, `/openapi.json` publicly return 200, exposing all 18 endpoints | live curl |
| 7 | No security headers at all | live `curl -I` |
| 8 | `GET /api/reflection` writes to the DB and can trigger a paid AI call | `app/routes.py:96-103` |
| 9 | Backend dependencies unpinned, no lockfile | `requirements.txt` |

## Design

### 1. Remove the public Postgres proxy *(operational, no code)*

Delete the TCP proxy on the Postgres service in Railway. The app uses the
private network, so nothing breaks. Verify afterwards that the app still
starts and `/api/health` returns 200. **Do this first** — it is the single
largest reduction in attack surface and requires no deploy.

### 2. Real sessions with expiry, revocation, and logout

Replace the static token with server-side sessions:

- New `sessions` table: `id` (random 32-byte urlsafe token, primary key),
  `created_at`, `expires_at`. No user column — single-user app.
- On login: generate a token via `secrets.token_urlsafe(32)`, insert with
  `expires_at = now + SESSION_TTL_DAYS` (new env var, default 14), set it as
  the cookie value with a matching `max_age`.
- `require_auth`: look the token up, reject if absent or expired. Constant-time
  comparison is no longer needed for lookup (the token is random and
  high-entropy), but the query must not leak timing beyond a normal index hit.
- **New `POST /api/logout`**: delete the session row and clear the cookie.
- Sliding renewal: on a successful authenticated request, if the session is
  more than halfway to expiry, extend `expires_at`. Keeps normal use from
  logging the user out while bounding a stolen cookie's life.
- Startup housekeeping: delete expired rows (cheap, single-user).
- `APP_PASSWORD` remains the login credential but is no longer the session
  secret — a leaked password no longer lets an attacker *compute* a valid
  cookie offline.

Frontend: `api.ts` gains a `logout()` call; `App.tsx` gets a logout control in
Settings and treats a 401 as "session ended" by returning to the login screen
(it already handles `UnauthorizedError`).

### 3. Login throttling

Track failed attempts server-side (in the `app_settings` table or a small
in-memory counter — single instance, so in-memory is acceptable and simpler,
but it resets on deploy; prefer the DB for durability):

- Count consecutive failures with a timestamp.
- After 5 consecutive failures, reject further attempts for a lockout window
  (start at 60 seconds, doubling to a 15-minute cap) regardless of password
  correctness, returning 429.
- A successful login clears the counter.
- **Log every failed attempt** (`logger.warning`) — today nothing is logged, so
  an ongoing attack is invisible.
- Because the app is single-user, a global counter is sufficient; per-IP
  tracking is unnecessary complexity and is trivially bypassed anyway.

### 4. The redaction boundary *(the pattern SimpleFIN will be built on)*

Establish it now, on the existing jobs, so the bank service inherits it:

- Ingestion jobs must never store `str(e)` in `app_settings`. They log the full
  exception server-side (`logger.exception` — Railway logs are not user-facing)
  and store a **fixed, enumerable status string**: `"ok"`, `"error: auth"`,
  `"error: unreachable"`, `"error: rate limited"`, `"error: see logs"`.
- A small helper — `services/safe_status.py` with
  `safe_status(exc) -> str` — maps common exception types to that closed set.
  Jobs call it; nothing else constructs status strings.
- `ai_metrics._call_json`'s failure log truncates `raw` to 40 characters. It
  currently logs full model output generated from personal content, and would
  log transaction descriptions once bank data flows through the same helper.
- Rule to carry forward (documented in CLAUDE.md): **prevent the
  credential-bearing string from being constructed; never scrub it afterwards.**

### 5. Backups

A weekly `pg_dump` job on the existing APScheduler, writing to an
**off-Railway** destination. Store the destination credentials as env vars;
skip silently (with a logged warning) when unset so local dev and any
un-configured deploy are unaffected. Retention: keep the last 8 weekly dumps.
Record `backup_last_run` / `backup_last_status` in `app_settings` using the
same closed status set, and surface it in Settings alongside the Gmail and
Calendar rows.

`scripts/cleardb.py` gains a guard: print the resolved target (engine, host,
database name) before prompting, and when `USE_POSTGRES` is true require the
operator to type the database host exactly, not just `CONFIRM`.

### 6. Cheap wins

- `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`.
- A headers middleware setting `X-Frame-Options: DENY`,
  `Content-Security-Policy: frame-ancestors 'none'`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, and
  `Strict-Transport-Security: max-age=31536000; includeSubDomains`.
- `GET /api/reflection` → `POST /api/reflection` (frontend updated together),
  so a top-level cross-site navigation cannot trigger a write or a paid call.
- `PUT /api/targets` takes a pydantic model with a bounded integer rather than
  a raw `dict`, so an oversized value 400s instead of 500s.
- `LoginBody.password` gains `max_length=200`, bounding the unauthenticated
  request body.
- `requirements.txt` split into pinned runtime deps (generated by
  `pip freeze`, committed as `requirements.txt`) and `requirements-dev.txt`
  for `pytest`/`ruff`, which are currently shipped into the production image.

## Explicitly out of scope

- **Full database encryption.** The app must hold the key to function, so it
  does not protect against app compromise. Targeted encryption of the future
  transaction columns, with a separate key, is deferred to the SimpleFIN spec
  where it belongs.
- **2FA.** A genuine improvement, but it must not delay the five items above.
  Revisit after bank data lands.
- The five `npm audit` findings — dev tooling only; `npm audit --production`
  reports zero.
- Per-IP rate limiting, a WAF, CORS configuration (correctly absent today).

## Testing

- pytest: login lockout after N failures and reset on success; session expiry
  rejected; logout invalidates a session; a second device's session is
  unaffected by another's logout; `safe_status` maps exception types to the
  closed set and **never** returns text containing a URL or credentials
  (property-style test with a credential-bearing URL in the exception);
  `/api/reflection` rejects GET; docs endpoints return 404; security headers
  present on a response; oversized target value returns 400.
- vitest: `logout()` clears client state.
- Manual: confirm `/docs` 404s and Postgres public proxy is gone after the
  Railway change.
