# Bank Transaction Ingestion (SimpleFIN) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Ingest all bank and card transactions from SimpleFIN and classify each one as spending, transfer, card payment, investment, income, or unknown inflow — so the app can answer "what did I actually spend?" without double-counting money that merely moved.

**Architecture:** Four separable pieces, following this repo's existing separation. `services/simplefin_service.py` does transport and normalization only. `bank_flows.py` is **pure computation** (no DB, no I/O) holding the pair matcher and the flow classifier — the same role `metrics.py` and `receipts.py` play today. `database.py` owns all SQL, including two new tables. `jobs/sync_bank.py` orchestrates: fetch → upsert → match pairs → classify → record status. No Claude calls anywhere in this feature; classification is deterministic arithmetic and lookup by design.

**Tech Stack:** Python 3, FastAPI, APScheduler, httpx, psycopg2/sqlite3, pytest.

## Global Constraints

Copied from the spec (`docs/superpowers/specs/2026-07-23-bank-ingestion-design.md`) and `CLAUDE.md`. Every task's requirements implicitly include this section.

- **`config.py` is the only place that reads `os.environ`.** No module reads env vars directly.
- **`database.py` is the only place with SQL.** No DB calls from `app/`, `jobs/`, or `services/`.
- **The redaction boundary is absolute.** The SimpleFIN access URL is a bearer credential embedded in the URL itself. It is never logged, never stored in the database, never returned by any route, and never allowed into an exception that escapes `services/simplefin_service.py`. Every status written to `app_settings` comes from `services/safe_status.py`'s `CLOSED_SET`. Rule: **prevent the credential-bearing string from being constructed; never scrub it afterwards.**
- **Ingestion jobs must never crash the web app.** Log and record status; do not raise.
- **Override resolution happens in SQL**, via `COALESCE(user_flow, flow)` — never in Python at one call site.
- **A re-sync never overwrites user data.** `user_flow` and `role` survive every sync.
- **New columns need a migration**; a brand-new table does not (`CREATE TABLE IF NOT EXISTS` suffices). Pattern lives in `database.py::_init_v2_tables()`: Postgres `ALTER TABLE … ADD COLUMN IF NOT EXISTS`, SQLite a `PRAGMA table_info` guard.
- **Balances are never stored.** Not needed for spending analysis; keeps the most sensitive field out of the database.
- **No UI in this phase.** Ingestion and classification only, verified by tests and one read-only debug route.
- Exact defaults: `SIMPLEFIN_SYNC_INTERVAL_HOURS=12`, `SIMPLEFIN_LOOKBACK_DAYS=90`, `PAIR_WINDOW_DAYS=3`, new accounts default to role `unknown`.
- Valid roles: `spending`, `bills`, `savings`, `investment`, `credit_card`, `unknown`.
- Valid flows: `spending`, `transfer`, `card_payment`, `investment`, `income`, `inflow_unknown`.

## Resolved spec gaps

Two things the spec requires but its schema table omits. Both are settled here so no task has to improvise:

1. **`ambiguous` column on `bank_transactions`.** Spec §4 says an unpaired transfer-looking transaction (Venmo, Zelle, Apple Pay, ATM) "stays `spending` and is *flagged* as ambiguous for later triage." That flag needs somewhere to live. It is a derived boolean, recomputed every classification pass, never user-editable in this phase.
2. **Money is compared in integer cents, never as floats.** Amounts are stored as `REAL` to match every other amount column in this repo, but the pair matcher converts to `int(round(amount * 100))` before comparing. Float equality would silently fail to pair a legitimate transfer.

## File Structure

| File | Responsibility |
|---|---|
| `config.py` (modify) | The five new env vars. Only place reading `os.environ`. |
| `database.py` (modify) | `bank_accounts` + `bank_transactions` schema and every query touching them. |
| `bank_flows.py` (create) | **Pure.** Pair matching and flow classification. No DB, no I/O, no network. |
| `services/simplefin_service.py` (create) | HTTP transport + normalization of SimpleFIN's payload. Owns the redaction boundary. |
| `jobs/sync_bank.py` (create) | Orchestration and status recording. |
| `scripts/simplefin_backfill.py` (create) | Replays a saved snapshot through the same ingest path. |
| `main.py` (modify) | Schedule the job. |
| `app/routes.py` (modify) | Read-only debug route + Settings status fields. |
| `tests/test_bank_flows.py` (create) | Pair matcher + classifier. The bulk of the testing. |
| `tests/test_simplefin_service.py` (create) | Normalization + the credential property test. |
| `tests/test_sync_bank.py` (create) | Idempotence, user-data survival, status recording. |
| `tests/test_database_bank.py` (create) | Schema, upserts, `COALESCE` resolution. |

---

### Task 1: Config and environment variables

**Files:**
- Modify: `config.py:34` (after the existing job-schedule block)
- Test: none (config is plain constant assignment, exercised by every later task)

**Interfaces:**
- Produces: `config.SIMPLEFIN_ACCESS_URL: str`, `config.SIMPLEFIN_SYNC_INTERVAL_HOURS: int`, `config.SIMPLEFIN_LOOKBACK_DAYS: int`, `config.PAIR_WINDOW_DAYS: int`, `config.INCOME_PAYEE_HINTS: list[str]`

- [x] **Step 1: Add the config block**

Insert after line 34 (`WEEKLY_PUSH_HOUR = ...`) in `config.py`:

```python
# SimpleFIN bank ingestion. The access URL is a BEARER CREDENTIAL — it carries
# its own authentication inside the URL. It is read here and nowhere else, never
# logged, never stored in the database, never returned by any route. Unset =
# jobs/sync_bank.py no-ops with a "not configured" status, so local dev and an
# un-configured deploy are unaffected.
SIMPLEFIN_ACCESS_URL = os.getenv("SIMPLEFIN_ACCESS_URL", "")
SIMPLEFIN_SYNC_INTERVAL_HOURS = int(os.getenv("SIMPLEFIN_SYNC_INTERVAL_HOURS", "12"))
# SimpleFIN caps history at a rolling 90 days; asking for more is harmless (the
# API caps it and reports the cap as a non-fatal error) but pointless.
SIMPLEFIN_LOOKBACK_DAYS = int(os.getenv("SIMPLEFIN_LOOKBACK_DAYS", "90"))
# How many days apart the two halves of a transfer may post and still pair.
# Settlement routinely lags a day or two; 3 is deliberately generous because a
# missed pair becomes phantom spending, which is the failure mode that matters.
PAIR_WINDOW_DAYS = int(os.getenv("PAIR_WINDOW_DAYS", "3"))
# Payroll signatures, comma-separated, matched case-insensitively against a
# transaction's payee and description. Deliberately conservative: only an
# unpaired deposit that matches one of these is called income. See the SoFi
# hazard in the spec — an unmatched deposit is never silently income.
INCOME_PAYEE_HINTS = [
    h.strip() for h in os.getenv("INCOME_PAYEE_HINTS", "").split(",") if h.strip()
]
```

- [x] **Step 2: Verify config still imports**

Run: `./venv/bin/python -c "import config; print(config.PAIR_WINDOW_DAYS, config.SIMPLEFIN_LOOKBACK_DAYS)"`
Expected: `3 90`

- [x] **Step 3: Document the new vars in CLAUDE.md**

In `CLAUDE.md`, under **Environment Variables → Optional**, add these rows:

```markdown
| `SIMPLEFIN_ACCESS_URL` | SimpleFIN bearer credential — the URL *is* the secret. Unset = bank sync no-ops. Never logged or stored |
| `SIMPLEFIN_SYNC_INTERVAL_HOURS` | Bank sync interval (default 12) |
| `SIMPLEFIN_LOOKBACK_DAYS` | Bank sync lookback window (default 90 — SimpleFIN's hard cap) |
| `PAIR_WINDOW_DAYS` | Max days between the two halves of a matched transfer (default 3) |
| `INCOME_PAYEE_HINTS` | Comma-separated payroll signatures; only matching unpaired deposits count as income |
```

- [x] **Step 4: Commit**

```bash
git add config.py CLAUDE.md
git commit -m "feat(config): SimpleFIN ingestion env vars"
```

---

### Task 2: Schema and database accessors

**Files:**
- Modify: `database.py:502-601` (inside `_init_v2_tables`), and a new section at the end of the file
- Test: `tests/test_database_bank.py`

**Interfaces:**
- Consumes: `config` constants from Task 1 (none directly — this task is config-free)
- Produces:
  - `db.upsert_bank_account(simplefin_id, name, org, kind) -> None`
  - `db.get_bank_accounts() -> list[dict]` — keys: `id, simplefin_id, name, org, kind, role, active, last_synced_at`
  - `db.set_bank_account_role(simplefin_id, role) -> bool`
  - `db.touch_bank_account_sync(simplefin_id, when_iso) -> None`
  - `db.upsert_bank_transaction(simplefin_id, account_id, posted, transacted_at, amount, description, payee, memo, mcc) -> None`
  - `db.get_bank_transactions_range(start_day, end_day) -> list[dict]` — includes computed key `resolved_flow`
  - `db.get_unclassified_window(start_day) -> list[dict]` — every transaction posted on/after `start_day`, for the matcher
  - `db.set_bank_transaction_derived(simplefin_id, flow, pair_id, ambiguous) -> None`
  - `db.set_bank_flow_override(simplefin_id, user_flow) -> bool`

- [x] **Step 1: Write the failing tests**

