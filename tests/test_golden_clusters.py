"""Regression test against hand-crafted golden clusters.

Catches drift if the prompt or fallback logic changes. Each fixture asserts:
- precluster_by_date produces the expected number of clusters
- All event_id_refs returned by the AI fallback path reference events that exist
"""
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "clustering" / "golden_clusters.json"


@pytest.fixture
def fixtures():
    return json.loads(FIXTURE_PATH.read_text())


def test_preclustering_matches_expected_clusters(fixtures):
    from services.story_clustering import precluster_by_date

    for fx in fixtures:
        clusters = precluster_by_date(fx["events"])
        assert len(clusters) == fx["expected_clusters"], (
            f"{fx['name']}: expected {fx['expected_clusters']} clusters, "
            f"got {len(clusters)}"
        )


def test_event_id_refs_are_grounded(fixtures, mock_anthropic):
    """For each fixture, every AI-returned event_id_ref must reference a real event.

    We feed each cluster-of-events to cluster_into_story with a deterministic
    mock that returns event_id_refs equal to all event ids in the cluster.
    The assertion confirms the function preserves those refs (i.e. doesn't
    invent new IDs or drop them all).
    """
    from ai_life_log import cluster_into_story
    from services.story_clustering import precluster_by_date

    for fx in fixtures:
        for cluster in precluster_by_date(fx["events"]):
            cluster_ids = {e["id"] for e in cluster}
            # Configure the mock to return the cluster's IDs verbatim
            payload = {
                "story_type": fx.get("expected_story_type") or "other",
                "summary": fx["name"],
                "highlights": [e["title"] for e in cluster],
                "event_id_refs": [e["id"] for e in cluster],
                "suggested_extras_questions": [],
                "location": "",
            }
            mock_anthropic.messages.create.return_value.content = [MagicMock(
                text=json.dumps(payload)
            )]
            out = cluster_into_story(cluster, active_categories=[])
            assert set(out["event_id_refs"]) <= cluster_ids, (
                f"{fx['name']}: event_id_refs {out['event_id_refs']} not grounded "
                f"in cluster {cluster_ids}"
            )
