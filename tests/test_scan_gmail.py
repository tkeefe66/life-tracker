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
                        lambda s, subj: ai_calls.append(subj) or True)

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
                        lambda s, subj: ai_calls.append(subj) or False)

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