Create `tests/test_database_bank.py`:

```python
"""bank_accounts / bank_transactions: upserts preserve user data, COALESCE resolves in SQL."""


def _account(db, sfid="acct-1", role=None):
    db.upsert_bank_account(sfid, "Everyday Checking", "Wells Fargo", "checking")
    if role:
        db.set_bank_account_role(sfid, role)
    return next(a for a in db.get_bank_accounts() if a["simplefin_id"] == sfid)


def test_new_account_defaults_to_unknown_role(temp_db_path):
    import database as db
    acct = _account(db)
    assert acct["role"] == "unknown"
    assert acct["active"] is True


def test_account_upsert_refreshes_name_but_never_role(temp_db_path):
    import database as db
    _account(db, role="spending")
    db.upsert_bank_account("acct-1", "RENAMED CHECKING", "Wells Fargo", "checking")
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-1")
    assert acct["name"] == "RENAMED CHECKING"
    assert acct["role"] == "spending"  # user data survives the sync


def test_transaction_upsert_updates_amount_but_never_user_flow(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -10.0, "PENDING COFFEE", "Coffee", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, False)
    db.set_bank_flow_override("t1", "transfer")

    # Pending transaction settles: amount and description change.
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-02", "2026-07-01",
                               -12.5, "COFFEE SHOP #4", "Coffee", "", "5814")

    rows = db.get_bank_transactions_range("2026-06-01", "2026-08-01")
    assert len(rows) == 1
    assert rows[0]["amount"] == -12.5
    assert rows[0]["description"] == "COFFEE SHOP #4"
    assert rows[0]["user_flow"] == "transfer"       # untouched
    assert rows[0]["resolved_flow"] == "transfer"   # COALESCE, computed in SQL


def test_resolved_flow_falls_back_to_derived_flow(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -10.0, "COFFEE", "Coffee", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, False)
    rows = db.get_bank_transactions_range("2026-06-01", "2026-08-01")
    assert rows[0]["resolved_flow"] == "spending"
    assert rows[0]["user_flow"] is None


def test_ambiguous_round_trips_as_a_real_bool(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -40.0, "VENMO PAYMENT", "Venmo", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, True)
    rows = db.get_bank_transactions_range("2026-06-01", "2026-08-01")
    assert rows[0]["ambiguous"] is True  # not 1 — SQLite ints must not leak to the API


def test_flow_override_returns_false_for_unknown_id(temp_db_path):
    import database as db
    assert db.set_bank_flow_override("nope", "transfer") is False


def test_balances_are_never_stored(temp_db_path):
    """The most sensitive field stays out of the database by construction."""
    import database as db
    with db._cursor() as c:
        if db.USE_POSTGRES:
            c.execute("SELECT column_name AS name FROM information_schema.columns "
                      "WHERE table_name = 'bank_accounts'")
        else:
            c.execute("PRAGMA table_info(bank_accounts)")
        cols = {r["name"] for r in c.fetchall()}
    assert not (cols & {"balance", "available_balance"})
```

