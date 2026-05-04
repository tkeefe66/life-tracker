"""Story clustering — pre-cluster events by date proximity, then call AI per cluster."""
import datetime
import logging
import re

import database
from ai_life_log import cluster_into_story

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
    r"|\b[A-Z]{3}\s*(?:->|→|-)\s*[A-Z]{3}\b"  # IATA code, ASCII or Unicode arrow, or hyphen
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


def run_clustering() -> int:
    """Run a full clustering pass over all currently-pending un-assigned events.

    For every event with status='proposed' AND parent_id IS NULL, assemble
    date-proximity clusters, classify each via Claude, persist parents + assign
    children. Returns the number of parent stories created.

    Re-running is safe: events already attached to a parent (parent_id NOT NULL)
    are excluded; existing parent rows (story_type IS NOT NULL) are also
    excluded — no duplicate parents.
    """
    with database._cursor() as c:
        c.execute(
            "SELECT * FROM life_log_entries "
            "WHERE status='proposed' AND parent_id IS NULL "
            "AND story_type IS NULL "  # exclude existing parents
            "ORDER BY date_start, id"
        )
        rows = database._rows(c.fetchall())
    events = [database._unpack_life_log_entry(r) for r in rows]
    if not events:
        return 0

    # Pre-cluster, then AI-classify each
    clusters = precluster_by_date(events)
    active = [cat["name"] for cat in database.get_active_categories()]
    n_parents = 0

    for cluster in clusters:
        # Hand the AI a normalized view (id, date_start, title, description, location)
        ai_input = [
            {"id": e["id"], "date_start": e["date_start"],
             "title": e.get("description") or "",
             "description": e.get("description") or "",
             "location": e.get("location") or ""}
            for e in cluster
        ]
        candidate = cluster_into_story(ai_input, active_categories=active)
        cluster_ids = {e["id"] for e in cluster}
        candidate = drop_orphan_highlights(candidate, cluster_ids)

        # Persist parent
        date_start = min(e["date_start"] for e in cluster)
        date_end = max(e["date_start"] for e in cluster)
        if date_end == date_start:
            date_end = None
        parent_id = database.save_story_parent(
            date_start=date_start, date_end=date_end,
            story_type=candidate.get("story_type") or "other",
            summary=candidate.get("summary") or "(untitled)",
            highlights=candidate.get("highlights") or [],
            location=candidate.get("location") or None,
            extras={
                "_suggested_extras_questions":
                    candidate.get("suggested_extras_questions") or []
            },
        )
        # Attach children
        for e in cluster:
            database.assign_child_to_story(e["id"], parent_id)
        n_parents += 1

    logger.info("run_clustering: created %d parent stories", n_parents)
    return n_parents
