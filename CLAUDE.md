# Weekly Updates Bot — Claude Code Guide

A Telegram bot that builds a 30-year personal Life Log — a memoir substrate captured passively from Google Calendar and actively via the `/log` command, with people as first-class entities, habit tracking, and natural-language queries via `/ask`. Syncs to Google Sheets.

---

## Quick Commands

```bash
# Local dev
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py                  # polling mode — no WEBHOOK_URL needed

# Lint & type check
ruff check .                    # linting
mypy .                          # type checking (if configured)

# DB ops
python scripts/cleardb.py       # wipe DB (prompts CONFIRM)
python scripts/calendar_auth.py # re-auth Google Calendar OAuth2

# One-time Life Log backfill from existing memory spreadsheet
LIFE_LOG_IMPORT_SHEET_ID=<source_sheet_id> python -m scripts.import_life_log_spreadsheet

# Optional: specify a tab name
python -m scripts.import_life_log_spreadsheet --tab "Memory Log"

# Calendar history backfill — creates Life Log proposals you can confirm via Telegram
python -m scripts.import_calendar_history --start-year 2018  # all years from 2018-now
python -m scripts.import_calendar_history --year 2024        # one year
python -m scripts.import_calendar_history --start-year 2024 --dry-run  # preview

# Force a sync without Telegram
python -c "from bot import _sync_to_sheets_with_ai; import asyncio; asyncio.run(_sync_to_sheets_with_ai(None))"
```

**Never run `python main.py` with `WEBHOOK_URL` set locally** — it will try to register a webhook pointing at your Railway URL and break the production bot.

---

## Life Log Architecture

**Goal:** A 30-year memoir substrate — every meaningful moment captured, queryable, and owned.

**Two streams:**
- **`life_log_entries`** — curated memoir entries (confirmed by the user, written to Sheets)
- **`activity_log`** — raw calendar activity (used for proposal generation and insights)

**People as first-class entities:** Every entry can link to named people. Each person has a relationship type (friend, family, partner, colleague, other), status (active / ended / lost_touch), and can have aliases for fuzzy matching.

**Spec:** `docs/superpowers/specs/2026-05-02-life-log-design.md`

**Stories design spec:** `docs/superpowers/specs/2026-05-04-story-driven-proposals-design.md`
**Implementation plan:** `docs/superpowers/plans/2026-05-04-story-driven-proposals.md`

---

## Repo Layout

```
.
├── main.py                         # Entry point: DB init, health checks, webhook vs polling
├── bot.py                          # Telegram handlers + state machine + sheet sync orchestration
├── ai_life_log.py                  # All Life Log Claude API calls
├── ai_summarize.py                 # Archive: old weekly-summary Claude calls (no longer actively called)
├── database.py                     # DB layer: PostgreSQL (prod) / SQLite (local)
├── config.py                       # All env vars — secrets and feature flags
├── google_sheets.py                # Writes Life Log, People, Habits tabs
├── handlers/
│   ├── log_command.py              # /log command + lifelog_confirming state
│   ├── lifelog_proposals.py        # Per-event proposal reply parser (still wired)
│   ├── lifelog_queries.py          # /ask command
│   ├── people.py                   # /people command + merge flow
│   ├── buildstories.py             # /buildstories — cluster pending into stories
│   ├── syncstories.py              # /syncstories — apply Stories sheet decisions, start review
│   ├── story_review.py             # Telegram narrative state machine (multi-state)
│   ├── pushstories.py              # /pushstories — retry sheet write
│   ├── showstory.py                # /showstory <id> — debug dump
│   ├── dismissbirthdays.py         # /dismissbirthdays — bulk dismiss birthday proposals
│   ├── calendarbackfill.py         # /calendarbackfill — import calendar history
│   └── (legacy, deregistered)      # syncproposals.py, pushproposals.py, proposals_review.py, showproposal.py
├── jobs/
│   ├── lifelog_realtime.py         # Scheduled: every 15 min, high-confidence calendar proposals
│   ├── lifelog_dayafter.py         # Scheduled: 9am daily, matched-confidence yesterday events
│   ├── lifelog_sunday.py           # Scheduled: 5pm Sunday, weekly digest of "maybe" candidates
│   └── monthly_forward.py         # Scheduled: roll unfinished Later items forward (archive)
├── services/
│   ├── calendar_service.py         # Google Calendar OAuth2 integration
│   └── lifelog_query_service.py    # Natural-language query layer (tool-using LLM)
└── scripts/
    ├── calendar_auth.py            # One-off: obtain/refresh Calendar credentials
    ├── cleardb.py                  # One-off: wipe all DB data
    ├── import_life_log_spreadsheet.py  # One-off: backfill from existing memory spreadsheet
    └── import_calendar_history.py  # One-off: batch-import calendar history as proposals
```

---

## Architecture