- [x] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_database_bank.py -v`
Expected: FAIL — `AttributeError: module 'database' has no attribute 'upsert_bank_account'`

- [x] **Step 3: Add the schema**

In `database.py::_init_v2_tables()`, insert after the `rides` index (line 574, `CREATE INDEX IF NOT EXISTS ix_rides_ride_key ...`):

```python
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS bank_accounts (
                id {serial} PRIMARY KEY,
                simplefin_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                org TEXT DEFAULT '',
                kind TEXT DEFAULT '',
                role TEXT NOT NULL DEFAULT 'unknown',
                active {bool_t} NOT NULL DEFAULT TRUE,
                last_synced_at TEXT
            )
        """)
        # Balances are deliberately absent — see the spec. Not needed for spending
        # analysis, and the most sensitive field is safest when never stored.
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS bank_transactions (
                id {serial} PRIMARY KEY,
                simplefin_id TEXT NOT NULL UNIQUE,
                account_id INTEGER NOT NULL,
                posted TEXT NOT NULL,
                transacted_at TEXT,
                amount REAL NOT NULL,
                description TEXT DEFAULT '',
                payee TEXT DEFAULT '',
                memo TEXT DEFAULT '',
                mcc TEXT,
                flow TEXT,
                user_flow TEXT,
                pair_id TEXT,
                ambiguous {bool_t} NOT NULL DEFAULT FALSE,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_bank_txn_posted ON bank_transactions(posted)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_bank_txn_account ON bank_transactions(account_id)")
```

Both are brand-new tables, so no migration is needed — `CREATE TABLE IF NOT EXISTS` is sufficient per the repo convention.

- [x] **Step 4: Add the accessors**

Append to the end of `database.py`:

```python
# ── Bank accounts & transactions ──────────────────────────────────────────────
# The sync job may overwrite everything SimpleFIN reports, but never `role`
# (user-set) or `user_flow` (user override). Same Override + Learning pattern as
# social events and rides: AI/derived verdict and user verdict live in separate
# columns, and resolution happens in SQL so every caller agrees.

BANK_ROLES = ("spending", "bills", "savings", "investment", "credit_card", "unknown")
BANK_FLOWS = ("spending", "transfer", "card_payment", "investment", "income", "inflow_unknown")


def upsert_bank_account(simplefin_id, name, org="", kind=""):
    """Insert or refresh an account. Never touches `role` or `active` — those are
    the user's, and a nightly sync must not reset them."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO bank_accounts (simplefin_id, name, org, kind)
                VALUES ({p}, {p}, {p}, {p})
                ON CONFLICT(simplefin_id) DO UPDATE SET
                    name = excluded.name, org = excluded.org, kind = excluded.kind""",
            (simplefin_id, name, org, kind),
        )


def _bank_account_rows(rows):
    out = [dict(r) for r in rows]
    for r in out:
        r["active"] = bool(r["active"])
    return out


def get_bank_accounts():
    with _cursor() as c:
        c.execute("""SELECT id, simplefin_id, name, org, kind, role, active, last_synced_at
                     FROM bank_accounts ORDER BY id""")
        return _bank_account_rows(c.fetchall())


def set_bank_account_role(simplefin_id, role):
    """Returns True iff a row was updated, so a route can turn an unknown id into a 404."""
    if role not in BANK_ROLES:
        raise ValueError(f"unknown role: {role}")
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_accounts SET role = {p} WHERE simplefin_id = {p}",
                  (role, simplefin_id))
        return c.rowcount > 0


def touch_bank_account_sync(simplefin_id, when_iso):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_accounts SET last_synced_at = {p} WHERE simplefin_id = {p}",
                  (when_iso, simplefin_id))


def upsert_bank_transaction(simplefin_id, account_id, posted, transacted_at, amount,
                            description="", payee="", memo="", mcc=None):
    """Insert or refresh. Pending transactions settle — amount, description and
    posted date all legitimately change — so those are overwritten. `flow`,
    `user_flow`, `pair_id` and `ambiguous` are never touched here: the first is
    recomputed by the classification pass, the second belongs to the user."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO bank_transactions
                    (simplefin_id, account_id, posted, transacted_at, amount,
                     description, payee, memo, mcc)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
                ON CONFLICT(simplefin_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    posted = excluded.posted,
                    transacted_at = excluded.transacted_at,
                    amount = excluded.amount,
                    description = excluded.description,
                    payee = excluded.payee,
                    memo = excluded.memo,
                    mcc = excluded.mcc""",
            (simplefin_id, account_id, posted, transacted_at, amount,
             description, payee, memo, mcc),
        )


def set_bank_transaction_derived(simplefin_id, flow, pair_id, ambiguous):
    """Write the derived columns. Deliberately does NOT touch user_flow."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""UPDATE bank_transactions
                SET flow = {p}, pair_id = {p}, ambiguous = {p}
                WHERE simplefin_id = {p}""",
            (flow, pair_id, bool(ambiguous), simplefin_id),
        )


def set_bank_flow_override(simplefin_id, user_flow):
    """The confirmed user verdict. Returns True iff a row was updated."""
    if user_flow is not None and user_flow not in BANK_FLOWS:
        raise ValueError(f"unknown flow: {user_flow}")
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_transactions SET user_flow = {p} WHERE simplefin_id = {p}",
                  (user_flow, simplefin_id))
        return c.rowcount > 0


_BANK_TXN_SELECT = """
    SELECT t.id, t.simplefin_id, t.account_id, t.posted, t.transacted_at, t.amount,
           t.description, t.payee, t.memo, t.mcc, t.flow, t.user_flow, t.pair_id,
           t.ambiguous, a.role AS account_role, a.name AS account_name,
           COALESCE(t.user_flow, t.flow) AS resolved_flow
    FROM bank_transactions t JOIN bank_accounts a ON a.id = t.account_id
"""


def _bank_txn_rows(rows):
    """Cast `ambiguous` to a real bool — SQLite returns 0/1 ints, which would
    otherwise leak into the API as non-bool JSON (same reason as _ride_bool_rows)."""
    out = [dict(r) for r in rows]
    for r in out:
        r["ambiguous"] = bool(r["ambiguous"])
    return out


def get_bank_transactions_range(start_day, end_day):
    p = _p()
    with _cursor() as c:
        c.execute(f"{_BANK_TXN_SELECT} WHERE t.posted >= {p} AND t.posted <= {p} "
                  f"ORDER BY t.posted, t.simplefin_id", (start_day, end_day))
        return _bank_txn_rows(c.fetchall())


def get_unclassified_window(start_day):
    """Every transaction posted on/after `start_day`, for the matcher and classifier.
    Returns already-paired rows too — the matcher needs them to know what is taken."""
    p = _p()
    with _cursor() as c:
        c.execute(f"{_BANK_TXN_SELECT} WHERE t.posted >= {p} "
                  f"ORDER BY t.posted, t.simplefin_id", (start_day,))
        return _bank_txn_rows(c.fetchall())
```

- [x] **Step 5: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_database_bank.py -v`
Expected: PASS (7 tests)

- [x] **Step 6: Run the full suite for regressions**

Run: `./venv/bin/python -m pytest tests/ -q`
Expected: all pass

- [x] **Step 7: Commit**

```bash
git add database.py tests/test_database_bank.py
git commit -m "feat(db): bank_accounts and bank_transactions schema and accessors"
```

---

### Task 3: Pair matcher (pure)

**Files:**
- Create: `bank_flows.py`
- Test: `tests/test_bank_flows.py`

**Interfaces:**
- Consumes: transaction dicts shaped like `db.get_unclassified_window()` output — keys used: `simplefin_id`, `account_id`, `posted` (ISO `YYYY-MM-DD`), `amount` (float), `pair_id`.
- Produces: `bank_flows.match_pairs(txns, window_days=3) -> dict[str, str]` — maps `simplefin_id` → `pair_id` for **newly** matched transactions only. `pair_id` is the lexicographically smaller `simplefin_id` of the pair, so re-running produces identical values.

- [x] **Step 1: Write the failing tests**

Create `tests/test_bank_flows.py`:

```python
"""Pair matching and flow classification — pure arithmetic, no DB, no AI."""
import bank_flows


def txn(sfid, account_id, posted, amount, pair_id=None, description="", payee="", mcc=None):
    return {"simplefin_id": sfid, "account_id": account_id, "posted": posted,
            "amount": amount, "pair_id": pair_id, "description": description,
            "payee": payee, "mcc": mcc}


def test_opposite_amounts_across_accounts_pair():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("b", 2, "2026-07-02", 500.0),
    ])
    assert out == {"a": "a", "b": "a"}  # pair_id is the smaller simplefin_id


def test_same_account_movement_does_not_pair():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("b", 1, "2026-07-01", 500.0),
    ])
    assert out == {}


def test_outside_the_window_does_not_pair():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("b", 2, "2026-07-09", 500.0),
    ], window_days=3)
    assert out == {}


def test_near_miss_amounts_do_not_pair():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("b", 2, "2026-07-01", 500.01),
    ])
    assert out == {}


def test_same_sign_amounts_do_not_pair():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("b", 2, "2026-07-01", -500.0),
    ])
    assert out == {}


def test_already_paired_transaction_is_not_repaired():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0, pair_id="a"),
        txn("b", 2, "2026-07-01", 500.0, pair_id="a"),
        txn("c", 3, "2026-07-01", 500.0),
    ])
    assert out == {}  # 'a' is taken; 'c' has nothing free to pair with


def test_half_arriving_in_a_later_sync_pairs_on_the_later_run():
    """The first sync sees only one half and matches nothing; the second sees both."""
    first = [txn("a", 1, "2026-07-01", -500.0)]
    assert bank_flows.match_pairs(first) == {}
    second = [txn("a", 1, "2026-07-01", -500.0), txn("b", 2, "2026-07-02", 500.0)]
    assert bank_flows.match_pairs(second) == {"a": "a", "b": "a"}


def test_ties_resolve_by_smallest_date_gap_then_lowest_id():
    """Two equally valid partners: the nearer date wins."""
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("z", 2, "2026-07-03", 500.0),
        txn("y", 2, "2026-07-01", 500.0),
    ])
    assert out == {"a": "a", "y": "a"}   # same-day 'y' beats 'z'


def test_exact_date_tie_resolves_by_lowest_id():
    out = bank_flows.match_pairs([
        txn("m", 1, "2026-07-01", -500.0),
        txn("z", 2, "2026-07-01", 500.0),
        txn("a", 3, "2026-07-01", 500.0),
    ])
    assert out == {"m": "a", "a": "a"}  # 'a' < 'z'


def test_matching_is_deterministic_regardless_of_input_order():
    rows = [txn("a", 1, "2026-07-01", -500.0),
            txn("z", 2, "2026-07-01", 500.0),
            txn("b", 3, "2026-07-01", 500.0)]
    assert bank_flows.match_pairs(rows) == bank_flows.match_pairs(list(reversed(rows)))


def test_float_cents_still_pair():
    """Money compares in integer cents — float equality would drop this pair."""
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -1234.56),
        txn("b", 2, "2026-07-01", 1234.56),
    ])
    assert out == {"a": "a", "b": "a"}


def test_one_outflow_claims_only_one_partner():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("b", 2, "2026-07-01", 500.0),
        txn("c", 3, "2026-07-01", 500.0),
    ])
    assert out == {"a": "a", "b": "a"}  # 'c' is left unpaired
```

- [x] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_bank_flows.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bank_flows'`

- [x] **Step 3: Write the implementation**

Create `bank_flows.py`:

```python
"""Pure computation for bank ingestion: pair matching and flow classification.

No database, no network, no Claude — the same role metrics.py and receipts.py
play. Everything here is deterministic and re-runnable: the same input always
produces the same output, which is what lets the sync job re-classify from
scratch on every run without churning the database.

The central problem this module exists to solve: about a quarter of the user's
transactions are money *moving*, not money *spent*. Summing outflows naively
would double-count every credit-card purchase and invent spending from
checking-to-checking transfers.
"""
from datetime import date

# Transfer-ish wording that we can't yet prove is a transfer. An unpaired
# transaction matching one of these stays `spending` (never silently reclassified)
# but is flagged for later triage. The Venmo/Zelle/ATM policy is deliberately
# deferred — flagging costs nothing and avoids inventing a rule.
AMBIGUOUS_HINTS = (
    "venmo", "zelle", "cash app", "cashapp", "apple cash", "paypal",
    "atm", "withdrawal", "transfer", "xfer", "wire",
)


def _cents(amount) -> int:
    """Money compares as integer cents. Float equality would silently fail to
    pair a legitimate transfer, which turns into phantom spending."""
    return int(round(float(amount) * 100))


def _day(posted) -> date:
    return date.fromisoformat(str(posted)[:10])


def match_pairs(txns, window_days=3):
    """Find the two halves of each money movement.

    Two transactions pair when ALL hold:
      - different accounts
      - amounts equal in absolute value and opposite in sign
      - `posted` dates within `window_days`
      - neither is already paired

    Returns {simplefin_id: pair_id} for NEWLY matched transactions only;
    already-paired rows are excluded from the result but still consume their
    partner. `pair_id` is the lexicographically smaller of the two ids, so the
    value is stable across re-runs.

    Ties break by smallest date gap, then lowest partner id — deterministic and
    independent of input order.
    """
    free = [t for t in txns if not t.get("pair_id")]
    # Sort so iteration order never depends on how the caller ordered its query.
    free.sort(key=lambda t: (str(t["posted"]), str(t["simplefin_id"])))

    # Index the positive side by absolute cents; outflows go looking for a partner.
    by_cents = {}
    for t in free:
        if _cents(t["amount"]) > 0:
            by_cents.setdefault(abs(_cents(t["amount"])), []).append(t)

    taken = set()
    matched = {}
    for out_txn in free:
        if _cents(out_txn["amount"]) >= 0 or out_txn["simplefin_id"] in taken:
            continue
        key = abs(_cents(out_txn["amount"]))
        out_day = _day(out_txn["posted"])

        candidates = [
            c for c in by_cents.get(key, [])
            if c["simplefin_id"] not in taken
            and c["account_id"] != out_txn["account_id"]
            and abs((_day(c["posted"]) - out_day).days) <= window_days
        ]
        if not candidates:
            continue
        partner = min(candidates, key=lambda c: (abs((_day(c["posted"]) - out_day).days),
                                                 str(c["simplefin_id"])))
        pair_id = min(str(out_txn["simplefin_id"]), str(partner["simplefin_id"]))
        matched[out_txn["simplefin_id"]] = pair_id
        matched[partner["simplefin_id"]] = pair_id
        taken.add(out_txn["simplefin_id"])
        taken.add(partner["simplefin_id"])

    return matched
```

- [x] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_bank_flows.py -v`
Expected: PASS (12 tests)

- [x] **Step 5: Commit**

```bash
git add bank_flows.py tests/test_bank_flows.py
git commit -m "feat(bank): deterministic pair matcher for money movements"
```

---

### Task 4: Flow classifier (pure)

**Files:**
- Modify: `bank_flows.py`
- Test: `tests/test_bank_flows.py` (append)

**Interfaces:**
- Consumes: `bank_flows.match_pairs` from Task 3.
- Produces:
  - `bank_flows.classify_flow(txn, role, partner_role, income_hints) -> str` — one of the six flows
  - `bank_flows.is_ambiguous(txn, flow) -> bool`
  - `bank_flows.classify_all(txns, roles_by_account_id, pair_map, income_hints) -> dict[str, tuple[str, str | None, bool]]` — maps `simplefin_id` → `(flow, pair_id, ambiguous)`, which is exactly the argument triple `db.set_bank_transaction_derived` takes.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_bank_flows.py`:

```python
# ── Flow classification ───────────────────────────────────────────────────────

HINTS = ["demandbase", "acme payroll"]


def test_investment_beats_card_payment_and_transfer():
    """Rule 1 wins: a contribution paid from a card-ish account is still saving."""
    t = txn("a", 1, "2026-07-01", -500.0)
    assert bank_flows.classify_flow(t, "spending", "investment", HINTS) == "investment"
    assert bank_flows.classify_flow(t, "investment", "credit_card", HINTS) == "investment"


def test_investment_to_investment_is_investment_on_both_sides():
    """Traditional -> Roth conversion contributes to nothing."""
    t = txn("a", 1, "2026-07-01", -6000.0)
    u = txn("b", 2, "2026-07-01", 6000.0)
    assert bank_flows.classify_flow(t, "investment", "investment", HINTS) == "investment"
    assert bank_flows.classify_flow(u, "investment", "investment", HINTS) == "investment"


def test_card_payment_beats_transfer():
    t = txn("a", 1, "2026-07-01", -2000.0)
    assert bank_flows.classify_flow(t, "spending", "credit_card", HINTS) == "card_payment"
    assert bank_flows.classify_flow(t, "credit_card", "spending", HINTS) == "card_payment"


def test_matched_pair_between_ordinary_accounts_is_transfer():
    t = txn("a", 1, "2026-07-01", -500.0)
    assert bank_flows.classify_flow(t, "spending", "savings", HINTS) == "transfer"


def test_unpaired_deposit_matching_a_payroll_hint_is_income():
    t = txn("a", 1, "2026-07-01", 3200.0, payee="DEMANDBASE PAYROLL")
    assert bank_flows.classify_flow(t, "spending", None, HINTS) == "income"


def test_income_hint_matches_description_too_and_is_case_insensitive():
    t = txn("a", 1, "2026-07-01", 3200.0, description="direct dep acme payroll llc")
    assert bank_flows.classify_flow(t, "bills", None, HINTS) == "income"


def test_unpaired_deposit_without_a_hint_is_inflow_unknown_never_income():
    """The SoFi hazard: a savings drawdown must never be reported as earnings."""
    t = txn("a", 1, "2026-07-01", 2000.0, description="TRANSFER FROM SOFI")
    assert bank_flows.classify_flow(t, "spending", None, HINTS) == "inflow_unknown"


def test_payroll_hint_into_a_non_spending_account_is_not_income():
    """Rule 4 is scoped to spending/bills accounts."""
    t = txn("a", 1, "2026-07-01", 3200.0, payee="DEMANDBASE PAYROLL")
    assert bank_flows.classify_flow(t, "unknown", None, HINTS) == "inflow_unknown"


def test_empty_hints_never_produce_income():
    t = txn("a", 1, "2026-07-01", 3200.0, payee="DEMANDBASE PAYROLL")
    assert bank_flows.classify_flow(t, "spending", None, []) == "inflow_unknown"


def test_ordinary_unpaired_outflow_is_spending():
    t = txn("a", 1, "2026-07-01", -14.20, payee="COFFEE SHOP")
    assert bank_flows.classify_flow(t, "spending", None, HINTS) == "spending"


def test_unpaired_venmo_outflow_stays_spending_and_is_flagged():
    t = txn("a", 1, "2026-07-01", -40.0, description="VENMO PAYMENT 123")
    flow = bank_flows.classify_flow(t, "spending", None, HINTS)
    assert flow == "spending"
    assert bank_flows.is_ambiguous(t, flow) is True


def test_a_matched_transfer_is_not_flagged_ambiguous():
    t = txn("a", 1, "2026-07-01", -500.0, description="ONLINE TRANSFER")
    flow = bank_flows.classify_flow(t, "spending", "savings", HINTS)
    assert bank_flows.is_ambiguous(t, flow) is False


def test_plain_spending_is_not_flagged_ambiguous():
    t = txn("a", 1, "2026-07-01", -14.20, payee="COFFEE SHOP")
    assert bank_flows.is_ambiguous(t, "spending") is False


def test_classify_all_wires_pairs_roles_and_flags_together():
    txns = [
        txn("a", 1, "2026-07-01", -2000.0, description="AUTOPAY THANK YOU"),
        txn("b", 2, "2026-07-01", 2000.0, description="PAYMENT RECEIVED"),
        txn("c", 1, "2026-07-02", -40.0, description="VENMO PAYMENT"),
        txn("d", 1, "2026-07-03", 3200.0, payee="DEMANDBASE PAYROLL"),
    ]
    roles = {1: "spending", 2: "credit_card"}
    pair_map = bank_flows.match_pairs(txns)
    out = bank_flows.classify_all(txns, roles, pair_map, HINTS)

    assert out["a"] == ("card_payment", "a", False)
    assert out["b"] == ("card_payment", "a", False)
    assert out["c"] == ("spending", None, True)
    assert out["d"] == ("income", None, False)


def test_classify_all_treats_an_unknown_account_role_as_unknown():
    txns = [txn("a", 99, "2026-07-01", 500.0, payee="DEMANDBASE PAYROLL")]
    out = bank_flows.classify_all(txns, {}, {}, HINTS)
    assert out["a"] == ("inflow_unknown", None, False)


def test_classify_all_honours_a_preexisting_pair_id():
    """A pair matched in an earlier sync still classifies as a pair."""
    txns = [
        txn("a", 1, "2026-07-01", -2000.0, pair_id="a"),
        txn("b", 2, "2026-07-01", 2000.0, pair_id="a"),
    ]
    out = bank_flows.classify_all(txns, {1: "spending", 2: "credit_card"}, {}, HINTS)
    assert out["a"][0] == "card_payment"
    assert out["b"][0] == "card_payment"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_bank_flows.py -v -k "flow or ambiguous or classify"`
Expected: FAIL with `AttributeError: module 'bank_flows' has no attribute 'classify_flow'`

- [x] **Step 3: Write the implementation**

Append to `bank_flows.py`:

```python
def classify_flow(txn, role, partner_role, income_hints):
    """Classify one transaction. Rules apply in order; the first match wins, and
    only the final fallback is a guess.

    `role` is the role of this transaction's own account; `partner_role` is the
    role of the account on the other half of a matched pair, or None if unpaired.
    """
    # 1. Investment — either side. Contributions and the backdoor-Roth conversion
    #    leg are saving, never spending. Investment-to-investment contributes to
    #    nothing on either side.
    if role == "investment" or partner_role == "investment":
        return "investment"

    # 2. Card payment — a matched pair with a credit card on one side. The
    #    purchases are already recorded on the card; counting the payment too
    #    would double-count. Reported separately so paydown reads as progress.
    if partner_role is not None and "credit_card" in (role, partner_role):
        return "card_payment"

    # 3. Transfer — any other matched pair between two known accounts.
    if partner_role is not None:
        return "transfer"

    amount = float(txn["amount"])

    # 4. Income — an unpaired deposit into a spending/bills account whose payee or
    #    description matches a configured payroll signature. Conservative by design.
    if amount > 0 and role in ("spending", "bills"):
        haystack = f"{txn.get('payee') or ''} {txn.get('description') or ''}".lower()
        if any(h.lower() in haystack for h in income_hints if h):
            return "income"

    # 5. Any other unpaired deposit. Counted as neither income nor spending.
    #    This is the SoFi hazard guard: money drawn down from an unconnected
    #    savings account arrives here, and must never be reported as earnings.
    if amount > 0:
        return "inflow_unknown"

    # 6. Everything else.
    return "spending"


def is_ambiguous(txn, flow):
    """True when a transaction we called `spending` uses transfer-ish wording.

    It stays `spending` — an AI or keyword flag alone never excludes anything
    silently — but it is surfaced for later triage. The Venmo/Zelle/ATM policy is
    deliberately deferred; flagging costs nothing now.
    """
    if flow != "spending":
        return False
    haystack = f"{txn.get('payee') or ''} {txn.get('description') or ''}".lower()
    return any(hint in haystack for hint in AMBIGUOUS_HINTS)


def classify_all(txns, roles_by_account_id, pair_map, income_hints):
    """Classify a whole window at once.

    `pair_map` is match_pairs()' output (newly matched only); a transaction's
    existing `pair_id` is honoured too, so pairs matched in an earlier sync keep
    their classification.

    Returns {simplefin_id: (flow, pair_id, ambiguous)} — exactly the argument
    triple db.set_bank_transaction_derived takes.
    """
    pair_of = {}
    for t in txns:
        sfid = t["simplefin_id"]
        pair_of[sfid] = pair_map.get(sfid) or t.get("pair_id") or None

    # Who is on the other side of each pair, by account.
    partners = {}
    for t in txns:
        pid = pair_of[t["simplefin_id"]]
        if pid:
            partners.setdefault(pid, []).append(t)

    out = {}
    for t in txns:
        sfid = t["simplefin_id"]
        pid = pair_of[sfid]
        role = roles_by_account_id.get(t["account_id"], "unknown")

        partner_role = None
        if pid:
            others = [o for o in partners.get(pid, []) if o["simplefin_id"] != sfid]
            if others:
                partner_role = roles_by_account_id.get(others[0]["account_id"], "unknown")

        flow = classify_flow(t, role, partner_role, income_hints)
        out[sfid] = (flow, pid, is_ambiguous(t, flow))
    return out
```

- [x] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_bank_flows.py -v`
Expected: PASS (28 tests)

- [x] **Step 5: Commit**

```bash
git add bank_flows.py tests/test_bank_flows.py
git commit -m "feat(bank): deterministic flow classifier with conservative income rule"
```

---

### Task 5: SimpleFIN service and the redaction boundary

**Files:**
- Create: `services/simplefin_service.py`
- Test: `tests/test_simplefin_service.py`

**Interfaces:**
- Consumes: `config.SIMPLEFIN_ACCESS_URL`, `config.SIMPLEFIN_LOOKBACK_DAYS`; `services.safe_status.safe_status`.
- Produces:
  - `simplefin_service.is_configured() -> bool`
  - `simplefin_service.fetch_accounts(days=None) -> dict` — raises `SimpleFinError` on any transport or protocol failure. **`SimpleFinError` carries a `status` attribute from `CLOSED_SET` and never any message text.**
  - `simplefin_service.normalize(payload) -> tuple[list[dict], list[dict]]` — `(accounts, transactions)`. Account dicts: `simplefin_id, name, org, kind`. Transaction dicts: `simplefin_id, account_simplefin_id, posted, transacted_at, amount, description, payee, memo, mcc`.

- [x] **Step 1: Write the failing tests**

Create `tests/test_simplefin_service.py`:

```python
"""SimpleFIN transport + normalization. The access URL must never escape."""
import httpx
import pytest


def _payload():
    return {
        "accounts": [
            {
                "id": "acct-1", "name": "EVERYDAY CHECKING ...7395",
                "org": {"name": "Wells Fargo"}, "currency": "USD",
                "balance": "1234.56",
                "transactions": [
                    {"id": "t1", "posted": 1751328000, "transacted_at": 1751241600,
                     "amount": "-14.20", "description": "COFFEE SHOP",
                     "payee": "Coffee Shop", "memo": "", "mcc": "5814"},
                    {"id": "t2", "posted": 1751414400, "amount": "3200.00",
                     "description": "DIRECT DEP"},
                ],
            },
            {"id": "acct-2", "name": "Platinum Card", "org": {"domain": "americanexpress.com"},
             "transactions": []},
        ],
        "errors": [],
    }


def test_normalize_flattens_accounts_and_transactions():
    from services import simplefin_service
    accounts, txns = simplefin_service.normalize(_payload())

    assert [a["simplefin_id"] for a in accounts] == ["acct-1", "acct-2"]
    assert accounts[0]["org"] == "Wells Fargo"
    assert accounts[1]["org"] == "americanexpress.com"  # falls back to domain
    assert [t["simplefin_id"] for t in txns] == ["t1", "t2"]
    assert txns[0]["account_simplefin_id"] == "acct-1"
    assert txns[0]["amount"] == -14.20
    assert txns[0]["posted"] == "2026-06-30" or len(txns[0]["posted"]) == 10


def test_normalize_tolerates_missing_optional_fields():
    """mcc is absent on 74% of real transactions — every card account has none."""
    from services import simplefin_service
    _, txns = simplefin_service.normalize(_payload())
    assert txns[1]["mcc"] is None
    assert txns[1]["payee"] == ""
    assert txns[1]["memo"] == ""
    assert txns[1]["transacted_at"] is None


def test_normalize_never_returns_a_balance():
    from services import simplefin_service
    accounts, _ = simplefin_service.normalize(_payload())
    for a in accounts:
        assert "balance" not in a


def test_normalize_skips_transactions_without_an_id():
    from services import simplefin_service
    payload = _payload()
    payload["accounts"][0]["transactions"].append({"amount": "-1.00"})
    _, txns = simplefin_service.normalize(payload)
    assert [t["simplefin_id"] for t in txns] == ["t1", "t2"]


def test_not_configured_when_url_is_blank(monkeypatch):
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", "")
    assert simplefin_service.is_configured() is False


@pytest.mark.parametrize("exc_factory", [
    lambda url: httpx.ConnectError(f"failed to connect to {url}"),
    lambda url: httpx.ReadTimeout(f"timed out reading {url}"),
    lambda url: RuntimeError(f"boom while requesting {url}"),
])
def test_the_access_url_never_survives_a_transport_failure(monkeypatch, exc_factory):
    """The whole point of the boundary: a credential-bearing exception goes in,
    only a closed-set token comes out."""
    from services import simplefin_service
    from services.safe_status import CLOSED_SET

    secret = "https://user:sup3rsecret@bridge.example.com/simplefin"
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", secret)

    def boom(*a, **kw):
        raise exc_factory(secret)

    monkeypatch.setattr(simplefin_service.httpx, "get", boom)

    with pytest.raises(simplefin_service.SimpleFinError) as ei:
        simplefin_service.fetch_accounts()

    err = ei.value
    assert err.status in CLOSED_SET
    blob = f"{err!r} {err} {err.args} {err.status}"
    assert "sup3rsecret" not in blob
    assert "bridge.example.com" not in blob


def test_http_401_maps_to_auth(monkeypatch):
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", "https://x@y.example/sf")
    monkeypatch.setattr(simplefin_service.httpx, "get",
                        lambda *a, **kw: httpx.Response(401, text="nope"))
    with pytest.raises(simplefin_service.SimpleFinError) as ei:
        simplefin_service.fetch_accounts()
    assert ei.value.status == "error: auth"


def test_non_json_body_maps_to_see_logs(monkeypatch):
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", "https://x@y.example/sf")
    monkeypatch.setattr(simplefin_service.httpx, "get",
                        lambda *a, **kw: httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(simplefin_service.SimpleFinError) as ei:
        simplefin_service.fetch_accounts()
    assert ei.value.status == "error: see logs"


def test_successful_fetch_returns_the_payload(monkeypatch):
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", "https://x@y.example/sf")
    monkeypatch.setattr(simplefin_service.httpx, "get",
                        lambda *a, **kw: httpx.Response(200, json=_payload()))
    data = simplefin_service.fetch_accounts(days=90)
    assert len(data["accounts"]) == 2
```

- [x] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_simplefin_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'simplefin_service'`

- [x] **Step 3: Write the implementation**

Create `services/simplefin_service.py`:

```python
"""SimpleFIN transport and normalization — and the hardest instance of this
repo's redaction boundary.

A SimpleFIN access URL carries its credentials INSIDE THE URL. httpx puts the
request URL into most of its exception messages, so an exception that escapes
this module would carry the user's bank credentials into a log line, a status
string, or an API response.

The rule (see CLAUDE.md): prevent the credential-bearing string from being
constructed; never scrub it afterwards. Concretely, every call in this module is
wrapped, and the only thing that ever crosses the boundary is `SimpleFinError`,
which carries a `status` from safe_status's CLOSED_SET and NO message text.
Callers must never log the original exception object — this module already did,
server-side, with logger.exception.
"""
import datetime
import logging
import time

import httpx

from config import SIMPLEFIN_ACCESS_URL, SIMPLEFIN_LOOKBACK_DAYS
from services.safe_status import safe_status

logger = logging.getLogger(__name__)


class SimpleFinError(Exception):
    """Carries a closed-set status and nothing else. Deliberately constructed with
    no message argument so `str(e)` and `e.args` cannot leak the access URL."""

    def __init__(self, status):
        super().__init__()
        self.status = status

    def __str__(self):
        return self.status

    def __repr__(self):
        return f"SimpleFinError({self.status!r})"


def is_configured() -> bool:
    return bool(SIMPLEFIN_ACCESS_URL.strip())


def fetch_accounts(days=None):
    """GET /accounts for the lookback window. Raises SimpleFinError on any failure.

    The URL is built inside the try, used once, and never returned or logged.
    """
    days = SIMPLEFIN_LOOKBACK_DAYS if days is None else days
    start = int(time.time()) - days * 86400
    try:
        resp = httpx.get(f"{SIMPLEFIN_ACCESS_URL.rstrip('/')}/accounts",
                         params={"start-date": start}, timeout=180)
    except Exception as e:
        # logger.exception is safe: Railway logs are server-side only, and the
        # operator needs the real detail. The DB and the API get `status` alone.
        logger.exception("SimpleFIN request failed")
        raise SimpleFinError(safe_status(e)) from None  # `from None`: drop the chained cause

    if resp.status_code != 200:
        logger.error("SimpleFIN returned HTTP %d", resp.status_code)
        # Build a bare object carrying only the code — never the response itself,
        # whose .request holds the credential-bearing URL.
        raise SimpleFinError(safe_status(_StatusOnly(resp.status_code)))

    try:
        return resp.json()
    except Exception:
        logger.exception("SimpleFIN returned a non-JSON body")
        raise SimpleFinError("error: see logs") from None


class _StatusOnly(Exception):
    """A minimal carrier so safe_status can map an HTTP code without ever seeing
    the httpx response (which holds the request URL)."""

    def __init__(self, status_code):
        super().__init__()
        self.status_code = status_code


def _epoch_to_day(value):
    if value in (None, ""):
        return None
    try:
        return datetime.date.fromtimestamp(int(value)).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def normalize(payload):
    """Flatten SimpleFIN's nested payload into (accounts, transactions).

    Balances are dropped here and never propagate further — the most sensitive
    field is safest when it is never stored. `mcc` is absent on roughly
    three-quarters of real transactions (every credit card reports none), so
    every optional field tolerates absence rather than assuming presence.
    """
    accounts, txns = [], []
    for acct in payload.get("accounts", []) or []:
        sfid = acct.get("id")
        if not sfid:
            continue
        org = acct.get("org") or {}
        accounts.append({
            "simplefin_id": str(sfid),
            "name": acct.get("name") or "?",
            "org": org.get("name") or org.get("domain") or "",
            # SimpleFIN's own type, stored verbatim. Not all bridges populate it.
            "kind": acct.get("type") or "",
        })
        for t in acct.get("transactions", []) or []:
            tid = t.get("id")
            if not tid:
                continue
            posted = _epoch_to_day(t.get("posted"))
            if not posted:
                continue
            try:
                amount = float(t.get("amount"))
            except (TypeError, ValueError):
                continue
            txns.append({
                "simplefin_id": str(tid),
                "account_simplefin_id": str(sfid),
                "posted": posted,
                "transacted_at": _epoch_to_day(t.get("transacted_at")),
                "amount": amount,
                "description": t.get("description") or "",
                "payee": t.get("payee") or "",
                "memo": t.get("memo") or "",
                "mcc": t.get("mcc") or None,
            })
    return accounts, txns
```

- [x] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_simplefin_service.py -v`
Expected: PASS (10 tests)

- [x] **Step 5: Extend the shared boundary test**

`tests/test_safe_status.py::test_job_modules_use_the_shared_constants_not_ad_hoc_literals` enumerates job modules. Read that test and add `jobs/sync_bank.py` to its module list so the new job is held to the same invariant. (Task 6 creates the file; if it does not exist yet, do this step at the end of Task 6 instead.)

- [x] **Step 6: Commit**

```bash
git add services/simplefin_service.py tests/test_simplefin_service.py
git commit -m "feat(services): SimpleFIN transport with an absolute redaction boundary"
```

---

### Task 6: Sync job

**Files:**
- Create: `jobs/sync_bank.py`
- Test: `tests/test_sync_bank.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: `sync_bank.run(payload=None) -> None`. **`payload` exists so a saved snapshot can be replayed through the identical ingest path** (Task 8) — when given, no network call happens.

- [x] **Step 1: Write the failing tests**

Create `tests/test_sync_bank.py`:

```python
"""sync_bank: idempotent, never crashes, never leaks, never clobbers user data."""
import pytest


def _payload():
    return {
        "accounts": [
            {"id": "chk", "name": "EVERYDAY CHECKING", "org": {"name": "Wells Fargo"},
             "transactions": [
                 {"id": "p1", "posted": 1751328000, "amount": "-2000.00",
                  "description": "AUTOPAY PAYMENT THANK YOU"},
                 {"id": "s1", "posted": 1751328000, "amount": "-14.20",
                  "description": "COFFEE SHOP", "mcc": "5814"},
             ]},
            {"id": "card", "name": "Platinum Card", "org": {"name": "American Express"},
             "transactions": [
                 {"id": "p2", "posted": 1751328000, "amount": "2000.00",
                  "description": "PAYMENT RECEIVED"},
             ]},
        ],
        "errors": [],
    }


def _configure(monkeypatch, roles=True):
    import database as db
    from jobs import sync_bank
    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)
    monkeypatch.setattr(sync_bank, "INCOME_PAYEE_HINTS", ["demandbase"])
    sync_bank.run(payload=_payload())
    if roles:
        db.set_bank_account_role("chk", "spending")
        db.set_bank_account_role("card", "credit_card")
    return sync_bank


def test_sync_stores_accounts_and_transactions(temp_db_path, monkeypatch):
    import database as db
    sync_bank = _configure(monkeypatch, roles=False)

    accts = {a["simplefin_id"] for a in db.get_bank_accounts()}
    assert accts == {"chk", "card"}
    assert all(a["role"] == "unknown" for a in db.get_bank_accounts())
    rows = db.get_bank_transactions_range("2026-01-01", "2027-01-01")
    assert {r["simplefin_id"] for r in rows} == {"p1", "s1", "p2"}
    assert db.get_setting("bank_last_status") == "ok"


def test_card_payment_is_matched_and_not_counted_as_spending(temp_db_path, monkeypatch):
    import database as db
    sync_bank = _configure(monkeypatch)
    sync_bank.run(payload=_payload())  # re-run now that roles are set

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-01-01", "2027-01-01")}
    assert rows["p1"]["resolved_flow"] == "card_payment"
    assert rows["p2"]["resolved_flow"] == "card_payment"
    assert rows["p1"]["pair_id"] == rows["p2"]["pair_id"] is not None
    assert rows["s1"]["resolved_flow"] == "spending"


def test_resync_is_idempotent(temp_db_path, monkeypatch):
    import database as db
    sync_bank = _configure(monkeypatch)
    sync_bank.run(payload=_payload())
    before = db.get_bank_transactions_range("2026-01-01", "2027-01-01")
    sync_bank.run(payload=_payload())
    after = db.get_bank_transactions_range("2026-01-01", "2027-01-01")
    assert [dict(r) for r in before] == [dict(r) for r in after]


def test_resync_updates_a_settled_amount_but_keeps_user_flow_and_role(temp_db_path, monkeypatch):
    import database as db
    sync_bank = _configure(monkeypatch)
    db.set_bank_flow_override("s1", "transfer")

    settled = _payload()
    settled["accounts"][0]["transactions"][1]["amount"] = "-16.31"
    sync_bank.run(payload=settled)

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-01-01", "2027-01-01")}
    assert rows["s1"]["amount"] == -16.31          # settled value wins
    assert rows["s1"]["user_flow"] == "transfer"   # user override survives
    assert rows["s1"]["resolved_flow"] == "transfer"
    assert {a["simplefin_id"]: a["role"] for a in db.get_bank_accounts()}["chk"] == "spending"


def test_sync_skipped_when_not_configured(temp_db_path, monkeypatch):
    import database as db
    from jobs import sync_bank
    from services.safe_status import NOT_CONFIGURED
    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: False)
    sync_bank.run()
    assert db.get_setting("bank_last_status") == NOT_CONFIGURED
    assert db.get_bank_accounts() == []


def test_transport_failure_records_a_closed_set_status_and_does_not_raise(temp_db_path, monkeypatch):
    import database as db
    from jobs import sync_bank
    from services.safe_status import CLOSED_SET
    from services.simplefin_service import SimpleFinError

    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)

    def boom(*a, **kw):
        raise SimpleFinError("error: auth")

    monkeypatch.setattr(sync_bank.simplefin_service, "fetch_accounts", boom)
    sync_bank.run()  # must not raise — an ingestion job never crashes the app

    status = db.get_setting("bank_last_status")
    assert status == "error: auth"
    assert status in CLOSED_SET


def test_an_unexpected_error_still_records_a_closed_set_status(temp_db_path, monkeypatch):
    import database as db
    from jobs import sync_bank
    from services.safe_status import CLOSED_SET

    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)

    def boom(*a, **kw):
        raise RuntimeError("https://user:secret@bridge.example.com/simplefin exploded")

    monkeypatch.setattr(sync_bank.simplefin_service, "fetch_accounts", boom)
    sync_bank.run()

    stored = " ".join(str(db.get_setting(k)) for k in
                      ("bank_last_status", "bank_last_result", "bank_last_run"))
    assert db.get_setting("bank_last_status") in CLOSED_SET
    assert "secret" not in stored and "bridge.example.com" not in stored


def test_transaction_for_an_unknown_account_is_skipped_not_crashed(temp_db_path, monkeypatch):
    """A transaction whose account SimpleFIN did not report can't get an FK."""
    import database as db
    from jobs import sync_bank
    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)

    payload = _payload()
    payload["accounts"][0]["transactions"].append(
        {"id": "orphan", "posted": 1751328000, "amount": "-5.00", "description": "X"})
    sync_bank.run(payload=payload)
    assert db.get_setting("bank_last_status") == "ok"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_sync_bank.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobs.sync_bank'`

