# Money Labels — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** User-assigned labels on bank transactions ("Monthly Rent", "401k
contribution") that double as categories: an inline label editor in the
vendor drill-down, and a Labels view of the "Where it went" section grouped
by label with an Unlabeled bucket.

**Architecture:** One new user column (`user_label`) following the
`user_note` contract exactly (sync never touches it). `breakdown()` gains a
`by` mode; label lines carry `label: string|null` where `null` is the
Unlabeled bucket. The vocabulary is `SELECT DISTINCT user_label` — no table,
no management UI. Frontend extends `VendorBreakdown` in place: a
Vendors/Labels toggle and a datalist-backed inline editor (no new deps).

**Tech Stack:** FastAPI + pytest (SQLite path), React + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/2026-07-23-money-vendor-breakdown-design.md`
(Phase 2). One deliberate deviation: the spec says the vocabulary rides only
the `by=label` response; this plan includes it in **both** modes' responses,
because the label editor lives in the vendor-view drill-down and needs the
vocabulary there too. Phase 3 (suggested_label/auto-apply) stays out of scope.

## Global Constraints

- No SQL outside `database.py`; no DB calls outside `app/money.py` wiring.
- `user_label` is a USER column: the sync never reads or writes it
  (Override + Learning rule 3). Nullable TEXT; migration per the
  `_init_v2_tables()` pattern, modeled verbatim on the `user_note` block.
- Label strings are trimmed; empty/whitespace → NULL (clear). `null` in the
  API means "no label".
- Label view: labeled lines sorted by net amount desc then name; the
  Unlabeled bucket (`label: null`) always LAST. Labeled + Unlabeled must sum
  to the window's spending total (same contributing-row predicate as the
  vendor view: spending-negative + refund-positive, netted, round-once).
- Rows route takes exactly one of `payee` / `label` — both or neither → 400.
  (This changes the Phase-1 "missing payee → 422" behavior; the old route
  test must be UPDATED, not duplicated.)
- Secondary surface fails quietly; money via `money()`/`signedMoney()`,
  null-checked; existing OKLCH tokens only.
- Baselines on this branch before Task 1: backend 487 passed (use the
  project venv at `/Users/tomkeefe/Code Apps/weekly-updates/venv` — system
  Python lacks apscheduler), frontend 73 passed, tsc clean, build green.
- Work in the worktree at `.claude/worktrees/money-labels` (branch
  `worktree-money-labels`).

---

### Task 1: DB layer — `user_label` column, setter, vocabulary

**Files:**
- Modify: `database.py` (migration in `_init_v2_tables` after the
  `suggested_flow` block ~line 656; `_BANK_TXN_SELECT` ~line 1285; new
  functions next to `set_bank_flow_override`)
- Test: `tests/test_database_bank.py` (append; mirror the file's existing
  seeding helpers/conventions — read its `user_note`/override tests first)

**Interfaces:**
- Produces: `db.set_bank_label(simplefin_id, label) -> bool` (None clears;
  True iff a row was updated); `db.get_bank_label_vocabulary() -> list[str]`
  (most-used first, then alphabetical); every `_BANK_TXN_SELECT` row gains a
  `user_label` key.

- [ ] **Step 1: Write the failing tests** — append to
  `tests/test_database_bank.py`, adapting seeding to that file's own
  helpers (the assertions below are the requirements):

```python
# ── user_label: set, clear, vocabulary ─────────────────────────────────────────

def test_set_bank_label_roundtrip_and_clear(temp_db_path):
    import database as db
    acct_id = _seed_account(db)                     # use the file's helper
    _seed_txn(db, "t1", acct_id, "2026-07-20", -50.0)

    assert db.set_bank_label("t1", "Monthly Rent") is True
    row = next(t for t in db.get_all_bank_transactions() if t["simplefin_id"] == "t1")
    assert row["user_label"] == "Monthly Rent"

    assert db.set_bank_label("t1", None) is True
    row = next(t for t in db.get_all_bank_transactions() if t["simplefin_id"] == "t1")
    assert row["user_label"] is None


