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


# ── Triage queries and the bulk override writer ────────────────────────────────

def test_triage_returns_ambiguous_row_with_no_user_flow(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -40.0, "VENMO PAYMENT", "Venmo", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, True)

    triage = db.get_bank_triage(50)
    assert [r["simplefin_id"] for r in triage["ambiguous"]] == ["t1"]
    assert triage["ambiguous"][0]["ambiguous"] is True
    assert triage["ambiguous"][0]["user_flow"] is None


def test_reappearing_queue_trap_resync_after_override_does_not_reopen(temp_db_path):
    """spec §6.4. Without the `user_flow IS NULL` predicate the third assertion
    fails and the queue is uncleanable — this is the most important test in the
    feature."""
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -40.0, "VENMO PAYMENT", "Venmo", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, True)

    ids = [r["simplefin_id"] for r in db.get_bank_triage(50)["ambiguous"]]
    assert "t1" in ids

    db.set_bank_flow_override("t1", "transfer")
    ids = [r["simplefin_id"] for r in db.get_bank_triage(50)["ambiguous"]]
    assert "t1" not in ids

    # Simulate the next sync's classification pass recomputing ambiguous = true
    # from scratch (it reads `flow`, not `resolved_flow`, so it has no idea a
    # user already ruled on this row).
    db.set_bank_transaction_derived("t1", "spending", None, True)
    ids = [r["simplefin_id"] for r in db.get_bank_triage(50)["ambiguous"]]
    assert "t1" not in ids


def test_clearing_override_returns_row_to_the_queue(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -40.0, "VENMO PAYMENT", "Venmo", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, True)
    db.set_bank_flow_override("t1", "transfer")
    assert "t1" not in [r["simplefin_id"] for r in db.get_bank_triage(50)["ambiguous"]]

    db.set_bank_flow_override("t1", None)
    assert "t1" in [r["simplefin_id"] for r in db.get_bank_triage(50)["ambiguous"]]


def test_inflow_unknown_bucket_keyed_on_resolved_flow(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               500.0, "MYSTERY DEPOSIT", "", "", None)
    db.set_bank_transaction_derived("t1", "inflow_unknown", None, False)
    db.upsert_bank_transaction("t2", acct["id"], "2026-07-02", "2026-07-02",
                               600.0, "MYSTERY DEPOSIT 2", "", "", None)
    db.set_bank_transaction_derived("t2", "inflow_unknown", None, False)
    db.set_bank_flow_override("t2", "income")

    ids = [r["simplefin_id"] for r in db.get_bank_triage(50)["inflow_unknown"]]
    assert ids == ["t1"]  # t2's resolved_flow is "income", not inflow_unknown


def test_triage_ordering_newest_posted_first_tie_broken_by_simplefin_id(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    for sfid, posted in [("b", "2026-07-01"), ("a", "2026-07-01"), ("c", "2026-07-03")]:
        db.upsert_bank_transaction(sfid, acct["id"], posted, posted, -10.0, "X", "", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, True)

    ids = [r["simplefin_id"] for r in db.get_bank_triage(50)["ambiguous"]]
    assert ids == ["c", "a", "b"]  # newest posted first; 07-01 tie broken a < b


def test_triage_limit_caps_each_bucket_independently(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    for i in range(3):
        sfid = f"amb-{i}"
        db.upsert_bank_transaction(sfid, acct["id"], f"2026-07-0{i+1}", f"2026-07-0{i+1}",
                                   -10.0, "X", "", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, True)
    for i in range(3):
        sfid = f"inf-{i}"
        db.upsert_bank_transaction(sfid, acct["id"], f"2026-07-0{i+1}", f"2026-07-0{i+1}",
                                   500.0, "Y", "", "", None)
        db.set_bank_transaction_derived(sfid, "inflow_unknown", None, False)

    triage = db.get_bank_triage(2)
    assert len(triage["ambiguous"]) == 2
    assert len(triage["inflow_unknown"]) == 2


def test_recently_sorted_only_user_flow_rows_newest_first_capped(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("no-override", acct["id"], "2026-07-05", "2026-07-05",
                               -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("no-override", "spending", None, False)

    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, False)
    db.set_bank_flow_override("t1", "transfer")

    db.upsert_bank_transaction("t2", acct["id"], "2026-07-03", "2026-07-03",
                               -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("t2", "spending", None, False)
    db.set_bank_flow_override("t2", "income")

    rows = db.get_bank_recently_sorted(50)
    assert [r["simplefin_id"] for r in rows] == ["t2", "t1"]  # newest posted first, no-override absent

    capped = db.get_bank_recently_sorted(1)
    assert [r["simplefin_id"] for r in capped] == ["t2"]


def test_bulk_flow_override_updates_and_counts_skips_unknown_ids(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", -10.0, "X", "", "", None)
    db.upsert_bank_transaction("b", acct["id"], "2026-07-02", "2026-07-02", -10.0, "X", "", "", None)

    count = db.set_bank_flow_overrides_bulk(["a", "b", "nope"], "transfer")
    assert count == 2

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["a"]["user_flow"] == "transfer"
    assert rows["b"]["user_flow"] == "transfer"


def test_bulk_flow_override_unknown_flow_raises_before_any_write(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("a", "spending", None, False)
    db.set_bank_flow_override("a", "transfer")

    with pytest.raises(ValueError):
        db.set_bank_flow_overrides_bulk(["a"], "not-a-real-flow")

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["a"]["user_flow"] == "transfer"  # unchanged — nothing written


def test_bulk_flow_override_none_clears(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("a", "spending", None, False)
    db.set_bank_flow_override("a", "transfer")

    count = db.set_bank_flow_overrides_bulk(["a"], None)
    assert count == 1

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["a"]["user_flow"] is None
