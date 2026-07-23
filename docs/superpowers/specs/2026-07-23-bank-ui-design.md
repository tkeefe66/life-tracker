# Bank / Money UI

**Date:** 2026-07-23
**Status:** Draft — awaiting review

Ingestion shipped deliberately blind (`2026-07-23-bank-ingestion-design.md` §6:
"No UI in this phase"). 992 classified transactions now sit behind
`GET /api/bank/debug`, a route whose own docstring says it exists only because
there is no bank UI yet. This spec designs that UI.

---

## Open questions — need the user's call before implementation

> **Questions 1–3 were decided on 2026-07-23 — see §12 Resolved.** All three
> landed on the option this spec already assumed, so nothing below changed as a
> result; the text is kept as the record of what was weighed. Questions 4–6
> stand at their proposed defaults and do not block implementation.

1. **Does Money get its own tab?** This spec recommends promoting Money out of
   the Insights segmented control into a fifth top-level tab (§3), which
   reverses the explicit decision in
   `2026-07-23-insights-tab-and-spend-subtotals-design.md` ("A Spend tab was
   previously agreed but never started; it is folded into this design rather
   than shipped as a fifth tab"). The argument for reversing it is in §3; it
   rests on facts that didn't exist then (a maintenance queue, account roles,
   a second and larger spend total). If the user disagrees, the fallback is
   §3's Option B and everything downstream still works — only the nav changes.

2. **An ATM withdrawal has no truthful flow value.** 13 of the 62 ambiguous
   rows ($1,139.10) are cash out of a machine. It is not `spending` (the money
   still exists at the moment of withdrawal), not `transfer` (nothing arrived
   in a connected account), and not `investment`. Every option in `BANK_FLOWS`
   is a small lie. Choices: (a) triage them as `spending` and accept that cash
   is counted at withdrawal rather than at purchase — defensible, since the
   money leaves the tracked world; (b) triage them as `transfer` and accept
   that cash spending is invisible; (c) add a seventh flow, `cash`, which is a
   schema change plus a decision about which totals it joins. **The spec
   assumes (a)** and labels the triage choice accordingly, but this is the
   user's call, not a design detail.

3. **Should income appear at all in v1?** There are 7 `income` rows in 90 days
   and payroll is split with an unconnected SoFi account, so any income figure
   is a floor with an unknown gap — the SoFi hazard from the ingestion spec.
   Showing "$X in" invites the user to do arithmetic the data cannot support.
   The spec shows it, once, explicitly framed as "at least" (§5.3). The
   alternative is to omit income entirely until SoFi is connected.

Secondary, lower stakes:

4. **Bulk correction.** 43 of 62 ambiguous rows are P2P (Venmo/Zelle/Cash
   App/Apple Cash). §6.3 offers a one-shot "apply to the other N like this"
   that writes `user_flow` on each matching row and persists no rule. A
   persisted rule engine is explicitly out of scope (§8). Confirm that's the
   right trade — the counter-argument is that Venmo genuinely is both spending
   (paying a friend back for dinner) and movement, so no standing rule would
   be correct anyway.

5. **A fourth validated chart hue.** §7 reuses `--chart-delivery`'s already
   validated value for the bank spending series rather than picking a new one,
   and keeps the two charts from co-rendering by default. Cleaner would be a
   fourth validated hue, but that requires the full six-check validation pass
   against both surfaces and all existing pairs, which is not something to
   improvise inside an implementation task.

6. **Should the Money surface be lock-flagged?** Bank descriptions and payees
   are the most sensitive free text in the database — more so than the
   `substances` metric that carries `private: True`. §9 keeps bank data off
   every outbound surface but does not gate the on-screen view. Confirm that's
   enough.

---

## Problem

The app answers "are you doing the things you said you'd do?" It answers a
second, quieter question alongside it — "what did this cost?" — and until now
that answer covered only the three signals that leave a receipt email:
delivery, rides, social. The bank ingestion phase established the real
denominator, and the numbers are not close:

| Surface | 90-day total |
|---|---|
| Tracked categories (delivery + rides + social) | receipts only — a small fraction |
| `spending` from the bank | 647 transactions |

The user's stated motivation for the whole bank phase was "I make a lot yet
I'm broke." That question is currently answerable only by hand-editing a URL
into `/api/bank/debug` and reading JSON. Meanwhile 62 transactions are sitting
in a triage state that exists *specifically* to be shown to a human, and 12
accounts carry roles that can only be set with a `curl`.

