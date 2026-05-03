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
