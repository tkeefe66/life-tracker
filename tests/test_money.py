"""app/money.py: the summary(weeks) aggregate over resolved bank flows.

Follows tests/test_database_bank.py's seeding conventions (upsert_bank_account /
upsert_bank_transaction / set_bank_transaction_derived / set_bank_flow_override) and
tests/test_scorecard.py's dynamic-today convention for anything that depends on
"this week" — money.summary() windows off app.scorecard._local_today(), so tests that
care about week boundaries compute dates relative to that, never hardcoded absolute
dates.
"""
from datetime import timedelta

import pytest


def _account(db, sfid="acct-1", role="spending"):
    db.upsert_bank_account(sfid, "Everyday Checking", "Wells Fargo", "checking")
    if role:
        db.set_bank_account_role(sfid, role)
    return next(a for a in db.get_bank_accounts() if a["simplefin_id"] == sfid)


def _txn(db, sfid, account_id, posted, amount, flow, pair_id=None, ambiguous=False, user_flow=None):
    db.upsert_bank_transaction(sfid, account_id, posted, posted, amount, "DESC", "", "", None)
    db.set_bank_transaction_derived(sfid, flow, pair_id, ambiguous)
    if user_flow is not None:
        db.set_bank_flow_override(sfid, user_flow)


def _this_monday(scorecard, metrics):
    return metrics.week_bounds(scorecard._local_today())[0]


# ── Double-count guard: a matched pair sums to one side, not both ──────────────

@pytest.mark.parametrize("flow", ["transfer", "card_payment", "investment"])
def test_matched_pair_reports_outflow_only_not_doubled(temp_db_path, flow):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct_a = _account(db, "acct-a")
    acct_b = _account(db, "acct-b")
    today = scorecard._local_today().isoformat()
    _txn(db, "out1", acct_a["id"], today, -1000.0, flow, pair_id="p1")
    _txn(db, "in1", acct_b["id"], today, 1000.0, flow, pair_id="p1")

    result = money.summary(weeks=1)
    assert result["totals"][flow]["amount"] == 1000.0
    assert result["totals"][flow]["count"] == 2


# ── Override moves the money: aggregate reads resolved_flow, not bare flow ─────

