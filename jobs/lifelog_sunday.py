"""
lifelog_sunday — runs Sunday at 5pm.

Reviews the past 7 days of calendar events. For any events not yet promoted,
classifies them. Sends a single digest message summarizing all "maybe"
candidates, with quick confirm/skip references.
"""
import datetime
import logging

import pytz
from telegram import Bot

import database as db
from ai_life_log import propose_from_calendar_event
from config import TELEGRAM_CHAT_ID, TIMEZONE
from services.calendar_service import GOOGLE_CALENDAR_ID, _get_service, is_configured

logger = logging.getLogger(__name__)


def _fetch_past_week_events() -> list:
    """Fetch calendar events from the past 7 days. Override in tests."""
    if not is_configured():
        return []
    tz = pytz.timezone(TIMEZONE)
    now = datetime.datetime.now(tz)
    time_max = now.isoformat()
    time_min = (now - datetime.timedelta(days=7)).isoformat()

    service = _get_service()
    result = service.events().list(
        calendarId=GOOGLE_CALENDAR_ID,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=250,
    ).execute()

    events = []
    for item in result.get("items", []):
        if item.get("status") == "cancelled":
            continue
        start = item.get("start", {})
        end = item.get("end", {})
        attendees_raw = item.get("attendees", []) or []
        attendees = [
            (a.get("displayName") or a.get("email", "").split("@")[0])
            for a in attendees_raw
            if not a.get("self")
        ]
        events.append({
            "event_id": item["id"],
            "title": item.get("summary", "(No title)"),
            "start_datetime": start.get("dateTime") or start.get("date", ""),
            "end_datetime": end.get("dateTime") or end.get("date", ""),
            "description": item.get("description", ""),
            "location": item.get("location", ""),
            "is_recurring": bool(item.get("recurringEventId")),
            "attendees": attendees,
        })
    return events


async def run_sunday_digest(bot: Bot):
    """Classify past-week events and send a single digest for maybe-confidence ones."""
    events = _fetch_past_week_events()
    active_cats = [c["name"] for c in db.get_active_categories()]

    proposals_made = []
    for event in events:
        # Already promoted to a life log entry? skip
        seen = db.get_activity_by_source_id("calendar", event["event_id"])
        if seen and seen[0].get("promoted_to_life_log"):
            continue

        # Record activity if not already seen
        if not seen:
            db.record_activity(
                source="calendar",
                source_id=event["event_id"],
                event_type="calendar_event",
                occurred_at=event["start_datetime"] or None,
                payload=event,
            )

        parsed = propose_from_calendar_event(
            title=event["title"],
            start=event["start_datetime"],
            end=event["end_datetime"],
            attendees=event.get("attendees", []),
            description=event.get("description", ""),
            location=event.get("location", ""),
            active_categories=active_cats,
        )

        if parsed.get("confidence") != "maybe":
            continue

        date_start = event["start_datetime"][:10]
        entry_id = db.save_proposal(
            date_start=date_start,
            date_end=None,
            categories=parsed["categories"],
            description=parsed["description"] or event["title"],
            location=parsed.get("location") or event.get("location"),
            source="calendar",
            source_id=event["event_id"],
        )
        proposals_made.append((entry_id, parsed, event))

    if not proposals_made:
        logger.info("lifelog_sunday: no maybes to digest")
        return

    lines = ["📋 *Sunday digest — possibly Life Log–worthy this week:*", ""]
    for entry_id, parsed, event in proposals_made:
        cats = " + ".join(parsed["categories"]) or "?"
        date = event["start_datetime"][:10]
        desc = parsed.get("description") or event["title"]
        lines.append(f"*#{entry_id}* — {desc}  ({cats}, {date})")
    lines.append("")
    lines.append("Reply *yes #N* / *skip #N* per item, or *yes all* / *skip all*.")

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text="\n".join(lines),
        parse_mode="Markdown",
    )
    logger.info("lifelog_sunday: sent digest with %d items", len(proposals_made))
