# Money Label Auto-Apply — Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Labels propagate: labeling one recurring transaction offers "apply
to N more of this vendor", and every sync suggests labels for new rows whose
vendor the user has already labeled — rule-based (same-vendor inheritance),
no AI.

**Architecture:** A derived `suggested_label` column, recomputed for the
WHOLE table on every sync by a pure `bank_flows.label_suggestions()` pass
(mirroring the flow reclassify's full-recompute philosophy, NOT
`suggested_flow`'s write-once) — a stale suggestion self-heals the next run.
Resolution in SQL: `COALESCE(user_label, suggested_label) AS resolved_label`;
the Labels view now groups on `resolved_label`, per the spec's Phase 3
design. Bulk apply is a second mode of the existing `POST /bank/label`
route. The vendor-key rule (`payee` falling back to `description`) moves to
`bank_flows.vendor_key` as the single source of truth.

**Tech Stack:** FastAPI + pytest (SQLite path), React + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/2026-07-23-money-vendor-breakdown-design.md`
(Phase 3). Deviation from the spec's data-model note: the sync recomputes
suggestions for the whole table each run instead of only suggesting on new
upserts — strictly more self-healing, same user-facing behavior, and it means
removing/renaming labels also retires their suggestions.

## Global Constraints

- `suggested_label` is DERIVED: only the sync writes it, never a route;
  the sync NEVER writes `user_label`. `COALESCE(user_label, suggested_label)
  AS resolved_label` is added to `_BANK_TXN_SELECT` — resolution lives in
  SQL, never per-call-site Python.
- Inheritance rule: a vendor's rows inherit a suggestion only when ALL of
  that vendor's user labels agree (unanimous). Any conflict → no suggestion
  for that vendor. Rows whose vendor has no user labels → suggestion NULL.
  Rows that HAVE a `user_label` → suggestion NULL (nothing to suggest).
- Vendor key everywhere = `bank_flows.vendor_key(t)` = `payee or
  description`; in SQL, `CASE WHEN payee != '' THEN payee ELSE description
  END`.
- Bulk apply targets only `user_label IS NULL` rows of the vendor; it writes
  `user_label` (a user action — the offer button is the confirmation).
  Bulk clearing is not a thing: bulk mode requires a non-empty label.
- The suggestion pass runs in the sync's enhancement slot (own try/except
  after `_suggest_triage_flows()`) — a failure logs and never overwrites the
  sync's earned status. No Claude call, no new env var.
- The label-view aggregation and label drill-down switch from `user_label`
  to `resolved_label` (spec Phase 3). The vocabulary stays USER labels only
  (`get_bank_label_vocabulary` unchanged).
- Secondary surface fails quietly; existing tokens only; money via
  `money()`/`signedMoney()`.
- Baselines before Task 1: backend 496 passed (project venv at
  `/Users/tomkeefe/Code Apps/weekly-updates/venv` — system Python lacks
  apscheduler), frontend 73 passed, tsc clean, build green.
- Work ONLY in the worktree `.claude/worktrees/money-label-autoapply`
  (branch `worktree-money-label-autoapply`). The parent directory is another
  agent's checkout — never edit, test, or commit there.

---

### Task 1: `bank_flows.vendor_key` + `label_suggestions` (pure)

**Files:**
- Modify: `bank_flows.py` (two new pure functions, no DB/network — same
  contract as the rest of the module)
- Modify: `app/money.py` (its `_vendor_key` delegates to the new function)
- Test: `tests/test_bank_flows.py` (append; mirror the file's conventions)

**Interfaces:**
- Produces: `bank_flows.vendor_key(txn) -> str` (`payee or description`);
  `bank_flows.label_suggestions(txns) -> dict[str, str | None]` mapping
  EVERY txn's `simplefin_id` to its suggested label (or None). Txns are
  dicts carrying at least `simplefin_id`, `payee`, `description`,
  `user_label`.

- [ ] **Step 1: Write the failing tests** — append to
  `tests/test_bank_flows.py`:

```python
# ── vendor_key + label_suggestions: same-vendor label inheritance ──────────────

def _ltxn(sfid, payee, user_label=None, description="RAW"):
    return {"simplefin_id": sfid, "payee": payee, "description": description,
            "user_label": user_label}


def test_vendor_key_prefers_payee_falls_back_to_description():
    import bank_flows
    assert bank_flows.vendor_key(_ltxn("a", "Amazon")) == "Amazon"
    assert bank_flows.vendor_key(_ltxn("b", "", description="CHECK 1042")) == "CHECK 1042"


def test_label_suggestions_unanimous_vendor_propagates():
    import bank_flows
    txns = [
        _ltxn("r1", "Check", user_label="Monthly Rent"),
        _ltxn("r2", "Check"),
        _ltxn("x1", "Cafe"),
    ]
    assert bank_flows.label_suggestions(txns) == {
        "r1": None,          # already user-labeled — nothing to suggest
        "r2": "Monthly Rent",
        "x1": None,          # vendor has no user labels
    }


def test_label_suggestions_conflicting_vendor_stays_silent():
    import bank_flows
    txns = [
        _ltxn("a1", "Amazon", user_label="Household"),
        _ltxn("a2", "Amazon", user_label="Gifts"),
        _ltxn("a3", "Amazon"),
    ]
    assert bank_flows.label_suggestions(txns)["a3"] is None


def test_label_suggestions_uses_vendor_key_fallback():
    import bank_flows
    txns = [
        _ltxn("c1", "", user_label="Monthly Rent", description="CHECK 1042"),
        _ltxn("c2", "", description="CHECK 1042"),
    ]
    assert bank_flows.label_suggestions(txns)["c2"] == "Monthly Rent"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bank_flows.py -v -k "vendor_key or label_sugg"`
Expected: FAIL — no attribute `vendor_key`.

- [ ] **Step 3: Implement** — in `bank_flows.py` (placement: near the other
  small pure helpers; docstring style per the module):

```python
def vendor_key(txn) -> str:
    """The vendor identity used everywhere labels and the breakdown group:
    payee, falling back to description when the bridge sent no payee."""
    return txn.get("payee") or txn.get("description") or ""


def label_suggestions(txns):
    """Same-vendor label inheritance, computed from scratch each sync (like
    classify_all, unlike suggested_flow's write-once): a vendor's unlabeled
    rows inherit its user label only when every user label on that vendor
    agrees — any conflict and the vendor stays silent. Returns a suggestion
    (or None) for EVERY row, so writing the result also retires stale
    suggestions whose vendor lost its labels."""
    labels_by_vendor = {}
    for t in txns:
        if t.get("user_label"):
            labels_by_vendor.setdefault(vendor_key(t), set()).add(t["user_label"])
    unanimous = {v: next(iter(ls)) for v, ls in labels_by_vendor.items() if len(ls) == 1}
    return {
        t["simplefin_id"]: (None if t.get("user_label")
                            else unanimous.get(vendor_key(t)))
        for t in txns
    }
```

In `app/money.py`, replace `_vendor_key`'s body with a delegation (imports
already include `bank_flows`):

```python
def _vendor_key(t: dict) -> str:
    return bank_flows.vendor_key(t)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bank_flows.py tests/test_money.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bank_flows.py app/money.py tests/test_bank_flows.py
git commit -m "feat(bank): vendor_key and pure same-vendor label suggestions"
```

---

### Task 2: DB — `suggested_label` column, `resolved_label`, bulk writers

**Files:**
- Modify: `database.py` (migration after the `user_label` block;
  `_BANK_TXN_SELECT`; three new functions near `set_bank_label`)
- Test: `tests/test_database_bank.py` (append)

**Interfaces:**
- Produces: `db.set_bank_label_suggestions_bulk(mapping) -> int` (one
  transaction; values are labels or None; unknown ids skipped; only-if-
  changed is NOT required — plain UPDATE per row is fine);
  `db.set_bank_labels_by_vendor(vendor, label) -> int` (writes `user_label`
  on the vendor's `user_label IS NULL` rows, returns count);
  `db.count_bank_unlabeled_by_vendor(vendor) -> int`;
  rows gain `suggested_label` and `resolved_label` keys.

- [ ] **Step 1: Write the failing tests** — append to
  `tests/test_database_bank.py` (reuse its `_seed_account`/`_seed_txn`
  helpers from Phase 2):

```python
# ── suggested_label: derived write, resolution, vendor bulk ────────────────────

def test_label_suggestions_bulk_write_and_resolved_label(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    _seed_txn(db, "t1", acct_id, "2026-07-20", -50.0)
    _seed_txn(db, "t2", acct_id, "2026-07-20", -60.0)
    db.set_bank_label("t2", "Groceries")

    written = db.set_bank_label_suggestions_bulk({"t1": "Groceries", "t2": None, "ghost": "X"})
    assert written == 2                     # unknown id skipped, not an error

    rows = {t["simplefin_id"]: t for t in db.get_all_bank_transactions()}
    assert rows["t1"]["suggested_label"] == "Groceries"
    assert rows["t1"]["user_label"] is None
    assert rows["t1"]["resolved_label"] == "Groceries"     # suggestion shows
    assert rows["t2"]["resolved_label"] == "Groceries"     # user label wins
    assert rows["t2"]["suggested_label"] is None


def test_user_label_beats_suggested_in_resolved(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    _seed_txn(db, "t1", acct_id, "2026-07-20", -50.0)
    db.set_bank_label_suggestions_bulk({"t1": "Groceries"})
    db.set_bank_label("t1", "Household")
    row = next(t for t in db.get_all_bank_transactions() if t["simplefin_id"] == "t1")
    assert row["resolved_label"] == "Household"


def test_set_bank_labels_by_vendor_skips_user_labeled_rows(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    for sfid in ("a1", "a2", "a3"):
        db.upsert_bank_transaction(sfid, acct_id, "2026-07-20", "2026-07-20",
                                   -10.0, "RAW", "Amazon", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, False)
    db.set_bank_label("a1", "Gifts")

    assert db.count_bank_unlabeled_by_vendor("Amazon") == 2
    applied = db.set_bank_labels_by_vendor("Amazon", "Household")
    assert applied == 2
    rows = {t["simplefin_id"]: t["user_label"] for t in db.get_all_bank_transactions()}
    assert rows == {"a1": "Gifts", "a2": "Household", "a3": "Household"}


def test_vendor_bulk_uses_description_fallback_for_empty_payee(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    db.upsert_bank_transaction("c1", acct_id, "2026-07-20", "2026-07-20",
                               -900.0, "CHECK 1042", "", "", None)
    db.set_bank_transaction_derived("c1", "spending", None, False)

    assert db.count_bank_unlabeled_by_vendor("CHECK 1042") == 1
    assert db.set_bank_labels_by_vendor("CHECK 1042", "Monthly Rent") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_database_bank.py -v -k "suggest or vendor"`
Expected: FAIL.

- [ ] **Step 3: Implement** — four edits to `database.py`:

(a) Migration after the `user_label` block (same dual-engine shape), plus
`suggested_label TEXT,` in the `CREATE TABLE`:

```python
        # suggested_label: nullable TEXT on bank_transactions. DERIVED — the
        # sync's label-suggestion pass owns it exclusively and recomputes it
        # for the whole table every run (unlike suggested_flow's write-once);
        # never written by a route, never touches user_label.
        if USE_POSTGRES:
            c.execute("ALTER TABLE bank_transactions ADD COLUMN IF NOT EXISTS suggested_label TEXT")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(bank_transactions)").fetchall()]
            if "suggested_label" not in cols:
                c.execute("ALTER TABLE bank_transactions ADD COLUMN suggested_label TEXT")
```

(b) `_BANK_TXN_SELECT`: add `t.suggested_label` and
`COALESCE(t.user_label, t.suggested_label) AS resolved_label` (next to the
existing `resolved_flow` COALESCE).

(c) The derived bulk writer (model on `set_bank_suggestions_bulk`, minus
the closed-set validation — labels are free text; the values come from the
user's own labels via the pure pass, not from a model):

```python
def set_bank_label_suggestions_bulk(mapping):
    """Write `suggested_label` for many rows in ONE transaction. DERIVED —
    only the sync's label pass calls this (jobs/sync_bank.py), with values
    that are the user's own labels propagated by bank_flows.label_suggestions;
    never touches user_label. None retires a stale suggestion. Unknown ids
    skipped. Returns rows actually updated."""
    p = _p()
    updated = 0
    with _cursor(write=True) as c:
        for simplefin_id, label in mapping.items():
            c.execute(f"UPDATE bank_transactions SET suggested_label = {p} WHERE simplefin_id = {p}",
                      (label, simplefin_id))
            updated += c.rowcount
    return updated
```

(d) The vendor-key bulk apply + count (SQL vendor key = the CASE
expression; a USER write, called by the label route's bulk mode):

```python
_VENDOR_KEY_SQL = "CASE WHEN payee != '' THEN payee ELSE description END"


def set_bank_labels_by_vendor(vendor, label):
    """Bulk USER apply: label every un-user-labeled row of one vendor.
    The route's bulk mode calls this after the user taps the explicit
    'apply to N more' offer — the tap is the confirmation. Never touches
    rows that already carry a user_label. Returns rows updated."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_transactions SET user_label = {p} "
                  f"WHERE {_VENDOR_KEY_SQL} = {p} AND user_label IS NULL",
                  (label, vendor))
        return c.rowcount


def count_bank_unlabeled_by_vendor(vendor):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT COUNT(*) AS n FROM bank_transactions "
                  f"WHERE {_VENDOR_KEY_SQL} = {p} AND user_label IS NULL",
                  (vendor,))
        return c.fetchone()["n"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_database_bank.py tests/test_money.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_bank.py
git commit -m "feat(db): suggested_label column, resolved_label, vendor bulk apply"
```

---

### Task 3: Sync wiring — the label-suggestion pass

**Files:**
- Modify: `jobs/sync_bank.py` (new `_suggest_labels()` + call in the
  enhancement slot)
- Test: `tests/test_sync_bank.py` (append; mirror its seeding conventions —
  read its existing suggestion-pass tests first)

**Interfaces:**
- Consumes: `bank_flows.label_suggestions` (Task 1),
  `db.set_bank_label_suggestions_bulk` (Task 2),
  `db.get_all_bank_transactions`.
- Produces: `_suggest_labels()` — reads the whole table, computes, writes.
  Called from `run()` inside the existing enhancement try/except, after
  `_suggest_triage_flows()`.

- [ ] **Step 1: Write the failing tests** — append to
  `tests/test_sync_bank.py` (adapt seeding to the file's helpers):

```python
# ── Label-suggestion pass: same-vendor inheritance, full recompute ─────────────

def test_label_pass_propagates_and_retires(temp_db_path):
    import database as db
    from jobs import sync_bank
    # seed: two same-vendor rows, one user-labeled
    ...seed account + txns "r1"/"r2" with payee "Check", txn "x1" payee "Cafe"...
    db.set_bank_label("r1", "Monthly Rent")

    sync_bank._suggest_labels()
    rows = {t["simplefin_id"]: t for t in db.get_all_bank_transactions()}
    assert rows["r2"]["suggested_label"] == "Monthly Rent"
    assert rows["x1"]["suggested_label"] is None

    # user clears the label -> next pass retires the suggestion
    db.set_bank_label("r1", None)
    sync_bank._suggest_labels()
    rows = {t["simplefin_id"]: t for t in db.get_all_bank_transactions()}
    assert rows["r2"]["suggested_label"] is None


def test_label_pass_conflict_stays_silent(temp_db_path):
    import database as db
    from jobs import sync_bank
    ...seed three "Amazon" rows a1/a2/a3...
    db.set_bank_label("a1", "Household")
    db.set_bank_label("a2", "Gifts")
    sync_bank._suggest_labels()
    rows = {t["simplefin_id"]: t for t in db.get_all_bank_transactions()}
    assert rows["a3"]["suggested_label"] is None
```

(The `...seed...` lines are placeholders for the file's real seeding
helpers — the assertions are the requirements.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sync_bank.py -v -k label_pass`
Expected: FAIL — no attribute `_suggest_labels`.

- [ ] **Step 3: Implement** — in `jobs/sync_bank.py`:

```python
def _suggest_labels():
    """Post-reclassify label pass: same-vendor inheritance, recomputed for
    the WHOLE table every run (pure + deterministic, like the flow
    reclassify — a retired or changed user label self-heals here). No AI,
    no network; see bank_flows.label_suggestions for the unanimity rule."""
    txns = db.get_all_bank_transactions()
    if not txns:
        return
    written = db.set_bank_label_suggestions_bulk(bank_flows.label_suggestions(txns))
    logger.info("Label suggestion pass: %d rows updated", written)
```

And inside `run()`'s existing enhancement try/except, directly after
`_suggest_triage_flows()`:

```python
            _suggest_triage_flows()
            _suggest_labels()
```

(Same block on purpose: both are enhancements; either failing logs without
touching the sync's status.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sync_bank.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add jobs/sync_bank.py tests/test_sync_bank.py
git commit -m "feat(jobs): same-vendor label suggestion pass after each sync"
```

---

### Task 4: `money.py` — resolve labels, expose suggestion + vendor in rows

**Files:**
- Modify: `app/money.py` (`breakdown` label mode key; `breakdown_rows`
  label filter key + two new output fields)
- Test: `tests/test_money.py` (append + one UPDATE)

**Interfaces:**
- Produces: label mode groups and filters on `resolved_label` (suggestions
  included; user label wins by COALESCE). `breakdown_rows` rows gain
  `suggested_label` and `vendor` (= `_vendor_key(t)`) keys — the frontend
  needs `vendor` for the bulk-apply offer in label view.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_money.py`:

```python
# ── Phase 3: suggestions resolve into the label view, user label wins ──────────

def test_breakdown_by_label_includes_suggestions_via_resolved(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "r1", acct["id"], today, -2000.0, "Check")
    _vendor_txn(db, "r2", acct["id"], today, -2000.0, "Check")
    db.set_bank_label("r1", "Monthly Rent")
    db.set_bank_label_suggestions_bulk({"r2": "Monthly Rent"})

    lines = money.breakdown(weeks=1, by="label")["lines"]
    assert lines[0] == {"label": "Monthly Rent", "count": 2, "amount": 4000.0}


def test_breakdown_rows_label_filter_matches_resolved_and_carries_vendor(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "r2", acct["id"], today, -2000.0, "Check")
    db.set_bank_label_suggestions_bulk({"r2": "Monthly Rent"})

    rows = money.breakdown_rows(weeks=1, label="Monthly Rent")["rows"]
    assert [r["simplefin_id"] for r in rows] == ["r2"]
    assert rows[0]["suggested_label"] == "Monthly Rent"
    assert rows[0]["user_label"] is None
    assert rows[0]["vendor"] == "Check"
```

UPDATE the existing `test_breakdown_rows_returns_vendor_rows_newest_first`
shape assertion to the new key set:

```python
    assert set(rows[0]) == {"simplefin_id", "posted", "amount", "account_name",
                            "resolved_flow", "user_note", "user_label",
                            "suggested_label", "vendor"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_money.py -v -k "resolved or vendor_rows or newest"`
Expected: FAIL.

- [ ] **Step 3: Implement** — in `app/money.py`:

- `breakdown`: label-mode key becomes `lambda t: t["resolved_label"]`
  (docstring: note suggestions resolve in, user label wins via the SQL
  COALESCE).
- `breakdown_rows`: label-mode `matches` becomes
  `lambda t: t["resolved_label"] == label`; output dicts gain
  `"suggested_label": t["suggested_label"]` and
  `"vendor": _vendor_key(t)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_money.py tests/test_api_routes.py -v`
Expected: all PASS (route shape tests only assert empty payloads — no
breakage expected; if one asserts row keys, update it the same way).

- [ ] **Step 5: Commit**

```bash
git add app/money.py tests/test_money.py
git commit -m "feat(api): labels resolve suggestions into the label view"
```

---

### Task 5: Route — bulk mode + sibling count on `POST /bank/label`

**Files:**
- Modify: `app/routes.py` (`LabelPatch` + `set_bank_label` route)
- Test: `tests/test_api_routes.py` (append + UPDATE the existing label test)

**Interfaces:**
- Produces: `POST /api/bank/label` with exactly one of:
  - `{"simplefin_id": …, "label": …}` — unchanged single-row behavior, but
    the response gains `"siblings"`: when a non-null label was set, the
    count of that row's vendor's remaining `user_label IS NULL` rows
    (0 when clearing). Response:
    `{"ok": true, "label": …, "siblings": N, "vendor": <vendor key>}`.
  - `{"payee": …, "label": …}` — bulk mode: requires non-empty label after
    trim (else 400); applies to the vendor's unlabeled rows; response
    `{"ok": true, "label": …, "applied": N}`.
  Both fields or neither → 400. (`payee` here means the VENDOR key — the
  same value breakdown lines and drill rows carry.)

- [ ] **Step 1: Write the failing tests** — in `tests/test_api_routes.py`:

UPDATE `test_bank_label_set_trim_clear_and_404`: the two 200 assertions
gain the new response fields —

```python
    r = client.post("/api/bank/label", json={"simplefin_id": "t1", "label": "  Groceries  "})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "label": "Groceries", "siblings": 0, "vendor": "Kroger"}

    r = client.post("/api/bank/label", json={"simplefin_id": "t1", "label": "   "})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "label": None, "siblings": 0, "vendor": "Kroger"}
```

ADD:

```python
def test_bank_label_single_reports_unlabeled_siblings(client, temp_db_path):
    import database as db
    db.upsert_bank_account("acct-1", "Checking", "WF", "checking")
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-1")
    for sfid in ("a1", "a2", "a3"):
        db.upsert_bank_transaction(sfid, acct["id"], "2026-07-20", "2026-07-20",
                                   -10.0, "RAW", "Amazon", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, False)

    r = client.post("/api/bank/label", json={"simplefin_id": "a1", "label": "Household"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "label": "Household", "siblings": 2, "vendor": "Amazon"}


def test_bank_label_bulk_applies_to_unlabeled_only(client, temp_db_path):
    import database as db
    db.upsert_bank_account("acct-1", "Checking", "WF", "checking")
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-1")
    for sfid in ("a1", "a2", "a3"):
        db.upsert_bank_transaction(sfid, acct["id"], "2026-07-20", "2026-07-20",
                                   -10.0, "RAW", "Amazon", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, False)
    db.set_bank_label("a1", "Gifts")

    r = client.post("/api/bank/label", json={"payee": "Amazon", "label": "Household"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "label": "Household", "applied": 2}

    r = client.post("/api/bank/label", json={"payee": "Amazon", "label": "  "})
    assert r.status_code == 400

    r = client.post("/api/bank/label", json={"label": "X"})
    assert r.status_code == 400

    r = client.post("/api/bank/label",
                    json={"simplefin_id": "a1", "payee": "Amazon", "label": "X"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_routes.py -v -k bank_label`
Expected: FAIL.

- [ ] **Step 3: Implement** — in `app/routes.py`:

```python
class LabelPatch(BaseModel):
    simplefin_id: Optional[str] = None
    payee: Optional[str] = None
    label: Optional[str] = None


@router.post("/bank/label")
def set_bank_label(body: LabelPatch):
    if (body.simplefin_id is None) == (body.payee is None):
        raise HTTPException(status_code=400,
                            detail="pass exactly one of simplefin_id or payee")
    label = (body.label or "").strip() or None
    if body.payee is not None:
        # Bulk mode: the user tapped an explicit "apply to N more" offer.
        if label is None:
            raise HTTPException(status_code=400, detail="bulk apply needs a label")
        applied = db.set_bank_labels_by_vendor(body.payee, label)
        return {"ok": True, "label": label, "applied": applied}
    row_vendor = db.get_bank_transaction_vendor(body.simplefin_id)
    if row_vendor is None or not db.set_bank_label(body.simplefin_id, label):
        raise HTTPException(status_code=404, detail="unknown transaction")
    siblings = db.count_bank_unlabeled_by_vendor(row_vendor) if label else 0
    return {"ok": True, "label": label, "siblings": siblings, "vendor": row_vendor}
```

This needs one more small DB helper (add it in THIS task, in `database.py`
next to `count_bank_unlabeled_by_vendor`, with a one-line test appended to
`tests/test_database_bank.py`):

```python
def get_bank_transaction_vendor(simplefin_id):
    """The vendor key of one row, or None if the id is unknown."""
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT {_VENDOR_KEY_SQL} AS vendor FROM bank_transactions "
                  f"WHERE simplefin_id = {p}", (simplefin_id,))
        row = c.fetchone()
        return row["vendor"] if row else None
```

```python
def test_get_bank_transaction_vendor(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    _seed_txn(db, "t1", acct_id, "2026-07-20", -50.0)   # payee "" → description
    assert db.get_bank_transaction_vendor("t1") is not None
    assert db.get_bank_transaction_vendor("ghost") is None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ -q`
Expected: ZERO failures.

- [ ] **Step 5: Commit**

```bash
git add app/routes.py database.py tests/test_api_routes.py tests/test_database_bank.py
git commit -m "feat(api): bulk label apply and sibling counts on the label route"
```

---

### Task 6: Frontend — suggested styling, prefill, "apply to N more" offer

**Files:**
- Modify: `frontend/src/components/VendorBreakdown.tsx`
- Modify: `frontend/src/styles.css` (one suggested-state rule + one offer rule)

**Interfaces:**
- Consumes: drill rows now carry `suggested_label` and `vendor`; single
  label POST returns `{ok, label, siblings, vendor}`; bulk POST
  `{payee, label}` → `{ok, label, applied}`.
- Produces:
  1. **Suggested state**: a row with no `user_label` but a `suggested_label`
     shows that text in the label button, styled as unconfirmed (reuse the
     dashed-border look: same `.vendor-label-btn` base + a
     `.vendor-label-suggested` modifier — italic or muted, existing tokens
     only). Opening the editor prefills with
     `user_label ?? suggested_label ?? ""`, so Enter confirms a suggestion
     as a real user label.
  2. **Bulk offer**: after a single save whose response has `siblings > 0`,
     show a one-shot offer row directly under that drill row: "Apply to
     {siblings} more {vendor} row(s)". Tapping POSTs bulk
     `{payee: vendor, label}`, then updates the CURRENT drill cache entry
     locally (set `user_label` on rows of that vendor that had none) and
     clears the offer. The offer also clears on drill collapse, account/
     mode/weeks change (it can live in one `offer` state object
     `{drillKey, simplefin_id, vendor, label, siblings} | null`, reset in
     the same places the drill cache resets, guarded by `fetchGen` like
     everything else).

- [ ] **Step 1: Implement** — key edits in `VendorBreakdown.tsx`:

1. `DrillRow` gains `suggested_label: string | null; vendor: string`.
2. `saveLabel` keeps its optimistic update, and its `.then` now reads the
   response: `apiSend(...)` already returns parsed JSON (check `api.ts` —
   mirror how Money.tsx consumes it; if apiSend discards the body, extend
   the call here with the same pattern `api.ts` exposes for JSON POSTs).
   On `resp.siblings > 0`, set the offer state (guarded by `fetchGen`).
   Also: a confirmed suggestion means the row's `user_label` changed —
   the optimistic drill update already handles it.
3. Label button rendering:

```tsx
  const shownLabel = r.user_label ?? r.suggested_label;
  ...
  <button type="button"
    className={r.user_label ? "vendor-label-btn" :
               r.suggested_label ? "vendor-label-btn vendor-label-suggested" :
               "vendor-label-btn"}
    onClick={() => setEditing(r.simplefin_id)}>
    {shownLabel ?? "＋ label"}
  </button>
```

   Editor `defaultValue={r.user_label ?? r.suggested_label ?? ""}`.
4. Offer row JSX (rendered inside the drill, directly after the matching
   row; one-shot):

```tsx
  {offer && offer.drillKey === drillKey && offer.simplefin_id === r.simplefin_id && (
    <button type="button" className="vendor-bulk-offer" onClick={() => applyBulk()}>
      Apply "{offer.label}" to {offer.siblings} more {offer.vendor} row{offer.siblings === 1 ? "" : "s"}
    </button>
  )}
```

```typescript
  const applyBulk = () => {
    if (!offer) return;
    const { drillKey, vendor, label } = offer;
    setOffer(null);
    const gen = fetchGen.current;
    apiSend("POST", "/bank/label", { payee: vendor, label })
      .then(() => {
        if (fetchGen.current !== gen) return;
        setDrill((prev) => ({
          ...prev,
          [drillKey]: (prev[drillKey] ?? []).map((r) =>
            r.vendor === vendor && !r.user_label ? { ...r, user_label: label } : r),
        }));
      })
      .catch(() => {});
  };
```

5. Reset `offer` to null everywhere `drill` resets (the mode/account/weeks
   effect) and when the drill section collapses.

- [ ] **Step 2: Styles** — append next to the label-editor rules (existing
  tokens only):

```css
.vendor-label-suggested { font-style: italic; color: var(--muted); }
.vendor-bulk-offer {
  display: block; margin: 2px 0 6px 12px; padding: 3px 10px;
  border: 1px solid var(--line); border-radius: 999px; background: none;
  color: var(--ink); font-size: 0.8rem; cursor: pointer;
}
```

- [ ] **Step 3: Verify**

Run: `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`
Expected: 73 tests pass, tsc clean, build green.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/VendorBreakdown.tsx frontend/src/styles.css
git commit -m "feat(frontend): suggested labels and one-tap bulk apply offer"
```

---

### Task 7: Full verification + docs proposal

- [ ] **Step 1: Full suites** — backend (venv) + frontend + tsc + build.
- [ ] **Step 2: Live check** — copy the local DB into the worktree, run
  uvicorn (test APP_PASSWORD, spare port), then with a curl session: label
  one row of a multi-row vendor → response reports siblings; bulk apply →
  applied count; run the sync's `_suggest_labels()` equivalent via a
  labeled vendor and confirm `by=label` groups suggested rows; clear the
  test labels afterwards (set each back to null) so the copied DB's state
  doesn't matter.
- [ ] **Step 3: Propose (do not make) CLAUDE.md updates** — the
  `bank_transactions` row (suggested_label: derived, sync-recomputed,
  COALESCE resolution) — ask the user.
- [ ] **Step 4: Report** — then superpowers:finishing-a-development-branch.
