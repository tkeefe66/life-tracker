# Triage Suggestions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:test-driven-development` for every backend task. Use `superpowers:subagent-driven-development` to work task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Queue rows arrive with an AI-suggested choice (learned from the user's confirmed answers) pre-highlighted for one-tap confirm.

**Spec:** `docs/superpowers/specs/2026-07-23-triage-suggestions-design.md` — the authority. All decisions closed.

**Architecture:** Established layering. `database.py`: new derived column + three functions. `ai_metrics.py`: one new `_call_json` task (the ONLY place bank text may reach Claude). `jobs/sync_bank.py`: a post-reclassify suggestion pass. Frontend: a chip treatment + one string helper. `bank_flows.py` and `app/money.py` logic untouched.

**Tech Stack:** Python 3, FastAPI, pytest; React + Vite, vitest.

## Global Constraints

- **`ai_metrics.MODEL` is untouched** (haiku). ONE `_call_json` call per batch of ≤40 rows, never per row; ≤3 batches per sync run.
- **`suggested_flow` is derived and advisory only.** No aggregate may read it — a test sets it on seeded rows and asserts `summary()` is byte-identical. Only the sync writes it; only for rows where it is NULL (one inference per row, ever).
- **`user_note` never reaches any AI.** The examples query does not select it; the prompt builder cannot receive it; a test asserts a sentinel note string does not appear in the built prompt.
- **Side-set validation before any write:** inflow rows (`amount > 0`) may only be suggested `income/transfer/refund`; outflow rows only `spending/transfer/card_payment/investment`. Invalid values and unknown ids are dropped, never written. The full allowed set for the column is the union of the two, enforced in `set_bank_suggestions_bulk`.
- **Quiet failure:** an AI exception in the suggestion pass is `logger.exception`-logged and MUST NOT change `bank_last_status` (the sync itself succeeded) or crash the job.
- **No green/red;** the suggested-chip treatment uses the `--accent-soft` token family; existing tokens only.
- **Baselines:** backend 448 (`./venv/bin/python -m pytest tests/ -q`); frontend 63 (`npm test -- --run`) + `npx tsc --noEmit` + `npm run build` clean. No commit with a failing check.

## File Structure

| File | Responsibility |
|---|---|
| `database.py` (modify) | `suggested_flow` column + migration; `_BANK_TXN_SELECT` gains it; `get_bank_flow_examples(limit)`, `get_bank_unsuggested_triage(limit)`, `set_bank_suggestions_bulk(mapping)` |
| `ai_metrics.py` (modify) | `suggest_bank_flows(rows, examples)` — prompt builder + `_call_json` + side-set validation |
| `jobs/sync_bank.py` (modify) | Post-reclassify suggestion pass (batch 40, max 3, quiet failure) |
| `frontend/src/lib.ts` (modify) | `suggestionHint(flow)` → `"looks like: Refunded"` etc. |
| `frontend/src/components/TriageQueue.tsx` (modify) | `.chip-suggested` treatment + hint line |
| `frontend/src/styles.css` (modify) | `.chip-suggested`, hint line styles |
| `CLAUDE.md` (modify) | The relaxed-and-hardened boundary |
| Tests | `tests/test_database_bank.py`, `tests/test_money.py` (invariant), `tests/test_ai_metrics.py`, `tests/test_sync_bank.py`, `frontend/src/lib.test.ts` |

---

### Task 1: Database — column, examples query, unsuggested query, bulk writer

**Files:** Modify `database.py`; test `tests/test_database_bank.py`, `tests/test_money.py`

**Interfaces:**
- Produces: `get_bank_flow_examples(limit=20)` → newest-first rows with `user_flow IS NOT NULL`: `{payee, description, side, user_flow}` where `side` is `"inflow"` if `amount > 0` else `"outflow"` — **no `user_note`, no amount, no dates, no account fields** (this list is exactly what may reach the model).
- `get_bank_unsuggested_triage(limit)` → queue rows (`(ambiguous AND user_flow IS NULL) OR (COALESCE(user_flow, flow) = 'inflow_unknown' AND user_flow IS NULL)`) with `suggested_flow IS NULL`, newest-posted first, tie-break `simplefin_id` ASC, capped.
- `set_bank_suggestions_bulk(mapping: dict[str, str])` → validates every value against the six-flow union BEFORE any write (`ValueError`, nothing written); all updates in one `_cursor(write=True)` block; only sets `suggested_flow`; unknown ids skipped; returns count updated.

