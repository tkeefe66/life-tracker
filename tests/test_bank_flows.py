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
