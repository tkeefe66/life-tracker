"""Story clustering — pre-cluster events by date proximity, then call AI per cluster."""
import datetime
import logging

logger = logging.getLogger(__name__)


def _parse_date(s: str) -> datetime.date:
    """Parse ISO date string (YYYY-MM-DD) into a date object."""
    return datetime.date.fromisoformat(s[:10])


def precluster_by_date(events: list, max_gap_days: int = 1) -> list:
    """Group events into clusters by date proximity.

    Two events fall in the same cluster if their `date_start` values differ by
    at most `max_gap_days` calendar days. A larger gap splits the cluster.

    Input events are expected to be dicts with a `date_start` ISO string.
    Output is a list of clusters, each cluster a list of events in date order.

    Args:
        events: List of event dicts, each with a `date_start` ISO string.
        max_gap_days: Maximum allowed gap (in calendar days) before splitting. Defaults to 1.

    Returns:
        List of clusters, where each cluster is a list of events sorted by date_start.
    """
    if not events:
        return []

    sorted_events = sorted(events, key=lambda e: e["date_start"])
    clusters = [[sorted_events[0]]]

    for prev, curr in zip(sorted_events, sorted_events[1:]):
        gap = (_parse_date(curr["date_start"]) - _parse_date(prev["date_start"])).days
        if gap <= max_gap_days:
            clusters[-1].append(curr)
        else:
            clusters.append([curr])

    return clusters
