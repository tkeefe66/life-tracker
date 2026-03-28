# Weekly Updates Bot

A Telegram bot that prompts daily for work and personal accomplishments, tracks habits, manages a long-term goals ("Later") list, and syncs everything to Google Sheets with AI summarization.

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

## State Machine

The bot uses a single-row `conversation_state` table to track where the user is in a flow. States live in `bot.py`:

- `idle` — free-form messages go through AI routing (`_handle_freeform_message`)
- `confirming_freeform` — waiting for user to confirm or correct AI-parsed dictation
- `collecting_work` / `collecting_personal` / `collecting_focus` — guided daily update Q&A
- `collecting_work_only` / `collecting_personal_only` — quick single-category log
- `collecting_later_item` / `collecting_later_date` — later goal entry flow
- `confirming_habit` / `collecting_habit_check` / `collecting_habit_reason` — habit flows

`db.set_state(state, ...)` persists state; `db.get_state()` retrieves it. `temp_data` is a JSON dict for mid-flow scratch data.

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

Free-form text sent while idle is routed through AI — it parses, shows a preview, and asks for confirmation before saving.

## AI Model

All Claude calls use `claude-haiku-4-5-20251001` via `_call()` in `ai_summarize.py`. Four tasks:

1. **`summarize_focus_and_extract_later`** — cleans weekly focus entries and extracts any later items embedded in them; result cached in `focus_summary_cache`
2. **`parse_habit`** — parses natural language habit descriptions into structured `{name, description, days[]}`
3. **`organize_later_items`** — deduplicates and groups all later items by theme; result cached in `later_org_cache`
4. **`parse_freeform_message`** — classifies free-form dictation into work/personal/focus/later entries; raises `questions[]` for ambiguous items instead of guessing

## Google Sheets Sync

`_sync_to_sheets_with_ai()` in `bot.py` orchestrates the sync:
1. AI-summarize any uncached focus weeks
2. AI-organize later items (if item count changed)
3. Write all three tabs via `google_sheets.sync_to_sheets()`

Accomplishment text is written verbatim to the *Weekly Reviews* tab — it is not AI-processed before going to Sheets.

## Local Dev Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in secrets
python main.py         # runs in polling mode (no WEBHOOK_URL needed)
```

Required env vars: `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

Optional: `DATABASE_URL` (PostgreSQL), `WEBHOOK_URL`, `GOOGLE_SHEETS_ID`, `GSHEETS_CREDS` (base64 service account JSON), `GOOGLE_CALENDAR_*`

## Deployment (Railway)

- `Procfile` and `nixpacks.toml` configure the Railway build
- Set `WEBHOOK_URL` to the Railway app URL — bot switches to webhook mode automatically
- `DATABASE_URL` is set by Railway's Postgres plugin; bot uses PostgreSQL when present
- `GSHEETS_CREDS` holds the service account JSON as a base64-encoded env var