**Who it's for:** one user, pull-based. Nothing pushes. The screen is opened
deliberately, usually not daily — which shapes everything below: the triage
queue must survive being ignored for two weeks and still be tractable, and no
surface may nag.

## What the UI must answer

In priority order. Anything not on this list is out of scope (§8).

1. **What did I actually spend, over the window?** One number, correct — which
   means built on `COALESCE(user_flow, flow) = 'spending'` and nothing else.
2. **Where did the rest go?** 276 of 992 rows are movement (card payments,
   transfers, investment). Showing spending without showing the movement it
   was separated from invites the user to distrust the number, because it
   won't match any statement total they've ever seen. The movement block is
   not a bonus feature; it is the credibility of the spending figure.
3. **Is it going up or down?** Weekly spending over the window.
4. **How much of my spending is the behavior I'm tracking?** The delivery /
   rides / social subtotals, now correctly framed as a *subset* of (1) rather
   than a total in their own right.
5. **What still needs my judgement?** The 62 ambiguous rows and the 35
   `inflow_unknown` deposits.
6. **Is the plumbing healthy?** Last sync, account roles, coverage window.

## 1. The thing this UI must not become

Bank data is **not a metric**. No target, no hit/miss, no ledger row, no
streak, no entry in `METRICS`. Rides are the precedent and the precedent is
explicit — CLAUDE.md: "Rides are not a metric. They are deliberately absent
from `METRICS` — no target, no hit/miss, no ledger row. They surface only as
counts and spend."

The temptation here is stronger than it was for rides, because spending has an
obvious number and an obvious direction, and a weekly budget is one line of
code away. Resist it. The app's premise is five things the user said they'd
do; a budget is a sixth thing the user has not said they'd do, and inventing
it would quietly change what the app is for. If the user later wants a
spending target, that is a deliberate `METRICS` addition with its own spec,
not a side effect of building a screen.

Concretely, this means the Money surface has **no green/red**, no `--chart-hit`
/ `--chart-over` semantics, no meters, and no "you're over" language.

## 2. Where the numbers come from

Every aggregate resolves through `COALESCE(user_flow, flow)`, in SQL, in
`database.py` — the existing `_BANK_TXN_SELECT` already exposes it as
`resolved_flow`. No caller re-derives it, and no caller reads bare `flow`.

| Displayed as | Built from |
|---|---|
| Spent | `resolved_flow = 'spending'`, sum of `abs(amount)` |
| Paid off cards | `resolved_flow = 'card_payment'`, outflow side only |
| Moved between accounts | `resolved_flow = 'transfer'`, outflow side only |
| Into investments | `resolved_flow = 'investment'`, outflow side only |
| Money in (at least) | `resolved_flow = 'income'` |
| Needs a decision | `ambiguous = true` **and** `user_flow IS NULL` |
| Unexplained deposits | `resolved_flow = 'inflow_unknown'` |

**Outflow side only, and why it matters.** A matched transfer is two rows
summing to zero. Summing `abs(amount)` over both halves doubles the figure and
reports $2,000 of movement for a $1,000 transfer. Every movement total filters
to `amount < 0` before summing. This is the mirror image of the double-count
hazard the classifier exists to prevent, and it is easy to reintroduce at the
presentation layer after the hard work was done at the classification layer.

## 3. Information architecture — where this lives

### The decision

**Money becomes a fifth top-level tab**: Today / Week / Money / Insights /
Settings. The Insights tab reverts to a single Behavior view and loses its
segmented control; its current Money view moves wholesale into the new tab and
grows the bank sections around it. Account roles go to **Settings**, not to
Money.

### Why, and what the alternatives cost

The Insights spec folded a planned Spend tab into an Insights segment. That was
right at the time: "Spend" then meant three receipt-derived subtotals and one
stacked chart — a section, not a screen. Three things changed.

- **Money acquired a maintenance queue.** Triage is not analysis. It is a
  worklist the user works through and clears. The Insights tab exists because
  the Week screen had become "a dumping ground" of unrelated surfaces; putting
  a 62-item worklist behind a segmented control on the analysis tab recreates
  precisely that mistake, one screen over.
- **There are now two spend totals, and one contains the other.** Tracked-
  category spend is a strict subset of bank spending. Two totals on one screen
  is a correctness hazard, not a layout problem: the user must never be able to
  read them as additive. Owning a whole screen lets the containment be
  expressed structurally — bank total in the hero, tracked categories nested
  beneath it under a heading that says so — rather than crammed into a segment.
