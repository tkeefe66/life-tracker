"""
lifelog_realtime — runs every ~6 hours by default.

Fetches recently-added calendar events, ingests them into activity_log,
classifies them, and creates "proposed" Life Log entries for high-confidence
matches. Silently writes to the Proposals tab in Sheets — no Telegram messages.
The day-after job sends ONE summary nudge per day.
"""
import logging

from telegram import Bot

import database as db
from ai_life_log import propose_from_calendar_event
from services.calendar_service import get_events_rolling_window, is_configured

logger = logging.getLogger(__name__)


def _fetch_recent_calendar_events() -> list:
    """Fetch the next 30 days of calendar events. Override in tests."""
    if not is_configured():
        return []
    return get_events_rolling_window(days=30)


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

        # Save proposal silently — sheet sync at end will surface it
        date_start = event["start_datetime"][:10]
        date_end = event["end_datetime"][:10] if event.get("end_datetime") else None
        if date_end == date_start:
            date_end = None

        db.save_proposal(
            date_start=date_start,
            date_end=date_end,
            categories=parsed["categories"],
            description=parsed["description"] or event["title"],
            location=parsed.get("location") or event.get("location"),
            source="calendar",
            source_id=event["event_id"],
        )
        new_proposals += 1

    if new_proposals:
        try:
            from google_sheets import sync_proposals_to_sheet
            sync_proposals_to_sheet(db.get_pending_proposals())
        except Exception as e:
            logger.warning("Proposals sheet sync failed (non-fatal): %s", e)

    logger.info("lifelog_realtime: %d new proposals (silent — review in Proposals tab)", new_proposals)
