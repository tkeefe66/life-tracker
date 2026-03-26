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


async def _sync_calendar_window(bot: Bot, days: int) -> int:
    """
    Fetch events for the given window, save new ones to Later Items, and
    send a Telegram summary. Returns the count of newly synced events.
    """
    try:
        events = get_events_rolling_window(days=days)
    except Exception as e:
        logger.error("Calendar fetch failed: %s", e)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"⚠️ Calendar sync failed: {e}\n\nCheck your GOOGLE_CALENDAR_* env vars.",
        )
        return 0

    new_events = []
    recurring_events = []

    for event in events:
        if db.is_event_synced(event["event_id"]):
            continue

        start = event["start_datetime"]
        end = event["end_datetime"]
        target_date = start[:10] if start else ""
        # For multi-day all-day events, end.date is exclusive (e.g. ends "2026-04-16"
        # for a trip through Apr 15), so subtract one day.
        end_date = None
        if end:
            end_d = end[:10]
            if end_d != target_date:
                from datetime import date, timedelta
                parsed = date.fromisoformat(end_d)
                # All-day end dates are exclusive; timed events are not
                if "T" not in end:
                    parsed -= timedelta(days=1)
                end_date = parsed.isoformat() if parsed.isoformat() != target_date else None

        db.save_later_item_full(
            content=event["title"],
            target_date=target_date,
            source="calendar",
            event_id=event["event_id"],
            end_date=end_date,
        )
        db.mark_event_synced(event["event_id"])
        new_events.append(event)

        if event["is_recurring"]:
            recurring_events.append(event)

    if not new_events:
        logger.info("calendar sync (%dd): no new events", days)
        return 0

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
    logger.info("calendar sync (%dd): synced %d new events", days, len(new_events))
    return len(new_events)


async def run_daily_calendar_sync(bot: Bot):
    """Called by the job scheduler. Syncs the next 2 days of calendar events."""
    if not is_configured():
        logger.info("Calendar not configured — skipping daily_calendar job")
        return
    await _sync_calendar_window(bot, days=2)


async def run_bulk_calendar_sync(bot: Bot, days: int = 180):
    """Manual bulk sync — looks ahead `days` days (default 6 months)."""
    if not is_configured():
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="⚠️ Calendar not configured. Check your GOOGLE_CALENDAR_* env vars.",
        )
        return
    await _sync_calendar_window(bot, days=days)
