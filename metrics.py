"""Pure metric computation — no DB, no I/O."""

from datetime import date, timedelta

METRICS = {
    "delivery": {"label": "Delivery orders", "direction": "ceiling", "default_target": 1},
    "gym": {"label": "Gym sessions", "direction": "floor", "default_target": 3},
    "social": {"label": "Social events", "direction": "floor", "default_target": 2},
    "alcohol": {"label": "Alcohol days", "direction": "ceiling", "default_target": 2},
}


def week_bounds(d):
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


def is_hit(direction, count, target):
    return count <= target if direction == "ceiling" else count >= target


def build_scorecard(week_start, counts, targets):
    ws, we = week_bounds(week_start)
    out = {}
    for key, meta in METRICS.items():
        t = targets.get(key, {"direction": meta["direction"], "value": meta["default_target"]})
        count = counts.get(key, 0)
        out[key] = {
            "label": meta["label"],
            "count": count,
            "target": t["value"],
            "direction": t["direction"],
            "hit": is_hit(t["direction"], count, t["value"]),
        }
    return {"week_start": ws.isoformat(), "week_end": we.isoformat(), "metrics": out}


def streaks(history):
    out = {}
    for key in METRICS:
        n = 0
        for card in reversed(history):
            if card["metrics"][key]["hit"]:
                n += 1
            else:
                break
        out[key] = n
    return out


WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def weekday_counts(dates):
    """ISO date strings -> counts per weekday, Monday-first."""
    out = [0] * 7
    for d in dates:
        out[date.fromisoformat(d).weekday()] += 1
    return out


def trend_direction(series):
    """Weekly counts oldest-first. None if fewer than 6 weeks."""
    if len(series) < 6:
        return None
    recent = series[-6:]
    delta = sum(recent[3:]) / 3 - sum(recent[:3]) / 3
    if delta >= 1:
        return "up"
    if delta <= -1:
        return "down"
    return "flat"


def weekday_skew(dates):
    """(weekday_index, share) when one weekday dominates; else None.
    Thresholds: >= 4 total events, max weekday >= 3 events and >= 40% share."""
    counts = weekday_counts(dates)
    total = sum(counts)
    if total < 4:
        return None
    mx = max(counts)
    if mx < 3 or mx / total < 0.4:
        return None
    return counts.index(mx), mx / total


def co_occurrence(dates_a, dates_b):
    """Jaccard overlap of two day sets; None unless both have >= 4 distinct days."""
    a, b = set(dates_a), set(dates_b)
    if len(a) < 4 or len(b) < 4:
        return None
    return len(a & b) / len(a | b)


def noticings(date_lists, series):
    """<= 3 plain-language statements. Priority: co-occurrence, weekday skew, trend."""
    out = []
    j = co_occurrence(date_lists.get("alcohol", []), date_lists.get("delivery", []))
    if j is not None and j >= 0.5:
        out.append("Alcohol days and delivery orders often land on the same day.")
    for key in METRICS:
        skew = weekday_skew(date_lists.get(key, []))
        if skew:
            day, share = skew
            out.append(f"{METRICS[key]['label']} cluster on {WEEKDAY_NAMES[day]}s ({round(share * 100)}% of them).")
    for key in METRICS:
        t = trend_direction(series.get(key, []))
        if t in ("up", "down"):
            out.append(f"{METRICS[key]['label']} trending {t} over the last six weeks.")
    return out[:3]
