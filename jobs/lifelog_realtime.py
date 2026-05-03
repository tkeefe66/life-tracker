"""
lifelog_realtime — runs every ~15 minutes.

Fetches recently-added calendar events, ingests them into activity_log,
and sends real-time Telegram proposals for any classified as "high confidence".
"""
import logging

from telegram import Bot

import database as db
from ai_life_log import propose_from_calendar_event
from config import TELEGRAM_CHAT_ID
from services.calendar_service import get_events_rolling_window, is_configured

logger = logging.getLogger(__name__)


def _fetch_recent_calendar_events() -> list:
    """Fetch the next 30 days of calendar events. Override in tests."""
    if not is_configured():
        return []
    return get_events_rolling_window(days=30)


def _format_proposal_message(parsed: dict, event: dict, entry_id: int) -> str:
    cats = " + ".join(parsed["categories"]) or "(no category)"
    people = ", ".join(parsed.get("people", [])) or "(none)"
    location = parsed.get("location") or event.get("location", "") or "(none)"

    start = event["start_datetime"][:10] if event["start_datetime"] else ""
    end = event["end_datetime"][:10] if event["end_datetime"] else ""
    date_label = f"{start} → {end}" if end and end != start else start

    return (
        f"📅 *{parsed.get('description', event['title'])}*\n"
        f"🗓 {date_label}\n"
        f"🏷 {cats}\n"
        f"👥 {people}\n"
        f"📍 {location}\n\n"
        f"Reply *yes #{entry_id}* to confirm, *skip #{entry_id}* to dismiss, "
        f"or *edit #{entry_id} <new text>* to revise."
    )


async def run_realtime_proposals(bot: Bot):
    events = _fetch_recent_calendar_events()
    if not events:
        return

    active_cats = [c["name"] for c in db.get_active_categories()]

    new_proposals = 0
    for event in events:
        # Already ingested? skip
        if db.get_activity_by_source_id("calendar", event["event_id"]):
            continue

        # Always record raw activity
        db.record_activity(
            source="calendar",
            source_id=event["event_id"],
            event_type="calendar_event",
            occurred_at=event["start_datetime"] or None,
            payload=event,
        )

        # Classify
        parsed = propose_from_calendar_event(
            title=event["title"],
            start=event["start_datetime"],
            end=event["end_datetime"],
            attendees=event.get("attendees", []),
            description=event.get("description", ""),
            location=event.get("location", ""),
            active_categories=active_cats,
        )

        if parsed.get("confidence") != "high":
            continue  # day-after / sunday jobs handle the rest

        # Save proposal
        date_start = event["start_datetime"][:10]
        date_end = event["end_datetime"][:10] if event.get("end_datetime") else None
        if date_end == date_start:
            date_end = None

        entry_id = db.save_proposal(
            date_start=date_start,
            date_end=date_end,
            categories=parsed["categories"],
            description=parsed["description"] or event["title"],
            location=parsed.get("location") or event.get("location"),
            source="calendar",
            source_id=event["event_id"],
        )

        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=_format_proposal_message(parsed, event, entry_id),
            parse_mode="Markdown",
        )
        new_proposals += 1

    logger.info("lifelog_realtime: %d new proposals", new_proposals)
