# Money Vendor Breakdown — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Where it went" section on the Money screen: bank spending grouped
by vendor (payee), filterable by account, with a per-vendor transaction
drill-down.

**Architecture:** `breakdown()` and `breakdown_rows()` in `app/money.py`
(pure Python bucketing over the existing `db.get_bank_transactions_range`
call — no new SQL), two thin GET routes in `app/routes.py`, and a new
`VendorBreakdown` component rendered by `Money.tsx`. Top-15/tail split is a
pure `lib.ts` helper so it gets a vitest test.

**Tech Stack:** FastAPI + pytest (SQLite path), React + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/2026-07-23-money-vendor-breakdown-design.md`
(Phase 1 only — no schema change, no labels).

## Global Constraints

- No SQL outside `database.py`; no DB calls outside `app/money.py`'s wiring.
- Spending = `resolved_flow == "spending"` rows, negative side only.
  Refunds = `resolved_flow == "refund"`, positive side only, netted into the
  same vendor's line. Negative-net lines are kept, not clamped.
- Round once per group, at the end — never sum already-rounded values.
- `weeks` clamps 1–52 (`_clamp_weeks`); `limit` clamps 1–200
  (`_clamp_triage_limit`).
- Money formats via `lib.ts money()` only; null-check, never truthiness.
- The whole section is a secondary surface: any failed fetch hides it, never
  sets screen-level error state.
- Vendor key is `payee`, falling back to `description` when payee is empty.
- Work in the existing worktree at
  `.claude/worktrees/money-vendor-breakdown` (branch
  `worktree-money-vendor-breakdown`).

---

### Task 1: `money.breakdown()` — vendor aggregation

**Files:**
- Modify: `app/money.py` (add `_window`, `_vendor_key`, `breakdown` after
  `summary`)
- Test: `tests/test_money.py` (append)

**Interfaces:**
- Consumes: `db.get_bank_transactions_range(start_iso, end_iso)` — rows with
  `payee`, `description`, `amount`, `posted`, `account_id`, `resolved_flow`.
- Produces: `breakdown(weeks: int, account_id: int | None = None) -> dict`
  returning `{"lines": [{"vendor": str, "count": int, "amount": float}, …]}`
  sorted by net amount descending, ties by vendor name. `count` is the number
  of contributing spending rows (refund rows adjust `amount` only).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_money.py`
  (reuse its `_account` / `_txn` helpers; note `_txn` writes `payee=""`, so
  these tests pass payee via `db.upsert_bank_transaction` directly where it
  matters):