- [ ] **Step 1: Failing tests.** In `tests/test_database_bank.py`: examples query returns only answered rows, newest first, capped, correct `side` both directions, and the returned dicts have EXACTLY the four keys (assert key set — this pins the never-send-notes contract at the source). Unsuggested query: excludes rows with a suggestion, excludes answered rows, includes both bucket kinds, ordering + cap. Bulk writer: writes both rows and returns 2; unknown id skipped; `ValueError` on a value outside the union with NOTHING written; wrote value visible as `suggested_flow` on `_BANK_TXN_SELECT` rows. In `tests/test_money.py`: seed the summary fixtures, snapshot `summary(12)`, set `suggested_flow` on every row, assert the new `summary(12)` compares equal — the aggregates-ignore-suggestions invariant.
- [ ] **Step 2: Run, watch fail** for the right reasons (AttributeError on the three functions; KeyError on `suggested_flow`).
- [ ] **Step 3: Implement.** Column + migration per `_init_v2_tables()` pattern (both engines); `t.suggested_flow` into `_BANK_TXN_SELECT`/`_bank_txn_rows`; the three functions beside the other bank helpers. Document on `set_bank_suggestions_bulk` that it is a DERIVED write the sync owns, and that the union-set validation is what keeps a bad model output out of the DB.
- [ ] **Step 4: Verify** — full pytest green (448 + new).
- [ ] **Step 5: Commit** — `feat(db): suggested_flow column, examples and unsuggested queries, bulk writer`

---

### Task 2: `ai_metrics.suggest_bank_flows`

**Files:** Modify `ai_metrics.py`; test `tests/test_ai_metrics.py`

**Interfaces:**
- Consumes: rows shaped `{simplefin_id, payee, description, side}`; examples from Task 1's query.
- Produces: `suggest_bank_flows(rows, examples) -> dict[str, str]` — only validated suggestions; abstentions and invalid outputs absent from the result.

- [ ] **Step 1: Failing tests** (monkeypatch `_call_json` like the module's existing tests do): model returns a valid mapping → passed through; an inflow row suggested `"spending"` → dropped; an unknown id → dropped; `null` value → dropped (abstain); model returns a non-dict → `{}` (mirror `_call_json`'s existing hardening expectations); **prompt-content test:** build the prompt with a row whose dict ALSO carries `user_note: "SENTINEL-NOTE-TEXT"` and an example carrying the same — assert the sentinel does not appear in the prompt string handed to `_call_json` (the builder must select fields explicitly, never serialize whole dicts).
- [ ] **Step 2: Run, watch fail** (`AttributeError: suggest_bank_flows`).
- [ ] **Step 3: Implement** with the module's `_call_json()` pattern and existing `MODEL`. Prompt: plain instruction (classify each deposit/charge into that side's listed choices using the user's past answers as examples; answer `null` when unsure — a wrong suggestion is worse than none), examples block (payee/description/side → flow), rows block (id/payee/description/side), JSON-object output. Validation: per-row side sets (`{"income","transfer","refund"}` / `{"spending","transfer","card_payment","investment"}`), drop everything else.
- [ ] **Step 4: Verify** — full pytest green.
- [ ] **Step 5: Commit** — `feat(ai): batched flow suggestions from the user's confirmed answers`

---

### Task 3: Sync hook

**Files:** Modify `jobs/sync_bank.py`; test `tests/test_sync_bank.py`

