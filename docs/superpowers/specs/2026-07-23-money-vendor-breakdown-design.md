# Money Vendor Breakdown & Labels — where the money went

**Date:** 2026-07-23
**Status:** Approved in conversation — spec written for user review

The Money screen answers "how much" (weekly spending, flow totals) but never
"where." This feature adds a **"Where it went"** section: spending grouped by
vendor, filterable by account, and — in later phases — by user-assigned labels
("Monthly Rent", "401k contribution") that double as categories.

## Why no vendor directory table

The obvious design — a `vendors` table with a payee→vendor mapping — was
considered and rejected. SimpleFIN's bridge already normalizes merchant names:
all raw-description variants (`Amazon.com*EK66U04`, `AMAZON MKTPL*5K71T`, …)
arrive with payee `"Amazon"`. Measured on the real data (965 rows): 238
distinct payees among spending rows, payee never empty, a clean power-law head
(Amazon 45 txns / Uber 42 / Uber Eats 37) and a long tail of 146 one-off
payees. A directory table would mostly mirror `payee` verbatim. Grouping on
`payee` directly is the design; labels (Phase 2) handle the cases where the
payee is not the useful name (checks, Zelle counterparties, ATM rows).

## Decisions (made 2026-07-23, before implementation)

1. **Three phases, each independently shippable:** vendor breakdown (no schema
   change) → labels (one column) → auto-apply (one more column + sync rule).
2. **Labels ARE the categories.** One system: a transaction gets one label
   from the user's own growing vocabulary; the category rollup is aggregation
   by label. Rejected: a separate fixed taxonomy alongside labels (two systems
   to maintain, overkill for a single-user app).
3. **Auto-apply is rule-based (same payee), not AI.** Recurring items — rent,
   401k, subscriptions — recur with identical payees, so payee-match
   inheritance covers them for free. Precedent note: the triage-suggestions
   spec (same date) has already relaxed the bank-text-to-Claude boundary for
   classification calls, so AI label suggestions are no longer boundary-
   breaking — they are simply not needed yet. If the vocabulary ever wants AI
   help, that is a separate spec reusing `ai_metrics`' few-shot pattern.
4. **`user_note` stays as-is** — a separate free-text field. Labels do not
   replace it. Per the triage-suggestions spec, notes never reach any AI;
   nothing here changes that.
5. **Whole vs by-account is a filter, not separate figures** — one code path,
   an `account_id` parameter.

## 1. Data model

**Phase 1:** no schema change. Groups on the existing
`bank_transactions.payee`.

**Phase 2:** one new nullable column, following the established user-column
contract (`user_flow`, `user_note`):

- `bank_transactions.user_label TEXT` — user-set; the sync never touches it.
  Migration per the `_init_v2_tables()` pattern (Postgres
  `ADD COLUMN IF NOT EXISTS`, SQLite `PRAGMA table_info` guard).
- The label vocabulary is **not a table** — it is
  `SELECT DISTINCT user_label`, surfaced through autocomplete. No vocabulary
  management UI; a label with zero rows simply disappears.

**Phase 3:** one derived column, mirroring `suggested_flow` from the
triage-suggestions feature:

- `bank_transactions.suggested_label TEXT` — system-written; the sync may
  overwrite it, never `user_label`. Resolved in SQL as
  `COALESCE(user_label, suggested_label) AS resolved_label`, exactly like
  `resolved_flow` (Override + Learning pattern rule 2: resolve in SQL, never
  per-call-site in Python).
- Inheritance rule (in the sync, pure logic in `bank_flows`): a newly
  upserted row whose payee matches an already-user-labeled payee gets that
  label suggested. **If one payee carries two or more different user labels**
  (e.g. Amazon split between "Household" and "Gifts"), the sync suggests
  nothing for that payee — ambiguity means stay silent, per the "an AI/system
  flag alone never changes behavior silently" rule.

## 2. Aggregation semantics

- **Vendor view:** spending rows only (`resolved_flow = 'spending'`, negative
  side), grouped by `payee`. Refund rows (`refund`, positive side) are
  **netted into their payee's line**, so a return shows the vendor at its true
  net — consistent with how `money.summary`'s `spent` already nets refunds.
  A payee with only refunds in the window shows as a negative-net line —
  that is the true figure, not an error (same stance as the weekly chart's
  negative refund weeks). Sorted by net total descending. Lines past the top
  15 collapse into an "Everything else (N vendors)" tail, expandable.