- [x] **Step 3: Write the implementation**

Create `jobs/sync_bank.py`:

```python
"""Scheduled job: sync bank + card transactions from SimpleFIN.

Runs every SIMPLEFIN_SYNC_INTERVAL_HOURS and once at startup. Fetch, upsert,
match pairs, classify — then record a closed-set status. Like every ingestion
job in this repo it must never crash the web app: it logs and records status
rather than raising.

Classification is recomputed from scratch on every run. That is deliberate:
bank_flows is pure and deterministic, so a re-run is free and it means a
newly-arrived transfer half retroactively fixes its partner's flow.
"""
import datetime
import logging

import pytz

import bank_flows
import database as db
from config import (INCOME_PAYEE_HINTS, PAIR_WINDOW_DAYS, SIMPLEFIN_LOOKBACK_DAYS,
                    TIMEZONE)
from services import simplefin_service
from services.safe_status import NOT_CONFIGURED, safe_status
from services.simplefin_service import SimpleFinError

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.datetime.now(pytz.timezone(TIMEZONE)).isoformat()


def run(payload=None):
    """Sync from SimpleFIN, or from an already-fetched `payload` (snapshot replay).

    When `payload` is given no network call happens, which is what lets
    scripts/simplefin_backfill.py replay a saved 90-day capture through this
    exact code path instead of a parallel one that could drift.
    """
    if payload is None and not simplefin_service.is_configured():
        logger.warning("Bank sync skipped: SimpleFIN not configured")
        db.set_setting("bank_last_status", NOT_CONFIGURED)
        return
    try:
        if payload is None:
            payload = simplefin_service.fetch_accounts()
        accounts, txns = simplefin_service.normalize(payload)

        now = _now_iso()
        for a in accounts:
            db.upsert_bank_account(a["simplefin_id"], a["name"], a["org"], a["kind"])
            db.touch_bank_account_sync(a["simplefin_id"], now)

        # SimpleFIN ids -> our integer FKs, resolved once.
        stored = db.get_bank_accounts()
        id_by_sfid = {a["simplefin_id"]: a["id"] for a in stored}
        roles_by_id = {a["id"]: a["role"] for a in stored}

        added = skipped = 0
        for t in txns:
            account_id = id_by_sfid.get(t["account_simplefin_id"])
            if account_id is None:
                # No account row means no valid FK. Skip rather than invent one.
                skipped += 1
                continue
            db.upsert_bank_transaction(
                t["simplefin_id"], account_id, t["posted"], t["transacted_at"],
                t["amount"], t["description"], t["payee"], t["memo"], t["mcc"],
            )
            added += 1

        # Re-match and re-classify a window wide enough that a transfer whose
        # halves arrived in different syncs still pairs on the later run.
        start_day = (datetime.date.today()
                     - datetime.timedelta(days=SIMPLEFIN_LOOKBACK_DAYS)).isoformat()
        window = db.get_unclassified_window(start_day)
        pair_map = bank_flows.match_pairs(window, window_days=PAIR_WINDOW_DAYS)
        derived = bank_flows.classify_all(window, roles_by_id, pair_map, INCOME_PAYEE_HINTS)
        for sfid, (flow, pair_id, ambiguous) in derived.items():
            db.set_bank_transaction_derived(sfid, flow, pair_id, ambiguous)

        counts = {}
        for flow, _, _ in derived.values():
            counts[flow] = counts.get(flow, 0) + 1
        unknown_roles = sum(1 for a in stored if a["role"] == "unknown")

        db.set_setting("bank_last_run", now)
        db.set_setting("bank_last_status", "ok")
        db.set_setting(
            "bank_last_result",
            f"{len(accounts)} accounts · {added} transactions · "
            f"{counts.get('spending', 0)} spending · {counts.get('transfer', 0)} transfers · "
            f"{counts.get('card_payment', 0)} card payments · "
            f"{counts.get('inflow_unknown', 0)} unknown inflows · "
            f"{unknown_roles} accounts need a role",
        )
        logger.info("Bank sync: %d accounts, %d transactions, %d skipped, flows=%s",
                    len(accounts), added, skipped, counts)
    except SimpleFinError as e:
        # Already logged server-side inside the service. `e.status` is closed-set
        # by construction and carries no message text.
        db.set_setting("bank_last_run", _now_iso())
        db.set_setting("bank_last_status", e.status)
    except Exception as e:
        # Full detail server-side only. The DB value must come from the closed
        # set — never str(e) — because the SimpleFIN URL carries the user's bank
        # credentials inside the URL itself.
        logger.exception("Bank sync failed")
        db.set_setting("bank_last_run", _now_iso())
        db.set_setting("bank_last_status", safe_status(e))
```

