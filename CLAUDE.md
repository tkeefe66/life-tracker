# On Track — Claude Code Guide

A single-user web app that answers one question weekly: **are you doing the things you
said you'd do?** Five scored metrics — delivery orders, gym sessions, social events,
alcohol days, substances — tracked partly passively (Gmail receipts, Google Calendar)
plus three manual check-in buttons. Uber/Lyft rides are tracked too, but as an
unscored series (no target). Delivery orders, rides, and social events also carry
dollar amounts, so the app answers "what did this cost?" alongside "did I do it?".
Pull-based: you open the app, nothing pushes proposals at you. Telegram survives only
as an optional send-only weekly scorecard push.

---

## Quick Commands

```bash
# Backend — local dev
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --port 8080

# Frontend — local dev (separate terminal; Vite proxies /api to :8080)
cd frontend && npm install && npm run dev

# Tests
pytest tests/ -v
cd frontend && npm test

# Frontend build (also runs at deploy time via nixpacks)
cd frontend && npm run build

# Re-auth Google (Calendar + Gmail scopes)
python scripts/calendar_auth.py

# DB wipe (prompts CONFIRM)
python scripts/cleardb.py
```

---

## Metrics

Week runs Monday–Sunday. Each metric has a user-set weekly target in the `targets`
table; a count `hit`s if it's on the right side of `direction`.

| Metric | Direction | Signal | Source |
|---|---|---|---|
| Delivery orders | Ceiling (≤ N/week) | Gmail receipts | `receipts.py` sender/subject rules, ambiguous cases → `ai_metrics.classify_receipt` |
| Gym sessions | Floor (≥ N/week) | Manual check-in | One tap on Today screen, one per day |
| Social events | Floor (≥ N/week) | Google Calendar + manual | `jobs/scan_calendar.py` pulls events, `ai_metrics.classify_social_event` scores social/not; counts once the event has occurred. Users can add events manually and override any detected one |
| Alcohol days | Ceiling (≤ N/week) | Manual check-in + level (1–3) | Level shown as trend only, not scored |
| Substances | Ceiling (≤ N/week, default 0) | Manual check-in | **`private: True`** — see Private metrics below |

**Manual check-ins are dated.** Every check-in route accepts an optional `date`
(defaults to today); past dates are allowed without limit, future dates are rejected
with 400. The Today screen is really a Day screen with ‹ › navigation, so backfill
works everywhere.

**Private metrics.** A `METRICS` entry may carry `private: True`. Read it with
`.get("private")` — the other entries deliberately omit the key. Privacy is enforced
in exactly two places, and nowhere else: the `format_scorecard_text` builder in
`jobs/weekly_push.py` (Telegram) and the `/api/reflection` route (which strips private
metrics from the card and drops noticings naming them before calling Claude). Private
metrics still appear on every on-screen surface.

**Rides are not a metric.** They are deliberately absent from `METRICS` — no target,
no hit/miss, no ledger row. They surface only as counts and spend.

`metrics.py` is pure computation (no DB, no I/O): `week_bounds`, `is_hit`,
`build_scorecard`, `streaks`, plus the insight math (`weekday_counts`,
`trend_direction`, `weekday_skew`, `co_occurrence`, `noticings`).
`app/scorecard.py` wires DB counts into it.

---

## Passive Ingestion — hard-won facts

- **The Gmail scan reads Trash** (`includeSpamTrash=True`). This is load-bearing, not
  incidental: the user's Uber receipts are auto-trashed, so without it the scan sees
  nothing. Gmail purges trash after ~30 days, which caps how far any backfill can
  reach.
- **The scan also runs once at startup** (`next_run_time=now` in `main.py`), so
  `railway redeploy --yes` forces an immediate re-scan instead of waiting up to
  `GMAIL_SCAN_INTERVAL_HOURS`. `gmail_last_status`/`gmail_last_result` in Settings only
  change when a scan actually runs.
- **One trip or order produces several emails.** Delivery: order receipt, tip receipt,
  refund adjustment — `receipts.is_followup()` skips the follow-ups, and a tip receipt
  with no existing order row *creates* it (many orders leave only the tip email).
  Rides: a "charge summary" and a "thanks for riding" receipt per trip.
- **Ride dedupe keys on the ride timestamp parsed from the snippet**
  (`receipts.extract_ride_time`), never the subject — two distinct trips the same
  morning share the subject "Your Sunday morning trip with Uber".
