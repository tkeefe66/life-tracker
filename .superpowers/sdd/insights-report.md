# Insights Tab + Spend Subtotals — Implementation Report

Branch: `insights-tab` (worktree: `.claude/worktrees/agent-aed9b45553e450515`)
Spec: `docs/superpowers/specs/2026-07-23-insights-tab-and-spend-subtotals-design.md`
Plan: `docs/superpowers/plans/2026-07-23-insights-tab-and-spend-subtotals.md`

## Status: DONE

Both phases complete, all commits made, backend and frontend suites plus
build green from this worktree.

---

## Commits (oldest → newest)

1. `95afb29` feat(frontend): service label, money, and day-subtotal helpers
2. `db8c723` feat(frontend): spend subtotals component
3. `2c3e516` feat(api): per-service spend breakdown on the week card
4. `ce325cf` feat(frontend): spend subtotals on Today and Week
5. `2df7ea2` feat(api): /spend endpoint for the money view
6. `55c2df9` feat(frontend): stacked weekly spend chart
7. `ce29767` feat(frontend): insights screen with behavior and money views
8. `15259f3` feat(frontend): insights tab in nav, week screen trimmed to scorecard and spend

---

## Phase 1 — Subtotals on Today and Week

- **1.1** `frontend/src/lib.ts` / `lib.test.ts`: added `serviceLabel`, `money`,
  `subtotalsFromDay`. TDD: wrote 9 tests first, watched them fail with
  `TypeError: X is not a function` (RED), implemented, watched 35/35 pass
  (GREEN, 26 baseline + 9 new).
- **1.2** `frontend/src/components/SpendSubtotals.tsx` (new) — renders one
  row per service (`serviceLabel` left, `money` right) plus a `Total` line;
  returns `null` when `rows` is empty or every amount is 0. No component test
  framework exists in this repo (confirmed) — verified via `tsc --noEmit`.
  CSS added to `styles.css` (`.spend`, `.spend-row`, `.spend-total`, etc.),
  existing tokens only.
- **1.3** `app/scorecard.py` — `_spend_by_service()` groups `orders` by
  service, `_personal_rides(...)` by service, and a single social row;
  drops zero/None amounts, rounds to 2dp, sorts descending. Wired into
  `scorecard_for_week()` as `card["spend_by_service"]`. TDD: wrote
  `test_spend_by_service_shape_sorting_and_exclusions` in
  `tests/test_scorecard.py` first (KeyError RED), implemented, PASSED
  (GREEN). Full suite: 165/165 (164 baseline + 1 new).
- **1.4** Wired `SpendSubtotals` into `Today.tsx` (after "Noticed quietly",
  `rows={subtotalsFromDay(data)}`) and `Scorecard.tsx` (beneath the ledger,
  `rows={card.spend_by_service}`; `spend_by_service` added to the `Card`
  interface). Verified: 35 frontend tests, `tsc --noEmit`, `vite build` all
  clean.

## Phase 2 — Insights tab

