"""Tests for Life Log database functions."""
import database as db


def test_categories_seeded(temp_db_path):
    cats = db.get_active_categories()
    names = {c["name"] for c in cats}
    assert "Vacation" in names
    assert "Relationship" in names
    assert "Bachelor Party" in names
    assert "Loss" in names
    assert len(cats) == 16


def test_get_category_usage_count_zero_initially(temp_db_path):
    cats = db.get_active_categories()
    for c in cats:
        assert c["usage_count"] == 0


def test_save_life_log_entry(temp_db_path):
    entry_id = db.save_life_log_entry(
        date_start="2026-05-02",
        date_end=None,
        categories=["Concert"],
        description="Dead & Co at The Sphere",
        location="Las Vegas, NV",
        notes=None,
        status="confirmed",
        source="manual",
        source_id=None,
    )
    assert entry_id > 0
    entry = db.get_life_log_entry(entry_id)
    assert entry["description"] == "Dead & Co at The Sphere"
    assert entry["categories"] == ["Concert"]
    assert entry["status"] == "confirmed"


def test_save_life_log_entry_multi_category(temp_db_path):
    entry_id = db.save_life_log_entry(
        date_start="2025-05-05",
        date_end="2025-05-19",
        categories=["Wedding", "Vacation"],
        description="Spinkel Wedding - London + Spain",
        location="London → Spain",
        notes=None,
        status="confirmed",
        source="manual",
        source_id=None,
    )
    entry = db.get_life_log_entry(entry_id)
    assert set(entry["categories"]) == {"Wedding", "Vacation"}


def test_get_life_log_entries_by_date_range(temp_db_path):
    db.save_life_log_entry(
        date_start="2025-01-15", date_end=None, categories=["Skiing"],
        description="A", location=None, notes=None,
        status="confirmed", source="manual", source_id=None,
    )
    db.save_life_log_entry(
        date_start="2025-06-15", date_end=None, categories=["Skiing"],
        description="B", location=None, notes=None,
        status="confirmed", source="manual", source_id=None,
    )
    db.save_life_log_entry(
        date_start="2024-12-01", date_end=None, categories=["Skiing"],
        description="C", location=None, notes=None,
        status="confirmed", source="manual", source_id=None,
    )
    entries = db.get_life_log_entries_in_range("2025-01-01", "2025-12-31")
    descriptions = [e["description"] for e in entries]
    assert descriptions == ["A", "B"]