```python
# ── breakdown(): vendor aggregation ────────────────────────────────────────────

def _vendor_txn(db, sfid, account_id, posted, amount, payee, flow="spending",
                user_flow=None, description="RAW DESC"):
    db.upsert_bank_transaction(sfid, account_id, posted, posted, amount,
                               description, payee, "", None)
    db.set_bank_transaction_derived(sfid, flow, None, False)
    if user_flow is not None:
        db.set_bank_flow_override(sfid, user_flow)


def test_breakdown_groups_by_payee_and_sorts_by_net_desc(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "a1", acct["id"], today, -30.0, "Amazon")
    _vendor_txn(db, "a2", acct["id"], today, -20.0, "Amazon")
    _vendor_txn(db, "u1", acct["id"], today, -40.0, "Uber")

    lines = money.breakdown(weeks=1)["lines"]
    assert lines == [
        {"vendor": "Amazon", "count": 2, "amount": 50.0},
        {"vendor": "Uber", "count": 1, "amount": 40.0},
    ]


def test_breakdown_nets_refunds_into_vendor_line_and_keeps_negative_net(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "r1", acct["id"], today, -100.0, "REI")
    _vendor_txn(db, "r2", acct["id"], today, 30.0, "REI", flow="spending",
                user_flow="refund")
    # A refund-only vendor in the window: negative net, still listed, last.
    _vendor_txn(db, "z1", acct["id"], today, 25.0, "Zappos", flow="spending",
                user_flow="refund")

    lines = money.breakdown(weeks=1)["lines"]
    assert lines[0] == {"vendor": "REI", "count": 1, "amount": 70.0}
    assert lines[-1] == {"vendor": "Zappos", "count": 0, "amount": -25.0}


def test_breakdown_excludes_non_spending_flows_and_positive_spending_rows(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "t1", acct["id"], today, -500.0, "Vanguard", flow="transfer")
    _vendor_txn(db, "i1", acct["id"], today, 2000.0, "Payroll", flow="income")
    # Mis-signed spending row (positive) is inert, mirroring _totals().
    _vendor_txn(db, "s1", acct["id"], today, 15.0, "Oddity")

    assert money.breakdown(weeks=1)["lines"] == []


def test_breakdown_account_filter(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct_a = _account(db, "acct-a")
    acct_b = _account(db, "acct-b")
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "a1", acct_a["id"], today, -10.0, "Amazon")
    _vendor_txn(db, "b1", acct_b["id"], today, -99.0, "Uber")

    lines = money.breakdown(weeks=1, account_id=acct_a["id"])["lines"]
    assert lines == [{"vendor": "Amazon", "count": 1, "amount": 10.0}]


def test_breakdown_vendor_key_falls_back_to_description_when_payee_empty(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "e1", acct["id"], today, -12.0, "", description="CHECK 1042")

    lines = money.breakdown(weeks=1)["lines"]
    assert lines == [{"vendor": "CHECK 1042", "count": 1, "amount": 12.0}]


def test_breakdown_window_excludes_older_weeks(temp_db_path):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct = _account(db)
    monday = _this_monday(scorecard, metrics)
    old = (monday - timedelta(weeks=2)).isoformat()
    _vendor_txn(db, "old1", acct["id"], old, -50.0, "Amazon")
    _vendor_txn(db, "new1", acct["id"], monday.isoformat(), -10.0, "Amazon")

    lines = money.breakdown(weeks=1)["lines"]
    assert lines == [{"vendor": "Amazon", "count": 1, "amount": 10.0}]


def test_breakdown_rounds_once_per_group(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    # Three thirds that each round to 3.33 but sum to 10.0 raw.
    for i, amt in enumerate((-3.334, -3.333, -3.333)):
        _vendor_txn(db, f"c{i}", acct["id"], today, amt, "Cafe")

    lines = money.breakdown(weeks=1)["lines"]
    assert lines == [{"vendor": "Cafe", "count": 3, "amount": 10.0}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_money.py -v -k breakdown`
Expected: FAIL — `AttributeError: module 'app.money' has no attribute 'breakdown'`

- [ ] **Step 3: Implement** — in `app/money.py`, after `summary()`:

```python
def _window(weeks: int) -> tuple:
    """The same window summary() computes: `weeks` Monday-starts back through
    the end (Sunday) of the current in-progress week."""
    this_monday = metrics.week_bounds(_local_today())[0]
    start = (this_monday - timedelta(weeks=weeks - 1)).isoformat()
    end = metrics.week_bounds(this_monday)[1].isoformat()
    return start, end


def _vendor_key(t: dict) -> str:
    return t["payee"] or t["description"]


def breakdown(weeks: int, account_id=None) -> dict:
    """Spending grouped by vendor (payee, description fallback) over the same
    window summary() uses. Refund rows net into their vendor's line — a
    refund-only vendor shows a negative net, which is the true figure. Raw
    sums per group, rounded once at the end (see _totals's rounding note).
    `count` is contributing spending rows only; refunds adjust amount, not
    count."""
    weeks = _clamp_weeks(weeks)
    start, end = _window(weeks)
    txns = db.get_bank_transactions_range(start, end)
    if account_id is not None:
        txns = [t for t in txns if t["account_id"] == account_id]

    groups: dict = {}
    for t in txns:
        if t["resolved_flow"] == "spending" and t["amount"] < 0:
            g = groups.setdefault(_vendor_key(t), {"count": 0, "raw": 0.0})
            g["count"] += 1
            g["raw"] += -t["amount"]
        elif t["resolved_flow"] == "refund" and t["amount"] > 0:
            g = groups.setdefault(_vendor_key(t), {"count": 0, "raw": 0.0})
            g["raw"] -= t["amount"]

    lines = [
        {"vendor": vendor, "count": g["count"], "amount": round(g["raw"], 2)}
        for vendor, g in groups.items()
    ]
    lines.sort(key=lambda l: (-l["amount"], l["vendor"]))
    return {"lines": lines}
```

Also extend the module docstring's route list mention if present — no other
changes to `summary()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_money.py -v`
Expected: all PASS (new and pre-existing).

- [ ] **Step 5: Commit**

```bash
git add app/money.py tests/test_money.py
git commit -m "feat(api): vendor breakdown aggregation in app/money.py"
```

---

### Task 2: `money.breakdown_rows()` — drill-down