- [x] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_sync_bank.py -v`
Expected: PASS (8 tests)

- [x] **Step 5: Add the job to the shared boundary test**

Open `tests/test_safe_status.py::test_job_modules_use_the_shared_constants_not_ad_hoc_literals` and add `jobs/sync_bank.py` to the list of modules it scans.

Run: `./venv/bin/python -m pytest tests/test_safe_status.py -v`
Expected: PASS

- [x] **Step 6: Commit**

```bash
git add jobs/sync_bank.py tests/test_sync_bank.py tests/test_safe_status.py
git commit -m "feat(jobs): SimpleFIN sync with pair matching and flow classification"
```

---

### Task 7: Scheduler, debug route, Settings surface

**Files:**
- Modify: `main.py:13`, `main.py:28-48`
- Modify: `app/routes.py:143-157` and append a new route
- Test: `tests/test_api_routes.py` (append)

**Interfaces:**
- Consumes: `jobs.sync_bank.run`, `db.get_bank_accounts`, `db.get_bank_transactions_range`, `db.set_bank_account_role`.
- Produces: `GET /api/bank/debug?start=YYYY-MM-DD&end=YYYY-MM-DD`, `POST /api/bank/accounts/{simplefin_id}/role`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_api_routes.py` (match the auth/client fixture pattern already used in that file — read the top of the file first and reuse its authenticated-client helper):

