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


def counts_for_week(week_start: date) -> dict:
    ws, we = metrics.week_bounds(week_start)
    start, end = ws.isoformat(), we.isoformat()
    checkins = db.get_checkins_range(start, end)
    # Deliberate split: scorecard counts social events by end_at (event has occurred),
    # while Today displays by start_at (see db.get_events_for_day) — do not "unify" these.
    social = [e for e in db.get_social_events_range(start, end) if _occurred(e["end_at"])]
    return {
        "gym": sum(1 for c in checkins if c["type"] == "gym"),
        "alcohol": sum(1 for c in checkins if c["type"] == "alcohol"),
        "delivery": len(db.get_delivery_orders_range(start, end)),
        "social": len(social),
    }


def scorecard_for_week(week_start: date) -> dict:
    return metrics.build_scorecard(week_start, counts_for_week(week_start), db.get_targets())


def history(weeks: int) -> dict:
    """Completed weeks only, oldest-first, plus streaks."""
    this_monday = metrics.week_bounds(_local_today())[0]
    cards = []
    for i in range(weeks, 0, -1):
        cards.append(scorecard_for_week(this_monday - timedelta(weeks=i)))
    return {"weeks": cards, "streaks": metrics.streaks(cards)}


def today_snapshot(day: Optional[date] = None) -> dict:
    d = (day or _local_today()).isoformat()
    checkins = db.get_checkins_range(d, d)
    alcohol = next((c for c in checkins if c["type"] == "alcohol"), None)
    return {
        "date": d,
        "gym": any(c["type"] == "gym" for c in checkins),
        "alcohol_level": alcohol["level"] if alcohol else None,
        "deliveries": db.get_delivery_orders_range(d, d),
        "social_events": db.get_events_for_day(d),
    }