- **`rides.ride_at` is immutable after insert.** `get_rides_range` buckets on its date,
  so rewriting it would retroactively move a ride into a different week.
- Amounts come from `receipts.extract_amount` (`Total $X` in the snippet).

### Bank ingestion — hard-won facts

- **SimpleFIN keeps a rolling 90 days; anything older is unrecoverable.**
  `scripts/simplefin_snapshot.py` exists purely to stop that decay clock — it
  archives the raw payload to disk (outside the repo, mode 0600) before the
  window closes. `scripts/simplefin_backfill.py` replays saved snapshots
  through `jobs.sync_bank.run(payload=...)` — the exact same path a live sync
  uses, so backfilled and synced rows are indistinguishable and the two can
  never drift apart.
- **The sync reclassifies the entire table every run, not a recent window.**
  That's deliberate: `bank_flows` is pure and deterministic, so a full
  recompute is cheap, and it means assigning an account role retroactively
  fixes every transaction that touched it, no matter how long ago it posted.
  An earlier windowed version froze classifications made while accounts were
  still `role="unknown"` — a row that aged out of the window kept its wrong
  flow forever even after the user fixed the role.

---

## Override + Learning Pattern

Social events and rides share one pattern; follow it for any future AI-classified
signal:

1. Store the AI verdict and the user override in **separate nullable columns**
   (`is_social` / `user_is_social`; `ai_is_work` / `user_is_work`).
2. **Resolve in SQL** so every caller agrees — `COALESCE(user_title, title)`,
   `COALESCE(user_is_social, is_social)`. Never resolve in Python at one call site.
3. The scan may overwrite its own columns but must never touch the user's.
4. Feed recent overrides back as few-shot examples in the classification prompt
   (`db.get_classification_examples`, `db.get_ride_examples`) so one correction fixes
   a recurring event.
5. Only a **confirmed** user verdict changes behavior — an AI flag alone never
   excludes anything silently.

---

## Repo Layout

```
.
├── main.py                # Entry point: FastAPI app + in-process APScheduler (lifespan)
├── config.py               # All env vars — secrets and feature flags
├── database.py              # DB layer: PostgreSQL (prod) / SQLite (local dev)
├── metrics.py               # Pure metric math (week bounds, hit/miss, streaks)
├── receipts.py               # Rule-based rules: delivery vs ride vs neither, follow-up
│                            #   detection, amount + ride-time parsing
├── bank_flows.py              # Pure computation for bank ingestion: pair matching
│                            #   (match_pairs) + flow classification (classify_all) —
│                            #   no DB, no network, no Claude, same role as metrics.py
├── ai_metrics.py              # All Claude calls (receipt, calendar-event, work-ride,
│                            #   weekly reflection)
├── app/
│   ├── api.py               # FastAPI app factory: /api/health, /api/login, static SPA mount
│   ├── auth.py               # Single-user password → HMAC session cookie
│   ├── routes.py              # Protected API routes (checkins, scorecard, insights,
│   │                        #   reflection, deliveries, rides, social, spend, targets,
│   │                        #   settings, bank debug/role)
│   └── scorecard.py            # DB → domain wiring: weekly cards, spend, insights, history
├── jobs/
│   ├── scan_gmail.py          # Every GMAIL_SCAN_INTERVAL_HOURS + once at startup:
│   │                        #   three-way route — delivery order / ride / neither
│   ├── scan_calendar.py         # Daily @ CALENDAR_SCAN_HOUR: social event classification
│   ├── sync_bank.py            # Every SIMPLEFIN_SYNC_INTERVAL_HOURS + once at startup:
│   │                        #   fetch, upsert, then reclassify the WHOLE table via
│   │                        #   bank_flows (see Bank Ingestion below)
│   └── weekly_push.py           # Mon @ WEEKLY_PUSH_HOUR: optional Telegram push (skips private)
├── services/
│   ├── google_auth.py           # Shared OAuth2 credentials (Calendar + Gmail scopes)
│   ├── calendar_service.py        # Google Calendar event fetch
│   ├── gmail_service.py           # Gmail message fetch (includes Trash — see above)
│   ├── simplefin_service.py        # SimpleFIN transport + normalization — the redaction
│   │                            #   boundary for the bank-access-URL credential
│   └── telegram_notify.py         # notify(text) — send-only, no inbound handling
├── frontend/                # React + Vite SPA, built to dist/
│   └── src/
│       ├── screens/          # Today, Scorecard (Week), Insights, Settings
│       ├── components/        # DayNav, WeekNav, TrendChart, WeekdayHeatmap, SpendSubtotals…
│       ├── lib.ts             # Pure helpers (dates, labels, money, chart scales) — unit-tested
│       └── styles.css          # The whole design system: OKLCH tokens, both themes
├── scripts/
│   ├── calendar_auth.py          # One-off: OAuth2 setup, prints refresh token
│   ├── simplefin_snapshot.py       # One-off: archive the raw SimpleFIN payload to disk,
│   │                            #   outside the repo, mode 0600 — stops the 90-day decay clock
│   ├── simplefin_backfill.py       # One-off: replay saved snapshot(s) through
│   │                            #   jobs.sync_bank.run(payload=...) — the live-sync code path
│   └── cleardb.py               # One-off: wipe all DB data
├── docs/superpowers/        # specs/ and plans/ — one pair per feature, chronological
└── tests/                   # pytest — metrics, receipts, routes, jobs, services
```

