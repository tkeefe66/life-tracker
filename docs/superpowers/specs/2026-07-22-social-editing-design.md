# Social Events: Manual Entry, Overrides, and Learning

**Date:** 2026-07-22
**Status:** Approved

## Problem

Social is the only metric with no manual control. Events come solely from the
calendar scan plus AI classification, and the result cannot be corrected: the
user cannot add a social event that was never on the calendar, rename one for
clarity, or overturn a wrong social/not-social verdict. Corrections also teach
the system nothing, so recurring events stay misjudged.

## Decisions

- Edits are **app-only** — nothing is written back to Google Calendar.
- Manual events capture **name, date, and optional cost**.
- Overrides are enough (no user-authored rule engine), but they **feed future
  AI classification as examples**.
- A user rename **always wins** over later calendar title changes.

## Design

### 1. Schema (`calendar_events` + migration)

Four new columns, added via a real migration (Postgres `ADD COLUMN IF NOT
EXISTS`; SQLite `PRAGMA table_info` guard), following the pattern established
by `delivery_orders.amount`:

| Column | Type | Meaning |
|---|---|---|
| `user_title` | TEXT | User rename; NULL = use calendar title |
| `user_is_social` | BOOLEAN | User verdict; NULL = use AI's `is_social` |
| `source` | TEXT | `'gcal'` (default) or `'manual'` |
| `amount` | REAL | Optional cost |

Manual events are ordinary rows with `source='manual'` and a synthetic
`gcal_event_id` of `manual:<uuid4>`, so all existing range queries, counting,
and insights work unchanged.

### 2. Resolution rules

- **Displayed title** = `COALESCE(user_title, title)`.
- **Counts as social** = `COALESCE(user_is_social, is_social)` — applied in
  `get_social_events_range` and `get_events_for_day` so the scorecard,
  insights, and Today list all agree.
- The calendar scan continues to overwrite `title` from Google; `user_title`
  is never touched by the scan. `event_needs_classification` still keys off
  `is_social IS NULL`, so a user override is never re-classified.
- Row dicts returned by the DB layer expose the resolved values as `title`
  and `is_social`, plus `source` and `amount`, so callers need no new logic.

### 3. API (`app/routes.py`)

- `POST /api/social` — body `{name, date, amount?}`. Validates ISO date and
  rejects future dates (reuse `_parse_date`); `amount` optional, `>= 0`.
  Creates a manual row spanning that day (`start_at` = `date`T12:00:00,
  `end_at` = `date`T13:00:00 local) with `is_social` true, `source='manual'`.
  Returns the created event.
- `PATCH /api/social/{event_id}` — body may contain `title`, `is_social`,
  `amount` (any subset). Writes `user_title` / `user_is_social` / `amount`.
  404 if the event does not exist.
- `DELETE /api/social/{event_id}` — manual events only; 400 for `gcal`
  events (detected events are turned off via `is_social: false`, not deleted).

### 4. Learning (`ai_metrics.py` + `jobs/scan_calendar.py`)

- New `db.get_classification_examples(limit=10)` — most recent rows where
  `user_is_social IS NOT NULL`, returning resolved title + the user's verdict.
- `ai_metrics.classify_social_event(..., examples=None)` includes a block:
  `The user has corrected past classifications:` followed by
  `- "<title>" IS social` / `IS NOT social` lines. Omitted entirely when
  there are no examples. Same `_call_json` pattern; `MODEL` unchanged.
- `jobs/scan_calendar.py` fetches the examples once per run and passes them
  to each classification call.

### 5. Social spend

`scorecard_for_week` adds `social_spend` — the week's summed `amount` across
social events (NULL treated as 0) — surfaced on the Scorecard's social row
exactly like `delivery_spend`.

### 6. Frontend (Today + Scorecard)

- **Today:** an "Add social event" affordance opens a small inline form (name,
  optional cost; date is the currently-viewed day). Each social event in
  "Noticed quietly" becomes tappable, opening an inline editor with: name
  field, "Counts as social" toggle, cost field, Save, and Delete (manual
  events only). Saving refreshes the day and week.
- **Scorecard:** social row shows `· $X spent` when `social_spend > 0`,
  matching the delivery-spend treatment.
- `GET /api/today` social event rows must carry `gcal_event_id`, `source`,
  `amount`, and the resolved title so the editor can populate.

## Testing

- pytest: migration idempotency; resolution rules (rename survives a scan
  upsert; `user_is_social` overrides the AI verdict in range queries);
  `POST/PATCH/DELETE /api/social` happy paths and guards (future date, bad
  amount, deleting a gcal event, 404s); manual event counts toward the
  scorecard; `social_spend` sums correctly; examples appear in the
  classification prompt and are omitted when empty.
- Frontend: build + existing vitest (no new pure logic).

## Out of Scope

- Writing changes back to Google Calendar, user-authored rules, per-person
  tracking, editing non-social calendar events, retroactive re-classification
  of past events.
