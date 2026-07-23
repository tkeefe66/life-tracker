# Bank Transaction Ingestion (SimpleFIN)

**Date:** 2026-07-23
**Status:** Draft — awaiting review

## Problem

The app tracks spending only where a receipt email exists. The user wants all
spending, to answer "I make a lot yet I'm broke." A SimpleFIN connection is
live and a redacted probe of 90 days across 12 accounts tells us what we have.

**The central risk is not ingestion, it is arithmetic.** 25% of the user's
transactions are money *moving*, not money *spent*. Summing outflows naively
would double-count every credit-card purchase, invent spending from
checking-to-checking transfers, and classify saving and debt paydown as
profligacy — producing a confidently wrong answer to the exact question being
asked.

## What the probe established

- **965 transactions / 90 days** across 12 accounts. SimpleFIN caps history at
  a **rolling 90 days**, so anything older is unrecoverable and history only
  accumulates from first sync. This argues for ingesting early.
- Every transaction carries `id`, `posted`, `transacted_at`, `amount`,
  `description`, `payee`, `memo`, and **`mcc`** (merchant category code).
  MCC makes categorization mostly a lookup rather than an inference.
  *Unverified:* whether `mcc` is populated or merely present — non-card
  checking activity likely has none. The ingest must tolerate empty values.
- 244 transactions match transfer-like wording. 173 inflows, 792 outflows.
- SoFi (high-interest savings) is **not** among the connected accounts.

## Account roles (from the user)

| Account | Role |
|---|---|
| Wells Fargo 7395 | `spending` — primary day-to-day, pays the Amex |
| Wells Fargo 4116 | `bills` |
| Wells Fargo 0407 | `savings_dynamic` — money in and out by design |
| Amex Platinum | `credit_card` — primary spender, paid from 7395 |
| Chase United, Barclays JetBlue | `credit_card` — being paid down |
| Citi Simplicity | `credit_card` — zero transactions; dormant or not syncing |
| Fidelity ×5 (401k, Roth, Traditional, Rollover, Individual) | `investment` |

Roles are **data, not code** — stored per account and editable, so a new
account or a changed habit doesn't require a deploy.

## Design

### 1. Schema

**`bank_accounts`**

| Column | Notes |
|---|---|
| `simplefin_id` | unique |
| `name`, `org` | as reported |
| `kind` | SimpleFIN's own type, stored verbatim |
| `role` | user-set: `spending` \| `bills` \| `savings` \| `investment` \| `credit_card` \| `unknown`. New accounts default to `unknown` and are surfaced for classification rather than silently guessed |
| `active` | excluded accounts stay in the table |
| `last_synced_at` | |

Balances are **not** stored. They are not needed for spending analysis, and
not storing them keeps the most sensitive field out of the database.

**`bank_transactions`**

| Column | Notes |
|---|---|
| `simplefin_id` | unique — the dedupe key |
| `account_id` | FK |
| `posted`, `transacted_at` | dates |
| `amount` | signed, as reported |
| `description`, `payee`, `memo` | as reported |
| `mcc` | nullable |
| `flow` | derived, see §3 |
| `user_flow` | user override, nullable — same pattern as social/rides |
| `pair_id` | nullable, links the two halves of a matched movement |

Resolution is `COALESCE(user_flow, flow)`, computed in SQL so every caller
agrees — the established pattern in this codebase.

### 2. Sync job

`jobs/sync_bank.py`, scheduled every `SIMPLEFIN_SYNC_INTERVAL_HOURS`
(default 12) and once at startup, matching the Gmail job's shape.

- Fetches `/accounts?start-date=` for a configurable lookback (default 90).
- Upserts accounts, then transactions keyed on `simplefin_id`. Re-syncs
  overwrite mutable fields (amount, description, `posted`) because pending
  transactions settle and change; they never overwrite `user_flow` or `role`.
