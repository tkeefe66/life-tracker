"""Protected API routes."""
import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

import ai_metrics
import database as db
import metrics
from app.auth import require_auth
from app.scorecard import _local_today, history, insights, scorecard_for_week, today_snapshot
from services import google_auth

router = APIRouter(dependencies=[Depends(require_auth)])


class CheckinBody(BaseModel):
    type: Literal["gym", "alcohol"]
    date: Optional[str] = None
    level: Optional[int] = Field(default=None, ge=1, le=3)


def _parse_date(value: str) -> datetime.date:
    try:
        d = datetime.date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    if d > _local_today():
        raise HTTPException(status_code=400, detail="date cannot be in the future")
    return d


@router.get("/today")
def get_today(date: Optional[str] = None):
    day = _parse_date(date) if date else None
    return today_snapshot(day)


@router.post("/checkins")
def post_checkin(body: CheckinBody):
    if body.type == "alcohol" and body.level is None:
        raise HTTPException(status_code=400, detail="alcohol check-in requires level 1-3")
    day = (_parse_date(body.date) if body.date else _local_today()).isoformat()
    db.record_checkin(day, body.type, body.level)
    return {"ok": True}


@router.delete("/checkins/{type}")
def delete_checkin(type: Literal["gym", "alcohol"], date: Optional[str] = None):
    day = (_parse_date(date) if date else _local_today()).isoformat()
    db.delete_checkin(day, type)
    return {"ok": True}


@router.get("/scorecard")
def get_scorecard(week_start: Optional[str] = None):
    if week_start:
        try:
            start = datetime.date.fromisoformat(week_start)
        except ValueError:
            raise HTTPException(status_code=400, detail="week_start must be YYYY-MM-DD")
    else:
        start = _local_today()
    return scorecard_for_week(start)


@router.get("/history")
def get_history(weeks: int = 8):
    return history(min(max(weeks, 1), 52))


@router.get("/insights")
def get_insights(weeks: int = 12):
    return insights(min(max(weeks, 1), 52))


@router.get("/reflection")
def get_reflection():
    week_start = metrics.week_bounds(_local_today())[0] - datetime.timedelta(weeks=1)
    ws = week_start.isoformat()
    cached = db.get_reflection(ws)
    if cached:
        return {"week_start": ws, "text": cached}
    card = scorecard_for_week(week_start)
    text = ai_metrics.weekly_reflection(card, insights(12)["noticings"])
    if not text:
        return Response(status_code=204)
    db.save_reflection(ws, text)
    return {"week_start": ws, "text": text}


@router.get("/targets")
def get_targets():
    return db.get_targets()


@router.put("/targets")
def put_targets(body: dict):
    for metric, value in body.items():
        if metric not in metrics.METRICS:
            raise HTTPException(status_code=400, detail=f"unknown metric: {metric}")
        if type(value) is not int or value < 0:
            raise HTTPException(status_code=400, detail=f"invalid value for {metric}")
    for metric, value in body.items():
        db.set_target(metric, value)
    return db.get_targets()


@router.get("/settings")
def get_settings():
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    return {
        "telegram_push": db.get_setting("telegram_push", "off"),
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "google_configured": google_auth.is_configured(),
        "gmail_last_run": db.get_setting("gmail_last_run"),
        "gmail_last_status": db.get_setting("gmail_last_status"),
        "gmail_last_result": db.get_setting("gmail_last_result"),
        "calendar_last_run": db.get_setting("calendar_last_run"),
        "calendar_last_status": db.get_setting("calendar_last_status"),
    }


class SettingsBody(BaseModel):
    telegram_push: Literal["on", "off"]


@router.put("/settings")
def put_settings(body: SettingsBody):
    db.set_setting("telegram_push", body.telegram_push)
    return {"ok": True}


@router.get("/deliveries")
def get_deliveries(days: int = 60):
    d = min(max(days, 1), 365)
    end = _local_today()
    start = end - datetime.timedelta(days=d)
    orders = db.get_delivery_orders_range(start.isoformat(), end.isoformat())
    orders.sort(key=lambda o: o["ordered_at"], reverse=True)
    return {"orders": [
        {"service": o["service"], "subject": o["subject"], "ordered_at": o["ordered_at"]}
        for o in orders
    ]}
