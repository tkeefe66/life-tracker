# Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every actionable finding from the 2026-08-03 security audit (`SECURITY-AUDIT.md` in the repo root, which is this plan's spec) without changing any product behavior.

**Architecture:** Thirteen independent tasks. Each one is a small, self-contained change with its own test and its own commit. Nothing here refactors an existing subsystem — the audit found the fundamentals sound, so every task either adds a guard, adds a notification, or tightens a value. Tasks are ordered by real exploitability, not by size.

**Tech Stack:** Python 3 / FastAPI / Pydantic v2 / pytest (SQLite for tests, PostgreSQL in production) · React + TypeScript + Vite / vitest · Railway deploy.

---

## START HERE — orientation for a cold-start agent

You have no prior context. Read this whole section before touching any file.

### What this app is

"On Track" is a **single-user** personal life-tracking web app. One person (the repo owner) logs in with a password and sees their own delivery orders, Uber/Lyft rides, gym and alcohol check-ins, calendar events, and bank transactions. It is deployed as one Railway service at `https://life-tracker.tomkeefe.ai` with a Railway Postgres plugin. It ingests data passively from Gmail, Google Calendar, and SimpleFIN (banking), and calls the Anthropic API for a few classification tasks.

Because it is single-user, several things that would be bugs elsewhere are correct here: there is no `user_id` column anywhere, no per-row ownership check, and a global (not per-IP) login lockout counter. **Do not "fix" any of those.** Multi-user support is explicitly out of scope for this plan — see "Out of scope" at the bottom.

### Read these first

1. `CLAUDE.md` in the repo root — the project guide. Non-optional.
2. `SECURITY-AUDIT.md` in the repo root — this plan's spec, with the full reasoning behind each task.

### Verify your environment before starting

**Read this even if you think you know how to set up a Python project.** The
environment in this repo drifted badly enough on 2026-08-03 that local test runs
were not exercising what production installs — the same class of problem the
audit caught in the dependency tree. Do not assume; verify.

Production runs **Python 3.11** (`nixpacks.toml` installs `python311`;
`.python-version` says `3.11`). The `venv/` checked out here was Python 3.9 at
one point, and `pytest` on this machine resolves to Homebrew's system Python,
not the venv. **Always invoke the venv's binaries by path** — `venv/bin/pytest`,
never a bare `pytest`, because `source venv/bin/activate` has silently failed
here before.

Rebuild and verify:

```bash
cd "/Users/tomkeefe/Code Apps/life-tracker"
/opt/homebrew/opt/python@3.11/bin/python3.11 -m venv --clear venv
venv/bin/pip install -r requirements.txt
venv/bin/pip install pytest pytest-asyncio   # NOT in requirements.txt — see below
venv/bin/python --version                    # must print 3.11.x
venv/bin/pytest tests/ -q
```

```bash
cd frontend && npm install && npm test -- --run && npm run build
```

**Test dependencies are not tracked.** `requirements.txt` holds runtime deps
only; there is no `requirements-dev.txt`. `pytest` and `pytest-asyncio` must be
installed by hand — `pytest.ini` sets `asyncio_mode = auto`, so `pytest-asyncio`
is required or every async test errors on collection.

Expected after Task 0: pytest all green (606 tests); vitest all green (111
tests); `npm run build` completes (it runs `tsc --noEmit && vite build`).

**Before Task 0 the suite has exactly one known failure**
(`test_breakdown_rows_returns_vendor_rows_newest_first`) — that is what Task 0
fixes. Any *other* failure means something is wrong with your environment, not
with the plan. Stop and report it rather than working around it.

### How to tell where you are (resuming mid-plan)

This plan may be picked up after a partial run. Determine state this way, in order:

1. `git log --oneline -20` — each task below commits with a message starting `sec(N):`. The highest `N` present is the last completed task.
2. Check the `- [ ]` / `- [x]` checkboxes in this file.
3. If those two disagree, trust git and re-tick the boxes.
4. Re-run the full test suite before continuing, regardless.

Tasks are independent. If one is blocked, skip it and do the next — but say so in your report rather than silently dropping it.

---

## Global Constraints

These are project rules. Violating one is a plan failure even if the tests pass.

- **`database.py` is the only file containing SQL.** No DB calls from `app/`, `jobs/`, or `services/` — those call functions in `database.py`.
- **`config.py` is the only file that reads `os.environ`.** (One-off scripts in `scripts/` are the documented exception.) Adding an env var means adding it to `config.py`, to `.env.example`, and to the table in `CLAUDE.md` — all three, in the same commit.
- **`ai_metrics.py` is the only file that calls Claude.** No task in this plan should add a Claude call.
- **The redaction boundary is absolute.** Ingestion jobs and anything writing to `app_settings` must never store `str(exception)`. Use `logger.exception(...)` for server-side detail, then `services.safe_status.safe_status(e)` for the stored value, which maps to a closed set of strings. Rationale: the SimpleFIN access URL *is* a credential and HTTP libraries put URLs into exception messages. This bit the project for real on 2026-07-23.
- **Never lower the `httpx` / `httpcore` logger levels** pinned to `WARNING` in `main.py`. Same reason.
- **New DB column ⇒ new migration.** The pattern lives in `database._init_v2_tables()`: Postgres uses `ALTER TABLE … ADD COLUMN IF NOT EXISTS`, SQLite uses a `PRAGMA table_info` guard before `ALTER TABLE`. A brand-new *table* needs no migration — `CREATE TABLE IF NOT EXISTS` is enough. **No task in this plan adds a column.**
- **Money formatting, chart colors, and UI tokens are out of scope.** Do not touch `frontend/src/styles.css`.
- **Tests only exercise the SQLite path.** Postgres DDL is verified by deploying, not by tests.
- **Python 3.11 in production** (`nixpacks.toml` → `python311`, `.python-version` → `3.11`). The codebase still uses `Optional[int]` rather than `int | None` throughout — match the surrounding style rather than modernizing it, since that is a repo-wide convention and not a version constraint.
- **Dependency versions set by this plan (Task 2):** `fastapi==0.136.3`, `starlette==1.3.1`, `cryptography>=42.0.0`.
- **Pin direct dependencies with `==`.** `requirements.txt` uses exact pins throughout, and `nixpacks.toml` runs a fresh `pip install -r requirements.txt` on every deploy with no lockfile committed — so a `>=` floor lets an unverified release reach production between deploys. If you ever have a genuine reason to use a floor, the comment must say why.
- **Always run Python tools as `venv/bin/<tool>`**, never bare. See the environment section above.
- **Commit message prefix:** every task commits as `sec(N): <summary>` where N is the task number, so a later agent can resume from `git log`.

### Verification commands (used by every task)

```bash
pytest tests/ -v                              # full Python suite
pytest tests/test_api_auth.py -v              # auth/app-factory tests
cd frontend && npm test -- --run              # vitest
cd frontend && npm run build                  # tsc --noEmit + vite build
```

---

## Manual prerequisites — a human must do these

**Do not attempt these yourself.** They need console access an agent does not have. Report them to the user as a checklist; they are not blockers for any code task below.

- [ ] **Backblaze B2 console:** confirm the backup bucket is **private**, not public. Confirm `BACKUP_S3_ACCESS_KEY` is an *application key scoped to that one bucket* with write+list permission, not a master/account-wide key. This was the single unverifiable item in the audit and it sits under the most sensitive data in the system.
- [ ] **Anthropic console:** set a spend alert / usage limit on the API key. No code in this repo monitors spend.
- [ ] **Railway → web service → Variables:** set `SESSION_TTL_DAYS` and `SESSION_MAX_DAYS` explicitly. They are currently unset, so the app runs on code defaults (14 and 60) by accident rather than by decision. Setting them to those same values is fine — the point is that it becomes a choice.
- [ ] **Railway → Postgres service → Variables:** the orphaned `DATABASE_PUBLIC_URL` template still exists. It resolves to an empty host today (harmless), but it means one dashboard toggle produces a working public connection string. Either delete it or note in writing that public networking must never be enabled on this service.
- [ ] **Before making the repo public** (not now): `scripts/simplefin_backfill.py` lines 40-49 hardcode real bank names and account last-4 digits. They are in git history, so deleting the lines is not enough — this needs a history rewrite (`git filter-repo`) or a fresh repo. Treat as a launch blocker, not a cleanup task.
- [ ] **Separate audit:** `inventory.tomkeefe.ai` appears in Certificate Transparency alongside this app. It was not probed. It deserves its own pass.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `app/limits.py` | **Create** | Pure-ASGI request body size cap. New file so the cap is unit-testable without the app factory, and so it stays out of `api.py`'s already-dense login section. |
| `app/api.py` | Modify | Register the body cap; tighten CSP; make `/api/*` cache policy explicit; fire a security alert when the login lockout trips. |
| `app/routes.py` | Modify | Add `POST /logout-all`; add length caps to the label and nickname write paths. |
| `database.py` | Modify | Add `delete_all_sessions()`. No schema change. |
| `services/telegram_notify.py` | Modify | Add a non-blocking `notify_background()` used by security alerts. |
| `jobs/backup_db.py` | Modify | Encrypt the dump before upload; alert on a status transition away from `ok`. |
| `scripts/verify_backup.py` | **Create** | Non-destructive backup verification: download the newest dump, decrypt it, and prove `pg_restore` can read it. Also carries the full restore runbook in its docstring. |
| `scripts/drop_v1_archive.py` | **Create** | One-off: export the unused v1 archive tables to JSON, then drop them. Gated behind an explicit confirmation. |
| `config.py` | Modify | Read `BACKUP_ENCRYPTION_KEY`. |
| `requirements.txt` | Modify | Pin `starlette>=1.3.1`; add `cryptography`. |
| `.env.example` | Modify | Document `BACKUP_ENCRYPTION_KEY`. |
| `CLAUDE.md` | Modify | Document `BACKUP_ENCRYPTION_KEY` in the env var table. |
| `.claude/settings.local.json` | Modify | Remove the `railway variables *` auto-approval. |
| `frontend/src/api.ts` | Modify | Add `logoutAll()`. |
| `frontend/src/screens/Settings.tsx` | Modify | Add the "Sign out everywhere" button. |
| `tests/test_money.py` | Modify | Task 0 only: de-fragilize the date fixture in one test. No production code involved. |
| `tests/test_limits.py` | **Create** | Body-cap unit tests. |
| `tests/test_api_auth.py` | Modify | Body-cap integration, CSP, cache-control, lockout alert. |
| `tests/test_api_routes.py` | Modify | `/logout-all`, length caps. |
| `tests/test_backup_db.py` | Modify | Encryption and alert-on-transition. |
| `frontend/src/api.test.ts` | Modify | `logoutAll()` test. |

---

# PHASE 0 — Green baseline

Not a security task. It exists because every later task's verification step says
"run the full suite and expect PASS," and that instruction is worthless while the
suite is already red.

---

### Task 0: Fix the date-fragile Money test

