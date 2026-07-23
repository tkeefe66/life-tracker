# Refunds and Triage Notes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:test-driven-development` for every backend task — write the failing test, watch it fail for the right reason, then implement. Use `superpowers:subagent-driven-development` to work task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a user-only `refund` flow (netted out of spending) and an optional per-transaction note to the triage queues.

**Spec:** `docs/superpowers/specs/2026-07-23-refund-and-triage-notes-design.md` — the authority on behavior. All decisions are made; there are no open questions.

**Architecture:** Same layering as the Money UI. `database.py` owns the new column and validation; `app/money.py` owns the netting math; routes stay thin; `lib.ts` owns the button table and strings; components render. `bank_flows.py` is untouched — `refund` is a user-only verdict that exists exclusively in `user_flow`.

**Tech Stack:** Python 3, FastAPI, pytest; React + Vite, vitest.

## Global Constraints

- **`bank_flows.py` is untouched.** `classify_flow` must never return `"refund"` — a test pins this.
- **`database.py` is the only place with SQL**; both engines (SQLite tests, Postgres prod); migration per the `_init_v2_tables()` pattern.
- **`user_note` is a user column** — the sync never reads or writes it (Override + Learning rule 3).
- **Netting math:** `spent = sum(abs(amount): resolved_flow='spending', amount<0) − sum(amount: resolved_flow='refund', amount>0)`; weekly buckets net within the posted week; round once, at the end.
- **`totals.refund` sums the positive side only** — the mirror of the movement flows' outflow-side rule. A `refund` verdict on a negative-amount row is inert (contributes to nothing) and not special-cased.
- **Note semantics:** trimmed; explicit `""` → NULL (clears); omitted key → stored note untouched (put-back keeps the note); >500 chars → 400 with nothing written; a `note` key in the BULK body → 400.
- **Boundary:** `user_note` is bank text — never reaches `/api/reflection` or Telegram. No new read of `config.SIMPLEFIN_ACCESS_URL`.
- **No green/red, no metric semantics.** Hero sub-line "after $X refunded" only when refunds exist. Money via `money()`, null-checked; negative amounts format as `−$12.50` (U+2212 minus before the $).
- **Baselines:** backend `./venv/bin/python -m pytest tests/ -q` → **427 passing**; frontend `npm test -- --run` → **62 passing**, then `npm run build` + `npx tsc --noEmit` clean. No commit with a failing check.

## File Structure

| File | Responsibility |
|---|---|
| `database.py` (modify) | `refund` in `BANK_FLOWS`; `user_note` column + migration; `_BANK_TXN_SELECT` + note-aware override writer |
| `tests/test_bank_flows.py` (modify) | Pin `classify_flow` never returns `refund` |
| `app/money.py` (modify) | Netting in `summary`; `user_note` passes through `triage` rows |
| `app/routes.py` (modify) | `note` on `FlowPatch` (tri-state), 500 cap, bulk rejects note |
| `frontend/src/lib.ts` (modify) | `TRIAGE_CHOICES.inflow` + `flowLabel("refund")` + negative `weekCaption` |
| `frontend/src/components/TriageQueue.tsx` (modify) | Note input + note display |
| `frontend/src/components/BankSpendChart.tsx` (modify) | Clamp negative week bars to zero height |
| `frontend/src/screens/Money.tsx` (modify) | Hero sub-line; thread `note` through answers; notes in Recently sorted |
| `CLAUDE.md` (modify) | Record the new flow + user column |

---

### Task 1: Database — `refund` flow, `user_note` column, note-aware writer

**Files:**
- Modify: `database.py` (bank section)
- Test: `tests/test_database_bank.py`, `tests/test_bank_flows.py`

**Interfaces:**
- Produces: `BANK_FLOWS` including `"refund"`; `set_bank_flow_override(simplefin_id, user_flow, note=_NOTE_UNSET)` — omitted `note` leaves the stored note untouched; a string trims then writes (`""` → NULL); returns True iff a row was updated, as today. Every `_BANK_TXN_SELECT` row dict gains `user_note`.