def test_override_moves_money_out_of_spending_into_transfer(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _txn(db, "t1", acct["id"], today, -50.0, "spending")

    before = money.summary(weeks=1)
    assert before["spent"] == 50.0
    assert "transfer" not in before["totals"]

    db.set_bank_flow_override("t1", "transfer")

    after = money.summary(weeks=1)
    assert after["spent"] == 0
    assert after["spent"] != before["spent"]
    assert after["totals"]["transfer"]["amount"] == 50.0
    assert "spending" not in after["totals"]


# ── Weeks before coverage are absent, not zero ──────────────────────────────────

def test_weeks_before_coverage_are_absent_not_zero(temp_db_path):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct = _account(db)
    this_monday = _this_monday(scorecard, metrics)
    txn_monday = this_monday - timedelta(weeks=3)
    _txn(db, "t1", acct["id"], txn_monday.isoformat(), -20.0, "spending")

    result = money.summary(weeks=12)
    assert len(result["weeks"]) <= 4
    assert result["weeks"][0]["week_start"] == txn_monday.isoformat()
    assert result["covered_from"] == txn_monday.isoformat()


# ── Partial flags ────────────────────────────────────────────────────────────

def test_first_week_partial_when_coverage_starts_midweek(temp_db_path):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct = _account(db)
    this_monday = _this_monday(scorecard, metrics)
    first_monday = this_monday - timedelta(weeks=5)
    covered_from = first_monday + timedelta(days=2)  # Wednesday, mid-week
    _txn(db, "t1", acct["id"], covered_from.isoformat(), -20.0, "spending")

    result = money.summary(weeks=12)
    weeks = result["weeks"]
    assert weeks[0]["week_start"] == first_monday.isoformat()
    assert weeks[0]["partial"] is True
    assert weeks[-1]["week_start"] == this_monday.isoformat()
    assert weeks[-1]["partial"] is True  # last week always partial — in progress
    if len(weeks) > 2:
        assert weeks[1]["partial"] is False  # a fully-covered middle week


def test_first_week_not_partial_when_coverage_starts_exactly_monday(temp_db_path):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct = _account(db)
    this_monday = _this_monday(scorecard, metrics)
    first_monday = this_monday - timedelta(weeks=5)
    _txn(db, "t1", acct["id"], first_monday.isoformat(), -20.0, "spending")

    result = money.summary(weeks=12)
    weeks = result["weeks"]
    assert weeks[0]["week_start"] == first_monday.isoformat()
    assert weeks[0]["partial"] is False
    assert weeks[-1]["partial"] is True  # still always partial, being in progress


# ── Empty table ──────────────────────────────────────────────────────────────

def test_empty_table_returns_shaped_payload(temp_db_path):
    import app.money as money

    result = money.summary(weeks=12)
    assert result == {
        "covered_from": None,
        "covered_to": None,
        "weeks": [],
        "totals": {},
        "spent": 0,
        "tracked": [],
        "triage_counts": {"ambiguous": 0, "inflow_unknown": 0},
    }


# ── tracked reuses app.scorecard.spend(), not a re-derivation ──────────────────

def test_tracked_reuses_scorecard_spend_and_excludes_confirmed_work_ride(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    today = scorecard._local_today().isoformat()
    db.add_ride("r1", "Uber", f"{today}T08:00:00", f"{today}T08:00", "Personal", 20.0)
    db.add_ride("r2", "Lyft", f"{today}T09:00:00", f"{today}T09:00", "Work trip", 40.0)
    rides = db.get_rides_range(today, today)
    work_id = next(r["id"] for r in rides if r["subject"] == "Work trip")
    db.set_ride_work_override(work_id, True)

    result = money.summary(weeks=1)
    tracked = result["tracked"]
    assert any(row["service"] == "Uber" and row["kind"] == "ride" for row in tracked)
    assert not any(row["service"] == "Lyft" for row in tracked)

    # Same figures as the existing scorecard helper for the identical window —
    # proof this is reuse, not a re-derivation.
    assert tracked == scorecard.spend(1)["by_service"]


# ── weeks clamps 1–52 ────────────────────────────────────────────────────────

def test_weeks_below_one_clamps_to_one(temp_db_path):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct = _account(db)
    this_monday = _this_monday(scorecard, metrics)
    two_weeks_ago = this_monday - timedelta(weeks=2)
    _txn(db, "old", acct["id"], two_weeks_ago.isoformat(), -10.0, "spending")
    today = scorecard._local_today().isoformat()
    _txn(db, "new", acct["id"], today, -5.0, "spending")

    result = money.summary(weeks=0)
    assert len(result["weeks"]) == 1
    assert result["weeks"][0]["week_start"] == this_monday.isoformat()


def test_weeks_above_52_clamps_to_52(temp_db_path):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct = _account(db)
    this_monday = _this_monday(scorecard, metrics)
    far_back_monday = this_monday - timedelta(weeks=60)
    _txn(db, "ancient", acct["id"], far_back_monday.isoformat(), -10.0, "spending")
    today = scorecard._local_today().isoformat()
    _txn(db, "recent", acct["id"], today, -5.0, "spending")

    result = money.summary(weeks=9999)
    # covered_from reflects the WHOLE table, unaffected by the clamp.
    assert result["covered_from"] == far_back_monday.isoformat()
    # but the weeks window itself is clamped to 52, so the 60-week-old week
    # never appears even though the table's coverage reaches back that far.
    assert len(result["weeks"]) == 52
    expected_first = this_monday - timedelta(weeks=51)
    assert result["weeks"][0]["week_start"] == expected_first.isoformat()
