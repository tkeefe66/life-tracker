"""sync_bank: idempotent, never crashes, never leaks, never clobbers user data."""
import time

import pytest

# sync_bank.run() reclassifies only a SIMPLEFIN_LOOKBACK_DAYS-wide window measured
# back from the real wall clock (see jobs/sync_bank.py). A fixed epoch literal
# would drift out of that window as the calendar advances and silently stop
# being classified at all — so `posted` is computed relative to "now" at test
# run time instead of hardcoded.
_POSTED = int(time.time()) - 5 * 86400


def _payload():
    return {
        "accounts": [
            {"id": "chk", "name": "EVERYDAY CHECKING", "org": {"name": "Wells Fargo"},
             "transactions": [
                 {"id": "p1", "posted": _POSTED, "amount": "-2000.00",
                  "description": "AUTOPAY PAYMENT THANK YOU"},
                 {"id": "s1", "posted": _POSTED, "amount": "-14.20",
                  "description": "COFFEE SHOP", "mcc": "5814"},
             ]},
            {"id": "card", "name": "Platinum Card", "org": {"name": "American Express"},
             "transactions": [
                 {"id": "p2", "posted": _POSTED, "amount": "2000.00",
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
    rows = db.get_bank_transactions_range("2020-01-01", "2030-01-01")
    assert {r["simplefin_id"] for r in rows} == {"p1", "s1", "p2"}
    assert db.get_setting("bank_last_status") == "ok"


def test_card_payment_is_matched_and_not_counted_as_spending(temp_db_path, monkeypatch):
    import database as db
    sync_bank = _configure(monkeypatch)
    sync_bank.run(payload=_payload())  # re-run now that roles are set

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2020-01-01", "2030-01-01")}
    assert rows["p1"]["resolved_flow"] == "card_payment"
    assert rows["p2"]["resolved_flow"] == "card_payment"
    assert rows["p1"]["pair_id"] == rows["p2"]["pair_id"] is not None
    assert rows["s1"]["resolved_flow"] == "spending"


def test_resync_is_idempotent(temp_db_path, monkeypatch):
    import database as db
    sync_bank = _configure(monkeypatch)
    sync_bank.run(payload=_payload())
    before = db.get_bank_transactions_range("2020-01-01", "2030-01-01")
    sync_bank.run(payload=_payload())
    after = db.get_bank_transactions_range("2020-01-01", "2030-01-01")
    assert [dict(r) for r in before] == [dict(r) for r in after]


def test_resync_updates_a_settled_amount_but_keeps_user_flow_and_role(temp_db_path, monkeypatch):
    import database as db
    sync_bank = _configure(monkeypatch)
    db.set_bank_flow_override("s1", "transfer")

    settled = _payload()
    settled["accounts"][0]["transactions"][1]["amount"] = "-16.31"
    sync_bank.run(payload=settled)

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2020-01-01", "2030-01-01")}
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
