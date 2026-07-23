# Refunds and Triage Notes

**Date:** 2026-07-23
**Status:** Approved — all decisions made by the user before writing (no open questions)

The Money UI shipped earlier today with a two-queue triage worklist. Using it
surfaced two gaps in the inflow queue ("Where did this come from?"): a card
refund fits neither "It's income" nor "Moved from another account" (the
ingestion phase already flagged ~21 of the 35 `inflow_unknown` rows as almost
certainly Amex refunds), and some deposits need a human explanation that no
button can carry. This spec adds a **Refunded** verdict and an **optional
free-text note** on triage answers.

## Decisions (made 2026-07-23, before implementation)

1. **Refunds subtract from spending.** The hero shows net spending; the
   arithmetic is made visible rather than silent (§2).
2. **Notes are available on both queues**, not just inflow — a mystery charge
   deserves an explanation as much as a mystery deposit.
3. **Notes are optional and ride along with the answer** — type if you want,
   then tap a choice; both save in one request. A note alone does not clear a
   row (rejected: "note is the answer").
4. **Notes travel with the transaction** — rendered wherever the row appears
   (both queues and Recently sorted), not only in an audit trail.

## 1. The `refund` flow

`"refund"` becomes the seventh member of `BANK_FLOWS`. It is a **user-only
verdict**: `bank_flows.classify_flow` must never produce it, and `bank_flows.py`
is untouched by this feature. A refund exists only in `user_flow`, arriving via
the same Override + Learning pattern as every other correction — which also
means the sync can never overwrite it, and `resolved_flow =
COALESCE(user_flow, flow)` needs no change.

Rejected alternative: keep refunds as positive-amount `spending` rows and make
every spending aggregate sign-aware. That overloads one flow value with two
meanings and touches every aggregation point; a distinct value touches only the
places that must know about refunds anyway.

The inflow queue gains a third button:

| Button | `user_flow` |
|---|---|
| It's income | `income` |
| Moved from another account | `transfer` |
| **Refunded** | **`refund`** |

The outflow queue is unchanged. The API validates against `BANK_FLOWS`, so a
`refund` verdict on an outflow row is *accepted* but inert: the refund total
sums only positive amounts (§2), so a mis-tap contributes nothing and is
recoverable from Recently sorted. Nothing special-cases it.

## 2. The math

- **`spent` nets refunds:** `sum(abs(amount) where resolved_flow='spending' and
  amount<0) − sum(amount where resolved_flow='refund' and amount>0)`, rounded
  once at the end (the existing round-once rule).
- **Weekly bars net refunds within their posted week**, so the bars still sum
  to the hero. A week that nets negative renders a zero-height bar; the tap
  caption shows the true (negative) net. `weekCaption` must format a negative
  spending value sensibly (`−$X`), not hide it.
- **`totals` gains a `refund` entry** (`{count, amount}`, positive side only) —
  the mirror image of the movement flows' outflow-side rule.
- **The hero gains a sub-line, only when refunds exist:** "after $X refunded",
  derived on the frontend from `totals.refund.amount`. No new API field. The
  movement block ("Where the rest went") keeps its three rows; refunds are not
  "separated out", they are subtracted, and the two sentences must not blur.
- `triage_counts` is unchanged — a refund answer clears the row via
  `user_flow`, exactly like every other verdict.
- `flowLabel("refund")` → `"Refunded"`.

## 3. Notes

- **Schema:** new nullable `user_note` TEXT column on `bank_transactions`, with
  a migration per the `_init_v2_tables()` pattern (Postgres `ADD COLUMN IF NOT
  EXISTS`, SQLite `PRAGMA table_info` guard). It is a **user column**: the sync
  never reads or writes it (Override + Learning rule 3).
- **Write path:** `POST /api/bank/transactions/{id}/flow` body gains an
  optional `note`. Trimmed; empty string → NULL; capped at 500 characters
  (400 above the cap). Flow and note write atomically in one transaction.
  Omitting `note` leaves the stored note untouched (so put-back — `{flow:
  null}` — keeps the note: it explains the transaction, not the answer).
  Passing `note: ""` explicitly clears it.
- **Bulk carries no note.** One sentence shared across forty rows explains
  nothing. A `note` key in the bulk body is a 400 — rejected loudly rather
  than silently dropped, per the repo's explicit-error convention.
- **Read path:** `_BANK_TXN_SELECT` gains `user_note`, so every row dict —
  triage buckets, recent, range queries — carries it with no second query.
- **Frontend:** each triage row gets a quiet "add note" affordance that
  expands a single text input; typing then tapping a choice sends both. The
  saved note renders muted beneath the row's label wherever the row appears:
  both queues and Recently sorted.

## 4. Boundary

Notes are bank text — the most personal free text in the database, since the
user wrote it. The existing rule extends verbatim: **bank text (now including
`user_note`) never reaches Claude (`/api/reflection`) or Telegram
(`format_scorecard_text`)**. It holds by construction (bank data is not in
`METRICS`); it is stated so it keeps holding.

## 5. Out of scope

- Notes on anything other than bank transactions.
- A refund-to-purchase matching pass (linking a refund to its original charge).
- Bulk-applying notes, or persisted note templates.
- Any change to `bank_flows.py` classification, pairing, or ambiguity.
- Surfacing refunds as their own movement row (hero sub-line only, per §2).

## 6. Testing

- `BANK_FLOWS` accepts `refund` end-to-end (override writers, route validation).
- `classify_flow` never returns `refund` — asserted across representative
  inputs so a future "helpful" classifier change fails a test.
- `summary`: refund subtracts from `spent`; weekly net within the posted week;
  negative week floors the bar data at the frontend (backend reports the true
  net); `totals.refund` sums positive side only, count includes the row.
- Refund on a negative-amount row contributes nothing to `spent` or `refund`
  totals (the inert mis-tap case).
- Notes: write-with-flow atomic; omitted note preserves stored note across
  put-back; explicit `""` clears; 501 chars → 400 with nothing written; a
  `note` key in the bulk body → 400; `user_note` present in triage and recent
  rows.
- `lib.ts`: the inflow choices table is exactly income/transfer/refund with the
  labels above; `flowLabel("refund")`; `weekCaption` with negative spending.
- Components remain tsc/build-verified (no component test framework).
