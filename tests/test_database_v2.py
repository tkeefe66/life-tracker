"""v2 table helpers: checkins, delivery orders, calendar events, targets, settings."""


def _db(temp_db_path):
    import database
    return database


def test_checkin_upsert_and_range(temp_db_path):
    db = _db(temp_db_path)
    db.record_checkin("2026-07-14", "gym")
    db.record_checkin("2026-07-14", "alcohol", level=2)
    db.record_checkin("2026-07-14", "alcohol", level=3)  # upsert, not duplicate
    rows = db.get_checkins_range("2026-07-14", "2026-07-20")
    assert len(rows) == 2
    alcohol = next(r for r in rows if r["type"] == "alcohol")
    assert alcohol["level"] == 3


def test_delete_checkin(temp_db_path):
    db = _db(temp_db_path)
    db.record_checkin("2026-07-14", "gym")
    db.delete_checkin("2026-07-14", "gym")
    assert db.get_checkins_range("2026-07-14", "2026-07-14") == []


def test_delivery_order_dedupe(temp_db_path):
    db = _db(temp_db_path)
    assert db.add_delivery_order("msg1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order") is True
    assert db.add_delivery_order("msg1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order") is False
    assert db.has_delivery_order("msg1") is True
    assert db.has_delivery_order("msg2") is False
    rows = db.get_delivery_orders_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["service"] == "Uber Eats"


def test_delivery_range_excludes_outside_week(temp_db_path):
    db = _db(temp_db_path)
    db.add_delivery_order("m1", "DoorDash", "2026-07-13T12:00:00-06:00", "s")  # Sunday before
    db.add_delivery_order("m2", "DoorDash", "2026-07-14T12:00:00-06:00", "s")  # Monday
    assert len(db.get_delivery_orders_range("2026-07-14", "2026-07-20")) == 1


def test_calendar_event_classification_flow(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner w/ Sam", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    assert db.event_needs_classification("ev1") is True
    db.set_event_classification("ev1", True, 0.9)
    assert db.event_needs_classification("ev1") is False
    # upsert again (calendar re-fetch) must NOT wipe classification
    db.upsert_calendar_event("ev1", "Dinner w/ Sam (edited)", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    assert db.event_needs_classification("ev1") is False
    rows = db.get_social_events_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["title"] == "Dinner w/ Sam (edited)"


def test_social_range_excludes_non_social(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dentist", "2026-07-15T09:00:00-06:00", "2026-07-15T10:00:00-06:00")
    db.set_event_classification("ev1", False, 0.95)
    assert db.get_social_events_range("2026-07-14", "2026-07-20") == []


def test_events_for_day(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    assert len(db.get_events_for_day("2026-07-15")) == 1
    assert db.get_events_for_day("2026-07-16") == []


def test_targets_seed_and_update(temp_db_path):
    db = _db(temp_db_path)
    db.seed_default_targets()
    t = db.get_targets()
    assert t["delivery"] == {"direction": "ceiling", "value": 1}
    assert t["gym"] == {"direction": "floor", "value": 3}
    db.set_target("gym", 4)
    db.seed_default_targets()  # idempotent — must not reset user value
    assert db.get_targets()["gym"]["value"] == 4


def test_settings_roundtrip(temp_db_path):
    db = _db(temp_db_path)
    assert db.get_setting("telegram_push", "off") == "off"
    db.set_setting("telegram_push", "on")
    db.set_setting("telegram_push", "on")  # upsert
    assert db.get_setting("telegram_push") == "on"


def test_reflection_roundtrip(temp_db_path):
    import database as db
    assert db.get_reflection("2026-07-13") is None
    db.save_reflection("2026-07-13", "A solid week.")
    assert db.get_reflection("2026-07-13") == "A solid week."
    db.save_reflection("2026-07-13", "Revised.")
    assert db.get_reflection("2026-07-13") == "Revised."
