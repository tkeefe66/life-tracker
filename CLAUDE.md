# Weekly Updates Bot — Claude Code Guide

A Telegram bot that prompts daily for work and personal accomplishments, tracks habits, manages a long-term goals ("Later") list, and syncs everything to Google Sheets with AI summarization.

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

# Force a sync without Telegram
python -c "from bot import _sync_to_sheets_with_ai; import asyncio; asyncio.run(_sync_to_sheets_with_ai())"
```

**Never run `python main.py` with `WEBHOOK_URL` set locally** — it will try to register a webhook pointing at your Railway URL and break the production bot.

---

## Repo Layout

```
.
├── main.py                  # Entry point: DB init, health checks, webhook vs polling
├── bot.py                   # All Telegram handlers + state machine + sheet sync orchestration
├── ai_summarize.py          # Every Claude API call lives here
├── database.py              # DB layer: PostgreSQL (prod) / SQLite (local)
├── config.py                # All env vars — secrets and feature flags
├── google_sheets.py         # Writes Weekly Reviews, Later, Habits tabs
├── jobs/
│   ├── daily_calendar.py    # Scheduled: import today's calendar events
│   ├── daily_ai_status.py   # Scheduled: proactive morning status message
│   └── monthly_forward.py   # Scheduled: roll unfinished Later items forward
├── services/
│   └── calendar_service.py  # Google Calendar OAuth2 integration
└── scripts/
    ├── calendar_auth.py     # One-off: obtain/refresh Calendar credentials
    └── cleardb.py           # One-off: wipe all DB data
```

---

## Architecture

| File | Role |
|------|------|
| `main.py` | Entry point — initializes DB, runs startup health checks, starts bot in webhook (production) or polling (local dev) mode |
| `bot.py` | All Telegram command handlers and the central state machine (`handle_message`) |
| `ai_summarize.py` | All Claude API calls — focus summarization, habit parsing, later item organization, free-form message parsing |
| `database.py` | DB layer — supports PostgreSQL (Railway prod) and SQLite (local dev); switches on `DATABASE_URL` |
| `config.py` | Env var loading — all secrets and feature flags come from here |
| `google_sheets.py` | Writes to three tabs: *Weekly Reviews*, *Later*, *Habits* |
| `jobs/` | Scheduled background jobs: `daily_calendar.py`, `daily_ai_status.py`, `monthly_forward.py` |
| `services/calendar_service.py` | Google Calendar OAuth2 integration |
| `scripts/` | One-off utility scripts (`calendar_auth.py`, `cleardb.py`) |

---

## State Machine

The bot uses a single-row `conversation_state` table to track where the user is in a flow. All states live in `bot.py`.

```
idle
 ├─► confirming_freeform         (AI-parsed free text, awaiting confirm/correct)
 ├─► collecting_work             ┐
 │    └─► collecting_personal    ├ guided /update flow
 │         └─► collecting_focus  ┘
 ├─► collecting_work_only        ┐ quick single-category
 ├─► collecting_personal_only    ┘ log flows
 ├─► collecting_later_item       ┐
 │    └─► collecting_later_date  ┘ later goal entry
 └─► confirming_habit            ┐
      ├─► collecting_habit_check  ├ habit flows
      └─► collecting_habit_reason ┘
