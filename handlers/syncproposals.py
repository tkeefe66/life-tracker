"""Handler for /syncproposals — read decisions from the Proposals sheet and apply them."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)


async def syncproposals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Read the Proposals tab, apply Decision column, then re-sync the sheet."""
    await update.message.reply_text("📋 Reading Proposals tab from Google Sheets…")

    try:
        from google_sheets import read_proposal_decisions, sync_proposals_to_sheet
        decisions = read_proposal_decisions()
    except Exception as e:
        logger.error("Failed to read proposals sheet: %s", e, exc_info=True)
        await update.message.reply_text(f"❌ Couldn't read sheet: {e}")
        return

    if not decisions:
        await update.message.reply_text(
            "No decisions to apply. Open the *Proposals* tab and fill in the Decision column "
            "(`yes` / `skip` / new description text), then run /syncproposals again.",
            parse_mode="Markdown",
        )
        return

    confirmed = skipped = edited = missing = 0

    for d in decisions:
        entry = db.get_life_log_entry(d["id"])
        if entry is None or entry["status"] != "proposed":
            missing += 1
            continue

        if d["decision"] == "confirm":
            db.confirm_proposal(d["id"])
            for cat in entry["categories"]:
                db.increment_category_usage(cat)
            if entry.get("source_id"):
                rows = db.get_activity_by_source_id(entry["source"], entry["source_id"])
                if rows:
                    db.mark_activity_promoted(rows[0]["id"])
            confirmed += 1

        elif d["decision"] == "skip":
            db.dismiss_proposal(d["id"])
            skipped += 1

        elif d["decision"] == "edit":
            db.update_life_log_entry(
                d["id"], entry["categories"], d["new_description"],
                entry.get("location"), entry.get("notes"),
            )
            db.confirm_proposal(d["id"])
            for cat in entry["categories"]:
                db.increment_category_usage(cat)
            if entry.get("source_id"):
                rows = db.get_activity_by_source_id(entry["source"], entry["source_id"])
                if rows:
                    db.mark_activity_promoted(rows[0]["id"])
            edited += 1

    # Re-sync the sheet so processed rows disappear
    remaining = db.get_pending_proposals()
    try:
        sync_proposals_to_sheet(remaining)
    except Exception as e:
        logger.warning("Re-sync of proposals sheet failed (non-fatal): %s", e)

    await update.message.reply_text(
        f"✅ Applied decisions:\n"
        f"• {confirmed} confirmed\n"
        f"• {edited} edited & confirmed\n"
        f"• {skipped} skipped\n"
        f"• {missing} skipped (no longer pending)\n\n"
        f"📊 {len(remaining)} proposals still pending in the Proposals tab.",
    )
