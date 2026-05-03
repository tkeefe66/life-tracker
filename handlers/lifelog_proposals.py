"""Handler for replies to Life Log proposals (yes #N, skip #N, edit #N <text>, yes all, skip all)."""
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)


_YES_N = re.compile(r"^\s*(yes|y|confirm|save|ok)\s+#?(\d+)\s*$", re.IGNORECASE)
_SKIP_N = re.compile(r"^\s*(skip|no|dismiss)\s+#?(\d+)\s*$", re.IGNORECASE)
_EDIT_N = re.compile(r"^\s*edit\s+#?(\d+)\s+(.+)$", re.IGNORECASE | re.DOTALL)
_YES_ALL = re.compile(r"^\s*yes\s+all\s*$", re.IGNORECASE)
_SKIP_ALL = re.compile(r"^\s*skip\s+all\s*$", re.IGNORECASE)


async def _confirm_one(entry_id: int):
    """Confirm a single proposal. Returns the entry dict on success, None if not found/pending."""
    entry = db.get_life_log_entry(entry_id)
    if entry is None or entry["status"] != "proposed":
        return None
    db.confirm_proposal(entry_id)
    for cat in entry["categories"]:
        db.increment_category_usage(cat)
    if entry.get("source_id"):
        rows = db.get_activity_by_source_id(entry["source"], entry["source_id"])
        if rows:
            db.mark_activity_promoted(rows[0]["id"])
    # Fire-and-forget sync
    try:
        from google_sheets import sync_life_log_to_sheets
        entries = db.get_all_life_log_entries()
        people = db.get_all_people()
        people_by_entry = {
            e["id"]: [p["name"] for p in db.get_people_for_entry(e["id"])]
            for e in entries
        }
        sync_life_log_to_sheets(entries, people, people_by_entry)
    except Exception as e:
        logger.warning("Auto-sync failed (non-fatal): %s", e)
    return entry


async def handle_proposal_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """
    Handle proposal reply commands. Returns True if message was a recognized proposal reply,
    False if the message should fall through to normal routing.
    """
    m = _YES_N.match(text)
    if m:
        entry_id = int(m.group(2))
        entry = await _confirm_one(entry_id)
        if entry is None:
            await update.message.reply_text(f"No pending proposal #{entry_id}.")
        else:
            await update.message.reply_text(
                f"✅ Confirmed: *{entry['description']}*", parse_mode="Markdown"
            )
        return True

    m = _SKIP_N.match(text)
    if m:
        entry_id = int(m.group(2))
        entry = db.get_life_log_entry(entry_id)
        if entry is None or entry["status"] != "proposed":
            await update.message.reply_text(f"No pending proposal #{entry_id}.")
        else:
            db.dismiss_proposal(entry_id)
            await update.message.reply_text(f"⏭ Skipped #{entry_id}.")
        return True

    m = _EDIT_N.match(text)
    if m:
        entry_id = int(m.group(1))
        new_desc = m.group(2).strip()
        entry = db.get_life_log_entry(entry_id)
        if entry is None or entry["status"] != "proposed":
            await update.message.reply_text(f"No pending proposal #{entry_id}.")
        else:
            db.update_life_log_entry(
                entry_id, entry["categories"], new_desc, entry.get("location"), entry.get("notes")
            )
            await _confirm_one(entry_id)
            await update.message.reply_text(
                f"✅ Edited & confirmed: *{new_desc}*", parse_mode="Markdown"
            )
        return True

    if _YES_ALL.match(text):
        pending = db.get_pending_proposals()
        for p in pending:
            await _confirm_one(p["id"])
        await update.message.reply_text(f"✅ Confirmed all {len(pending)} proposals.")
        return True

    if _SKIP_ALL.match(text):
        pending = db.get_pending_proposals()
        for p in pending:
            db.dismiss_proposal(p["id"])
        await update.message.reply_text(f"⏭ Skipped all {len(pending)} proposals.")
        return True

    return False