- [ ] **Step 1: Failing tests** (follow the file's existing payload/monkeypatch conventions): after a sync, unsuggested queue rows got suggestions written (monkeypatch `ai_metrics.suggest_bank_flows` to a canned mapping); rows that already had `suggested_flow` are NOT in the rows passed to the AI (assert on the captured call args — the no-double-spend guarantee); batching: 100 unsuggested rows → ≤3 calls of ≤40 (capture call count/sizes); AI raising → sync completes, `bank_last_status` unchanged from what the sync earned, nothing written; zero unsuggested rows → zero AI calls.
- [ ] **Step 2: Run, watch fail.**
- [ ] **Step 3: Implement** after the reclassify/derived-write step: loop `db.get_bank_unsuggested_triage(40)` up to 3 iterations (stop early when empty or when a batch returns no writable suggestions — every batch re-queries, so written suggestions fall out of the next query naturally; guard against an all-abstain batch looping on the same rows by breaking when `set_bank_suggestions_bulk` wrote 0), each batch: `examples = db.get_bank_flow_examples()`, `mapping = ai_metrics.suggest_bank_flows(batch, examples)`, `db.set_bank_suggestions_bulk(mapping)`. The whole pass wrapped in its own try/except: `logger.exception`, no status change, no raise.
- [ ] **Step 4: Verify** — full pytest green.
- [ ] **Step 5: Commit** — `feat(jobs): suggestion pass after each bank sync`

---

### Task 4: Frontend — hint helper, suggested chip

**Files:** Modify `frontend/src/lib.ts`, `frontend/src/lib.test.ts`, `frontend/src/components/TriageQueue.tsx`, `frontend/src/styles.css`

- [ ] **Step 1: Failing vitest cases:** `suggestionHint("refund") === "looks like: Refunded"` (reuses `flowLabel`); `suggestionHint("spending") === "looks like: Spent it"`? — NO: use `flowLabel` verbatim for consistency (`"looks like: Refunded"`, `"looks like: Moved between accounts"`); an unknown flow → `""` (no hint rather than a raw token); `null`/`undefined` → `""`.
- [ ] **Step 2: Run, watch fail; implement in `lib.ts`; vitest green.**
- [ ] **Step 3: `TriageQueue.tsx`.** `TriageRow` gains `suggested_flow?: string | null`. When a row is unanswered and `suggestionHint(row.suggested_flow)` is non-empty: the chip whose `choice.flow === row.suggested_flow` gets class `chip-suggested`, and the hint renders as a small muted line in the row meta. If no chip in THIS queue's choices matches the suggested flow (defensive — side sets should prevent it), show nothing. Tap behavior unchanged.
- [ ] **Step 4: Styles.** `.chip-suggested { border-color / background from --accent-soft family }` + a `.triage-hint` muted line — existing tokens only, both themes already covered by the tokens themselves.
- [ ] **Step 5: Verify** — `npm test -- --run` (63 + new), `npx tsc --noEmit`, `npm run build` all clean. (`Money.tsx` needs no change: rows pass through whole.) Confirm that with tsc — if its local row type needs the field, add the optional field there too and note it.
- [ ] **Step 6: Commit** — `feat(frontend): suggested-choice highlight on triage rows`

---

### Task 5: CLAUDE.md

**Files:** `CLAUDE.md` (modify)

- [ ] **Step 1:** Rewrite the outbound-boundary bullet: bank payee/description may reach Claude in `ai_metrics.suggest_bank_flows` ONLY (same footing as Gmail subjects and calendar titles); `/api/reflection` and Telegram stay bank-free; `user_note` never reaches any AI, enforced by a prompt-content test.
- [ ] **Step 2:** `bank_transactions` table row: add `suggested_flow` (derived, advisory, sync-written, ignored by every aggregate).
- [ ] **Step 3:** `./venv/bin/python -m pytest tests/ -q` once; commit `docs: triage suggestions and the revised bank-text boundary`.

---

## Final verification

- [ ] Full backend + frontend suites, tsc, build — all green at HEAD.
- [ ] Grep branch diff: no `str(e` additions; no `SIMPLEFIN_ACCESS_URL`; no `MODEL =` change in `ai_metrics.py`.
- [ ] Grep `app/money.py` and `app/scorecard.py` for `suggested_flow` — zero hits.
- [ ] Hand-walk on local SQLite: sync with canned AI (monkeypatch) → suggestions land on queue rows only; answer a suggested row → leaves queue normally; `summary()` unchanged by suggestions.
