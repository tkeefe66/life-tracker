"""
Telegram bot handlers and scheduled job callbacks.
"""

import datetime
import json
import logging
from datetime import date, timedelta

import pytz
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db
from config import (
    DAILY_PROMPT_HOUR,
    DAILY_PROMPT_MINUTE,
    HABIT_REMINDER_HOUR,
    HABIT_REMINDER_MINUTE,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TIMEZONE,
    WEEKLY_SUMMARY_HOUR,
)

logger = logging.getLogger(__name__)

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_date(date_str: str) -> str:
    return date.fromisoformat(date_str).strftime("%A, %B %d")


def _week_label(week_start: str) -> str:
    start = date.fromisoformat(week_start)
    end = start + timedelta(days=6)
    return f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"


def _monday_of_week(d: date = None) -> str:
    d = d or date.today()
    return (d - timedelta(days=d.weekday())).isoformat()


def _next_monday() -> str:
    today = date.today()
    if today.weekday() == 6:  # Sunday — skip to the Monday of next week, not tomorrow
        return (today + timedelta(days=7)).isoformat()
    days_ahead = (7 - today.weekday()) % 7 or 7
    return (today + timedelta(days=days_ahead)).isoformat()


# ── Message senders ───────────────────────────────────────────────────────────

async def _send(bot, text: str):
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="Markdown")


async def _prompt_work(bot, for_date: str):
    await _send(
        bot,
        f"💼 *Work Accomplishments — {_fmt_date(for_date)}*\n\n"
        "What did you accomplish at work today?\n"
        "You can list multiple items in one message.\n\n"
        "_(Type /skip if you had nothing work-related)_",
    )


async def _prompt_personal(bot, for_date: str):
    await _send(
        bot,
        f"🏆 *Personal Accomplishments — {_fmt_date(for_date)}*\n\n"
        "What personal things did you accomplish?\n\n"
        "_(Type /skip if nothing to add)_",
    )


async def _prompt_focus(bot):
    next_mon = date.fromisoformat(_next_monday()).strftime("%B %d")
    await _send(
        bot,
        f"🎯 *Next Week's Focus — Week of {next_mon}*\n\n"
        "What are your top priorities for next week?\n"
        "List as many items as you like.\n\n"
        "_(Type /skip to leave blank for now)_",
    )


# ── Conversation flow ─────────────────────────────────────────────────────────

async def _start_collection(bot, dates: list[str]):
    """Begin collecting work/personal for each date in order, then ask focus."""
    if not dates:
        db.set_state("collecting_focus")
        await _prompt_focus(bot)
        return

    current, *remaining = dates
    db.set_state("collecting_work", current_date=current, pending_dates=remaining)
    await _prompt_work(bot, current)


# ── Scheduled jobs ────────────────────────────────────────────────────────────

async def daily_prompt_job(context: ContextTypes.DEFAULT_TYPE):
    """Fires every day at the configured time."""
    bot = context.bot
    today = date.today().isoformat()

    missed = db.get_missed_dates()
    dates_to_collect = missed + [today]

    if missed:
        missed_fmt = ", ".join(_fmt_date(d) for d in missed)
        await _send(
            bot,
            f"📅 *Daily Update Time!*\n\n"
            f"Hey! You missed updates for: *{missed_fmt}*.\n\n"
            f"Let's catch up and also log today. I'll walk you through each day one at a time. 📝",
        )
    else:
        await _send(
            bot,
            f"📅 *Daily Update — {_fmt_date(today)}*\n\n"
            "Time to log your accomplishments! Let's go 💪",
        )

    await _start_collection(bot, dates_to_collect)


