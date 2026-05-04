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


from services.story_clustering import is_flight, drop_orphan_highlights


def test_is_flight_matches_common_patterns():
    assert is_flight("JFK → BTV flight")
    assert is_flight("Flight to Boston")
    assert is_flight("AA 1234 BOS-LAX")
    assert is_flight("United UA456 to SFO")


def test_is_flight_rejects_non_flights():
    assert not is_flight("Skiing at Killington")
    assert not is_flight("Dinner with Sarah")
    assert not is_flight("Onboarding meeting")


def test_drop_orphan_highlights_removes_unreferenced():
    cluster_event_ids = {1, 2, 3}
    candidate = {
        "highlights": ["A", "B", "C"],
        "event_id_refs": [1, 999, 2],  # 999 is orphan
    }
    out = drop_orphan_highlights(candidate, cluster_event_ids)
    assert out["highlights"] == ["A", "C"]
    assert out["event_id_refs"] == [1, 2]


def test_drop_orphan_highlights_handles_missing_refs():
    """If event_id_refs is shorter/longer than highlights, align by index."""
    cluster_event_ids = {1, 2}
    candidate = {
        "highlights": ["A", "B", "C"],
        "event_id_refs": [1, 2],  # only 2 refs for 3 highlights
    }
    out = drop_orphan_highlights(candidate, cluster_event_ids)
    # Highlight without a ref is dropped (we can't validate it).
    assert out["highlights"] == ["A", "B"]
    assert out["event_id_refs"] == [1, 2]
