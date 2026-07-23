# Triage Suggestions — learning from the user's answers

**Date:** 2026-07-23
**Status:** Approved — all decisions made by the user before writing

The triage queues work, and the user has started answering them. This spec
makes the queues learn: each confirmed answer becomes a few-shot example, and
remaining rows arrive with an AI-suggested choice pre-highlighted. One tap
confirms. This is the Override + Learning Pattern's step 4 (feed overrides
back as examples), which social events and rides already implement — applied
to bank triage.

## Decisions (made 2026-07-23, before implementation)

1. **Suggest + one-tap confirm.** A suggestion is never counted anywhere until
   the user taps it. Rejected: auto-applying exact repeats, auto-applying all.
2. **AI few-shot** (Claude haiku via `ai_metrics`), not exact-counterparty
   matching — the queue's wording varies too much for exact matching.
3. **Permanent feature** — every sync's new queue rows get suggestions.
4. **Both queues** — inflow and outflow.

## 1. The boundary change, stated precisely

This feature deliberately relaxes one rule and hardens another:

- **Bank payee/description text may now reach Claude in the classification
  call only** — `ai_metrics.suggest_bank_flows`, exactly as Gmail subjects and
  calendar titles already do. The reflection prompt (`/api/reflection`) and
  Telegram (`format_scorecard_text`) remain bank-free; nothing in this feature
  touches either.
- **`user_note` never reaches any AI.** Notes are the user's own words — the
  most personal text in the database. The prompt builder must exclude them,
  and a test must assert a note string cannot appear in the built prompt.

CLAUDE.md's outbound-boundary bullet is updated to say exactly this.

## 2. Data model

New **derived**, nullable TEXT column `bank_transactions.suggested_flow`
(+ migration per the `_init_v2_tables()` pattern):

- Written only by the sync job (§4). Never read by any aggregate —
  `app/money.py` must not reference it, and a test proves setting it changes
  no `summary()` figure.
- One suggestion per row, computed once: rows are immutable text, so a
  suggestion is not recomputed as examples improve. Answering the row makes it
  moot (the row leaves the queue via `user_flow`); put-back returns the row
  with its old suggestion still attached, which is correct and free.
- Allowed values: the union of both queues' button flows —
  `spending / transfer / card_payment / investment / income / refund` — or
  NULL (no suggestion / abstained / not yet processed).

## 3. The examples query

`db.get_bank_flow_examples(limit=20)` — most recent rows with
`user_flow IS NOT NULL`, newest first, returning `payee`, `description`,
`side` (`"inflow"` if `amount > 0` else `"outflow"`), and `user_flow`.
Excludes `user_note` by construction (simply not selected). Mirrors
`get_classification_examples` / `get_ride_examples`.

## 4. The inference call and the sync hook

`ai_metrics.suggest_bank_flows(rows, examples)` — the module's `_call_json()`
pattern, existing `MODEL` (haiku), ONE call per batch (never per row):

- Input: up to 40 unanswered rows (`simplefin_id`, `payee`, `description`,
  `side`) plus up to 20 examples.
- Output: `{simplefin_id: flow_or_null}`. Every value is validated against the
  row's side-specific allowed set — inflow: `income/transfer/refund`;
  outflow: `spending/transfer/card_payment/investment` — invalid or
  unknown-id entries are dropped, never written. `null` = abstain.
- The prompt instructs the model to abstain when unsure — a wrong
  pre-highlight is worse than none.

`jobs/sync_bank.py`, after the reclassify pass: select queue rows
(`ambiguous AND user_flow IS NULL`, plus `resolved_flow = 'inflow_unknown'`
rows) with `suggested_flow IS NULL`, in batches of 40, **max 3 batches per
run** (covers the current ~97-row backlog in one run; a runaway queue costs at
most 3 calls per sync). Write results via a new
`db.set_bank_suggestions_bulk(mapping)` (derived write, one transaction,
values validated against the §2 set before any write). AI failure follows the
job's existing quiet-failure rules — `logger.exception`, never crash, and
**`bank_last_status` stays whatever the sync itself earned** (the sync
succeeded; suggestions are an enhancement, not part of its contract). Rows
simply arrive unsuggested and are retried next sync (their `suggested_flow`
is still NULL).

Cost: ≤3 haiku calls per sync, 2 syncs/day steady-state — negligible; the
`MODEL` constant is untouched.

## 5. What the user sees

Triage rows carry `suggested_flow` (via `_BANK_TXN_SELECT`). In `TriageQueue`,
the chip whose flow matches gets a `.chip-suggested` treatment (existing
tokens — the `--accent-soft` family; no green/red) plus a small muted hint
line: `looks like: {label}` using the existing `flowLabel`-style plain
language. Tapping it is the ordinary confirm (writes `user_flow`); tapping any
other chip works exactly as today. No new interaction, no auto-anything.
Recently-sorted rows don't show suggestions (already answered).

## 6. Out of scope

- Persisted rules, auto-apply of any kind (spec §6.3 of the bank-UI design
  still governs).
- Re-suggesting a row as examples improve; suggestion feedback loops beyond
  the examples query itself.
- Suggestions anywhere outside the two triage queues.
- Sending `user_note`, amounts, dates, or account names to the model — the
  prompt carries payee/description/side only.
- Any change to `bank_flows.py`, pairing, or `ambiguous` semantics.

## 7. Testing

- Examples query: returns only answered rows, newest first, capped, sides
  correct, **no `user_note` key in the result**.
- `suggest_bank_flows`: side-set validation drops an inflow row suggested
  `spending` and an unknown id; abstain (`null`) passes through as no-write;
  fenced/prose-padded model output tolerated (`_call_json` already does);
  **a `user_note` string placed on an input row cannot appear in the built
  prompt** (assert on the prompt text).
- Sync hook: writes only to queue rows with NULL `suggested_flow`; batch cap
  and 3-batch ceiling honored; AI exception → sync still completes with its
  normal status; already-suggested rows are not re-sent (no double spend).
- `set_bank_suggestions_bulk`: validates before any write; one transaction;
  unknown ids skipped.
- Aggregates ignore suggestions: set `suggested_flow` on seeded rows, assert
  `summary()` byte-identical.
- Frontend: `tsc`/build for the chip treatment; a `lib.ts` helper for the
  `looks like: {label}` string is unit-tested.
