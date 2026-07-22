"""scan_gmail job: rules first, AI only on ambiguous, dedupe by message id."""


def _candidates():
    return [
        {"gmail_message_id": "m1", "sender": "a@uber.com", "subject": "Your order from Pete's",
         "ordered_at": "2026-07-15T19:30:00-06:00"},
        {"gmail_message_id": "m2", "sender": "a@uber.com", "subject": "Your Tuesday trip with Uber",
         "ordered_at": "2026-07-15T08:00:00-06:00"},
        {"gmail_message_id": "m3", "sender": "a@uber.com", "subject": "Update on your recent request",
         "ordered_at": "2026-07-16T12:00:00-06:00"},
    ]


def test_scan_stores_orders_and_uses_ai_for_ambiguous(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", _candidates)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    ai_calls = []
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt",
                        lambda s, subj, snip="": ai_calls.append(subj) or True)

    scan_gmail.run()

    stored = db.get_delivery_orders_range("2026-07-13", "2026-07-19")
    assert {r["gmail_message_id"] for r in stored} == {"m1", "m3"}  # m2 is a ride
    assert ai_calls == ["Update on your recent request"]  # AI only for the ambiguous one
    assert db.get_setting("gmail_last_status") == "ok"


def test_scan_skips_already_seen(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    db.add_delivery_order("m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order from Pete's")
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", _candidates)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    ai_calls = []
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt",
                        lambda s, subj, snip="": ai_calls.append(subj) or False)

    scan_gmail.run()
    assert len(db.get_delivery_orders_range("2026-07-13", "2026-07-19")) == 1  # no dupes


def test_scan_records_error_status(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    def boom():
        raise RuntimeError("token expired")
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", boom)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)

    scan_gmail.run()  # must not raise
    assert db.get_setting("gmail_last_status").startswith("error:")


def test_scan_noop_when_unconfigured(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: False)
    scan_gmail.run()
    assert db.get_setting("gmail_last_status") == "error: Google not configured"


def test_query_uses_lookback_default():
    from services import gmail_service
    assert "newer_than:7d" in gmail_service._query()
    assert "from:(" in gmail_service._query()


def test_scan_writes_last_result(temp_db_path, mock_anthropic, monkeypatch):
    import database as db
    from jobs import scan_gmail

    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [
        {"gmail_message_id": "m1", "sender": "noreply@doordash.com",
         "subject": "Order Confirmation for Tom", "ordered_at": "2026-07-20T18:00:00"},
    ])

    scan_gmail.run()

    assert db.get_setting("gmail_last_status") == "ok"
    result = db.get_setting("gmail_last_result")
    assert result is not None
    assert result.startswith("1 candidates")
    assert "new orders" in result


def test_scan_skips_followup_emails(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    triplet = [
        {"gmail_message_id": "o1", "sender": "noreply@uber.com",
         "subject": "Your Monday evening order with Uber Eats",
         "ordered_at": "2026-07-15T21:01:00-06:00",
         "snippet": "Thanks for ordering, Tom Here's your receipt for Oblio's"},
        {"gmail_message_id": "o2", "sender": "noreply@uber.com",
         "subject": "Your Monday evening order with Uber Eats",
         "ordered_at": "2026-07-15T22:01:00-06:00",
         "snippet": "Tip Thanks for tipping, Tom Here's your receipt for Oblio's"},
        {"gmail_message_id": "o3", "sender": "noreply@uber.com",
         "subject": "Your Monday evening order with Uber Eats",
         "ordered_at": "2026-07-15T22:30:00-06:00",
         "snippet": "Refunded Just a quick update, Tom We adjusted the total"},
    ]
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: triplet)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    ai_calls = []
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt",
                        lambda s, subj, snip="": ai_calls.append(subj) or True)

    scan_gmail.run()
    stored = db.get_delivery_orders_range("2026-07-14", "2026-07-16")
    assert [r["gmail_message_id"] for r in stored] == ["o1"]
    assert ai_calls == []  # follow-ups skipped by rules; o1's subject rule-matches "order"


def test_tip_only_order_creates_row(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    candidates = [
        {"gmail_message_id": "t1", "sender": "noreply@uber.com",
         "subject": "Your Monday evening order with Uber Eats",
         "ordered_at": "2026-07-15T22:01:00-06:00",
         "snippet": "Tip Thanks for tipping, Tom Here's your receipt for Oblio's Total $16.31"},
    ]
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: candidates)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    ai_calls = []
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt",
                        lambda s, subj, snip="": ai_calls.append(subj) or True)

    scan_gmail.run()
    stored = db.get_delivery_orders_range("2026-07-14", "2026-07-16")
    assert [r["gmail_message_id"] for r in stored] == ["t1"]
    assert stored[0]["amount"] == 16.31
    assert ai_calls == []  # tip-only order never goes through AI