- **Label view (Phase 2):** same math, grouped by `resolved_label`
  (Phase 2: just `user_label`), plus an "Unlabeled" bucket so the section
  always sums to the window's spending total.
- **Window:** the same `weeks` selector the Money screen already uses, same
  clamp (1–52).
- **Account filter:** optional `account_id`; applies identically to both
  views. Chips show the user-set account names, active accounts only.
- **Rounding:** sum raw per-row amounts within each group, round once at the
  end — the same round-once rule `money.summary` documents. Never sum
  already-rounded values.

## 3. API

All new routes are protected (session cookie) and are **secondary surfaces**:
a failed fetch hides the section, never blanks the Money screen.

- `GET /api/bank/breakdown?weeks=N&by=payee|label&account_id=<opt>` —
  grouped lines + tail bucket (`by=payee` ships in Phase 1; `by=label` and
  the label mode of the rows route ship in Phase 2); in `by=label` mode the
  response also carries
  the label vocabulary (saves the frontend a second request for autocomplete).
  Assembled by a new `breakdown()` in `app/money.py` (same DB→domain role as
  `summary()`; no SQL outside `database.py`).
- `GET /api/bank/breakdown/rows?weeks=N&payee=…|label=…&account_id=<opt>` —
  the drill-down: transactions behind one line (date, amount, account,
  label). Limit-clamped like triage (1–200).
- `POST /api/bank/label` `{simplefin_id, label}` — Phase 2. `label: null`
  clears. Touches only `user_label`; same shape as the existing flow-override
  route. Label strings are trimmed; empty string is treated as null.
- Phase 3 adds a bulk variant: `POST /api/bank/label` with
  `{payee, label}` applies to all of that payee's un-user-labeled rows
  (the "apply to N similar" action). The response reports how many rows were
  labeled.
- `/api/bank/summary` is untouched.

## 4. Frontend

- New **"Where it went"** section on the Money screen, below the existing
  flow totals: account filter chips (All + named active accounts), a
  Vendors/Labels toggle (Labels appears in Phase 2), top ~15 lines with count
  and net amount, expandable "Everything else" tail.
- **Tapping a line expands its transactions** (drill-down fetch). This
  drill-down is *the* labeling surface — each row gets an inline label
  control in Phase 2: type-ahead over the existing vocabulary, free text
  creates a new label. Triage stays focused on flow questions.
- Phase 3 surfaces "Apply to N other <payee> rows?" after labeling a row
  whose payee has unlabeled siblings — same offer mechanic as triage's
  `signature_count` bulk action.
- Conventions: money formatting via the one shared formatter (null-checked,
  `$0` displays), quiet failure for the whole section, no new chart — this is
  a table section, so no chart-token or viewBox concerns.

## 5. Phasing & testing

1. **Phase 1 — vendor breakdown.** `breakdown()` + two GET routes + section
   UI with account chips and drill-down (read-only rows). Tests: pytest on
   the aggregation math — refund netting per payee, account filter, tail
   bucket, round-once — against SQLite; vitest for new `lib.ts` helpers;
   `tsc --noEmit` + `vite build`.
2. **Phase 2 — labels.** `user_label` migration, label POST route, inline
   picker + vocabulary autocomplete, Labels view with Unlabeled bucket.
   Tests: label set/clear round-trip, vocabulary distinct-query, label-view
   sums (labeled + unlabeled = window spending total).
3. **Phase 3 — auto-apply.** `suggested_label` migration, payee-inheritance
   in the sync (silent on conflicting labels), bulk apply route + offer UI.
   Tests: inheritance on new upserts, conflict silence, bulk apply skips
   rows that already have a `user_label`.

Each phase lands as its own plan + branch cycle. Coordination note: the
triage-suggestions feature (in flight on the same date) also adds a bank
migration and touches triage UI; rebase whichever lands second — the columns
(`suggested_flow` vs `user_label`/`suggested_label`) do not collide.

## Rejected alternatives

- **Vendor directory table upfront** — see "Why no vendor directory table."
- **AI labeling from the start** — not needed for recurring items (identical
  payees), and keeps this feature free of Claude-call cost and prompt
  plumbing. Now a precedented, allowed follow-up rather than a boundary
  violation, but still deliberately out of scope.
- **Two systems (fixed categories + labels)** — rejected as decision 2.
- **Bank data as a metric** — unchanged and out of scope: no targets, no
  hit/miss, nothing here enters `METRICS`, so the reflection and Telegram
  paths remain bank-free by construction.
