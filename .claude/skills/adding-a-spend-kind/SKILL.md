---
name: adding-a-spend-kind
description: Use when adding a new item kind to the day log or spend surfaces (a new source of rows with dollar amounts — new tracked series, future bank kinds), when extending METRICS-adjacent payloads, or when a spend figure disagrees between the Today, Week, and Money screens.
---

# Adding a Spend Kind

## Overview

Every item kind (`delivery`, `ride`, `social`, `date`) flows through a fixed
set of consumers that each enumerate kinds BY NAME. Adding a kind to the
backend without touching every consumer compiles clean, passes the suites,
and ships wrong numbers — the frontend gaps are invisible to tests because
they live in components verified only by `tsc` + build.

**Baseline failure this skill exists to prevent (2026-08-02, dates kind):**
four shipped bugs — date spend mislabeled "Social" on Today, date-only days
reading "Work travel" on Week, the tracked chart/legend/caption omitting the
kind its own totals list included, stale share-sentence copy.

## Scope

This is the SURFACING half only: making an already-queryable kind correct on
every screen and figure. Ingestion (a new table + migration, `receipts.py`
rules, `jobs/scan_gmail.py` routing) is CLAUDE.md's "Adding a New Feature"
checklist, items 1/3/6 — do that first.

**Two shapes of kind — decide before starting:**
- **Piggyback** (like `date`): a flag on an existing source's rows
  (`calendar_events.is_date`). Rows already reach the day screen; you touch
  resolution + every consumer below.
- **Own source** (like `delivery`/`ride`): its own table and payload array.
  Everything below PLUS `today_snapshot` and a new `Today.tsx` entries block.

## The Checklist — every row, every time

### Backend (`app/scorecard.py` unless noted)

| Consumer | What to add |
|---|---|
| `database.py` range query | New/updated query; resolve day + overrides IN SQL, expose `day` |
| `_spend_by_service` | New `(kind, Service)` accumulation + parameter |
| `scorecard_for_week` | `<kind>_count` / `<kind>_spend` card keys + pass to `_spend_by_service` |
| `week_days` | Fetch + per-day items loop (`kind`, `service`, `label`, `at`, `amount`, `is_work`) |
| `spend()` | Window fetch, per-week split, weekly-row key, `by_service`, itemized entries |
| `today_snapshot` | Own-source kinds only: add the array to the `/today` payload — miss it and the kind never appears on the Day screen at all |
| Privacy | Not in `METRICS` ⇒ Telegram/reflection excluded by construction — extend the content locks in `test_weekly_push.py` / `test_api_routes.py` |

**Spelling trap:** `spend()` weekly rows and `SpendWeekPoint` use plural keys
(`"rides"`, `"dates"`); `DayItem.kind` / `_spend_by_service` / items use
singular (`"ride"`, `"date"`). The new kind needs both spellings, in the
right places.

### Frontend

| Consumer | The trap if missed |
|---|---|
| `lib.ts` `DayItem.kind` union | Type lies; downstream narrowing hides the kind |
| `lib.ts` `subtotalsFromDay` | "Spent today" lumps the kind under another row — disagrees with Week/Money |
| `lib.ts` `dayChips` | Items-but-no-chips renders as **"Work travel"** in `WeekDays.tsx` — the only other no-chip case |
| `lib.ts` `serviceLabel` | Wrong row label in `SpendSubtotals` |
| `lib.ts` `categoryForKind` | Falls back to `money` icon silently. The six-category set is CLOSED by design (see the comment above the function + the day-log spec) — mapping to an existing category or accepting the `money` fallback are the options; a seventh category is a deliberate design event, not a checklist item |
| `Today.tsx` | Own-source kinds only: `TodayData` gains the array, and a new `<kind>Entries: LogEntry[]` block (pattern: `deliveryEntries`) must be hand-built and folded into `dayLogEntries` — the Day log does NOT consume a generic items list |
| `lib.ts` `trackedShareSentence` | Copy enumerates kinds — number includes it, sentence doesn't |
| `SpendChart.tsx` | `SpendWeekPoint` interface, `CATS` stack order, `totals` sum |
| `Money.tsx` | `LEGEND` (key union + entry) and the selected-week caption |
| `StatusChip.tsx` / `styles.css` | Only if the kind gets a day-log chip or chart segment — chart colors: **REQUIRED SUB-SKILL** chart-color-validation |

### Tests

Backend: scorecard separation + week_days grouping + content locks.
Frontend: `lib.test.ts` cases for `subtotalsFromDay` split and `dayChips`
chip; update the exact-copy `trackedShareSentence` assertions.

## Verify

Grep finds the kind-literal consumers — every hit site must also mention the
new kind:

```bash
grep -rn "\"delivery\"\|'delivery'" frontend/src/lib.ts frontend/src/components/SpendChart.tsx frontend/src/screens/Money.tsx
```

**A clean grep is NOT completeness** — it cannot see `Today.tsx`'s hand-built
entry blocks, `today_snapshot`, `StatusChip.tsx`, or `--chart-*` tokens in
`styles.css`. Those four come only from walking the checklist tables above.
Finish with the real suites plus a manual look at Today, Week, and Money
showing the same number for the same item.
