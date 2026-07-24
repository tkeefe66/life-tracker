# Account Nicknames + Breakdown Fixes + Investments View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the vendor in Labels-view drill rows, let the user nickname bank accounts (resolved everywhere in SQL), stop investment-role chips from blanking the breakdown, and add a live-only Investments section (holdings vs. cost basis, never persisted).

**Architecture:** Two approved specs, one plan. Part A (Tasks 1–4) is the nickname column + frontend fixes: a user-set `nickname` on `bank_accounts` following the `role`/`active` pattern, resolved via `COALESCE` in exactly two SQL sites so every surface agrees. Part B (Tasks 5–9) is the investments view: `normalize_holdings` beside `normalize()` in the SimpleFIN service, an `investments()` builder in `app/money.py` that fetches live and persists nothing, a thin 503-mapping route, and a fetch-on-expand component.

**Tech Stack:** FastAPI + SQLite/Postgres via `database.py`, React + Vite frontend, pytest + vitest.

## Global Constraints

- Specs: `docs/superpowers/specs/2026-07-24-account-nicknames-and-breakdown-fixes-design.md` and `docs/superpowers/specs/2026-07-24-investments-view-design.md` — read both before starting.
- SQL only in `database.py`; env reads only in `config.py`; no Claude calls anywhere in this plan.
- Redaction boundary: nothing from SimpleFIN error paths crosses except `SimpleFinError.status` (closed set). Holdings/market values are NEVER written to the DB, `app_settings`, or logs.
- The sync never touches user columns: `nickname` joins `role`/`active` in that set. `upsert_bank_account` must not gain a nickname parameter.
- Money formatting: `money()`/`signedMoney()` from `lib.ts`; U+2212 for negatives; null-check, never truthiness.
- Secondary surfaces fail quietly — a failed investments or breakdown fetch never sets screen-level error.
- No new chart hues: gain/loss colors reuse existing theme-aware tokens (`--chart-social` up, `--danger` down).
- Verify with the real suites before claiming done: `pytest tests/ -v`; in `frontend/`: `npm test -- --run && npm run build`.

---

## Part A — Account nicknames + breakdown fixes

### Task 1: `nickname` column, resolution, and setter in `database.py`

**Files:**
- Modify: `database.py` (migration block near line 682 `user_no_label` comment; `get_bank_accounts` ~line 1145; new `set_bank_account_nickname` after `set_bank_account_role` ~line 1160; `_BANK_TXN_SELECT` ~line 1526)
- Test: `tests/test_database_bank.py`

**Interfaces:**
- Produces: `db.set_bank_account_nickname(simplefin_id: str, nickname: str | None) -> bool` (True iff a row updated; empty/whitespace stores NULL). `db.get_bank_accounts()` rows gain `nickname` (nullable) and `display_name` (resolved). Every `_BANK_TXN_SELECT` consumer's `account_name` becomes the resolved name.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_database_bank.py`:

```python
def test_nickname_resolves_in_accounts_and_transactions(temp_db_path):
    import database as db
    db.upsert_bank_account("acct-n1", "EVERYDAY CHECKING ...7395 (7395)", "Wells Fargo", "checking")
    acct_id = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-n1")["id"]
    db.upsert_bank_transaction("txn-n1", acct_id, "2026-07-20", None, -12.5, description="COFFEE")

    assert db.set_bank_account_nickname("acct-n1", "Checking") is True
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-n1")
    assert acct["nickname"] == "Checking"
    assert acct["display_name"] == "Checking"
    # resolution reaches the transactions join too
    row = db.get_bank_transactions_range("2026-07-20", "2026-07-20")[0]
    assert row["account_name"] == "Checking"


def test_nickname_empty_clears_to_bank_name(temp_db_path):
    import database as db
    db.upsert_bank_account("acct-n2", "ROTH IRA (9304)", "Fidelity", "investment")
    db.set_bank_account_nickname("acct-n2", "Roth")
    assert db.set_bank_account_nickname("acct-n2", "   ") is True
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-n2")
    assert acct["nickname"] is None
    assert acct["display_name"] == "ROTH IRA (9304)"


def test_nickname_unknown_account_returns_false(temp_db_path):
    import database as db
    assert db.set_bank_account_nickname("nope", "X") is False


def test_sync_upsert_preserves_nickname(temp_db_path):
    import database as db
    db.upsert_bank_account("acct-n3", "OLD NAME", "Org", "checking")
    db.set_bank_account_nickname("acct-n3", "Mine")
    db.upsert_bank_account("acct-n3", "NEW NAME", "Org", "checking")  # a later sync
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-n3")
    assert acct["nickname"] == "Mine"
    assert acct["display_name"] == "Mine"
    assert acct["name"] == "NEW NAME"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_database_bank.py -k nickname -v`