def test_set_bank_label_unknown_id_returns_false(temp_db_path):
    import database as db
    assert db.set_bank_label("nope", "X") is False


def test_label_vocabulary_distinct_most_used_first(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    for i in range(3):
        _seed_txn(db, f"g{i}", acct_id, "2026-07-20", -10.0)
        db.set_bank_label(f"g{i}", "Groceries")
    _seed_txn(db, "r1", acct_id, "2026-07-20", -100.0)
    db.set_bank_label("r1", "Monthly Rent")
    _seed_txn(db, "u1", acct_id, "2026-07-20", -5.0)   # unlabeled — not in vocab

    assert db.get_bank_label_vocabulary() == ["Groceries", "Monthly Rent"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_database_bank.py -v -k label`
Expected: FAIL — no attribute `set_bank_label`.

- [ ] **Step 3: Implement** — three edits to `database.py`:

(a) Migration, directly after the `suggested_flow` block in
`_init_v2_tables`:

```python
        # user_label: nullable TEXT on bank_transactions. A user column — the
        # sync never reads or writes it (Override + Learning rule 3). Labels
        # double as categories; the vocabulary is SELECT DISTINCT, not a table.
        if USE_POSTGRES:
            c.execute("ALTER TABLE bank_transactions ADD COLUMN IF NOT EXISTS user_label TEXT")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(bank_transactions)").fetchall()]
            if "user_label" not in cols:
                c.execute("ALTER TABLE bank_transactions ADD COLUMN user_label TEXT")
```

Also add `user_label TEXT,` to the `bank_transactions` `CREATE TABLE`
statement (next to `user_note TEXT,`) so fresh databases match migrated ones.

(b) `_BANK_TXN_SELECT`: add `t.user_label` to the column list (after
`t.user_note`).

(c) New functions, next to `set_bank_flow_override`:

```python
def set_bank_label(simplefin_id, label):
    """User label on one transaction. None clears. Returns True iff a row was
    updated, so the route can turn an unknown id into a 404. A user column —
    the sync never touches it."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_transactions SET user_label = {p} WHERE simplefin_id = {p}",
                  (label, simplefin_id))
        return c.rowcount > 0


def get_bank_label_vocabulary():
    """Distinct labels, most-used first then alphabetical — autocomplete
    order. The vocabulary IS this query: a label with zero rows disappears."""
    with _cursor() as c:
        c.execute("""SELECT user_label AS label FROM bank_transactions
                     WHERE user_label IS NOT NULL
                     GROUP BY user_label ORDER BY COUNT(*) DESC, user_label""")
        return [r["label"] for r in c.fetchall()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_database_bank.py tests/test_money.py -v`
Expected: all PASS (the added SELECT column must not break existing tests).

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_bank.py
git commit -m "feat(db): user_label column, setter, and label vocabulary"
```

---

### Task 2: `money.breakdown` label mode + `breakdown_rows` label filter

**Files:**
- Modify: `app/money.py` (`breakdown`, `breakdown_rows`)
- Test: `tests/test_money.py` (append; also UPDATE the existing
  `breakdown_rows` shape assertion — see Step 1)

**Interfaces:**
- Consumes: `db.get_bank_label_vocabulary()`, `user_label` on rows (Task 1).
- Produces:
  `breakdown(weeks, account_id=None, by="payee") -> {"lines": [...], "labels": [...]}`
  — `by="payee"` lines keep the `vendor` key; `by="label"` lines use
  `{"label": str|null, "count", "amount"}` with the null (Unlabeled) line
  last. `labels` (the vocabulary) present in BOTH modes.
  `breakdown_rows(weeks, vendor=None, label=None, account_id=None, limit=100)`
  — exactly one of vendor/label given by the caller (the route enforces);
  row dicts gain a `user_label` key.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_money.py`
  (uses its existing `_account` / `_vendor_txn` helpers), and update one
  existing test:

```python
# ── breakdown(by="label") and label-filtered rows ──────────────────────────────

def test_breakdown_by_label_groups_with_unlabeled_bucket_last(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "r1", acct["id"], today, -2000.0, "Check")
    db.set_bank_label("r1", "Monthly Rent")
    _vendor_txn(db, "g1", acct["id"], today, -40.0, "Kroger")
    _vendor_txn(db, "g2", acct["id"], today, -60.0, "Safeway")
    for sfid in ("g1", "g2"):
        db.set_bank_label(sfid, "Groceries")
    _vendor_txn(db, "u1", acct["id"], today, -15.0, "Mystery")   # unlabeled

    result = money.breakdown(weeks=1, by="label")
    assert result["lines"] == [
        {"label": "Monthly Rent", "count": 1, "amount": 2000.0},
        {"label": "Groceries", "count": 2, "amount": 100.0},
        {"label": None, "count": 1, "amount": 15.0},
    ]
    assert result["labels"] == ["Groceries", "Monthly Rent"]


def test_breakdown_label_and_vendor_views_sum_identically(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "a1", acct["id"], today, -30.0, "Amazon")
    db.set_bank_label("a1", "Household")
    _vendor_txn(db, "a2", acct["id"], today, 10.0, "Amazon", user_flow="refund")
    _vendor_txn(db, "u1", acct["id"], today, -5.0, "Cafe")

    by_vendor = money.breakdown(weeks=1)["lines"]
    by_label = money.breakdown(weeks=1, by="label")["lines"]
    assert sum(l["amount"] for l in by_vendor) == sum(l["amount"] for l in by_label)


def test_breakdown_vendor_mode_now_carries_vocabulary(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "a1", acct["id"], today, -30.0, "Amazon")
    db.set_bank_label("a1", "Household")

    result = money.breakdown(weeks=1)
    assert result["labels"] == ["Household"]
    assert result["lines"][0]["vendor"] == "Amazon"


def test_breakdown_rows_by_label(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "a1", acct["id"], today, -30.0, "Amazon")
    _vendor_txn(db, "k1", acct["id"], today, -40.0, "Kroger")
    for sfid in ("a1", "k1"):
        db.set_bank_label(sfid, "Household")
    _vendor_txn(db, "u1", acct["id"], today, -5.0, "Cafe")

    rows = money.breakdown_rows(weeks=1, label="Household")["rows"]
    assert sorted(r["simplefin_id"] for r in rows) == ["a1", "k1"]
    assert all(r["user_label"] == "Household" for r in rows)
```

UPDATE the existing `test_breakdown_rows_returns_vendor_rows_newest_first`:
its exact-shape assertion must gain the new key —

```python
    assert set(rows[0]) == {"simplefin_id", "posted", "amount", "account_name",
                            "resolved_flow", "user_note", "user_label"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_money.py -v -k "label or breakdown"`
Expected: new tests FAIL (unexpected keyword `by` / `label`); the updated
shape test FAILS (no `user_label` key yet).

- [ ] **Step 3: Implement** — in `app/money.py`:

Replace `breakdown`'s body so grouping is keyed by mode (docstring: extend,
don't rewrite — keep the existing refund/round-once notes and add the label
mode + Unlabeled-last rule):

```python
def breakdown(weeks: int, account_id=None, by: str = "payee") -> dict:
    weeks = _clamp_weeks(weeks)
    start, end = _window(weeks)
    txns = db.get_bank_transactions_range(start, end)
    if account_id is not None:
        txns = [t for t in txns if t["account_id"] == account_id]

    key = _vendor_key if by == "payee" else (lambda t: t["user_label"])
    groups: dict = {}
    for t in txns:
        if t["resolved_flow"] == "spending" and t["amount"] < 0:
            g = groups.setdefault(key(t), {"count": 0, "raw": 0.0})
            g["count"] += 1
            g["raw"] += -t["amount"]
        elif t["resolved_flow"] == "refund" and t["amount"] > 0:
            g = groups.setdefault(key(t), {"count": 0, "raw": 0.0})
            g["raw"] -= t["amount"]

    field = "vendor" if by == "payee" else "label"
    lines = [{field: k, "count": g["count"], "amount": round(g["raw"], 2)}
             for k, g in groups.items()]
    if by == "payee":
        lines.sort(key=lambda l: (-l["amount"], l["vendor"]))
    else:
        # Labeled lines by net desc then name; the Unlabeled bucket (None)
        # always last — it's the catch-all, not a peer category.
        labeled = sorted((l for l in lines if l["label"] is not None),
                         key=lambda l: (-l["amount"], l["label"]))
        lines = labeled + [l for l in lines if l["label"] is None]
    return {"lines": lines, "labels": db.get_bank_label_vocabulary()}
```

Extend `breakdown_rows` with the label filter and the new output key
(again: extend the docstring):

```python
def breakdown_rows(weeks: int, vendor: str = None, label: str = None,
                   account_id=None, limit: int = 100) -> dict:
    weeks = _clamp_weeks(weeks)
    limit = _clamp_triage_limit(limit)
    start, end = _window(weeks)
    txns = db.get_bank_transactions_range(start, end)
    if vendor is not None:
        matches = lambda t: _vendor_key(t) == vendor
    else:
        matches = lambda t: t["user_label"] == label
    rows = [
        t for t in txns
        if matches(t)
        and (account_id is None or t["account_id"] == account_id)
        and ((t["resolved_flow"] == "spending" and t["amount"] < 0)
             or (t["resolved_flow"] == "refund" and t["amount"] > 0))
    ]
    rows.sort(key=lambda t: (t["posted"], t["simplefin_id"]), reverse=True)
    return {"rows": [{
        "simplefin_id": t["simplefin_id"],
        "posted": t["posted"],
        "amount": t["amount"],
        "account_name": t["account_name"],
        "resolved_flow": t["resolved_flow"],
        "user_note": t["user_note"],
        "user_label": t["user_label"],
    } for t in rows[:limit]]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_money.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/money.py tests/test_money.py
git commit -m "feat(api): label mode for breakdown and label-filtered drill-down"
```

---

### Task 3: Routes — widen `by`, XOR rows params, `POST /bank/label`

**Files:**
- Modify: `app/routes.py` (the two breakdown routes ~line 323; new POST near
  the other bank POSTs; the `LabelPatch` model next to `FlowPatch` ~line 349)
- Test: `tests/test_api_routes.py` (append + UPDATE two existing tests; add
  the new POST route to the `PROTECTED_ROUTES` list per that file's own
  convention comment)

**Interfaces:**
- Consumes: `money.breakdown(..., by=)`, `money.breakdown_rows(..., label=)`,
  `db.set_bank_label` (Tasks 1–2).
- Produces: `GET /api/bank/breakdown?by=payee|label` (else 400);
  `GET /api/bank/breakdown/rows` with exactly one of `payee`/`label` (else
  400); `POST /api/bank/label` `{simplefin_id, label}` → trims, empty→null,
  404 unknown id, returns `{"ok": true, "label": <stored value>}`.

- [ ] **Step 1: Write the failing tests** — in `tests/test_api_routes.py`
  (mirror the file's `(client, temp_db_path)` conventions):

UPDATE `test_bank_breakdown_shape_and_bad_by`: empty-DB response is now
`{"lines": [], "labels": []}`, and `by=label` is now 200 (assert its shape
too); a junk mode still 400s —

```python
def test_bank_breakdown_shape_and_bad_by(client, temp_db_path):
    r = client.get("/api/bank/breakdown?weeks=4")
    assert r.status_code == 200
    assert r.json() == {"lines": [], "labels": []}

    r = client.get("/api/bank/breakdown?weeks=4&by=label")
    assert r.status_code == 200
    assert r.json() == {"lines": [], "labels": []}

    r = client.get("/api/bank/breakdown?weeks=4&by=vibes")
    assert r.status_code == 400
```

UPDATE `test_bank_breakdown_rows_requires_payee` → rename to
`test_bank_breakdown_rows_requires_exactly_one_of_payee_or_label`:

```python
def test_bank_breakdown_rows_requires_exactly_one_of_payee_or_label(client, temp_db_path):
    r = client.get("/api/bank/breakdown/rows?weeks=4")
    assert r.status_code == 400

    r = client.get("/api/bank/breakdown/rows?weeks=4&payee=Amazon&label=Rent")
    assert r.status_code == 400

    r = client.get("/api/bank/breakdown/rows?weeks=4&payee=Amazon")
    assert r.status_code == 200
    assert r.json() == {"rows": []}

    r = client.get("/api/bank/breakdown/rows?weeks=4&label=Rent")
    assert r.status_code == 200
    assert r.json() == {"rows": []}
```

ADD:

```python
def test_bank_label_set_trim_clear_and_404(client, temp_db_path):
    import database as db
    db.upsert_bank_account("acct-1", "Checking", "WF", "checking")
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-1")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-20", "2026-07-20",
                               -50.0, "DESC", "Kroger", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, False)

    r = client.post("/api/bank/label", json={"simplefin_id": "t1", "label": "  Groceries  "})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "label": "Groceries"}

    r = client.post("/api/bank/label", json={"simplefin_id": "t1", "label": "   "})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "label": None}

    r = client.post("/api/bank/label", json={"simplefin_id": "nope", "label": "X"})
    assert r.status_code == 404
```

Add `("POST", "/api/bank/label")` (in that file's tuple format) to
`PROTECTED_ROUTES`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_routes.py -v -k "breakdown or bank_label"`
Expected: FAIL — old 400/422 behavior, missing route.

- [ ] **Step 3: Implement** — in `app/routes.py`:

```python
@router.get("/bank/breakdown")
def get_bank_breakdown(weeks: int = 12, by: str = "payee",
                       account_id: Optional[int] = None):
    if by not in ("payee", "label"):
        raise HTTPException(status_code=400, detail="by must be 'payee' or 'label'")
    return money.breakdown(weeks, account_id=account_id, by=by)


@router.get("/bank/breakdown/rows")
def get_bank_breakdown_rows(weeks: int, payee: Optional[str] = None,
                            label: Optional[str] = None,
                            account_id: Optional[int] = None, limit: int = 100):
    if (payee is None) == (label is None):
        raise HTTPException(status_code=400,
                            detail="pass exactly one of payee or label")
    return money.breakdown_rows(weeks, vendor=payee, label=label,
                                account_id=account_id, limit=limit)
```

Model next to `FlowPatch`, route next to the other bank POSTs:

```python
class LabelPatch(BaseModel):
    simplefin_id: str
    label: Optional[str] = None


@router.post("/bank/label")
def set_bank_label(body: LabelPatch):
    label = (body.label or "").strip() or None
    if not db.set_bank_label(body.simplefin_id, label):
        raise HTTPException(status_code=404, detail="unknown transaction")
    return {"ok": True, "label": label}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ -v`
Expected: all PASS (489+ — Task 1–3 additions on the 487 baseline).

- [ ] **Step 5: Commit**

```bash
git add app/routes.py tests/test_api_routes.py
git commit -m "feat(api): label routes — by=label, label rows filter, label POST"
```

---

### Task 4: Inline label editor in the vendor drill-down

**Files:**
- Modify: `frontend/src/components/VendorBreakdown.tsx`
- Modify: `frontend/src/styles.css` (editor styles only)

**Interfaces:**
- Consumes: `POST /api/bank/label` (Task 3); `labels` array now present in
  the breakdown response; `apiSend` from `../api` (same helper Money.tsx
  uses).
- Produces: each drill row shows its label as a small tag-button; tapping it
  (or the "＋ label" affordance when unlabeled) swaps in a text input backed
  by a shared `<datalist>` of the vocabulary. Enter/blur saves (trimmed,
  empty clears), Escape cancels. Optimistic local update; failed POST leaves
  the row unchanged (quiet failure).

- [ ] **Step 1: Implement the editor** in `VendorBreakdown.tsx`:

1. `import { apiSend } from "../api"` (extend the existing `../api` import).
2. Extend `DrillRow` with `user_label: string | null`.
3. New state + handler:

```typescript
  const [vocab, setVocab] = useState<string[]>([]);
  const [editing, setEditing] = useState<string | null>(null);

  // In the lines fetch .then, alongside setLines(d.lines):
  //   setVocab(d.labels ?? []);
  // (breakdown response now carries the vocabulary in both modes)

  const saveLabel = (drillKey: string, row: DrillRow, raw: string) => {
    const label = raw.trim() || null;
    setEditing(null);
    if (label === row.user_label) return;
    apiSend("POST", "/bank/label", { simplefin_id: row.simplefin_id, label })
      .then(() => {
        setDrill((prev) => ({
          ...prev,
          [drillKey]: (prev[drillKey] ?? []).map((r) =>
            r.simplefin_id === row.simplefin_id ? { ...r, user_label: label } : r),
        }));
        if (label && !vocab.includes(label)) {
          setVocab((v) => [...v, label].sort());
        }
      })
      .catch(() => {});
  };
```

4. In the drill-row JSX, after the note span, the label control (and one
shared datalist rendered once, next to the section label):

```tsx
      <datalist id="vendor-label-vocab">
        {vocab.map((l) => <option key={l} value={l} />)}
      </datalist>
```

```tsx
                      {editing === r.simplefin_id ? (
                        <input
                          className="vendor-label-input"
                          list="vendor-label-vocab"
                          defaultValue={r.user_label ?? ""}
                          autoFocus
                          onBlur={(e) => saveLabel(l.vendor, r, e.currentTarget.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") e.currentTarget.blur();
                            if (e.key === "Escape") setEditing(null);
                          }}
                        />
                      ) : (
                        <button
                          type="button"
                          className="vendor-label-btn"
                          onClick={() => setEditing(r.simplefin_id)}
                        >
                          {r.user_label ?? "＋ label"}
                        </button>
                      )}
```

(Adapt placement so the row layout stays two-ended: date/flow/account/note
left, label control + amount right — implementer judgment, keep it tight.)

- [ ] **Step 2: Styles** — append to `styles.css` next to the vendor rules,
  existing tokens only (verify names against the file — `--line`, `--muted`,
  `--ink` are the ones the vendor block already uses):

```css
.vendor-label-btn {
  padding: 1px 8px; border-radius: 999px; border: 1px dashed var(--line);
  background: none; color: var(--muted); font-size: 0.8rem; cursor: pointer;
}
.vendor-label-input {
  font: inherit; font-size: 0.8rem; color: var(--ink);
  background: none; border: 1px solid var(--line); border-radius: 6px;
  padding: 1px 6px; max-width: 10rem;
}
```

- [ ] **Step 3: Verify**

Run: `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`
Expected: 73 tests pass, tsc clean, build green.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/VendorBreakdown.tsx frontend/src/styles.css
git commit -m "feat(frontend): inline label editor in the vendor drill-down"
```

---

### Task 5: Labels view — Vendors/Labels toggle + label lines

**Files:**
- Modify: `frontend/src/components/VendorBreakdown.tsx`

**Interfaces:**
- Consumes: `GET /bank/breakdown?by=label` → `{lines: [{label, count,
  amount}], labels}`; `GET /bank/breakdown/rows?label=…` (Tasks 2–3).
- Produces: a two-chip toggle (Vendors | Labels) under the section header.
  Label view lists all label lines (no top-15/tail split — the vocabulary is
  user-sized), `label: null` displayed as "Unlabeled" and always last
  (server ordering preserved). Labeled lines expand to their rows via the
  `label=` rows fetch — same drill UI, including the Task-4 editor.
  The Unlabeled line is NOT expandable in this phase. Mode switch resets
  expansion/drill and bumps `fetchGen` (same discipline as the account
  filter).

- [ ] **Step 1: Implement** — key changes in `VendorBreakdown.tsx`:

1. Types + state:

```typescript
interface LabelLine { label: string | null; count: number; amount: number }
  const [mode, setMode] = useState<"payee" | "label">("payee");
  const [labelLines, setLabelLines] = useState<LabelLine[] | null>(null);
```

2. The lines effect gains `mode` in its dependency array and fetches the
   current mode's shape (both set `vocab`); switching mode resets
   `expanded`/`drill`/`showRest` and bumps `fetchGen` exactly like the
   account filter does. Vendor lines keep their state; simplest correct
   form — one effect, branch on mode:

```typescript
  useEffect(() => {
    const acct = accountId !== null ? `&account_id=${accountId}` : "";
    setExpanded(null);
    setDrill({});
    setShowRest(false);
    fetchGen.current += 1;
    const gen = fetchGen.current;
    const byParam = mode === "label" ? "&by=label" : "";
    apiGet<{ lines: (VendorLine | LabelLine)[]; labels: string[] }>(
      `/bank/breakdown?weeks=${weeks}${byParam}${acct}`,
    )
      .then((d) => {
        if (fetchGen.current !== gen) return;
        setVocab(d.labels ?? []);
        if (mode === "label") setLabelLines(d.lines as LabelLine[]);
        else setLines(d.lines as VendorLine[]);
      })
      .catch(() => {
        if (fetchGen.current !== gen) return;
        if (mode === "label") setLabelLines(null);
        else setLines(null);
      });
  }, [weeks, accountId, mode]);
```

3. Visibility rule: the section hides only when the VENDOR view has nothing
   (`!lines || lines.length === 0` — unchanged), so a bank-less screen stays
   clean but an empty label view can't hide the toggle you'd need to switch
   back. In label mode with `labelLines === null` (failed fetch), render the
   toggle and nothing below it.

4. Toggle JSX (above the account chips; reuse the chip classes):

```tsx
      <div className="vendor-chip-row">
        <button type="button"
          className={mode === "payee" ? "vendor-chip vendor-chip-on" : "vendor-chip"}
          onClick={() => setMode("payee")}>Vendors</button>
        <button type="button"
          className={mode === "label" ? "vendor-chip vendor-chip-on" : "vendor-chip"}
          onClick={() => setMode("label")}>Labels</button>
      </div>
```

5. Label-view rendering: map over `labelLines` with the same row/drill JSX
   factored to take `(displayName, drillKey, expandable, fetchQuery)` — the
   pragmatic shape: extract the current per-vendor row+drill block into a
   small local render helper both views call. Vendor view behavior must not
   change (vendorSplit, tail, drill by `payee=`); label view: `displayName =
   l.label ?? "Unlabeled"`, drill fetch uses
   `label=${encodeURIComponent(l.label)}` and `drillKey = "label:" + l.label`,
   `expandable = l.label !== null`. Drill rows in label view pass the row's
   OWN drill key to `saveLabel` so the optimistic update lands in the right
   cache entry. (After relabeling a row in label view its line totals are
   stale until the next mode/account refetch — acceptable for this phase;
   do NOT add a full refetch per label save.)

- [ ] **Step 2: Verify**

Run: `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/VendorBreakdown.tsx
git commit -m "feat(frontend): Labels view with Unlabeled bucket on the Money screen"
```

---

### Task 6: Full verification + docs proposal

- [ ] **Step 1: Full suites**

Run: `pytest tests/ -q` (venv) and `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`
Expected: everything green.

- [ ] **Step 2: Live check** — start the app against the copied local DB
  (see Phase 1's Task 6 pattern: copy `weekly_updates.db` from the main
  checkout into the worktree, run uvicorn with an overridden
  `APP_PASSWORD`), then via curl with a session cookie: label a real
  transaction via POST, confirm it appears in `by=label` lines and its
  vocabulary, clear it, confirm it returns to Unlabeled.

- [ ] **Step 3: Propose (do not make) doc updates** — CLAUDE.md's
  `bank_transactions` table row (mention `user_label` alongside `user_note`)
  and the routes bullet (`label` route). Ask the user.

- [ ] **Step 4: Report** — then superpowers:finishing-a-development-branch.