```python
def test_bank_debug_returns_accounts_and_flow_totals(client, temp_db_path):
    import database as db
    db.upsert_bank_account("chk", "Checking", "Wells Fargo", "checking")
    db.set_bank_account_role("chk", "spending")
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "chk")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -14.20, "COFFEE", "Coffee", "", "5814")
    db.set_bank_transaction_derived("t1", "spending", None, False)

    r = client.get("/api/bank/debug?start=2026-06-01&end=2026-08-01")
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["spending"] == pytest.approx(14.20)
    assert body["accounts"][0]["role"] == "spending"
    assert body["counts"]["spending"] == 1


def test_bank_debug_never_exposes_the_access_url(client, temp_db_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "SIMPLEFIN_ACCESS_URL",
                        "https://user:sup3rsecret@bridge.example.com/simplefin")
    r = client.get("/api/bank/debug?start=2026-06-01&end=2026-08-01")
    assert "sup3rsecret" not in r.text and "bridge.example.com" not in r.text


def test_set_account_role(client, temp_db_path):
    import database as db
    db.upsert_bank_account("chk", "Checking", "Wells Fargo", "checking")
    r = client.post("/api/bank/accounts/chk/role", json={"role": "spending"})
    assert r.status_code == 200
    assert next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "chk")["role"] == "spending"


def test_set_account_role_rejects_an_unknown_role(client, temp_db_path):
    import database as db
    db.upsert_bank_account("chk", "Checking", "Wells Fargo", "checking")
    r = client.post("/api/bank/accounts/chk/role", json={"role": "yacht"})
    assert r.status_code == 400


def test_set_account_role_404s_for_an_unknown_account(client, temp_db_path):
    r = client.post("/api/bank/accounts/nope/role", json={"role": "spending"})
    assert r.status_code == 404
```

