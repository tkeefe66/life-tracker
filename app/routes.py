"""Protected API routes."""
import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import database as db
import metrics
from app.auth import require_auth
from app.scorecard import _local_today, history, scorecard_for_week, today_snapshot
from services import google_auth

router = APIRouter(dependencies=[Depends(require_auth)])


class CheckinBody(BaseModel):
    type: Literal["gym", "alcohol"]
    date: Optional[str] = None
    level: Optional[int] = Field(default=None, ge=1, le=3)


@router.get("/today")
def get_today():
    return today_snapshot()


@router.post("/checkins")
def post_checkin(body: CheckinBody):
    if body.type == "alcohol" and body.level is None:
        raise HTTPException(status_code=400, detail="alcohol check-in requires level 1-3")
    day = body.date or _local_today().isoformat()
    db.record_checkin(day, body.type, body.level)
    return {"ok": True}


@router.delete("/checkins/{type}")
def delete_checkin(type: Literal["gym", "alcohol"], date: Optional[str] = None):
    db.delete_checkin(date or _local_today().isoformat(), type)
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


@router.get("/targets")
def get_targets():
    return db.get_targets()


@router.put("/targets")
def put_targets(body: dict):
    for metric, value in body.items():
        if metric not in metrics.METRICS:
            raise HTTPException(status_code=400, detail=f"unknown metric: {metric}")
        if not isinstance(value, int) or value < 0:
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
        "calendar_last_run": db.get_setting("calendar_last_run"),
        "calendar_last_status": db.get_setting("calendar_last_status"),
    }


class SettingsBody(BaseModel):
    telegram_push: Literal["on", "off"]


@router.put("/settings")
def put_settings(body: SettingsBody):
    db.set_setting("telegram_push", body.telegram_push)
    return {"ok": True}