- [ ] **Step 1: Write the failing tests** in `tests/test_database_bank.py`:
  - `set_bank_flow_override(id, "refund")` succeeds and `resolved_flow` becomes `"refund"` (proves `BANK_FLOWS` accepts it).
  - Write flow + note together: `set_bank_flow_override(id, "refund", note="  Amex return — shoes  ")` stores the trimmed `"Amex return — shoes"`, readable via `user_note` on a triage/range row.
  - Omitted note preserves: after storing a note, `set_bank_flow_override(id, None)` (put-back) leaves `user_note` unchanged.
  - Explicit clear: `set_bank_flow_override(id, "income", note="")` sets `user_note` to NULL.
  - `user_note` key present (None) on rows that never had a note.
  In `tests/test_bank_flows.py`:
  - `classify_flow` never returns `"refund"`: iterate a representative grid (roles spending/credit_card/savings/investment/unknown × paired/unpaired × positive/negative amount × income-hint match) and assert `"refund"` is not produced. This is the guard that keeps refund user-only.
- [ ] **Step 2: Run, watch them fail** — `AttributeError`/`ValueError: unknown flow: refund` / missing `user_note` key, each for the right reason.
- [ ] **Step 3: Implement.** Add `"refund"` to `BANK_FLOWS`. Add `user_note TEXT` to the `bank_transactions` CREATE TABLE **and** the migration block in `_init_v2_tables()` (Postgres `ALTER TABLE … ADD COLUMN IF NOT EXISTS`; SQLite `PRAGMA table_info` guard — copy the existing pattern in that function). Add `t.user_note` to `_BANK_TXN_SELECT` and `_bank_txn_rows`. Extend `set_bank_flow_override` with a module-level `_NOTE_UNSET = object()` sentinel default; when a string arrives, `note = note.strip() or None`, and write flow and note in the same `_cursor(write=True)` UPDATE. Document in the docstring that omitting `note` is "don't touch", `""` is "clear".
- [ ] **Step 4: Verify** — full pytest green (427 + new).
- [ ] **Step 5: Commit** — `feat(db): refund flow value and user_note column`

---

### Task 2: `app/money.py` — refund netting

**Files:**
- Modify: `app/money.py`
- Test: `tests/test_money.py`

**Interfaces:**
- Consumes: rows carrying `resolved_flow` (may now be `"refund"`) and `user_note`.
- Produces: `summary(weeks)` where `spent` and each week's `spending` are netted; `totals["refund"] = {count, amount}` (positive side); `triage(limit)` rows carry `user_note`.