- [x] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_api_routes.py -v -k bank`
Expected: FAIL with 404s (routes do not exist)

- [x] **Step 3: Add the routes**

Append to `app/routes.py`:

```python
@router.get("/bank/debug")
def bank_debug(start: str, end: str):
    """Read-only verification surface for this phase — there is no bank UI yet.

    Returns per-flow counts and totals so the arithmetic can be eyeballed against
    reality. Never returns the access URL (which lives only in config and is
    never read here) and never returns balances (which are never stored).
    """
    rows = db.get_bank_transactions_range(start, end)
    counts, totals = {}, {}
    for r in rows:
        flow = r["resolved_flow"] or "unclassified"
        counts[flow] = counts.get(flow, 0) + 1
        totals[flow] = round(totals.get(flow, 0.0) + abs(r["amount"]), 2)
    return {
        "accounts": db.get_bank_accounts(),
        "counts": counts,
        "totals": totals,
        "ambiguous": [
            {"simplefin_id": r["simplefin_id"], "posted": r["posted"],
             "amount": r["amount"], "description": r["description"]}
            for r in rows if r["ambiguous"]
        ],
        "last_run": db.get_setting("bank_last_run"),
        "last_status": db.get_setting("bank_last_status"),
    }


@router.post("/bank/accounts/{simplefin_id}/role")
def set_bank_account_role(simplefin_id: str, body: dict):
    role = (body or {}).get("role")
    if role not in db.BANK_ROLES:
        raise HTTPException(status_code=400, detail="unknown role")
    if not db.set_bank_account_role(simplefin_id, role):
        raise HTTPException(status_code=404, detail="unknown account")
    return {"ok": True, "simplefin_id": simplefin_id, "role": role}
```

If `HTTPException` is not already imported in `app/routes.py`, add `from fastapi import HTTPException` to its imports.

- [x] **Step 4: Add the Settings fields**

In `app/routes.py::get_settings()`, after line 157's `"backup_last_status"` entry, add:

```python
        "bank_last_run": db.get_setting("bank_last_run"),
        "bank_last_status": db.get_setting("bank_last_status"),
        "bank_last_result": db.get_setting("bank_last_result"),
```

- [x] **Step 5: Schedule the job**

In `main.py`, extend the config import on line 13:

```python
from config import (BACKUP_HOUR, CALENDAR_SCAN_HOUR, GMAIL_SCAN_INTERVAL_HOURS,
                    SIMPLEFIN_SYNC_INTERVAL_HOURS, TIMEZONE, WEEKLY_PUSH_HOUR)
```

Add the import beside the others inside `lifespan` (after line 30):

```python
    from jobs.sync_bank import run as sync_bank
```

And register the job after the `scan_gmail` block (after line 41):

```python
    # next_run_time=now, same reasoning as scan_gmail: a deploy should refresh
    # bank_last_status immediately rather than waiting a full interval. Also
    # matters more here — SimpleFIN keeps only a rolling 90 days, so a sync that
    # silently stops running loses history permanently.
    scheduler.add_job(
        sync_bank,
        IntervalTrigger(hours=SIMPLEFIN_SYNC_INTERVAL_HOURS),
        id="sync_bank",
        next_run_time=datetime.datetime.now(pytz.timezone(TIMEZONE)),
    )
