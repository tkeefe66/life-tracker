"""Handler for /pushstories — retry pushing pending stories to the Sheet."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db
from google_sheets import sync_stories_to_sheet

logger = logging.getLogger(__name__)


async def pushstories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stories = db.get_pending_stories_with_children()
    if not stories:
        await update.message.reply_text("No pending stories.")
        return

    await update.message.reply_text(
        f"📤 Pushing {len(stories)} stories to the Stories tab…"
    )
    try:
        url = sync_stories_to_sheet(stories)
    except Exception as e:
        logger.error("sync_stories_to_sheet failed: %s", e, exc_info=True)
        await update.message.reply_text(
            f"❌ Sheet write failed.\n\n`{type(e).__name__}: {e}`",
            parse_mode="Markdown",
        )
        return
    await update.message.reply_text(
        f"✅ Wrote {len(stories)} stories.\n📝 [Open]({url})",
        parse_mode="Markdown",
    )
