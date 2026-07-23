"""Protected API routes."""
import datetime
from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

import ai_metrics
import database as db
import metrics
from app.auth import COOKIE_NAME, logout as auth_logout, require_auth
from app.scorecard import _local_today, history, insights, scorecard_for_week, spend, today_snapshot, week_days
from services import google_auth

router = APIRouter(dependencies=[Depends(require_auth)])


@router.post("/logout")
def post_logout(request: Request, response: Response):
    auth_logout(request.cookies.get(COOKIE_NAME, ""))
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


class CheckinBody(BaseModel):
    type: Literal["gym", "alcohol", "substances"]
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
def delete_checkin(type: Literal["gym", "alcohol", "substances"], date: Optional[str] = None):
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


@router.get("/week-days")
def get_week_days(week_start: Optional[str] = None):
    if week_start:
        try:
            start = datetime.date.fromisoformat(week_start)
        except ValueError:
            raise HTTPException(status_code=400, detail="week_start must be YYYY-MM-DD")
    else:
        start = _local_today()
    return week_days(start)


@router.get("/history")
def get_history(weeks: int = 8):
    return history(min(max(weeks, 1), 52))


@router.get("/insights")
def get_insights(weeks: int = 12):
    return insights(min(max(weeks, 1), 52))


@router.get("/spend")
def get_spend(weeks: int = 12):
    return spend(min(max(weeks, 1), 52))


@router.post("/reflection")
def get_reflection():
    week_start = metrics.week_bounds(_local_today())[0] - datetime.timedelta(weeks=1)
    ws = week_start.isoformat()
    cached = db.get_reflection(ws)
    if cached:
        return {"week_start": ws, "text": cached}
    card = scorecard_for_week(week_start)
    private_labels = [m["label"] for m in metrics.METRICS.values() if m.get("private")]
    card = {**card, "metrics": {k: v for k, v in card["metrics"].items()
                                if not metrics.METRICS.get(k, {}).get("private")}}
    notes = [n for n in insights(12)["noticings"]
             if not any(lbl in n for lbl in private_labels)]
    text = ai_metrics.weekly_reflection(card, notes)
    if not text:
        return Response(status_code=204)
    db.save_reflection(ws, text)
    return {"week_start": ws, "text": text}


@router.get("/targets")
def get_targets():
    return db.get_targets()


MAX_TARGET_VALUE = 100_000


@router.put("/targets")
def put_targets(body: dict):
    for metric, value in body.items():
        if metric not in metrics.METRICS:
            raise HTTPException(status_code=400, detail=f"unknown metric: {metric}")
        if type(value) is not int or value < 0 or value > MAX_TARGET_VALUE:
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
        "backup_last_run": db.get_setting("backup_last_run"),
        "backup_last_status": db.get_setting("backup_last_status"),
    }


class SettingsBody(BaseModel):
    telegram_push: Literal["on", "off"]


@router.put("/settings")
def put_settings(body: SettingsBody):
    db.set_setting("telegram_push", body.telegram_push)
    return {"ok": True}


class SocialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    date: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0)


class SocialPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    is_social: Optional[bool] = None
    amount: Optional[float] = Field(default=None, ge=0)


@router.post("/social")
def post_social(body: SocialCreate):
    day = (_parse_date(body.date) if body.date else _local_today()).isoformat()
    event_id = "manual:" + uuid4().hex
    start_at, end_at = f"{day}T12:00:00", f"{day}T13:00:00"
    db.add_manual_social_event(event_id, body.name, start_at, end_at, body.amount)
    return {
        "gcal_event_id": event_id, "title": body.name,
        "start_at": start_at, "end_at": end_at,
        "source": "manual", "amount": body.amount,
    }


@router.patch("/social/{event_id}")
def patch_social(event_id: str, body: SocialPatch):
    if db.get_event(event_id) is None:
        raise HTTPException(status_code=404, detail="event not found")
    # Distinguish "field omitted" (leave untouched) from "field explicitly set to
    # null" (clear the override) — pydantic v2's model_fields_set tracks which keys
    # were actually present in the request body, regardless of their value.
    provided = body.model_fields_set
    updates = {}
    if "title" in provided:
        updates["user_title"] = body.title
    if "is_social" in provided:
        updates["user_is_social"] = body.is_social
    if "amount" in provided:
        updates["amount"] = body.amount
    if updates:
        db.set_event_overrides(event_id, updates)
    return {"ok": True}


@router.delete("/social/{event_id}")
def delete_social(event_id: str):
    ev = db.get_event(event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="event not found")
    if ev.get("source") != "manual":
        raise HTTPException(status_code=400, detail="detected events can only be turned off, not deleted")
    db.delete_event(event_id)
    return {"ok": True}


@router.get("/deliveries")
def get_deliveries(days: int = 60):
    d = min(max(days, 1), 365)
    end = _local_today()
    start = end - datetime.timedelta(days=d)
    orders = db.get_delivery_orders_range(start.isoformat(), end.isoformat())
    orders.sort(key=lambda o: o["ordered_at"], reverse=True)
    return {"orders": [
        {"service": o["service"], "subject": o["subject"], "ordered_at": o["ordered_at"], "amount": o["amount"]}
        for o in orders
    ]}


class RidePatch(BaseModel):
    is_work: bool


@router.get("/rides")
def get_rides(days: int = 60):
    d = min(max(days, 1), 365)
    end = _local_today()
    start = end - datetime.timedelta(days=d)
    rides = db.get_rides_range(start.isoformat(), end.isoformat())
    rides.sort(key=lambda r: r["ride_at"], reverse=True)
    return {"rides": [
        {
            "id": r["id"], "service": r["service"], "ride_at": r["ride_at"],
            "subject": r["subject"], "amount": r["amount"],
            "ai_is_work": r["ai_is_work"], "user_is_work": r["user_is_work"],
            "is_work": bool(r["user_is_work"]),
        }
        for r in rides
    ]}


@router.patch("/rides/{ride_id}")
def patch_ride(ride_id: int, body: RidePatch):
    if not db.set_ride_work_override(ride_id, body.is_work):
        raise HTTPException(status_code=404, detail="ride not found")
    return {"ok": True}