async def weekly_summary_job(context: ContextTypes.DEFAULT_TYPE):
    """Fires every Sunday at the configured time."""
    bot = context.bot
    today = date.today()
    week_start = _monday_of_week(today)

    accomplishments = db.get_accomplishments_for_week(week_start)
    focus_entries = db.get_weekly_focus(week_start)
    focus_content = focus_entries[-1]["content"] if focus_entries else None

    if not accomplishments and not focus_content:
        await _send(
            bot,
            "📊 *Weekly Summary*\n\n"
            "No accomplishments recorded this week. Log your daily updates so I can track your progress!\n\n"
            "Use /update to add today's accomplishments.",
        )
        return

    # Build Telegram summary message
    work = [a for a in accomplishments if a["category"] == "work"]
    personal = [a for a in accomplishments if a["category"] == "personal"]

    msg = f"📊 *Weekly Summary — {_week_label(week_start)}*\n\n"

    if work:
        msg += "💼 *Work Accomplishments:*\n"
        for entry in work:
            day = date.fromisoformat(entry["date"]).strftime("%a")
            preview = entry["content"][:120] + ("…" if len(entry["content"]) > 120 else "")
            msg += f"• {day}: {preview}\n"
        msg += "\n"

    if personal:
        msg += "🏆 *Personal Accomplishments:*\n"
        for entry in personal:
            day = date.fromisoformat(entry["date"]).strftime("%a")
            preview = entry["content"][:120] + ("…" if len(entry["content"]) > 120 else "")
            msg += f"• {day}: {preview}\n"
        msg += "\n"

    if focus_content:
        msg += f"🎯 *Next Week's Focus:*\n{focus_content}\n\n"

    await _send(bot, msg)

    # Sync to Google Sheets
    await _send(bot, "🔄 Updating your Google Sheet…")
    try:
        url = await _sync_to_sheets_with_ai(bot)
        await _send(
            bot,
            f"✅ Google Sheet updated!\n\n"
            f"📝 [View your weekly accomplishments]({url})",
        )
    except Exception as e:
        logger.error("Google Sheets sync failed: %s", e)
        await _send(
            bot,
            "⚠️ Couldn't update Google Sheets right now. Your data is saved locally.\n"
            "Use /sync to retry when you're ready.",
        )


# ── Command handlers ──────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"👋 *Welcome to Weekly Updates Bot!*\n\n"
        f"Your chat ID is: `{chat_id}`\n"
        f"_(Add this to your .env as TELEGRAM\\_CHAT\\_ID)_\n\n"
        f"I'll message you every day to track your accomplishments, then send you a weekly "
        f"summary on Sunday with a link to your Google Sheet.\n\n"
        f"*Commands:*\n"
        f"• /update — Log today's accomplishments\n"
        f"• /skip — Skip the current question\n"
        f"• /status — This week's progress\n"
        f"• /sync — Push data to Google Sheets now\n"
        f"• /summary — Generate weekly summary now\n",
        parse_mode="Markdown",
    )


async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = date.today().isoformat()
    missed = db.get_missed_dates()
    dates_to_collect = missed + [today]

    if missed:
        missed_fmt = ", ".join(_fmt_date(d) for d in missed)
        await update.message.reply_text(
            f"You have missed updates for: *{missed_fmt}*\n\nLet's catch up!",
            parse_mode="Markdown",
        )

    await _start_collection(context.bot, dates_to_collect)


