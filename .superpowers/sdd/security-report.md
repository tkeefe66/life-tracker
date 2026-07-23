# Security Hardening — Implementation Report

**Branch:** `security-hardening` (started from the same commit as `main`)
**Spec:** `docs/superpowers/specs/2026-07-23-security-hardening-design.md`
**Plan:** `docs/superpowers/plans/2026-07-23-security-hardening.md`
**Commits:** 6, one per section, all with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

```
1e1083b fix(security): disable public docs, add security headers, POST reflection, bound inputs
574c205 feat(security): closed-set job statuses so exceptions can never leak credentials
7d31181 feat(security): server-side sessions with expiry, renewal, and logout
31a2863 feat(security): login lockout with logged failed attempts
fa08b15 feat(ops): weekly database backup job and cleardb production guard
8fa208c chore(deps): pin runtime dependencies, split dev tooling
```

## Final verification

- Backend: `venv/bin/pytest tests/ -v` → **217 passed** (baseline 175 + 42 new)
- Frontend: `npm test -- --run` → **47 passed** (baseline 44 + 3 new); `npm run build` → clean `tsc --noEmit && vite build`
- Live local server check (`uvicorn main:app`, real HTTP, not just TestClient):
  - `curl -I /api/health` → all five security headers present
  - `/docs`, `/redoc`, `/openapi.json` → all `404`
  - `/api/login` → sets cookie, `200`; `/api/logout` with that cookie → `200`
- Fresh venv built strictly from the newly-pinned `requirements.txt` + `requirements-dev.txt`, full 217-test suite green against it (proves the pinned set actually installs and works together, not just "was already installed").

## Section 1 — Cheap wins (`1e1083b`)

- `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` — the 18 previously-public endpoints (`/docs`, `/redoc`, `/openapi.json`) now 404.
- `@app.middleware("http")` sets `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Strict-Transport-Security: max-age=31536000; includeSubDomains` on every response.
- `GET /api/reflection` → `POST /api/reflection`; frontend (`Insights.tsx`) updated to `apiSend("POST", ...)`. A GET on the old path now falls through to the SPA static-file mount and 404s (verified by test — Starlette's routing prefers the catch-all `Mount` over returning a routing-level 405 once any full-path match exists).
- `PUT /api/targets` gains an explicit upper bound (`MAX_TARGET_VALUE = 100_000`) alongside the existing type/negative checks, so an oversized value 400s instead of reaching the DB driver as a 500.
- `LoginBody.password: str = Field(max_length=200)`.

**Deliberate deviation from the plan's literal wording:** the plan says targets should use "a pydantic model with a bounded integer." I kept the existing hand-rolled `dict` validation and just added the upper bound, rather than switching to a pydantic-constrained type. Reason: the existing tests (and the existing code) require `-1` and `True` to return `400`, which a pydantic `int` field would instead reject with `422` (FastAPI's default for validation errors). Preserving the tested `400` semantics while still closing the actual vulnerability (oversized-int → DB-level 500) seemed better than a literal reading that would silently change response codes for two already-covered cases. Verified behavior: `test_targets_update_rejects_oversized_value` (100001 → 400).

