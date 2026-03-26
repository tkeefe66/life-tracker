"""
daily_calendar — runs at midnight each day.

Fetches events from Google Calendar (rolling 2-day window), adds any
new events to the Later Items table, and sends a Telegram summary.
Recurring events are flagged for habit consideration.
"""

import logging

from telegram import Bot

import database as db
from config import TELEGRAM_CHAT_ID
from services.calendar_service import get_events_rolling_window, is_configured

logger = logging.getLogger(__name__)


def _format_event_line(event: dict) -> str:
    start = event["start_datetime"]
    # Strip timezone suffix for readability — show just date or date+time
    if "T" in start:
        date_part, time_part = start[:10], start[11:16]
        label = f"{date_part} {time_part}"
    else:
        label = start
    title = event["title"]
    flag = " ♻️" if event["is_recurring"] else ""
    return f"• {title} — {label}{flag}"


async def run_daily_calendar_sync(bot: Bot):
    """Called by the job scheduler. Syncs calendar and notifies via Telegram."""
    if not is_configured():
        logger.info("Calendar not configured — skipping daily_calendar job")
        return

    try:
        events = get_events_rolling_window(days=2)
    except Exception as e:
        logger.error("Calendar fetch failed: %s", e)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"⚠️ Calendar sync failed: {e}\n\nCheck your GOOGLE_CALENDAR_* env vars.",
        )
        return

    new_events = []
    recurring_events = []

    for event in events:
        if db.is_event_synced(event["event_id"]):
            continue

        # Choose a target_date from the event start
        start = event["start_datetime"]
        target_date = start[:10] if start else ""

        db.save_later_item_full(
            content=event["title"],
            target_date=target_date,
            source="calendar",
            event_id=event["event_id"],
        )
        db.mark_event_synced(event["event_id"])
        new_events.append(event)

        if event["is_recurring"]:
            recurring_events.append(event)

    if not new_events:
        logger.info("daily_calendar: no new events to sync")
        return

    lines = [f"📅 *{len(new_events)} new calendar event(s) added to Later:*\n"]
    for e in new_events:
        lines.append(_format_event_line(e))

    if recurring_events:
        lines.append(
            f"\n♻️ *{len(recurring_events)} recurring event(s) detected* — "
            f"consider adding these as habits with /habit"
        )

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text="\n".join(lines),
        parse_mode="Markdown",
    )
    logger.info("daily_calendar: synced %d new events", len(new_events))
