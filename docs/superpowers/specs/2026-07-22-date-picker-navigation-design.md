# Tap-to-Pick Date Navigation

**Date:** 2026-07-22
**Status:** Approved

## Problem

Reaching a specific past day (Today screen) or week (Scorecard) requires repeated
‹ taps. The date/week label should be tappable and open a calendar picker.

## Decision

Native `<input type="date">`, not a custom calendar popup — zero dependencies,
OS-native UX on mobile, minimal code.

## Design

### Interaction

- In `DayNav` and `WeekNav`, the `.nav-label` block becomes `position: relative`
  and contains an invisible native date input (`opacity: 0`, absolutely
  positioned to cover the block, `cursor: pointer`). Tapping the label taps the
  input, so the OS calendar opens without `showPicker()` (works on iOS Safari,
  Android, desktop).
- The input sets `max={todayIso}` and `value` = the currently shown day
  (DayNav) or week start (WeekNav).
- onChange guard: ignore empty values and values `> max` (desktop allows typing
  past `max`).
- Accessibility: `aria-label="Pick a date"` (DayNav) / `"Jump to a week"`
  (WeekNav).
- Affordance cue: dotted underline on the label text (`text-decoration:
  underline dotted` on the `h2`), via a `.nav-label` CSS rule.

### Wiring

- Both components gain a required `onPick(iso: string)` prop.
- **Today screen:** `onPick` sets `selected` — `null` when the picked date
  equals `todayIso`, else the picked ISO date (same snap logic as `›`).
- **Scorecard:** `onPick` maps the picked date to its containing Monday via a
  new pure helper `mondayOf(iso: string): string` in `frontend/src/lib.ts`,
  then applies the existing `weekStart` logic (`null` when it equals
  `currentWeekStart`, else the Monday).

### Testing

- vitest for `mondayOf`: mid-week date, a Monday itself, a Sunday, and a
  year-boundary date.
- Picker interaction is native browser UI: verification is
  `npm test && npm run build` plus a manual tap check.

## Out of Scope

- Custom-styled calendar popup, hit/miss dots on calendar days, backend
  changes, range limits beyond `max=today`.