Expected: FAIL — `AttributeError: module 'database' has no attribute 'set_bank_account_nickname'` (and KeyError `nickname` after partial implementation).

- [ ] **Step 3: Implement.** Four edits in `database.py`:

(a) Migration — append inside `_init_v2_tables()` right after the `user_no_label` migration block, same idiom:

```python
        # nickname: nullable TEXT on bank_accounts. A USER column — the sync's
        # upsert never touches it (same footing as role/active). Resolved via
        # COALESCE(NULLIF(nickname, ''), name) in exactly two places:
        # get_bank_accounts (display_name) and _BANK_TXN_SELECT (account_name).
        if USE_POSTGRES:
            c.execute("ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS nickname TEXT")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(bank_accounts)").fetchall()]
            if "nickname" not in cols:
                c.execute("ALTER TABLE bank_accounts ADD COLUMN nickname TEXT")
```

(b) `get_bank_accounts()` — replace the SELECT:

```python
def get_bank_accounts():
    with _cursor() as c:
        c.execute("""SELECT id, simplefin_id, name, org, kind, role, active, last_synced_at,
                            nickname,
                            COALESCE(NULLIF(nickname, ''), name) AS display_name
                     FROM bank_accounts ORDER BY id""")
        return _bank_account_rows(c.fetchall())
```

(c) New setter after `set_bank_account_role`:

```python
def set_bank_account_nickname(simplefin_id, nickname):
    """Returns True iff a row was updated (unknown id -> 404 at the route).
    Empty/whitespace clears to NULL so the bank's own name shows again.
    A user column — the sync never reads or writes it."""
    nickname = (nickname or "").strip() or None
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_accounts SET nickname = {p} WHERE simplefin_id = {p}",
                  (nickname, simplefin_id))
        return c.rowcount > 0
```

(d) `_BANK_TXN_SELECT` — change the account_name line:

```python
           a.role AS account_role,
           COALESCE(NULLIF(a.nickname, ''), a.name) AS account_name,
```

- [ ] **Step 4: Run the full backend suite** (the join change touches many consumers)

Run: `pytest tests/ -v`
Expected: all PASS, including the four new tests.

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_bank.py
git commit -m "feat(db): user-set bank account nickname, resolved in SQL"
```

---

### Task 2: Nickname route

**Files:**
- Modify: `app/routes.py` (after the role route, ~line 315)
- Test: `tests/test_api_routes.py`

**Interfaces:**
- Consumes: `db.set_bank_account_nickname` (Task 1).
- Produces: `POST /api/bank/accounts/{simplefin_id}/nickname`, body `{"nickname": "…"}` → `{"ok": true, "simplefin_id": …, "nickname": <str|null>}`; 404 unknown account; 400 non-string nickname.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api_routes.py`:

