"""Story clustering — pre-cluster events by date proximity, then call AI per cluster."""
import datetime
import logging
import re

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


_FLIGHT_RE = re.compile(
    r"\b(flight|fly|flying|flew|airline)\b"
    r"|\b[A-Z]{3}\s*[-→]\s*[A-Z]{3}\b"      # IATA code dash IATA code
    r"|\b(?:AA|UA|DL|BA|AC|JB|WN|NK|F9)\s*\d{1,4}\b",  # carrier code + flight num
    re.IGNORECASE,
)


def is_flight(title: str) -> bool:
    """True if the event title looks like a flight."""
    return bool(title and _FLIGHT_RE.search(title))


def drop_orphan_highlights(candidate: dict, cluster_event_ids: set) -> dict:
    """Drop highlights whose event_id_refs aren't in the cluster.

    `candidate` is the AI's output for one cluster:
      {"highlights": [...], "event_id_refs": [...]}
    Highlights and refs are aligned by index; if there are more highlights than
    refs, the extras are dropped (we cannot validate them).
    """
    highlights = candidate.get("highlights") or []
    refs = candidate.get("event_id_refs") or []
    pairs = list(zip(highlights, refs))  # truncates to shorter
    kept = [(h, r) for h, r in pairs if r in cluster_event_ids]
    if len(kept) < len(pairs):
        logger.warning(
            "Dropped %d orphan highlight(s) referencing events not in cluster",
            len(pairs) - len(kept),
        )
    out = dict(candidate)
    out["highlights"] = [h for h, _ in kept]
    out["event_id_refs"] = [r for _, r in kept]
    return out
