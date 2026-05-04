"""Handler for /dismissbirthdays — bulk-dismiss pending birthday proposals."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db
from ai_life_log import _BIRTHDAY_RE

logger = logging.getLogger(__name__)


async def dismissbirthdays_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dismiss every pending proposal whose description or source title looks like a birthday.

    The AI rewrites titles into memoir-style descriptions, so we check both
    the proposal's `description` field and the original calendar title stored
    in `activity_log.payload.title` to catch them all.
    """
    pending = db.get_pending_proposals()
    if not pending:
        await update.message.reply_text("No pending proposals.")
        return

    dismissed_ids = []
    for entry in pending:
        desc = entry.get("description") or ""
        if _BIRTHDAY_RE.search(desc):
            dismissed_ids.append(entry["id"])
            continue

        # Fall back to checking the original calendar title
        if entry.get("source") == "calendar" and entry.get("source_id"):
            rows = db.get_activity_by_source_id("calendar", entry["source_id"])
            if rows:
                payload = rows[0].get("payload") or {}
                title = payload.get("title") or ""
                if _BIRTHDAY_RE.search(title):
                    dismissed_ids.append(entry["id"])

    for eid in dismissed_ids:
        db.dismiss_proposal(eid)

    remaining = len(pending) - len(dismissed_ids)
    msg = (
        f"🎂 Dismissed {len(dismissed_ids)} birthday proposal(s).\n"
        f"📊 {remaining} non-birthday proposals still pending.\n\n"
        f"Run /pushproposals to refresh the Proposals tab."
    )
    await update.message.reply_text(msg)
