"""Handler for /pushproposals — retry writing pending proposals to the Sheet."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)


async def pushproposals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Push all pending proposals from the DB to the Proposals tab.

    Use this when /calendarbackfill reported success but the Proposals tab
    is empty — the AI work is already saved in the DB; this just re-tries
    the Sheets write.
    """
    pending = db.get_pending_proposals()
    if not pending:
        await update.message.reply_text("No pending proposals in the DB.")
        return

    await update.message.reply_text(
        f"📤 Pushing {len(pending)} pending proposals to the *Proposals* tab…",
        parse_mode="Markdown",
    )

    try:
        from google_sheets import sync_proposals_to_sheet
        sheet_url = sync_proposals_to_sheet(pending)
    except Exception as e:
        logger.error("sync_proposals_to_sheet failed: %s", e, exc_info=True)
        await update.message.reply_text(
            f"❌ Sheet write failed.\n\n"
            f"`{type(e).__name__}: {e}`\n\n"
            f"Proposals are still safe in the DB. Check Railway logs for the full traceback.",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        f"✅ Wrote {len(pending)} proposals to the Proposals tab.\n\n"
        f"📝 [Open Proposals tab]({sheet_url})\n\n"
        f"Fill the Decision column, then run /syncproposals.",
        parse_mode="Markdown",
    )
