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
