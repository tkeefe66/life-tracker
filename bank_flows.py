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
