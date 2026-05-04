"""Handler for /showproposal <id> — debug-dump a proposal and its source event."""
import json
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)


async def showproposal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the DB row for a proposal plus its original calendar event JSON.

    Usage: /showproposal 39
    """
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /showproposal <id>")
        return
    try:
        entry_id = int(args[0])
    except ValueError:
        await update.message.reply_text("ID must be an integer.")
        return

    entry = db.get_life_log_entry(entry_id)
    if entry is None:
        await update.message.reply_text(f"No entry with ID {entry_id}.")
        return

    parts = [f"📋 *Proposal {entry_id}* (status: {entry.get('status')})"]
    parts.append(f"• date_start: `{entry.get('date_start')}`")
    parts.append(f"• date_end: `{entry.get('date_end')}`")
    parts.append(f"• categories: `{entry.get('categories')}`")
    parts.append(f"• description: {entry.get('description')!r}")
    parts.append(f"• location: {entry.get('location')!r}")
    parts.append(f"• source: `{entry.get('source')}` source_id: `{entry.get('source_id')}`")

    if entry.get("source") == "calendar" and entry.get("source_id"):
        rows = db.get_activity_by_source_id("calendar", entry["source_id"])
        if rows:
            payload = rows[0].get("payload") or {}
            parts.append("\n📅 *Original calendar event:*")
            parts.append(f"• title: {payload.get('title')!r}")
            parts.append(f"• start: `{payload.get('start_datetime')}`")
            parts.append(f"• end: `{payload.get('end_datetime')}`")
            parts.append(f"• location: {payload.get('location')!r}")
            parts.append(f"• description: {payload.get('description')!r}")
            parts.append(f"• attendees: `{payload.get('attendees')}`")
            parts.append(f"• is_recurring: `{payload.get('is_recurring')}`")
        else:
            parts.append("\n⚠️ No matching activity_log row for this source_id.")

    msg = "\n".join(parts)
    # Telegram message limit is 4096 chars; truncate if needed
    if len(msg) > 3800:
        msg = msg[:3800] + "\n…(truncated)"
    await update.message.reply_text(msg, parse_mode="Markdown")