def test_order_then_tip_updates_amount_to_tip_total(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    order = {"gmail_message_id": "o1", "sender": "noreply@uber.com",
              "subject": "Your Monday evening order with Uber Eats",
              "ordered_at": "2026-07-15T21:01:00-06:00",
              "snippet": "Thanks for ordering, Tom Here's your receipt for Oblio's Total $40.00"}
    tip = {"gmail_message_id": "t1", "sender": "noreply@uber.com",
           "subject": "Your Monday evening order with Uber Eats",
           "ordered_at": "2026-07-15T22:01:00-06:00",
           "snippet": "Tip Thanks for tipping, Tom Here's your receipt for Oblio's Total $45.00"}
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt", lambda *a, **k: True)

    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [order])
    scan_gmail.run()
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [tip])
    scan_gmail.run()

    stored = db.get_delivery_orders_range("2026-07-14", "2026-07-16")
    assert [r["gmail_message_id"] for r in stored] == ["o1"]  # tip does not create a second row
    assert stored[0]["amount"] == 45.00  # tip total wins over order total


def test_tip_then_order_no_double_count(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    tip = {"gmail_message_id": "t1", "sender": "noreply@uber.com",
           "subject": "Your Monday evening order with Uber Eats",
           "ordered_at": "2026-07-15T22:01:00-06:00",
           "snippet": "Tip Thanks for tipping, Tom Here's your receipt for Oblio's Total $45.00"}
    order = {"gmail_message_id": "o1", "sender": "noreply@uber.com",
              "subject": "Your Monday evening order with Uber Eats",
              "ordered_at": "2026-07-15T21:01:00-06:00",
              "snippet": "Thanks for ordering, Tom Here's your receipt for Oblio's Total $40.00"}
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt", lambda *a, **k: True)

    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [tip])
    scan_gmail.run()
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [order])
    scan_gmail.run()

    stored = db.get_delivery_orders_range("2026-07-14", "2026-07-16")
    assert len(stored) == 1
    assert stored[0]["gmail_message_id"] == "t1"
    assert stored[0]["amount"] == 45.00  # later order receipt is skipped, no overwrite


def test_refund_followup_updates_amount(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    order = {"gmail_message_id": "o1", "sender": "noreply@uber.com",
              "subject": "Your Monday evening order with Uber Eats",
              "ordered_at": "2026-07-15T21:01:00-06:00",
              "snippet": "Thanks for ordering, Tom Here's your receipt for Oblio's Total $40.00"}
    refund = {"gmail_message_id": "r1", "sender": "noreply@uber.com",
              "subject": "Your Monday evening order with Uber Eats",
              "ordered_at": "2026-07-15T22:30:00-06:00",
              "snippet": "Refunded Just a quick update, Tom We adjusted the total Total $32.00"}
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt", lambda *a, **k: True)

    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [order])
    scan_gmail.run()
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [refund])
    scan_gmail.run()

    stored = db.get_delivery_orders_range("2026-07-14", "2026-07-16")
    assert [r["gmail_message_id"] for r in stored] == ["o1"]
    assert stored[0]["amount"] == 32.00


def test_same_day_different_dayparts_two_rows(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    candidates = [
        {"gmail_message_id": "o1", "sender": "noreply@uber.com",
         "subject": "Your Monday morning order with Uber Eats",
         "ordered_at": "2026-07-15T09:01:00-06:00",
         "snippet": "Thanks for ordering, Tom Here's your receipt Total $12.00"},
        {"gmail_message_id": "o2", "sender": "noreply@uber.com",
         "subject": "Your Monday evening order with Uber Eats",
         "ordered_at": "2026-07-15T21:01:00-06:00",
         "snippet": "Thanks for ordering, Tom Here's your receipt Total $40.00"},
    ]
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: candidates)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt", lambda *a, **k: True)

    scan_gmail.run()
    stored = db.get_delivery_orders_range("2026-07-14", "2026-07-16")
    assert {r["gmail_message_id"] for r in stored} == {"o1", "o2"}


def test_fetch_includes_trash_and_snippet(monkeypatch):
    from services import gmail_service

    captured = {}

    class FakeReq:
        def __init__(self, result):
            self._r = result

        def execute(self):
            return self._r

    class FakeMessages:
        def list(self, **kw):
            captured.update(kw)
            return FakeReq({"messages": [{"id": "x1"}]})

        def get(self, **kw):
            return FakeReq({
                "payload": {"headers": [
                    {"name": "From", "value": "noreply@uber.com"},
                    {"name": "Subject", "value": "Your order"},
                ]},
                "internalDate": "1753200000000",
                "snippet": "Thanks for ordering",
            })

    class FakeUsers:
        def messages(self):
            return FakeMessages()

    class FakeService:
        def users(self):
            return FakeUsers()

    monkeypatch.setattr(gmail_service, "_get_service", lambda: FakeService())
    out = gmail_service.fetch_delivery_candidates()
    assert captured["includeSpamTrash"] is True
    assert out[0]["snippet"] == "Thanks for ordering"
