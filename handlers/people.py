"""Handler for the /people command — list, view, merge people in the Life Log."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)


async def people_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []

    if len(args) >= 4 and args[0].lower() == "merge" and args[2].lower() == "into":
        try:
            merge_id = int(args[1])
            keep_id = int(args[3])
        except ValueError:
            await update.message.reply_text("Usage: /people merge <merge_id> into <keep_id>")
            return
        db.merge_people(keep_id=keep_id, merge_id=merge_id)
        await update.message.reply_text(f"✅ Merged person #{merge_id} into #{keep_id}.")
        return

    people = db.get_all_people()
    if not people:
        await update.message.reply_text("No people in your Life Log yet.")
        return

    lines = ["👥 *People in your Life Log:*", ""]
    for p in people:
        rel = p.get("relationship_type") or "—"
        status = p.get("status", "active")
        last_seen = p.get("last_seen") or "?"
        line = f"`#{p['id']}` *{p['name']}* ({rel}, {status}) — last seen {last_seen}"
        if p.get("aliases"):
            line += f"  _aliases: {', '.join(p['aliases'])}_"
        lines.append(line)
    lines.append("")
    lines.append("To merge duplicates: `/people merge <id> into <id>`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
