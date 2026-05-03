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


_YES = {"yes", "y", "yep", "yeah", "save", "ok", "looks good"}
_NO = {"no", "n", "cancel", "stop", "nevermind"}


def _link_or_create_people(entry_id: int, names: list, date_start: str) -> list:
    """Find or create people, link to entry, return new (just-created) people only."""
    new_people = []
    for raw_name in names:
        name = raw_name.strip()
        if not name:
            continue
        existing = db.find_person_by_name(name)
        if existing:
            db.link_entry_to_people(entry_id, [existing["id"]])
            db.update_person_last_seen(existing["id"], date_start)
        else:
            pid = db.save_person(
                name=name, aliases=[], relationship_type=None,
                first_seen=date_start, notes=None,
            )
            db.link_entry_to_people(entry_id, [pid])
            new_people.append(db.get_person(pid))
    return new_people


async def handle_confirm_response(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """
    Called by the main message dispatcher when state is lifelog_confirming.
    Returns True if the message was handled.
    """
    state_data = db.get_state()
    temp = json.loads(state_data.get("temp_data") or "{}")
    parsed = temp.get("parsed", {})
    text_lc = text.strip().lower()

    if text_lc in _YES:
        if not parsed.get("categories"):
            await update.message.reply_text(
                "Can't save — no category was determined. Try `/log` again with more detail.",
            )
            db.set_state("idle")
            return True

        entry_id = db.save_life_log_entry(
            date_start=parsed["date_start"],
            date_end=parsed.get("date_end"),
            categories=parsed["categories"],
            description=parsed["description"],
            location=parsed.get("location"),
            notes=None,
            status="confirmed",
            source="manual",
            source_id=None,
        )
        for cat in parsed["categories"]:
            db.increment_category_usage(cat)

        new_people = _link_or_create_people(
            entry_id, parsed.get("people", []), parsed["date_start"]
        )

        # Fire-and-forget sync (best-effort; ignores errors)
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

        rel_event = parsed.get("relationship_event")
        if rel_event and rel_event.get("person"):
            person = db.find_person_by_name(rel_event["person"])
            if person:
                action = rel_event.get("action")
                if action == "end":
                    db.set_person_relationship_status(
                        person["id"], "ended", end_date=parsed["date_start"],
                    )
                elif action == "start":
                    from database import _cursor, _p as _ph
                    with _cursor(write=True) as c:
                        c.execute(
                            f"UPDATE people SET relationship_type='dating', "
                            f"start_date={_ph()}, status='active' WHERE id={_ph()}",
                            (parsed["date_start"], person["id"]),
                        )

        await update.message.reply_text(
            f"✅ Saved!\n\n📝 *{parsed['description']}*",
            parse_mode="Markdown",
        )

        # If new people were created, kick off onboarding for the first one
        if new_people:
            first = new_people[0]
            remaining_ids = [p["id"] for p in new_people[1:]]
            db.set_state(
                "lifelog_new_person",
                temp_data={"current_person_id": first["id"], "pending_person_ids": remaining_ids},
            )
            await _ask_relationship_type(update, first)
        else:
            db.set_state("idle")

        return True

    if text_lc in _NO:
        db.set_state("idle")
        await update.message.reply_text("Cancelled — nothing was saved.")
        return True

    # Treat as a correction — re-parse with feedback
    logger.info("lifelog correction received: %r", text)
    await update.message.reply_text("Got it — re-reading with your correction… 🤔")
    active_cats = [c["name"] for c in db.get_active_categories()]
    new_parsed = parse_log_command(
        temp.get("original_text", ""),
        today=_today_str(),
        active_categories=active_cats,
        correction=text,
    )
    db.set_state(
        "lifelog_confirming",
        temp_data={"original_text": temp.get("original_text", ""), "parsed": new_parsed},
    )
    await update.message.reply_text(_format_preview(new_parsed), parse_mode="Markdown")
    return True


async def _ask_relationship_type(update: Update, person: dict):
    """Onboard a newly-created person — ask relationship type."""
    await update.message.reply_text(
        f"👤 First time logging *{person['name']}*. What's the relationship?\n\n"
        "Reply with one:\n"
        "• `family`\n"
        "• `friend`\n"
        "• `dating prospect`\n"
        "• `dating`\n"
        "• `colleague`\n"
        "• `acquaintance`\n"
        "• `other`\n\n"
        "_(Type /skip to leave blank for now)_",
        parse_mode="Markdown",
    )


_RELATIONSHIP_TYPES = {
    "family": "family",
    "friend": "friend",
    "dating prospect": "dating_prospect",
    "dating": "dating",
    "colleague": "colleague",
    "acquaintance": "acquaintance",
    "other": "other",
}


async def handle_new_person_response(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Handle the relationship-type reply during person onboarding."""
    state_data = db.get_state()
    temp = json.loads(state_data.get("temp_data") or "{}")
    person_id = temp.get("current_person_id")
    pending_ids = temp.get("pending_person_ids", [])

    text_lc = text.strip().lower()
    if text_lc == "/skip":
        rel_type = None
    elif text_lc in _RELATIONSHIP_TYPES:
        rel_type = _RELATIONSHIP_TYPES[text_lc]
    else:
        await update.message.reply_text(
            "Pick one: family, friend, dating prospect, dating, colleague, acquaintance, other "
            "(or /skip)."
        )
        return True

    if rel_type:
        # Update relationship_type directly via raw SQL (no dedicated DB function for this field).
        # Pattern matches how other handlers in the codebase reach into _cursor/_p.
        from database import _cursor, _p as _ph
        with _cursor(write=True) as c:
            c.execute(
                f"UPDATE people SET relationship_type={_ph()} WHERE id={_ph()}",
                (rel_type, person_id),
            )

    person = db.get_person(person_id)
    label = rel_type or "(no type)"
    await update.message.reply_text(f"✅ {person['name']} → {label}")

    if pending_ids:
        next_id, *rest = pending_ids
        next_person = db.get_person(next_id)
        db.set_state(
            "lifelog_new_person",
            temp_data={"current_person_id": next_id, "pending_person_ids": rest},
        )
        await _ask_relationship_type(update, next_person)
    else:
        db.set_state("idle")
        await update.message.reply_text("All done! 🎉")

    return True
