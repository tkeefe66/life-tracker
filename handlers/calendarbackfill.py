"""Handler for /calendarbackfill — one-shot import of calendar history into Proposals."""
import datetime
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)


async def calendarbackfill_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage:
      /calendarbackfill 2010         → scan 2010 through current year
      /calendarbackfill 2010 2020    → scan 2010 through 2020 (range)
      /calendarbackfill              → show usage hint
    """
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "📅 *Calendar history backfill*\n\n"
            "Usage:\n"
            "• `/calendarbackfill 2010` — scan 2010 → current year\n"
            "• `/calendarbackfill 2018 2022` — scan 2018 → 2022 inclusive\n\n"
            "Each year is scanned, AI-classified, and matching proposals "
            "land in the *Proposals* tab of your Sheet.",
            parse_mode="Markdown",
        )
        return

    try:
        start_year = int(args[0])
        end_year = int(args[1]) if len(args) > 1 else datetime.date.today().year
    except ValueError:
        await update.message.reply_text("Years must be integers, e.g. /calendarbackfill 2010")
        return

    if start_year > end_year:
        await update.message.reply_text("Start year must be <= end year.")
        return
    if end_year - start_year > 30:
        await update.message.reply_text("Range too big (>30 years). Run smaller chunks.")
        return

    from scripts.import_calendar_history import import_year, _fetch_year_events

    await update.message.reply_text(
        f"📅 Scanning calendar from {start_year} → {end_year}…\n"
        f"This will take a few minutes. I'll ping you when it's done."
    )

    total_proposals = 0
    failed_years = []

    for y in range(start_year, end_year + 1):
        try:
            count = import_year(y, dry_run=False)
            total_proposals += count
            logger.info("Year %d: %d proposals", y, count)
        except Exception as e:
            logger.error("Year %d failed: %s", y, e, exc_info=True)
            failed_years.append((y, str(e)))

    # Push everything to the Proposals tab
    pending = db.get_pending_proposals()
    sheet_url = ""
    try:
        from google_sheets import sync_proposals_to_sheet
        sheet_url = sync_proposals_to_sheet(pending)
    except Exception as e:
        logger.warning("Proposals sheet sync failed: %s", e)

    msg = (
        f"✅ Backfill done!\n\n"
        f"• {total_proposals} new proposals created\n"
        f"• {len(pending)} total pending in your sheet\n"
    )
    if failed_years:
        msg += f"\n⚠️ {len(failed_years)} year(s) failed — check Railway logs."
    if sheet_url:
        msg += f"\n\n📝 [Open Proposals tab]({sheet_url})\n\n"
    msg += "Open the *Proposals* tab, fill the Decision column, then run /syncproposals."

    await update.message.reply_text(msg, parse_mode="Markdown")
