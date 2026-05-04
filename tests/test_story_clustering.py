"""Tests for story clustering pre-pass and helpers."""
from services.story_clustering import precluster_by_date


def _ev(id_, date_start, title=""):
    """Build a minimal event dict matching what get_pending_stories returns."""
    return {"id": id_, "date_start": date_start, "description": title,
            "title": title, "source_id": f"evt-{id_}"}


def test_precluster_groups_consecutive_dates():
    events = [
        _ev(1, "2024-03-12"),
        _ev(2, "2024-03-13"),
        _ev(3, "2024-03-14"),
    ]
    clusters = precluster_by_date(events)
    assert len(clusters) == 1
    assert [e["id"] for e in clusters[0]] == [1, 2, 3]


def test_precluster_splits_on_two_day_gap():
    events = [
        _ev(1, "2024-03-12"),
        _ev(2, "2024-03-13"),
        _ev(3, "2024-03-16"),  # gap of 3 calendar days
        _ev(4, "2024-03-17"),
    ]
    clusters = precluster_by_date(events)
    assert len(clusters) == 2
    assert [e["id"] for e in clusters[0]] == [1, 2]
    assert [e["id"] for e in clusters[1]] == [3, 4]


def test_precluster_one_day_gap_keeps_together():
    """Date diff of 1 day between two events still in same cluster (spec §clustering)."""
    events = [
        _ev(1, "2024-03-12"),
        _ev(2, "2024-03-14"),  # diff of 2 → split
    ]
    assert len(precluster_by_date(events)) == 2

    events = [
        _ev(1, "2024-03-12"),
        _ev(2, "2024-03-13"),  # diff of 1 → same group
    ]
    assert len(precluster_by_date(events)) == 1


def test_precluster_handles_same_day():
    events = [_ev(1, "2024-03-12"), _ev(2, "2024-03-12")]
    clusters = precluster_by_date(events)
    assert len(clusters) == 1


def test_precluster_empty_returns_empty():
    assert precluster_by_date([]) == []
