---
name: chart-color-validation
description: Use when adding or changing any --chart-* token in frontend/src/styles.css, adding a series to a stacked or multi-hue chart, or picking a color for any new chart mark in this app.
---

# Chart Color Validation

## Overview

Chart colors in this app are **validated, not chosen** (repo guide rule).
Every hue passed a colorblind-separation + contrast check against BOTH
theme surfaces; a new hue that "looks fine" can be indistinguishable under
red-green CVD. Never eyeball it — run the validator.

**Baseline failure this skill exists to prevent (2026-08-02):** magenta
picked by eye for the dates series looked clearly distinct from teal but
failed deutan separation at ΔE 5.1 (floor is 8). Gold passed at 11+.

## Current validated palette

| Token | Light (surface `#ffffff`) | Dark (surface `#1c1e24` = oklch(23.5% 0.012 277)) |
|---|---|---|
| `--chart-delivery` (+ `--chart-hit`, `--chart-bank`) | oklch(50% 0.185 277) `#4e4fc9` | oklch(63% 0.17 277) `#727bed` |
| `--chart-rides` (+ `--chart-over`) | oklch(56% 0.135 60) `#ac5d00` | oklch(62% 0.14 60) `#c26e12` |
| `--chart-social` | oklch(55% 0.11 175) `#00866e` | oklch(64% 0.10 175) `#3aa089` |
| `--chart-dates` | oklch(48% 0.145 90) `#7d5600` | oklch(66% 0.12 90) `#af8e2a` |

Dark steps are their own validated values — never an automatic flip of light.

## Workflow

1. Convert candidates OKLCH → hex with `scripts/oklch2hex.js` (in this
   skill's directory). New tokens are named `--chart-<kind>`; aliases (like
   `--chart-hit` = `--chart-delivery`) exist only where an older token was
   retrofitted — a new series never needs one.
2. Run the dataviz skill's validator (`Skill: dataviz`, then
   `scripts/validate_palette.js` from its base directory) with the hues **in
   stack/adjacency order** — that order is the `CATS` array in
   `frontend/src/components/SpendChart.tsx` (bottom→top: delivery, rides,
   social, dates), NOT the styles.css token order — new candidate in its
   real position:

```bash
node scripts/validate_palette.js "#4e4fc9,#ac5d00,#00866e,#7d5600,#<candidate>" --mode light --surface "#ffffff"
node scripts/validate_palette.js "#727bed,#c26e12,#3aa089,#af8e2a,#<dark-candidate>" --mode dark --surface "#1c1e24"
```

3. Every check the validator reports must PASS in BOTH modes. No shipping
   on a FAIL; a contrast WARN obligates visible labels or a table view.
4. Record the validation in the styles.css comment next to the token, in
   the existing format, e.g.:
   `/* dates: dark's own validated step (CVD 11.0, normal 15.2, ≥3:1). */`
5. Build and LOOK at the chart in both themes (`npm run build` + the app,
   or dev server) — the validator checks color only, never layout, legend
   width, or label collisions.

Growing past ~5 series: stop adding hues — fold small series into "Other"
or facet (dataviz non-negotiable: a series never gets a generated hue).

## Hue-picking heuristics (save iterations)

- Under red-green CVD only **lightness** and **blue-yellow** survive.
  Against this palette's teal(175)/violet(277)/orange(60): yellow-gold (~90)
  separates; magenta/pink (~340) collapses into teal.
- Dark mode's lightness band is narrower (~0.48–0.67) — a light-mode pass
  says nothing about dark.
- Chroma floor is 0.1 post-conversion; low-chroma golds clip to exactly 0.1
  and fail — nudge C up, re-convert, re-run.

## Common mistakes

| Mistake | Fix |
|---|---|
| Validating only light mode | Both surfaces, both commands |
| Passing hues in token order, not stack order | Adjacency = what touches in the chart |
| Reusing `--accent` or a status color as a series | Series get their own validated `--chart-*` step |
| Editing an existing token "slightly" | Any change re-runs the full validation |
