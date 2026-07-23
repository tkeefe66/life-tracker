# Insights Tab + Per-Service Spend Subtotals

**Date:** 2026-07-23
**Status:** Approved

## Problem

The Week screen has become a dumping ground: current-week scorecard, 12-week
trend charts, weekday heatmap, noticings, a numbers table, and an AI
reflection all on one page. Trends belong on their own surface. Separately,
spend is now tracked across delivery, rides, and social, but the app only
shows category totals — the user wants to see Uber Eats spend distinct from
Uber ride spend, on both Today and Week.

A Spend tab was previously agreed but never started; it is folded into this
design rather than shipped as a fifth tab.

## Decisions

- **Four tabs:** Today / Week / Insights / Settings. Trends and Spend become
  two views of one Insights tab, not two tabs.
- **Subtotals are per service**, not per category: `Uber Eats`, `DoorDash`,
  `Uber rides`, `Lyft rides`, `Social`. Services with no spend in the period
  are omitted entirely.
- **Week becomes** week navigation + the metric ledger + spend subtotals.
  Everything else moves to Insights.

## Design

### 1. Navigation

`App.tsx`'s tab state gains `insights`; the bottom bar becomes Today / Week /
Insights / Settings. The Week tab label stays "Week".

### 2. Week screen (`Scorecard.tsx`)

Keeps: week navigation, the metric ledger (meters, streaks, hit/miss).
Gains: a spend subtotals block beneath the ledger.
Loses (moved to Insights): trend charts, weekday heatmap, noticings, the
"Show the numbers" table, and the AI reflection card.

### 3. Today screen

Gains the same spend subtotals block, scoped to the viewed day, placed after
"Noticed quietly". Computed client-side from the existing `/today` payload —
deliveries, rides, and social events already carry `service`/`amount`, so
**no backend change is needed for Today.**

### 4. Spend subtotals — shared component

`SpendSubtotals` renders one line per service: label left, amount right,
plus a total line. Label rules: delivery services print as-is (`Uber Eats`),
ride services get a suffix (`Uber` → `Uber rides`), social spend is a single
`Social` line. Nothing renders when the period has no spend at all.

**Work rides are excluded**, consistent with every other spend figure —
only `user_is_work = true` excludes, matching the rides tracker's rule.

### 5. Backend — week subtotals

`scorecard_for_week` gains `spend_by_service`: a list of
`{kind, service, amount}` (kind ∈ `delivery` | `ride` | `social`), amounts
rounded to 2dp, sorted by amount descending, zero/None amounts excluded.
The existing `delivery_spend` / `rides_spend` / `social_spend` fields stay
(the ledger and other callers use them).

### 6. Backend — new `GET /api/spend?weeks=12`

Clamped 1–52. Returns:

- `weeks`: `[{week_start, delivery, rides, social}]`, oldest-first, one entry
  per week in the window including zero weeks.
- `by_service`: same shape as `spend_by_service`, aggregated over the window.
- `items`: up to 100 individual charges, newest first, each
  `{kind, service, label, at, amount}` — `label` is the order subject, the
  ride subject, or the social event title.

### 7. Insights screen (new `Insights.tsx`)

A segmented control at the top switches two views; the choice is component
state (not persisted).

**Behavior view** — the AI reflection card, the 12-week trend charts, the
weekday heatmap, noticings, and the numbers table. These move over
unchanged; `TrendChart` and `WeekdayHeatmap` are reused as-is.

**Money view**:
- A hero total for the window (largest text on the page, proportional
  figures, no `tabular-nums`).
- A 12-week stacked bar chart of weekly spend, segmented by category
  (delivery / rides / social), 2px gaps between segments and between bars,
  x-axis labeled at first and last week, y-scale from the window max.
- **A legend is always present, and each category is direct-labeled in the
  by-service table below** — this is mandatory, not stylistic: the
  rides↔social pair validates at tritan ΔE 5.7, below the 8.0 target, so
  color alone must never carry identity.
- The by-service table for the window (same component as the subtotals).
- The itemized list from `items`, grouped by day, each row showing service,
  label, and amount.
- Tapping a bar shows a caption with that week's range and its three
  category totals.

### 8. Chart colors — validated, do not substitute

| Slot | Light | Dark |
|---|---|---|
| delivery | `oklch(50% 0.185 277)` `#4e4fc9` | `oklch(63% 0.17 277)` `#727bed` |
| rides | `oklch(56% 0.135 60)` `#ac5d00` | `oklch(62% 0.14 60)` `#c26e12` |
| social | `oklch(55% 0.11 175)` `#00866e` | `oklch(64% 0.10 175)` `#3aa089` |

Both triples pass all six checks (lightness band, chroma floor, all-pairs CVD
separation, normal-vision floor, contrast) against their own surface. Added
as `--chart-delivery` / `--chart-rides` / `--chart-social`; the existing
`--chart-hit` / `--chart-over` keep their current meaning for the behavior
charts.

## Testing

- pytest: `spend_by_service` shape, sorting, exclusion of work rides and
  zero amounts; `/api/spend` week series (including zero weeks), clamping,
  `items` cap and ordering.
- vitest: the service-label formatter (`Uber` → `Uber rides`, `Uber Eats`
  unchanged, social → `Social`) and the client-side day aggregation used by
  Today.
- Build + existing suites stay green.

## Out of Scope

- Monthly/annual ranges, budgets or spend targets, editing amounts from the
  Insights screen, exporting, per-merchant breakdown within a service,
  persisting the Behavior/Money choice across sessions.
