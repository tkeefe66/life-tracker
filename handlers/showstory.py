"""Handler for /showstory <id> — dump story DB row + children."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)


async def showstory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /showstory <id>")
        return
    try:
        sid = int(args[0])
    except ValueError:
        await update.message.reply_text("ID must be an integer.")
        return
    parent = db.get_life_log_entry(sid)
    if parent is None:
        await update.message.reply_text(f"No entry with ID {sid}.")
        return

    parts = [
        f"📖 *Story {sid}* (status: {parent.get('status')})",
        f"• type: `{parent.get('story_type')}`",
        f"• date: `{parent.get('date_start')}` → `{parent.get('date_end')}`",
        f"• summary: {parent.get('description')!r}",
        f"• location: {parent.get('location')!r}",
        f"• why_mattered: {parent.get('why_mattered')!r}",
        f"• highlights: {parent.get('highlights')}",
        f"• extras: `{parent.get('extras')}`",
    ]

    p = db._p()
    with db._cursor() as c:
        c.execute(
            f"SELECT * FROM life_log_entries WHERE parent_id={p} ORDER BY date_start, id",
            (sid,),
        )
        children = [db._unpack_life_log_entry(r) for r in db._rows(c.fetchall())]

    if children:
        parts.append("\n👶 *Children:*")
        for ch in children:
            parts.append(
                f"  • #{ch['id']} {ch.get('date_start')} — "
                f"{ch.get('description')!r} @ {ch.get('location')!r}"
            )
    msg = "\n".join(parts)
    if len(msg) > 3800:
        msg = msg[:3800] + "\n…(truncated)"
    await update.message.reply_text(msg, parse_mode="Markdown")