- **Money is now a question the user opens the app to ask.** It was previously
  a detail of a behavior review. A surface reached in two taps (Insights →
  Money) with no memory of the last choice (the segment state is deliberately
  not persisted) is a surface the user will stop visiting.

**Option B — keep it inside Insights, add bank sections to the Money view.**
Cheapest, no nav change, and it preserves a prior decision rather than
reversing it. Costs: the worklist lands on the analysis tab; the money view
becomes roughly three times its current length, which will want its own
sub-navigation, and nesting a segmented control inside a segmented control is
worse than adding a tab. Viable fallback if the user rejects the fifth tab —
nothing else in this spec depends on the choice.

**Option C — extend the Week screen.** Rejected. Week is scoped to one week and
to the scored ledger; bank data is a rolling 90-day window with no week-level
verdict. A 90-day figure on a screen with ‹ › week navigation would be read as
that week's figure.

**Option D — a separate "Bank" tab distinct from "Money".** Rejected: it
splits one question ("what did this cost?") across two surfaces by data
provenance, which is an implementation detail the user should never see.

### Why account roles go to Settings, not Money

Roles are configuration: set once per account, changed when a habit changes,
and load-bearing for classification (`role` is the first input to
`classify_flow`). Settings already owns the Sync section that reports
`bank_last_status`, and a role change is exactly the kind of edit whose effect
appears on the *next* sync — which is a Settings-shaped promise, not a
Money-shaped one. Money stays a reading surface plus one worklist.

## 4. The 90-day horizon and what shows before history exists

SimpleFIN keeps a rolling 90 days and history only accumulates forward from the
first sync. Three consequences the UI must handle rather than paper over.

- **Weeks before coverage begins are absent, not zero.** A zero-height bar for
  a week that predates the data reads as "spent nothing that week," which is a
  lie the chart tells confidently. The weekly series starts at the first week
  containing a transaction and no earlier, and the response carries
  `covered_from` so the frontend never has to infer it.
- **The first and last weeks are partial** — the first because coverage began
  mid-week, the last because it hasn't finished. Both are marked the way
  `SpendChart` already marks the in-progress week (`spend-seg-current`), and
  the caption says "partial week."
- **A permanent footnote**: "Bank data starts {covered_from}. SimpleFIN keeps
  90 days, so nothing before that exists." Not an error, not dismissible — the
  horizon is a property of the data source, not a temporary condition.

The default window is **12 weeks (84 days)**, which fits inside the 90-day cap
with room to spare and matches the window Insights already uses.

### Empty states

Three distinct ones, and they must not collapse into each other.

| Condition | What the screen shows |
|---|---|
| `SIMPLEFIN_ACCESS_URL` unset (**this is production today** — the credential has never been set on Railway) | Tracked-category sections render normally. In place of the bank sections, one quiet line: "Bank sync isn't set up." No error styling, no call to action the user can't complete from the phone. |
| Configured, but no transactions yet (first sync pending) | "Waiting for the first sync." plus the Settings sync status line. |
| Configured, transactions present, nothing ambiguous | Triage section renders as "Nothing to sort out." — a cleared worklist is a result worth showing, not an empty region to hide. |

A failed `/api/bank/summary` fetch hides the bank sections and leaves the rest
of the screen intact, per the app's secondary-surface rule. It never sets a
screen-level error.

## 5. The Money screen

Top to bottom. One screen, no sub-navigation.

### 5.1 Spent

Hero total for the window, largest text on the page, proportional figures (not
`tabular-nums`), reusing the existing `.money-hero` / `.money-hero-sub`
treatment. Subtitle: "spent · last 12 weeks".

Beneath it, one chart: **weekly bank spending**, single series, hand-rolled
SVG, same viewBox discipline as `SpendChart` (`0 0 360 96`,
`preserveAspectRatio="xMidYMid meet"`, `width:100%; height:auto`, x-axis band
inside the viewBox, never a fixed pixel height). Tap a bar for a caption with
the week range, the total, and "partial week" where it applies. Single series
means no legend is required — the direct-label rule exists for categorical
marks that can be confused with each other.

### 5.2 Where the rest went

No chart. Four rows, same visual grammar as `SpendSubtotals` (label left in
`--ink-2`, amount right in `--ink` with `tabular-nums`), each with a count:

