# Ride Cancellation Detection + Effective-Date (Night Cutoff)

**Date:** 2026-07-29
**Status:** Approved in conversation

Two related fixes prompted by looking at real Jul 25 Uber emails: a canceled
trip's $5.65 fee sits in the rides ledger indistinguishable from a real ride,
and a 2:34 AM ride was displayed at its email-arrival time (not its actual
trip time) and attributed to the wrong calendar day/week because
`get_rides_range` bucketed on raw `ride_at`.

## Problems

1. **Cancellation fees look like ordinary rides.** Uber charges a fee when a
   trip is canceled after a grace period and sends a receipt with its own
   phrasing ("Here's the receipt for your canceled trip"). Nothing in the
   `rides` table distinguishes this $5.65 fee row from a completed $27.82
   trip — both show up as "{service} ride" with an amount.
2. **Late-night rides display and bucket wrong.** A ride actually taken at
   2:34 AM shows the Gmail message's arrival time (`ride_at`), which can lag
   the true trip time, and `get_rides_range` bucketed purely on
   `substr(ride_at, 1, 10)` — the literal calendar day of that arrival
   timestamp. A ride just after midnight is still "tonight" in any normal
   sense of a week/day boundary, but the raw-date bucketing would count it
   against the wrong day, and — worse — the wrong week when it falls on a
   Monday in the small hours.

## Decisions

1. **A shared "effective date" (night cutoff), applied to rides only.**
   `metrics.NIGHT_CUTOFF_HOUR = 4` and `metrics.effective_date(ts)`: any
   timestamp before 04:00 local belongs to the previous calendar day.
   `database._effective_date_expr()` is the SQL twin, built from the same
   constant, used to both filter (`WHERE`) and group (`ORDER BY`/per-week
   regrouping in `app/scorecard.py`) rides queries. Net effect: a ride at
   2026-07-25T02:34 belongs to 2026-07-24; a Monday 01:00 ride belongs to
   Sunday — the previous week.
2. **Bucket on the TRUE ride time, not the email-arrival time, when it's
   known.** `ride_key` already holds the parsed trip time
   (`receipts.extract_ride_time`) in ISO shape when parsing succeeded; it
   falls back to a non-ISO `"{day}|{subject}"` shape when it didn't (see
   `jobs/scan_gmail.py`). `database._ride_true_ts_expr()` resolves to
   `ride_key` only when it matches the ISO shape, else falls back to
   `ride_at`. The resolved value is exposed as a new `ride_time` field from
   `get_rides_range` — `ride_at` itself stays untouched and immutable
   (unchanged invariant: rewriting it would let a later email silently
   re-bucket an already-settled ride).
3. **Cancellation detection is a label, not an exclusion.**
   `receipts.is_cancellation_fee(snippet)` keys on Uber's "canceled trip" /
   "cancelled trip" phrasing (spelling varies) and is checked against real
   specimen snippets (cancellation, completed-trip charge summary, and
   thanks-for-riding follow-up — the latter two must not match). A
   cancellation fee still counts as a ride and still counts toward spend —
   nothing in `app/scorecard.py`'s aggregates changes. It only relabels the
   Today screen's "Noticed quietly" row from "{service} ride" to
   "{service} cancellation fee".
4. **New nullable `is_cancellation` column on `rides`,** following the
   established migration pattern (Postgres `ADD COLUMN IF NOT EXISTS`,
   SQLite `PRAGMA table_info` guard). NULL means "not yet determined" — it's
   the backfill signal, not a third boolean state with its own meaning.
5. **Self-healing backfill on the existing `has_ride` dead end.** The scan
   used to `continue` immediately once a ride's `gmail_message_id` was
   already stored, learning nothing more from a repeat sighting. It now
   fetches the existing row (`db.get_ride_by_message_id`) and, only if
   `is_cancellation` is still NULL, derives it from the snippet and sets it —
   which self-heals the two Jul 25 rows already in the database the next
   time the 7-day lookback window scans them. A row whose flag is already
   set (even `False`) is left alone; the backfill never re-evaluates it.

## Rejected alternatives

- **Excluding cancellation fees from counts/spend.** Rejected — the user
  chose label-only. A cancellation fee is still a real charge; hiding it from
  spend would understate what was actually paid, and hiding it from the
  rides count would understate ride-tracking accuracy for a signal whose
  only job is to reflect reality.
- **Hiding cancellation-fee rows entirely from the UI.** Rejected — the
  dollars would vanish from every spend view even though the money was
  actually spent. Label-only keeps the row visible with an honest
  explanation instead of a mystery gap in the total.
