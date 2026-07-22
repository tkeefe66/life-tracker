# Frontend Redesign: Day Navigation + Insight-Driven Scorecard

**Date:** 2026-07-22
**Status:** Approved

## Problem

The Today screen only allows logging for the current day — no way to backfill
yesterday's gym/alcohol check-in. The Scorecard shows only the current week with no
navigation, despite the backend already supporting `GET /scorecard?week_start=` and
`GET /history?weeks=N`. The app reports hit/miss but surfaces no insight: no trends,
no streaks display, no patterns, no cross-metric observations.

## Goals

- Log check-ins for any past day.
- Navigate to any past week's scorecard.
- Surface insights: trends over time, day-of-week patterns, streaks/momentum,
  cross-metric observations, and an AI-written weekly reflection.

## Decisions Made

- Day navigation on the Today screen (not a calendar view or yesterday-only chip).
- Insight engine: computed rules as the always-on layer **plus** an AI weekly
  reflection paragraph.
- Keep 3 tabs (Today / Scorecard / Settings); Scorecard grows into the insights
  surface.
- Charts: hand-rolled SVG components styled with the existing OKLCH token system.
  **No new frontend dependencies.**

## Design

### 1. Day screen (evolves `frontend/src/Today.tsx`)

- Header gains `‹ [date label] ›`. Label reads "Today" / "Yesterday" / "Mon Jul 14"
  (weekday + month + day; include year only if not the current year).
- `›` is disabled when the selected day is today. `‹` navigates back without limit.
- All check-in mutations (`POST /api/checkins`, `DELETE /api/checkins/{type}`) send
  the selected `date` explicitly — including when the selected day is today.
- The "Noticed quietly" list and the mini week progress strip reflect the selected
  day and its containing week respectively.
- When a past day is selected, the header gets a subtle visual tint (existing token,
  e.g. `--accent-soft`) so past-day editing is unmistakable.
- Data source: `GET /api/today?date=YYYY-MM-DD` (param optional; omitted = today).

### 2. Scorecard screen (evolves `frontend/src/Scorecard.tsx`)

Top-to-bottom scrollable sections:

1. **Week header with `‹ ›`** — navigates any past week via
   `GET /api/scorecard?week_start=`. `›` disabled at the current week. Ledger rows
   unchanged, but each metric row gains its current streak label (e.g. "3 wks").
2. **Trends** — per metric, a 12-week SVG bar chart. Bars = weekly counts, colored
   hit/miss using existing tokens; the target value is drawn as a horizontal line.
3. **Patterns** — a metric × weekday grid covering the last 8 weeks. Cell intensity
   via `color-mix(in oklch, ...)` on the accent token. Ceiling metrics (delivery,
   alcohol) use the "over" token family so intensity reads as caution, not success.
4. **Noticings** — 0–3 short computed statements. Rule types: weekday skew
   ("delivery orders cluster on Sundays"), trend direction ("gym sessions trending
   up over 4 weeks"), co-occurrence ("alcohol days and delivery orders land on the
   same day more often than not"). Each rule has a minimum-data threshold (≥ 3 weeks
   of history and a meaningful effect size) so nothing fires on noise. Zero
   statements = section hidden.
5. **Reflection** — AI paragraph for the last completed week in a quiet card. If
   generation failed or no reflection exists yet, the card is hidden entirely —
   never an error state on this screen.

### 3. Backend changes

- `GET /api/today`: optional `date` query param (ISO `YYYY-MM-DD`; 400 on bad
  format or future date).
- `POST /api/checkins` and `DELETE /api/checkins/{type}`: reject future dates with
  400 (validation currently missing). Past dates allowed without limit.
- **New** `GET /api/insights?weeks=12` (clamped 1–52). Returns:
  - per-metric weekly count series (reusing history machinery),
  - per-metric weekday counts over the window,
  - streaks (existing `metrics.streaks`),
  - computed noticing statements.
  Pure math (weekday aggregation, trend slope, co-occurrence, statement rules)
  lives in `metrics.py`; DB wiring in `app/scorecard.py`, per existing layering.
- **New** `GET /api/reflection`: returns the cached reflection for the last
  completed week; on cache miss, generates via new `ai_metrics.weekly_reflection()`
  (haiku via the `_call_json()` pattern — model unchanged) and caches it.
- **New table** `weekly_reflections` (`week_start` unique, `text`, `created_at`)
  with a migration for existing Postgres deployments. One AI call per week maximum.

### 4. New frontend components (`frontend/src/components/`)

- `DayNav` — ‹ date › header for the Day screen.
- `WeekNav` — ‹ week label › header for the Scorecard.
- `TrendChart` — 12-week SVG bar chart with target line.
- `WeekdayHeatmap` — metric × weekday intensity grid.

All SVG/DOM styled exclusively with the existing OKLCH custom-property tokens. No
chart library, no new dependencies.

### 5. Error handling

- Insights with insufficient data: sections render an unobtrusive "not enough data
  yet" note (trends/patterns) or hide (noticings, reflection).
- Reflection generation failures: log server-side, return 204/empty to the client,
  card hidden.
- Ingestion-job conventions unchanged (never crash the app).

### 6. Testing

- **pytest:** future-date rejection on check-ins and `/today`; insights math in
  `metrics.py` (weekday counts, trend direction, co-occurrence, statement
  thresholds); reflection caching (second request makes no AI call).
- **vitest:** date-label formatting and day/week navigation helpers in `lib.ts`.

## Phasing

1. **Backfill + navigation** — Day screen arrows, dated check-in calls, server-side
   future-date validation, Scorecard week ‹ ›.
2. **Computed insights** — `/api/insights`, trends charts, weekday heatmap, streak
   labels, noticings.
3. **AI weekly reflection** — `ai_metrics.weekly_reflection()`, `weekly_reflections`
   table + migration, reflection card.

## Out of Scope

- New metrics, Telegram changes, auth changes, calendar-grid editing view, manual
  theme toggle, chart libraries.
