"""
monthly_forward — runs on the 1st of each month.

Looks ahead at the next 30 days of calendar events and sends a
forward-looking summary via Telegram to help with monthly planning.
"""

import datetime
import logging

import pytz
from telegram import Bot

import database as db
from config import TELEGRAM_CHAT_ID, TIMEZONE
from services.calendar_service import get_events_rolling_window, is_configured
from ai_summarize import _call

logger = logging.getLogger(__name__)


async def run_monthly_forward(bot: Bot):
    """Called by the job scheduler on the 1st of each month."""
    if not is_configured():
        logger.info("Calendar not configured — skipping monthly_forward job")
        return

    tz = pytz.timezone(TIMEZONE)
    now = datetime.datetime.now(tz)
    month_name = (now + datetime.timedelta(days=1)).strftime("%B")

    try:
        events = get_events_rolling_window(days=30)
    except Exception as e:
        logger.error("monthly_forward calendar fetch failed: %s", e)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"⚠️ Monthly forward planning failed to fetch calendar: {e}",
        )
        return

    if not events:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"📆 *{month_name} Forward Look*\n\nNo calendar events found in the next 30 days.",
            parse_mode="Markdown",
        )
        return

    events_text = "\n".join(
        f"- {e['title']} on {e['start_datetime'][:10]}"
        + (f" (recurring)" if e["is_recurring"] else "")
        for e in events
    )

    prompt = f"""You are helping someone plan for the coming month.

Here are their calendar events for the next 30 days:
{events_text}

Write a brief forward-looking summary (3-5 bullet points) highlighting:
- Key commitments or busy periods
- Any recurring patterns worth noting
- Suggested focus areas based on what's coming up

Keep it concise and actionable. Use • bullet points."""

    try:
        summary = _call(prompt, max_tokens=400)
    except Exception as e:
        logger.error("monthly_forward AI summary failed: %s", e)
        summary = "\n".join(f"• {e['title']} — {e['start_datetime'][:10]}" for e in events[:10])

    message = f"📆 *{month_name} Forward Look* ({len(events)} events)\n\n{summary}"
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message,
        parse_mode="Markdown",
    )
    logger.info("monthly_forward: sent summary for %s (%d events)", month_name, len(events))
