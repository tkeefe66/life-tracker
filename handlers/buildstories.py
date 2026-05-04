"""Handler for /buildstories — cluster pending proposals and push to Sheet."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db
from services.story_clustering import run_clustering
from google_sheets import sync_stories_to_sheet

logger = logging.getLogger(__name__)


async def buildstories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cluster all currently-pending un-assigned events into stories.

    Idempotent: re-running picks up only events that aren't already attached
    to a parent story.
    """
    await update.message.reply_text(
        "📚 Building stories from pending events… this may take a few minutes."
    )

    try:
        n_new = run_clustering()
    except Exception as e:
        logger.error("run_clustering failed: %s", e, exc_info=True)
        await update.message.reply_text(
            f"❌ Clustering failed.\n\n`{type(e).__name__}: {e}`",
            parse_mode="Markdown",
        )
        return

    stories = db.get_pending_stories_with_children()
    sheet_url = ""
    sheet_error = ""
    try:
        sheet_url = sync_stories_to_sheet(stories)
    except Exception as e:
        logger.error("sync_stories_to_sheet failed: %s", e, exc_info=True)
        sheet_error = f"{type(e).__name__}: {e}"

    msg = (
        f"✅ Built {n_new} new stories.\n"
        f"📊 {len(stories)} total stories pending in your Sheet.\n"
    )
    if sheet_url:
        msg += (
            f"\n📝 [Open Stories tab]({sheet_url})\n\n"
            f"Mark each story `yes` or `skip` in the Decision column, "
            f"then run /syncstories."
        )
    elif sheet_error:
        msg += (
            f"\n⚠️ Sheet write failed: `{sheet_error}`\n"
            f"Stories are saved in the DB. Run /pushstories to retry."
        )
    await update.message.reply_text(msg, parse_mode="Markdown")
