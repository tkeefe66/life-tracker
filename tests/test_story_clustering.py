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


def test_is_flight_matches_ascii_arrow_iata_pattern():
    """Calendar titles often use ASCII arrows like 'JFK->BTV' (no 'flight' keyword)."""
    # ASCII arrow with no spaces
    assert is_flight("JFK->BTV")
    # ASCII arrow with spaces
    assert is_flight("JFK -> BTV")
    # Unicode arrow without 'flight' keyword
    assert is_flight("JFK→BTV")
    # Plain hyphen (e.g., "BOS-LAX trip")
    assert is_flight("BOS-LAX")


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


import json
from unittest.mock import MagicMock


def test_cluster_into_story_returns_expected_shape(mock_anthropic):
    from ai_life_log import cluster_into_story

    payload = {
        "story_type": "trip",
        "summary": "Vermont ski trip with Sarah and Tom",
        "highlights": ["JFK→BTV flight", "Skied Killington"],
        "event_id_refs": [1, 2],
        "suggested_extras_questions": [
            "What was the mode of travel?",
            "Who came on the trip?",
        ],
        "location": "Killington, VT",
    }
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text=json.dumps(payload))
    ]

    events = [
        {"id": 1, "date_start": "2024-03-12", "title": "JFK->BTV flight",
         "description": "", "location": "", "source_id": "e1"},
        {"id": 2, "date_start": "2024-03-13", "title": "Skiing Killington",
         "description": "", "location": "Killington, VT", "source_id": "e2"},
    ]
    out = cluster_into_story(events, active_categories=["Vacation", "Skiing"])
    assert out["story_type"] == "trip"
    assert out["summary"].startswith("Vermont")
    assert out["event_id_refs"] == [1, 2]


def test_cluster_into_story_falls_back_to_singletons_on_parse_failure(mock_anthropic):
    from ai_life_log import cluster_into_story

    mock_anthropic.messages.create.return_value.content = [MagicMock(text="not json")]

    events = [
        {"id": 1, "date_start": "2024-03-12", "title": "Random event",
         "description": "", "location": "", "source_id": "e1"},
    ]
    out = cluster_into_story(events, active_categories=[])
    # Fall-through default: story_type="other"
    assert out["story_type"] == "other"
    assert out["event_id_refs"] == [1]


def test_run_clustering_pipeline_writes_parents_and_assigns_children(
    temp_db_path, mock_anthropic
):
    import database
    from services.story_clustering import run_clustering

    # Two events 1 day apart -> same cluster; one event a month later -> separate cluster
    e1 = database.save_proposal(
        date_start="2024-03-12", date_end=None,
        categories=["Vacation"], description="Vermont arrival",
        location="VT", source="calendar", source_id="evt-1",
    )
    e2 = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Skiing Killington",
        location="VT", source="calendar", source_id="evt-2",
    )
    e3 = database.save_proposal(
        date_start="2024-04-15", date_end=None,
        categories=["Concert"], description="Phish at MSG",
        location="NYC", source="calendar", source_id="evt-3",
    )

    # Configure mock_anthropic to return a different shape for each call
    responses = [
        # Cluster 1 (e1 + e2)
        json.dumps({
            "story_type": "trip", "summary": "Vermont weekend",
            "highlights": ["Vermont arrival", "Skied Killington"],
            "event_id_refs": [e1, e2],
            "suggested_extras_questions": ["mode of travel?"],
            "location": "VT",
        }),
        # Cluster 2 (e3)
        json.dumps({
            "story_type": "other", "summary": "Phish at MSG",
            "highlights": ["Phish at MSG"], "event_id_refs": [e3],
            "suggested_extras_questions": [], "location": "NYC",
        }),
    ]
    iter_responses = iter(responses)
    def _next(*a, **kw):
        m = MagicMock()
        m.content = [MagicMock(text=next(iter_responses))]
        return m
    mock_anthropic.messages.create.side_effect = _next

    n_stories = run_clustering()
    assert n_stories == 2

    stories = database.get_pending_stories_with_children()
    assert len(stories) == 2
    by_type = {s["story_type"]: s for s in stories}
    assert "trip" in by_type and "other" in by_type
    assert {c["id"] for c in by_type["trip"]["children"]} == {e1, e2}
    assert {c["id"] for c in by_type["other"]["children"]} == {e3}
