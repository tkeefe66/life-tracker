"""Handler for /syncstories — apply sheet decisions and start Telegram review."""
import json
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db
from google_sheets import read_story_decisions

logger = logging.getLogger(__name__)


async def present_next_story(reply):
    """Lazy import shim — calls handlers.story_review.present_next_story.

    Defined at module level so tests can patch this name. The real
    implementation lives in handlers.story_review (added in Task 14).
    """
    from handlers.story_review import present_next_story as _impl
    await _impl(reply)


async def syncstories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Read the Stories sheet, dismiss skips, enqueue confirms for Telegram review."""
    await update.message.reply_text("📋 Reading Stories tab from Google Sheets…")
    try:
        decisions = read_story_decisions()
    except Exception as e:
        logger.error("read_story_decisions failed: %s", e, exc_info=True)
        await update.message.reply_text(f"❌ Couldn't read sheet: {e}")
        return

    if not decisions:
        await update.message.reply_text(
            "No decisions to apply. Open the *Stories* tab, mark each story "
            "`yes` or `skip`, then run /syncstories.",
            parse_mode="Markdown",
        )
        return

    survivors, dismissed, missing = [], 0, 0
    for d in decisions:
        entry = db.get_life_log_entry(d["id"])
        if entry is None or entry["status"] != "proposed" or entry["parent_id"] is not None:
            missing += 1
            continue
        if d["decision"] == "skip":
            db.dismiss_story(d["id"])
            dismissed += 1
        elif d["decision"] == "confirm":
            survivors.append(d["id"])

    # Save the survivor queue into conversation_state.temp_data
    state = db.get_state()
    temp = state.get("temp_data") or {}
    if isinstance(temp, str):
        try:
            temp = json.loads(temp)
        except Exception:
            temp = {}
    temp["pending_story_ids"] = survivors
    temp["current_story_id"] = None  # set when we present the first story
    db.set_state(
        state="story_confirming" if survivors else "idle",
        temp_data=temp,
    )

    await update.message.reply_text(
        f"✅ Decisions applied:\n"
        f"• {len(survivors)} stories surviving — review in Telegram next\n"
        f"• {dismissed} dismissed\n"
        f"• {missing} skipped (already handled or no longer pending)\n"
    )
    if survivors:
        await present_next_story(update.message.reply_text)
