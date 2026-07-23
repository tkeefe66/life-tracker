"""bank_accounts / bank_transactions: upserts preserve user data, COALESCE resolves in SQL."""
import pytest


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


def test_bulk_derived_write_commits_all_rows_together(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -10.0, "A", "", "", None)
    db.upsert_bank_transaction("t2", acct["id"], "2026-07-02", "2026-07-01",
                               20.0, "B", "", "", None)

    db.set_bank_transactions_derived_bulk([
        ("t1", "transfer", "t1", False),
        ("t2", "transfer", "t1", False),
    ])

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["t1"]["flow"] == "transfer"
    assert rows["t2"]["flow"] == "transfer"
    assert rows["t1"]["pair_id"] == rows["t2"]["pair_id"] == "t1"


def test_bulk_derived_write_is_atomic_an_exception_partway_leaves_no_rows_updated(temp_db_path):
    """A review found the per-row version leaves a hole: if a run is interrupted
    partway, one half of a matched pair keeps its pair_id and the other goes free
    — and it does not self-heal on the next sync. The bulk write must commit
    all-or-nothing."""
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -10.0, "A", "", "", None)
    db.upsert_bank_transaction("t2", acct["id"], "2026-07-02", "2026-07-01",
                               20.0, "B", "", "", None)

    # The second item's pair_id is a list — sqlite3 (and psycopg2) cannot bind an
    # unsupported parameter type, so execute() raises partway through the loop.
    items = [
        ("t1", "transfer", "t1", False),
        ("t2", "transfer", ["not", "a", "valid", "param"], False),
    ]
    with pytest.raises(Exception):
        db.set_bank_transactions_derived_bulk(items)

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["t1"]["flow"] is None  # the first row's write was rolled back too
    assert rows["t2"]["flow"] is None


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
