"""
Google Calendar service — fetches events using OAuth2 refresh token.
Credentials are stored as Railway env vars (no local files needed in production).
"""

import datetime
import logging

import pytz
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from config import (
    GOOGLE_CALENDAR_CLIENT_ID,
    GOOGLE_CALENDAR_CLIENT_SECRET,
    GOOGLE_CALENDAR_REFRESH_TOKEN,
    GOOGLE_CALENDAR_ID,
    TIMEZONE,
)

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

TOKEN_URI = "https://oauth2.googleapis.com/token"


def _get_service():
    if not all([GOOGLE_CALENDAR_CLIENT_ID, GOOGLE_CALENDAR_CLIENT_SECRET, GOOGLE_CALENDAR_REFRESH_TOKEN]):
        raise RuntimeError("Google Calendar credentials not configured. Set GOOGLE_CALENDAR_CLIENT_ID, "
                           "GOOGLE_CALENDAR_CLIENT_SECRET, and GOOGLE_CALENDAR_REFRESH_TOKEN.")

    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_CALENDAR_REFRESH_TOKEN,
        token_uri=TOKEN_URI,
        client_id=GOOGLE_CALENDAR_CLIENT_ID,
        client_secret=GOOGLE_CALENDAR_CLIENT_SECRET,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def get_events_rolling_window(days: int = 2) -> list:
    """
    Fetch calendar events in a rolling window: now → now + days.
    Returns a list of dicts with keys:
        event_id, title, start_datetime, end_datetime, description, location, is_recurring
    """
    tz = pytz.timezone(TIMEZONE)
    now = datetime.datetime.now(tz)
    time_min = now.isoformat()
    time_max = (now + datetime.timedelta(days=days)).isoformat()

    service = _get_service()
    result = service.events().list(
        calendarId=GOOGLE_CALENDAR_ID,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute()

    events = []
    for item in result.get("items", []):
        if item.get("status") == "cancelled":
            continue

        start = item.get("start", {})
        end = item.get("end", {})
        start_dt = start.get("dateTime") or start.get("date", "")
        end_dt = end.get("dateTime") or end.get("date", "")

        events.append({
            "event_id": item["id"],
            "title": item.get("summary", "(No title)"),
            "start_datetime": start_dt,
            "end_datetime": end_dt,
            "description": item.get("description", ""),
            "location": item.get("location", ""),
            "is_recurring": bool(item.get("recurringEventId")),
        })

    logger.info("Fetched %d events from calendar (%d-day window)", len(events), days)
    return events


def is_configured() -> bool:
    return all([GOOGLE_CALENDAR_CLIENT_ID, GOOGLE_CALENDAR_CLIENT_SECRET, GOOGLE_CALENDAR_REFRESH_TOKEN])
