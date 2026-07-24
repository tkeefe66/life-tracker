# Label Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Suggested labels — needs a look" audit section on the Money
screen (confirm / change / reject each unconfirmed suggestion, rejection
survives the sync) plus "N suggested" badges on label-view lines.

**Architecture:** One new USER boolean `user_no_label` (sync never touches
it) makes rejection durable; `resolved_label` becomes a CASE expression that
nulls out rejected rows — resolution stays in SQL, single source. The audit
list is a thin `_BANK_TXN_SELECT` filter; the badge is a per-line counter in
`breakdown`'s existing label-mode loop. New `LabelAudit` component; badge in
`VendorBreakdown`.

**Tech Stack:** FastAPI + pytest (SQLite path), React + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/2026-07-24-label-audit-design.md`.

## Global Constraints

- `user_no_label` is a USER column: sync/suggestion pass never writes it;
  migration per `_init_v2_tables()` pattern (both engines) AND the
  `CREATE TABLE`; cast to real bool in `_bank_txn_rows` like `ambiguous`.
- Mutual exclusivity, enforced at the DB writers: `set_bank_label(...)`
  always writes `user_no_label = FALSE`; new `set_bank_no_label(...)` writes
  `user_no_label = TRUE, user_label = NULL`.
- `resolved_label` = `CASE WHEN t.user_no_label THEN NULL ELSE
  COALESCE(t.user_label, t.suggested_label) END` — a rejected row is
  Unlabeled everywhere instantly.
- Rejected rows are excluded from: the suggestion pass
  (`bank_flows.label_suggestions` returns None), vendor bulk apply, sibling
  counts, and the audit list.
- Audit list: `suggested_label IS NOT NULL AND user_label IS NULL AND NOT
  user_no_label`, newest first, `limit` clamped 1–200, plus table-wide
  `total`.
- Badge: label-mode lines gain `"suggested"` = count of that line's
  contributing spending-side rows whose `user_label IS NULL`; field exists
  only in label mode; Unlabeled line naturally gets 0.
- `POST /bank/label` third form `{"simplefin_id", "no_label": true}`;
  `no_label` with `payee` or with a non-empty `label` → 400. Response
  `{"ok": true, "label": null, "siblings": 0, "vendor": …}`.
- Secondary surfaces fail quietly; existing tokens only; money via
  `money()`/`signedMoney()`.
- Baselines before Task 1: backend 513 passed (project venv at
  `/Users/tomkeefe/Code Apps/weekly-updates/venv`), frontend 73, tsc clean,
  build green.
- Work ONLY in `.claude/worktrees/money-label-audit` (branch
  `worktree-money-label-audit`); the parent checkout belongs to another
  agent.

---

### Task 1: DB — `user_no_label`, durable rejection, audit queries

**Files:**
- Modify: `database.py`
- Test: `tests/test_database_bank.py` (append)

**Interfaces:**
- Produces: `db.set_bank_no_label(simplefin_id) -> bool`;
  `db.get_bank_label_suggestion_rows(limit) -> list` (full
  `_BANK_TXN_SELECT` rows, newest first);
  `db.count_bank_label_suggestions() -> int`; `set_bank_label` now also
  clears the flag; rows carry a real-bool `user_no_label`; `resolved_label`
  is the CASE expression; vendor bulk/count exclude flagged rows.

- [ ] **Step 1: Write the failing tests** — append to
  `tests/test_database_bank.py`:

```python
# ── user_no_label: durable rejection ───────────────────────────────────────────