**Files:**
- Modify: `app/money.py` (add after `breakdown`)
- Test: `tests/test_money.py` (append)

**Interfaces:**
- Consumes: `_window`, `_vendor_key`, `_clamp_weeks`, `_clamp_triage_limit`
  from Task 1 / existing module.
- Produces:
  `breakdown_rows(weeks: int, vendor: str, account_id=None, limit: int = 100) -> dict`
  returning `{"rows": [{"simplefin_id", "posted", "amount", "account_name",
  "resolved_flow", "user_note"}, …]}` newest-first, capped at `limit`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_money.py`:

```python
# ── breakdown_rows(): per-vendor drill-down ────────────────────────────────────

def test_breakdown_rows_returns_vendor_rows_newest_first(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today()
    d0, d1 = today.isoformat(), (today - timedelta(days=1)).isoformat()
    _vendor_txn(db, "a1", acct["id"], d1, -30.0, "Amazon")
    _vendor_txn(db, "a2", acct["id"], d0, -20.0, "Amazon")
    _vendor_txn(db, "a3", acct["id"], d0, 5.0, "Amazon", user_flow="refund")
    _vendor_txn(db, "u1", acct["id"], d0, -40.0, "Uber")

    rows = money.breakdown_rows(weeks=1, vendor="Amazon")["rows"]
    assert [r["simplefin_id"] for r in rows] == ["a3", "a2", "a1"]
    assert rows[0]["resolved_flow"] == "refund"
    assert set(rows[0]) == {"simplefin_id", "posted", "amount", "account_name",
                            "resolved_flow", "user_note"}


def test_breakdown_rows_respects_account_filter_and_limit_clamp(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct_a = _account(db, "acct-a")
    acct_b = _account(db, "acct-b")
    today = scorecard._local_today().isoformat()
    for i in range(3):
        _vendor_txn(db, f"a{i}", acct_a["id"], today, -10.0, "Amazon")
    _vendor_txn(db, "b1", acct_b["id"], today, -10.0, "Amazon")

    rows = money.breakdown_rows(weeks=1, vendor="Amazon",
                                account_id=acct_a["id"])["rows"]
    assert len(rows) == 3

    # limit clamps low to 1 (the same 1-200 clamp triage uses)
    rows = money.breakdown_rows(weeks=1, vendor="Amazon", limit=0)["rows"]
    assert len(rows) == 1


def test_breakdown_rows_excludes_other_flows_for_same_vendor(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "v1", acct["id"], today, -900.0, "Vanguard", flow="investment")

    assert money.breakdown_rows(weeks=1, vendor="Vanguard")["rows"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_money.py -v -k breakdown_rows`
Expected: FAIL — no attribute `breakdown_rows`.

- [ ] **Step 3: Implement** — in `app/money.py`, after `breakdown()`:

```python
def breakdown_rows(weeks: int, vendor: str, account_id=None, limit: int = 100) -> dict:
    """The transactions behind one breakdown line: that vendor's contributing
    rows (spending negative side + refund positive side) in the same window,
    newest first, capped at `limit` (clamped 1-200 like triage)."""
    weeks = _clamp_weeks(weeks)
    limit = _clamp_triage_limit(limit)
    start, end = _window(weeks)
    txns = db.get_bank_transactions_range(start, end)
    rows = [
        t for t in txns
        if _vendor_key(t) == vendor
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
    } for t in rows[:limit]]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_money.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/money.py tests/test_money.py
git commit -m "feat(api): per-vendor drill-down rows in app/money.py"
```

---

### Task 3: Routes — `/api/bank/breakdown` and `/api/bank/breakdown/rows`

**Files:**
- Modify: `app/routes.py` (add after the `/bank/summary` route, ~line 320)
- Test: `tests/test_api_routes.py` (append; follow the file's existing
  logged-in-client fixture conventions — read its `/bank/summary` tests first
  and mirror them)

**Interfaces:**
- Consumes: `money.breakdown(weeks, account_id)`,
  `money.breakdown_rows(weeks, vendor, account_id, limit)` from Tasks 1–2;
  `router`, `HTTPException` already imported in `app/routes.py`.
- Produces: `GET /api/bank/breakdown?weeks=12&by=payee&account_id=<int opt>`
  and `GET /api/bank/breakdown/rows?weeks=12&payee=<str>&account_id=<int
  opt>&limit=<int opt>`. `by` other than `"payee"` → 400 (Phase 2 widens to
  `label`). Missing/empty `payee` on the rows route → FastAPI 422 (required
  query param).

- [ ] **Step 1: Write the failing tests** — append to
  `tests/test_api_routes.py`, using the same authenticated-client pattern as
  the existing `/bank/summary` test in that file (mirror its fixture usage
  exactly; do not invent a new client setup):

```python
def test_bank_breakdown_shape_and_bad_by(client):
    r = client.get("/api/bank/breakdown?weeks=4")
    assert r.status_code == 200
    assert r.json() == {"lines": []}

    r = client.get("/api/bank/breakdown?weeks=4&by=label")
    assert r.status_code == 400


def test_bank_breakdown_rows_requires_payee(client):
    r = client.get("/api/bank/breakdown/rows?weeks=4")
    assert r.status_code == 422

    r = client.get("/api/bank/breakdown/rows?weeks=4&payee=Amazon")
    assert r.status_code == 200
    assert r.json() == {"rows": []}
```

(If the file's client fixture is named differently — e.g. `auth_client` —
use that name; the two tests above are the only new assertions.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_routes.py -v -k breakdown`
Expected: FAIL — 404s (routes don't exist).

- [ ] **Step 3: Implement** — in `app/routes.py`, directly after
  `get_bank_summary`:

```python
@router.get("/bank/breakdown")
def get_bank_breakdown(weeks: int = 12, by: str = "payee",
                       account_id: Optional[int] = None):
    if by != "payee":
        raise HTTPException(status_code=400,
                            detail="by must be 'payee' (labels arrive in a later phase)")
    return money.breakdown(weeks, account_id=account_id)


@router.get("/bank/breakdown/rows")
def get_bank_breakdown_rows(weeks: int, payee: str,
                            account_id: Optional[int] = None, limit: int = 100):
    return money.breakdown_rows(weeks, payee, account_id=account_id, limit=limit)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_routes.py -v && pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes.py tests/test_api_routes.py
git commit -m "feat(api): bank breakdown and drill-down routes"
```

---

### Task 4: `lib.ts` — types + top-15/tail split helper

**Files:**
- Modify: `frontend/src/lib.ts` (append)
- Test: `frontend/src/lib.test.ts` (append)

**Interfaces:**
- Produces:

```typescript
export interface VendorLine { vendor: string; count: number; amount: number }
export interface VendorTail { vendors: number; count: number; amount: number }
export function vendorSplit(lines: VendorLine[], topN?: number):
  { top: VendorLine[]; tail: VendorTail | null; rest: VendorLine[] }
```

`topN` defaults to 15. `tail` is null when `lines.length <= topN` (no
"Everything else" line for a tail of zero — and a tail of exactly one vendor
is promoted into `top` instead, since "Everything else (1 vendor)" is longer
than just showing the line). `tail.amount` sums raw amounts (they are
already 2-dp from the API; summing them is display-only, not re-rounded).

- [ ] **Step 1: Write the failing tests** — append to
  `frontend/src/lib.test.ts`:

```typescript
import { vendorSplit, type VendorLine } from "./lib";

const line = (vendor: string, amount: number): VendorLine =>
  ({ vendor, count: 1, amount });

describe("vendorSplit", () => {
  it("returns everything in top when at or under the cutoff", () => {
    const lines = [line("A", 30), line("B", 20)];
    const { top, tail, rest } = vendorSplit(lines, 15);
    expect(top).toEqual(lines);
    expect(tail).toBeNull();
    expect(rest).toEqual([]);
  });

  it("promotes a single-vendor tail instead of an 'Everything else (1)'", () => {
    const lines = [line("A", 30), line("B", 20), line("C", 10)];
    const { top, tail } = vendorSplit(lines, 2);
    expect(top).toEqual(lines);
    expect(tail).toBeNull();
  });

  it("aggregates a multi-vendor tail and exposes its lines as rest", () => {
    const lines = [line("A", 30), line("B", 20), line("C", 10), line("D", 5)];
    const { top, tail, rest } = vendorSplit(lines, 2);
    expect(top).toEqual([line("A", 30), line("B", 20)]);
    expect(tail).toEqual({ vendors: 2, count: 2, amount: 15 });
    expect(rest).toEqual([line("C", 10), line("D", 5)]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test -- --run`
Expected: FAIL — `vendorSplit` is not exported.

- [ ] **Step 3: Implement** — append to `frontend/src/lib.ts`:

```typescript
export interface VendorLine { vendor: string; count: number; amount: number }
export interface VendorTail { vendors: number; count: number; amount: number }

// Top-N + "Everything else" split for the vendor breakdown. A tail of one
// vendor is promoted into `top` — "Everything else (1 vendor)" would be
// longer than the line it hides.
export function vendorSplit(lines: VendorLine[], topN: number = 15):
  { top: VendorLine[]; tail: VendorTail | null; rest: VendorLine[] } {
  if (lines.length <= topN + 1) return { top: lines, tail: null, rest: [] };
  const top = lines.slice(0, topN);
  const rest = lines.slice(topN);
  const tail = {
    vendors: rest.length,
    count: rest.reduce((s, l) => s + l.count, 0),
    amount: rest.reduce((s, l) => s + l.amount, 0),
  };
  return { top, tail, rest };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test -- --run`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib.ts frontend/src/lib.test.ts
git commit -m "feat(frontend): vendorSplit helper for the breakdown tail"
```

---

### Task 5: `VendorBreakdown` component + Money screen section

**Files:**
- Create: `frontend/src/components/VendorBreakdown.tsx`
- Modify: `frontend/src/screens/Money.tsx`
- Modify: `frontend/src/styles.css` (chip styles only if `.chip` doesn't
  already exist — grep first)

**Interfaces:**
- Consumes: `GET /api/bank/breakdown`, `GET /api/bank/breakdown/rows`,
  `GET /api/bank/accounts` (existing route; rows carry `id`, `name`,
  `active`); `vendorSplit`, `money`, `dayRowDate`, `flowLabel` from `lib.ts`;
  `apiGet` from `../api`.
- Produces: `<VendorBreakdown weeks={12} />` — fully self-contained section
  (own fetching, own quiet failure). Money.tsx renders it inside the
  `bankSectionsVisible` block, after the movement-flows section.

- [ ] **Step 1: Write the component** —
  `frontend/src/components/VendorBreakdown.tsx`:

```typescript
import { useEffect, useState } from "react";
import { apiGet } from "../api";
import { dayRowDate, flowLabel, money, vendorSplit, type VendorLine } from "../lib";

interface AccountRow { id: number; name: string; active: boolean }
interface DrillRow {
  simplefin_id: string;
  posted: string;
  amount: number;
  account_name: string;
  resolved_flow: string;
  user_note: string | null;
}

// "Where it went" — bank spending grouped by vendor, filterable by account,
// with a per-vendor transaction drill-down. Secondary surface: any failed
// fetch hides the section (lines === null), never the screen.
export default function VendorBreakdown({ weeks }: { weeks: number }) {
  const [lines, setLines] = useState<VendorLine[] | null>(null);
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [showRest, setShowRest] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [drill, setDrill] = useState<Record<string, DrillRow[]>>({});

  useEffect(() => {
    apiGet<AccountRow[]>("/bank/accounts")
      .then((rows) => setAccounts(rows.filter((a) => a.active)))
      .catch(() => setAccounts([]));
  }, []);

  useEffect(() => {
    const acct = accountId !== null ? `&account_id=${accountId}` : "";
    setExpanded(null);
    setDrill({});
    setShowRest(false);
    apiGet<{ lines: VendorLine[] }>(`/bank/breakdown?weeks=${weeks}${acct}`)
      .then((d) => setLines(d.lines))
      .catch(() => setLines(null));
  }, [weeks, accountId]);

  if (!lines || lines.length === 0) return null;

  const { top, tail, rest } = vendorSplit(lines);
  const shown = showRest ? [...top, ...rest] : top;

  const toggleVendor = (vendor: string) => {
    if (expanded === vendor) {
      setExpanded(null);
      return;
    }
    setExpanded(vendor);
    if (!drill[vendor]) {
      const acct = accountId !== null ? `&account_id=${accountId}` : "";
      apiGet<{ rows: DrillRow[] }>(
        `/bank/breakdown/rows?weeks=${weeks}&payee=${encodeURIComponent(vendor)}${acct}`,
      )
        .then((d) => setDrill((prev) => ({ ...prev, [vendor]: d.rows })))
        .catch(() => {});
    }
  };

  return (
    <>
      <p className="section-label">Where it went</p>
      {accounts.length > 1 && (
        <div className="chip-row">
          <button
            type="button"
            className={accountId === null ? "chip chip-on" : "chip"}
            onClick={() => setAccountId(null)}
          >
            All accounts
          </button>
          {accounts.map((a) => (
            <button
              key={a.id}
              type="button"
              className={accountId === a.id ? "chip chip-on" : "chip"}
              onClick={() => setAccountId((prev) => (prev === a.id ? null : a.id))}
            >
              {a.name}
            </button>
          ))}
        </div>
      )}
      <div className="spend">
        {shown.map((l) => (
          <div key={l.vendor}>
            <button type="button" className="vendor-row" onClick={() => toggleVendor(l.vendor)}>
              <span className="spend-service">{l.vendor}</span>
              <span className="spend-amount num">
                {l.count > 0 ? `${l.count} · ` : ""}{money(l.amount)}
              </span>
            </button>
            {expanded === l.vendor && drill[l.vendor] && (
              <div className="vendor-drill">
                {drill[l.vendor].map((r) => (
                  <div className="vendor-drill-row" key={r.simplefin_id}>
                    <span>
                      {dayRowDate(r.posted.slice(0, 10)).monthDay}
                      {r.resolved_flow === "refund" && ` · ${flowLabel("refund")}`}
                      {" · "}{r.account_name}
                      {r.user_note && <span className="triage-note">{r.user_note}</span>}
                    </span>
                    <span className="num">{money(Math.abs(r.amount))}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        {tail && !showRest && (
          <button type="button" className="vendor-row" onClick={() => setShowRest(true)}>
            <span className="spend-service">Everything else ({tail.vendors} vendors)</span>
            <span className="spend-amount num">{tail.count} · {money(tail.amount)}</span>
          </button>
        )}
      </div>
    </>
  );
}
```

Note: if `flowLabel("refund")` does not return a short human word, inline
the literal `"Refund"` instead — check `flowLabel`'s cases while editing.

- [ ] **Step 2: Wire into Money.tsx** — import and render inside the
  existing `bankSectionsVisible && summary` block, immediately after the
  movement-flows (`Where the rest went`) section and before `Money in`:

```typescript
import VendorBreakdown from "../components/VendorBreakdown";
// … inside the bankSectionsVisible block, after the MOVEMENT_FLOWS section:
          <VendorBreakdown weeks={12} />
```

- [ ] **Step 3: Styles** — first `grep -n "chip\|vendor-" frontend/src/styles.css`.
  `dayChips` suggests chip styles may exist; reuse them if the classnames
  match. Add only what's missing, using existing tokens (no new colors —
  chips and rows use the same surface/border/text tokens as `.spend-row`
  and existing buttons):

```css
.chip-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 12px; }
.chip {
  padding: 4px 12px; border-radius: 999px;
  border: 1px solid var(--border); background: transparent;
  color: var(--text-quiet); font-size: 0.85rem; cursor: pointer;
}
.chip-on { border-color: var(--text); color: var(--text); }
.vendor-row {
  display: flex; justify-content: space-between; width: 100%;
  padding: 6px 0; background: none; border: none;
  color: inherit; font: inherit; cursor: pointer; text-align: left;
}
.vendor-drill { padding: 0 0 6px 12px; }
.vendor-drill-row {
  display: flex; justify-content: space-between;
  padding: 2px 0; color: var(--text-quiet); font-size: 0.9rem;
}
```

(Verify the token names — `--border`, `--text`, `--text-quiet` — against
what `styles.css` actually defines and use its real names.)

- [ ] **Step 4: Verify**

Run: `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`
Expected: tests pass, no type errors, build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/VendorBreakdown.tsx frontend/src/screens/Money.tsx frontend/src/styles.css
git commit -m "feat(frontend): 'Where it went' vendor breakdown on the Money screen"
```

---

### Task 6: Full verification + docs proposal

**Files:**
- None created; CLAUDE.md change is proposed, not made.

- [ ] **Step 1: Run the full suites**

Run: `pytest tests/ -v` then `cd frontend && npm test -- --run && npm run build`
Expected: everything passes.

- [ ] **Step 2: Manual look** — with the local dev servers up
  (`uvicorn main:app --reload --port 8080` + `cd frontend && npm run dev`),
  open the Money screen: section renders with real local data (the local
  SQLite has 965 rows), chips filter, a vendor line expands, "Everything
  else" expands, negative-net vendors render via `money()` without crashing.

- [ ] **Step 3: Propose (do not make) the CLAUDE.md update** — the repo
  guide's `app/routes.py` bullet lists bank routes
  (`bank debug/role/summary/triage/…`); adding `breakdown` there needs the
  user's explicit OK. Ask.

- [ ] **Step 4: Report** — summarize what shipped, then follow
  superpowers:finishing-a-development-branch for merge/PR choice.
