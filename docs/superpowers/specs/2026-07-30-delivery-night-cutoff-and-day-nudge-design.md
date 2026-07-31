# Delivery Night Cutoff + Manual Day Nudge

**Date:** 2026-07-30
**Status:** Approved in conversation

Prompted by a 12:49 AM Uber Eats order displaying on July 30's Day log: the
4 AM night cutoff (`metrics.effective_date`, spec
2026-07-29-ride-cancellation-and-effective-date-design.md) applied to rides
only, with deliveries deliberately deferred. The user has now made that
deferred decision: the cutoff extends to delivery orders, and both deliveries
and rides gain a manual ±1-day nudge for the cases where the automatic rule
is wrong.

## Decisions

1. **The night cutoff extends to delivery orders, and only them.** Calendar
   events keep their start-time bucketing (the user confirmed they land on
   the right days); check-ins and bank transactions are date-only, so the
   cutoff is a no-op there. Rides already have it.
2. **Resolution happens in SQL, exposed as a computed `day` field.**
   `get_delivery_orders_range` filters and orders by
   `COALESCE(user_date, _effective_date_expr(ordered_at))` and returns the
   result as `day`. `get_rides_range` does the same —
   `COALESCE(user_date, <effective date of the true ride time>)` — so
   `app/scorecard.py` stops re-deriving effective dates in Python
   (`metrics.effective_date(r["ride_time"])`) and every
   `o["ordered_at"][:10]` becomes `o["day"]`. One resolver, every caller
   agrees — same rule as the Override + Learning pattern's COALESCE
   convention, minus the learning (no AI verdict here, so no example
   feedback).
3. **History re-buckets.** The cutoff applies at query time with no cutover
   date. A past Monday 1 AM order moves to that Sunday, which can
   retroactively change a past week's delivery count, hit/miss, and streaks.
   Chosen explicitly: one rule everywhere beats a permanent special case,
   and the new number better reflects reality.
4. **New nullable `user_date` TEXT column on both `delivery_orders` and
   `rides`.** Standard migration pattern (Postgres `ADD COLUMN IF NOT
   EXISTS`, SQLite `PRAGMA table_info` guard). The scan never touches it —
   same footing as `user_is_work`.
5. **The nudge is bounded to the automatic day ± 1.** Valid stored values
   are exactly `{auto − 1, auto + 1}`; any other requested day is a 400.
   Repeated taps therefore cannot walk an item arbitrarily far from where
   the receipt says it happened. Moving an item back to its automatic day
   stores `NULL` (override cleared — self-healing if the cutoff rule ever
   changes). Future dates are rejected, consistent with check-ins.
6. **API:** `PATCH /api/deliveries/{id}` (new route) accepts `{day}`; the
   existing `PATCH /api/rides/{id}` gains an optional `day` alongside
   `is_work`. Both validate per decision 5 against the automatic day
   computed server-side.
7. **UI: tap expands an inline action strip** under the row (the pattern the
   social edit form established). Delivery rows become interactive for the
   first time. Ride rows change behavior: tap no longer instantly toggles
   work/personal — the toggle moves into the strip alongside the move
   actions, which also ends accidental work toggles. Only valid move
   targets render (given the ±1 bound relative to the automatic day). One
   expander open at a time. After a move the row disappears from the
   currently-viewed day on refresh — same mechanics as "didn't happen"
   removal. Move-target computation (valid days + labels) is a pure
   `lib.ts` helper with vitest coverage.
8. **Displayed time stays `ordered_at`.** Delivery receipts arrive at order
   time; there is no true-time parsing analogous to `ride_key`, and none is
   needed.

## Rejected alternatives

- **Arbitrary date picker for the nudge.** The cutoff is only ever wrong by
  one day; a picker is more UI for no real case. ±1 nudge chosen.
- **Forward-only cutoff (freeze old weeks).** Adds a cutover-date special
  case to every delivery query forever; rejected in favor of re-bucketing.
- **Keeping ride tap as instant work-toggle with a separate move chip.**
  Two tap targets crowd a small row; the expander is consistent with social
  rows and safer.
- **Extending the cutoff to calendar events.** Events bucket on start time,
  which the user confirmed is already right; a midnight-starting event is
  rare and the nudge pattern could cover it later if ever needed.

## Invariants untouched

- Ingest dedupe cluster key `(service, raw ordered_at day, subject)` —
  cutoff and override are query-time only; `jobs/scan_gmail.py` unchanged.
- `ride_at` immutability; `ordered_at` is likewise never rewritten.
- No AI involvement, no privacy-surface changes, no new env vars.

## Implementation

- `database.py`: `user_date` columns + migrations; `get_delivery_orders_range`
  and `get_rides_range` filter/order/expose the resolved `day`;
  `set_delivery_user_date` / `set_ride_user_date` helpers.
- `app/routes.py`: new `PATCH /deliveries/{id}`; extend `PATCH /rides/{id}`.
  Validation (±1 of automatic day, no future) lives with the route, using a
  pure helper in `metrics.py` for the automatic-day computation.
- `app/scorecard.py`: group by the returned `day` everywhere
  (`week_days`, `spend`, weekly counts, day view).
- `frontend/src/screens/Today.tsx`: expander state + action strip for
  delivery and ride rows; ride work toggle moves into the strip.
- `frontend/src/lib.ts`: pure move-target helper (valid days, labels).

## Testing

pytest (SQLite path): a 12:49 AM order buckets to the previous day; a Monday
1 AM order lands in the previous week's count and can flip hit/miss;
`user_date` wins over the cutoff; move-to-auto stores NULL; ±1 and
future-date validation on both routes; rides `day` with and without
override; scorecard regrouping uses `day`. Frontend: `lib.ts` helper tests
(`npm test -- --run`), `tsc --noEmit` + `vite build`, manual look.