| File | Role |
|------|------|
| `main.py` | Entry point — initializes DB, runs startup health checks, starts bot in webhook (production) or polling (local dev) mode |
| `bot.py` | All Telegram command handlers and the central state machine (`handle_message`) |
| `ai_life_log.py` | All Life Log Claude calls — propose from calendar, parse /log, recommend categories, backfill extraction |
| `ai_summarize.py` | Archive — old focus summarization and later-item organization calls; retained for reference, no longer actively called |
| `database.py` | DB layer — supports PostgreSQL (Railway prod) and SQLite (local dev); switches on `DATABASE_URL` |
| `config.py` | Env var loading — all secrets and feature flags come from here |
| `google_sheets.py` | Writes Life Log, People, and Habits tabs |
| `handlers/` | Life Log command and state handlers (log, proposals, queries, people) |
| `services/lifelog_query_service.py` | Natural-language query layer — tool-using Claude for `/ask` |
| `jobs/` | Scheduled background jobs: lifelog_realtime, lifelog_dayafter, lifelog_sunday, monthly_forward |
| `services/calendar_service.py` | Google Calendar OAuth2 integration |
| `scripts/` | One-off utility scripts |

---

## State Machine

The bot uses a single-row `conversation_state` table to track where the user is in a flow. All states live in `bot.py` and `handlers/`.

```
idle
 ├─► lifelog_confirming        (waiting for /log preview confirm/correct/cancel)
 ├─► lifelog_new_person        (asking relationship type for newly-detected person)
 ├─► story_resume_prompt       (asks "resume queue? yes/clear")
 ├─► story_confirming          (yes / edit summary / drop #N / skip)
 ├─► story_why_mattered        (one-sentence "why mattered" capture)
 ├─► story_extras_optin        (yes → Q&A loop / skip → confirm + advance)
 ├─► story_extras_qa           (one structured question at a time)
 ├─► confirming_habit          ┐
 ├─► collecting_habit_check    ├ habit flows
 └─► collecting_habit_reason   ┘
```

**Persistence:** `db.set_state(state, ...)` writes to DB; `db.get_state()` reads it.
**Scratch data:** `temp_data` is a JSON dict stored in the state row for mid-flow data.

**Rule:** Every state transition must explicitly call `db.set_state()`. Never assume state changed because a message was sent.

**Idle text:** Free-form text sent while idle returns a hint pointing to `/log` and `/ask`. Nothing is auto-parsed from idle input.

---

## Telegram Commands

| Command | What it does |
|---------|-------------|
| `/log [text]` | Add a Life Log entry — AI extracts category, people, location, date |
| `/ask [question]` | Natural-language query against your Life Log (e.g. "When did I last see Megan?") |
| `/buildstories` | Cluster pending calendar events into stories (parents + children) and push to the *Stories* tab |
| `/syncstories` | Apply Decision column from Stories tab; surviving stories enter a Telegram narrative review queue |
| `/pushstories` | Re-push pending stories to the Sheet (retry after a failed write) |
| `/showstory [id]` | Inspect a story (and its child events) by ID |
| `/people` | List people in your Life Log; `/people merge <id> into <id>` to dedupe |
| `/skip` | Skip the current prompt in any active flow |
| `/status` | This week's logged data |
| `/sync` | Push Life Log + People + Habits tabs to Google Sheets |
| `/summary` | Trigger the weekly summary now |
| `/habit [description]` | Add a habit via natural language |
| `/habits` | List active habits |
| `/habitstop [name]` | Deactivate a habit |
| `/cleardb` | Delete all data (requires `CONFIRM` reply) |
| `/start` | Show the command list |

