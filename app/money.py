"""Assemble the Money screen's bank-side aggregate. DB -> domain wiring, the same
role app/scorecard.py plays for the scorecard. No SQL here — all access goes
through database.py. Bank data is not a metric: no targets, no hit/miss, anywhere
in this module."""
from datetime import timedelta

import database as db
import metrics
from app.scorecard import _local_today
from app.scorecard import spend as _tracked_spend

# transfer / card_payment / investment are matched pairs: the same movement posts
# once as an outflow on one account and once as an inflow on the other. Summing
# abs(amount) over both halves would double the total, so these three filter to
# the outflow side (amount < 0) before summing. `spending` and `inflow_unknown`
# rows have no counterpart row to double-count against, so they sum abs(amount)
# straight. `income` filters to positive amounts for the same reason as the
# movement flows, just mirrored.
MOVEMENT_FLOWS = ("transfer", "card_payment", "investment")

MIN_WEEKS = 1
MAX_WEEKS = 52


def _clamp_weeks(weeks: int) -> int:
    return max(MIN_WEEKS, min(MAX_WEEKS, weeks))


def _flow_amount(flow: str, rows: list) -> float:
    if flow in MOVEMENT_FLOWS:
        return sum(abs(t["amount"]) for t in rows if t["amount"] < 0)
    if flow == "income":
        return sum(t["amount"] for t in rows if t["amount"] > 0)
    return sum(abs(t["amount"]) for t in rows)


def _totals(txns: list) -> dict:
    """Group by resolved_flow and aggregate. Round once, at the end — summing
    already-rounded per-row amounts can drift a cent from the true total, the
    same double-rounding trap app.scorecard.spend()'s by_service comment warns
    about. A flow with no rows in the window is simply absent, not a zero entry."""
    grouped: dict = {}
    for t in txns:
        grouped.setdefault(t["resolved_flow"], []).append(t)
    return {
        flow: {"count": len(rows), "amount": round(_flow_amount(flow, rows), 2)}
        for flow, rows in grouped.items()
    }


def _triage_counts(all_txns: list) -> dict:
    """Same predicates as db.get_bank_triage's two buckets — the un-triaged
    queue size. Table-wide and uncapped, not scoped to the requested window:
    this is a "how much is still waiting" count, not a chart figure."""
    ambiguous = sum(1 for t in all_txns if t["ambiguous"] and t["user_flow"] is None)
    inflow_unknown = sum(1 for t in all_txns if t["resolved_flow"] == "inflow_unknown")
    return {"ambiguous": ambiguous, "inflow_unknown": inflow_unknown}


def summary(weeks: int) -> dict:
    """Weekly bank spending plus movement/income totals over a window ending at
    the current (in-progress) week, and the existing tracked-category figures for
    the same window. One db.get_bank_transactions_range call for the window;
    everything else is bucketed in Python."""
    weeks = _clamp_weeks(weeks)
    tracked = _tracked_spend(weeks)["by_service"]

    all_txns = db.get_all_bank_transactions()
    if not all_txns:
        return {
            "covered_from": None,
            "covered_to": None,
            "weeks": [],
            "totals": {},
            "spent": 0,
            "tracked": tracked,
            "triage_counts": {"ambiguous": 0, "inflow_unknown": 0},
        }

    # Derived from the WHOLE table, never the window, so the coverage footnote
    # tells the truth regardless of how small a `weeks` window is requested.
    covered_from = min(t["posted"] for t in all_txns)[:10]
    covered_to = max(t["posted"] for t in all_txns)[:10]

    this_monday = metrics.week_bounds(_local_today())[0]
    week_starts = [this_monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    window_start = week_starts[0].isoformat()
    window_end = metrics.week_bounds(week_starts[-1])[1].isoformat()

    txns = db.get_bank_transactions_range(window_start, window_end)

    weeks_out = []
    for ws in week_starts:
        we = metrics.week_bounds(ws)[1]
        ws_iso, we_iso = ws.isoformat(), we.isoformat()
        if we_iso < covered_from:
            continue  # entirely before coverage began -- absent, not a zero bar
        week_spending = sum(
            abs(t["amount"]) for t in txns
            if t["resolved_flow"] == "spending" and ws_iso <= t["posted"][:10] <= we_iso
        )
        weeks_out.append({"week_start": ws_iso, "spending": round(week_spending, 2), "partial": False})

    if weeks_out:
        # covered_from can only fall strictly after the first surviving week's
        # Monday (coverage began mid-week) or on/before it (coverage began
        # exactly that Monday, or earlier still and this window simply doesn't
        # reach that far back) -- only the first case is a partial week.
        weeks_out[0]["partial"] = covered_from > weeks_out[0]["week_start"]
        weeks_out[-1]["partial"] = True  # always in progress

    totals = _totals(txns)

    return {
        "covered_from": covered_from,
        "covered_to": covered_to,
        "weeks": weeks_out,
        "totals": totals,
        "spent": totals.get("spending", {}).get("amount", 0),
        "tracked": tracked,
        "triage_counts": _triage_counts(all_txns),
    }