```

**Persistence:** `db.set_state(state, ...)` writes to DB; `db.get_state()` reads it.  
**Scratch data:** `temp_data` is a JSON dict stored in the state row for mid-flow data.

**Rule:** Every state transition must explicitly call `db.set_state()`. Never assume state changed because a message was sent.

---

## Telegram Commands

| Command | What it does |
|---------|-------------|
| `/update` | Full daily check-in (work → personal → focus → habits) |
| `/work [text]` | Quick-log work; inline text saves immediately, bare command prompts |
| `/personal [text]` | Quick-log personal |
| `/focus [text]` | Quick-log next week's focus |
| `/log yesterday\|mm-dd-yy` | Log accomplishments for a specific past date |
| `/skip` | Skip the current prompt in any active flow |
| `/status` | This week's logged days |
| `/sync` | Push all data to Google Sheets now |
| `/summary` | Trigger the weekly summary immediately |
| `/habit [description]` | Add a habit via natural language |
| `/habits` | List active habits |
| `/habitstop [name]` | Deactivate a habit |
| `/later` | Add a longer-term goal |
| `/calendarsync [days]` | Bulk-import calendar events into Later items |
| `/cleardb` | Delete all data (requires `CONFIRM` reply) |

Free-form text sent while idle is routed through `_handle_freeform_message` — Claude parses it, shows a preview, and asks for confirmation before saving. **Nothing is auto-saved from free-form input.**

---

## AI Layer (`ai_summarize.py`)

All Claude calls use **`claude-haiku-4-5-20251001`** via the `_call()` helper. Do not change the model without testing cost impact — this bot can run many calls per day.

| Function | Purpose | Cached? |
|----------|---------|---------|
| `summarize_focus_and_extract_later` | Cleans weekly focus entries; extracts embedded later items | ✅ `focus_summary_cache` |
| `parse_habit` | Parses natural language → `{name, description, days[]}` | ❌ |
| `organize_later_items` | Deduplicates and groups later items by theme | ✅ `later_org_cache` (invalidated on count change) |
| `parse_freeform_message` | Classifies dictation into work/personal/focus/later; raises `questions[]` for ambiguous items | ❌ |

**Key contract:** `parse_freeform_message` must **never guess** on ambiguous input — it raises `questions[]` instead. Do not change this behavior.

---

## Google Sheets Sync

`_sync_to_sheets_with_ai()` in `bot.py` orchestrates in order:

1. AI-summarize any uncached focus weeks
2. AI-organize later items (only if item count changed since last org)
3. Write all three tabs via `google_sheets.sync_to_sheets()`

**Accomplishment text is written verbatim to *Weekly Reviews*** — it is intentionally not AI-processed before Sheets.

Tabs: **Weekly Reviews** | **Later** | **Habits**

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

**Important tables:**
- `conversation_state` — single row, tracks current state + `temp_data` JSON
- `accomplishments` — daily work/personal logs
- `habits` — habit definitions + active flag
- `habit_logs` — per-habit daily check-ins
- `later_items` — long-term goals

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

- **One responsibility per file** — don't add Claude calls outside `ai_summarize.py`; don't add DB calls outside `database.py`
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
| Duplicate later items after sync | `organize_later_items` cache stale | Invalidate `later_org_cache` manually or change item count |
| Webhook not receiving messages | `WEBHOOK_URL` set incorrectly or Railway URL changed | Re-register webhook via Telegram API |
| SQLite in prod | `DATABASE_URL` env var missing in Railway | Attach Postgres plugin; redeploy |
| AI parse returns empty `questions[]` | Prompt too short or freeform message lacks context | Check `ai_summarize.py` prompt; test with verbose input |

---

## Adding a New Feature — Checklist

1. **New command?** Add handler in `bot.py`, register in `create_application()`, update `_COMMANDS_TEXT` in `bot.py`, send updated command list to user, and document here
2. **New AI task?** Add function to `ai_summarize.py` only — keep the `_call()` pattern
3. **New DB table/column?** Add to `database.py` schema + write a migration; test local SQLite and prod Postgres
4. **New env var?** Add to `config.py`, document in the table above, add to `.env.example`
5. **New scheduled job?** Add to `jobs/`, wire into `main.py`, document the schedule

> **Rule — keep `/start` current:** Whenever a command is added, removed, or its description changes, update `_COMMANDS_TEXT` in `bot.py` AND send the updated list to the user via Telegram (call `await _send(bot, _COMMANDS_TEXT)` or ask the user to run `/start`).