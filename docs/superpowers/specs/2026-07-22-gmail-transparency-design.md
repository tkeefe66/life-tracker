# Gmail Receipt Transparency + 30-Day Backfill

**Date:** 2026-07-22
**Status:** Approved

## Problem

The Gmail receipt pipeline is a black box: the user cannot see what a scan found
or which orders made it into the app, and the fixed 7-day lookback cannot
backfill the month of orders missed while the Gmail API was disabled.

## Design

### 1. Configurable scan window

- New env var `GMAIL_SCAN_LOOKBACK_DAYS`, read only in `config.py`, integer,
  default `7`. Document in `.env.example` and CLAUDE.md's env table.
- `services/gmail_service.fetch_delivery_candidates` builds its query with
  `newer_than:{GMAIL_SCAN_LOOKBACK_DAYS}d` instead of the hardcoded `7d`.
- Operational step (not code): set `GMAIL_SCAN_LOOKBACK_DAYS=30` on the Railway
  service and restart; the startup scan backfills the last month. The value
  stays at 30 — dedupe on `gmail_message_id` skips known emails before any AI
  call, so steady-state cost is unchanged. Backfilled orders keep their
  original `ordered_at` dates, retroactively correcting past weeks' scorecards.

### 2. Scan summary in Settings

- `jobs/scan_gmail.py` already computes `candidates`, `ai_checked`, `added`.
  After a successful scan it additionally stores
  `gmail_last_result` = `"{candidates} candidates · {ai_checked} AI-checked · {added} new orders"`
  via `db.set_setting`. On failure the existing error status behavior is
  unchanged and `gmail_last_result` is left as-is (it describes the last
  successful scan).
- `GET /api/settings` returns `gmail_last_result` alongside the existing
  `gmail_last_run` / `gmail_last_status`.
- The Settings screen shows the result line under the Gmail row when present.

### 3. Detected-orders list

- New route `GET /api/deliveries?days=60` (clamped 1–365): returns
  `{"orders": [{service, subject, ordered_at}, ...]}` newest-first, built on
  the existing `db.get_delivery_orders_range` (no new SQL). Range =
  local today − days … local today.
- Settings screen gains a collapsible "Detected orders" section (native
  `<details>`/`<summary>`, styled with existing tokens) listing service,
  subject, and order date per row. Empty state: "None detected yet."

## Testing

- pytest: `GMAIL_SCAN_LOOKBACK_DAYS` default; `/api/deliveries` shape,
  ordering, and clamping; scan job writes `gmail_last_result` on success.
- Frontend: build + existing vitest suite (list rendering only, no new pure
  logic).

## Out of Scope

- Rejected-candidates audit table, re-classification UI, manual scan trigger
  button, changes to receipt matching rules.
