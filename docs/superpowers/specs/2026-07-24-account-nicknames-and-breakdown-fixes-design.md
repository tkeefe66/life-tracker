# Account Nicknames + Breakdown Drill Fixes

**Date:** 2026-07-24
**Status:** Approved in conversation

Three related fixes to the Money screen's "Where it went" section, prompted by
a Labels-view drill-down that showed "Jun 8 · EVERYDAY CHECKING ...7395 (7395)"
— the raw bank account name with no clue what the transaction actually was —
and by Fidelity account chips that blank the whole section when clicked.

## Decisions

1. **Labels-view drill rows show the vendor.** The rows endpoint already
   returns `vendor` per row (`app/money.py:breakdown_rows`); the Labels view
   just never rendered it. Row becomes `date · vendor · account`. The Vendors
   view keeps `date · account` — its group header is already the vendor.
   Frontend-only change.
2. **Account nicknames follow the `role`/`active` pattern.** New nullable
   `nickname` TEXT column on `bank_accounts` — user-set, the sync never
   touches it (it already only overwrites `name`/`org`/`kind`). Standard
   migration in `_init_v2_tables()` (Postgres `ADD COLUMN IF NOT EXISTS`,
   SQLite `PRAGMA table_info` guard).
3. **Resolved in SQL, one place** (Override + Learning rule 2):
   `COALESCE(NULLIF(nickname, ''), name)` — as `display_name` in
   `get_bank_accounts()` and as `account_name` in the transactions join —
   so filter chips, drill rows, triage cards, and the label audit all pick
   it up with zero per-surface work. Raw `name`/`org` still returned from
   `/bank/accounts` so Settings can show which account you're naming.
4. **Nicknames are edited in Settings**, next to the role dropdown in the
   existing Bank accounts section. Empty string clears back to the bank's
   name (stored as NULL, not "").
5. **Investment-role accounts get no filter chip.** By construction
   (`bank_flows.classify_flow`), any transaction touching an
   investment-role account classifies as `investment` flow, never
   `spending`/`refund` — so its chip can never show a breakdown line.
   Filtering is by role, not by has-rows-in-window, so a savings account
   with an occasional debit keeps its chip. Requires `/bank/accounts` to
   include `role` (it already does).
6. **A filtered-empty result never blanks the section.**
   `VendorBreakdown.tsx` currently `return null` whenever the vendor list
   is empty — including when an account filter produced the emptiness,
   which unmounts the chips and leaves no way to deselect. New rule: hide
   the section only when the *unfiltered* data is empty; a filtered-empty
   result keeps the chips and shows "No spending in this account over this
   window."

## API

- `POST /bank/accounts/{simplefin_id}/nickname` — body
  `{"nickname": "Checking"}`; empty/whitespace string stores NULL.
  Mirrors the role route (404 unknown account). Response
  `{"ok": true, "simplefin_id": …, "nickname": …}`.
- `GET /bank/accounts` rows gain `nickname` (raw, nullable) and
  `display_name` (resolved). Existing `name`/`org`/`role`/`active`
  unchanged.
- `account_name` in `/bank/breakdown/rows`, triage, and label-audit
  responses silently becomes the resolved name — no shape change.

## Frontend

- **Settings:** text input per account row beside the role select,
  placeholder = bank name, saves on blur/Enter (same idiom as the label
  input in the drill-down).
- **VendorBreakdown:** drill rows in label mode render `r.vendor`; chips
  filter out `role === "investment"`; empty-state per decision 6. Chips
  and drill rows show `display_name`.

## Testing

pytest (SQLite path): migration adds the column; nickname route set/clear/404;
`get_bank_accounts` resolution; `account_name` resolution in the transactions
join. Frontend: no new pure logic in `lib.ts`; verified by
`npm test -- --run`, `tsc --noEmit` + `vite build`, manual look.

## Out of scope

Investment holdings display — separate spec
(`2026-07-24-investments-view-design.md`).