```
Paid off cards        91 · $X
Moved between accounts 82 · $X
Into investments      103 · $X
```

Followed by one line of prose: "Separated out so they don't count as
spending." That sentence is the whole point of the block.

### 5.3 Money in

One row, and the framing is load-bearing: **"at least $X in"** with the
footnote "Only deposits matching a known payroll signature count. Anything from
an account that isn't connected doesn't appear here." This is the SoFi hazard
surfaced honestly rather than as a number that looks complete. See open
question 3.

### 5.4 Tracked categories

The existing `SpendSubtotals` rows for delivery / rides / social, under a
heading that states containment explicitly: **"Of that, the things you're
tracking"**, with the share as a sentence, not a gauge: "Delivery, rides and
social are $X of the $Y above."

The existing 12-week stacked `SpendChart` moves here behind a `<details>`
disclosure ("Show tracked categories over time"), collapsed by default. This
keeps it available while preventing it from rendering simultaneously with the
spending chart — see §7 on why that matters.

### 5.5 Needs a decision

The triage worklist. §6.

### 5.6 Coverage footnote

§4's permanent footnote, plus last sync time echoed from `/api/settings`.

## 6. Triage — the 62 rows, and how a correction flows

### 6.1 What is in the queue, and why it is two queues

| Queue | Rows | The question | The choices |
|---|---|---|---|
| **Spent it, or moved it?** | 62 `ambiguous` | These read like transfers but were counted as spending | Spent it · Moved it · Paid a card · Saved / invested |
| **Where did this come from?** | 35 `inflow_unknown`, $3,620.62 | A deposit that isn't payroll | It's income · Moved from another account |

They are separated because they are different questions with different answer
sets, and merging them would produce a choice list where most options are
nonsense for most rows.

**The user never sees the word `flow`, or any of its six values.** The buttons
are plain-language; the mapping to `user_flow` happens in the frontend and is
unit-tested in `lib.ts`:

| Button | `user_flow` |
|---|---|
| Spent it | `spending` |
| Moved it | `transfer` |
| Paid a card | `card_payment` |
| Saved / invested | `investment` |
| It's income | `income` |
| Moved from another account | `transfer` |

"Spent it" writes `user_flow = 'spending'` even though `flow` is already
`spending` and the resolved value doesn't change. That is not a no-op: it is
the confirmation that removes the row from the queue (§6.4).

### 6.2 The row

Each row shows what the user needs to recognize the transaction and nothing
more: amount, date, payee (falling back to description when payee is empty —
Amex populates one and not the other), and the account name. Newest first,
capped at 50 per queue with a "N more" line, so an ignored queue never renders
a thousand-row list.

Answering a row removes it with the same settle animation the Today check-ins
use. No confirmation dialog; the correction is reversible by re-answering, and
a modal for a one-tap decision on a 62-item worklist is hostile.

### 6.3 Bulk

Under a row the user has just answered, offer — only when the derived signature
matches 2 or more other unanswered rows — a single line:

> Apply to the other 42 Venmo charges · $1,204.18

Tapping it writes the same `user_flow` to those rows in one request. It
persists **no rule**: a Venmo charge next week arrives in the queue again.
That is deliberate, not a shortcut — Venmo is genuinely both spending (paying a
friend back for dinner) and movement (moving your own money), so no standing
rule would be correct. What the bulk action actually buys is clearing a
one-time 90-day backlog; the steady-state queue is a handful of rows a week.

The signature derivation lives in `bank_flows.py` (pure, no DB, no I/O — the
same home as every other piece of transaction reasoning) as
`triage_signature(txn)`, returning a coarse counterparty token: `venmo`,
`zelle`, `cash app`, `apple cash`, `paypal`, `atm`, or `""` for no opinion.
An empty signature never offers bulk.

### 6.4 The correction path, and the trap in it

A correction is `POST /api/bank/transactions/{simplefin_id}/flow` →
`db.set_bank_flow_override` → `user_flow`. Every aggregate already reads
`COALESCE(user_flow, flow)`, so the number on the hero changes on the next
fetch with no other code involved. This is the Override + Learning Pattern
exactly as social events and rides implement it: separate nullable column,
resolved in SQL, sync never touches the user's column.

**The trap.** `ambiguous` is a *derived* column, recomputed from scratch on
every sync, and `bank_flows.is_ambiguous` takes `flow` — not `resolved_flow`.
A row the user has ruled on still classifies as `flow = 'spending'` next sync,
`is_ambiguous` still fires, and the row **reappears in the queue**. The queue
would be uncleanable: work it to zero on Monday, find it full again Tuesday.

