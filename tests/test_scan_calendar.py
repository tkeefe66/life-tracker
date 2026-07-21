"""scan_calendar job: upsert + classify-once semantics."""


def _events():
    return [
        {"event_id": "ev1", "title": "Dinner w/ Sam", "start_datetime": "2026-07-15T19:00:00-06:00",
         "end_datetime": "2026-07-15T21:00:00-06:00", "description": "", "location": "Bar Dough",
         "attendees": ["Sam"]},
        {"event_id": "ev2", "title": "Dentist", "start_datetime": "2026-07-16T09:00:00-06:00",
         "end_datetime": "2026-07-16T10:00:00-06:00", "description": "", "location": "",
         "attendees": []},
    ]


def test_scan_classifies_new_events(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_calendar

    monkeypatch.setattr(scan_calendar.calendar_service, "get_events_range", lambda days_back: _events())
    monkeypatch.setattr(scan_calendar.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_calendar.ai_metrics, "classify_social_event",
                        lambda title, desc, loc, att: {"is_social": title.startswith("Dinner"), "confidence": 0.9})

    scan_calendar.run()

    social = db.get_social_events_range("2026-07-13", "2026-07-19")
    assert [e["gcal_event_id"] for e in social] == ["ev1"]
    assert db.get_setting("calendar_last_status") == "ok"


def test_scan_does_not_reclassify(temp_db_path, monkeypatch):
    from jobs import scan_calendar

    monkeypatch.setattr(scan_calendar.calendar_service, "get_events_range", lambda days_back: _events())
    monkeypatch.setattr(scan_calendar.google_auth, "is_configured", lambda: True)
    calls = []
    monkeypatch.setattr(scan_calendar.ai_metrics, "classify_social_event",
                        lambda title, desc, loc, att: calls.append(title) or {"is_social": True, "confidence": 0.9})

    scan_calendar.run()
    scan_calendar.run()  # second run: events already classified
    assert len(calls) == 2  # ev1 + ev2, once each


def test_scan_records_error(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_calendar

    def boom(days_back):
        raise RuntimeError("auth expired")
    monkeypatch.setattr(scan_calendar.calendar_service, "get_events_range", boom)
    monkeypatch.setattr(scan_calendar.google_auth, "is_configured", lambda: True)

    scan_calendar.run()  # must not raise
    assert db.get_setting("calendar_last_status").startswith("error:")