There is **no component test framework**. Pure frontend logic lives in `lib.ts` and is
tested in `lib.test.ts` (vitest); components are verified by `tsc --noEmit` + `vite build`
plus a manual look.

---

## Environment Variables

This table is the source of truth. `.env.example` was brought to exact parity
with `config.py` on 2026-07-23 (33 vars, verified by diffing the file against
every `os.getenv` call) — keep it that way: adding a var to `config.py` means
adding it here *and* there in the same change.

### Required

| Var | Description |
|-----|-------------|
| `ANTHROPIC_API_KEY` | Claude API key |
| `APP_PASSWORD` | Single-user login password; exchanged for a session cookie |

### Optional

| Var | Description |
|-----|-------------|
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Send-only weekly scorecard push, toggled in Settings (default off) |
| `DATABASE_URL` | Postgres URL (Railway sets automatically); blank = local SQLite |
| `GOOGLE_CALENDAR_CLIENT_ID` / `_CLIENT_SECRET` / `_REFRESH_TOKEN` / `_ID` | OAuth2 for Calendar + Gmail — one refresh token must carry both scopes; generate via `scripts/calendar_auth.py` |
| `TIMEZONE` | Local timezone for week boundaries and job schedule times (default `America/Denver`) |
| `GMAIL_SCAN_INTERVAL_HOURS` | Gmail receipt scan interval (default 4) |
| `GMAIL_SCAN_LOOKBACK_DAYS` | Gmail scan lookback window in days (default 7) |
| `CALENDAR_SCAN_HOUR` | Daily calendar scan hour, local time (default 6) |
| `WEEKLY_PUSH_HOUR` | Monday scorecard push hour, local time (default 9) |
| `SESSION_TTL_DAYS` | Session lifetime before expiry, with sliding renewal past the halfway point (default 14) |
| `SESSION_MAX_DAYS` | Absolute session lifetime cap regardless of renewal — closes off an actively-used or stolen cookie renewing forever (default 60) |
| `BACKUP_S3_BUCKET` / `_ENDPOINT` / `_ACCESS_KEY` / `_SECRET_KEY` | Off-Railway S3-compatible destination for the weekly `pg_dump` backup (`jobs/backup_db.py`). All unset = backups no-op with a logged warning |
| `BACKUP_HOUR` | Daily backup hour, local time (default 4) |
| `SIMPLEFIN_ACCESS_URL` | SimpleFIN bearer credential — the URL *is* the secret. Unset = bank sync no-ops. Never logged or stored |
| `SIMPLEFIN_SYNC_INTERVAL_HOURS` | Bank sync interval (default 12) |
| `SIMPLEFIN_LOOKBACK_DAYS` | Bank sync lookback window (default 90 — SimpleFIN's hard cap) |
| `PAIR_WINDOW_DAYS` | Max days between the two halves of a matched transfer (default 3) |
| `INCOME_PAYEE_HINTS` | Comma-separated payroll signatures; only matching unpaired deposits count as income |

**Note:** the guardrail that used to block this. A global deny rule on
`Read(./.env.*)` matched `.env.example` as well as the real secrets file, which
is why the template drifted for so long — editing it failed silently-ish and
got skipped. The deny was narrowed on 2026-07-23 to the actually-secret names
(`.env`, `.env.local*`, `.env.production*`, and friends), so `.env.example` is
editable now. If a future session reports it cannot read `.env.example`, that
rule has been widened again — fix the rule rather than working around it.

---

## Database

`database.py` auto-selects engine:
- `DATABASE_URL` set → PostgreSQL (Railway prod)
- Not set → SQLite at `./weekly_updates.db` (local dev)

**Active v2 tables:**

| Table | Key | Notes |
|---|---|---|
| `checkins` | unique `(date, type)` | `type` ∈ gym / alcohol / substances; `level` is alcohol-only |
| `delivery_orders` | unique `gmail_message_id` | `amount` nullable; cluster key for dedupe is `(service, day, subject)` |
| `rides` | unique `gmail_message_id` | `ride_key` = parsed trip time; `ai_is_work` / `user_is_work`; `ride_at` immutable after insert |
| `calendar_events` | unique `gcal_event_id` | `user_title` / `user_is_social` overrides, `source` (`gcal`\|`manual`), `amount`. Manual events use id `manual:<uuid4>` |
| `weekly_reflections` | unique `week_start` | Cached AI paragraph — at most one Claude call per week |
| `bank_accounts` | unique `simplefin_id` | `role` (spending/bills/savings/investment/credit_card/unknown) and `active` are user-set; the sync overwrites `name`/`org`/`kind` but never those two |
| `bank_transactions` | unique `simplefin_id` | `flow` (derived) / `user_flow` (override) resolved via `COALESCE(user_flow, flow)`, same Override + Learning pattern as social events and rides; `pair_id` links the two halves of a matched transfer/card-payment, set by `bank_flows.match_pairs` |
| `targets` | metric PK | per-metric direction + value |
| `app_settings` | key PK | Telegram toggle, `gmail_last_run` / `_status` / `_result`, calendar equivalents |

**Archive tables (v1, no new writes):** `life_log_entries`, `people`, `life_log_people`,
`activity_log`, `habits`, `habit_logs`, `categories`, `conversation_state`,
`accomplishments`, `weekly_focus`, `later_items`, and their caches. Kept for data, not
read or written by any v2 code path.

If you add a column, add a migration — do not rely on `CREATE TABLE IF NOT EXISTS` to
catch schema changes in production. The established pattern lives in `_init_v2_tables()`:
Postgres `ALTER TABLE … ADD COLUMN IF NOT EXISTS`, SQLite a `PRAGMA table_info` guard
before `ALTER TABLE`. A brand-new table needs no migration — `CREATE TABLE IF NOT EXISTS`
is enough. Tests only exercise the SQLite path; Postgres DDL is verified by deploying.

---

## Deployment (Railway)

- One Railway service, one deploy. `Procfile` runs `uvicorn main:app --host 0.0.0.0 --port $PORT`.
- `nixpacks.toml` builds the frontend during the build phase (`cd frontend && npm ci && npm run build`) so `app/api.py` can mount `frontend/dist/` as the SPA at `/`.
- Railway's Postgres plugin sets `DATABASE_URL`.

**Deploy checklist:**
- [ ] `ANTHROPIC_API_KEY` and `APP_PASSWORD` set in Railway
- [ ] PostgreSQL plugin attached
- [ ] Google OAuth vars set, refresh token carries both Calendar + Gmail scopes
- [ ] `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` set if using the weekly push
- [ ] `SESSION_TTL_DAYS` / `SESSION_MAX_DAYS` set if the defaults (14 / 60 days) aren't right for this deploy
- [ ] `BACKUP_S3_BUCKET` / `_ENDPOINT` / `_ACCESS_KEY` / `_SECRET_KEY` set if weekly backups should actually run (unset = silent no-op)
- [ ] `SIMPLEFIN_ACCESS_URL` set if bank sync should actually run — optional, unset means it no-ops cleanly

---

## Code Conventions

- **`config.py`** is the only place that reads `os.environ` — never hardcode secrets or read env vars elsewhere
- **`database.py`** is the only place with SQL — no DB calls from `app/`, `jobs/`, or `services/`
- **`ai_metrics.py`** is the only place with Claude calls — uses `claude-haiku-4-5-20251001` via `_call_json()`. **Never change `MODEL` without checking cost impact** — jobs run multiple times per day
- Ingestion jobs (`jobs/scan_gmail.py`, `jobs/scan_calendar.py`) must never crash the web app on failure — log and record status in `app_settings`, don't raise
- **The redaction boundary.** Ingestion jobs (and `jobs/weekly_push.py`) never store `str(exception)` in `app_settings` — that value is read back by `/api/settings` and rendered in Settings. `except Exception as e:` blocks call `logger.exception(...)` for full server-side detail, then `db.set_setting(..., safe_status(e))` (`services/safe_status.py`), which maps the exception to one of `"ok"` / `"error: auth"` / `"error: unreachable"` / `"error: rate limited"` / `"error: see logs"` — never anything else. This exists because the SimpleFIN bank-access URL (`services/simplefin_service.py`) carries its credentials inside the URL itself, and HTTP libraries routinely put the request URL into exception messages (a Gmail URL already leaked this way once, and it's the same failure mode SimpleFIN would hit without this boundary). Rule: **prevent the credential-bearing string from being constructed; never scrub it afterwards.** Any new ingestion job follows the same pattern. The pre-flight "we never even tried" statuses (`services.safe_status.NOT_CONFIGURED`, `GOOGLE_NOT_CONFIGURED`) are also `CLOSED_SET` members — written outside the try/except, before `safe_status()` ever runs, but from the same named constants so the invariant holds everywhere, not just inside the exception path.
- **HTTP client loggers are the third leak path, and the boundary above does not cover them.** `httpx` logs the full request URL at INFO — and since the SimpleFIN access URL *is* the credential, an INFO-level `httpx` logger writes the bearer token to the deploy logs on every sync. Nothing is raised and nothing is stored, so neither `safe_status()` nor `app_settings` ever sees it: the exception-based boundary is bypassed entirely rather than defeated. `main.py` pins `httpx` and `httpcore` to `WARNING` immediately after `logging.basicConfig`, and a test in `tests/test_simplefin_service.py` locks it. That test asserts an **explicit** level on those loggers, not the effective level — under pytest the root logger already has handlers, so `basicConfig` no-ops and an effective-level assertion passes while production still leaks. This happened for real on 2026-07-23, the first time the credential was set in Railway — the token reached the deploy logs and was knowingly left in place rather than rotated, so the logs from that date should be treated as sensitive. Never lower those levels, and treat any new logging config or HTTP client as subject to the same check.
- Google auth expiry surfaces as a visible banner in the app, never silent missing data
- **Money formats one way:** `$16.31`, whole dollars trimmed to `$20`, via `.toFixed(2).replace(/\.00$/, "")`. Always null-check, never truthiness — a real `$0` must display
- **Secondary surfaces fail quietly.** A failed fetch for insights/reflection/spend hides that section; it never sets the screen-level error state that would blank the page
- **Charts:** hand-rolled SVG, no chart library. Never set a fixed pixel height on a chart — use a wide viewBox with `width:100%; height:auto`, or the plot letterboxes into the middle of its container. The x-axis band lives inside the viewBox so labels can't clip
- **Chart colors are validated, not chosen.** `--chart-*` tokens were checked for colorblind separation and contrast against each theme's surface (dark needed its own steps — the UI tokens are too light for chart marks). Don't substitute values or add hues without re-validating, and keep legends/labels wherever a pair's separation is marginal

---

## Adding a New Feature — Checklist

1. **New metric?** Add to `METRICS` in `metrics.py`, a DB source table + query in `database.py`, wire counting into `app/scorecard.py`, add a target row via `db.seed_default_targets()`. Targets seed automatically at startup. Adding a key breaks tests that assert on `METRICS`-derived key sets — widen them, don't weaken them. Sensitive? Mark it `private: True`
2. **New AI task?** Add to `ai_metrics.py` only — keep the `_call_json()` pattern. If a user can disagree with the verdict, follow the Override + Learning Pattern above
3. **New DB table/column?** Add to `database.py` schema + a migration (see Database); test local SQLite and prod Postgres
4. **New env var?** Add to `config.py`, document above, add to `.env.example`
5. **New scheduled job?** Add to `jobs/`, wire into the `lifespan()` scheduler in `main.py`
6. **New passive signal from email?** Extend `receipts.py`'s rules and the three-way route in `jobs/scan_gmail.py`. Assume one real-world event produces several emails — decide the cluster key before writing the ingest, and never key on a subject line
7. **Anything with a dollar amount?** It belongs in the per-service spend breakdown, and work-excluded rides must stay excluded from every figure

---

## Working On This Repo

Each feature has a spec and a plan in `docs/superpowers/`, written before the code and
committed alongside it. They are the record of *why* — read the relevant pair before
changing a subsystem; several encode decisions (and rejected alternatives) that aren't
recoverable from the code.

Verify with the real suites — `pytest tests/ -v` and, in `frontend/`,
`npm test -- --run && npm run build` — before claiming anything works.