**Why:** `tests/test_money.py::test_breakdown_rows_returns_vendor_rows_newest_first`
fails on Mondays and passes every other day. It inserts a transaction dated
"yesterday" and then queries a `weeks=1` window. The app's week runs Monday
through Sunday (see `metrics.week_bounds`), so on a Monday "yesterday" is Sunday
— the *previous* week — and the row is correctly excluded. The production code
is right; the test's fixture dates are wrong.

Discovered 2026-08-03, which was a Monday. Left unfixed it will heal itself
tomorrow and re-break next Monday, which is worse than a permanent failure
because nobody will trust the suite.

**Files:**
- Modify: `tests/test_money.py` (~line 726-745)

**Interfaces:** none.

- [ ] **Step 1: Reproduce the failure**

Run: `venv/bin/pytest tests/test_money.py::test_breakdown_rows_returns_vendor_rows_newest_first -q`

Expected on a Monday: FAIL with `assert ['a3', 'a2'] == ['a3', 'a2', 'a1']`.
Expected on any other day: PASS.

**If it passes**, you are not running on a Monday. Do not conclude the bug is
gone — reproduce it deterministically by confirming the arithmetic instead:
`venv/bin/python -c "import metrics, datetime; print(metrics.week_bounds(datetime.date(2026,8,3)))"`
and check whether `2026-08-02` falls inside that range. Then fix it anyway.

- [ ] **Step 2: Anchor both fixture dates inside the same week**

The test currently reads:

```python
    today = scorecard._local_today()
    d0, d1 = today.isoformat(), (today - timedelta(days=1)).isoformat()
```

`d1` is the problem: it is only in the same Mon–Sun week as `d0` on Tue–Sun.

Replace those two lines with dates derived from the week's own start, so the
relationship holds on every day of the week:

```python
    # Both dates must land inside the SAME Mon-Sun week as the weeks=1 query
    # window, or the older row falls into the previous week and vanishes from
    # the result. Deriving them from the week's start rather than from "today
    # minus one day" is what makes this hold on a Monday too -- it did not,
    # and this test failed every Monday and passed the other six days.
    today = scorecard._local_today()
    week_start, _ = metrics.week_bounds(today)
    d1, d0 = week_start.isoformat(), (week_start + timedelta(days=1)).isoformat()
```

`d0` must remain the *newer* date and `d1` the older one, because the assertion
expects newest-first ordering (`a3`, `a2` on `d0`; `a1` on `d1`).

Add `import metrics` to the test function's imports alongside the existing
`import database as db` / `from app import scorecard` lines if it is not already
there. Confirm `timedelta` is already imported at module level — it is used by
the current code, so it should be.

- [ ] **Step 3: Verify the fix holds on a Monday specifically**

Run: `venv/bin/pytest tests/test_money.py -q`
Expected: PASS, all tests in the file.

Then confirm it is genuinely date-independent rather than accidentally passing
today, by checking the derived dates land in one week:

```bash
venv/bin/python -c "
import datetime, metrics
for offset in range(7):
    d = datetime.date(2026, 8, 3) + datetime.timedelta(days=offset)
    ws, we = metrics.week_bounds(d)
    print(d, d.strftime('%a'), '-> week', ws, we, '| d1', ws, 'in week:', ws >= ws and ws <= we)
"
```

Every day must show the derived `d1` inside its own week.

- [ ] **Step 4: Run the full suite**

Run: `venv/bin/pytest tests/ -q`
Expected: PASS — 606 passed, 0 failed. This is the green baseline every later
task depends on.

- [ ] **Step 5: Commit**

```bash
git add tests/test_money.py
git commit -m "sec(0): fix date-fragile breakdown_rows test

The test dated one fixture row 'yesterday' and queried a weeks=1 window. The
app's week is Mon-Sun, so on a Monday that row landed in the previous week and
was correctly excluded -- the test failed every Monday and passed the other six
days. Both dates now derive from the week's own start."
```

---

# PHASE 1 — Unauthenticated exposure

The only finding an anonymous stranger can act on today, plus the outdated dependency.

**Do Task 2 before Task 1.** It changes the runtime dependency set, and every
later task should be written and tested against the final versions rather than
the ones being replaced. If the fastapi bump breaks something, that must surface
before new code is layered on top of it.

---

### Task 1: Cap request body size

**Why (restated for a cold-start agent):** `POST /api/login` is reachable with no authentication. Its Pydantic model caps `password` at 200 characters, but Pydantic runs *after* Starlette has already read and parsed the entire request body into memory. A live probe confirmed a 1 MB body is accepted onto the wire with no `413` from Railway's edge. Repeated multi-hundred-megabyte POSTs exhaust the single service's memory. The login lockout does not help — the body is fully buffered before the lockout code runs.

**Files:**
- Create: `app/limits.py`
- Create: `tests/test_limits.py`
- Modify: `app/api.py` (registration inside `create_app`, after line 141)
- Modify: `tests/test_api_auth.py` (append)

**Interfaces:**
- Produces: `app.limits.BodySizeLimitMiddleware(app, max_bytes: int = MAX_BODY_BYTES)` — a pure ASGI middleware class. `app.limits.MAX_BODY_BYTES: int = 65536`. `app.limits.declared_content_length(headers: list) -> Optional[int]`.
- Consumes: nothing from other tasks.

**Design note — why pure ASGI and not `@app.middleware("http")`:** Starlette's `BaseHTTPMiddleware` (what that decorator uses) sits above the ASGI receive channel and cannot reject a request while its body is still arriving. A pure ASGI middleware can. It checks `Content-Length` first for the ordinary case, and for chunked requests that omit the header it counts bytes as they stream, so a lying or absent `Content-Length` cannot evade the cap.

- [ ] **Step 1: Write the failing unit tests**

Create `tests/test_limits.py`:

```python
"""app/limits: request body size cap.

Exercises the middleware directly through the ASGI interface rather than
through the app, so both the Content-Length fast path and the streaming
counter are covered without needing a route that reads a huge body."""
import pytest


def _scope(headers):
    return {"type": "http", "method": "POST", "path": "/api/login", "headers": headers}


async def _collect(middleware, scope, messages):
    """Drives the middleware with a canned sequence of receive messages and
    returns the ASGI messages it sent."""
    sent = []
    remaining = list(messages)

    async def receive():
        return remaining.pop(0)

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


class _EchoApp:
    """Minimal downstream app: drains the body, then returns 200."""

    def __init__(self):
        self.body = b""
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        while True:
            message = await receive()
            self.body += message.get("body", b"")
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def test_declared_content_length_parses_header():
    from app.limits import declared_content_length

    assert declared_content_length([(b"content-length", b"123")]) == 123
    assert declared_content_length([(b"Content-Length", b"123")]) == 123
    assert declared_content_length([(b"content-type", b"application/json")]) is None
    assert declared_content_length([(b"content-length", b"not-a-number")]) is None


@pytest.mark.asyncio
async def test_oversized_content_length_is_rejected_before_the_app_runs():
    from app.limits import BodySizeLimitMiddleware

    downstream = _EchoApp()
    middleware = BodySizeLimitMiddleware(downstream, max_bytes=100)
    scope = _scope([(b"content-length", b"5000")])

    sent = await _collect(middleware, scope, [])

    assert sent[0]["status"] == 413
    # `called`, not `body`: on the fast path the app is never invoked at all,
    # so asserting body == b"" would pass trivially and prove nothing.
    assert downstream.called is False


@pytest.mark.asyncio
async def test_streamed_body_over_the_cap_is_rejected_without_content_length():
    from app.limits import BodySizeLimitMiddleware

    downstream = _EchoApp()
    middleware = BodySizeLimitMiddleware(downstream, max_bytes=100)
    scope = _scope([(b"transfer-encoding", b"chunked")])
    messages = [
        {"type": "http.request", "body": b"x" * 60, "more_body": True},
        {"type": "http.request", "body": b"x" * 60, "more_body": False},
    ]

    sent = await _collect(middleware, scope, messages)

    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_body_within_the_cap_passes_through_untouched():
    from app.limits import BodySizeLimitMiddleware

    downstream = _EchoApp()
    middleware = BodySizeLimitMiddleware(downstream, max_bytes=100)
    scope = _scope([(b"content-length", b"10")])
    messages = [{"type": "http.request", "body": b"x" * 10, "more_body": False}]

    sent = await _collect(middleware, scope, messages)

    assert sent[0]["status"] == 200
    assert downstream.body == b"x" * 10


@pytest.mark.asyncio
async def test_non_http_scopes_pass_straight_through():
    from app.limits import BodySizeLimitMiddleware

    seen = []

    async def downstream(scope, receive, send):
        seen.append(scope["type"])

    middleware = BodySizeLimitMiddleware(downstream, max_bytes=100)
    await middleware({"type": "lifespan"}, None, None)

    assert seen == ["lifespan"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_limits.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.limits'`

- [ ] **Step 3: Write the middleware**

Create `app/limits.py`:

