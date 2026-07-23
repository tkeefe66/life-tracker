# Insights Tab + Spend Subtotals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development for all pure logic and backend work. Two phases; do Phase 1 completely (tests green, committed) before starting Phase 2.

**Goal:** Split the overloaded Week screen into Week (scorecard + spend) and a new Insights tab (Behavior | Money), and show per-service spend subtotals on Today and Week.

**Spec:** `docs/superpowers/specs/2026-07-23-insights-tab-and-spend-subtotals-design.md` — read it first; it is the authority on behavior.

## Global Constraints

- `database.py` only SQL; `config.py` only env; `ai_metrics.py` only Claude calls (untouched here).
- **Work rides are excluded from every spend figure.** Reuse the existing `_personal_rides()` helper in `app/scorecard.py` — do not re-derive the rule.
- Only `user_is_work = true` excludes a ride; an AI flag alone never does.
- **Chart colors are pre-validated. Do not substitute, tweak, or add hues.** Add these tokens; keep `--chart-hit` / `--chart-over` as they are:

```css
:root {
  --chart-delivery: oklch(50% 0.185 277);
  --chart-rides: oklch(56% 0.135 60);
  --chart-social: oklch(55% 0.11 175);
}
@media (prefers-color-scheme: dark) {
  :root {
    --chart-delivery: oklch(63% 0.17 277);
    --chart-rides: oklch(62% 0.14 60);
    --chart-social: oklch(64% 0.10 175);
  }
}
```

- The rides↔social pair sits at tritan ΔE 5.7, below the 8.0 target, so **the legend and the by-service table are required secondary encoding** — color must never be the only way to tell categories apart. Do not "simplify" either away.
- Money amounts render with the app's existing convention: `$16.31`, whole dollars trimmed (`$20`), via `.toFixed(2).replace(/\.00$/, "")`. Use null checks so a real `$0` shows.
- Backend `pytest tests/ -v` (baseline 164); frontend `cd frontend && npm test -- --run && npm run build` (baseline 26). No commits with failing checks.

---

## Phase 1 — Subtotals on Today and Week

### 1.1 Pure helpers (`frontend/src/lib.ts`, `lib.test.ts`)

- [ ] Tests first, then implement:
  - `serviceLabel(kind: string, service: string): string` — `("ride","Uber") → "Uber rides"`, `("ride","Lyft") → "Lyft rides"`, `("delivery","Uber Eats") → "Uber Eats"`, `("delivery","DoorDash") → "DoorDash"`, `("social", anything) → "Social"`.
  - `money(amount: number): string` — `16.31 → "$16.31"`, `20 → "$20"`, `0 → "$0"`.
  - `subtotalsFromDay(day: {deliveries, rides, social_events}): {kind, service, amount}[]` — sums by `(kind, service)` over a `/today` payload, skipping null amounts, **skipping rides whose `user_is_work` is true**, sorted by amount descending, empty array when nothing has an amount. Tests: mixed day; a work ride excluded; all-null amounts → `[]`.
- [ ] Commit: `feat(frontend): service label, money, and day-subtotal helpers`

### 1.2 `SpendSubtotals` component

- [ ] Create `frontend/src/components/SpendSubtotals.tsx`. Props: `{ rows: {kind, service, amount}[]; title?: string }`.
- [ ] Renders nothing (`null`) when `rows` is empty or every amount is 0.
- [ ] One line per row — `serviceLabel` left in `--ink-2`, `money` right in `--ink` with `tabular-nums` — then a `Total` line separated by a hairline `--line` rule, in `--ink`.
- [ ] Styles appended to `styles.css` using existing tokens only.
- [ ] Commit: `feat(frontend): spend subtotals component`

### 1.3 Backend — `spend_by_service` on the week card

- [ ] Tests first in `tests/test_scorecard.py` / `tests/test_api_routes.py`: seed a week with two delivery orders from different services, a personal ride, a confirmed-work ride, and a social event with a cost; assert `spend_by_service` contains one row per service, amounts correct, sorted descending, the work ride absent, and any zero/None amount rows absent.
- [ ] In `app/scorecard.py`'s `scorecard_for_week`, after the existing spend fields, build `card["spend_by_service"]` as a list of `{"kind","service","amount"}`:
  - delivery: group `orders` by `service`, summing `amount or 0`
  - ride: group `_personal_rides(...)` by `service`
  - social: a single `{"kind": "social", "service": "Social", "amount": social_spend}` row
  - drop rows whose summed amount is 0, round each to 2dp, sort by amount descending.
- [ ] Commit: `feat(api): per-service spend breakdown on the week card`

### 1.4 Wire into Today and Week

