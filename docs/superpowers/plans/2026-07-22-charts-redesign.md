# Trends & Patterns Chart Redesign

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development for the pure helpers. Frontend-only; controller merges.

**Goal:** Make the Scorecard's Trends and Patterns sections professional and genuinely readable — currently the SVGs letterbox into the middle of the container, there is no time axis, the target line is unlabeled, and empty weeks dominate the space.

## Diagnosis (verified, fix these root causes — do not merely restyle)

1. **Letterboxing.** `TrendChart` renders `viewBox="0 0 240 56"` with the default `preserveAspectRatio="xMidYMid meet"`. In a ~670px-wide container the plot is scaled to fit and centred, so bars and the target line occupy only the middle band with dead margins either side — visible in the user's screenshot. Fix by making the SVG's coordinate system match its rendered box: keep a fixed viewBox and set `preserveAspectRatio="none"` ONLY if no strokes/text live inside (they do, so instead) — **render with a wide viewBox (e.g. `0 0 360 96`) plus `width="100%" height="auto"` and `preserveAspectRatio="xMidYMid meet"`**, so it scales uniformly and fills the width without distortion. All text inside the SVG must therefore be sized in viewBox units, not px.
2. **No time axis.** Nothing tells the reader which week a bar is. Add first/last week labels.
3. **Unlabeled threshold.** The dashed line is the target but carries no value.
4. **Container height excludes the axis band** — size the container to plot + axis.

## Global Constraints

- Frontend only. No backend, no API, no new dependencies. Hand-rolled SVG.
- **Validated chart colors (do not substitute or eyeball).** Add chart-specific tokens; dark mode gets its own steps because the app's dark UI tokens sit outside the chart lightness band (verified with the dataviz validator):

```css
:root {
  --chart-hit: oklch(50% 0.185 277);   /* light — #4e4fc9 */
  --chart-over: oklch(56% 0.135 60);   /* light — #ac5d00 */
  --chart-zero: var(--line);
}
@media (prefers-color-scheme: dark) {
  :root {
    --chart-hit: oklch(63% 0.17 277);  /* dark — #727bed, validated */
    --chart-over: oklch(62% 0.14 60);  /* dark — #c26e12, validated */
  }
}
```

Both pairs PASS lightness band, chroma floor, CVD separation (ΔE ≥ 26), normal-vision floor, and ≥3:1 contrast in their own mode.
- Axes, gridlines and the target line are **solid hairlines**, never dashed (dashed reads as "projection"). Recessive: use `--line` / `--muted`.
- Marks: 2px gap between adjacent bars, 2px rounded top corners, no borders around marks.
- Text inside charts uses text tokens (`--muted`, `--ink-2`), never a series color.
- Frontend checks: `cd frontend && npm test -- --run && npm run build`. No commits with failing checks.

---

### Task 1: Pure chart helpers + tests

**Files:** `frontend/src/lib.ts`, `frontend/src/lib.test.ts`

