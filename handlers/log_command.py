"""Handler for the /log command — manual Life Log entry capture."""
import datetime
import json
import logging

import pytz
from telegram import Update
from telegram.ext import ContextTypes

import database as db
from ai_life_log import parse_log_command
from config import TIMEZONE

logger = logging.getLogger(__name__)


def _today_str() -> str:
    return datetime.datetime.now(pytz.timezone(TIMEZONE)).date().isoformat()


def _format_preview(parsed: dict) -> str:
    cats = ", ".join(parsed.get("categories", [])) or "(none)"
    location = parsed.get("location") or "(none)"
    people = ", ".join(parsed.get("people", [])) or "(none)"
    date_start = parsed.get("date_start", "")
    date_end = parsed.get("date_end")
    date_label = f"{date_start} → {date_end}" if date_end else date_start

    lines = [
        "Here's what I understood:",
        "",
        f"📝 *{parsed.get('description', '')}*",
        f"📅 {date_label}",
        f"🏷  {cats}",
        f"👥 {people}",
        f"📍 {location}",
    ]

    questions = parsed.get("questions", [])
    if questions:
        lines.append("")
        lines.append("❓ I wasn't sure about:")
        for q in questions:
            lines.append(f"• {q}")

    lines.append("")
    lines.append("Reply *Yes* to save, *No* to cancel, or send a correction.")
    return "\n".join(lines)


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /log [text]."""
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "📝 *What happened?*\n\n"
            "Tell me what to log and I'll figure out the category, people, and date.\n\n"
            "Examples:\n"
            "• `/log Met Megan at Goldens in Golden`\n"
            "• `/log Skied Killington with Justin yesterday`\n"
            "• `/log Spinkel Wedding next week in London`",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text("Reading your message… 🤔")

    active_cats = [c["name"] for c in db.get_active_categories()]
    parsed = parse_log_command(text, today=_today_str(), active_categories=active_cats)

    db.set_state(
        "lifelog_confirming",
        temp_data={"original_text": text, "parsed": parsed},
    )

    await update.message.reply_text(_format_preview(parsed), parse_mode="Markdown")