- **Follows the redaction boundary absolutely.** `services/simplefin_service.py`
  catches its own transport errors and returns a value from
  `services/safe_status.py`'s closed set. The access URL is read once from
  `config.SIMPLEFIN_ACCESS_URL` and is never passed anywhere an exception could
  capture it. No `str(e)` reaches `app_settings`.
- Records `bank_last_run` / `bank_last_status` / `bank_last_result`, surfaced
  in Settings beside Gmail and Calendar.

### 3. Flow classification — deterministic first

Applied in order; the first match wins. Only the final fallback is a guess.

1. **`investment`** — either side sits in an account whose role is
   `investment`. Contributions and the backdoor-Roth conversion leg are saving,
   never spending. An investment-to-investment movement (Traditional → Roth) is
   `investment` on both sides and contributes to nothing.
2. **`card_payment`** — a matched pair (see §4) where one side is a
   `credit_card` account. The purchases are already recorded on the card;
   counting the payment as well double-counts. Reported separately as debt
   paydown so aggressive paydown reads as progress, not profligacy.
3. **`transfer`** — any other matched pair between two known accounts.
   Checking → checking, or checking → savings.
4. **`income`** — an unpaired positive amount into a `spending` or `bills`
   account.
5. **`spending`** — everything else. Category derived from `mcc` when present.

### 4. Pair matching

The mechanism that makes the above work, and it is arithmetic, not AI:

Two transactions pair when **all** hold — different accounts, both known;
amounts equal in absolute value and opposite in sign; `posted` within
`PAIR_WINDOW_DAYS` (default 3, since settlement lags); neither already paired.
Matching runs after each sync over an unpaired backlog window, so a transfer
whose halves arrive in different syncs still pairs later.

When several candidates tie, prefer the smallest date gap, then the
lowest `simplefin_id`, so the result is deterministic and re-runnable.

**Deliberately not solved here:** an unpaired transfer-looking transaction
(Venmo, Zelle, Apple Pay, ATM) stays `spending` and is *flagged* as ambiguous
for later triage. The user has explicitly deferred that policy. Flagging costs
nothing now and avoids silently inventing a rule.

### 5. Security

The access URL is a bearer credential to the user's financial data.

- Lives only in `config.SIMPLEFIN_ACCESS_URL`, read once.
- Never logged, never stored in the database, never returned by any route.
- All error paths route through `safe_status`.
- **Targeted encryption is deferred to a follow-up, deliberately.** Storing
  `description`/`payee` in plaintext matches every other table today; adding
  column encryption is a separate, reviewable change, and doing it badly is
  worse than doing it later.

### 6. Out of scope

No UI in this phase — ingestion and classification only, verified through
tests and a read-only debug route. Also excluded: MCC → human category
mapping, bills/subscription detection, income analysis, budgets, the
Venmo/Zelle/ATM policy, and backfill beyond SimpleFIN's 90-day window.

## Testing

- Pair matching: exact opposite amounts across accounts pair; same-account
  movements do not; outside the window do not; near-miss amounts do not;
  an already-paired transaction is not re-paired; halves arriving in separate
  syncs pair on the later run; ties resolve deterministically.
- Flow precedence: investment beats card_payment beats transfer; an
  investment-to-investment move contributes nothing; an unpaired positive into
  `spending` is income; an unpaired Venmo outflow stays `spending` and flagged.
- Re-sync idempotence: syncing the same payload twice changes nothing;
  a settled amount updates; `user_flow` and `role` survive.
- Security: a transport failure stores only a closed-set status; the access URL
  appears in no stored value, log line, or API response (property test with a
  credential-bearing URL in the exception).

## Open questions

1. **SoFi** is not connected — add it in SimpleFIN, or accept the blind spot?
2. **Citi Simplicity** returns zero transactions — dormant, or a sync problem?
3. Which Wells Fargo account actually receives payroll? Needed for income in a
   later phase, not for ingestion.
