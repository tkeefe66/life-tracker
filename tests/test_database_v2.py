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


def test_delivery_order_roundtrip_with_amount(temp_db_path):
    db = _db(temp_db_path)
    assert db.add_delivery_order(
        "m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order", 16.31
    ) is True
    rows = db.get_delivery_orders_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["amount"] == 16.31 and "id" in rows[0]


def test_delivery_order_amount_defaults_to_none(temp_db_path):
    db = _db(temp_db_path)
    db.add_delivery_order("m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order")
    rows = db.get_delivery_orders_range("2026-07-14", "2026-07-20")
    assert rows[0]["amount"] is None


def test_find_delivery_order_hit_and_miss(temp_db_path):
    db = _db(temp_db_path)
    db.add_delivery_order(
        "m1", "Uber Eats", "2026-07-15T19:30:00-06:00",
        "Your Monday evening order with Uber Eats", 16.31,
    )
    found = db.find_delivery_order("Uber Eats", "2026-07-15", "Your Monday evening order with Uber Eats")
    assert found is not None
    assert found["amount"] == 16.31
    assert "id" in found
    assert db.find_delivery_order("Uber Eats", "2026-07-16", "Your Monday evening order with Uber Eats") is None
    assert db.find_delivery_order("DoorDash", "2026-07-15", "Your Monday evening order with Uber Eats") is None
    assert db.find_delivery_order("Uber Eats", "2026-07-15", "Some other subject") is None


def test_set_delivery_amount_updates(temp_db_path):
    db = _db(temp_db_path)
    db.add_delivery_order("m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order")
    order = db.find_delivery_order("Uber Eats", "2026-07-15", "Your order")
    db.set_delivery_amount(order["id"], 22.5)
    updated = db.find_delivery_order("Uber Eats", "2026-07-15", "Your order")
    assert updated["amount"] == 22.5


def test_delivery_migration_is_idempotent(temp_db_path):
    db = _db(temp_db_path)
    db.add_delivery_order("m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order", 10.0)
    db.initialize_db()
    db.initialize_db()
    rows = db.get_delivery_orders_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["amount"] == 10.0


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


def test_events_for_day_and_range_expose_resolved_is_social(temp_db_path):
    """The editor needs the checkbox's true initial state, not a hardcoded guess."""
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    day_row = db.get_events_for_day("2026-07-15")[0]
    assert day_row["is_social"] is True
    range_row = db.get_social_events_range("2026-07-14", "2026-07-20")[0]
    assert range_row["is_social"] is True
    # User override flips the resolved verdict too.
    db.set_event_overrides("ev1", {"user_is_social": True})
    day_row = db.get_events_for_day("2026-07-15")[0]
    assert day_row["is_social"] is True


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


# ── Social overrides + manual events (calendar_events migration) ────────────