```python
def test_bank_account_nickname_set_clear_and_404(temp_db_path):
    import database as db
    client = _client(temp_db_path)
    db.upsert_bank_account("acct-r1", "EVERYDAY CHECKING ...7395 (7395)", "Wells Fargo", "checking")

    r = client.post("/api/bank/accounts/acct-r1/nickname", json={"nickname": "Checking"})
    assert r.status_code == 200 and r.json()["nickname"] == "Checking"
    acct = next(a for a in client.get("/api/bank/accounts").json()
                if a["simplefin_id"] == "acct-r1")
    assert acct["display_name"] == "Checking"

    r = client.post("/api/bank/accounts/acct-r1/nickname", json={"nickname": ""})
    assert r.status_code == 200 and r.json()["nickname"] is None

    assert client.post("/api/bank/accounts/nope/nickname",
                       json={"nickname": "X"}).status_code == 404
    assert client.post("/api/bank/accounts/acct-r1/nickname",
                       json={"nickname": 7}).status_code == 400
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_api_routes.py::test_bank_account_nickname_set_clear_and_404 -v`
Expected: FAIL with 404/405 (route doesn't exist).

- [ ] **Step 3: Implement** — in `app/routes.py`, directly after `set_bank_account_role`:

```python
@router.post("/bank/accounts/{simplefin_id}/nickname")
def set_bank_account_nickname(simplefin_id: str, body: dict):
    """Set or clear an account's display nickname. Empty string clears back to
    the bank's own name. Unlike roles, takes effect immediately — resolution
    is COALESCE in SQL, no sync involved."""
    nickname = (body or {}).get("nickname")
    if nickname is not None and not isinstance(nickname, str):
        raise HTTPException(status_code=400, detail="nickname must be a string")
    if not db.set_bank_account_nickname(simplefin_id, nickname):
        raise HTTPException(status_code=404, detail="unknown account")
    return {"ok": True, "simplefin_id": simplefin_id,
            "nickname": (nickname or "").strip() or None}
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_api_routes.py::test_bank_account_nickname_set_clear_and_404 -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes.py tests/test_api_routes.py
git commit -m "feat(api): bank account nickname route"
```

---

### Task 3: Nickname input in Settings

**Files:**
- Modify: `frontend/src/screens/Settings.tsx` (BankAccount interface ~line 24; handler beside `updateRole` ~line 101; the Bank accounts row ~line 236)

**Interfaces:**
- Consumes: `POST /bank/accounts/{id}/nickname` (Task 2); `/bank/accounts` now returns `nickname`/`display_name`.

- [ ] **Step 1: Extend the interface** — add to `BankAccount`:

```ts
  nickname: string | null;
  display_name: string;
```

- [ ] **Step 2: Add the handler** — after `updateRole`:

```tsx
  const updateNickname = async (simplefinId: string, raw: string) => {
    setSaveError("");
    const nickname = raw.trim();
    const prev = bankAccounts;
    const current = prev?.find((a) => a.simplefin_id === simplefinId);
    if (!current || (current.nickname ?? "") === nickname) return;
    setBankAccounts((accts) =>
      accts ? accts.map((a) => (a.simplefin_id === simplefinId
        ? { ...a, nickname: nickname || null, display_name: nickname || a.name }
        : a)) : accts
    );
    try {
      await apiSend("POST", `/bank/accounts/${simplefinId}/nickname`, { nickname });
    } catch (e) {
      setBankAccounts(prev);
      setSaveError((e as Error).message);
    }
  };
```

- [ ] **Step 3: Add the input to each account row** — replace the row body so name, nickname input, and role select all fit:

```tsx
              <label className="row" key={a.simplefin_id}>
                <span className="grow">{a.org} — {a.name}</span>
                <input
                  type="text"
                  className="nickname-input"
                  aria-label="Nickname"
                  placeholder="Nickname"
                  defaultValue={a.nickname ?? ""}
                  onBlur={(e) => updateNickname(a.simplefin_id, e.currentTarget.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
                />
                <select
                  value={a.role}
                  onChange={(e) => updateRole(a.simplefin_id, e.target.value)}
                >
                  {BANK_ROLES.map((role) => (
                    <option key={role} value={role}>{role}</option>
                  ))}
                </select>
              </label>
```

Add to `frontend/src/styles.css` (near other input styles):

```css
.nickname-input { max-width: 9rem; }
```

- [ ] **Step 4: Verify**

Run in `frontend/`: `npm test -- --run && npm run build`
Expected: tests pass, `tsc`/`vite build` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/Settings.tsx frontend/src/styles.css
git commit -m "feat(frontend): account nickname editing in Settings"
```

---

### Task 4: VendorBreakdown — vendor in drill rows, nickname chips, investment filter, filtered-empty state

**Files:**
- Modify: `frontend/src/components/VendorBreakdown.tsx`

**Interfaces:**
- Consumes: `/bank/accounts` rows now carrying `display_name` and `role`; drill rows already carry `vendor` and resolved `account_name`.

- [ ] **Step 1: Extend `AccountRow` and the accounts fetch** (~lines 6, 55-59):

```ts
interface AccountRow { id: number; name: string; display_name: string; role: string; active: boolean }
```

```tsx
  useEffect(() => {
    apiGet<AccountRow[]>("/bank/accounts")
      // investment-role accounts get no chip: by construction (bank_flows
      // classify_flow rule 1) their rows are never spending/refund, so the
      // chip could only ever show an empty result.
      .then((rows) => setAccounts(rows.filter((a) => a.active && a.role !== "investment")))
      .catch(() => setAccounts([]));
  }, []);
```

Chip text (~line 303): `{a.display_name}` instead of `{a.name}`.

- [ ] **Step 2: Show the vendor in Labels-view drill rows** — in `renderRow`'s drill map (~line 207), the left span becomes:

```tsx
                    <span>
                      {dayRowDate(r.posted.slice(0, 10)).monthDay}
                      {r.resolved_flow === "refund" && ` · ${flowLabel("refund")}`}
                      {mode === "label" && ` · ${r.vendor}`}
                      {" · "}{r.account_name}
                      {r.user_note && <span className="triage-note">{r.user_note}</span>}
                    </span>
```

- [ ] **Step 3: Fix the filtered-empty blanking** — replace the early return (~line 135):

```tsx
  if (!lines) return null;
  // Hide the whole section only when the UNFILTERED data is empty (no bank
  // spending at all). A filtered-empty result must keep the chips rendered —
  // returning null here would unmount them with no way to deselect.
  if (lines.length === 0 && accountId === null) return null;
```

And where the vendor/label lists render, show an empty state when the current view has no lines (both modes), keeping the chip rows above it:

```tsx
  const viewEmpty = mode === "payee"
    ? lines.length === 0
    : (labelLines?.length ?? 0) === 0;
```

In the JSX, wrap the existing `{mode === "payee" ? (…) : (…)}` list block:

```tsx
      {viewEmpty ? (
        <p className="quiet">No spending in this account over this window.</p>
      ) : mode === "payee" ? (
        /* existing vendor list block unchanged */
      ) : (
        /* existing label list block unchanged */
      )}
```

- [ ] **Step 4: Verify**

Run in `frontend/`: `npm test -- --run && npm run build`
Expected: clean. Then a manual look: Labels view drill shows `date · vendor · account`; no Fidelity chips; clicking an account with no spending shows the quiet line, chips stay.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/VendorBreakdown.tsx
git commit -m "fix(frontend): vendor in label drills, nickname chips, no investment chips, filtered-empty state"
```

---

## Part B — Investments view

### Task 5: `normalize_holdings` in the SimpleFIN service

**Files:**
- Modify: `services/simplefin_service.py` (after `normalize()`)
- Test: `tests/test_simplefin_service.py`

**Interfaces:**
- Produces: `normalize_holdings(payload) -> list[dict]` — `[{simplefin_id, name, org, holdings: [{symbol, description, shares, cost_basis, market_value}]}]`; floats coerced (SimpleFIN sends numerics as strings), non-finite/absent `market_value` drops the holding, zero-holdings accounts omitted, balances never returned.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_simplefin_service.py`:

```python
def test_normalize_holdings_extracts_and_coerces():
    from services.simplefin_service import normalize_holdings
    payload = {"accounts": [
        {"id": "a1", "name": "ROTH IRA", "org": {"name": "Fidelity"},
         "balance": "9999.99",
         "holdings": [
             {"symbol": "VOO", "description": "Vanguard 500", "shares": "1.5",
              "cost_basis": "500.00", "market_value": "600.25"},
             {"symbol": "JUNK", "market_value": "nan"},          # non-finite -> dropped
             {"symbol": "NOMV", "cost_basis": "10"},              # no market_value -> dropped
             "not-a-dict",
         ]},
        {"id": "a2", "name": "CHECKING", "org": "Wells Fargo", "holdings": []},
        {"id": "a3", "name": "NOHOLD"},
    ]}
    out = normalize_holdings(payload)
    assert [a["simplefin_id"] for a in out] == ["a1"]      # empty/missing holdings omitted
    a1 = out[0]
    assert a1["org"] == "Fidelity"
    assert a1["holdings"] == [{"symbol": "VOO", "description": "Vanguard 500",
                               "shares": 1.5, "cost_basis": 500.0, "market_value": 600.25}]
    assert "balance" not in a1


def test_normalize_holdings_tolerates_garbage_payloads():
    from services.simplefin_service import normalize_holdings
    assert normalize_holdings(None) == []
    assert normalize_holdings({"accounts": None}) == []
    assert normalize_holdings({"accounts": [{"holdings": [{}]}]}) == []  # no account id
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_simplefin_service.py -k normalize_holdings -v`
Expected: FAIL — ImportError: cannot import name 'normalize_holdings'.

- [ ] **Step 3: Implement** — append to `services/simplefin_service.py`:

```python
def _to_float(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def normalize_holdings(payload):
    """Per-account holdings from a SimpleFIN payload — the live-only
    investments view's input. Same defensive posture as normalize(): floats
    coerced (bridges send numerics as strings), non-finite dropped, absent
    fields tolerated. A holding with no market_value cannot be displayed and
    is dropped; accounts with no holdings are omitted (every checking/credit
    account has an empty array). Balances still never cross this boundary."""
    out = []
    if not isinstance(payload, dict):
        return out
    for acct in payload.get("accounts", []) or []:
        if not isinstance(acct, dict):
            continue
        sfid = acct.get("id")
        if not sfid:
            continue
        holdings = []
        for h in acct.get("holdings", []) or []:
            if not isinstance(h, dict):
                continue
            mv = _to_float(h.get("market_value"))
            if mv is None:
                continue
            holdings.append({
                "symbol": h.get("symbol") or "?",
                "description": h.get("description") or "",
                "shares": _to_float(h.get("shares")),
                "cost_basis": _to_float(h.get("cost_basis")),
                "market_value": mv,
            })
        if not holdings:
            continue
        org = acct.get("org") or {}
        if isinstance(org, str):
            org = {"name": org}
        elif not isinstance(org, dict):
            org = {}
        out.append({
            "simplefin_id": str(sfid),
            "name": acct.get("name") or "?",
            "org": org.get("name") or org.get("domain") or "",
            "holdings": holdings,
        })
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_simplefin_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/simplefin_service.py tests/test_simplefin_service.py
git commit -m "feat(simplefin): normalize_holdings for the live investments view"
```

---

### Task 6: `investments()` in `app/money.py`

**Files:**
- Modify: `app/money.py` (new function at the end; add `from services import simplefin_service` to imports)
- Test: `tests/test_money.py`

**Interfaces:**
- Consumes: `simplefin_service.is_configured/fetch_accounts/normalize_holdings` (Task 5); `db.get_bank_accounts()` `display_name` (Task 1).
- Produces: `investments() -> dict` per the spec's API shape. Raises `SimpleFinError` upward. Not configured → `{"total": None, "accounts": []}`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_money.py`:

```python
def _fake_holdings_payload():
    return {"accounts": [
        {"id": "inv-1", "name": "ROTH IRA (9304)", "org": {"name": "Fidelity"},
         "holdings": [
             {"symbol": "VOO", "description": "Vanguard 500", "shares": "2",
              "cost_basis": "800.00", "market_value": "1000.00"},
             {"symbol": "GIFT", "description": "No basis", "shares": "1",
              "market_value": "50.00"},                         # null basis
         ]},
        {"id": "inv-2", "name": "401K (4542)", "org": {"name": "Fidelity"},
         "holdings": [
             {"symbol": "TDF", "description": "Target date", "shares": "10",
              "cost_basis": "2000.00", "market_value": "1900.00"},
         ]},
    ]}


def test_investments_math_null_basis_and_sorting(temp_db_path, monkeypatch):
    import database as db
    from app import money
    from services import simplefin_service
    db.upsert_bank_account("inv-1", "ROTH IRA (9304)", "Fidelity", "investment")
    db.set_bank_account_nickname("inv-1", "Roth")
    monkeypatch.setattr(simplefin_service, "is_configured", lambda: True)
    monkeypatch.setattr(simplefin_service, "fetch_accounts",
                        lambda days=None: _fake_holdings_payload())

    out = money.investments()
    # accounts sorted by market value desc: 401k (1900) before Roth (1050)
    assert [a["simplefin_id"] for a in out["accounts"]] == ["inv-2", "inv-1"]
    roth = out["accounts"][1]
    assert roth["name"] == "Roth"                       # nickname resolution
    assert roth["market_value"] == 1050.0               # null-basis mv still counts
    assert roth["cost_basis"] == 800.0                  # null-basis excluded
    assert roth["gain"] == 200.0 and roth["gain_pct"] == 25.0
    voo = roth["holdings"][0]                           # holdings sorted mv desc
    assert voo["symbol"] == "VOO" and voo["gain"] == 200.0 and voo["gain_pct"] == 25.0
    gift = roth["holdings"][1]
    assert gift["gain"] is None and gift["gain_pct"] is None and gift["cost_basis"] is None
    tdf = out["accounts"][0]["holdings"][0]
    assert tdf["gain"] == -100.0 and tdf["gain_pct"] == -5.0
    total = out["total"]
    assert total["market_value"] == 2950.0
    assert total["cost_basis"] == 2800.0
    assert total["gain"] == 100.0
    assert total["gain_pct"] == 3.6                     # round((2900-2800)/2800*100, 1)


def test_investments_not_configured_returns_empty(temp_db_path, monkeypatch):
    from app import money
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "is_configured", lambda: False)
    assert money.investments() == {"total": None, "accounts": []}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_money.py -k investments -v`
Expected: FAIL — `AttributeError: module 'app.money' has no attribute 'investments'`.

- [ ] **Step 3: Implement** — in `app/money.py`, add `from services import simplefin_service` to the imports, then append:

```python
def investments() -> dict:
    """Live holdings vs. cost basis — fetched from SimpleFIN on demand,
    computed, returned, and deliberately never persisted (no DB row, no
    app_settings, no log line): the companion of the bank spec's
    balances-are-never-stored rule. Gains are vs. cost basis only; a
    missing/zero basis yields null gain rather than an error, and its
    market value still counts toward totals while staying out of
    cost_basis/gain_pct. Raw sums, rounded once at the end (see _totals'
    rounding note). Raises SimpleFinError upward — the route maps it to 503."""
    if not simplefin_service.is_configured():
        return {"total": None, "accounts": []}
    payload = simplefin_service.fetch_accounts(days=1)
    names = {a["simplefin_id"]: a["display_name"] for a in db.get_bank_accounts()}

    def gain_fields(mv, cb):
        """cb is the basis-valid sum (None/0/negative -> no gain figures)."""
        if not cb or cb <= 0:
            return {"cost_basis": round(cb, 2) if cb else None, "gain": None, "gain_pct": None}
        return {"cost_basis": round(cb, 2), "gain": round(mv - cb, 2),
                "gain_pct": round((mv - cb) / cb * 100, 1)}

    accounts = []
    t_mv = t_cb = t_mv_basis = 0.0
    for a in simplefin_service.normalize_holdings(payload):
        holdings = sorted(a["holdings"], key=lambda h: -h["market_value"])
        mv = sum(h["market_value"] for h in holdings)
        basis = [(h["market_value"], h["cost_basis"]) for h in holdings
                 if h["cost_basis"] is not None and h["cost_basis"] > 0]
        cb = sum(c for _, c in basis)
        mv_basis = sum(m for m, _ in basis)
        accounts.append({
            "simplefin_id": a["simplefin_id"],
            "name": names.get(a["simplefin_id"], a["name"]),
            "market_value": round(mv, 2),
            **gain_fields(mv_basis, cb),
            "holdings": [{
                "symbol": h["symbol"],
                "description": h["description"],
                "shares": h["shares"],
                "market_value": round(h["market_value"], 2),
                **gain_fields(h["market_value"], h["cost_basis"]),
            } for h in holdings],
        })
        t_mv += mv
        t_cb += cb
        t_mv_basis += mv_basis
    accounts.sort(key=lambda a: -a["market_value"])
    return {
        "total": {"market_value": round(t_mv, 2), **gain_fields(t_mv_basis, t_cb)},
        "accounts": accounts,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_money.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/money.py tests/test_money.py
git commit -m "feat(money): live investments builder — fetch, compute, never persist"
```

---

### Task 7: Investments route

**Files:**
- Modify: `app/routes.py` (new route after the label-suggestions route; add `from services.simplefin_service import SimpleFinError` to imports)
- Test: `tests/test_api_routes.py`

**Interfaces:**
- Consumes: `money.investments()` (Task 6).
- Produces: `GET /api/bank/investments` → 200 with the builder's dict; 503 `{"detail": "<closed-set status>"}` on `SimpleFinError`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api_routes.py`:

```python
def test_bank_investments_not_configured_and_error_paths(temp_db_path, monkeypatch):
    from services import simplefin_service
    from services.simplefin_service import SimpleFinError
    client = _client(temp_db_path)

    monkeypatch.setattr(simplefin_service, "is_configured", lambda: False)
    r = client.get("/api/bank/investments")
    assert r.status_code == 200 and r.json() == {"total": None, "accounts": []}

    monkeypatch.setattr(simplefin_service, "is_configured", lambda: True)
    def boom(days=None):
        raise SimpleFinError("error: unreachable")
    monkeypatch.setattr(simplefin_service, "fetch_accounts", boom)
    r = client.get("/api/bank/investments")
    assert r.status_code == 503 and r.json()["detail"] == "error: unreachable"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_api_routes.py::test_bank_investments_not_configured_and_error_paths -v`
Expected: FAIL with 404 (route doesn't exist).

- [ ] **Step 3: Implement** — in `app/routes.py`:

```python
@router.get("/bank/investments")
def get_bank_investments():
    """Live holdings — never persisted. The 503 detail is the closed-set
    status string and nothing else (redaction boundary: no exception text
    crosses; the service already logged the real detail server-side)."""
    try:
        return money.investments()
    except SimpleFinError as e:
        raise HTTPException(status_code=503, detail=e.status)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes.py tests/test_api_routes.py
git commit -m "feat(api): live bank investments endpoint"
```

---

### Task 8: `signedPct` helper in `lib.ts`

**Files:**
- Modify: `frontend/src/lib.ts` (after `signedMoney`)
- Test: `frontend/src/lib.test.ts`

**Interfaces:**
- Produces: `signedPct(pct: number): string` — `+25%`, `+3.6%`, `−5%` (U+2212, trailing `.0` trimmed). Callers null-check `gain_pct` before calling.

- [ ] **Step 1: Write the failing tests** — append to `frontend/src/lib.test.ts`:

```ts
describe("signedPct", () => {
  it("formats gains with + and losses with U+2212", () => {
    expect(signedPct(25)).toBe("+25%");
    expect(signedPct(3.6)).toBe("+3.6%");
    expect(signedPct(-5)).toBe("−5%");
    expect(signedPct(0)).toBe("+0%");
  });
});
```

(add `signedPct` to the existing import from `./lib`.)

- [ ] **Step 2: Run to verify failure**

Run in `frontend/`: `npm test -- --run`
Expected: FAIL — `signedPct` is not exported.

- [ ] **Step 3: Implement** — in `frontend/src/lib.ts` after `signedMoney`:

```ts
/** Signed percent for investment gains: `+3.6%`, `−5%` — U+2212 for losses
 * (matching signedMoney), one decimal with a trailing `.0` trimmed. Callers
 * null-check first: a missing cost basis shows nothing, never `+0%`. */
export function signedPct(pct: number): string {
  const s = Math.abs(pct).toFixed(1).replace(/\.0$/, "");
  return pct < 0 ? `−${s}%` : `+${s}%`;
}
```

- [ ] **Step 4: Run to verify pass**

Run in `frontend/`: `npm test -- --run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib.ts frontend/src/lib.test.ts
git commit -m "feat(frontend): signedPct helper"
```

---

### Task 9: Investments component + mount

**Files:**
- Create: `frontend/src/components/Investments.tsx`
- Modify: `frontend/src/screens/Money.tsx` (import; mount after `<LabelAudit />`), `frontend/src/styles.css`

**Interfaces:**
- Consumes: `GET /bank/investments` (Task 7), `money`/`signedMoney`/`signedPct` (Task 8).

- [ ] **Step 1: Create the component** — `frontend/src/components/Investments.tsx`:

```tsx
import { useState } from "react";
import { apiGet } from "../api";
import { money, signedMoney, signedPct } from "../lib";

interface Holding {
  symbol: string; description: string; shares: number | null;
  market_value: number; cost_basis: number | null;
  gain: number | null; gain_pct: number | null;
}
interface InvAccount {
  simplefin_id: string; name: string; market_value: number;
  cost_basis: number | null; gain: number | null; gain_pct: number | null;
  holdings: Holding[];
}
interface InvData {
  total: { market_value: number; cost_basis: number | null;
           gain: number | null; gain_pct: number | null } | null;
  accounts: InvAccount[];
}

function Gain({ gain, pct }: { gain: number | null; pct: number | null }) {
  if (gain === null) return null;
  return (
    <span className={gain < 0 ? "inv-loss" : "inv-gain"}>
      {signedMoney(gain)}{pct !== null && ` · ${signedPct(pct)}`}
    </span>
  );
}

// Live holdings vs. cost basis. Fetch-on-expand: a SimpleFIN round-trip takes
// seconds, so the Money screen never pays for it unless this section opens.
// Secondary surface: failure is a quiet inline line, never screen-level error.
// Nothing here is ever persisted — display and discard.
export default function Investments() {
  const [data, setData] = useState<InvData | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "error" | "ready">("idle");

  const load = () => {
    if (state === "loading" || state === "ready") return;
    setState("loading");
    apiGet<InvData>("/bank/investments")
      .then((d) => { setData(d); setState("ready"); })
      .catch(() => setState("error"));
  };

  return (
    <details
      className="money-details"
      onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) load(); }}
    >
      <summary>Investments</summary>
      {state === "loading" && <p className="quiet">Fetching from SimpleFIN…</p>}
      {state === "error" && <p className="quiet">Couldn't reach SimpleFIN right now.</p>}
      {state === "ready" && (!data?.total || data.accounts.length === 0) && (
        <p className="quiet">No holdings reported.</p>
      )}
      {state === "ready" && data?.total && data.accounts.length > 0 && (
        <>
          <div className="inv-total">
            <span>{money(data.total.market_value)}</span>
            <Gain gain={data.total.gain} pct={data.total.gain_pct} />
          </div>
          {data.accounts.map((a) => (
            <div className="inv-acct" key={a.simplefin_id}>
              <div className="inv-acct-head">
                <span>{a.name} · {money(a.market_value)}</span>
                <Gain gain={a.gain} pct={a.gain_pct} />
              </div>
              {a.holdings.map((h) => (
                <div className="inv-row" key={h.symbol + h.description}>
                  <span className="inv-name">
                    <strong>{h.symbol}</strong>
                    {h.description && <span className="quiet"> {h.description}</span>}
                  </span>
                  <span className="inv-nums num">
                    {money(h.market_value)} <Gain gain={h.gain} pct={h.gain_pct} />
                  </span>
                </div>
              ))}
            </div>
          ))}
        </>
      )}
    </details>
  );
}
```

- [ ] **Step 2: Mount it** — in `frontend/src/screens/Money.tsx`, add the import beside the other component imports:

```tsx
import Investments from "../components/Investments";
```

and render `<Investments />` on the line after `<LabelAudit />` (~line 410).

- [ ] **Step 3: Styles** — append to `frontend/src/styles.css`:

```css
/* Investments — gain/loss reuse validated theme-aware tokens (no new hues:
   --chart-social up, --danger down; both were contrast-checked per theme). */
.inv-total { display: flex; justify-content: space-between; gap: 8px; margin: 8px 0 4px; font-weight: 600; }
.inv-acct { margin-top: 10px; }
.inv-acct-head { display: flex; justify-content: space-between; gap: 8px; font-weight: 500; }
.inv-row { display: flex; justify-content: space-between; gap: 8px; padding: 2px 0 2px 10px; }
.inv-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inv-nums, .inv-gain, .inv-loss { white-space: nowrap; }
.inv-gain { color: var(--chart-social); }
.inv-loss { color: var(--danger); }
```

- [ ] **Step 4: Verify**

Run in `frontend/`: `npm test -- --run && npm run build`
Expected: clean. Then a manual look with the backend running: section collapsed by default; opening it shows the loading line, then total + per-account groups with colored gains; killing `SIMPLEFIN_ACCESS_URL` shows the quiet error line.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Investments.tsx frontend/src/screens/Money.tsx frontend/src/styles.css
git commit -m "feat(frontend): fetch-on-expand investments section"
```

---

### Task 10: Repo guide updates

**Files:**
- Modify: `CLAUDE.md` (bank_accounts table row; routes list in Repo Layout; frontend components list)

- [ ] **Step 1: Update `CLAUDE.md`:**
  - `bank_accounts` row: note `nickname` — user-set nullable TEXT, sync never touches it, resolved `COALESCE(NULLIF(nickname,''), name)` as `display_name` / `account_name` everywhere.
  - `app/routes.py` comment line: add `nickname` and `investments` to the bank route list.
  - `frontend/src/components` comment: add `Investments`.
  - Add one line under "Bank ingestion — hard-won facts" or Code Conventions: investments are live-only — holdings/market values are never persisted (extends the no-stored-balances rule).

- [ ] **Step 2: Run the full suites one last time**

Run: `pytest tests/ -v` and in `frontend/`: `npm test -- --run && npm run build`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: nickname column and live-only investments in the repo guide"
```