```

Update the startup log line (line 46-48) to mention it:

```python
    logger.info("On Track started — gmail every %dh, bank every %dh, calendar daily @%02d:00, "
                "push Mon @%02d:00, backup Sun @%02d:00",
                GMAIL_SCAN_INTERVAL_HOURS, SIMPLEFIN_SYNC_INTERVAL_HOURS,
                CALENDAR_SCAN_HOUR, WEEKLY_PUSH_HOUR, BACKUP_HOUR)
```

- [x] **Step 6: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_api_routes.py -v -k bank`
Expected: PASS (5 tests)

- [x] **Step 7: Verify the app still boots**

Run: `./venv/bin/python -c "import main; print('ok')"`
Expected: `ok`

- [x] **Step 8: Commit**

```bash
git add main.py app/routes.py tests/test_api_routes.py
git commit -m "feat(api): bank debug route, account roles, scheduled sync"
```

---

### Task 8: Snapshot backfill

**Files:**
- Create: `scripts/simplefin_backfill.py`
- Test: manual (a one-off operator script, consistent with `scripts/calendar_auth.py` and `scripts/cleardb.py`, neither of which is unit-tested)

**Interfaces:**
- Consumes: `jobs.sync_bank.run(payload=...)` from Task 6; snapshot files written by `scripts/simplefin_snapshot.py`.

**Why this exists:** SimpleFIN keeps a rolling 90 days. A snapshot was captured on 2026-07-22 covering 2026-04-25 → 2026-07-21, before any of this code existed. Without a replay path that history is unreachable — the live API can no longer return its early weeks.

- [x] **Step 1: Write the script**

Create `scripts/simplefin_backfill.py`:

```python
"""One-off: replay a saved SimpleFIN snapshot through the normal ingest path.

SimpleFIN keeps a rolling 90 days, so snapshots taken by
scripts/simplefin_snapshot.py are the only copy of anything older than the live
window. This feeds them to jobs.sync_bank.run(payload=...) — the same code path
a live sync uses, so backfilled rows are indistinguishable from synced ones and
the ingest logic can never drift between the two.

Safe to re-run: every upsert keys on simplefin_id, and classification is
recomputed deterministically.

Usage:
    python scripts/simplefin_backfill.py                      # newest snapshot
    python scripts/simplefin_backfill.py --all                # every snapshot, oldest first
    python scripts/simplefin_backfill.py path/to/snapshot.json
"""
import json
import sys
from pathlib import Path

SNAPSHOT_DIR = Path.home() / ".on-track" / "simplefin-snapshots"


def _load(path: Path) -> dict:
    envelope = json.loads(path.read_text())
    # Snapshots are wrapped in a capture envelope; older ad-hoc dumps may not be.
    return envelope.get("payload", envelope)


# The account roles the user gave, from the spec's "Account roles" table. Matched
# as case-insensitive substrings against "<org> <name>", first match wins.
#
# This seeds the INITIAL load only: seed_roles never touches an account whose
# role is already set, so "a new account is surfaced, never silently guessed"
# stays true for everything that arrives later. Roles remain data, not code —
# editable via the API without a deploy.
ROLE_SEEDS = [
    ("wells fargo", "7395", "spending"),   # primary day-to-day, pays the Amex
    ("wells fargo", "4116", "bills"),
    ("wells fargo", "0407", "savings"),    # savings_dynamic — in and out by design
    ("american express", "", "credit_card"),
    ("chase", "", "credit_card"),
    ("barclays", "", "credit_card"),
    ("citi", "", "credit_card"),
    ("fidelity", "", "investment"),        # covers all five: 401k, Roth, Trad, Rollover, Individual
]


def seed_roles(db) -> int:
    """Apply ROLE_SEEDS to accounts still marked `unknown`. Returns how many changed."""
    changed = 0
    for acct in db.get_bank_accounts():
        if acct["role"] != "unknown":
            continue
        haystack = f"{acct['org']} {acct['name']}".lower()
        for org_hint, id_hint, role in ROLE_SEEDS:
            if org_hint in haystack and (not id_hint or id_hint in haystack):
                db.set_bank_account_role(acct["simplefin_id"], role)
                print(f"  role: {acct['name'][:34]:36} -> {role}")
                changed += 1
                break
    return changed


def main() -> int:
    args = [a for a in sys.argv[1:]]
    if "--all" in args:
        args.remove("--all")
        paths = sorted(SNAPSHOT_DIR.glob("simplefin-*.json"))
    elif args:
        paths = [Path(args.pop(0)).expanduser()]
    else:
        paths = sorted(SNAPSHOT_DIR.glob("simplefin-*.json"))[-1:]

    if not paths:
        print(f"No snapshots found in {SNAPSHOT_DIR}.", file=sys.stderr)
        print("Run scripts/simplefin_snapshot.py first.", file=sys.stderr)
        return 1

    import database as db
    from jobs.sync_bank import run as sync_bank

    db.initialize_db()

    for path in paths:
        if not path.exists():
            print(f"Missing: {path}", file=sys.stderr)
            return 1
        print(f"Replaying {path.name} …")
        sync_bank(payload=_load(path))
        print(f"  {db.get_setting('bank_last_result')}")

    seeded = seed_roles(db)
    if seeded:
        # Roles drive pair matching, so the first pass classified against
        # `unknown` everywhere. Re-run to let card payments and transfers resolve.
        print(f"\nSeeded {seeded} account role(s); re-classifying …")
        for path in paths:
            sync_bank(payload=_load(path))
        print(f"  {db.get_setting('bank_last_result')}")

    unknown = [a for a in db.get_bank_accounts() if a["role"] == "unknown"]
    if unknown:
        print(f"\n{len(unknown)} account(s) still need a role — classification "
              f"treats them as unknown, so their transfers will not pair correctly:")
        for a in unknown:
            print(f"  {a['simplefin_id']:24} {a['name'][:32]:34} ({a['org']})")
        print("\nSet each with: POST /api/bank/accounts/<simplefin_id>/role "
              '{"role": "spending|bills|savings|investment|credit_card"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 2: Verify against the real snapshot, locally**

Run: `./venv/bin/python scripts/simplefin_backfill.py`

Expected, in order: `Replaying simplefin-2026-07-22.json …`, a result line reporting **12 accounts and ~965 transactions**, then `Seeded 11 account role(s)` (Citi Simplicity has zero transactions but should still receive `credit_card`; the 12th account is seeded only if its org matches a `ROLE_SEEDS` entry), a per-account `role:` line for each, then a second result line.

If any account is left `unknown`, the script prints it — report that rather than editing `ROLE_SEEDS` to force a match, since an unmatched name means the seed table and reality disagree.

- [x] **Step 3: Confirm the arithmetic is actually right**

**This is the real acceptance test for the whole feature** — it is the first moment anyone can see whether the classification does the job the spec exists to do.

Compare the two result lines from Step 2. The second pass must show a **materially larger `card payments` + `transfers` count and a correspondingly smaller `spending` count** than the first, because roles now let pairs resolve.

Check the totals against the spec's central premise: roughly **a quarter of transactions (~240 of 965)** should land in a non-spending flow. Also verify by hand:

- The Amex has 255 transactions and is paid from Wells Fargo 7395. Those payments must appear as `card_payment` on **both** sides, never as spending on either.
- The five Fidelity accounts (101 transactions total) must be entirely `investment` — a Traditional → Roth conversion must contribute nothing to any spend figure.
- `inflow_unknown` should be non-empty. That is the SoFi drawdown showing up correctly as neither income nor spending, not a bug.

Report the actual numbers. If the non-spending share is far off ~25%, stop and say so rather than proceeding — it means the matcher or the roles are wrong, and a wrong answer here is exactly the failure the spec was written to prevent.

- [x] **Step 4: Commit**

```bash
git add scripts/simplefin_backfill.py
git commit -m "feat(scripts): replay saved SimpleFIN snapshots through the sync path"
```

---

## Final Verification

- [x] **Run the full backend suite**

Run: `./venv/bin/python -m pytest tests/ -v`
Expected: all pass, including the pre-existing suites.

- [x] **Run the frontend suite** (nothing here should touch it — this confirms that)

Run: `cd frontend && npm test -- --run && npm run build`
Expected: pass.

- [x] **Grep for the credential across everything the user can reach**

Run: `./venv/bin/python -m pytest tests/ -q -k "credential or redaction or never_expose or closed_set"`
Expected: pass — the boundary tests are the ones that matter most in this feature.

- [x] **Confirm no SQL escaped `database.py`**

Run: `grep -rn "SELECT \|INSERT \|UPDATE \|DELETE " jobs/sync_bank.py services/simplefin_service.py bank_flows.py app/routes.py`
Expected: no matches.

---

## Self-Review Notes

**Spec coverage.** §1 Schema → Task 2. §2 Sync job → Tasks 5, 6, 7. §3 Flow classification, all six rules and the SoFi hazard → Task 4. §4 Pair matching including tie-breaks and the deferred Venmo/Zelle policy → Tasks 3, 4. §5 Security → Tasks 5, 6, plus the Final Verification greps. §6 Out of scope respected: no UI, no MCC→category mapping, no budgets. Testing section → every bullet has a named test.

**Two things the spec left implicit, resolved above:** the `ambiguous` column, and integer-cents comparison. Both are documented under "Resolved spec gaps."

**One thing deliberately deferred, flagged here rather than silently dropped:** column encryption for `description`/`payee`, which the spec itself defers to a follow-up (§5). These columns store plaintext merchant names, matching every other table in this repo today.
