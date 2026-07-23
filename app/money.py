"""Assemble the Money screen's bank-side aggregate. DB -> domain wiring, the same
role app/scorecard.py plays for the scorecard. No SQL here — all access goes
through database.py. Bank data is not a metric: no targets, no hit/miss, anywhere
in this module."""
from datetime import timedelta

import bank_flows
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

MIN_TRIAGE_LIMIT = 1
MAX_TRIAGE_LIMIT = 200


def _clamp_weeks(weeks: int) -> int:
    return max(MIN_WEEKS, min(MAX_WEEKS, weeks))


def _clamp_triage_limit(limit: int) -> int:
    return max(MIN_TRIAGE_LIMIT, min(MAX_TRIAGE_LIMIT, limit))


def _flow_amount(flow: str, rows: list) -> float:
    if flow in MOVEMENT_FLOWS:
        return sum(abs(t["amount"]) for t in rows if t["amount"] < 0)
    if flow in ("income", "refund"):
        return sum(t["amount"] for t in rows if t["amount"] > 0)
    if flow == "spending":
        return sum(abs(t["amount"]) for t in rows if t["amount"] < 0)
    return sum(abs(t["amount"]) for t in rows)


def _totals(txns: list) -> dict:
    """Group by resolved_flow and aggregate. Round once, at the end — summing
    already-rounded per-row amounts can drift a cent from the true total, the
    same double-rounding trap app.scorecard.spend()'s by_service comment warns
    about. A flow with no rows in the window is simply absent, not a zero entry.

    `refund` and `spending` are both special cases: count, like amount, only
    reflects the sign-filtered rows that actually contribute (refund's
    positive side, spending's negative side -- the mirror of the movement
    flows' outflow-side rule). A `refund` verdict on a negative-amount row
    (a mis-tap), or a `spending` verdict on a positive-amount row, is inert —
    it still keeps the flow's key present (there IS a row), just at count 0 /
    amount 0.0, never contributing to the total."""
    grouped: dict = {}
    for t in txns:
        grouped.setdefault(t["resolved_flow"], []).append(t)
    out = {}
    for flow, rows in grouped.items():
        if flow == "refund":
            count = sum(1 for t in rows if t["amount"] > 0)
        elif flow == "spending":
            count = sum(1 for t in rows if t["amount"] < 0)
        else:
            count = len(rows)
        out[flow] = {"count": count, "amount": round(_flow_amount(flow, rows), 2)}
    return out


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
            if t["resolved_flow"] == "spending" and t["amount"] < 0
            and ws_iso <= t["posted"][:10] <= we_iso
        )
        # Refunds net within their OWN posted week, not the spending week's --
        # a week can go negative when refunds exceed spending, and that's the
        # true figure; flooring it for the chart is presentation, not here.
        week_refund = sum(
            t["amount"] for t in txns
            if t["resolved_flow"] == "refund" and t["amount"] > 0
            and ws_iso <= t["posted"][:10] <= we_iso
        )
        week_net = round(week_spending - week_refund, 2)
        weeks_out.append({"week_start": ws_iso, "spending": week_net, "partial": False})

    if weeks_out:
        # covered_from can only fall strictly after the first surviving week's
        # Monday (coverage began mid-week) or on/before it (coverage began
        # exactly that Monday, or earlier still and this window simply doesn't
        # reach that far back) -- only the first case is a partial week.
        weeks_out[0]["partial"] = covered_from > weeks_out[0]["week_start"]
        weeks_out[-1]["partial"] = True  # always in progress

    totals = _totals(txns)
    # Refunds net out of spend: spending_total - refund_total, rounded once at
    # the end. This must be computed from the RAW (unrounded) per-row sums,
    # not from totals[...]["amount"] -- those are already rounded to 2dp, and
    # subtracting two already-rounded values then rounding again is a second
    # rounding pass, not a no-op cleanup: it can diverge from a true
    # round-once result (e.g. spending -10.007 + refund +0.003 double-rounds
    # to 10.01, while round-once of the raw difference is 10.0). Mirrors the
    # weekly loop above, which rounds each week's raw sums once.
    raw_spending = sum(abs(t["amount"]) for t in txns if t["resolved_flow"] == "spending" and t["amount"] < 0)
    raw_refund = sum(t["amount"] for t in txns if t["resolved_flow"] == "refund" and t["amount"] > 0)
    spent = round(raw_spending - raw_refund, 2)

    return {
        "covered_from": covered_from,
        "covered_to": covered_to,
        "weeks": weeks_out,
        "totals": totals,
        "spent": spent,
        "tracked": tracked,
        "triage_counts": _triage_counts(all_txns),
    }


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


def _decorate_bucket(rows: list) -> list:
    """Attach `label` and the signature-grouping fields to one bucket's rows.

    Grouping is computed from THIS bucket's rows alone, never pooled across
    buckets: db.get_bank_triage's `ambiguous` and `inflow_unknown` buckets
    answer different questions (spent-it-or-moved-it vs where-did-this-come-
    from), so a Venmo row in one must never count toward a bulk offer in the
    other. `signature_count`/`signature_amount` exclude the row itself and are
    computed straight from the rows already in hand -- no extra DB query. By
    construction db.get_bank_triage and db.get_bank_recently_sorted each only
    return unanswered-in-this-bucket rows for that bucket (the ambiguous and
    inflow_unknown queries both filter on user_flow, and recently-sorted rows
    are decorated separately from the queues), so a row that has already been
    overridden and dropped out of a bucket can never inflate another row's
    offer in that same bucket.
    """
    signatures = [bank_flows.triage_signature(t) for t in rows]
    out = []
    for i, t in enumerate(rows):
        sig = signatures[i]
        others = [rows[j] for j in range(len(rows)) if j != i and signatures[j] == sig] if sig else []
        out.append({
            "simplefin_id": t["simplefin_id"],
            "posted": t["posted"],
            "amount": t["amount"],
            "payee": t["payee"],
            "description": t["description"],
            "label": t["payee"] or t["description"],
            "account_name": t["account_name"],
            "resolved_flow": t["resolved_flow"],
            "user_flow": t["user_flow"],
            "user_note": t["user_note"],
            "signature": sig,
            "signature_count": len(others),
            "signature_amount": round(sum(abs(o["amount"]) for o in others), 2),
        })
    return out


def triage(limit: int) -> dict:
    """Assemble the triage worklist: the two un-triaged queues (`ambiguous`,
    `inflow_unknown`) plus the `recent` undo list, each capped at `limit`
    (clamped 1-200). Each bucket's signature grouping is independent -- see
    _decorate_bucket."""
    limit = _clamp_triage_limit(limit)
    queue = db.get_bank_triage(limit)
    recent = db.get_bank_recently_sorted(limit)
    return {
        "ambiguous": _decorate_bucket(queue["ambiguous"]),
        "inflow_unknown": _decorate_bucket(queue["inflow_unknown"]),
        "recent": _decorate_bucket(recent),
    }