- [ ] Tests first, then implement:
  - `niceMax(values: number[], target: number): number` — the y-axis top: `Math.max(...values, target, 1)` rounded up so the target line and tallest bar both sit comfortably (e.g. round up to the next integer, then add 1 when the max equals the target so the line isn't flush with the top edge). Tests: all-zero series with target 3 → ≥3; max 7 target 1 → ≥7.
  - `weekRangeLabel(weekStartIso: string): string` — `"Jul 13–19"`, crossing months as `"Jun 29–Jul 5"`, using existing `parseDay`/`MONTHS`. Tests for both.
- [ ] Commit: `feat(frontend): chart scale and week-range helpers`

### Task 2: Rebuild `TrendChart`

**File:** `frontend/src/components/TrendChart.tsx` (rewrite), `frontend/src/styles.css`

New props: `{ points: {count: number; hit: boolean; weekStart: string}[]; target: number; direction: string; unit?: string }`.

Layout inside a `0 0 360 96` viewBox:
- Plot band y=8..64. X-axis label band y=64..96 (this is the fix for the clipped-axis anti-pattern).
- Bars fill the full plot width: `bw = 356 / points.length`, each bar drawn at `x = 2 + i*bw + 1` with `width = bw - 2` (the 2px gap), `rx="2"`, anchored to the baseline y=64.
- **Zero weeks** render as a 2px stub in `--chart-zero` at the baseline, so "zero" reads as a real observation instead of blank space.
- Bar fill: `--chart-hit` when that week met the target, `--chart-over` when it missed. Position relative to the target line is the primary encoding; color reinforces it (so identity is never color-alone).
- **Target line:** solid 1px `--muted` line across the plot at the target value, with the value direct-labeled at the right end (e.g. `≤1` / `≥3`) in 9px `--muted`, right-aligned inside the viewBox.
- **Direct label** the most recent bar with its count in 10px `--ink-2` just above the bar (omit if the bar is at the top of the plot; then place it inside the bar end).
- **X axis:** the first week's `weekRangeLabel` at x=2 (start-anchored) and the last week's at x=358 (end-anchored), 9px `--muted`, in the axis band. No other ticks.
- Every bar gets `<title>` with `"{weekRangeLabel} · {count} {unit}"` for native hover.
- Interactive: each bar is focusable/tappable; tapping calls an optional `onSelect(index)` prop. Keep hit targets full-height (an invisible full-height rect per column, so you don't have to hit a 3px bar).
- `aria-label` on the SVG summarising the series.

CSS: `.trend { width: 100%; height: auto; display: block; }` — remove the fixed pixel height that currently fights the viewBox.

- [ ] Commit: `feat(frontend): readable trend charts with axis, target label, and zero states`

### Task 3: Trends section in `Scorecard.tsx`

- [ ] Each metric becomes a compact row: a header line with the metric label (left) and this week's `count / targetLabel` (right, `--ink-2`), then the chart.
- [ ] Tapping a bar sets that metric's selected index; render a caption line under that chart: `"Jul 13–19 · 4"` in 11px `--muted`. Tapping the same bar again clears it.
- [ ] A metric whose 12 weeks are entirely zero renders `"No data yet"` in `--muted` instead of an empty chart.
- [ ] Section heading stays `Trends · last 12 weeks`.
- [ ] **Table view (accessibility requirement):** below the Patterns section add a native `<details><summary>Show the numbers</summary>` containing one compact table — rows = weeks (most recent first), columns = the metrics, cells = counts. Use `tabular-nums`, hairline `--line` row rules, `--ink-2` text. Wrap it in a container with `overflow-x: auto` so it never makes the page scroll sideways.
- [ ] Commit: `feat(frontend): trends rows with captions, empty states, and a table view`

### Task 4: Rebuild the weekday heatmap

**Files:** `frontend/src/components/WeekdayHeatmap.tsx`, `frontend/src/styles.css`

- [ ] Remove the per-cell `border` (anti-pattern: borders around marks). Separation comes from the existing 6px grid gap.
- [ ] A zero cell renders as `--surface-2` with no tint — visibly "nothing", not an empty outlined box.
- [ ] Non-zero cells: `color-mix(in oklch, var(--chart-hit) <pct>%, var(--surface-2))` for floor metrics and `var(--chart-over)` for ceiling metrics, where `pct` scales the row's own max from 25% (min visible) to 100%, so a 1-of-3 cell is clearly lighter than 3-of-3 rather than nearly invisible.
- [ ] Append the row's max to the row label as a scale cue: `Delivery orders` + a muted `max 3` at the row end.
- [ ] Each cell keeps a `title` of `"{metric} · {weekday}s: {count}"` and is tappable, setting a caption line under the grid (same pattern as trends).
- [ ] A small scale legend under the grid: three swatches light→dark with `less` / `more` labels in `--muted`.
- [ ] Keep the M T W T F S S header row; ensure the grid never overflows on a 360px-wide screen (cells may shrink; the label column uses `minmax(72px, 1fr)`).
- [ ] Commit: `feat(frontend): clearer weekday heatmap with scale legend and zero states`

### Verification

- `cd frontend && npm test -- --run && npm run build` green.
- Check against the dataviz anti-patterns list: no letterboxing, no dashed grid, axis band included in the container, threshold labeled, no borders around marks, values reachable without hover (direct label + caption + table view), no color-only encoding.
- Confirm both themes by reading the tokens — every color used must be one of the tokens above.