async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state_data = db.get_state()
    state = state_data.get("state", "idle")

    if state == "idle":
        await update.message.reply_text("Nothing to skip right now. Use /update to start logging!")
        return

    current_date = state_data.get("current_date")
    pending = json.loads(state_data.get("pending_dates", "[]"))

    if state == "collecting_work":
        db.set_state("collecting_personal", current_date=current_date, pending_dates=pending)
        await _prompt_personal(context.bot, current_date)

    elif state == "collecting_personal":
        if pending:
            next_date, *remaining = pending
            await update.message.reply_text(f"Got it. Moving on to {_fmt_date(next_date)}.")
            db.set_state("collecting_work", current_date=next_date, pending_dates=remaining)
            await _prompt_work(context.bot, next_date)
        else:
            db.set_state("collecting_focus")
            await _prompt_focus(context.bot)

    elif state == "collecting_focus":
        db.set_state("idle")
        await update.message.reply_text(
            "✅ All done! Your updates are saved.\n\nI'll remind you again tomorrow. Keep it up! 💪"
        )

    elif state in ("collecting_habit_check", "collecting_habit_reason", "confirming_habit"):
        db.set_state("idle")
        await update.message.reply_text("Skipped. Use /habits to see your habits anytime.")

    elif state == "collecting_later_item":
        db.set_state("idle")
        await update.message.reply_text("Cancelled. Use /later anytime to add a long-term goal.")

    elif state == "collecting_later_date":
        item = state_data.get("later_item_draft", "")
        db.save_later_item(item, "", source="manual")
        db.set_state("idle")
        await update.message.reply_text(
            f"🔭 Saved to your Later list without a date: *{item}*",
            parse_mode="Markdown",
        )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    week_start = _monday_of_week()
    accomplishments = db.get_accomplishments_for_week(week_start)
    focus_entries = db.get_weekly_focus(week_start)

    start = date.fromisoformat(week_start)
    msg = f"📊 *Week of {_week_label(week_start)}*\n\n"

    if not accomplishments:
        msg += "No accomplishments logged yet this week.\n"
    else:
        work_count = sum(1 for a in accomplishments if a["category"] == "work")
        personal_count = sum(1 for a in accomplishments if a["category"] == "personal")
        msg += f"💼 Work entries: {work_count}\n"
        msg += f"🏆 Personal entries: {personal_count}\n\n"

    dates_logged = {a["date"] for a in accomplishments}
    msg += "*Days logged:*\n"
    for i in range(7):
        day = start + timedelta(days=i)
        if day > date.today():
            break
        symbol = "✅" if day.isoformat() in dates_logged else "❌"
        msg += f"{symbol} {day.strftime('%A, %b %d')}\n"

    if focus_entries:
        msg += f"\n🎯 *Next week focus logged:* Yes"
    else:
        msg += f"\n🎯 *Next week focus:* Not yet"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def _sync_to_sheets_with_ai(bot) -> str:
    """
    1. Summarize focus weeks (cached — only re-runs when new entries added).
       Also extracts any later items detected in focus entries.
    2. Organize later items (cached — only re-runs when new items added).
    3. Rebuild both Google Sheet tabs.
    Returns URL.
    """
    from google_sheets import sync_to_sheets
    from ai_summarize import summarize_focus_and_extract_later, organize_later_items

    all_accomplishments = db.get_all_accomplishments()
    all_focus = db.get_all_focus_entries()

    # ── Step 1: Focus summaries ───────────────────────────────────────────────
    focus_by_week = {}
    for entry in all_focus:
        focus_by_week.setdefault(entry["week_start"], []).append(entry["content"])

    focus_summaries = {}   # {week_start: summary_text}
    weeks_to_process = []

    for week_start, entries in focus_by_week.items():
        cached = db.get_cached_summary(week_start)
        if cached and cached["entry_count"] == len(entries):
            focus_summaries[week_start] = cached["summary_text"]
        else:
            weeks_to_process.append((week_start, entries))

    if weeks_to_process:
        await _send(bot, f"🤖 Processing {len(weeks_to_process)} week(s) of focus items…")
        for week_start, entries in weeks_to_process:
            result = summarize_focus_and_extract_later(entries)
            focus_summaries[week_start] = result["next_week_summary"]
            db.save_cached_summary(week_start, result["next_week_summary"], len(entries))

            # Auto-save any later items Claude detected in the focus entries
            for item in result.get("later_items", []):
                db.save_later_item(
                    content=item.get("content", ""),
                    target_date=item.get("target_date", ""),
                    source="auto",
                )

    # ── Step 2: Later items organization ─────────────────────────────────────
    all_later = db.get_all_later_items()
    item_count = len(all_later)
    organized_later = []

    if all_later:
        cached_later = db.get_cached_later_org()
        if cached_later and cached_later["item_count"] == item_count:
            import json as _json
            organized_later = _json.loads(cached_later["groups_json"])
        else:
            await _send(bot, "🗂 Organizing your later items…")
            organized_later = organize_later_items(all_later)
            import json as _json
            db.save_cached_later_org(_json.dumps(organized_later), item_count)

    # ── Step 3: Sync ──────────────────────────────────────────────────────────
    all_habits = db.get_all_active_habits()
    all_habit_logs = db.get_all_habit_logs()

    return sync_to_sheets(all_accomplishments, focus_summaries, organized_later,
                          all_habits, all_habit_logs)


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Syncing to Google Sheets…")
    try:
        all_accomplishments = db.get_all_accomplishments()
        if not all_accomplishments:
            await update.message.reply_text("No data to sync yet. Use /update to log accomplishments first.")
            return

        url = await _sync_to_sheets_with_ai(context.bot)
        await update.message.reply_text(
            f"✅ Synced!\n\n📝 [View Google Sheet]({url})",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Sync failed: %s", e)
        await update.message.reply_text(f"❌ Sync failed: {e}")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await weekly_summary_job(context)


async def work_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick-log work accomplishments for today only, then return to idle."""
    today = date.today().isoformat()
    db.set_state("collecting_work_only", current_date=today, pending_dates=[])
    await _prompt_work(context.bot, today)


async def personal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick-log personal accomplishments for today only, then return to idle."""
    today = date.today().isoformat()
    db.set_state("collecting_personal_only", current_date=today, pending_dates=[])
    await _prompt_personal(context.bot, today)


async def focus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick-log next week's focus, then return to idle."""
    db.set_state("collecting_focus")
    await _prompt_focus(context.bot)


async def _prompt_habit_check(bot, habit: dict):
    await _send(bot,
        f"📌 *Habit Check-in: {habit['name']}*\n\n"
        f"{habit['description']}\n\n"
        f"Did you do it today? Reply *Yes* or *No*\n\n"
        f"_(Type /skip to skip habit check-ins)_",
    )


async def _start_habit_checkins_or_done(bot, update):
    """After focus is collected, check for unlogged habits. If none, go idle."""
    today_str = date.today().isoformat()
    unlogged = db.get_unlogged_habits_for_date(today_str)

    if unlogged:
        first, *rest = unlogged
        db.set_state("collecting_habit_check",
                     temp_data={"current_habit_id": first["id"],
                                "pending_habit_ids": [h["id"] for h in rest]})
        await update.message.reply_text(
            f"Almost done! Let's quickly check in on your habits for today 📌"
        )
        await _prompt_habit_check(bot, first)
    else:
        db.set_state("idle")
        await update.message.reply_text(
            "🎉 *All done!* Accomplishments and focus saved.\n\n"
            "I'll compile your weekly summary this Sunday. Keep crushing it! 💪",
            parse_mode="Markdown",
        )


async def _next_habit_or_done(bot, update, pending_habit_ids: list):
    """Move to next habit check-in, or go idle if none left."""
    if pending_habit_ids:
        next_id, *remaining = pending_habit_ids
        habit = db.get_habit(next_id)
        db.set_state("collecting_habit_check",
                     temp_data={"current_habit_id": next_id, "pending_habit_ids": remaining})
        await _prompt_habit_check(bot, habit)
    else:
        db.set_state("idle")
        await update.message.reply_text(
            "🎉 *All done!* Great work today. Keep it up! 💪",
            parse_mode="Markdown",
        )


async def morning_habit_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """Fires every morning. Sends nudges for any habits scheduled today."""
    bot = context.bot
    today = date.today()
    habits = db.get_active_habits_for_weekday(today.weekday())
    if not habits:
        return
    names = "\n".join(f"• {h['description']}" for h in habits)
    await _send(bot,
        f"🌅 *Good morning! Habit reminders for today:*\n\n{names}\n\nGo get it done! 💪",
        )


async def habit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a new habit using natural language."""
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "🏃 *Add a Habit*\n\n"
            "Describe the habit in plain language. Examples:\n"
            "• `/habit go to the gym for an hour on Tues and Thurs`\n"
            "• `/habit read for 30 minutes every weekday`\n"
            "• `/habit meditate every morning`",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text("Parsing your habit…")
    from ai_summarize import parse_habit
    parsed = parse_habit(text)

    if not parsed:
        await update.message.reply_text(
            "Couldn't parse that habit. Try being more specific, e.g.:\n"
            "`/habit go to the gym for 1 hour on Tuesday and Thursday`",
            parse_mode="Markdown",
        )
        return

    db.set_state("confirming_habit", temp_data=parsed)
    day_names = ", ".join(["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d] for d in parsed["days"])
    await update.message.reply_text(
        f"Here's what I understood:\n\n"
        f"*{parsed['confirmation_text']}*\n"
        f"Days: {day_names}\n\n"
        f"Does that look right? Reply *Yes* to save or *No* to cancel.",
        parse_mode="Markdown",
    )


async def habits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all active habits."""
    habits = db.get_all_active_habits()
    if not habits:
        await update.message.reply_text(
            "No habits set up yet.\n\nUse `/habit [description]` to add one.",
            parse_mode="Markdown",
        )
        return

    day_abbr = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    msg = "📌 *Your Active Habits:*\n\n"
    for h in habits:
        days = ", ".join(day_abbr[d] for d in h["days_of_week"])
        msg += f"• *{h['name']}* — {h['description']}\n  Days: {days}\n\n"
    msg += "Use `/habitstop [habit name]` to deactivate one."
    await update.message.reply_text(msg, parse_mode="Markdown")


async def habitstop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deactivate a habit by name."""
    query = " ".join(context.args).lower() if context.args else ""
    if not query:
        await update.message.reply_text("Usage: `/habitstop gym`", parse_mode="Markdown")
        return
    habits = db.get_all_active_habits()
    match = next((h for h in habits if query in h["name"].lower()), None)
    if not match:
        await update.message.reply_text(f"No active habit matching '{query}'. Use /habits to see your list.")
        return
    db.deactivate_habit(match["id"])
    await update.message.reply_text(f"✅ *{match['name']}* deactivated.", parse_mode="Markdown")


async def later_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log a longer-term goal with a target date."""
    db.set_state("collecting_later_item")
    await update.message.reply_text(
        "🔭 *Later / Long-term Goal*\n\n"
        "What's on your longer-term radar?\n\n"
        "_(Type /skip to cancel)_",
        parse_mode="Markdown",
    )


# ── Message handler (state machine) ──────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    state_data = db.get_state()
    state = state_data.get("state", "idle")

    if state == "idle":
        await update.message.reply_text(
            "Not sure what to do with that!\n\n"
            "Quick log:\n"
            "• /work — add work accomplishments\n"
            "• /personal — add personal accomplishments\n"
            "• /focus — update next week's focus\n"
            "• /update — full daily check-in"
        )
        return

    current_date = state_data.get("current_date")
    pending = json.loads(state_data.get("pending_dates", "[]"))

    if state == "collecting_work_only":
        db.save_accomplishment(current_date, "work", text)
        db.set_state("idle")
        await update.message.reply_text(
            f"💼 Work logged for {_fmt_date(current_date)}!\n\n"
            "Use /personal to log personal accomplishments, or /update for the full check-in."
        )
        return

    elif state == "collecting_personal_only":
        db.save_accomplishment(current_date, "personal", text)
        db.set_state("idle")
        await update.message.reply_text(
            f"🏆 Personal logged for {_fmt_date(current_date)}!\n\n"
            "Use /work to log work accomplishments, or /update for the full check-in."
        )
        return

    elif state == "collecting_work":
        db.save_accomplishment(current_date, "work", text)
        db.set_state("collecting_personal", current_date=current_date, pending_dates=pending)
        await _prompt_personal(context.bot, current_date)

    elif state == "collecting_personal":
        db.save_accomplishment(current_date, "personal", text)

        if pending:
            next_date, *remaining = pending
            await update.message.reply_text(
                f"✅ Saved for {_fmt_date(current_date)}! Now moving to {_fmt_date(next_date)}."
            )
            db.set_state("collecting_work", current_date=next_date, pending_dates=remaining)
            await _prompt_work(context.bot, next_date)
        else:
            await update.message.reply_text(f"✅ Saved for {_fmt_date(current_date)}!")
            db.set_state("collecting_focus")
            await _prompt_focus(context.bot)

    elif state == "collecting_focus":
        next_monday = _next_monday()
        db.save_weekly_focus(next_monday, text)
        await _start_habit_checkins_or_done(context.bot, update)

    elif state == "confirming_habit":
        temp = json.loads(state_data.get("temp_data") or "{}")
        if text.lower() in ["yes", "y", "yep", "yeah", "correct", "looks good", "save"]:
            habit_id = db.save_habit(
                name=temp.get("name", "Habit"),
                description=temp.get("description", ""),
                days_of_week=temp.get("days", []),
            )
            db.set_state("idle")
            day_abbr = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
            days_str = ", ".join(day_abbr[d] for d in temp.get("days", []))
            await update.message.reply_text(
                f"✅ *{temp.get('name')}* saved!\n\n"
                f"I'll send you a morning nudge on {days_str} "
                f"and check in during your daily update. 💪",
                parse_mode="Markdown",
            )
        else:
            db.set_state("idle")
            await update.message.reply_text(
                "No problem! Use `/habit [description]` again with the corrected details.",
                parse_mode="Markdown",
            )

    elif state == "collecting_habit_check":
        temp = json.loads(state_data.get("temp_data") or "{}")
        habit_id = temp.get("current_habit_id")
        pending = temp.get("pending_habit_ids", [])
        today_str = date.today().isoformat()

        if text.lower() in ["yes", "y", "yep", "yeah", "did it", "done", "✅"]:
            db.log_habit(habit_id, today_str, completed=True)
            habit = db.get_habit(habit_id)
            await update.message.reply_text(f"✅ *{habit['name']}* logged!", parse_mode="Markdown")
            await _next_habit_or_done(context.bot, update, pending)
        elif text.lower() in ["no", "n", "nope", "didn't", "missed", "❌"]:
            habit = db.get_habit(habit_id)
            db.set_state("collecting_habit_reason",
                         temp_data={"current_habit_id": habit_id, "pending_habit_ids": pending})
            await update.message.reply_text(
                f"What got in the way of *{habit['name']}* today?\n\n_(Type /skip to skip)_",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text("Just reply *Yes* or *No* 😊", parse_mode="Markdown")

    elif state == "collecting_habit_reason":
        temp = json.loads(state_data.get("temp_data") or "{}")
        habit_id = temp.get("current_habit_id")
        pending = temp.get("pending_habit_ids", [])
        db.log_habit(habit_id, date.today().isoformat(), completed=False, miss_reason=text)
        await update.message.reply_text("📝 Got it, noted.")
        await _next_habit_or_done(context.bot, update, pending)

    elif state == "collecting_later_item":
        db.set_state("collecting_later_date", later_item_draft=text)
        await update.message.reply_text(
            "📅 When are you targeting this?\n\n"
            "_(e.g. 'Q3 2026', 'June', 'end of year', 'no date yet')_\n\n"
            "_(Type /skip to save without a date)_"
        )

    elif state == "collecting_later_date":
        item = state_data.get("later_item_draft", "")
        db.save_later_item(item, text, source="manual")
        db.set_state("idle")
        await update.message.reply_text(
            f"🔭 Saved to your Later list!\n\n"
            f"*{item}* — {text}\n\n"
            "Use /sync anytime to update your Google Sheet.",
            parse_mode="Markdown",
        )


# ── Application factory ───────────────────────────────────────────────────────

def create_application() -> Application:
    tz = pytz.timezone(TIMEZONE)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("update", update_command))
    app.add_handler(CommandHandler("work", work_command))
    app.add_handler(CommandHandler("personal", personal_command))
    app.add_handler(CommandHandler("focus", focus_command))
    app.add_handler(CommandHandler("later", later_command))
    app.add_handler(CommandHandler("habit", habit_command))
    app.add_handler(CommandHandler("habits", habits_command))
    app.add_handler(CommandHandler("habitstop", habitstop_command))
    app.add_handler(CommandHandler("skip", skip_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("sync", sync_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Morning habit reminders
    app.job_queue.run_daily(
        morning_habit_reminder_job,
        time=datetime.time(hour=HABIT_REMINDER_HOUR, minute=HABIT_REMINDER_MINUTE, tzinfo=tz),
        name="morning_habits",
    )

    # Daily prompt
    app.job_queue.run_daily(
        daily_prompt_job,
        time=datetime.time(hour=DAILY_PROMPT_HOUR, minute=DAILY_PROMPT_MINUTE, tzinfo=tz),
        name="daily_prompt",
    )

    # Weekly summary — Sunday only (days=(6,))
    app.job_queue.run_daily(
        weekly_summary_job,
        time=datetime.time(hour=WEEKLY_SUMMARY_HOUR, minute=0, tzinfo=tz),
        days=(6,),
        name="weekly_summary",
    )

    return app
