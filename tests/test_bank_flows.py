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