- **2.1** `app/scorecard.py::spend(weeks)` + `GET /api/spend?weeks=12` in
  `app/routes.py`. Returns `{weeks, by_service, items}` per spec §6. TDD:
  wrote 5 tests first in `tests/test_api_routes.py` (all `KeyError` RED —
  route didn't exist), implemented, all 5 PASSED (GREEN). Full suite:
  170/170 (165 + 5 new).
  - **Design decision (window semantics):** the spec doesn't explicitly say
    whether the in-progress current week is included in the window. I
    mirrored `history()`'s existing "last N *completed* weeks" convention
    (already used by the Behavior view's trend charts via `/insights`),
    excluding the current week, for consistency between the two Insights
    views. Documented in a docstring on `spend()`.
  - `items` only includes entries with a non-zero/non-null amount (they're
    "charges"), capped at 100, newest-first.
- **2.2** `frontend/src/components/SpendChart.tsx` (new) — stacked weekly
  bar chart. Mirrors `TrendChart.tsx` exactly: `viewBox="0 0 360 96"`,
  `preserveAspectRatio="xMidYMid meet"`, CSS `width:100%; height:auto` —
  **no fixed pixel height anywhere**. Stack order bottom→top: delivery,
  rides, social, using `--chart-delivery` / `--chart-rides` /
  `--chart-social` (added verbatim from the plan's given block, both
  light and dark). 2px gaps between bars and between stacked segments.
  `<title>` per bar with week range + total; `onSelect(index)` prop.
- **2.3** `frontend/src/screens/Insights.tsx` (new) — segmented control
  (Behavior/Money, local state, defaults Behavior).
  - **Behavior view**: the reflection card, trend charts, weekday heatmap,
    noticings, and numbers table were moved (not rewritten) verbatim from
    `Scorecard.tsx`, along with their `/insights` and `/reflection` fetches.
    `TrendChart` and `WeekdayHeatmap` reused unchanged.
  - **Money view**: fetches `/spend?weeks=12`; renders hero total (large,
    proportional — no `.num`/`tabular-nums` class), `SpendChart`, a
    **mandatory legend** (swatch + label per category), a tap caption for
    the selected week's range + three category totals, `SpendSubtotals`
    (the by-service table — same component as the subtotals, satisfying
    the "direct-labeled" requirement), and an itemized list grouped by day.
  - All fetches degrade quietly (`.catch(() => setX(null))`, section
    hidden) — never blanks the tab.
- **2.4** `App.tsx`: added `insights` to the `Tab` union, a `TAB_META`
  entry between Week and Settings with a hand-drawn trend-line+dot icon
  (20×20, `currentColor`, 1.5 stroke, matching the existing icons).
  `Scorecard.tsx`: deleted all moved sections, state, fetches, imports,
  and interfaces (`Insights`, `Reflection` interfaces; `insights`,
  `reflection`, `selected` state; `TrendChart`/`WeekdayHeatmap` imports;
  `weekRangeLabel` import — no longer used there).
  - **Design decision (streaks):** the spec requires the Week ledger to
    keep showing streaks (`"Keeps: ... the metric ledger (meters,
    streaks, hit/miss)"`), but the plan says to remove the `/insights`
    fetch from `Scorecard.tsx` entirely. Streaks are only computed by
    `history()`/`insights()`. I resolved this by having `Scorecard.tsx`
    fetch the existing (previously frontend-unused) `GET /history?weeks=12`
    endpoint just for `streaks`, instead of the heavier `/insights` (which
    also computes noticings and weekday counts that Week no longer needs).
    This satisfies both documents: the ledger still shows streaks, and the
    `/insights` fetch is gone from Scorecard.tsx as instructed.

---

## Final Verification

```
$ ./venv/bin/python -m pytest tests/ -v      → 170 passed, 2 warnings (pre-existing, unrelated: urllib3/OpenSSL, google.api_core Python-version FutureWarning)
$ npm test -- --run                          → 35 passed (26 baseline + 9 new)
$ npx tsc --noEmit && npm run build           → clean, dist built (172.45 kB JS, 14.97 kB CSS)
```

Baselines were 164 backend / 26 frontend — net +6 backend tests, +9 frontend
tests, all from this feature.

Also ran a live smoke test (throwaway venv-hosted uvicorn on a scratch
SQLite DB, not committed) confirming `/api/scorecard` includes
`spend_by_service` and `/api/spend?weeks=N` returns the documented shape
against a real running server, not just the test client.

## Self-Review Against the Checklist

- [x] Both phases complete, all 8 commits made on `insights-tab`, nothing
      pushed.
- [x] Backend (170) + frontend (35) suites and `tsc --noEmit && vite build`
      all green from this worktree.
- [x] Week screen (`Scorecard.tsx`) contains only: `WeekNav`, the ledger
      (`.ledger`/`.metric` rows with streaks sourced from `/history`), and
      `SpendSubtotals`. No trend charts, heatmap, noticings, numbers table,
      or reflection card remain there.
- [x] Insights (`Insights.tsx`) contains everything that moved (reflection,
      trends, heatmap, noticings, numbers table) plus the new Money view,
      behind a Behavior/Money segmented control.
- [x] No fixed pixel height on `SpendChart` or `TrendChart` — both use
      `viewBox` + `preserveAspectRatio="xMidYMid meet"` + CSS
      `width:100%; height:auto`.
- [x] All chart colors come from `--chart-delivery` / `--chart-rides` /
      `--chart-social`, copied verbatim from the plan (light + dark), never
      substituted or tweaked.
- [x] Legend and by-service table both present in the Money view (neither
      dropped) — the required secondary encoding for the rides↔social pair.
- [x] Test output pristine — only pre-existing, unrelated warnings.
- [x] No dead state/fetches/imports/interfaces left in `Scorecard.tsx`
      (manually re-read the final file; confirmed no `tsc` errors, though
      note `noUnusedLocals`/`noUnusedParameters` are **not** enabled in
      `tsconfig.json`, so `tsc` alone would not have caught an unused
      import — I verified by reading, not just by the type-checker passing).

## Files Changed

- `frontend/src/lib.ts`, `frontend/src/lib.test.ts`
- `frontend/src/components/SpendSubtotals.tsx` (new)
- `frontend/src/components/SpendChart.tsx` (new)
- `frontend/src/screens/Insights.tsx` (new)
- `frontend/src/screens/Today.tsx`
- `frontend/src/screens/Scorecard.tsx`
- `frontend/src/App.tsx`
- `frontend/src/styles.css`
- `app/scorecard.py`
- `app/routes.py`
- `tests/test_scorecard.py`
- `tests/test_api_routes.py`

## Concerns / Notes for the Reviewer

1. **Window semantics for `/api/spend`** (documented above) — chose
   "last N completed weeks," excluding the in-progress current week, to
   match `history()`'s established convention. This is a reasonable
   default but wasn't explicit in the spec; flagging in case the intended
   behavior was to include the partial current week.
2. **Streaks sourcing** (documented above) — `Scorecard.tsx` now calls
   `/history?weeks=12` (previously unused by the frontend) instead of
   `/insights?weeks=12` to keep the ledger's streak display without
   pulling in noticings/weekday data it no longer needs. This is a genuine
   reconciliation between the spec (streaks stay on Week) and the plan
   (remove `/insights` fetch from Scorecard) rather than a literal
   instruction from either document — flagging for visibility, not
   because I believe it's wrong.
3. **No browser-based visual verification.** I attempted a live-server
   visual check via the Chrome automation tool but `localhost` is blocked
   by the extension's site permissions, which I have no ability (and no
   mandate) to grant myself. Fell back to the project's own established
   verification convention for visual/component work — `tsc --noEmit` +
   `vite build`, since this repo explicitly has no component test
   framework. I did run a live (non-browser) smoke test of the new
   backend endpoints via `curl` against a real running server.
4. **Ride amount rounding in `spend()`/`_spend_by_service()`**: individual
   `items` amounts and per-service aggregates are each rounded to 2dp
   independently (matching the existing `delivery_spend`/`rides_spend`
   pattern in `scorecard_for_week`), so in rare cases the sum of rounded
   per-week totals could differ from a rounded grand-total by a cent due
   to floating-point/rounding order. This mirrors pre-existing behavior
   elsewhere in `scorecard.py` and wasn't introduced by this feature.

---

## Review fix wave

A code review of the above surfaced 10 issues (1 critical, 3 important, 6
minor). All fixed on `insights-tab` in one follow-up commit.

### 1. CRITICAL — `/api/spend` excluded the current week

Concern #1 above turned out to be the wrong call. `app/scorecard.py::spend()`
mirrored `history()`'s "last N *completed* weeks" convention, but that
rationale (a partial week corrupts streaks) doesn't apply to money — it made
an order placed an hour ago invisible in Money while it was already visible
in Today's "Spent today" and Week's "Spent this week."

Fix: `week_starts` now runs `range(weeks - 1, -1, -1)` instead of
`range(weeks, 0, -1)`, so the window is the current in-progress week plus
the preceding `weeks - 1`, current week last. `window_start`/`window_end`,
`by_service`, and `items` all derive from the same `week_starts` list, so
all three stay in agreement. Docstring on `spend()` rewritten to explain why
money differs from `history()`. Checked `Insights.tsx`'s "last 12 weeks"
labels (hero subtitle, by-service title, chart aria-label) — still accurate
now that the window is literally the last 12 calendar weeks including the
in-progress one; no wording change needed.

### 2. IMPORTANT — three `/spend` tests were clock-dependent

`tests/test_api_routes.py`'s spend tests seeded absolute 2026-06/07 dates
against an unanchored `?weeks=4` query — passing only during the week these
were written. Reseeded every spend test relative to
`app.scorecard._local_today()` / `metrics.week_bounds(...)`, the pattern
`tests/test_scorecard.py` already uses. Verified they're genuinely
clock-independent by monkeypatching `_local_today()` to a date a month out
and re-deriving the same week math — the assertions are computed from the
same call the route uses, so they hold on any date.

### 3. IMPORTANT — no `/spend` test covered social spend

Added `test_spend_includes_social_spend_in_weeks_by_service_and_items`:
seeds a manual social event with an amount, asserts it lands in the
`weeks[].social` figure, the `("social", "Social")` `by_service` row, and
`items` with `label` mapped from the event's `title`.

### 4. IMPORTANT — no test pinned the current-week boundary

Added `test_spend_includes_the_in_progress_current_week`: seeds a delivery
order dated `_local_today()`, asserts it appears in `weeks[-1]` (with
`week_start` equal to this week's Monday), in `by_service`, and in `items`.

### 5. MINOR — social by-service amount rounded twice

`spend()` accumulated the already-rounded weekly `social_total` into
`by_service`, while delivery/rides accumulated raw amounts — a source of
off-by-a-cent drift between the Money hero total and the by-service Total.
Now accumulates `social_raw` (unrounded) into `by_service`, rounding once at
the end alongside delivery/rides.

### 6. MINOR — orphaned doc comment in `lib.ts`

`buildSocialPatch`'s JSDoc had ended up sitting above `serviceLabel` instead
of `buildSocialPatch`. Moved the comment back down to sit directly above
`buildSocialPatch`.

### 7. MINOR — bottom-of-file import in `lib.test.ts`

The `{ money, serviceLabel, subtotalsFromDay }` import this feature added
sat after all the `describe` blocks. Folded it into the top import
statement (`dayLabel, money, serviceLabel, subtotalsFromDay, targetLabel,
weekLabel`); left the file's other pre-existing scattered per-group imports
alone since they predate this branch and weren't part of the flagged issue.

### 8. MINOR — duplicate React key risk in the itemized list

`Insights.tsx`'s money-view itemized list keyed rows by
`` `${item.kind}:${item.service}:${item.at}` ``, which collides for two
receipts sharing a timestamp. Added the array index to the key.

### 9. MINOR — hand-rolled money formatting in `Scorecard.tsx`

Replaced the two inline `` `$${x.toFixed(2).replace(/\.00$/, "")}` `` call
sites (delivery/social spent-this-week captions) with the existing `money()`
helper from `lib.ts`.

### 10. MINOR — "Spent today" counted not-yet-occurred social events

`subtotalsFromDay` summed every social event in the `/today` payload
regardless of whether it had happened yet, so an evening event scheduled for
later today inflated "Spent today" before it occurred — disagreeing with
Week, which gates social spend on `_occurred(end_at)`. `SubtotalSocialEvent`
gained an optional `end_at`; `subtotalsFromDay` now takes an optional `now`
(epoch ms, defaults to `Date.now()`) and skips any social event whose
`end_at` is still in the future. It stays visible under "Noticed quietly" —
only the spend subtotal is affected. Missing `end_at` still counts (backward
compatible with the one pre-existing test that omits it). Added two vitest
cases: one social event past its `end_at` and one still upcoming in the same
day, and a case confirming the no-`end_at` default still counts.

### Verification

```
$ source venv/bin/activate && pytest tests/ -v
======================= 172 passed, 2 warnings in 2.67s ========================
(baseline 170 → +2: social-spend coverage, current-week-boundary test)

$ cd frontend && npm test -- --run
 Test Files  1 passed (1)
      Tests  37 passed (37)
(baseline 35 → +2: future-social-event exclusion, no-end_at backward-compat)

$ cd frontend && npm run build
✓ 43 modules transformed.
✓ built in 257ms
```

Also confirmed the two originally-failing spend tests (window-shift and
current-week-boundary) went RED against the pre-fix code before the
`spend()` change, then GREEN after — the TDD loop this task required.

### Files changed in this wave

- `app/scorecard.py` — `spend()` window fix, raw-vs-rounded social
  aggregation fix, docstring rewrite.
- `tests/test_api_routes.py` — all `/spend` tests reseeded clock-relative;
  added social-spend coverage and current-week-boundary test.
- `frontend/src/lib.ts` — moved `buildSocialPatch`'s doc comment;
  `subtotalsFromDay` gained the `end_at`/`now` future-event gate.
- `frontend/src/lib.test.ts` — folded the bottom import into the top block;
  added two `subtotalsFromDay` future-event tests.
- `frontend/src/screens/Insights.tsx` — itemized-list key now includes the
  index.
- `frontend/src/screens/Scorecard.tsx` — uses `money()` instead of inline
  formatting.