- **Per-ride manual attribution toggle, or a cutoff-agnostic badge only.**
  Considered flagging a late-night ride and letting the user manually pick
  which day/week it belongs to (mirroring the ride work/personal override).
  Rejected: the user chose an automatic cutoff — a fixed, predictable rule
  needs no per-ride interaction, and a "badge only, no re-bucketing" version
  would still leave the wrong day/week getting credit for a ride that
  obviously belongs to the night before.
- **Configurable cutoff hour.** YAGNI — `NIGHT_CUTOFF_HOUR` is a plain
  module constant, not a setting. Nothing in the current design calls for
  per-user tuning, and a config knob here would be speculative complexity for
  a rule everyone experiences the same way (rides after midnight but before
  a reasonable "day starts" hour belong to the night before).
- **`date(ride_at, '-4 hours')` (SQLite) exactly as originally specified.**
  Deviation, not a rejected alternative in the design-conversation sense, but
  worth recording: this literal expression is provably wrong. `ride_at`
  strings carry a trailing local UTC offset (e.g. `-06:00`), and SQLite's
  `date()`/`datetime()` functions silently normalize an offset-bearing string
  to UTC before applying the `-4 hours` modifier — verified empirically:
  `date('2026-07-15T02:34:00-06:00', '-4 hours')` returns `'2026-07-15'`, not
  `'2026-07-14'`, because the conversion-through-UTC step defeats the cutoff
  exactly when it matters (the `ride_at` fallback path). `database.py`
  instead computes the cutoff from the literal wall-clock date/hour
  substrings on both dialects (SQLite and Postgres), so they can never drift
  apart and neither ever silently reinterprets an offset it should ignore.
  Postgres's `::timestamp` cast (as originally specified) does correctly
  discard the offset rather than converting through it, but the substring
  approach was used on both sides anyway for symmetry and to avoid relying on
  that cast's easy-to-misremember behavior.

## Deferred decisions

- **Applying effective-date to delivery orders** is a future, separate
  decision, deliberately out of scope here. Delivery is a SCORED metric
  (`metrics.METRICS["delivery"]`), and moving its week boundary changes
  hit/miss outcomes retroactively — a much higher-stakes change than rides,
  which carry no target. Should be its own spec if pursued.
  *Resolved 2026-07-30 — see
  2026-07-30-delivery-night-cutoff-and-day-nudge-design.md.*
- **Bank transactions** are date-only (no time component) in this schema, so
  the night-cutoff expression is a no-op there for now — nothing to change,
  but also nothing gained by wiring it in until bank data carries a
  timestamp.

## Implementation

- `metrics.py`: `NIGHT_CUTOFF_HOUR`, `effective_date(ts)` — pure, no I/O.
- `database.py`: `_ride_true_ts_expr()`, `_effective_date_expr()` (dialect-aware,
  built from `NIGHT_CUTOFF_HOUR`); `get_rides_range` filters/orders by the
  resolved effective date and returns a new `ride_time` field; `add_ride`
  gains an optional `is_cancellation` parameter; new `get_ride_by_message_id`
  / `set_ride_cancellation` helpers; migration for the `is_cancellation`
  column.
- `receipts.py`: `is_cancellation_fee(snippet)`.
- `jobs/scan_gmail.py`: sets `is_cancellation` at insert time; backfills a
  NULL flag on an already-seen ride instead of a bare `continue`.
- `app/scorecard.py`: `week_days` and `spend` regroup rides by
  `metrics.effective_date(r["ride_time"])` instead of raw `ride_at[:10]`, and
  display `ride_time` instead of `ride_at` in spend items; `scorecard_for_week`
  needed no change (its window filtering already happens inside
  `get_rides_range`).
- `frontend/src/screens/Today.tsx`: `Ride` gains `ride_time` and
  `is_cancellation`; the "Noticed quietly" ride row displays `ride_time` and
  reads "{service} cancellation fee" when flagged, "{service} ride"
  otherwise.

## Testing

pytest (SQLite path): `effective_date` boundary cases (before/at/after 04:00,
midnight, Monday-early-morning-into-Sunday, trailing-offset ignored);
`is_cancellation_fee` against the three real specimen snippets plus a
spelling-variant case; `get_rides_range` buckets a 02:34 ride into the
previous day and a Monday 01:00 ride into the previous week, and exposes the
resolved `ride_time` (both the `ride_key`-wins and `ride_at`-fallback paths);
`scan_gmail` sets the flag at insert time, does not flag a completed trip,
backfills a NULL flag on a repeat sighting without re-running work
classification, and never re-flags an already-set row; `app/scorecard.py`
regroups a late-night ride into the correct day/week in both `week_days` and
`spend`. Frontend: no new pure logic in `lib.ts` (the only change is which
fields a Today row reads); verified by `npm test -- --run`, `tsc --noEmit`
+ `vite build`, manual look.
