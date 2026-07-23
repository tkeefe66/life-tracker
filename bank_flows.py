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
import re
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


def _hint_matches_field(hint, field):
    """Word-boundary substring match: `hint` must already be stripped + lowercased
    by the caller; this function lowercases `field` (case-insensitive) and matches
    `hint` with no word character immediately before or after it. Prevents a short
    hint like "pay" or "ach" from sweeping up unrelated deposits ("payroll",
    "beach") — see Finding 6 in the Task 4 review."""
    if not hint:
        return False
    pattern = r"(?<!\w)" + re.escape(hint) + r"(?!\w)"
    return re.search(pattern, (field or "").lower()) is not None


def classify_flow(txn, role, partner_role, paired, income_hints):
    """Classify one transaction. Rules apply in order; the first match wins, and
    only the final fallback is a guess.

    `role` is the role of this transaction's own account; `partner_role` is the
    role of the account on the other half of a matched pair, or None if that
    partner's role is unknown (including a partner that exists but has aged out
    of the current classification window). `paired` is the explicit signal for
    "this transaction is one half of a matched pair" — it must NOT be inferred
    from `partner_role is not None`, because a partner can be legitimately
    matched (`pair_id` set) while its row is absent from this window, which
    would otherwise present identically to a genuinely unpaired transaction.
    """
    # 1. Investment — either side. Contributions and the backdoor-Roth conversion
    #    leg are saving, never spending. Investment-to-investment contributes to
    #    nothing on either side.
    if role == "investment" or partner_role == "investment":
        return "investment"

    # 2. Card payment — a matched pair with a credit card on one side. The
    #    purchases are already recorded on the card; counting the payment too
    #    would double-count. Reported separately so paydown reads as progress.
    #    Knowing THIS side is a credit card is enough even if the partner's role
    #    is unknown (partner missing from the window).
    if paired and "credit_card" in (role, partner_role):
        return "card_payment"

    # 3. Transfer — any other matched pair between two known accounts. Also the
    #    landing spot when a matched pair's partner has aged out of the window:
    #    a paired row must never fall through to rules 4/5/6 just because its
    #    partner isn't visible right now — that would invent phantom spending or
    #    (worse) mislabel a payroll-shaped transfer as income.
    if paired:
        return "transfer"

    amount = float(txn["amount"])

    # 4. Income — an unpaired deposit into a spending/bills account whose payee or
    #    description matches a configured payroll signature. Conservative by
    #    design, and reachable ONLY when genuinely unpaired (see rule 3 above).
    if amount > 0 and role in ("spending", "bills"):
        payee = txn.get("payee")
        description = txn.get("description")
        for h in income_hints:
            hint = (h or "").strip().lower()
            if not hint:
                continue
            if _hint_matches_field(hint, payee) or _hint_matches_field(hint, description):
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
                # Sort so the chosen partner (and thus the resulting flow) doesn't
                # depend on input order — matches match_pairs()'s own tie-breaking
                # discipline. Only reachable with 3+ rows sharing a stale pair_id.
                others.sort(key=lambda o: str(o["simplefin_id"]))
                partner_role = roles_by_account_id.get(others[0]["account_id"], "unknown")

        flow = classify_flow(t, role, partner_role, paired=bool(pid), income_hints=income_hints)
        out[sfid] = (flow, pid, is_ambiguous(t, flow))
    return out