def test_no_label_nulls_resolution_and_excludes_from_audit(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    _seed_txn(db, "t1", acct_id, "2026-07-20", -50.0)
    db.set_bank_label_suggestions_bulk({"t1": "Groceries"})

    row = next(t for t in db.get_all_bank_transactions() if t["simplefin_id"] == "t1")
    assert row["resolved_label"] == "Groceries"
    assert db.count_bank_label_suggestions() == 1

    assert db.set_bank_no_label("t1") is True
    row = next(t for t in db.get_all_bank_transactions() if t["simplefin_id"] == "t1")
    assert row["user_no_label"] is True
    assert row["resolved_label"] is None          # instantly Unlabeled, even
    assert row["suggested_label"] == "Groceries"  # though the suggestion lingers
    assert db.count_bank_label_suggestions() == 0
    assert db.get_bank_label_suggestion_rows(10) == []

    assert db.set_bank_no_label("ghost") is False


def test_setting_a_label_clears_no_label_flag(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    _seed_txn(db, "t1", acct_id, "2026-07-20", -50.0)
    db.set_bank_no_label("t1")

    db.set_bank_label("t1", "Household")
    row = next(t for t in db.get_all_bank_transactions() if t["simplefin_id"] == "t1")
    assert row["user_no_label"] is False
    assert row["resolved_label"] == "Household"


def test_vendor_bulk_and_count_skip_rejected_rows(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    for sfid in ("a1", "a2", "a3"):
        db.upsert_bank_transaction(sfid, acct_id, "2026-07-20", "2026-07-20",
                                   -10.0, "RAW", "Amazon", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, False)
    db.set_bank_no_label("a1")

    assert db.count_bank_unlabeled_by_vendor("Amazon") == 2
    assert db.set_bank_labels_by_vendor("Amazon", "Household") == 2
    rows = {t["simplefin_id"]: t for t in db.get_all_bank_transactions()}
    assert rows["a1"]["user_label"] is None
    assert rows["a1"]["user_no_label"] is True


def test_label_suggestion_rows_newest_first_and_capped(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    for i, day in enumerate(("2026-07-18", "2026-07-19", "2026-07-20")):
        _seed_txn(db, f"s{i}", acct_id, day, -10.0)
    db.set_bank_label_suggestions_bulk({"s0": "X", "s1": "X", "s2": "X"})

    rows = db.get_bank_label_suggestion_rows(2)
    assert [r["simplefin_id"] for r in rows] == ["s2", "s1"]
    assert db.count_bank_label_suggestions() == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_database_bank.py -v -k "no_label or suggestion_rows"`
Expected: FAIL — missing column/functions.

- [ ] **Step 3: Implement** — five edits to `database.py`:

(a) `CREATE TABLE bank_transactions`: add
`user_no_label {bool_t} NOT NULL DEFAULT FALSE,` after `suggested_label
TEXT,` (the statement already interpolates `bool_t`).

(b) Migration after the `suggested_label` block:

```python
        # user_no_label: the user's durable "this row gets no label" verdict.
        # A USER column — the sync and suggestion pass never write it; without
        # it a rejected suggestion would come back on the next full recompute.
        if USE_POSTGRES:
            c.execute("ALTER TABLE bank_transactions ADD COLUMN IF NOT EXISTS "
                      "user_no_label BOOLEAN NOT NULL DEFAULT FALSE")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(bank_transactions)").fetchall()]
            if "user_no_label" not in cols:
                c.execute("ALTER TABLE bank_transactions ADD COLUMN "
                          "user_no_label INTEGER NOT NULL DEFAULT 0")
```

(c) `_BANK_TXN_SELECT`: add `t.user_no_label`, and replace the
`resolved_label` COALESCE with:

```sql
CASE WHEN t.user_no_label THEN NULL
     ELSE COALESCE(t.user_label, t.suggested_label) END AS resolved_label
```

(SQLite evaluates `WHEN t.user_no_label` on the 0/1 integer correctly.)
In `_bank_txn_rows`, cast it: `r["user_no_label"] = bool(r["user_no_label"])`
alongside the existing `ambiguous` cast.

(d) Writers — `set_bank_label` gains the flag reset; new rejection setter:

```python
def set_bank_label(simplefin_id, label):
    """User label on one transaction. None clears. Always resets
    user_no_label — a label verdict and a no-label verdict are mutually
    exclusive answers to the same question. Returns True iff a row updated."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_transactions SET user_label = {p}, user_no_label = {p} "
                  f"WHERE simplefin_id = {p}",
                  (label, False, simplefin_id))
        return c.rowcount > 0


def set_bank_no_label(simplefin_id):
    """The user's durable rejection: this row gets no label. Clears any
    user_label (mutual exclusivity) and survives the sync's full suggestion
    recompute — bank_flows.label_suggestions returns None for flagged rows.
    Returns True iff a row updated."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_transactions SET user_no_label = {p}, user_label = NULL "
                  f"WHERE simplefin_id = {p}",
                  (True, simplefin_id))
        return c.rowcount > 0
```

(e) Audit queries + rejected-row exclusions:

```python
_LABEL_SUGGESTION_WHERE = ("t.suggested_label IS NOT NULL AND t.user_label IS NULL "
                           "AND NOT t.user_no_label")


def get_bank_label_suggestion_rows(limit):
    p = _p()
    with _cursor() as c:
        c.execute(f"{_BANK_TXN_SELECT} WHERE {_LABEL_SUGGESTION_WHERE} "
                  f"ORDER BY t.posted DESC, t.simplefin_id DESC LIMIT {p}", (limit,))
        return _bank_txn_rows(c.fetchall())


def count_bank_label_suggestions():
    with _cursor() as c:
        c.execute("SELECT COUNT(*) AS n FROM bank_transactions t "
                  f"WHERE {_LABEL_SUGGESTION_WHERE}")
        return c.fetchone()["n"]
```

And add `AND NOT user_no_label` to the WHERE of both
`set_bank_labels_by_vendor` and `count_bank_unlabeled_by_vendor`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_database_bank.py tests/test_money.py tests/test_sync_bank.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_bank.py
git commit -m "feat(db): durable no-label verdict and label-audit queries"
```

---

### Task 2: `bank_flows` — suggestion pass respects rejection

**Files:**
- Modify: `bank_flows.py` (`label_suggestions`)
- Test: `tests/test_bank_flows.py` (append)

**Interfaces:**
- Consumes/produces: `label_suggestions(txns)` — txns may carry
  `user_no_label`; flagged rows map to None (never inherit).

- [ ] **Step 1: Write the failing test** — append to
  `tests/test_bank_flows.py` (its `_ltxn` helper builds txn dicts; pass the
  flag explicitly):

```python
def test_label_suggestions_skips_rejected_rows():
    import bank_flows
    txns = [
        _ltxn("r1", "Check", user_label="Monthly Rent"),
        {**_ltxn("r2", "Check"), "user_no_label": True},
        _ltxn("r3", "Check"),
    ]
    result = bank_flows.label_suggestions(txns)
    assert result["r2"] is None
    assert result["r3"] == "Monthly Rent"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_bank_flows.py -v -k rejected`
Expected: FAIL — r2 currently gets "Monthly Rent".

- [ ] **Step 3: Implement** — in `label_suggestions`, the per-row expression
  becomes:

```python
        t["simplefin_id"]: (None if t.get("user_label") or t.get("user_no_label")
                            else unanimous.get(vendor_key(t)))
```

Extend the docstring: rows carrying the user's no-label verdict never
inherit.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bank_flows.py tests/test_sync_bank.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bank_flows.py tests/test_bank_flows.py
git commit -m "feat(bank): suggestion pass respects the no-label verdict"
```

---

### Task 3: `money.py` — audit list + badge counts

**Files:**
- Modify: `app/money.py`
- Test: `tests/test_money.py` (append)

**Interfaces:**
- Produces: `label_suggestions(limit=50) -> {"rows": [{simplefin_id, posted,
  amount, vendor, account_name, suggested_label, description}], "total": N}`
  (limit clamped 1–200 via `_clamp_triage_limit`);
  `breakdown(by="label")` lines gain `"suggested": int` (label mode only).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_money.py`:

```python
# ── Label audit list + suggested badges ────────────────────────────────────────

def test_label_suggestions_list_shape_and_total(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "r1", acct["id"], today, -25.0, "Amex")
    db.set_bank_label_suggestions_bulk({"r1": "Amex Benefit"})

    result = money.label_suggestions(limit=10)
    assert result["total"] == 1
    row = result["rows"][0]
    assert row == {
        "simplefin_id": "r1", "posted": today, "amount": -25.0,
        "vendor": "Amex", "account_name": row["account_name"],
        "suggested_label": "Amex Benefit", "description": "RAW DESC",
    }


def test_breakdown_label_lines_carry_suggested_counts(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "u1", acct["id"], today, -10.0, "Check")
    db.set_bank_label("u1", "Rent")
    _vendor_txn(db, "u2", acct["id"], today, -10.0, "Check")
    db.set_bank_label_suggestions_bulk({"u2": "Rent"})
    _vendor_txn(db, "x1", acct["id"], today, -5.0, "Cafe")

    lines = money.breakdown(weeks=1, by="label")["lines"]
    rent = next(l for l in lines if l["label"] == "Rent")
    unlabeled = next(l for l in lines if l["label"] is None)
    assert rent["count"] == 2 and rent["suggested"] == 1
    assert unlabeled["suggested"] == 0

    # vendor mode: no `suggested` key at all
    vlines = money.breakdown(weeks=1)["lines"]
    assert all("suggested" not in l for l in vlines)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_money.py -v -k "suggestions_list or suggested_counts"`
Expected: FAIL.

- [ ] **Step 3: Implement** — in `app/money.py`:

New function after `breakdown_rows`:

```python
def label_suggestions(limit: int = 50) -> dict:
    """The audit list: rows carrying an unconfirmed suggestion (no user
    verdict either way), newest first, capped like triage. `total` is
    table-wide so the UI can say "N more"."""
    limit = _clamp_triage_limit(limit)
    rows = db.get_bank_label_suggestion_rows(limit)
    return {
        "rows": [{
            "simplefin_id": t["simplefin_id"],
            "posted": t["posted"],
            "amount": t["amount"],
            "vendor": _vendor_key(t),
            "account_name": t["account_name"],
            "suggested_label": t["suggested_label"],
            "description": t["description"],
        } for t in rows],
        "total": db.count_bank_label_suggestions(),
    }
```

In `breakdown`'s grouping loop, label mode counts suggestion-resolved
spending rows — in the spending branch, after `g["count"] += 1`:

```python
            if by == "label" and t["user_label"] is None and t["resolved_label"] is not None:
                g["suggested"] = g.get("suggested", 0) + 1
```

and in the line-building comprehension for label mode include
`"suggested": g.get("suggested", 0)` (leave vendor mode's dict untouched —
the cleanest form is building the line dict per mode, mirroring the existing
`field` split).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_money.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/money.py tests/test_money.py
git commit -m "feat(api): label audit list and suggested badge counts"
```

---

### Task 4: Routes — `GET /bank/label-suggestions` + `no_label` form

**Files:**
- Modify: `app/routes.py`
- Test: `tests/test_api_routes.py` (append; add the GET to
  `PROTECTED_ROUTES` per the file's convention)

**Interfaces:**
- Produces: `GET /api/bank/label-suggestions?limit=50` →
  `money.label_suggestions(limit)`. `POST /api/bank/label` accepts
  `{"simplefin_id", "no_label": true}` (sets the durable rejection;
  response `{"ok": true, "label": null, "siblings": 0, "vendor": …}`);
  `no_label` with `payee` → 400; `no_label` with a non-empty `label` → 400.

- [ ] **Step 1: Write the failing tests** — append to
  `tests/test_api_routes.py`:

```python
def test_bank_label_suggestions_endpoint(client, temp_db_path):
    import database as db
    r = client.get("/api/bank/label-suggestions")
    assert r.status_code == 200
    assert r.json() == {"rows": [], "total": 0}

    db.upsert_bank_account("acct-1", "Checking", "WF", "checking")
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-1")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-20", "2026-07-20",
                               -25.0, "RAW", "Amex", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, False)
    db.set_bank_label_suggestions_bulk({"t1": "Amex Benefit"})

    r = client.get("/api/bank/label-suggestions?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["rows"][0]["suggested_label"] == "Amex Benefit"


def test_bank_label_no_label_form(client, temp_db_path):
    import database as db
    db.upsert_bank_account("acct-1", "Checking", "WF", "checking")
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-1")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-20", "2026-07-20",
                               -25.0, "RAW", "Amex", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, False)
    db.set_bank_label_suggestions_bulk({"t1": "Amex Benefit"})

    r = client.post("/api/bank/label", json={"simplefin_id": "t1", "no_label": True})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "label": None, "siblings": 0, "vendor": "Amex"}
    assert client.get("/api/bank/label-suggestions").json()["total"] == 0

    r = client.post("/api/bank/label", json={"simplefin_id": "t1", "no_label": True,
                                             "label": "X"})
    assert r.status_code == 400
    r = client.post("/api/bank/label", json={"payee": "Amex", "no_label": True,
                                             "label": "X"})
    assert r.status_code == 400
    r = client.post("/api/bank/label", json={"simplefin_id": "ghost", "no_label": True})
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_api_routes.py -v -k "label_suggestions or no_label"`
Expected: FAIL.

- [ ] **Step 3: Implement** — in `app/routes.py`:

`LabelPatch` gains `no_label: Optional[bool] = None`. The route, after the
existing XOR check and before the bulk branch:

```python
    if body.no_label:
        if body.payee is not None or (body.label or "").strip():
            raise HTTPException(status_code=400,
                                detail="no_label is a single-row verdict and takes no label")
        row_vendor = db.get_bank_transaction_vendor(body.simplefin_id)
        if row_vendor is None or not db.set_bank_no_label(body.simplefin_id):
            raise HTTPException(status_code=404, detail="unknown transaction")
        return {"ok": True, "label": None, "siblings": 0, "vendor": row_vendor}
```

New GET after the breakdown routes:

```python
@router.get("/bank/label-suggestions")
def get_bank_label_suggestions(limit: int = 50):
    return money.label_suggestions(limit)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ -q`
Expected: ZERO failures.

- [ ] **Step 5: Commit**

```bash
git add app/routes.py tests/test_api_routes.py
git commit -m "feat(api): label-suggestions endpoint and durable no-label form"
```

---

### Task 5: Frontend — `LabelAudit` section + suggested badges

**Files:**
- Create: `frontend/src/components/LabelAudit.tsx`
- Modify: `frontend/src/components/VendorBreakdown.tsx` (badge only)
- Modify: `frontend/src/screens/Money.tsx` (render `<LabelAudit />` after
  the triage queues / "Recently sorted" block, inside `bankSectionsVisible`)
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `GET /bank/label-suggestions?limit=50`;
  `POST /bank/label` (confirm = `{simplefin_id, label}`, reject =
  `{simplefin_id, no_label: true}`); label-view lines now carry `suggested`.
- Produces: self-contained audit section (own fetch, quiet failure, renders
  null when empty); `N suggested` pill on label lines.

- [ ] **Step 1: `LabelAudit.tsx`**:

```tsx
import { useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import { dayRowDate, money } from "../lib";

interface SuggestionRow {
  simplefin_id: string;
  posted: string;
  amount: number;
  vendor: string;
  account_name: string;
  suggested_label: string;
  description: string;
}

// "Suggested labels — needs a look": every unconfirmed suggestion, one tap
// to confirm / change / reject. Rejection is durable (no_label verdict).
// Secondary surface: failed fetch hides the section; renders null when empty.
export default function LabelAudit() {
  const [rows, setRows] = useState<SuggestionRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [editing, setEditing] = useState<string | null>(null);

  useEffect(() => {
    apiGet<{ rows: SuggestionRow[]; total: number }>("/bank/label-suggestions?limit=50")
      .then((d) => {
        setRows(d.rows);
        setTotal(d.total);
      })
      .catch(() => setRows(null));
  }, []);

  if (!rows || rows.length === 0) return null;

  const removeRow = (r: SuggestionRow) => {
    setRows((prev) => prev?.filter((x) => x.simplefin_id !== r.simplefin_id) ?? prev);
    setTotal((t) => Math.max(0, t - 1));
  };
  const restoreRow = (r: SuggestionRow) => {
    setRows((prev) => (prev && !prev.some((x) => x.simplefin_id === r.simplefin_id)
      ? [r, ...prev] : prev));
    setTotal((t) => t + 1);
  };

  const answer = (r: SuggestionRow, body: object) => {
    removeRow(r);
    apiSend("POST", "/bank/label", body).catch(() => restoreRow(r));
  };

  return (
    <>
      <p className="section-label">Suggested labels — needs a look</p>
      <p className="footnote">
        {total} transaction{total === 1 ? "" : "s"} inherited a label you haven't confirmed.
      </p>
      {rows.map((r) => (
        <div className="audit-card" key={r.simplefin_id}>
          <div className="audit-head">
            <span>{r.vendor}</span>
            <span className="num">{money(Math.abs(r.amount))}</span>
          </div>
          <p className="audit-meta">
            {dayRowDate(r.posted.slice(0, 10)).monthDay} · {r.account_name} · suggested:{" "}
            <em>{r.suggested_label}</em>
          </p>
          {editing === r.simplefin_id ? (
            <input
              className="vendor-label-input"
              defaultValue={r.suggested_label}
              autoFocus
              aria-label="Label"
              onBlur={(e) => {
                const label = e.currentTarget.value.trim();
                setEditing(null);
                if (label) answer(r, { simplefin_id: r.simplefin_id, label });
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") e.currentTarget.blur();
                if (e.key === "Escape") setEditing(null);
              }}
            />
          ) : (
            <div className="audit-btns">
              <button type="button" className="audit-btn audit-btn-primary"
                onClick={() => answer(r, { simplefin_id: r.simplefin_id, label: r.suggested_label })}>
                ✓ {r.suggested_label}
              </button>
              <button type="button" className="audit-btn"
                onClick={() => setEditing(r.simplefin_id)}>
                Change…
              </button>
              <button type="button" className="audit-btn"
                onClick={() => answer(r, { simplefin_id: r.simplefin_id, no_label: true })}>
                No label
              </button>
            </div>
          )}
        </div>
      ))}
      {total > rows.length && <p className="triage-more">{total - rows.length} more</p>}
    </>
  );
}
```

Note the Escape-then-blur hazard from the drill-down editor applies here
too: mirror `VendorBreakdown`'s `cancelingRef` pattern (a ref checked and
reset in `onBlur`) rather than shipping the naive version above — the
Escape branch must not save.

- [ ] **Step 2: Badge in `VendorBreakdown.tsx`** — `LabelLine` gains
  `suggested?: number`; in the label-view line rendering, after the display
  name:

```tsx
  {mode === "label" && (l as LabelLine).suggested > 0 && (
    <span className="suggest-badge">{(l as LabelLine).suggested} suggested</span>
  )}
```

(Adapt to however the label-view render helper threads the line object —
the badge must appear only in label mode and only when `suggested > 0`.)

- [ ] **Step 3: `Money.tsx`** — import and render `<LabelAudit />` inside
  the `bankSectionsVisible` block, after the "Recently sorted" details
  block.

- [ ] **Step 4: Styles** — append (existing tokens only; verify names):

```css
.audit-card {
  background: var(--surface-2); border: 1px solid var(--line);
  border-radius: var(--r); padding: 10px 12px; margin-bottom: 10px;
}
.audit-head { display: flex; justify-content: space-between; font-size: 0.9rem; }
.audit-meta { font-size: 0.78rem; color: var(--muted); margin: 2px 0 8px; }
.audit-btns { display: flex; gap: 8px; flex-wrap: wrap; }
.audit-btn {
  font-size: 0.8rem; padding: 4px 12px; border-radius: 999px;
  border: 1px solid var(--line); background: none; color: var(--ink); cursor: pointer;
}
.audit-btn-primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.suggest-badge {
  font-size: 0.72rem; padding: 1px 8px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent); margin-left: 8px; white-space: nowrap;
}
```

- [ ] **Step 5: Verify**

Run: `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`
Expected: 73 tests pass, tsc clean, build green.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/LabelAudit.tsx frontend/src/components/VendorBreakdown.tsx frontend/src/screens/Money.tsx frontend/src/styles.css
git commit -m "feat(frontend): label audit section and suggested badges"
```

---

### Task 6: Full verification + finish

- [ ] **Step 1: Full suites** — backend (venv) + frontend + tsc + build.
- [ ] **Step 2: Live check** — copy the local DB into the worktree, seed a
  suggestion (set one row's `suggested_label` via
  `db.set_bank_label_suggestions_bulk`), run uvicorn (test APP_PASSWORD,
  spare port), curl: audit list shows it; `no_label` form removes it AND
  `by=label` no longer resolves it; confirm form writes a real label and
  clears the flag; badge counts appear on `by=label` lines.
- [ ] **Step 3: Propose (do not make) the CLAUDE.md update** — the
  `bank_transactions` row gains `user_no_label` (durable no-label verdict,
  mutually exclusive with `user_label`, excluded from suggestion pass and
  bulk apply; resolution CASE) — ask the user.
- [ ] **Step 4: Report** — then superpowers:finishing-a-development-branch.