def test_calendar_events_migration_is_idempotent(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    db.initialize_db()
    db.initialize_db()
    rows = db.get_social_events_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["gcal_event_id"] == "ev1"


def test_add_manual_social_event_appears_in_range(temp_db_path):
    db = _db(temp_db_path)
    db.add_manual_social_event(
        "manual:abc", "Trivia night", "2026-07-15T12:00:00", "2026-07-15T13:00:00", amount=20.0
    )
    rows = db.get_social_events_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Trivia night"
    assert row["source"] == "manual"
    assert row["amount"] == 20.0
    assert row["gcal_event_id"] == "manual:abc"


def test_add_manual_social_event_amount_optional(temp_db_path):
    db = _db(temp_db_path)
    db.add_manual_social_event("manual:xyz", "Coffee", "2026-07-15T12:00:00", "2026-07-15T13:00:00")
    rows = db.get_social_events_range("2026-07-14", "2026-07-20")
    assert rows[0]["amount"] is None


def test_get_event_returns_row_or_none(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    row = db.get_event("ev1")
    assert row is not None and row["title"] == "Dinner"
    assert db.get_event("nope") is None


def test_user_rename_survives_calendar_scan_upsert(temp_db_path):
    """Critical correctness point: user_title must survive a scan re-upsert of title."""
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner w/ Sam", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    db.set_event_overrides("ev1", {"user_title": "Sam's birthday dinner"})

    # Nightly scan re-fetches and overwrites `title` from Google.
    db.upsert_calendar_event("ev1", "Dinner w/ Sam (Google edit)", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")

    rows = db.get_social_events_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1
    assert rows[0]["title"] == "Sam's birthday dinner"  # user rename wins

    row = db.get_event("ev1")
    assert row["title"] == "Dinner w/ Sam (Google edit)"  # raw calendar title still updated
    assert row["user_title"] == "Sam's birthday dinner"


def test_user_is_social_false_removes_detected_event(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner w/ Sam", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    assert len(db.get_social_events_range("2026-07-14", "2026-07-20")) == 1
    db.set_event_overrides("ev1", {"user_is_social": False})
    assert db.get_social_events_range("2026-07-14", "2026-07-20") == []


def test_user_is_social_true_adds_non_social_event(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dentist", "2026-07-15T09:00:00-06:00", "2026-07-15T10:00:00-06:00")
    db.set_event_classification("ev1", False, 0.95)
    assert db.get_social_events_range("2026-07-14", "2026-07-20") == []
    db.set_event_overrides("ev1", {"user_is_social": True})
    rows = db.get_social_events_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["gcal_event_id"] == "ev1"


def test_set_event_overrides_amount_and_partial_update(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    db.set_event_overrides("ev1", {"amount": 45.5})
    row = db.get_event("ev1")
    assert row["amount"] == 45.5
    assert row["user_title"] is None  # untouched columns stay untouched
    db.set_event_overrides("ev1", {"user_title": "New name"})
    row = db.get_event("ev1")
    assert row["amount"] == 45.5  # still there — not clobbered by the second partial update
    assert row["user_title"] == "New name"


def test_delete_event(temp_db_path):
    db = _db(temp_db_path)
    db.add_manual_social_event("manual:del", "Cancelled plan", "2026-07-15T12:00:00", "2026-07-15T13:00:00")
    assert db.get_event("manual:del") is not None
    db.delete_event("manual:del")
    assert db.get_event("manual:del") is None


def test_get_classification_examples_only_overridden_newest_first_capped(temp_db_path):
    db = _db(temp_db_path)
    for i in range(3):
        eid = f"ev{i}"
        db.upsert_calendar_event(eid, f"Event {i}", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
        db.set_event_classification(eid, True, 0.9)
    # Only override some — others should never appear as examples.
    db.set_event_overrides("ev0", {"user_is_social": False})
    db.set_event_overrides("ev1", {"user_is_social": True})

    examples = db.get_classification_examples(limit=10)
    titles = [e["title"] for e in examples]
    assert "Event 2" not in titles  # never overridden
    assert examples[0]["title"] == "Event 1"  # newest override first
    assert examples[1]["title"] == "Event 0"
    # SQLite stores booleans as 0/1 — assert truthiness, not Python identity.
    assert bool(examples[0]["user_is_social"]) is True
    assert bool(examples[1]["user_is_social"]) is False


def test_get_classification_examples_respects_limit(temp_db_path):
    db = _db(temp_db_path)
    for i in range(5):
        eid = f"ev{i}"
        db.upsert_calendar_event(eid, f"Event {i}", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
        db.set_event_classification(eid, True, 0.9)
        db.set_event_overrides(eid, {"user_is_social": True})
    examples = db.get_classification_examples(limit=2)
    assert len(examples) == 2


# ── Rides ─────────────────────────────────────────────────────────────────────

def test_ride_add_and_fetch_by_range(temp_db_path):
    db = _db(temp_db_path)
    assert db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip", 14.50) is True
    rows = db.get_rides_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1
    row = rows[0]
    assert row["service"] == "Uber"
    assert row["ride_at"] == "2026-07-15T08:03:00-06:00"
    assert row["subject"] == "Your trip"
    assert row["amount"] == 14.50
    assert row["ai_is_work"] is None
    assert row["user_is_work"] is None
    assert "id" in row


def test_ride_range_excludes_outside_week(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-13T08:00:00-06:00", "2026-07-13T08:00", "s")  # Sunday before
    db.add_ride("r2", "Uber", "2026-07-14T08:00:00-06:00", "2026-07-14T08:00", "s")  # Monday
    assert len(db.get_rides_range("2026-07-14", "2026-07-20")) == 1


def test_has_ride_dedupes_by_message_id(temp_db_path):
    db = _db(temp_db_path)
    assert db.has_ride("r1") is False
    assert db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip") is True
    assert db.has_ride("r1") is True
    # Same message id again refuses to insert (truthy iff actually inserted).
    assert db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip") is False
    assert len(db.get_rides_range("2026-07-14", "2026-07-20")) == 1


def test_find_ride_by_key_hit_and_miss(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip", 14.50)
    found = db.find_ride_by_key("Uber", "2026-07-15T08:03")
    assert found is not None
    assert found["amount"] == 14.50
    assert "id" in found
    assert db.find_ride_by_key("Uber", "2026-07-15T09:00") is None  # different key, miss
    assert db.find_ride_by_key("Lyft", "2026-07-15T08:03") is None  # different service, miss


def test_set_ride_amount_updates(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip")
    ride = db.find_ride_by_key("Uber", "2026-07-15T08:03")
    db.set_ride_amount(ride["id"], 22.5)
    updated = db.find_ride_by_key("Uber", "2026-07-15T08:03")
    assert updated["amount"] == 22.5


def test_set_ride_classification_sets_ai_fields(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip")
    ride = db.find_ride_by_key("Uber", "2026-07-15T08:03")
    db.set_ride_classification(ride["id"], True, 0.85)
    row = db.get_rides_range("2026-07-14", "2026-07-20")[0]
    assert bool(row["ai_is_work"]) is True
    assert row["ai_confidence"] == 0.85
    assert row["user_is_work"] is None  # AI verdict never sets the user override


def test_set_ride_work_override_sets_user_verdict_and_reports_hit(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip")
    ride = db.find_ride_by_key("Uber", "2026-07-15T08:03")
    assert db.set_ride_work_override(ride["id"], True) is True
    row = db.get_rides_range("2026-07-14", "2026-07-20")[0]
    assert bool(row["user_is_work"]) is True
    # 404-style "not found" — updating an unknown id reports no row touched.
    assert db.set_ride_work_override(999999, True) is False


def test_get_ride_examples_only_overridden_newest_first_capped(temp_db_path):
    db = _db(temp_db_path)
    for i in range(3):
        rid = f"r{i}"
        db.add_ride(rid, "Uber", f"2026-07-15T0{i}:00:00-06:00", f"2026-07-15T0{i}:00", f"Trip {i}")
    rides = db.get_rides_range("2026-07-14", "2026-07-20")
    by_subject = {r["subject"]: r["id"] for r in rides}
    # Only override some — Trip 2 must never appear as an example.
    db.set_ride_work_override(by_subject["Trip 0"], False)
    db.set_ride_work_override(by_subject["Trip 1"], True)

    examples = db.get_ride_examples(limit=10)
    subjects = [e["subject"] for e in examples]
    assert "Trip 2" not in subjects
    assert examples[0]["subject"] == "Trip 1"  # newest override first
    assert examples[1]["subject"] == "Trip 0"
    assert bool(examples[0]["user_is_work"]) is True
    assert bool(examples[1]["user_is_work"]) is False


def test_get_ride_examples_normalizes_bool_type(temp_db_path):
    """SQLite stores booleans as 0/1 ints — get_ride_examples must normalize like
    _ride_bool_rows does elsewhere in this layer, not leak raw ints to callers."""
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Trip")
    ride = db.find_ride_by_key("Uber", "2026-07-15T08:03")
    db.set_ride_work_override(ride["id"], True)
    examples = db.get_ride_examples()
    assert examples[0]["user_is_work"] is True  # real bool, not SQLite's 0/1 int


def test_get_ride_examples_respects_limit(temp_db_path):
    db = _db(temp_db_path)
    for i in range(5):
        rid = f"r{i}"
        db.add_ride(rid, "Uber", f"2026-07-15T0{i}:00:00-06:00", f"2026-07-15T0{i}:00", f"Trip {i}")
        ride = db.find_ride_by_key("Uber", f"2026-07-15T0{i}:00")
        db.set_ride_work_override(ride["id"], True)
    examples = db.get_ride_examples(limit=2)
    assert len(examples) == 2


def test_rides_migration_is_idempotent(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip", 14.50)
    db.initialize_db()
    db.initialize_db()
    rows = db.get_rides_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["amount"] == 14.50


# ── Effective date (night cutoff) — rides only ────────────────────────────────

def test_get_rides_range_buckets_late_night_ride_into_previous_day(temp_db_path):
    """A ride at 2026-07-25T02:34 belongs to 2026-07-24 — the SQL bucketing/
    filtering must agree with metrics.effective_date."""
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-25T02:40:00-06:00", "2026-07-25T02:34", "Late ride", 9.0)

    # The 25th's range must NOT include it...
    assert db.get_rides_range("2026-07-25", "2026-07-25") == []
    # ...the 24th's range must.
    rows = db.get_rides_range("2026-07-24", "2026-07-24")
    assert len(rows) == 1
    assert rows[0]["subject"] == "Late ride"


def test_get_rides_range_monday_early_morning_buckets_into_sunday_previous_week(temp_db_path):
    """Net effect on weekly bucketing: a Monday 01:00 ride belongs to Sunday —
    the previous week's range must include it, the Monday-starting week must not."""
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-20T01:10:00-06:00", "2026-07-20T01:00", "Monday early ride", 12.0)

    # Week of 2026-07-20 (Mon) .. 2026-07-26 (Sun) must NOT include it.
    assert db.get_rides_range("2026-07-20", "2026-07-26") == []
    # Week of 2026-07-13 (Mon) .. 2026-07-19 (Sun) must.
    rows = db.get_rides_range("2026-07-13", "2026-07-19")
    assert len(rows) == 1


def test_get_rides_range_exposes_resolved_ride_time(temp_db_path):
    """`ride_time` resolves to the TRUE ride time (parsed ride_key) rather than
    the raw email-arrival ride_at, so the frontend can display it."""
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-25T08:10:00-06:00", "2026-07-25T02:34", "Late ride", 9.0)
    row = db.get_rides_range("2026-07-24", "2026-07-24")[0]
    assert row["ride_time"] == "2026-07-25T02:34"
    assert row["ride_at"] == "2026-07-25T08:10:00-06:00"  # raw column unchanged


def test_get_rides_range_falls_back_to_ride_at_when_ride_key_not_iso(temp_db_path):
    """When ride_key parsing failed, it holds the "{day}|{subject}" fallback
    shape — ride_time must fall back to ride_at rather than exposing that
    non-timestamp string."""
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-25T08:10:00-06:00", "2026-07-25|Your trip", "Your trip", 9.0)
    row = db.get_rides_range("2026-07-25", "2026-07-25")[0]
    assert row["ride_time"] == "2026-07-25T08:10:00-06:00"


# ── Sessions ──────────────────────────────────────────────────────────────────

def test_session_create_get_delete_roundtrip(temp_db_path):
    db = _db(temp_db_path)
    assert db.get_session("tok1") is None
    db.create_session("tok1", "2026-07-01T00:00:00", "2026-07-15T00:00:00")
    row = db.get_session("tok1")
    assert row["created_at"] == "2026-07-01T00:00:00"
    assert row["expires_at"] == "2026-07-15T00:00:00"
    db.delete_session("tok1")
    assert db.get_session("tok1") is None


def test_delete_session_is_idempotent_for_unknown_token(temp_db_path):
    db = _db(temp_db_path)
    db.delete_session("never-existed")  # must not raise


def test_update_session_expiry(temp_db_path):
    db = _db(temp_db_path)
    db.create_session("tok1", "2026-07-01T00:00:00", "2026-07-15T00:00:00")
    db.update_session_expiry("tok1", "2026-07-29T00:00:00")
    assert db.get_session("tok1")["expires_at"] == "2026-07-29T00:00:00"


def test_delete_expired_sessions_only_removes_past_ones(temp_db_path):
    db = _db(temp_db_path)
    db.create_session("expired", "2026-06-01T00:00:00", "2026-06-15T00:00:00")
    db.create_session("still-valid", "2026-07-01T00:00:00", "2026-07-30T00:00:00")
    db.delete_expired_sessions("2026-07-10T00:00:00")
    assert db.get_session("expired") is None
    assert db.get_session("still-valid") is not None