```python
"""Request body size limiting.

POST /api/login is reachable without authentication, and Pydantic's
max_length on the password field is only enforced AFTER Starlette has read
and parsed the whole body into memory — so an oversized body is a memory-
exhaustion vector that the login lockout cannot mitigate, because the body is
buffered before the lockout code ever runs.

This is a pure ASGI middleware rather than a @app.middleware("http")
function on purpose: BaseHTTPMiddleware sits above the receive channel and
cannot refuse a request while its body is still arriving. Content-Length is
checked first (cheap, covers every ordinary client); a chunked request that
omits the header is counted as it streams, so an absent or dishonest
Content-Length cannot evade the cap.
"""
import logging
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Every JSON body this app accepts is a handful of short fields — the largest
# is a 500-character bank note. The biggest legitimate body is the bulk flow
# apply (MAX_BULK_FLOW_IDS = 200 ids), measured at 5-27 KB depending on id
# length. 64 KB is comfortably above that and far below anything that
# threatens the process.
MAX_BODY_BYTES = 64 * 1024


class _BodyTooLarge(HTTPException):
    """Raised from the receive wrapper when a streamed body exceeds the cap.

    Subclasses HTTPException deliberately. FastAPI's body reader wraps
    `await request.body()` in `except Exception -> HTTPException(400, "There
    was an error parsing the body")`, so a plain Exception raised from inside
    receive() gets caught and converted there — the chunked path would answer
    400 instead of 413, indistinguishable from malformed JSON, and the
    `except _BodyTooLarge` clause below would be dead code. FastAPI re-raises
    HTTPException untouched, so this base class is what makes both the
    Content-Length path and the chunked path answer 413.

    This was found in review after the first implementation shipped a plain
    Exception and 614 green tests failed to catch it — every integration test
    sent a Content-Length, so none exercised the streaming path."""

    def __init__(self):
        super().__init__(status_code=413, detail="Request body too large")


def declared_content_length(headers) -> Optional[int]:
    """Content-Length from a raw ASGI header list, or None when absent or
    unparseable. A chunked request has no Content-Length; those are caught by
    counting bytes instead."""
    for name, value in headers:
        if name.lower() == b"content-length":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


class BodySizeLimitMiddleware:
    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = declared_content_length(scope.get("headers", []))
        if declared is not None and declared > self.max_bytes:
            logger.warning(
                "Rejected request declaring %d bytes (max %d)", declared, self.max_bytes
            )
            await self._reject(send)
            return

        received = 0
        response_started = False

        async def counting_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    logger.warning(
                        "Rejected streamed request body over %d bytes", self.max_bytes
                    )
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            # Fallback only. In practice FastAPI's own HTTPException handler
            # answers first (see the _BodyTooLarge docstring), so this fires
            # only for a reader that bypasses FastAPI's body machinery.
            # Re-raise rather than swallow when a response has already begun:
            # writing a second http.response.start would be malformed, and a
            # silent return would leave the server with a truncated response
            # and no logged cause.
            if not response_started:
                await self._reject(send)
            else:
                raise

    async def _reject(self, send) -> None:
        body = b'{"detail":"Request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `pytest tests/test_limits.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Register the middleware**

In `app/api.py`, add the import alongside the existing ones (after line 13):

```python
from app.limits import MAX_BODY_BYTES, BodySizeLimitMiddleware
```

Then in `create_app`, insert the registration between the `FastAPI(...)` construction and the `@app.middleware("http")` decorator. The result should read:

```python
def create_app(lifespan=None) -> FastAPI:
    app = FastAPI(title="On Track", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    # Registered BEFORE the security_headers decorator below, which means
    # security_headers ends up OUTSIDE it in the middleware stack (Starlette
    # makes the most recently added middleware outermost). That ordering is
    # deliberate: a 413 still gets the standard security headers.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_BODY_BYTES)

    @app.middleware("http")
    async def security_headers(request, call_next):
        ...
```

Leave the body of `security_headers` exactly as it is.

- [ ] **Step 6: Write the integration test**

Append to `tests/test_api_auth.py`:

```python
def test_oversized_login_body_is_rejected_with_413(temp_db_path):
    client = _client(temp_db_path)
    resp = client.post("/api/login", json={"password": "x" * 200_000})
    assert resp.status_code == 413


def test_oversized_body_still_carries_security_headers(temp_db_path):
    client = _client(temp_db_path)
    resp = client.post("/api/login", json={"password": "x" * 200_000})
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_normal_login_body_is_unaffected_by_the_cap(temp_db_path):
    client = _client(temp_db_path)
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 200


def test_chunked_oversized_body_is_rejected_with_413(temp_db_path):
    """The only test that exercises the streaming counter through the real
    FastAPI stack. httpx sends a generator body as Transfer-Encoding: chunked
    with no Content-Length, so the fast path cannot fire.

    This test is why _BodyTooLarge subclasses HTTPException: without that, the
    chunked path answers 400 ("error parsing the body") instead of 413, and
    every other test here still passes because they all send a Content-Length."""
    client = _client(temp_db_path)

    def body():
        yield b'{"password":"'
        for _ in range(25):
            yield b"x" * 8192
        yield b'"}'

    resp = client.post("/api/login", content=body(),
                       headers={"content-type": "application/json"})
    assert resp.status_code == 413
```

Note the existing `test_login_rejects_oversized_password` (a 201-character password expecting `422`) must still pass — 201 bytes is far under the 64 KB cap, so Pydantic still handles it. If it now returns 413, the cap is set wrong.

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/ -v`
Expected: PASS, including the pre-existing `test_login_rejects_oversized_password` returning 422.

- [ ] **Step 8: Commit**

```bash
git add app/limits.py tests/test_limits.py app/api.py tests/test_api_auth.py
git commit -m "sec(1): cap request body size to 64KB before the body is buffered

Unauthenticated memory-exhaustion vector: POST /api/login reads and parses
the entire body before Pydantic's max_length applies. Pure-ASGI middleware
so the request can be refused while still arriving."
```

---

### Task 2: Bump fastapi and pin `starlette>=1.3.1`

**Why:** Production installs `starlette 0.52.1` (confirmed both from Railway build logs and, on 2026-08-03, by rebuilding the local venv from `requirements.txt` and observing it directly). Seven advisories apply. Practical impact against *this* app is low — the `Host`-header path-desync issue only matters where code makes security decisions from `request.url.path`, and the only such call here picks a cache header (`app/api.py:148`) — but it is an outdated transitive dependency, and that CVE becomes a real bypass the moment anyone adds path-based logic.

**⚠️ This task changed after the plan was first written.** The original version said "pin `starlette>=1.3.1`" and nothing else. That is **impossible**:

```
$ pip install --dry-run "fastapi==0.128.8" "starlette>=1.3.1"
ERROR: Cannot install fastapi==0.128.8 and starlette>=1.3.1 because these
package versions have conflicting dependencies.
ERROR: ResolutionImpossible
```

The lowest fastapi that accepts a patched starlette is **0.133.0** — that release dropped the `starlette<1.0.0` cap. (An earlier revision of this plan said 0.134.0; that was wrong, caught in review and corrected. Verified by resolver: 0.132.0 → ResolutionImpossible, 0.133.0 → resolves.) This task therefore bumps fastapi as well, to `0.136.3` — chosen because it is a current release verified to resolve cleanly with `starlette 1.3.1`. The user approved this larger scope on 2026-08-03.

**Files:**
- Modify: `requirements.txt`

**Interfaces:** none.

- [ ] **Step 1: Make the change**

`requirements.txt` currently reads:

```
fastapi==0.128.8
pydantic==2.13.4
uvicorn[standard]==0.39.0
httpx==0.28.1
apscheduler==3.11.3
google-auth==2.29.0
google-auth-oauthlib==1.3.1
google-api-python-client==2.198.0
python-dotenv==1.0.1
pytz==2024.1
anthropic==0.118.0
psycopg2-binary==2.9.12
boto3==1.42.97
```

Replace the `fastapi` line and add a `starlette` line, so the top of the file becomes:

```
# starlette is pinned explicitly rather than left to fastapi's floor: fastapi
# 0.128.8 resolved to starlette 0.52.1, which carries seven advisories
# including a Host-header path-desync bug. fastapi<0.133.0 cannot accept a
# patched starlette at all (pip reports ResolutionImpossible), so the fastapi
# bump here is a prerequisite for the starlette fix, not an unrelated upgrade.
# Pinned exactly, like every other line here: nixpacks reinstalls from this
# file on every deploy and no lockfile is committed, so a >= floor would let
# an unverified starlette reach production between deploys.
fastapi==0.136.3
starlette==1.3.1
pydantic==2.13.4
```

Leave every other line untouched.

- [ ] **Step 2: Install and verify the resolved versions**

```bash
venv/bin/pip install -r requirements.txt
venv/bin/pip list | grep -iE "^(fastapi|starlette) "
```

Expected: `fastapi 0.136.3` and `starlette 1.3.1` exactly. If pip reports any resolution conflict, **stop and report it** — do not hand-pick different versions to force it through.

- [ ] **Step 3: Run the full suite**

Run: `venv/bin/pytest tests/ -q`
Expected: PASS, 606 tests.

This is a major-version starlette bump (0.52 → 1.x) *and* a fastapi minor bump, so this is the single most likely task in the plan to break something. If anything fails, **stop and report exactly what**, with the traceback. Do not pin backwards to make it pass — that silently reverts the security fix this task exists for. The most likely breakage areas are `TestClient` behavior, middleware signatures, and Pydantic/`Field` interaction.

One known-benign change: under the old versions the suite emitted
`StarletteDeprecationWarning: Using httpx with starlette.testclient is
deprecated; install httpx2 instead`. That warning may disappear or change. A
changed warning is not a failure.

- [ ] **Step 4: Verify the app still boots**

Tests exercise the app factory but not a real server start. Confirm uvicorn comes up:

```bash
venv/bin/python -c "from main import app; print('app imported OK')"
```

Expected: `app imported OK` with no traceback. An import-time failure here is exactly the class of breakage a dependency bump causes and tests miss.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "sec(2): bump fastapi to 0.136.3 and pin starlette==1.3.1

Production resolved to starlette 0.52.1 with seven open advisories. None are
reachable from this app's code today, but fastapi==0.128.8 cannot accept a
patched starlette at all (ResolutionImpossible), so the fastapi bump is a
prerequisite for the fix rather than a separate upgrade."
```

- [ ] **Step 6: Report the deploy requirement**

Tell the user this takes effect only on the next Railway deploy, and that the deploy needs watching — a fastapi minor bump plus a starlette major bump is precisely the change that passes every test and then fails at boot in production. The healthcheck at `/api/health` with a 120s timeout (`railway.toml`) is the safety net, and Railway keeps serving the previous deployment if the new one fails to become healthy.

---

# PHASE 2 — Local tooling

---

### Task 3: Remove the `railway variables` auto-approval

**Why:** `.claude/settings.local.json` auto-approves `Bash(railway variables *)` with no confirmation prompt. That command prints `ANTHROPIC_API_KEY`, `APP_PASSWORD`, `SIMPLEFIN_ACCESS_URL`, `GOOGLE_CALENDAR_REFRESH_TOKEN`, and `DATABASE_URL` to stdout, where they land in a session transcript. It is not a remote-attacker path — it is a local tooling path that partly defeats the redaction boundary the codebase is careful about everywhere else. The SimpleFIN URL in particular *is* the credential.

**Files:**
- Modify: `.claude/settings.local.json` (line 61)

**Interfaces:** none.

- [ ] **Step 1: Remove the entry**

Delete this single line from the permissions allow list:

```json
      "Bash(railway variables *)",
```

Leave every other Railway entry in place — `railway status`, `railway deployment *`, and the `mcp__railway__list_*` tools do not print secret values.

- [ ] **Step 2: Verify the file is still valid JSON**

```bash
python -c "import json,pathlib; json.loads(pathlib.Path('.claude/settings.local.json').read_text()); print('valid')"
```

Expected: `valid`

- [ ] **Step 3: Commit**

```bash
git add .claude/settings.local.json
git commit -m "sec(3): drop railway variables auto-approval

That command prints every secret to stdout with no prompt, where it lands in
a session transcript."
```

---

# PHASE 3 — Incident response

The audit's framing: the fundamentals are sound, so the real gaps are all "what happens after something else goes wrong." These two tasks are that.

---

### Task 4: Revoke all sessions (backend)

**Why:** `database.py` exposes only `delete_session(token)` and an expiry sweep. There is no way to revoke everything. Because sessions are deliberately decoupled from the password — which is the *right* design, it is why a leaked password cannot be used to forge a cookie — **rotating `APP_PASSWORD` does not invalidate existing cookies.** If a cookie leaks, the only remediation today is waiting out the 60-day absolute cap or deleting rows from Postgres by hand.

**Files:**
- Modify: `database.py` (add next to `delete_expired_sessions`, ~line 1475)
- Modify: `app/routes.py` (add after `post_logout`, ~line 25)
- Modify: `tests/test_api_routes.py` (append)

**Interfaces:**
- Produces: `database.delete_all_sessions() -> None`; `POST /api/logout-all` returning `{"ok": True}`.
- Consumed by: Task 5 (frontend button).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_routes.py`. First check how that file builds an authenticated client — if it has its own helper, use it; otherwise use this, which matches `tests/test_api_auth.py`:

```python
def test_logout_all_invalidates_every_session(temp_db_path):
    from fastapi.testclient import TestClient
    from app.api import create_app

    app = create_app()
    phone = TestClient(app, base_url="https://testserver")
    laptop = TestClient(app, base_url="https://testserver")

    assert phone.post("/api/login", json={"password": "test-password"}).status_code == 200
    assert laptop.post("/api/login", json={"password": "test-password"}).status_code == 200
    assert phone.get("/api/settings").status_code == 200
    assert laptop.get("/api/settings").status_code == 200

    assert laptop.post("/api/logout-all").status_code == 200

    # Both devices are now signed out — including the one that never asked.
    # That is the entire point: this is the "my cookie leaked" button.
    assert phone.get("/api/settings").status_code == 401
    assert laptop.get("/api/settings").status_code == 401


def test_logout_all_requires_authentication(temp_db_path):
    from fastapi.testclient import TestClient
    from app.api import create_app

    client = TestClient(create_app(), base_url="https://testserver")
    assert client.post("/api/logout-all").status_code == 401
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_api_routes.py -k logout_all -v`
Expected: FAIL — 404 on `/api/logout-all`.

- [ ] **Step 3: Add the database function**

In `database.py`, immediately after `delete_expired_sessions` (which ends around line 1481), add:

```python
def delete_all_sessions():
    """Revoke every session on every device. Single-user app, so this is the
    "my cookie may have leaked" button — there is no other remediation, since
    sessions are deliberately independent of APP_PASSWORD and rotating the
    password therefore does not invalidate an issued cookie."""
    with _cursor(write=True) as c:
        c.execute("DELETE FROM sessions")
```

Note there is no placeholder here, so no `_p()` call is needed — unlike `delete_session` above it, which does use one.

- [ ] **Step 4: Add the route**

In `app/routes.py`, directly after the existing `post_logout` route (which ends at line 25), add:

```python
@router.post("/logout-all")
def post_logout_all(response: Response):
    """Revokes every session, including the caller's own. Sits behind the
    router-level require_auth like everything else, so only someone already
    holding a valid session can trigger it."""
    db.delete_all_sessions()
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_api_routes.py -k logout_all -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the full suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add database.py app/routes.py tests/test_api_routes.py
git commit -m "sec(4): add delete_all_sessions and POST /api/logout-all

There was no revocation path at all. Because sessions are independent of
APP_PASSWORD by design, rotating the password did not invalidate a leaked
cookie -- the only remedy was waiting out SESSION_MAX_DAYS or hand-editing
Postgres."
```

---

### Task 5: "Sign out everywhere" button (frontend)

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/screens/Settings.tsx`
- Modify: `frontend/src/api.test.ts`

**Interfaces:**
- Consumes: `POST /api/logout-all` from Task 4.
- Produces: `logoutAll(): Promise<void>` exported from `frontend/src/api.ts`.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/api.test.ts`, inside the existing `describe("logout", ...)` block or in a new sibling block — match whichever the file uses:

```ts
describe("logoutAll", () => {
  it("POSTs to /api/logout-all so every session is revoked", async () => {
    await logoutAll();
    expect(fetchMock).toHaveBeenCalledWith("/api/logout-all", { method: "POST" });
  });

  it("never throws even if the request fails — the client always treats sign-out as done", async () => {
    fetchMock.mockRejectedValueOnce(new Error("network"));
    await expect(logoutAll()).resolves.toBeUndefined();
  });
});
```

Add `logoutAll` to the existing import at the top of the file:

```ts
import { apiGet, login, LockedOutError, logout, logoutAll, onUnauthorized, UnauthorizedError } from "./api";
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- --run`
Expected: FAIL — `logoutAll` is not exported.

- [ ] **Step 3: Add the API function**

In `frontend/src/api.ts`, directly after the existing `logout` function (lines 43-45), add:

```ts
// Deliberately mirrors logout(): swallow failures and always resolve. The
// caller returns to the login screen either way -- a sign-out that appears to
// fail is worse than one that already succeeded server-side.
export async function logoutAll(): Promise<void> {
  await fetch("/api/logout-all", { method: "POST" }).catch(() => {});
}
```

- [ ] **Step 4: Add the button**

In `frontend/src/screens/Settings.tsx`, add `logoutAll` to the existing import on line 2:

```ts
import { apiGet, apiSend, logout, logoutAll } from "../api";
```

Then next to the existing `signOut` handler (around line 98), add:

```ts
  const signOutEverywhere = async () => {
    // Confirm because this is destructive and effectively irreversible: it
    // kills sessions on devices the user may not have in hand, and the only
    // way back is re-entering the password on each one. It sits directly
    // below the ordinary Sign out button, so a misclick is plausible.
    if (!window.confirm("Sign out on every device? You'll need to log in again everywhere.")) return;
    await logoutAll();
    onLoggedOut();
  };
```

This is the first and only `window.confirm` in `frontend/src` — deliberate, because
this is the only destructive control on the screen.

Then add a sibling row. The existing Account section reads:

```tsx
      <h2 className="section-label">Account</h2>
      <div className="group">
        <div className="row">
          <span className="grow">Sign out</span>
          <button type="button" onClick={signOut}>Sign out</button>
        </div>
      </div>
```

Note the existing button has **no className at all**. Add a sibling row inside the
same `<div className="group">`, with matching label/button text (every other row in
this file pairs identical text — the explanatory sentence lives in the confirm
dialog, not the label):

```tsx
        <div className="row">
          <span className="grow">Sign out everywhere</span>
          <button type="button" onClick={signOutEverywhere}>Sign out everywhere</button>
        </div>
```

Do not add a className, do not add new CSS, do not touch `styles.css`.

- [ ] **Step 5: Run the tests and the build**

```bash
cd frontend && npm test -- --run && npm run build
```
Expected: both PASS. `npm run build` runs `tsc --noEmit` first, so a type error fails the build.

- [ ] **Step 6: Look at it**

There is no component test framework in this repo (`CLAUDE.md` says so explicitly) — components are verified by `tsc --noEmit` + `vite build` plus a manual look.

**The manual look cannot be done against a local dev server.** The session cookie is set with `Secure` (`app/auth.py:19`), so a real browser will not send it over plain HTTP and login does not work locally. Do not burn time fighting this.

Do the static verification instead — `npm test -- --run`, `npm run build`, and read the rendered JSX — and record that the visual check is outstanding and must happen on a deploy. The same limitation applies to Task 12's CSP check.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api.ts frontend/src/screens/Settings.tsx frontend/src/api.test.ts
git commit -m "sec(5): add Sign out everywhere button"
```

---

### Task 6: Alert when the login lockout trips

**Why:** Failed logins are logged and the lockout works — a live probe confirmed attempts 1-5 return 401 and 6-8 return 429. But nothing tells the owner. The only way to learn someone is hammering the login is to manually open Railway's logs, whose retention is short. The notification channel already exists and is send-only.

**Files:**
- Modify: `services/telegram_notify.py`
- Modify: `app/api.py` (`_record_login_failure`, lines 108-127)
- Modify: `tests/test_api_auth.py` (append)

**Interfaces:**
- Produces: `services.telegram_notify.notify_background(text: str) -> None`.
- Consumed by: Task 7.

**Two design decisions to preserve:**

1. **Fire in a background thread.** `notify()` uses `httpx` with a 15-second timeout, and `_record_login_failure` runs while holding `_LOGIN_LOCK` — the process-wide lock serializing the whole login path. A blocking call there would let anyone stall every login by triggering a lockout. Fire-and-forget in a daemon thread; `notify()` already swallows every exception.
2. **Alert on the crossing only, not every subsequent failure.** Otherwise a sustained attack sends one message per guess.

Note the security alert is intentionally independent of the Telegram weekly-push toggle in Settings: someone who turned off their weekly scorecard still wants to know their login is being attacked.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_auth.py`:

```python
def test_alert_fires_when_the_lockout_threshold_is_crossed(temp_db_path, monkeypatch):
    from app import api

    sent = []
    monkeypatch.setattr(api, "notify_background", lambda text: sent.append(text))
    client = _client(temp_db_path)

    for _ in range(api.LOCKOUT_THRESHOLD):
        client.post("/api/login", json={"password": "nope"})

    assert len(sent) == 1
    assert "lock" in sent[0].lower()


def test_alert_does_not_repeat_on_every_failure_past_the_threshold(temp_db_path, monkeypatch):
    """Drives the extra failures from a client that HOLDS A VALID SESSION, and
    that detail is the whole test.

    A session-less client is short-circuited by the lockout pre-check in the
    login route (`if already_locked and not has_valid_session(request)`), which
    raises 429 before `_record_login_failure` runs again — so `count` never
    climbs past the threshold and `count == LOCKOUT_THRESHOLD` versus
    `count >= LOCKOUT_THRESHOLD` becomes indistinguishable. An earlier version
    of this test used a session-less client and passed under BOTH, verified by
    mutation. The session-cookie exemption is the only path that re-enters the
    branch with count > threshold, so it is the only path that can lock it."""
    from app import api

    sent = []
    monkeypatch.setattr(api, "notify_background", lambda text: sent.append(text))
    client = _client(temp_db_path)

    # Establish a real session first — this is what buys the pre-check exemption.
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 200

    for _ in range(api.LOCKOUT_THRESHOLD + 3):
        client.post("/api/login", json={"password": "nope"})

    assert len(sent) == 1  # the crossing only, not one per guess


def test_no_alert_below_the_threshold(temp_db_path, monkeypatch):
    from app import api

    sent = []
    monkeypatch.setattr(api, "notify_background", lambda text: sent.append(text))
    client = _client(temp_db_path)

    for _ in range(api.LOCKOUT_THRESHOLD - 1):
        client.post("/api/login", json={"password": "nope"})

    assert sent == []


def test_alert_never_contains_the_attempted_password(temp_db_path, monkeypatch):
    from app import api

    sent = []
    monkeypatch.setattr(api, "notify_background", lambda text: sent.append(text))
    client = _client(temp_db_path)

    for _ in range(api.LOCKOUT_THRESHOLD):
        client.post("/api/login", json={"password": "sentinel-guess-value"})

    assert sent
    assert "sentinel-guess-value" not in " ".join(sent)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_api_auth.py -k alert -v`
Expected: FAIL — `AttributeError: module 'app.api' has no attribute 'notify_background'`

- [ ] **Step 3: Add the background notifier**

Append to `services/telegram_notify.py`:

```python
def notify_background(text: str) -> None:
    """Fire-and-forget notify() on a daemon thread.

    Callers on latency-sensitive or lock-holding paths must not block on a
    15-second HTTP timeout — the login lockout path in particular runs while
    holding the process-wide login lock, so a blocking send there would let
    anyone stall every login by triggering a lockout. notify() already
    swallows every exception, so nothing can escape the thread."""
    threading.Thread(target=notify, args=(text,), daemon=True).start()
```

Add `import threading` to that file's imports, next to `import logging`.

- [ ] **Step 4: Wire it into the lockout**

In `app/api.py`, add to the imports (after line 13):

```python
from services.telegram_notify import notify_background
```

Then change the tail of `_record_login_failure` (currently lines 124-127) from:

```python
    if count >= LOCKOUT_THRESHOLD:
        extra = count - LOCKOUT_THRESHOLD
        seconds = min(LOCKOUT_BASE_SECONDS * (2 ** extra), LOCKOUT_MAX_SECONDS)
        db.set_setting(LOGIN_LOCKED_UNTIL_KEY, _iso(now + datetime.timedelta(seconds=seconds)))
```

to:

```python
    if count >= LOCKOUT_THRESHOLD:
        extra = count - LOCKOUT_THRESHOLD
        seconds = min(LOCKOUT_BASE_SECONDS * (2 ** extra), LOCKOUT_MAX_SECONDS)
        db.set_setting(LOGIN_LOCKED_UNTIL_KEY, _iso(now + datetime.timedelta(seconds=seconds)))
        if count == LOCKOUT_THRESHOLD:
            # Only on the crossing. Past the threshold every further guess
            # would otherwise send its own message, turning a sustained
            # attack into a notification flood. Background thread because
            # this runs under _LOGIN_LOCK — see notify_background's docstring.
            # The attempted password is deliberately absent from the text.
            notify_background(
                f"On Track: login locked after {count} failed attempts. "
                f"If this wasn't you, rotate APP_PASSWORD and use "
                f"Settings -> Sign out everywhere."
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_api_auth.py -k alert -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the full suite**

**First, prove the repeat test is not vacuous — MANDATORY.** Mutate `app/api.py` from `count == LOCKOUT_THRESHOLD` to `count >= LOCKOUT_THRESHOLD`, re-run the alert tests, and confirm the repeat test now FAILS. Then revert and confirm it passes again. If it still passes under the mutation, the test cannot detect the regression it exists to prevent — that is exactly what happened on the first attempt at this task.

Set a fresh `PYTHONPYCACHEPREFIX` per mutation run. macOS caches bytecode outside the tree, so a same-size edit within the same second silently reuses the stale `.pyc` and a real mutation looks like it had no effect:

```bash
PYTHONPYCACHEPREFIX=/tmp/pycache-mut1 venv/bin/pytest tests/test_api_auth.py -k alert -v
```

Then `git diff app/api.py` to confirm the mutation is fully reverted before committing.

Then run: `venv/bin/pytest tests/ -q`
Expected: PASS. Pay attention to the pre-existing lockout tests in `tests/test_api_auth.py` — they run real failed logins, and if `notify_background` is not stubbed there it will spawn threads that no-op (Telegram is unconfigured under test, so `notify` logs a warning and returns False). That is harmless, but if any test asserts on log output it may need adjusting.

- [ ] **Step 7: Commit**

```bash
git add services/telegram_notify.py app/api.py tests/test_api_auth.py
git commit -m "sec(6): alert on Telegram when the login lockout trips

The lockout worked but told nobody -- detection required manually opening
Railway logs. Fires once on the threshold crossing, from a daemon thread so
it cannot block the login path while holding _LOGIN_LOCK."
```

---

### Task 7: Alert when a backup fails

**Why:** Backup status is written to `app_settings` and rendered on the Settings screen, so it is not *silent* — but nobody checks Settings daily. SimpleFIN keeps a rolling 90 days and nothing older is recoverable, so a backup gap that goes unnoticed for a month is irreversible data loss, not delayed recovery.

**Files:**
- Modify: `jobs/backup_db.py`
- Modify: `tests/test_backup_db.py` (append)

**Interfaces:**
- Consumes: `services.telegram_notify.notify_background` from Task 6.

**Design decision:** alert on a *transition*, not on every failing run. The job runs daily; a persistent failure would otherwise send a message every day forever. Read the previous status before writing the new one and only notify when it changes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backup_db.py`:

```python
def test_alert_fires_when_backup_status_goes_from_ok_to_error(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    db.set_setting("backup_last_status", "ok")
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)
    monkeypatch.setattr(backup_db, "_dump_to_file", _raise_dump_error)

    backup_db.run()

    assert len(sent) == 1
    assert "backup" in sent[0].lower()


def test_alert_does_not_repeat_while_the_failure_persists(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    db.set_setting("backup_last_status", "ok")
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)
    monkeypatch.setattr(backup_db, "_dump_to_file", _raise_dump_error)

    backup_db.run()
    backup_db.run()
    backup_db.run()

    assert len(sent) == 1  # one alert for the transition, not one per run


def test_alert_fires_on_recovery_back_to_ok(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    db.set_setting("backup_last_status", "error: see logs")
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)
    monkeypatch.setattr(backup_db, "_dump_to_file", lambda path: _write_plausible_dump(path))
    monkeypatch.setattr(backup_db, "_upload", lambda *a, **k: None)
    monkeypatch.setattr(backup_db, "_verify_uploaded", lambda key: True)
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: None)

    backup_db.run()

    assert len(sent) == 1
    assert "recover" in sent[0].lower() or "ok" in sent[0].lower()


def test_alert_never_contains_exception_text(temp_db_path, monkeypatch):
    """The redaction boundary applies to notifications too -- a pg_dump or S3
    error can embed DATABASE_URL or the S3 credentials in its message."""
    import database as db
    from jobs import backup_db

    db.set_setting("backup_last_status", "ok")
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)

    def _raise_with_secret(path):
        raise RuntimeError("postgres://user:SENTINELSECRET@host:5432/railway")

    monkeypatch.setattr(backup_db, "_dump_to_file", _raise_with_secret)

    backup_db.run()

    assert sent
    assert "SENTINELSECRET" not in " ".join(sent)
```

Add these two helpers near the top of `tests/test_backup_db.py`, after the existing imports:

```python
def _raise_dump_error(path):
    from jobs.backup_db import BackupDumpError
    raise BackupDumpError("pg_dump exited with status 1")


def _write_plausible_dump(path):
    """Writes a file comfortably over MIN_DUMP_BYTES so
    _assert_dump_is_plausible passes."""
    with open(path, "wb") as f:
        f.write(b"x" * 4096)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_backup_db.py -k alert -v`
Expected: FAIL — `AttributeError: module 'jobs.backup_db' has no attribute 'notify_background'`

- [ ] **Step 3: Implement the transition alert**

In `jobs/backup_db.py`, add to the imports (after line 47):

```python
from services.telegram_notify import notify_background
```

Add this helper just above `run()`:

```python
def _set_status_and_alert(status: str) -> None:
    """Writes backup_last_status and notifies only when it CHANGES.

    The job runs daily, so alerting on every failing run would send a message
    every day for as long as the failure persists. Transitions are what carry
    information: ok -> error means something just broke, error -> ok means it
    just healed.

    The message is built only from `status`, which is always a closed-set
    value from services/safe_status.py -- never str(exception). A pg_dump or
    S3 failure can carry DATABASE_URL or the S3 credentials in its message,
    and a Telegram push is an outbound path like any other.

    First observation (`previous is None`) is special-cased -- see the inline
    comment. A first success is not a recovery, and a deploy that never opted
    into backups is not a failure; but a first run that genuinely errors does
    still alert."""
    previous = db.get_setting("backup_last_status")
    db.set_setting("backup_last_status", status)
    if previous == status:
        return
    if previous is None and status in ("ok", NOT_CONFIGURED):
        # First observation ever, on a deploy that has never recorded a status.
        # "ok" here is a FIRST SUCCESS, not a recovery — announcing "recovered"
        # would imply a failure that never happened. NOT_CONFIGURED here means
        # the deploy simply never opted into backups, which CLAUDE.md documents
        # as a clean no-op rather than a fault, so calling it FAILED is a false
        # alarm. A first observation that is a REAL error still alerts below —
        # a backup that has never once worked is exactly what the user needs
        # to hear about. Note "ok" -> NOT_CONFIGURED still alerts: that means
        # working backups just went dark, which is the silent failure this
        # whole task exists to catch.
        return
    if status == "ok":
        notify_background("On Track: database backup recovered — latest run succeeded.")
    else:
        notify_background(
            f"On Track: database backup FAILED (status: {status}). "
            f"SimpleFIN only keeps 90 days, so a prolonged gap is unrecoverable."
        )
```

Then replace every `db.set_setting("backup_last_status", ...)` call inside `run()` with `_set_status_and_alert(...)`. There are four:

| Line (approx) | Current | Becomes |
|---|---|---|
| 273 | `db.set_setting("backup_last_status", NOT_CONFIGURED)` | `_set_status_and_alert(NOT_CONFIGURED)` |
| 290 | `db.set_setting("backup_last_status", "ok")` | `_set_status_and_alert("ok")` |
| 298 | `db.set_setting("backup_last_status", PG_DUMP_VERSION_MISMATCH)` | `_set_status_and_alert(PG_DUMP_VERSION_MISMATCH)` |
| 302 | `db.set_setting("backup_last_status", safe_status(e))` | `_set_status_and_alert(safe_status(e))` |

Leave every `db.set_setting("backup_last_run", ...)` call exactly as it is.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_backup_db.py -v`
Expected: PASS, including all pre-existing tests in that file.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add jobs/backup_db.py tests/test_backup_db.py
git commit -m "sec(7): alert on Telegram when backup status changes

Alerts on transition, not per-run, so a persistent failure does not send a
message daily. Message is built only from the closed-set safe_status value,
never exception text."
```

---

# PHASE 4 — Backup integrity

The database is the highest-value object in this system. A dump of it exposes a complete financial picture, dated alcohol and substance-use records (including the one metric the app marks `private: True`), calendar titles and locations, and AI-written summaries of the owner's habits.

---

### Task 8: Encrypt the dump before upload

**Why:** `pg_dump` output is uploaded to Backblaze B2 unencrypted, protected only by TLS in transit and the bucket's ACL — and whether that bucket is private and whether the access key is bucket-scoped could not be verified from the repo (it is a manual prerequisite above).

**The threat this addresses, stated precisely:** compromise of the B2 bucket or its access key *alone*. It does **not** protect against Railway compromise, because the encryption key lives in a Railway env var — an attacker with Railway access already has the database itself, so this adds nothing there. That is the honest scope, and it is worth doing: B2 credentials and Railway credentials are separate blast radii.

**Files:**
- Modify: `requirements.txt`
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `CLAUDE.md`
- Modify: `jobs/backup_db.py`
- Modify: `tests/test_backup_db.py` (append)

**Interfaces:**
- Produces: `jobs.backup_db._encrypt_file(src_path: str, dst_path: str) -> None`, `jobs.backup_db._is_encryption_configured() -> bool`, and the `.dump.enc` key suffix.
- Consumed by: Task 9 (`scripts/verify_backup.py` must decrypt).

**Design decision — a missing key does not stop backups.** If `BACKUP_ENCRYPTION_KEY` is unset, the job logs a warning and uploads unencrypted, exactly as today. Rationale: a backup gap is irreversible (SimpleFIN's 90-day window), an unencrypted backup is not. Failing closed here would trade a certain, permanent loss for a conditional, partial one. The warning and the file extension both make the state visible.

- [ ] **Step 1: Add the dependency**

Add to `requirements.txt`:

```
cryptography>=42.0.0
```

Then:

```bash
source venv/bin/activate && pip install -r requirements.txt
```

- [ ] **Step 2: Add the config var**

In `config.py`, alongside the other `BACKUP_*` vars:

```python
# Fernet key (44-char urlsafe base64) used to encrypt the pg_dump before it
# leaves the machine. Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Unset means backups still run, unencrypted, with a logged warning -- a
# backup gap is unrecoverable (SimpleFIN keeps 90 days), an unencrypted
# backup is not. Store the key somewhere OTHER than Railway as well, or a
# Railway-side loss takes the backups with it.
BACKUP_ENCRYPTION_KEY = os.getenv("BACKUP_ENCRYPTION_KEY", "")
```

Add the same variable to `.env.example` with a one-line comment, and a row to the Optional env var table in `CLAUDE.md`. Per the Global Constraints, all three must change in this commit.

- [ ] **Step 3: Write the failing tests**

Append to `tests/test_backup_db.py`:

```python
def test_dump_is_encrypted_before_upload_when_a_key_is_set(temp_db_path, monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    from jobs import backup_db

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(backup_db, "BACKUP_ENCRYPTION_KEY", key)

    plaintext = tmp_path / "plain.dump"
    plaintext.write_bytes(b"PGDMP-sentinel-payload")
    ciphertext = tmp_path / "plain.dump.enc"

    backup_db._encrypt_file(str(plaintext), str(ciphertext))

    raw = ciphertext.read_bytes()
    assert b"PGDMP-sentinel-payload" not in raw          # actually encrypted
    assert Fernet(key.encode()).decrypt(raw) == b"PGDMP-sentinel-payload"  # and reversible


def test_uploaded_key_ends_in_enc_when_encryption_is_on(temp_db_path, monkeypatch):
    from cryptography.fernet import Fernet
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "BACKUP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)
    monkeypatch.setattr(backup_db, "_dump_to_file", _write_plausible_dump)
    monkeypatch.setattr(backup_db, "_verify_uploaded", lambda key: True)
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: None)
    monkeypatch.setattr(backup_db, "notify_background", lambda text: None)

    uploaded = []
    monkeypatch.setattr(backup_db, "_upload", lambda path, key: uploaded.append(key))

    backup_db.run()

    assert len(uploaded) == 1
    assert uploaded[0].endswith(".dump.enc")


def test_backup_still_runs_unencrypted_when_no_key_is_set(temp_db_path, monkeypatch):
    """A missing key must not stop backups -- a gap is unrecoverable, an
    unencrypted dump is not."""
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "BACKUP_ENCRYPTION_KEY", "")
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)
    monkeypatch.setattr(backup_db, "_dump_to_file", _write_plausible_dump)
    monkeypatch.setattr(backup_db, "_verify_uploaded", lambda key: True)
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: None)
    monkeypatch.setattr(backup_db, "notify_background", lambda text: None)

    uploaded = []
    monkeypatch.setattr(backup_db, "_upload", lambda path, key: uploaded.append(key))

    backup_db.run()

    import database as db
    assert len(uploaded) == 1
    assert uploaded[0].endswith(".dump")
    assert not uploaded[0].endswith(".enc")
    assert db.get_setting("backup_last_status") == "ok"
```

- [ ] **Step 4: Run to verify they fail**

Run: `pytest tests/test_backup_db.py -k encrypt -v`
Expected: FAIL — no `_encrypt_file` attribute.

- [ ] **Step 5: Implement encryption**

In `jobs/backup_db.py`, add `BACKUP_ENCRYPTION_KEY` to the existing `from config import (...)` block, then add these two functions above `run()`:

```python
def _is_encryption_configured() -> bool:
    return bool(BACKUP_ENCRYPTION_KEY)


def _encrypt_file(src_path: str, dst_path: str) -> None:
    """Fernet-encrypts src_path to dst_path.

    Fernet loads the whole payload into memory. A dump is ~390 KB, so that is
    fine -- but if this database ever grows into the hundreds of megabytes,
    switch to a streaming cipher rather than raising the memory ceiling.

    Lazy import so an un-configured deploy never needs cryptography installed
    to boot, matching how _s3_client() defers boto3."""
    from cryptography.fernet import Fernet

    with open(src_path, "rb") as f:
        plaintext = f.read()
    token = Fernet(BACKUP_ENCRYPTION_KEY.encode()).encrypt(plaintext)
    with open(dst_path, "wb") as f:
        f.write(token)
```

Then change the body of the inner `try` in `run()`. It currently reads:

```python
        stamp = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y%m%dT%H%M%S")
        key = f"{BACKUP_PREFIX}{stamp}.dump"
        fd, tmp_path = tempfile.mkstemp(suffix=".dump")
        os.close(fd)
        try:
            _dump_to_file(tmp_path)
            _assert_dump_is_plausible(tmp_path)
            _upload(tmp_path, key)
```

Change it to:

```python
        stamp = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y%m%dT%H%M%S")
        encrypting = _is_encryption_configured()
        if not encrypting:
            logger.warning(
                "BACKUP_ENCRYPTION_KEY unset — uploading an UNENCRYPTED dump. "
                "Backups still run because a gap is unrecoverable, but the "
                "dump is protected only by the bucket's own access control."
            )
        suffix = ".dump.enc" if encrypting else ".dump"
        key = f"{BACKUP_PREFIX}{stamp}{suffix}"
        fd, tmp_path = tempfile.mkstemp(suffix=".dump")
        os.close(fd)
        enc_path = tmp_path + ".enc"
        try:
            _dump_to_file(tmp_path)
            # Plausibility is checked on the PLAINTEXT dump: MIN_DUMP_BYTES is
            # calibrated against pg_dump's own header/TOC overhead, and
            # ciphertext size would not carry that meaning.
            _assert_dump_is_plausible(tmp_path)
            if encrypting:
                _encrypt_file(tmp_path, enc_path)
                _upload(enc_path, key)
            else:
                _upload(tmp_path, key)
```

Finally, update the `finally` block so it removes both temp files:

```python
        finally:
            os.unlink(tmp_path)
            if os.path.exists(enc_path):
                os.unlink(enc_path)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_backup_db.py -v`
Expected: PASS, all tests including pre-existing ones.

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add requirements.txt config.py .env.example CLAUDE.md jobs/backup_db.py tests/test_backup_db.py
git commit -m "sec(8): encrypt the pg_dump before uploading to B2

Addresses compromise of the B2 bucket or its key alone -- NOT Railway
compromise, since the key is a Railway env var. A missing key logs a warning
and uploads unencrypted rather than skipping the backup: a gap is
unrecoverable (SimpleFIN keeps 90 days), an unencrypted dump is not."
```

- [ ] **Step 9: Report the manual step**

Tell the user they must generate a key and set it in Railway, and that they should **also store it somewhere other than Railway** — otherwise losing the Railway project takes the ability to read the backups with it. Give them the command:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Do **not** generate one yourself and paste it into the conversation — that puts the key in a transcript, which is the same class of mistake as Task 3.

---

### Task 9: Backup verification script

**Why:** There is no restore script and no documented restore path. A `pg_restore` from a B2-hosted dump has never been tested. An untested backup is not a backup — and with encryption added in Task 8 there is now a second way for a backup to be unreadable.

**Files:**
- Create: `scripts/verify_backup.py`

**Interfaces:**
- Consumes: `jobs.backup_db._s3_client`, `BACKUP_PREFIX`, `BACKUP_S3_BUCKET`, `_is_encryption_configured` from Task 8; `config.BACKUP_ENCRYPTION_KEY`.

**Scope decision:** this script is **read-only**. It downloads the newest dump, decrypts it, and runs `pg_restore --list` to prove the file is a structurally valid dump whose table-of-contents can be read. It never writes to a database. The genuinely destructive full-restore procedure is documented in the docstring for a human to run deliberately — it is not scripted, because a restore script that exists is a restore script that can be run by accident.

- [ ] **Step 1: Write the script**

Create `scripts/verify_backup.py`:

```python
"""One-off: verify the most recent database backup is readable.

Read-only. Downloads the newest dump from the backup bucket, decrypts it if
BACKUP_ENCRYPTION_KEY is set, and runs `pg_restore --list` to prove the file
is a structurally valid custom-format dump whose table-of-contents parses.
Never writes to any database.

    python scripts/verify_backup.py

Run it after changing anything about the backup path, and periodically
otherwise -- an untested backup is not a backup.

WHAT THIS DOES NOT PROVE: that a restore into a live database succeeds. It
proves the file exists, decrypts, and is a well-formed dump. That is the
large majority of what goes wrong, and it is provable without risk.

── FULL RESTORE RUNBOOK (destructive — a human runs this, deliberately) ──

Deliberately not scripted: a restore script that exists is one that can be
run by accident, and this restores over live financial data.

1. Verify first:
       python scripts/verify_backup.py

2. Download and decrypt the dump you want:
       python scripts/verify_backup.py --keep /tmp/restore.dump

3. Restore into a SCRATCH database first and confirm it looks right. Never
   restore straight into production:
       createdb ontrack_restore_test
       pg_restore -d ontrack_restore_test --no-owner /tmp/restore.dump
       psql ontrack_restore_test -c "SELECT count(*) FROM bank_transactions;"

4. Only if step 3 looks correct, restore into production. Take a fresh dump
   of the current state first -- you are about to overwrite it:
       pg_dump --format=custom -h $PGHOST -U $PGUSER -d $PGDATABASE \\
           > /tmp/pre-restore-safety.dump
       pg_restore -d "$DATABASE_URL" --clean --if-exists --no-owner /tmp/restore.dump

5. Delete /tmp/restore.dump and the scratch database when done -- both hold
   the full unencrypted contents of the database.
"""
import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import BACKUP_ENCRYPTION_KEY, BACKUP_S3_BUCKET  # noqa: E402
from jobs.backup_db import BACKUP_PREFIX, _is_encryption_configured, _s3_client  # noqa: E402


def _newest_backup_key() -> str:
    client = _s3_client()
    resp = client.list_objects_v2(Bucket=BACKUP_S3_BUCKET, Prefix=BACKUP_PREFIX)
    contents = resp.get("Contents", [])
    if not contents:
        raise SystemExit(f"No backups found under {BACKUP_PREFIX} — nothing to verify.")
    # Keys are timestamp-prefixed (YYYYmmddTHHMMSS), so lexical max is newest.
    return max(obj["Key"] for obj in contents)


def _download(key: str, path: str) -> None:
    _s3_client().download_file(BACKUP_S3_BUCKET, key, path)


def _decrypt(src_path: str, dst_path: str) -> None:
    from cryptography.fernet import Fernet

    with open(src_path, "rb") as f:
        token = f.read()
    with open(dst_path, "wb") as f:
        f.write(Fernet(BACKUP_ENCRYPTION_KEY.encode()).decrypt(token))


def _pg_restore_list(path: str) -> str:
    result = subprocess.run(
        ["pg_restore", "--list", path], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SystemExit(
            f"pg_restore could not read the dump (exit {result.returncode}).\n"
            f"{result.stderr[:2000]}"
        )
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        metavar="PATH",
        help="also write the decrypted dump here (for a deliberate restore)",
    )
    args = parser.parse_args()

    if not BACKUP_S3_BUCKET:
        raise SystemExit("BACKUP_S3_* not configured — nothing to verify.")

    key = _newest_backup_key()
    print(f"Newest backup: {key}")

    workdir = tempfile.mkdtemp(prefix="verify-backup-")
    downloaded = os.path.join(workdir, "downloaded")
    dump_path = downloaded

    try:
        _download(key, downloaded)
        print(f"Downloaded {os.path.getsize(downloaded)} bytes")

        if key.endswith(".enc"):
            if not _is_encryption_configured():
                raise SystemExit(
                    "Backup is encrypted but BACKUP_ENCRYPTION_KEY is not set. "
                    "Without the key this backup cannot be read — set it and retry."
                )
            dump_path = os.path.join(workdir, "decrypted.dump")
            _decrypt(downloaded, dump_path)
            print(f"Decrypted OK ({os.path.getsize(dump_path)} bytes)")
        elif _is_encryption_configured():
            print(
                "NOTE: newest backup is unencrypted but a key is configured — "
                "this dump predates encryption being enabled."
            )

        toc = _pg_restore_list(dump_path)
        tables = [line for line in toc.splitlines() if " TABLE DATA " in line]
        print(f"pg_restore read the dump: {len(tables)} tables with data")
        if not tables:
            raise SystemExit(
                "Dump parsed but contains NO table data — this backup is useless. "
                "Investigate before trusting it."
            )

        if args.keep:
            with open(dump_path, "rb") as src, open(args.keep, "wb") as dst:
                dst.write(src.read())
            print(f"Decrypted dump written to {args.keep}")
            print("It holds the full unencrypted database — delete it when done.")

        print("\nVERIFIED: the newest backup exists, decrypts, and is readable.")
    finally:
        for name in os.listdir(workdir):
            os.unlink(os.path.join(workdir, name))
        os.rmdir(workdir)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Check it imports and its help renders**

```bash
source venv/bin/activate
python scripts/verify_backup.py --help
```
Expected: argparse help text, no import error. (Running it for real needs the production `BACKUP_S3_*` credentials, which are not in the local environment — that is the user's step, below.)

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_backup.py
git commit -m "sec(9): add read-only backup verification script

Downloads the newest dump, decrypts it, and proves pg_restore can read its
table of contents. The destructive full-restore procedure is documented in
the docstring rather than scripted."
```

- [ ] **Step 4: Report the manual step**

Tell the user to run this against production once, from a machine with the `BACKUP_S3_*` credentials set, and to report what it prints. That run is the thing that converts "we have backups" into "we have verified backups" — it is the actual deliverable of this task, and an agent cannot do it.

---

# PHASE 5 — Hardening and minimization

Lower-value items. Each is small and independent.

---

### Task 10: Length caps on the label and nickname write paths

**Why:** `LabelPatch.label` and the `nickname` handler accept unbounded strings, unlike every other user-text field in `app/routes.py` (`SocialCreate.name`, `SocialPatch.title`, `FlowPatch.note`, all capped). Authenticated-only, so the impact is the owner bloating their own database — a consistency fix, not a vulnerability.

**Files:**
- Modify: `app/routes.py` (`LabelPatch` ~line 452; `set_bank_account_nickname` ~line 376)
- Modify: `tests/test_api_routes.py` (append)

**Interfaces:** none new.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_routes.py`, using whatever authenticated-client helper that file already defines:

```python
def test_bank_label_rejects_an_oversized_label(temp_db_path):
    from fastapi.testclient import TestClient
    from app.api import create_app

    client = TestClient(create_app(), base_url="https://testserver")
    client.post("/api/login", json={"password": "test-password"})

    resp = client.post("/api/bank/label", json={"payee": "Acme", "label": "x" * 300})
    assert resp.status_code == 422


def test_bank_account_nickname_rejects_an_oversized_nickname(temp_db_path):
    from fastapi.testclient import TestClient
    from app.api import create_app

    client = TestClient(create_app(), base_url="https://testserver")
    client.post("/api/login", json={"password": "test-password"})

    resp = client.post("/api/bank/accounts/acct-1/nickname", json={"nickname": "x" * 300})
    assert resp.status_code == 400
```

Note the different expected codes: `LabelPatch` is a Pydantic model so an over-length value fails validation with `422`, while the nickname handler takes a raw `dict` and raises `HTTPException(400)` by hand — matching its existing `isinstance` check on line 382.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_api_routes.py -k oversized -v`
Expected: FAIL — both currently succeed or return a different status.

- [ ] **Step 3: Cap the label**

In `app/routes.py`, add a constant next to the existing `MAX_NOTE_LEN = 500` on line 437:

```python
MAX_NOTE_LEN = 500
MAX_LABEL_LEN = 200
```

Then change `LabelPatch` (line 452) from:

```python
class LabelPatch(BaseModel):
    simplefin_id: Optional[str] = None
    payee: Optional[str] = None
    label: Optional[str] = None
    no_label: Optional[bool] = None
```

to:

```python
class LabelPatch(BaseModel):
    simplefin_id: Optional[str] = None
    payee: Optional[str] = None
    label: Optional[str] = Field(default=None, max_length=MAX_LABEL_LEN)
    no_label: Optional[bool] = None
```

`Field` is already imported on line 7.

- [ ] **Step 4: Cap the nickname**

In `set_bank_account_nickname` (line 376), add a length check directly after the existing type check on lines 381-382:

```python
    if nickname is not None and not isinstance(nickname, str):
        raise HTTPException(status_code=400, detail="nickname must be a string")
    if nickname is not None and len(nickname) > MAX_LABEL_LEN:
        raise HTTPException(status_code=400, detail=f"nickname too long (max {MAX_LABEL_LEN} chars)")
```

`MAX_LABEL_LEN` is defined at line 437, below this route. Module-level constants are resolved at call time, not definition time, so this works — but if it reads confusingly, move both constants up near the top of the file instead.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_api_routes.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/routes.py tests/test_api_routes.py
git commit -m "sec(10): cap label and nickname length

Every other user-text field in routes.py was already capped; these two were
the exceptions."
```

---

### Task 11: Make the `/api/*` cache policy explicit

**Why:** `cache_control_for_path` returns `None` for `/api/*`, so **no** `Cache-Control` header is set on authenticated responses carrying financial data. Modern browsers do not disk-cache `fetch()` responses without a caching header, so this is safe today — but it relies on client behavior rather than stating intent, and any intermediary that treats an absent header as cacheable could cache bank data.

**Files:**
- Modify: `app/api.py` (`cache_control_for_path`, lines 28-49)
- Modify: `tests/test_api_auth.py` (there is an existing test asserting `None` — find and update it)

**Interfaces:** `cache_control_for_path("/api/x")` changes return from `None` to `"no-store, private"`.

- [ ] **Step 1: Find ALL the existing assertions — there are two, and grepping the function name finds only one**

```bash
grep -rn "cache_control_for_path" tests/          # finds the pure-function test
grep -rn "cache-control\|Cache-Control" tests/    # finds the full-stack test too
```

Two pre-existing tests assert the old behavior:

1. `test_cache_control_for_path_pure_function` — asserts `cache_control_for_path("/api/…") is None`.
2. `test_api_responses_get_no_cache_control_header` — a full-stack `client.get("/api/health")` asserting `"cache-control" not in resp.headers`. **This one does not mention the function by name, so the first grep misses it.**

Both must be updated, not deleted — the policy is still being asserted, just with a different value.

The second test also needs **renaming**: `test_api_responses_get_no_cache_control_header` becomes a lie the moment the header exists. Rename it to `test_api_responses_are_explicitly_uncacheable` and give it a docstring explaining why the policy is explicit rather than absent. Do not add a *second* full-stack test alongside it — that produces two identical tests that can only ever fail together.

- [ ] **Step 2: Update the test and add one**

Change the existing `assert cache_control_for_path("/api/whatever") is None` to:

```python
    assert cache_control_for_path("/api/whatever") == "no-store, private"
```

And rename the full-stack test, updating its assertion in place rather than adding a new one beside it:

```python
def test_api_responses_are_explicitly_uncacheable(temp_db_path):
    """The policy is stated, not merely absent. Leaving the header off relied
    on browsers not disk-caching fetch() responses by default — true today,
    but an implicit assumption, and these responses carry bank transactions."""
    client = _client(temp_db_path)
    resp = client.get("/api/health")
    assert resp.headers["Cache-Control"] == "no-store, private"
```

Keep the pure-function test too — it and this one cover genuinely different
layers (the decision vs. the middleware actually applying it). What you must
not end up with is two full-stack tests making the same request and the same
assertion.

- [ ] **Step 3: Run to verify they fail**

Run: `pytest tests/ -k cache -v`
Expected: FAIL

- [ ] **Step 4: Change the function**

In `app/api.py`, change lines 45-46 from:

```python
    if path.startswith("/api/"):
        return None
```

to:

```python
    if path.startswith("/api/"):
        return "no-store, private"
```

And update the docstring bullet above it (currently lines 32-34) from:

```
    - `/api/*`: left alone entirely (returns None) — API responses must never
      pick up the static-asset caching rules below.
```

to:

```
    - `/api/*`: `no-store, private`. These responses carry bank transactions,
      health check-ins, and calendar contents. Leaving the header absent
      relied on browsers not disk-caching fetch() responses by default —
      true today, but an implicit assumption rather than a stated policy,
      and any intermediary treating "no header" as cacheable would be free
      to store financial data.
```

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/api.py tests/
git commit -m "sec(11): set Cache-Control no-store on /api/* explicitly"
```

---

### Task 12: Tighten the Content-Security-Policy

**Why:** the CSP is currently only `frame-ancestors 'none'`, which stops clickjacking but provides no script-source restriction. No XSS sink exists in the frontend today (the audit grepped for `dangerouslySetInnerHTML`, `innerHTML`, `eval`, and URL sinks and found none), so this is defense-in-depth against a future one. It matters more than usual here because much of the stored text originates from third parties — anyone who emails the owner or sends them a calendar invite writes strings into this database.

**⚠️ This task can break the app in a way tests will not catch.** A CSP that blocks a resource the SPA needs produces a blank page with console errors, and neither `pytest` nor `vite build` will notice. The browser check in Step 4 is mandatory, not optional.

**Files:**
- Modify: `app/api.py` (`SECURITY_HEADERS`, lines 19-25)
- Modify: `tests/test_api_auth.py` (the existing `test_security_headers_present`)

- [ ] **Step 1: Update the header**

In `app/api.py`, change the `Content-Security-Policy` entry in `SECURITY_HEADERS` from:

```python
    "Content-Security-Policy": "frame-ancestors 'none'",
```

to:

```python
    # style-src allows 'unsafe-inline' because React writes inline style
    # attributes; script-src deliberately does NOT, which is the directive
    # that actually matters. img-src allows data: for inline SVG/icon URIs.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
```

- [ ] **Step 2: Update the test**

Find `test_security_headers_present` in `tests/test_api_auth.py` and make sure its CSP assertion still holds. If it asserts equality with the old string, change it to assert on the directives that matter rather than the whole string:

```python
    csp = resp.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
```

- [ ] **Step 3: Run the suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 4: Verify in a real browser — MANDATORY**

```bash
cd frontend && npm run build          # build so the real bundle is served
source venv/bin/activate && uvicorn main:app --port 8080
```

Open `http://localhost:8080`, log in, and visit **every** screen: Today, Scorecard (Week), Money, Insights, Settings. Open DevTools → Console and confirm there is **not a single** `Refused to load…` or `Content Security Policy` violation. Check the Money screen especially — it renders charts, and inline SVG or a data-URI would be the most likely thing to trip `img-src`.

If you find a violation, do **not** loosen `script-src` to make it go away. Report exactly which directive blocked what and stop — loosening `script-src 'self'` removes the entire value of this task.

Note the local server is HTTP, so the session cookie's `Secure` flag means login will not work in a real browser locally. If that blocks you, say so and report this task as needing verification on a deploy preview instead of forcing it through.

- [ ] **Step 5: Commit**

```bash
git add app/api.py tests/test_api_auth.py
git commit -m "sec(12): tighten CSP beyond frame-ancestors

Defense-in-depth against a future XSS sink. Much of the stored text in this
app comes from third parties who email or send calendar invites to the owner."
```

---

### Task 13: Export and drop the unused v1 archive tables

**Why:** `people` and `life_log_entries` hold named individuals with `relationship_type` and freeform notes — a rolodex of the owner's real-life relationships with editorial commentary. Along with nine other v1 tables they are read by no code path in `app/`, `jobs/`, or `main.py`. They are pure blast radius with zero present-day product value: they add nothing except what a database compromise would expose.

**⚠️ Destructive and irreversible. Do not run this script.** Write it, test that it imports, commit it, and hand it to the user. The export step must succeed before any drop, and a human should look at the exported file before dropping anything.

**Files:**
- Create: `scripts/drop_v1_archive.py`

- [ ] **Step 1: Confirm the tables really are unused**

Before writing anything, verify the audit's claim yourself rather than trusting it:

```bash
cd "/Users/tomkeefe/Code Apps/life-tracker"
for t in life_log_entries people life_log_people activity_log habits habit_logs \
         categories conversation_state accomplishments weekly_focus later_items; do
  echo "=== $t ==="
  grep -rn "$t" --include=*.py . | grep -v "^./database.py" | grep -v "^./scripts/cleardb.py" | grep -v "^./tests/"
done
```

Expected: no output under any table other than the excluded files. **If any table shows a real usage, remove it from the list in Step 2 and say so in your report.**

- [ ] **Step 2: Write the script**

Create `scripts/drop_v1_archive.py`:

```python
"""One-off: export and then drop the unused v1 archive tables.

These tables are read by no v2 code path (see CLAUDE.md, "Archive tables").
They hold real personal data -- `people` and `life_log_entries` in particular
contain named individuals, relationship types, and freeform notes -- so they
are pure blast radius: they add nothing to the product and everything to what
a database compromise would expose.

DESTRUCTIVE AND IRREVERSIBLE. Exports every table to a timestamped JSON file
first and refuses to drop anything if the export fails.

    python scripts/drop_v1_archive.py --export-only          # safe, do this first
    python scripts/drop_v1_archive.py --export-and-drop      # destructive

The export lands OUTSIDE the repo (~/.on-track/v1-archive/) with mode 0600,
matching how scripts/simplefin_snapshot.py handles its archive -- it holds
the same data you are dropping, so it must not be committed or world-readable.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import database as db  # noqa: E402

V1_TABLES = [
    "life_log_entries",
    "people",
    "life_log_people",
    "activity_log",
    "habits",
    "habit_logs",
    "categories",
    "conversation_state",
    "accomplishments",
    "weekly_focus",
    "later_items",
]

EXPORT_DIR = os.path.expanduser("~/.on-track/v1-archive")


def _export() -> str:
    os.makedirs(EXPORT_DIR, mode=0o700, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    path = os.path.join(EXPORT_DIR, f"v1-archive-{stamp}.json")

    payload = {}
    for table in V1_TABLES:
        try:
            payload[table] = db.dump_table(table)
        except Exception as e:
            raise SystemExit(f"Export of {table} failed ({type(e).__name__}) — nothing dropped.")

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    total = sum(len(rows) for rows in payload.values())
    print(f"Exported {total} rows across {len(V1_TABLES)} tables to:\n  {path}")
    for table, rows in payload.items():
        print(f"  {table}: {len(rows)} rows")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--export-only", action="store_true")
    group.add_argument("--export-and-drop", action="store_true")
    args = parser.parse_args()

    path = _export()

    if args.export_only:
        print("\nExport only — nothing dropped. Open that file and confirm it")
        print("looks complete before running with --export-and-drop.")
        return

    print("\nAbout to PERMANENTLY DROP these tables from the live database.")
    print("This cannot be undone except by restoring the export above.")
    confirm = input("Type DROP to proceed: ")
    if confirm != "DROP":
        print("Aborted — nothing dropped.")
        return

    for table in V1_TABLES:
        db.drop_table(table)
        print(f"Dropped {table}")
    print(f"\nDone. The export at {path} is now the only copy of that data.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Add the two database helpers**

This script needs `dump_table` and `drop_table`, which do not exist. Per the Global Constraints, all SQL lives in `database.py`. Add both at the end of that file:

```python
def dump_table(table):
    """Return every row of `table` as a list of dicts. Used only by
    scripts/drop_v1_archive.py to export the v1 archive before dropping it.

    `table` is interpolated into the SQL because table names cannot be
    parameterized. It is validated against a hardcoded allowlist first —
    never pass a caller-supplied value here."""
    if table not in _DUMPABLE_TABLES:
        raise ValueError(f"refusing to dump unknown table: {table}")
    with _cursor() as c:
        c.execute(f"SELECT * FROM {table}")
        columns = [d[0] for d in c.description]
        return [dict(zip(columns, row)) for row in c.fetchall()]


def drop_table(table):
    """Permanently drop `table`. Used only by scripts/drop_v1_archive.py.

    Same allowlist guard as dump_table — this is the only DROP in the entire
    codebase and it must stay reachable from exactly one one-off script."""
    if table not in _DUMPABLE_TABLES:
        raise ValueError(f"refusing to drop unknown table: {table}")
    with _cursor(write=True) as c:
        c.execute(f"DROP TABLE IF EXISTS {table}")
```

And define the allowlist near the top of `database.py`, next to the other module-level constants:

```python
# The v1 archive tables — read by no v2 code path. The ONLY tables
# dump_table/drop_table will touch, so a bug or a bad argument can never
# reach a live v2 table.
_DUMPABLE_TABLES = frozenset({
    "life_log_entries", "people", "life_log_people", "activity_log",
    "habits", "habit_logs", "categories", "conversation_state",
    "accomplishments", "weekly_focus", "later_items",
})
```

- [ ] **Step 4: Write the guard tests**

Append to `tests/test_database_v2.py`:

```python
def test_dump_table_refuses_a_table_outside_the_allowlist(temp_db_path):
    import database as db
    import pytest

    with pytest.raises(ValueError):
        db.dump_table("bank_transactions")


def test_drop_table_refuses_a_table_outside_the_allowlist(temp_db_path):
    import database as db
    import pytest

    with pytest.raises(ValueError):
        db.drop_table("bank_transactions")


def test_dump_table_returns_rows_for_an_allowlisted_table(temp_db_path):
    import database as db

    assert db.dump_table("people") == []  # empty but readable
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_database_v2.py -v`
Expected: PASS

- [ ] **Step 6: Confirm the script imports, but DO NOT RUN IT**

```bash
python scripts/drop_v1_archive.py --help
```
Expected: help text. Do not pass `--export-only` or `--export-and-drop`.

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add scripts/drop_v1_archive.py database.py tests/test_database_v2.py
git commit -m "sec(13): add gated export-and-drop for the v1 archive tables

Eleven tables read by no v2 code path, including a rolodex of named
individuals with relationship notes. Pure blast radius. Script is not run by
this commit -- export first, human confirms, then drop."
```

- [ ] **Step 9: Hand it to the user**

Tell them to run `--export-only` first, open the exported JSON, confirm it looks complete, and only then run `--export-and-drop`. Note that `CLAUDE.md` currently states there is no `DROP` anywhere in the codebase — that line needs updating once this merges, and it should mention the allowlist guard.

---

## Out of scope — needs its own spec

**Do not attempt any of this as part of this plan.**

Making the app genuinely multi-user is a data-layer rebuild, not a hardening task:

1. **There is no `user_id` anywhere** — not on `sessions`, not on any data table. `require_auth` proves "a valid session exists," never "which user." Every query in `database.py` returns all rows, which is correct while all rows belong to one person. Adding a second user without redesigning this produces a cross-user data leak in every query simultaneously.
2. **Per-user credentials break the current secrets design.** `SIMPLEFIN_ACCESS_URL` and `GOOGLE_CALENDAR_REFRESH_TOKEN` are process-wide env vars read once by `config.py`. Multi-user requires them stored per-user and encrypted at rest — which invalidates the redaction boundary's core assumption that the credential never leaves `config.py` and `services/simplefin_service.py`.
3. **No application-layer field encryption.** Acceptable for one user, not for many.
4. **Logs and `app_settings` are single-tenant by assumption.**
5. **The login lockout is a global counter** — correct for one user, a trivial denial-of-service against every other user the moment there are two.

Write a spec for this before writing any code.

---

## Self-review

Checked against `SECURITY-AUDIT.md`:

- **Every "Important" finding has a task.** Body size cap → Task 1. Backups → Tasks 8 and 9 plus the Backblaze manual prerequisite. Alerting → Tasks 6 and 7. Starlette → Task 2. `railway variables` → Task 3. Session revocation → Tasks 4 and 5.
- **Every "Minor" finding is either a task or a manual prerequisite.** Length caps → Task 10. Cache-Control → Task 11. CSP → Task 12. v1 archive tables → Task 13. `ROLE_SEEDS`, `DATABASE_PUBLIC_URL`, `SESSION_TTL_DAYS`/`SESSION_MAX_DAYS`, HSTS preload, and the `inventory.tomkeefe.ai` audit → manual prerequisites, because they need console access or a history rewrite an agent should not perform unsupervised.
- **The one finding with no task:** "no minimum enforced on `APP_PASSWORD`." Deliberately dropped. The live value is 32 characters, mixed case and digits, and the lockout makes brute force impractical regardless. A startup length check would add a failure mode (refusing to boot) for no measurable gain. Noted here so a later reader knows it was considered, not missed.
- **Type consistency:** `notify_background(text: str) -> None` is defined in Task 6 and consumed identically in Task 7. `_is_encryption_configured()` and the `.dump.enc` suffix are defined in Task 8 and consumed in Task 9. `delete_all_sessions()` is defined in Task 4 and reached via `POST /api/logout-all` in Task 5. `MAX_LABEL_LEN` is used by both changes in Task 10.
- **No placeholders**, with one deliberate exception: Task 5 Step 4 requires reading the existing sign-out button's `className` out of `Settings.tsx` rather than inventing one, because the repo has no component test framework and matching the existing element is the only way to stay consistent with the design system. The step says so explicitly.