**Calendar passive ingestion:** New events on your Google Calendar are watched continuously by `jobs/lifelog_realtime.py` (every 15 min, high-confidence proposals), `jobs/lifelog_dayafter.py` (9am daily, matched-confidence yesterday's events), and `jobs/lifelog_sunday.py` (5pm Sunday, weekly digest of "maybe" candidates). Each proposal arrives in Telegram with `Reply yes #N to confirm, skip #N to dismiss, edit #N <text> to revise`. Use `yes all` / `skip all` for batches.

---

## AI Layer (`ai_life_log.py`)

All Claude calls use **`claude-haiku-4-5-20251001`** via the `_call()` helper. Do not change the model without testing cost impact — this bot can run many calls per day.

| Function | Purpose | Cached? |
|----------|---------|---------|
| `propose_from_calendar_event` | Classify a calendar event for Life Log promotion (high/matched/maybe/skip) | ❌ |
| `parse_log_command` | Parse a /log message into a structured Life Log entry; detects relationship events | ❌ |
| `recommend_category_changes` | Suggest merges/drops/adds to the category list (periodic) | ❌ |
| `extract_entry_from_existing_text` | One-time spreadsheet backfill — extract structured data from messy text | ❌ |

Note: `ai_summarize.py` and its functions are retained for archive purposes but no longer actively called.

---

## Google Sheets Sync

`_sync_to_sheets_with_ai()` in `bot.py` orchestrates:

1. `sync_life_log_to_sheets` — full rebuild of **Life Log** and **People** tabs
2. `sync_habits_to_sheets` — full rebuild of **Habits** tab

Old tabs (*Weekly Reviews*, *Later*) are kept for archive but no longer written to.

Tabs: **Life Log** | **People** | **Habits**

---

## Environment Variables

### Required

| Var | Description |
|-----|-------------|
| `ANTHROPIC_API_KEY` | Claude API key |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your personal chat ID (bot is single-user) |

### Optional

| Var | Description |
|-----|-------------|
| `DATABASE_URL` | PostgreSQL URL (Railway sets this automatically); falls back to SQLite |
| `WEBHOOK_URL` | Railway app URL; presence switches bot from polling → webhook mode |
| `GOOGLE_SHEETS_ID` | Spreadsheet ID from the Sheets URL |
| `GSHEETS_CREDS` | Service account JSON, base64-encoded |
| `GOOGLE_CALENDAR_*` | OAuth2 tokens for Calendar integration |

---

## Database

`database.py` auto-selects engine:
- **`DATABASE_URL` set** → PostgreSQL (Railway prod)
- **No `DATABASE_URL`** → SQLite at `./local.db` (local dev)

Schema lives in `database.py`. If you add a column, add a migration — do not rely on `CREATE TABLE IF NOT EXISTS` to catch schema changes in production.

**Active tables:**
- `conversation_state` — single row, tracks current state + `temp_data` JSON
- `life_log_entries` — curated memoir entries
- `people` — person entities with relationship type and status
- `life_log_people` — many-to-many join between entries and people
- `activity_log` — raw calendar activity (source for proposals)
- `categories` — Life Log categories (seeded, user-editable)
- `habits` — habit definitions + active flag
- `habit_logs` — per-habit daily check-ins

**Archive tables (no new writes):** `accomplishments`, `weekly_focus`, `later_items`

---

## Deployment (Railway)

- `Procfile` and `nixpacks.toml` configure the build
- Set `WEBHOOK_URL` → Railway URL; bot auto-switches to webhook mode
- Railway's Postgres plugin sets `DATABASE_URL`
- `GSHEETS_CREDS` = service account JSON, base64-encoded (`base64 -i creds.json | tr -d '\n'`)

**Deploy checklist:**
- [ ] All required env vars set in Railway dashboard
- [ ] `WEBHOOK_URL` points to this app's Railway domain
- [ ] PostgreSQL plugin attached
- [ ] `GSHEETS_CREDS` is base64 of valid service account with Sheets editor access

---

## Code Conventions

- **One responsibility per file** — Life Log Claude calls go in `ai_life_log.py`; no Claude calls outside AI modules; no DB calls outside `database.py`
- **All config via `config.py`** — never hardcode secrets or read `os.environ` directly outside `config.py`
- **Async throughout** — all Telegram handlers are `async def`; all DB calls should be awaited or run in executor if using a sync driver
- **Fail loudly in dev, gracefully in prod** — use `config.py` feature flags to distinguish; never silently swallow exceptions in prod without logging
- **State transitions are explicit** — always call `db.set_state()` before sending a Telegram message that depends on the new state

---

## Common Failure Modes

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Bot responds but doesn't save | State not persisted before handler returns | Check `db.set_state()` call order |
| Sheets sync silently skips | `GOOGLE_SHEETS_ID` or `GSHEETS_CREDS` missing/malformed | Verify env vars; check base64 decode |
| Webhook not receiving messages | `WEBHOOK_URL` set incorrectly or Railway URL changed | Re-register webhook via Telegram API |
| SQLite in prod | `DATABASE_URL` env var missing in Railway | Attach Postgres plugin; redeploy |
| Proposals not arriving | Calendar OAuth expired or `_CALENDAR_JOBS_AVAILABLE` false | Run `scripts/calendar_auth.py`; check startup logs |
| `/ask` returns no results | No life_log_entries in DB yet | Confirm some proposals or use `/log` to add entries |

---

## Adding a New Feature — Checklist

1. **New command?** Add handler in `bot.py` or `handlers/`, register in `create_application()`, update `_COMMANDS_TEXT` in `bot.py`, send updated command list to user, and document here
2. **New AI task?** Add function to `ai_life_log.py` only — keep the `_call()` pattern
3. **New DB table/column?** Add to `database.py` schema + write a migration; test local SQLite and prod Postgres
4. **New env var?** Add to `config.py`, document in the table above, add to `.env.example`
5. **New scheduled job?** Add to `jobs/`, wire into `main.py`, document the schedule

> **Rule — keep `/start` current:** Whenever a command is added, removed, or its description changes, update `_COMMANDS_TEXT` in `bot.py` AND send the updated list to the user via Telegram (call `await _send(bot, _COMMANDS_TEXT)` or ask the user to run `/start`).
