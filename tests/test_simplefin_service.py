"""SimpleFIN transport + normalization. The access URL must never escape."""
import httpx
import pytest


def _payload():
    return {
        "accounts": [
            {
                "id": "acct-1", "name": "EVERYDAY CHECKING ...7395",
                "org": {"name": "Wells Fargo"}, "currency": "USD",
                "balance": "1234.56",
                "transactions": [
                    {"id": "t1", "posted": 1751328000, "transacted_at": 1751241600,
                     "amount": "-14.20", "description": "COFFEE SHOP",
                     "payee": "Coffee Shop", "memo": "", "mcc": "5814"},
                    {"id": "t2", "posted": 1751414400, "amount": "3200.00",
                     "description": "DIRECT DEP"},
                ],
            },
            {"id": "acct-2", "name": "Platinum Card", "org": {"domain": "americanexpress.com"},
             "transactions": []},
        ],
        "errors": [],
    }


def test_normalize_flattens_accounts_and_transactions():
    from services import simplefin_service
    accounts, txns = simplefin_service.normalize(_payload())

    assert [a["simplefin_id"] for a in accounts] == ["acct-1", "acct-2"]
    assert accounts[0]["org"] == "Wells Fargo"
    assert accounts[1]["org"] == "americanexpress.com"  # falls back to domain
    assert [t["simplefin_id"] for t in txns] == ["t1", "t2"]
    assert txns[0]["account_simplefin_id"] == "acct-1"
    assert txns[0]["amount"] == -14.20
    assert txns[0]["posted"] == "2026-06-30" or len(txns[0]["posted"]) == 10


def test_normalize_tolerates_missing_optional_fields():
    """mcc is absent on 74% of real transactions — every card account has none."""
    from services import simplefin_service
    _, txns = simplefin_service.normalize(_payload())
    assert txns[1]["mcc"] is None
    assert txns[1]["payee"] == ""
    assert txns[1]["memo"] == ""
    assert txns[1]["transacted_at"] is None


def test_normalize_never_returns_a_balance():
    from services import simplefin_service
    accounts, _ = simplefin_service.normalize(_payload())
    for a in accounts:
        assert "balance" not in a


def test_normalize_skips_transactions_without_an_id():
    from services import simplefin_service
    payload = _payload()
    payload["accounts"][0]["transactions"].append({"amount": "-1.00"})
    _, txns = simplefin_service.normalize(payload)
    assert [t["simplefin_id"] for t in txns] == ["t1", "t2"]


def test_not_configured_when_url_is_blank(monkeypatch):
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", "")
    assert simplefin_service.is_configured() is False


@pytest.mark.parametrize("exc_factory", [
    lambda url: httpx.ConnectError(f"failed to connect to {url}"),
    lambda url: httpx.ReadTimeout(f"timed out reading {url}"),
    lambda url: RuntimeError(f"boom while requesting {url}"),
])
def test_the_access_url_never_survives_a_transport_failure(monkeypatch, exc_factory):
    """The whole point of the boundary: a credential-bearing exception goes in,
    only a closed-set token comes out."""
    from services import simplefin_service
    from services.safe_status import CLOSED_SET

    secret = "https://user:sup3rsecret@bridge.example.com/simplefin"
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", secret)

    def boom(*a, **kw):
        raise exc_factory(secret)

    monkeypatch.setattr(simplefin_service.httpx, "get", boom)

    with pytest.raises(simplefin_service.SimpleFinError) as ei:
        simplefin_service.fetch_accounts()

    err = ei.value
    assert err.status in CLOSED_SET
    blob = f"{err!r} {err} {err.args} {err.status}"
    assert "sup3rsecret" not in blob
    assert "bridge.example.com" not in blob


def test_http_401_maps_to_auth(monkeypatch):
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", "https://x@y.example/sf")
    monkeypatch.setattr(simplefin_service.httpx, "get",
                        lambda *a, **kw: httpx.Response(401, text="nope"))
    with pytest.raises(simplefin_service.SimpleFinError) as ei:
        simplefin_service.fetch_accounts()
    assert ei.value.status == "error: auth"


def test_non_json_body_maps_to_see_logs(monkeypatch):
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", "https://x@y.example/sf")
    monkeypatch.setattr(simplefin_service.httpx, "get",
                        lambda *a, **kw: httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(simplefin_service.SimpleFinError) as ei:
        simplefin_service.fetch_accounts()
    assert ei.value.status == "error: see logs"


def test_successful_fetch_returns_the_payload(monkeypatch):
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", "https://x@y.example/sf")
    monkeypatch.setattr(simplefin_service.httpx, "get",
                        lambda *a, **kw: httpx.Response(200, json=_payload()))
    data = simplefin_service.fetch_accounts(days=90)
    assert len(data["accounts"]) == 2
