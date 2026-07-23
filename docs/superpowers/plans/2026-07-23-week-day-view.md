# Week Day-by-Day View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development for all backend and pure-logic work. Implement in order; one commit per section.

**Goal:** Rebuild the Week tab around seven day rows — see what happened on each day and what it cost — with a compact ledger above, tap-to-expand itemised charges, and the date opening that day on Today.

**Spec:** `docs/superpowers/specs/2026-07-23-week-day-view-design.md` — read it first; it is the authority on behavior.

## Global Constraints

- `database.py` only SQL; `config.py` only env; `ai_metrics.py` untouched.
- **Work rides** (`user_is_work = true`, never an AI flag alone) are excluded from day totals and the week total, but still appear in the expanded panel labeled `work`. Reuse `_personal_rides` where a personal-only list is needed.
- Social events use the same occurrence rule as the scorecard (`_social_counts`) so Week and the metric count never disagree.
- Money formats via the existing `money()` helper — never hand-rolled.
- No fixed pixel heights on anything chart-like; existing OKLCH tokens only.
- Backend `pytest tests/ -v` (baseline 172); frontend `cd frontend && npm test -- --run && npm run build` (baseline 37). No commits with failing checks.

---

### 1. Backend — `GET /api/week-days`

**Files:** `app/scorecard.py`, `app/routes.py`, `tests/test_api_routes.py`

- [ ] Tests first. Seed a week (relative to `_local_today()`, never hardcoded dates — three tests on this repo recently rotted that way) with: a gym check-in, an alcohol check-in with a level, two delivery orders on one day, a personal ride, a confirmed-work ride, and a social event with a cost. Assert:
  - exactly 7 day entries, Monday-first, dates contiguous, empty days present with `items: []` and `total: 0`
  - items land on the correct dates
  - the work ride IS in `items` with `is_work: true` but is excluded from that day's `total` and from `week_total`
  - an AI-flagged-but-unconfirmed ride IS counted
  - a malformed `week_start` → 400; a mid-week `week_start` resolves to its Monday
- [ ] Add `week_days(week_start: date) -> dict` to `app/scorecard.py` returning the spec §7 shape. Build it from ONE ranged query per source (`get_checkins_range`, `get_delivery_orders_range`, `get_rides_range`, `get_social_events_range`) bucketed by day in Python — never seven per-day round trips. Item labels: delivery → `subject`, ride → `subject`, social → `title`. Round totals to 2dp.
- [ ] Add the route with `_parse_date`-style validation, resolving any date to its Monday via `metrics.week_bounds`.
- [ ] Commit: `feat(api): week-days endpoint for the day-by-day view`

### 2. Frontend — chip summary helper

**Files:** `frontend/src/lib.ts`, `frontend/src/lib.test.ts`

- [ ] Tests first, then implement `dayChips(day)` → `{label: string, tone: "accent" | "over"}[]`:
  - `gym: true` → `Gym` (accent); `substances: true` → `Substances` (over); `alcohol_level: 2` → `Alcohol 2` (over)
  - delivery items → `N delivery` (over); personal ride items → `N ride`/`N rides` (accent); social items → `Social` (accent)
  - work rides do NOT produce a chip (they show only in the expanded panel)
  - a day with nothing → `[]`
- [ ] Commit: `feat(frontend): day chip summary helper`

### 3. `WeekDays` component

**File:** `frontend/src/components/WeekDays.tsx`, styles appended to `styles.css`

Props: `{ days: Day[]; weekTotal: number; onOpenDay: (iso: string) => void }`.

- [ ] One row per day inside a single card. Row anatomy: date button (left), expander button (rest of row: chips, cost, chevron).
- [ ] **Two real controls, not click handlers on divs:**
  - Date: a `<button>` showing `Mon` over `Jul 20`, with `text-decoration: underline dotted` on the weekday — the same affordance `DayNav`/`WeekNav` use for a tappable date — and an `aria-label` like `"Open Monday, July 20"`.
  - Expander: a `<button>` spanning the remainder with `aria-expanded` and `aria-controls` pointing at the panel id.
  - Both at least 44px tall.
- [ ] Chips from `dayChips`; `tone` picks the accent or over tint. Empty day → muted "Nothing logged" and an em-dash cost. Cost via `money()`; em-dash when 0.
- [ ] One day expanded at a time (component state). Panel lists each item — label left, `money(amount)` right, `tabular-nums` — with work rides suffixed `· work` in muted text and no amount contribution. Panel ends with a link-styled button `Open <Weekday>, <Mon D> →` calling `onOpenDay`.
- [ ] Week total row closes the card.
- [ ] Commit: `feat(frontend): week day rows with expandable itemised charges`

### 4. Compact ledger strip

**File:** `frontend/src/screens/Scorecard.tsx`

- [ ] Replace the current ledger (meters, `m-sub` text, streak line) with a five-tile strip: count (large, `tabular-nums`) over the metric's short label, in `ORDER`. A metric that missed renders its count in `--over`, a hit in `--ink`. Keep the streak value as a small suffix on the tile ONLY if it fits cleanly at this size; otherwise drop it (full detail lives in Insights).
- [ ] The `/history?weeks=12` fetch becomes unnecessary if streaks are dropped — remove it and the `History` interface too, in that case.
- [ ] Commit: `feat(frontend): compact ledger strip on week`

### 5. Cross-screen navigation

**Files:** `frontend/src/App.tsx`, `frontend/src/screens/Today.tsx`, `frontend/src/screens/Scorecard.tsx`

- [ ] `App.tsx`: add `pendingDay: string | null` beside `tab`. Pass `onOpenDay={(iso) => { setPendingDay(iso); setTab("today"); }}` into `Scorecard`, and `initialDate={pendingDay}` into `Today`.
- [ ] `Today.tsx`: accept optional `initialDate`. In an effect keyed on it, when non-null set the selected date to it. Call an `onConsumed()` prop (or clear via a callback) so `App` resets `pendingDay` to null — otherwise returning to Today later re-pins the old day. Verify the existing `selected === null means today` semantics still hold when `initialDate` equals today.
- [ ] `Scorecard.tsx`: fetch `/week-days?week_start=` alongside the card, render `<WeekDays … onOpenDay={props.onOpenDay} />` between the ledger strip and `SpendSubtotals`. A failed fetch hides the day card quietly.
- [ ] Commit: `feat(frontend): tap a day on week to open it on today`

### 6. In-progress week cue (small carried-over fix)

**File:** `frontend/src/components/SpendChart.tsx`

- [ ] The final bar in the Money view's chart is the current, still-accumulating week, but nothing marks it — it reads as a spending decline. Give it a visible in-progress treatment (e.g. a lighter fill or a hairline dashed top edge) and mention "in progress" in that bar's `<title>` and in the tap caption. Do not change the colors' hues or add new tokens.
- [ ] Commit: `fix(charts): mark the in-progress week in the spend chart`

### Final verification

- Both suites and the build green.
- Re-read the spec's §3–§6 and confirm each behavior exists.
- Confirm: no seven-round-trip queries; no hardcoded dates in new tests; both row controls are real buttons with 44px targets.
