"""Handler for /proposals — paginated review of pending Life Log proposals."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)

PAGE_SIZE = 10


async def proposals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List the next page of pending proposals. Optional arg: page number (1-based)."""
    page = 1
    if context.args:
        try:
            page = max(1, int(context.args[0]))
        except ValueError:
            pass

    pending = db.get_pending_proposals()
    if not pending:
        await update.message.reply_text(
            "✨ No pending proposals. Run `/calendarsync` or wait for the next ingestion.",
            parse_mode="Markdown",
        )
        return

    total = len(pending)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    chunk = pending[start:end]

    if not chunk:
        await update.message.reply_text(
            f"Page {page} is past the end. Total pages: {pages}."
        )
        return

    lines = [f"📋 *Pending proposals — page {page}/{pages}* ({total} total)", ""]
    for p in chunk:
        cats = " + ".join(p.get("categories") or []) or "?"
        date = p.get("date_start") or ""
        end_date = p.get("date_end") or ""
        date_label = f"{date} → {end_date}" if end_date else date
        desc = p.get("description") or "(no description)"
        lines.append(f"*#{p['id']}* — {desc}")
        lines.append(f"     {cats} · {date_label}")
        lines.append("")

    lines.append("Reply *yes #N* / *skip #N* / *edit #N <text>* per item")
    lines.append("Or *yes all* / *skip all* to bulk-handle EVERYTHING pending")
    if pages > 1:
        next_page = page + 1 if page < pages else 1
        lines.append(f"Next page: `/proposals {next_page}`")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