- [ ] `Today.tsx`: after the "Noticed quietly" list, render `<SpendSubtotals rows={subtotalsFromDay(data)} title="Spent today" />`. No API change — `deliveries`, `rides`, and `social_events` in the `/today` payload already carry `service`/`amount`; if `social_events` rows lack a `kind`, map them in the helper.
- [ ] `Scorecard.tsx`: render `<SpendSubtotals rows={card.spend_by_service} title="Spent this week" />` beneath the ledger; add `spend_by_service` to the `Card` interface.
- [ ] Verify both suites + build. Commit: `feat(frontend): spend subtotals on Today and Week`

---

## Phase 2 — Insights tab

### 2.1 Backend — `GET /api/spend`

- [ ] Tests first in `tests/test_api_routes.py`: seeded orders/rides/social across two weeks → `weeks` covers every week in the window oldest-first including zero weeks; `by_service` matches the aggregate; `items` newest-first and capped at 100; work rides excluded from all three; `weeks` param clamps 1–52.
- [ ] In `app/scorecard.py` add `spend(weeks: int) -> dict` returning `{"weeks": [...], "by_service": [...], "items": [...]}` per the spec §6. Reuse `metrics.week_bounds`, `_personal_rides`, and `_social_counts`. `items` entries: `{kind, service, label, at, amount}` where `label` is the order `subject`, the ride `subject`, or the social event `title`; sort by `at` descending and cap at 100.
- [ ] In `app/routes.py` add `GET /api/spend?weeks=12`, clamped `min(max(weeks,1),52)`.
- [ ] Commit: `feat(api): /spend endpoint for the money view`

### 2.2 `SpendChart` component

- [ ] Create `frontend/src/components/SpendChart.tsx` — a 12-week stacked bar chart. Follow `TrendChart.tsx`'s established conventions exactly: a wide viewBox (`0 0 360 96`) with `preserveAspectRatio="xMidYMid meet"`, `width:100%; height:auto` in CSS (never a fixed pixel height — that bug was just fixed in TrendChart, do not reintroduce it), a plot band above an x-axis band inside the same viewBox, solid hairline axis, first/last week labels.
- [ ] Stack order bottom→top: delivery, rides, social, using the three tokens. 2px gap between adjacent bars and a 2px surface gap between stacked segments. No borders on marks.
- [ ] `<title>` per bar with the week range and total; `onSelect(index)` prop for tap.
- [ ] Commit: `feat(frontend): stacked weekly spend chart`

### 2.3 `Insights.tsx` screen

- [ ] Create `frontend/src/screens/Insights.tsx` with a segmented control (`Behavior` / `Money`) in local state, defaulting to Behavior.
- [ ] **Behavior view:** move — do not re-implement — the reflection card, trend charts, weekday heatmap, noticings, and numbers table out of `Scorecard.tsx`, along with their `/insights` and `/reflection` fetches. `TrendChart` and `WeekdayHeatmap` are reused unchanged.
- [ ] **Money view:** fetch `/spend?weeks=12`. Render, in order: hero total for the window (large, proportional figures, NOT `tabular-nums`); `SpendChart`; a legend with a swatch + label per category (required); tap-caption showing the selected week's range and its three category totals; `<SpendSubtotals rows={by_service} />`; then the itemized list grouped by day (service, label, amount per row).
- [ ] Failed fetches degrade quietly (section hidden), matching the app's existing pattern — never an error state that blanks the screen.
- [ ] Commit: `feat(frontend): insights screen with behavior and money views`

### 2.4 Navigation + Week cleanup

- [ ] `App.tsx`: add `insights` to the `Tab` union and a `TAB_META` entry between `scorecard` and `settings`, label `Insights`, with a small hand-drawn SVG icon in the same style as the others (20×20 viewBox, `currentColor`, 1.5 stroke) — e.g. an upward trend line with a dot.
- [ ] `Scorecard.tsx`: delete the now-moved sections and any state, fetches, imports, and interfaces they used (`/insights`, `/reflection`, trend/heatmap/noticings/table code). What remains: week navigation, the ledger, and the spend subtotals. Remove any CSS that is now unreferenced ONLY if nothing else uses it.
- [ ] Verify: both suites + build green; no unused-import or dead-state warnings from `tsc`.
- [ ] Commit: `feat(frontend): insights tab in nav, week screen trimmed to scorecard and spend`

### Final verification

- Backend `pytest tests/ -v`, frontend `npm test -- --run && npm run build`.
- Re-read the spec's §7 and confirm every listed element exists on the right view.
- Confirm no fixed pixel height on either chart component and that all chart colors come from the tokens above.