The fix is in the query, not in `bank_flows`:

```sql
WHERE t.ambiguous AND t.user_flow IS NULL
```

This is right for three reasons beyond expedience. `bank_flows` stays pure and
deterministic, which is what lets the sync reclassify the whole table every run.
`ambiguous` keeps meaning what it means — "the text looks transfer-ish" — which
is a fact about the transaction, not about the user. And "has the user ruled on
this?" is exactly `user_flow IS NOT NULL`, so no new column is needed to track
queue state.

Re-answering a row is possible from a "Recently sorted" `<details>` beneath the
queue, which lists the last 20 rows with a non-null `user_flow` and lets the
user clear the override (`flow: null`), returning the row to the queue. Without
this a mis-tap is unrecoverable from the UI.

### 6.5 What triage does **not** do

It does not re-run pair matching, does not create a pair, and does not touch
the partner of a matched row. A user who marks one half of an unmatched
transfer as "Moved it" fixes that half only; the other half — if it exists at
all in a connected account — is its own queue row with its own answer. Trying
to be clever here (auto-pairing on override) would let a single mis-tap corrupt
`pair_id`, which is the one derived value whose corruption does not self-heal
(see `set_bank_transactions_derived_bulk`'s docstring).

## 7. Charts and color

One new mark type: the weekly bank spending series. It is single-series, so it
carries no categorical burden and needs no legend.

`--chart-bank` is added as an **alias of `--chart-delivery`'s already-validated
values** (light `oklch(50% 0.185 277)`, dark `oklch(63% 0.17 277)`) rather than
a new hue. Chart colors in this codebase are validated, not chosen, and
validating a fourth hue against both surfaces and all existing pairs is a
deliberate exercise, not something to slip into an implementation task.

The cost is a collision: the same hue would mean "all bank spending" in one
chart and "delivery" in another. §5.4 keeps the stacked category chart behind a
collapsed `<details>` so the two do not render together by default, and the
category chart keeps its mandatory legend. If the user opens the disclosure the
collision is live but disambiguated by two section headings and a legend. See
open question 5 — a properly validated fourth hue is the better answer if the
user wants to spend the validation effort.

Everything else on the screen is type and rules, no marks.

## 8. Out of scope

- **Budgets, spending targets, or any hit/miss on money.** §1.
- **MCC / merchant categorization.** The ingestion spec measured MCC at 26%
  coverage and *zero* on all four credit cards including the primary spender.
  Categorizing bank spending is description inference, a phase of its own.
- **A persisted triage rule engine.** §6.3.
- **Monthly / annual / custom ranges.** 12 weeks, fixed, matching Insights.
- **Per-merchant drilldown, search, or filtering.**
- **Editing amounts, dates, or descriptions.** Bank rows are a record of what
  happened; the only thing a user may change is the flow.
- **Balances, net worth, cash-flow projection.** Balances are deliberately not
  stored.
- **Connecting SoFi**, and therefore any claim that income is complete.
- **Column encryption for `description` / `payee`** — deferred by the
  ingestion spec §5, still deferred, still a separate reviewable change.
- **Any AI commentary on bank data.** §9.
- **Backfill beyond the 90-day window**, and any UI for
  `scripts/simplefin_snapshot.py` / `simplefin_backfill.py`.
- **Removing `/api/bank/debug`.** It stays: it is the verification surface for
  the sync job, and the UI is not a substitute for it.

## 9. Privacy and the outbound boundary

Bank data is not marked `private: True` — that flag is a `METRICS` concept and
bank data is not a metric (§1). The equivalent guarantee is stated as a rule
instead, because a future phase will otherwise reach for this data:

- **Bank text never reaches Claude.** `/api/reflection` builds its prompt from
  the scorecard card and noticings; bank data appears in neither, and no bank
  field may be added to either. Payee strings are merchant names, people's
  names, and P2P handles — the most identifying free text in the database.
- **Bank data never reaches Telegram.** `format_scorecard_text` renders
  `METRICS` only; bank data is not in `METRICS`, so this holds by construction
  today. It must be stated so it keeps holding.
- **No route returns the access URL**, and none returns balances (never
  stored). The new routes read only from `bank_accounts` / `bank_transactions`
  and never touch `config.SIMPLEFIN_ACCESS_URL`. Any error path uses
  `services/safe_status.py`'s closed set.

## 10. API surface

| Route | Returns |
|---|---|
| `GET /api/bank/summary?weeks=12` | `covered_from`, `covered_to`, `weeks: [{week_start, spending, partial}]`, `totals: {flow: {count, amount}}`, `spent`, `tracked` (the existing per-service rows for the same window), `triage_counts: {ambiguous, inflow_unknown}` |
| `GET /api/bank/triage?limit=50` | `{ambiguous: [row], inflow_unknown: [row], recent: [row]}` where a row is `{simplefin_id, posted, amount, payee, description, account_name, resolved_flow, user_flow, signature, signature_count, signature_amount}` |
| `POST /api/bank/transactions/{simplefin_id}/flow` | body `{flow}` or `{flow: null}` to clear. 400 on an unknown flow, 404 on an unknown id |
| `POST /api/bank/transactions/flow` | body `{simplefin_ids: [...], flow}`, capped at 200 ids. Returns `{updated: N}` |
| `GET /api/bank/accounts` | id, name, org, kind, role, active, last_synced_at — for the Settings roles editor. No balances |
| `POST /api/bank/accounts/{simplefin_id}/role` | exists already, unchanged |

`GET /api/bank/summary` is assembled in a new `app/money.py` — DB → domain
wiring, the same role `app/scorecard.py` plays. No SQL outside `database.py`.

## 11. Testing

- **pytest** — `app/money.py`: movement totals filter to the outflow side (a
  matched $1,000 transfer reports $1,000, not $2,000); aggregates key on
  `resolved_flow`, so setting `user_flow` moves a dollar from spending to
  transfer; weeks before `covered_from` are absent rather than zero; first and
  last weeks are flagged partial; empty table returns a shaped-but-empty
  payload rather than raising.
- **pytest** — triage query: a row with `ambiguous = true, user_flow = NULL`
  is in the queue; the same row after an override is **not**, including after a
  re-classification pass sets `ambiguous` back to true (this is §6.4's trap and
  is the single most important test in the feature); clearing the override
  returns it.
- **pytest** — routes: unknown flow → 400; unknown id → 404; bulk cap enforced;
  no response contains anything resembling the access URL.
- **pytest** — `bank_flows.triage_signature`: each hint family maps to its
  token; a PayPal card-network purchase yields `""` (it is suppressed from
  ambiguity in the first place); grouping counts only unanswered rows.
- **vitest** — `lib.ts`: the button → `user_flow` mapping table in §6.1; the
  coverage footnote sentence; partial-week labeling; the "of that, tracked"
  containment sentence with a real `$0` tracked total (null-check, not
  truthiness).
- Components are verified by `tsc --noEmit` + `vite build` + a manual look —
  there is no component test framework and this feature does not add one.

## 12. Resolved

Decided by the user on 2026-07-23, before implementation:

- **Money gets its own tab** (open question 1 → §3's recommended option). This
  knowingly reverses `2026-07-23-insights-tab-and-spend-subtotals-design.md`'s
  "folded in rather than shipped as a fifth tab". The reversal is licensed by
  three facts that post-date that decision: Money now carries a maintenance
  queue, account roles exist, and there are two spend totals where one contains
  the other. Option B is not being built.
- **ATM withdrawals triage as `spending`** (open question 2 → option (a)). No
  seventh flow, no schema change. Accepted cost: cash is counted when it leaves
  the machine, not when it is spent, so a single withdrawal lands entirely on
  its withdrawal date. Revisit only if cash volume grows enough that the timing
  distortion becomes visible.
- **Income is shown, framed as a floor** (open question 3 → show it). One
  figure, labelled "at least", because payroll splits with the unconnected SoFi
  account. If SoFi is ever connected, drop the framing rather than the number.

Open questions 4–6 (bulk-correction semantics, a fourth chart hue, whether the
Money surface is lock-flagged) remain at the spec's proposed defaults and are
not blocking; each is called out at its point of use.

- **Flow resolution stays in SQL.** `_BANK_TXN_SELECT` already exposes
  `resolved_flow`; no new resolution logic is written anywhere.
- **`/api/bank/debug` survives** this phase unchanged.
- **The sync interval is unchanged.** A role change or a triage decision does
  not trigger a sync; overrides take effect immediately (they bypass
  classification), role changes take effect on the next run, as the existing
  role route's docstring already says.
