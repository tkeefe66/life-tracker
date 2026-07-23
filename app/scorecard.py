"""Assemble weekly scorecards from DB counts. All week logic is Monday–Sunday local time."""
import datetime
from datetime import date, timedelta
from typing import Optional

import pytz

import database as db
import metrics
from config import TIMEZONE


def _tz():
    return pytz.timezone(TIMEZONE)


def _local_today() -> date:
    return datetime.datetime.now(_tz()).date()


def _occurred(end_at_iso: str) -> bool:
    try:
        end = datetime.datetime.fromisoformat(end_at_iso)
    except ValueError:
        return False
    if end.tzinfo is None:
        end = _tz().localize(end)
    return end <= datetime.datetime.now(_tz())


def _social_counts(e: dict) -> bool:
    """Manual events are user-asserted facts, not calendar predictions — they count
    immediately, unlike detected gcal events which wait for _occurred(end_at)."""
    return e.get("source") == "manual" or _occurred(e["end_at"])


def counts_for_week(week_start: date) -> dict:
    ws, we = metrics.week_bounds(week_start)
    start, end = ws.isoformat(), we.isoformat()
    checkins = db.get_checkins_range(start, end)
    # Deliberate split: scorecard counts social events by end_at (event has occurred),
    # while Today displays by start_at (see db.get_events_for_day) — do not "unify" these.
    social = [e for e in db.get_social_events_range(start, end) if _social_counts(e)]
    return {
        "gym": sum(1 for c in checkins if c["type"] == "gym"),
        "alcohol": sum(1 for c in checkins if c["type"] == "alcohol"),
        "substances": sum(1 for c in checkins if c["type"] == "substances"),
        "delivery": len(db.get_delivery_orders_range(start, end)),
        "social": len(social),
    }


def scorecard_for_week(week_start: date) -> dict:
    ws, we = metrics.week_bounds(week_start)
    card = metrics.build_scorecard(week_start, counts_for_week(week_start), db.get_targets())
    orders = db.get_delivery_orders_range(ws.isoformat(), we.isoformat())
    card["delivery_spend"] = round(sum(o["amount"] or 0 for o in orders), 2)
    social = [e for e in db.get_social_events_range(ws.isoformat(), we.isoformat()) if _social_counts(e)]
    card["social_spend"] = round(sum(e["amount"] or 0 for e in social), 2)
    return card


def history(weeks: int) -> dict:
    """Completed weeks only, oldest-first, plus streaks."""
    this_monday = metrics.week_bounds(_local_today())[0]
    cards = []
    for i in range(weeks, 0, -1):
        cards.append(scorecard_for_week(this_monday - timedelta(weeks=i)))
    return {"weeks": cards, "streaks": metrics.streaks(cards)}


PATTERN_WEEKS = 8


def _date_lists(start: date, end: date) -> dict:
    """Per-metric ISO day lists for events inside [start, end]."""
    s, e = start.isoformat(), end.isoformat()
    checkins = db.get_checkins_range(s, e)
    social = [ev for ev in db.get_social_events_range(s, e) if _social_counts(ev)]
    return {
        "gym": [c["date"] for c in checkins if c["type"] == "gym"],
        "alcohol": [c["date"] for c in checkins if c["type"] == "alcohol"],
        "substances": [c["date"] for c in checkins if c["type"] == "substances"],
        "delivery": [o["ordered_at"][:10] for o in db.get_delivery_orders_range(s, e)],
        "social": [ev["end_at"][:10] for ev in social],
    }


def insights(weeks: int) -> dict:
    hist = history(weeks)
    series = {k: [w["metrics"][k]["count"] for w in hist["weeks"]] for k in metrics.METRICS}
    this_monday = metrics.week_bounds(_local_today())[0]
    dates = _date_lists(this_monday - timedelta(weeks=PATTERN_WEEKS), this_monday - timedelta(days=1))
    return {
        "weeks": hist["weeks"],
        "streaks": hist["streaks"],
        "weekday_counts": {k: metrics.weekday_counts(v) for k, v in dates.items()},
        "noticings": metrics.noticings(dates, series),
    }


def today_snapshot(day: Optional[date] = None) -> dict:
    d = (day or _local_today()).isoformat()
    checkins = db.get_checkins_range(d, d)
    alcohol = next((c for c in checkins if c["type"] == "alcohol"), None)
    return {
        "date": d,
        "gym": any(c["type"] == "gym" for c in checkins),
        "alcohol_level": alcohol["level"] if alcohol else None,
        "substances": any(c["type"] == "substances" for c in checkins),
        "deliveries": db.get_delivery_orders_range(d, d),
        "social_events": db.get_events_for_day(d),
    }