**TDD evidence:** `tests/test_api_auth.py::test_docs_endpoints_are_disabled`, `test_security_headers_present`; `tests/test_api_routes.py::test_reflection_get_is_rejected`, `test_targets_update_rejects_oversized_value`; `tests/test_api_auth.py::test_login_rejects_oversized_password`. All five confirmed red (endpoint/behavior didn't exist) before implementation, green after.

## Section 2 — The redaction boundary (`574c205`)

This is the core of the phase. `services/safe_status.py` (new) maps any exception to one of `"ok"` / `"error: auth"` / `"error: unreachable"` / `"error: rate limited"` / `"error: see logs"` — nothing else, ever. It inspects only:
- exception type (matched via the full MRO's class names, so it recognizes `google.auth.exceptions.RefreshError`, `googleapiclient.errors.HttpError`, requests/httpx-style errors, anthropic SDK errors, etc. **without importing any of those optional libraries** — dependency-light by design)
- well-known numeric status attributes (`.status_code`, `.response.status_code`, `.resp.status`, `.code`)

It **never** touches `.args` or `str(exc)`.

`jobs/scan_gmail.py`, `jobs/scan_calendar.py`, `jobs/weekly_push.py` all changed from `db.set_setting(..., f"error: {e}")` to `logger.exception(...)` (full detail, server-side, Railway logs only) + `db.set_setting(..., safe_status(e))`. `ai_metrics._call_json`'s failure log now truncates `raw` to 40 chars — it logs model output generated from personal content today, and would log transaction descriptions once bank data flows through the same helper.

**The critical property test** (`tests/test_safe_status.py::test_credential_bearing_url_never_survives_the_boundary`): six different exception shapes (`RuntimeError`, `ConnectionError`, `ValueError`, an HTTP-401-style error, a Google-HTTP-403-style error, a bare `Exception`) all constructed with the message `https://user:supersecret@bridge.example.com/path?token=abc` embedded. Asserts the returned value contains none of `user`, `supersecret`, `token`, `abc`, `http`, `bridge.example.com`, and is a member of the closed set. A second test (`test_safe_status_never_returns_str_of_the_exception`) explicitly asserts `str(exc) not in result` for a generic exception, so even an exception type this module has never seen before cannot fall back to leaking the message.

CLAUDE.md gained a "redaction boundary" bullet in Code Conventions documenting the rule for future jobs (including the eventual SimpleFIN integration): *prevent the credential-bearing string from being constructed; never scrub it afterwards.*

**TDD evidence:** 17 new tests in `tests/test_safe_status.py`, all written and confirmed red (`ModuleNotFoundError: No module named 'services.safe_status'`) before `services/safe_status.py` existed.

## Section 3 — Real sessions (`7d31181`)

Replaced the static `hmac(APP_PASSWORD, "on-track-session-v1")` cookie (never expired, computable offline from the password, no logout) with a `sessions` table (`token` PK — `secrets.token_urlsafe(32)`, `created_at`/`expires_at` as app-generated UTC ISO strings, not DB defaults, so expiry math never depends on the DB server's clock).

- `database.py`: `create_session`, `get_session`, `update_session_expiry`, `delete_session`, `delete_expired_sessions(now_iso)`.
- `config.py`: `SESSION_TTL_DAYS` (default 14).
- `app/auth.py`: rewritten. `require_auth` looks up the cookie's token, 401s on missing/unknown/expired (deleting the row the instant expiry is detected). **Sliding renewal**: once a session is past half its TTL, the next authenticated request extends `expires_at` and re-sets the cookie — bounds a stolen-but-unused cookie's life while never logging out an actively-used session.
- `app/api.py`: login creates a session and sets the cookie via the new helpers.
- `app/routes.py`: new `POST /api/logout` (on the protected router — requires a valid session to call) deletes exactly that session and clears the cookie.
- `main.py`: `db.delete_expired_sessions(...)` runs at startup.
- Frontend: `api.ts` gains `logout()`. `Settings.tsx` gets a "Sign out" row.

**Gap I found and closed, beyond the plan's literal text:** the plan says "confirm App.tsx's existing `UnauthorizedError` handling returns to login when a session expires mid-use (it already handles `UnauthorizedError`)." On inspection, that handling only existed in the *initial* `probe()` call in `App.tsx` — no other screen (Today, Scorecard, Insights, Settings) caught `UnauthorizedError`, so under the *old* 365-day static cookie this never mattered in practice, but with real, genuinely-expiring sessions a 401 mid-use on any of those screens would have left the UI stuck rather than returning to login. I added a small `onUnauthorized(handler)` registration hook in `api.ts` that `handle()` calls on every 401, and `App.tsx` registers it once to `setAuthed(false)`. This is a minimal, well-contained fix (not a redesign) that makes session revocation actually work end-to-end from any screen. Covered by `frontend/src/api.test.ts::onUnauthorized`.

**TDD evidence:** 9 new backend tests (`test_api_auth.py`: cookie-not-derivable, unknown-token, expired-session-401-and-swept, logout-invalidates-only-that-session, sliding-renewal extends / doesn't-renew-too-early, logout-clears-cookie; `test_database_v2.py`: session CRUD roundtrip, idempotent delete, update-expiry, delete-expired-only-removes-past-ones) plus 3 new vitest tests (`api.test.ts`: `logout()` POSTs and never throws, `onUnauthorized` fires on any 401).

## Section 4 — Login throttling (`31a2863`)

State lives in `app_settings` (`login_fail_count`, `login_locked_until`) — survives a redeploy, matches the plan's preference for durability over an in-memory counter. Single global counter (no per-IP tracking — correctly called out in the plan as unnecessary complexity for a single-user app). `_check_login_lockout()` runs **before** `verify_password`, so once locked, even the correct password 429s. Lockout starts at 60s on the 5th consecutive failure, doubles on each further failure past the threshold, capped at 15 minutes. A successful login clears both keys. Every failed attempt is `logger.warning()`'d with its running count.

**TDD evidence:** 5 new tests, all confirmed red before implementation (`AttributeError: module 'app.api' has no attribute '_parse'` etc.): 6th-attempt-429-even-with-correct-password, success-before-threshold-clears-counter, every-failure-logged, lockout-expires-and-access-restored (clock-controlled via a monkeypatchable `_utcnow()` seam, no real sleeping), lockout-window-doubles-on-repeated-failure-after-expiry.

## Section 5 — Backups + cleardb guard (`fa08b15`)

`jobs/backup_db.py` (new): weekly (Sunday @ `BACKUP_HOUR`, default 4am — wired into `main.py`'s scheduler). Skips with a logged warning when not on Postgres, or when any of `BACKUP_S3_BUCKET`/`_ENDPOINT`/`_ACCESS_KEY`/`_SECRET_KEY` (all new, optional, in `config.py`) is unset — records `"error: not configured"`. On a real run: `pg_dump --format=custom` → upload to the S3-compatible destination via `boto3` (lazy-imported, so an unconfigured deploy never needs it installed to boot) → prune to the last 8 dumps. Follows the same redaction-boundary pattern as section 2: `logger.exception` + `safe_status(e)`, verified with a test asserting a fake pg_dump failure embedding `postgres://user:hunter2@host` never survives into `backup_last_status`.

`/api/settings` and `Settings.tsx` gain `backup_last_run`/`backup_last_status`, shown as a Backups row beside Gmail/Calendar.

`scripts/cleardb.py`: now prints the resolved target (engine, host, database name) before prompting; on Postgres, requires typing the exact database host rather than a generic `CONFIRM`. Also added `"sessions"` to the wiped-tables list (a full DB wipe should invalidate all logged-in devices too).

**Necessary addition beyond the plan's literal file list:** `nixpacks.toml` gains the `postgresql` nix package. Without it, the runtime image has no `pg_dump` binary at all and the backup job would `safe_status()`-fail on every single run in production — the feature would ship non-functional. I judged this in-scope for section 5 (the job needs it to work at all) even though the plan's "Global Constraints" says not to touch deployment config beyond section 6. Flagging explicitly in case this reasoning should be reconsidered.

**TDD evidence:** 4 new tests in `tests/test_backup_db.py`, confirmed red (`ImportError: cannot import name 'backup_db'`) before the module existed: skip-on-sqlite, skip-when-unconfigured (`"error: not configured"`), upload-with-expected-key-and-prune (uploader/pruner/dumper all isolated behind mockable functions — **no real S3 client or pg_dump is ever exercised, and boto3 need not even be installed for tests to pass**, per the plan's explicit instruction not to test the actual upload), and safe-status-on-failure (a fake pg_dump error carrying a password never leaks into the stored status).

One test-design note: I couldn't simply monkeypatch `database.USE_POSTGRES` to simulate "this deploy uses Postgres," because that flag also gates every other DB call (`get_setting`/`set_setting` included) and would have broken the test's own status bookkeeping against the real SQLite test fixture. Added a `backup_db._using_postgres()` seam instead, monkeypatched in tests, leaving `database.USE_POSTGRES` (correctly `False`, SQLite) untouched.

## Section 6 — Dependency pinning (`8fa208c`)

`requirements-dev.txt` (new): `pytest==8.4.2`, `pytest-asyncio==1.2.0`, `ruff==0.15.22` — nixpacks' Python provider only auto-installs `requirements.txt`, so these were confirmed to never reach the production image already; the split makes that explicit and prevents future drift.

`requirements.txt`: all runtime deps pinned to exact versions (`pip freeze` from the working venv, including the new `boto3` from section 5). Verified by installing `requirements.txt` + `requirements-dev.txt` into a **brand-new venv from scratch** and running the full 217-test suite against it — green, proving the pinned set actually installs and works together (not just "happened to already be installed").

## Files changed (all paths relative to worktree root)

- `app/api.py`, `app/auth.py`, `app/routes.py`
- `config.py`, `main.py`, `database.py`
- `services/safe_status.py` (new)
- `jobs/scan_gmail.py`, `jobs/scan_calendar.py`, `jobs/weekly_push.py`, `jobs/backup_db.py` (new)
- `ai_metrics.py`
- `scripts/cleardb.py`
- `requirements.txt`, `requirements-dev.txt` (new), `nixpacks.toml`
- `frontend/src/api.ts`, `frontend/src/App.tsx`, `frontend/src/screens/Settings.tsx`, `frontend/src/screens/Insights.tsx`
- `frontend/src/api.test.ts` (new)
- `CLAUDE.md` (redaction-boundary rule, new env vars, `.env.example` note)
- Tests: `tests/test_api_auth.py`, `tests/test_api_routes.py`, `tests/test_database_v2.py`, `tests/test_safe_status.py` (new), `tests/test_backup_db.py` (new)

## Self-review (per the task's checklist)

- All six sections done; both suites and the build green — confirmed above with fresh runs, including a from-scratch pinned-venv run for section 6.
- `safe_status` property test genuinely proves credentials cannot pass through — verified across 6 exception shapes plus an explicit `str(exc) not in result` assertion for the unmapped case.
- Sessions are random (`secrets.token_urlsafe(32)`) and unrelated to `APP_PASSWORD` — verified by asserting two logins with the identical correct password produce different tokens, and neither equals the old HMAC-derived value.
- Logout invalidates exactly one session — verified with two independently-seeded sessions, logging one out, confirming the other still authenticates.
- Lockout triggers even with the correct password — verified directly (`test_lockout_triggers_after_five_failures_even_with_correct_password`), because the lockout check runs before password verification.
- The backup job no-ops cleanly when unconfigured — verified for both the SQLite case (skipped before any config check) and the Postgres-but-unconfigured case (`"error: not configured"`, no crash, no upload attempted).
- Nothing in the diff stores raw exception text where a user can read it — confirmed by grepping the full branch diff for `{e}`/`str(e)` patterns; every remaining hit is either a removed line or a comment explaining the pattern.

## Concerns / outstanding items

**Operator-only (explicitly out of my scope per the task):**
1. **Remove the public Postgres TCP proxy** in Railway — the app only ever connects via the private network, so this is safe to remove and is the single largest attack-surface reduction in the whole spec. Not attempted; requires Railway dashboard/CLI access I was told not to use.
2. **Rotate `APP_PASSWORD`** in Railway once this branch deploys, since the old password could compute the old static session token offline (moot after this deploy, but good hygiene regardless).

**My own flags, for your judgment:**
3. `.env.example` could not be read, edited, or even `ls`'d — blocked by a hardcoded sandbox guardrail on dotenv files. I documented every new env var (`SESSION_TTL_DAYS`, `BACKUP_S3_BUCKET`/`_ENDPOINT`/`_ACCESS_KEY`/`_SECRET_KEY`, `BACKUP_HOUR`) in `CLAUDE.md`'s environment table instead, with an explicit note to add them to `.env.example` by hand.
4. I deviated from the plan's literal "pydantic model" phrasing for `PUT /api/targets` (kept manual dict validation, added an upper bound) to preserve existing tested `400` response codes rather than switching to pydantic's `422`. See Section 1 above for the full reasoning.

---

## Review fix wave

A follow-up adversarial review of this branch surfaced 12 findings (2 high, 5
medium, 5 low). All are fixed. TDD was used for the four H/M findings that
involve genuine concurrency or state-machine logic (H1, H2, M1, M2) — a
failing test was written and confirmed red against the pre-fix code before
each fix landed.

### H1 — Login lockout counter was a non-atomic read-modify-write

`_record_login_failure()`'s `count = int(get_setting(...)) + 1; set_setting(...)`
raced under Starlette's anyio threadpool (sync `def` routes run there, up to
40 workers): concurrent failed logins could all read the same stale count and
all write the same next value, so the counter advanced roughly once per burst
instead of once per request — defeating the lockout by ~40x under real
concurrency.

**Fix:** the entire `login()` handler body (lockout check → password verify →
failure/success recording → session creation) now runs under one
process-wide `threading.Lock()` in `app/api.py`. This app is one Railway
service/one deploy (see CLAUDE.md) — a process-wide lock is correct here and
would not be sufficient if this ever ran as multiple replicas sharing one DB;
said explicitly in a new comment above the lock. Chose the lock over a
DB-level atomic `INSERT ... ON CONFLICT ... RETURNING` increment (the other
option the finding offered) because it also closes the TOCTOU between the
lockout *check* and the failure *record* — the finding explicitly required
the whole check-verify-record sequence to be race-free, not just the
increment — and because the deployed Python is 3.9 (confirmed via the venv),
old enough that depending on SQLite's `RETURNING` support (3.35+) felt like
an unnecessary second risk.

**TDD evidence:** `tests/test_api_auth.py::test_concurrent_failed_logins_increment_the_counter_atomically`
fires 40 concurrent failed-login requests via `ThreadPoolExecutor` (lockout
threshold monkeypatched high so the test isolates the atomicity property from
the separate lockout-cap behavior) and asserts the final counter equals
exactly 40. Confirmed red against the pre-fix code (counter landed below 40
due to lost updates); green and stable across 5 repeated runs after the fix.

### H2 — Backup job put the Postgres password in argv and could log it on failure

`jobs/backup_db.py` ran `subprocess.run(["pg_dump", DATABASE_URL, ...], check=True)`.
`DATABASE_URL` carries the password, so it was visible in `/proc/<pid>/cmdline`
and `ps` on every single run (not just failures), and `check=True`'s
`CalledProcessError.__str__()` embeds the full argv it was given, which
`logger.exception(...)` would then write to Railway logs on failure.

**Fix:** `_pg_dump_args()` builds `pg_dump --format=custom -h <host> -p <port>
-U <user> -d <dbname>` from `urlparse(DATABASE_URL)` — the password never
appears. `_pg_dump_env()` supplies it only via `PGPASSWORD` in the subprocess
`env=`. `check=True` is gone; `_dump_to_file` inspects `result.returncode`
itself and raises a new `BackupDumpError` with a message we fully control
(never a library-assembled string). stderr is still logged server-side in
full via `logger.error` for real debugging — it can carry a username on auth
failure but never the password, since the password is never in argv or
therefore echoed back by pg_dump.

**TDD evidence:** `test_pg_dump_invocation_never_puts_the_password_in_argv`
patches `subprocess.run`, calls `_dump_to_file` with a `DATABASE_URL`
embedding a distinctive password, and asserts it appears nowhere in the argv
list or as a substring of any argv element, while confirming it *does* appear
in `env["PGPASSWORD"]`, and that `check` is never passed as a kwarg.
`test_pg_dump_failure_without_check_true_is_raised_from_our_own_exception`
confirms a non-zero returncode still raises (so `run()`'s existing
`safe_status()` handling keeps working) without the password leaking into the
exception's `str()`. Both confirmed red (old code raised `TypeError` from a
mocked `CalledProcessError`-shaped stand-in / never even got that far) before
the fix.

### M1 — Sessions had no absolute lifetime cap

Sliding renewal in `app/auth.py` only ever advanced `expires_at`, never
`created_at`, so an actively-used or stolen-and-replayed cookie would renew
forever. Added `SESSION_MAX_DAYS` (new `config.py` var, default 60).
`require_auth` now rejects and deletes a session once `now - created_at >
SESSION_MAX_DAYS`, checked before the sliding-renewal logic so an ancient
session can never get itself renewed again on its way through. A new
`has_valid_session()` read-only check (used by M2 below) respects the same
cap.

**TDD evidence:** `test_session_older_than_max_days_is_rejected_even_if_not_yet_expired`
seeds a session with `created_at` past the cap but `expires_at` a year in the
future (simulating what unlimited renewal would produce) and confirms `401`
plus the row being swept. Confirmed red (`ImportError: cannot import name
'SESSION_MAX_DAYS'`) before the config var and check existed.

### M2 — An unauthenticated attacker could lock the owner out permanently

The failure counter only ever reset on a successful login — impossible while
locked — so an attacker sending one wrong guess per minute (slower than any
single lockout window, which caps at 15 minutes) could keep re-triggering a
fresh lock forever, since each failure past the threshold pushes
`locked_until` out again.

**Fix, part (a):** `app/auth.has_valid_session(request)` — a read-only
version of the session-validity check (no renewal, no deletion) — is
consulted in `login()` before `_check_login_lockout()`. A request that
already carries a still-valid session cookie skips the lockout check
entirely, so the owner's own already-authenticated devices are never blocked
by an attack aimed at the login endpoint.

**Fix, part (b):** a new `login_last_fail_at` timestamp (`app_settings`) is
recorded on every failure. `_record_login_failure()` now checks it first: if
the last failure was more than `LOGIN_FAIL_RESET_MINUTES` (30) ago, the
counter and any lock are cleared *before* this failure is recorded — so a
stray attempt after an abandoned attack counts as a fresh #1, not a
continuation of the old count that would otherwise instantly re-trigger a new
15-minute lock.

**Manual recovery** is now documented directly above the throttling constants
in `app/api.py`: clear the three `app_settings` rows (`login_fail_count`,
`login_locked_until`, `login_last_fail_at`) to force an immediate reset.

**TDD evidence:** `test_valid_session_cookie_bypasses_the_lockout` logs the
owner in first, then locks out a second, cookie-less client, and confirms the
owner's already-cookied client still gets `200` on `/api/login` while the
attacker gets `429`. `test_abandoned_attack_heals_after_30_minutes_of_inactivity`
simulates 7 rounds of an ongoing attack (each lockout window allowed to
naturally expire before the next failure, so the count keeps climbing), then
jumps the clock 31 minutes with no further attempts, and confirms the next
wrong-password attempt is counted as failure #1 (not #8, which would
otherwise instantly re-lock) — followed by an immediate successful
correct-password login. Both confirmed red before the fix (bypass returned
`429`; the healing test's count landed at 8, not 1).

### M3 — `nixpacks.toml` pulled unversioned `postgresql`

Pinned to `postgresql_17`. No in-repo evidence (railway.toml, docs) of an
older Postgres major in use, so went with the finding's suggested default. A
comment above the pin explains why: `pg_dump` refuses to dump from a server
newer than itself, so an nixpkgs default drifting below Railway's actual
Postgres version would silently break every backup going forward.

### M4 — `pydantic` imported directly but unpinned

Pinned `pydantic==2.13.4` (the version already resolved into the venv by
`fastapi`) in `requirements.txt`, next to the other exact pins.

### M5 — Backup integrity (partial, as scoped — no encryption)

Two additions to `jobs/backup_db.py`, both gated into `run()` before
`_prune_old_backups()` is ever reached:
(a) `_assert_dump_is_plausible()` raises `BackupTooSmallError` if the dump
file is under `MIN_DUMP_BYTES` (512 — a conservative floor comfortably below
even a near-empty custom-format dump's header overhead), so a `pg_dump` that
exits 0 with a truncated file is never uploaded.
(b) `_verify_uploaded(key)` lists the destination for the just-uploaded key
after `_upload()` and before `_prune_old_backups()`; if the key isn't found,
`BackupUnverifiedError` is raised and pruning never runs. Both paths fall
through to the existing `safe_status()` handling, so `backup_last_status`
still records `"error: see logs"` rather than crashing the scheduler.

**Test evidence:** `test_backup_refuses_to_upload_an_implausibly_small_dump`
and `test_backup_does_not_prune_unless_upload_is_confirmed_in_listing` each
assert `_upload`/`_prune_old_backups` (as applicable) were never called. The
existing `test_backup_uploads_with_expected_key_and_prunes` was updated to
write a plausibly-sized dump and mock `_verify_uploaded` to return `True`,
since it now sits on the path to `_prune_old_backups()`.

### L2 — `test_reflection_get_is_rejected` assumed `frontend/dist` exists

Changed the assertion to `status_code in (404, 405)` with a comment
explaining why both are correct depending on whether the SPA catch-all mount
exists — the actual property under test (no write, no Claude call) is
unchanged and still asserted.

### L3 — Literal "not configured" strings bypassed the `CLOSED_SET` invariant

`services/safe_status.py` gained two named constants — `NOT_CONFIGURED`
("error: not configured") and `GOOGLE_NOT_CONFIGURED` ("error: Google not
configured") — added to `CLOSED_SET`. `jobs/backup_db.py`, `jobs/scan_gmail.py`,
and `jobs/scan_calendar.py` now import and use these instead of duplicating
the literal at each call site. `tests/test_safe_status.py` gained
`test_not_configured_constants_are_closed_set_members` and
`test_job_modules_use_the_shared_constants_not_ad_hoc_literals`, so "every
status a job ever writes is a `CLOSED_SET` member" is enforced by a test, not
just true by convention. No existing test's literal string assertions needed
to change — the constants' values are identical to what was already there.

### L6 — Frontend couldn't distinguish a 429 lockout from a 401 wrong password

`frontend/src/api.ts`'s `login()` previously returned `resp.ok` for both,
so a lockout read as "wrong password" and the owner would keep retrying,
extending their own lockout. `login()` now throws a new `LockedOutError` on
`429`; `App.tsx`'s `LoginScreen` catches it specifically and shows "Too many
attempts — try again shortly." instead of "Wrong password." Three new tests
in `frontend/src/api.test.ts` cover the 200/401/429 cases.

### L7 — `scripts/cleardb.py`'s `TABLES` omitted `rides`

Added `rides`. `sessions` was already present (a prior pass had added it).
Also added `weekly_reflections` — another active v2 table missing from the
wipe list, same bug class as the one named in the finding — since it was a
one-line, directly-adjacent fix; flagging it here since it wasn't explicitly
named.

### L10 — CLAUDE.md's `.env.example` claim contradicted itself

Reworded the Environment Variables section to state `.env.example` may lag
the table (rather than claiming an exact match while also noting vars
missing from it), with an explicit list of vars that must be set manually.
Added `SESSION_TTL_DAYS` / `SESSION_MAX_DAYS` / `BACKUP_S3_*` to the Deploy
checklist. Also updated the redaction-boundary bullet in Code Conventions to
note the two pre-flight "not configured" constants are `CLOSED_SET` members
too, since that's now directly testable (L3) and the doc previously only
described the exception-path constants.

### Final verification

- Backend: `pytest tests/ -v` → **228 passed** (baseline 217 + 11 new: 1 for
  H1, 2 for M1, 2 for M2, 2 for H2, 2 for M5, 2 for L3).
- Frontend: `npm test -- --run` → **50 passed** (baseline 47 + 3 new, all in
  `api.test.ts`'s new `login` describe block); `npm run build` → clean
  `tsc --noEmit && vite build`.
- The H1 concurrency test was run 5 times in a row post-fix to check for
  flakiness from the threading — stable every time.

### Files changed this wave

- `app/api.py`, `app/auth.py`, `config.py`
- `jobs/backup_db.py`, `jobs/scan_gmail.py`, `jobs/scan_calendar.py`
- `services/safe_status.py`
- `nixpacks.toml`, `requirements.txt`, `scripts/cleardb.py`
- `frontend/src/api.ts`, `frontend/src/App.tsx`
- `CLAUDE.md`
- Tests: `tests/test_api_auth.py`, `tests/test_api_routes.py`,
  `tests/test_backup_db.py`, `tests/test_safe_status.py`,
  `frontend/src/api.test.ts`

### Concerns / outstanding items

- M2's fix bounds a single burst's overshoot past the lockout threshold to
  the size of that burst (the lockout *check* and the failure *record* are
  both inside the same process lock, so this is actually fully serialized in
  practice — no TOCTOU window remains within one process). If this app is
  ever scaled to multiple replicas sharing one Postgres instance, the
  process-wide lock stops being sufficient and the DB-level atomic-increment
  approach the H1 finding also offered would need to be revisited.
- `MIN_DUMP_BYTES = 512` is a heuristic floor, not derived from measuring a
  real dump of this app's actual schema (no configured backup destination
  exists yet to measure against). Worth sanity-checking against a real
  `pg_dump` output size once backups are actually configured in Railway.
- Did not touch the pre-existing `weekly_reflections` omission from
  `scripts/cleardb.py` scope boundary question mentioned above beyond adding
  it — flagging in case a full audit of that list against `database.py`'s
  active tables is wanted as a separate pass.
5. I added the `postgresql` nix package to `nixpacks.toml` (section 5) and `onUnauthorized()` global-401 handling to the frontend (section 3) — both beyond the plan's literal file lists, because without them the respective feature (backups, and session-expiry recovery from any screen) would not actually function correctly in production. Flagging both explicitly since the task said not to expand scope without surfacing it.
6. `jobs/backup_db.py`'s real S3 upload path (`boto3` client, `list_objects_v2`/`upload_file`/`delete_object`) is exercised nowhere by tests, per the plan's explicit instruction not to test the actual upload — it will only be proven correct against a real bucket once the operator configures `BACKUP_S3_*` and a scheduled run actually fires. Same for `pg_dump` itself, which isn't invoked in tests.

---

## Final Adversarial Re-Review Fixes

Applied the six findings from the adversarial re-review of the sections above.
One commit, `fix(security): discrete PG env vars, throttle authenticated
login failures, safe pg_dump pin`.

### 1 (M) — `urlparse().password` is never percent-decoded

`config.py` gained five optional discrete PG vars: `PGHOST`, `PGPORT`,
`PGUSER`, `PGPASSWORD`, `PGDATABASE` (plus `PGSSLMODE`, shared with fix 5
below) — Railway's Postgres plugin sets these directly, already decoded.
`jobs/backup_db.py`'s new `_connection_params()` prefers them, used only
when all five are present; otherwise it falls back to parsing
`DATABASE_URL`, now applying `urllib.parse.unquote()` to the username,
password, and dbname. Each connection detail stays a separate dict entry —
never concatenated into a single string — consistent with the existing
redaction-boundary rule that a credential-bearing string must never be
constructed, even transiently.

**Test evidence:** `test_discrete_pg_vars_are_preferred_and_used_verbatim`
(a `DATABASE_URL` that would parse to different, wrong values proves the
discrete vars actually win) and
`test_fallback_to_database_url_percent_decodes_username_password_and_dbname`
(username, password, and dbname each containing `+`/`@`/`/`/`%20`,
percent-encoded in the URL, come out decoded byte-for-byte).

### 2 (M) — Stolen session cookie enabled unlimited login brute-force

`app/api.py`'s `_check_login_lockout()` (raise-on-locked) became
`_is_locked_out()` (pure predicate). `login()` now takes a snapshot of the
lock state at the top of the critical section:

- If already locked **and** the request has no valid session cookie, it's
  refused with 429 before `verify_password` ever runs — this is the only
  place the lockout still short-circuits an attempt outright, and it has to
  stay that way: calling `verify_password` first here would let a locked-out
  attacker learn "that guess was right" the instant they guessed it, faster
  than waiting out the window like every wrong guess has to.
- Otherwise `verify_password` always runs. On success, the counters are
  cleared and the session issued unconditionally (M2a — the owner's
  already-authenticated device can always log in) — this is the *only*
  exemption a valid session buys.
- On failure, the attempt is always recorded via `_record_login_failure()`,
  regardless of session state, and then refused with 429 if the pre-attempt
  snapshot showed a lock already in effect, else 401. A session-holding
  attacker's wrong guesses now count toward the same shared counter as
  anyone else's and, once the threshold is crossed, get 429 like anyone
  else's — closing the "stolen cookie = unlimited brute force" hole.

All ten pre-existing lockout tests (including the off-by-one where the
attempt that trips the threshold itself still 401s, and the "correct
password during an active session-less lockout still 429s" case) pass
unmodified — the fix is additive, not a behavior change for a session-less
client. New test:
`test_wrong_password_from_a_valid_session_still_counts_toward_lockout`
proves a session-holding client's repeated wrong guesses now hit 429, while
its correct password still succeeds immediately despite the active lock
(M2a preserved).

### 3 (M) — `postgresql_17` pin risked breaking the whole deploy

`nixpacks.toml` reverted to the unversioned `postgresql` alias, which always
resolves. In exchange, `jobs/backup_db.py` now detects a server-newer-than-
pg_dump version mismatch from pg_dump's own stderr wording
(`_looks_like_pg_dump_version_mismatch`: both "server version" and "pg_dump
version" present) and records a distinct `PG_DUMP_VERSION_MISMATCH`
(`"error: pg_dump version mismatch"`) status — a new named constant added to
`services/safe_status.py`'s `CLOSED_SET`, alongside `NOT_CONFIGURED` and
`GOOGLE_NOT_CONFIGURED`. On detection, `_pg_dump_client_version()` also logs
the installed `pg_dump --version` output server-side (never stored in
`app_settings`) so the mismatch is diagnosable from Railway logs, not just
"pg_dump failed."

**Test evidence:** `test_version_mismatch_stderr_is_detected` /
`test_ordinary_failure_stderr_is_not_flagged_as_version_mismatch` (pure
detection logic against a fake stderr string) and
`test_run_records_distinct_status_on_pg_dump_version_mismatch` (`run()`
records the distinct status end-to-end).

### 4 (L) — `pg_dump` invoked without `-w`

Added to `_pg_dump_args()`. A missing/invalid password now fails fast with a
clear pg_dump error instead of the process hanging on an interactive prompt
nothing is present to answer. Covered by
`test_pg_dump_invoked_with_no_password_prompt_flag`.

### 5 (L) — Connection query parameters (`sslmode`) dropped

`_connection_params()` extracts `sslmode` from `DATABASE_URL`'s query string
in the fallback path (`urllib.parse.parse_qs`) and `_pg_dump_env()` passes
it through as `PGSSLMODE` — preserving e.g. `sslmode=require` instead of
silently downgrading to libpq's default `prefer`. Also available directly
via the discrete `PGSSLMODE` env var when using the discrete-var path.
Covered by `test_fallback_preserves_sslmode_from_database_url_query_string`.

### 6 (Low) — Committed report named the public Postgres proxy host:port

Removed `crossover.proxy.rlwy.net:16946` from this file's "Concerns" section
(item 1) — now describes it generically as "the public Postgres TCP proxy."
Grepped the rest of this file and found no other occurrence. Note:
`docs/superpowers/specs/2026-07-23-security-hardening-design.md` still names
the same host:port (line 23, in a table of prior-review findings) — out of
this fix's stated scope (`.superpowers/sdd/security-report.md` only), so left
untouched; flagging in case that spec doc should get the same treatment.

### Final verification

- Backend: `pytest tests/ -v` → **236 passed** (baseline 228 + 8 new: 2 for
  fix 1, 1 for fix 2, 3 for fix 3, 1 for fix 4, 1 for fix 5).
- Frontend: `npm test -- --run` → **50 passed** (unchanged — no frontend
  changes this wave); `npm run build` → clean `tsc --noEmit && vite build`.

### Concerns / outstanding items

- `docs/superpowers/specs/2026-07-23-security-hardening-design.md` still
  names the public proxy host:port (see item 6 above) — the redaction fix
  was scoped to the report file only.
- The discrete-PG-vars path and the `DATABASE_URL`-fallback path are both
  exercised only against fake `subprocess.run` calls, per the existing
  "never test the real pg_dump/S3 client" convention in this test file —
  final proof against a real Railway Postgres instance (with and without
  `PGHOST`/etc. set) still wants a real deploy.
- `PG_DUMP_VERSION_MISMATCH` detection is pattern-matched against pg_dump's
  English-language stderr wording, which could theoretically change across
  major pg_dump versions or locales; if it ever stops matching, the failure
  just falls back to the generic `"error: see logs"` rather than crashing
  anything, so this is a soft dependency, not a correctness risk.