- [ ] **Step 1: Write the failing tests** in `tests/test_money.py`:
  - A $100 spending outflow plus a +$30 row with `user_flow="refund"` → `spent == 70.0`; `totals["refund"] == {"count": 1, "amount": 30.0}`.
  - Both in the same week → that week's `spending == 70.0`; refund posted in a *different* week nets that week, not the spending week.
  - A week whose refunds exceed its spending reports the true negative net (e.g. `-30.0`) — the backend does not floor; flooring is presentation (Task 6).
  - Inert mis-tap: a negative-amount row with `user_flow="refund"` contributes to neither `spent` nor `totals["refund"]` (count 0 if it's the only one).
  - `triage()` rows (both buckets and `recent`) carry `user_note`, populated when set.
  - Round-once: values that would drift under double rounding come out right (mirror the existing rounding test's style).
- [ ] **Step 2: Run, watch fail.**
- [ ] **Step 3: Implement.** In `_totals`, add the refund branch: `amount > 0` filter, sum positive amounts. Compute `spent = spending_total - refund_total`, rounded once at the end. In the weekly loop, per week: `net = sum(abs spending outflows) - sum(refund inflows)`, rounded once. `triage` needs no change beyond the row dict already carrying `user_note` from Task 1 — assert, don't re-derive.
- [ ] **Step 4: Verify** — full pytest green.
- [ ] **Step 5: Commit** — `feat(api): refunds net out of spending`

---

### Task 3: Routes — note on the single-row flow route

**Files:**
- Modify: `app/routes.py`
- Test: `tests/test_api_routes.py`

**Interfaces:**
- Consumes: Task 1's `set_bank_flow_override(id, flow, note=…)` sentinel contract.
- Produces: `POST /api/bank/transactions/{id}/flow` body `{flow, note?}`; bulk route unchanged except rejecting `note`.

- [ ] **Step 1: Write the failing tests:**
  - `{"flow": "refund"}` → 200 (refund is a valid verdict at the API).
  - `{"flow": "income", "note": " why "}` → 200; a follow-up `GET /api/bank/triage` shows the row in `recent` with `user_note == "why"`.
  - `{"flow": null}` (put-back) after a noted answer → note still present on the row.
  - `{"flow": "income", "note": ""}` → clears the note.
  - `note` of 501 chars → 400, and the row's flow AND note are unchanged (assert both).
  - Bulk body `{"simplefin_ids": [...], "flow": "transfer", "note": "x"}` → 400, nothing written.
- [ ] **Step 2: Run, watch fail.**
- [ ] **Step 3: Implement.** `FlowPatch` gains `note: str | None = None`; distinguish omitted-vs-provided via `"note" in body.model_fields_set` — omitted passes the sentinel default through (call `db.set_bank_flow_override(id, flow)` without the kwarg), provided passes the string. Length check (`len(note) > 500` → `HTTPException(400, "note too long (max 500 chars)")`) BEFORE any write. `BulkFlowPatch` gains `note: str | None = None` solely to 400 when set (`HTTPException(400, "bulk apply does not take a note")`). Treat `note: null` in the single-row body as omitted (leave untouched) — document in the model docstring.
- [ ] **Step 4: Verify** — full pytest green.
- [ ] **Step 5: Commit** — `feat(api): optional note rides with a triage answer`

---

### Task 4: `lib.ts` — refund choice, label, negative captions

**Files:**
- Modify: `frontend/src/lib.ts`, `frontend/src/lib.test.ts`

**Interfaces:**
- Produces: `TRIAGE_CHOICES.inflow` of exactly `[{label:"It's income",flow:"income"},{label:"Moved from another account",flow:"transfer"},{label:"Refunded",flow:"refund"}]`; `flowLabel("refund") === "Refunded"`; `weekCaption` with negative spending.

- [ ] **Step 1: Write the failing vitest cases:**
  - The exact inflow table above (order matters; Refunded is third). Outflow table unchanged — re-assert it verbatim so a slip fails loudly.
  - `flowLabel("refund") === "Refunded"`.
  - `weekCaption({week_start:"2026-07-13", spending:-12.5, partial:false})` → `"Jul 13–19 · −$12.50"` — U+2212 minus, then `$`, then the `money()` magnitude; `spending: 0` still renders `"$0"`.
- [ ] **Step 2: Run, watch fail** — `cd frontend && npm test -- --run`.
- [ ] **Step 3: Implement.** Append the choice and label. In `weekCaption`, format the amount as `spending < 0 ? "−" + money(Math.abs(spending)) : money(spending)` — null-check stays, truthiness stays banned.
- [ ] **Step 4: Verify** — vitest green (62 + new).
- [ ] **Step 5: Commit** — `feat(frontend): refund choice and negative week captions`

---

### Task 5: `TriageQueue` note input + display; chart clamp

**Files:**
- Modify: `frontend/src/components/TriageQueue.tsx`, `frontend/src/components/BankSpendChart.tsx`, `frontend/src/styles.css`

Verified by `npx tsc --noEmit` + `npm run build` (no component framework).

- [ ] **Step 1: `TriageQueue.tsx`.** `onAnswer` prop becomes `(id: string, flow: string, note?: string) => void`. Each row gains a quiet "Add note" toggle (`.quiet-btn` style, right of the row meta); tapping expands a single `<input type="text" maxLength={500}>` (`.triage-note-input`, existing tokens only) whose value is held in per-row local state and passed as the third argument when a choice is tapped. Rows whose `user_note` is set render it muted (`--muted`, small) beneath the label — in the queues and via the same row markup the parent reuses for Recently sorted. Empty input → pass `undefined`, not `""` (an accidental clear must be impossible from this surface).
- [ ] **Step 2: `BankSpendChart.tsx`.** Bar height computes from `Math.max(0, w.spending)`; the `<title>` and the selection callback keep the true value so the caption (Task 4's negative format) tells the truth. No other geometry changes.
- [ ] **Step 3: Styles** — `.triage-note-input` and the muted note line, existing tokens only, no new colors.
- [ ] **Step 4: Verify** — `npx tsc --noEmit && npm run build` clean, `npm test -- --run` green (Money.tsx still compiles because Task 6 lands before this commits — see order note below). If tsc fails on the changed `onAnswer` arity in `Money.tsx`, that is expected ONLY if Task 6 has not landed; in the subagent flow Tasks 5 and 6 are separate commits, so make the third argument optional (`note?`) precisely so existing callers stay valid — tsc must be clean at THIS commit on its own.
- [ ] **Step 5: Commit** — `feat(frontend): triage note input and refund-safe chart floor`

---

### Task 6: Money screen wiring

**Files:**
- Modify: `frontend/src/screens/Money.tsx`

- [ ] **Step 1: Hero sub-line.** When `summary.totals.refund?.amount > 0`, render beneath `.money-hero-sub`: `after {money(summary.totals.refund.amount)} refunded` (a second `.money-hero-sub`-styled line; null-check, never truthiness — but a real $0 refund total means the line is omitted, which the `> 0` guard does correctly and intentionally).
- [ ] **Step 2: Thread notes.** `handleAnswer(row, flow, note?)` includes `note` in the POST body only when defined (omit the key otherwise — the API treats omitted as don't-touch). `toRecentRow` carries `user_note` (from the answered row plus the just-typed note when provided) so an optimistically-prepended Recently-sorted row shows its note without a refetch.
- [ ] **Step 3: Recently sorted rows** render `user_note` muted beneath the label (same treatment as the queues). "Put it back" body stays `{flow: null}` — no note key, stored note survives (already guaranteed server-side).
- [ ] **Step 4: Verify** — `npx tsc --noEmit && npm run build && npm test -- --run` all clean.
- [ ] **Step 5: Commit** — `feat(frontend): refund sub-line and notes through the money screen`

---

### Task 7: CLAUDE.md

**Files:** `CLAUDE.md` (modify)

- [ ] **Step 1:** In the `bank_transactions` table row of the Database section, add `user_note` to the listed user-set columns and note `refund` as a seventh flow that only `user_flow` can hold (the classifier never emits it).
- [ ] **Step 2:** In the Code Conventions outbound-boundary bullet for bank data, extend "bank text" to name `user_note` explicitly.
- [ ] **Step 3:** Run `./venv/bin/python -m pytest tests/ -q` once (docs-only change; prove nothing broke).
- [ ] **Step 4: Commit** — `docs: refund flow and triage notes in the repo guide`

---

## Final verification

- [ ] `./venv/bin/python -m pytest tests/ -q` — 427 baseline + all new, green.
- [ ] `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build` — 62 baseline + new, clean.
- [ ] Grep the branch diff for `str(e` and `SIMPLEFIN_ACCESS_URL` — both empty.
- [ ] Hand-walk against local SQLite: answer an inflow row as Refunded with a note → `summary` nets it; put it back → note survives; re-answer → queue behavior unchanged. Mirror the existing trap-walk script's setup.
