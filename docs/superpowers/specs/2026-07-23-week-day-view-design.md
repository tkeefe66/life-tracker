# Week Tab — Day-by-Day View

**Date:** 2026-07-23
**Status:** Approved

## Problem

The Week tab answers "did I hit my targets" but not "what did I actually do,
and when". A user looking at the week cannot see that they went to the gym on
Tuesday, had a social event Thursday, and spent $64 on Sunday. The 12-week
numbers table is the right *shape* — a table — but the wrong *grain*: it shows
weeks, not the days inside one week.

## Decisions

- **Layout:** one row per day, Mon–Sun, always all seven (empty days included).
- **Detail:** chips only in the row; itemised charges revealed on expand.
- **Ledger:** a compact five-tile strip above the days, replacing the current
  meters-and-streaks rows on this screen. Full hit/miss detail is available in
  Insights.
- **Two tap targets per row:** the date opens that day on Today; the rest of
  the row (chips, cost, chevron) expands it.
- **Sequencing:** implement only after the `insights-tab` branch merges — it
  rewrites `Scorecard.tsx`.

## Design

### 1. Screen structure (`Scorecard.tsx`)

Week navigation → compact ledger strip → day rows → week total →
`SpendSubtotals` (per-service, from the Insights branch). Nothing else.

### 2. Compact ledger strip

Five tiles in a row: count (large, `tabular-nums`) over the metric's short
label. A tile whose metric missed its target renders the count in
`--over`; hits use `--ink`. Substances is included like any other metric —
it is only excluded from the Telegram push and the AI reflection.

### 3. Day rows

Each row: weekday abbreviation + `Mon D` date on the left, chips in the
middle, the day's spend on the right, chevron at the end.

- **Chips** summarise, they do not enumerate: `Gym`, `Social`, `Alcohol 2`,
  `Substances`, `2 delivery`, `1 ride`. Ceiling-metric chips (delivery,
  alcohol, substances) use the `--over` tint; floor metrics and rides use the
  accent tint.
- A day with nothing logged and no charges shows a muted "Nothing logged" and
  an em-dash for cost.
- **Day cost** = the sum of that day's charge amounts, excluding
  confirmed-work rides. Em-dash when zero.
- A **week total** row closes the card.

### 4. Expansion

Tapping anywhere on the row except the date toggles that day open; only one
day is open at a time. The panel lists every charge — label and amount, one
per line, `tabular-nums` — followed by an "Open <Weekday>, <Mon D> →" link
that performs the same navigation as the date.

Confirmed-work rides appear in the panel labeled `work` and are **excluded
from both the day total and the week total**, consistent with every other
spend figure in the app. They are shown rather than hidden so a day with only
work travel doesn't look empty.

### 5. Interaction and accessibility

The date and the expander are two separate real controls, not two click
handlers on one element:

- Date: a `<button>` carrying the dotted-underline affordance already used
  for tappable dates in `DayNav`/`WeekNav`, so the signal is consistent.
- Expander: a `<button>` spanning the rest of the row with `aria-expanded`
  and `aria-controls` pointing at the panel.

Both clear a 44px minimum target height. Keyboard order within a row is
date → expander.

### 6. Cross-screen navigation (new capability)

Tapping a date must switch tabs *and* carry a date. Today currently owns its
selected date privately and `App.tsx` owns only the tab, so:

- `App.tsx` gains `pendingDay: string | null` alongside `tab`, and passes an
  `onOpenDay(iso)` callback down to the Week screen which sets both.
- `Today` accepts an optional `initialDate` prop; when it changes to a
  non-null value, Today selects that date. `App.tsx` clears `pendingDay`
  after it is consumed so returning to Today later doesn't re-pin an old day.

This is the app's first parameterised cross-screen navigation; keep it
minimal and explicit rather than introducing a router.

### 7. API — `GET /api/week-days?week_start=YYYY-MM-DD`

Validates the date (400 on malformed; any date resolves to its Monday via
`metrics.week_bounds`). Returns:

```json
{"week_start": "...", "week_end": "...", "week_total": 93.59,
 "days": [{"date": "2026-07-20", "gym": false, "alcohol_level": null,
           "substances": false, "total": 46.62,
           "items": [{"kind": "delivery", "service": "Uber Eats",
                      "label": "Oblio's Pizzeria", "at": "...",
                      "amount": 16.15, "is_work": false}]}]}
```

Always exactly seven day entries, Monday-first, including empty days. Built
from one ranged query per source (`get_checkins_range`,
`get_delivery_orders_range`, `get_rides_range`, `get_social_events_range`)
grouped by day in Python — never seven per-day round trips. Social events are
included using the same occurrence rule as the scorecard (`_social_counts`),
so the Week view and the metric count never disagree.

## Testing

- pytest: seven days always returned including empty ones; items grouped onto
  the right dates; day and week totals exclude confirmed-work rides but
  include AI-flagged-unconfirmed ones; work rides still present in `items`
  flagged `is_work`; malformed `week_start` → 400; a mid-week date resolves to
  its Monday.
- vitest: the chip-summary builder (counts → chip list, correct ceiling/floor
  tinting) as a pure helper in `lib.ts`.
- Build + existing suites stay green.

## Out of Scope

- Editing from the Week screen (the date link goes to Today, which already
  edits), drag/reorder, multi-day expansion, month view, a router.
