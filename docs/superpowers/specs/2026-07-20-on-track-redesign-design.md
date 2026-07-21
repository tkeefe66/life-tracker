# On Track — Goal-Alignment Web App (v2 Redesign)

**Date:** 2026-07-20
**Status:** Approved design
**Supersedes:** the Life Log / story-driven-proposals system (2026-05-02, 2026-05-04 specs)

## Why this redesign

v1 ("track the user's life") failed in practice: constant Telegram proposals, confirmation
loops, and story reviews made the user a data-entry clerk for their own life. The app went
unused.

v2 narrows the scope to one question, answered weekly: **is the user doing the things they
said they'd do?** The design principle is **passive by default, pull-based, minimal user
work** — two check-in buttons are the only manual input in the entire system.

## Goals

Two high-level goals, operationalized as four weekly metrics:

1. **Healthy lifestyle** — cook at home instead of ordering delivery; go to the gym;
   drink less.
2. **Consciously being social** — social events on the calendar. No per-person tracking,
   no new-vs-recurring distinction.

## Metrics

Week runs **Monday–Sunday** (ISO week). Each metric has a user-set weekly target; the
scorecard shows hit/miss per metric.

| Metric | Direction | Signal | Counting rule |
|---|---|---|---|
| Delivery orders | Ceiling (≤ N/week) | Gmail receipts (passive) | Receipts from food-delivery senders (Uber Eats, DoorDash, Grubhub, Seamless, etc.). Sender/subject rules first; Haiku classification for ambiguous messages. Deduped by Gmail message ID. Cooking at home is implied by a low count — no separate cooking signal. |
| Gym sessions | Floor (≥ N/week) | Manual one-tap check-in | One tap on the Today screen. One per day max. |
| Social events | Floor (≥ N/week) | Google Calendar (passive) | Daily job pulls events; Haiku classifies social / not-social. An event counts once it has occurred (end time passed) and was not declined. |
| Alcohol days | Ceiling (≤ N/week) | Manual check-in + level | One tap + severity dropdown: 1 = low, 2 = solid night, 3 = blackout. Metric = count of days with alcohol; levels are shown as a severity trend but are not scored. |

## Interaction model

- **Fully pull-based.** The web app is the entire experience. No proposal messages, no
  confirmation loops, nothing that requires a reply.
- **Telegram survives as a send-only channel.** One function, `notify(text)`. Its only
  designed use is an optional weekly scorecard push, toggleable in Settings (default off).
  No inbound handlers, no state machine.

## Web app UX

Mobile-first SPA (lives on the phone home screen). Three screens:

1. **Today** — two big buttons: "Gym ✓" and "Drank" (with level dropdown). Below them,
   today's passive detections (e.g., "1 delivery order detected", "Dinner w/ Sam →
   social") so the user can see the system working without having to act on anything.
2. **Scorecard** — current week vs. targets with hit/miss per metric; past weeks as trend
   charts; streaks.
3. **Settings** — edit the four targets, toggle the Telegram weekly push, Google
   connect/re-auth status.

**Auth:** single-user. One password from an env var (`APP_PASSWORD`) exchanged for a
session cookie. No accounts, no Google Sign-In.

## Architecture

- **Backend:** FastAPI. Replaces the Telegram polling/webhook entry point in `main.py`.
  Serves the JSON API and the built React app as static files. One Railway service, one
  deploy.
- **Frontend:** React + Vite, built to static assets at deploy time.
- **Scheduling:** APScheduler in-process (same pattern as v1 jobs):
  - Gmail receipt scan — every 4 hours
  - Calendar pull + social classification — daily
  - Weekly scorecard push (if enabled) — Monday morning, summarizing the completed week
- **AI:** all Claude calls in a new `ai_metrics.py`, keeping v1's `_call()` pattern and
  `claude-haiku-4-5-20251001`. Two tasks: classify ambiguous Gmail receipts, classify
  calendar events as social/not-social.
- **Google auth:** reuse the existing user-OAuth plumbing in
  `services/calendar_service.py`, extended with the `gmail.readonly` scope. Requires a
  one-time re-auth via `scripts/calendar_auth.py`.

### Reused from v1

`database.py` (Postgres/SQLite dual engine), `config.py` pattern, Google OAuth plumbing,
APScheduler job pattern, Railway deployment setup, `_call()` Haiku pattern.

### Retired (code deleted; DB data kept as archive)

`bot.py` state machine and all command handlers, `handlers/` (stories, people, proposals,
queries, habits flows), `jobs/lifelog_*` and `jobs/monthly_forward.py`, Google Sheets sync
(`google_sheets.py`), `ai_life_log.py`, `ai_summarize.py`. Archive tables
(`life_log_entries`, `people`, `activity_log`, `habits`, etc.) are never written again but
not dropped.

## Data model (new tables)

- `checkins` — id, date, type (`gym` | `alcohol`), level (1–3, alcohol only), created_at.
  Unique on (date, type).
- `delivery_orders` — id, gmail_message_id (unique), service, ordered_at, subject,
  detected_at
- `calendar_events` — id, gcal_event_id (unique), title, start_at, end_at, is_social,
  confidence, classified_at
- `targets` — metric, direction (`floor` | `ceiling`), value
- `app_settings` — key/value (Telegram push toggle, etc.)

Weekly scores are computed on the fly from these tables — no stored rollups to drift out
of sync.

## Error handling

- **Visible decay, never silent decay.** Google auth expiry surfaces as a banner in the
  app ("Calendar disconnected — reconnect"), not as quietly missing data. Silent decay is
  what killed trust in v1.
- Ingestion jobs log every run (start, counts, failures). A failing job never crashes the
  web app.
- API errors return actionable messages; the SPA surfaces them.

## Testing

pytest throughout:
- Metric computation: week boundaries, floor vs. ceiling scoring, streaks
- Receipt detection rules and Gmail message dedupe
- Calendar event counting rules (occurred, declined, all-day)
- API endpoints (check-ins, scorecard, targets, auth)
- AI classification with fixture prompts/responses (no live calls in tests)

## Out of scope (explicitly)

- Per-person / relationship tracking of any kind
- Memoir, life-log, or story features
- Google Sheets sync
- Interactive Telegram flows
- Multi-user support
