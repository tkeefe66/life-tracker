"""Telegram narrative state machine for story review."""
import json
import logging
from typing import Awaitable, Callable

import database as db

logger = logging.getLogger(__name__)


def _temp(state: dict) -> dict:
    """Pull temp_data out of a state row, normalizing string vs dict."""
    t = state.get("temp_data") or {}
    if isinstance(t, str):
        try:
            t = json.loads(t)
        except Exception:
            t = {}
    return t


def _children_of(parent_id: int) -> list:
    """Return child rows (full life_log_entries) ordered by date_start, id."""
    p = db._p()
    with db._cursor() as c:
        c.execute(
            f"SELECT * FROM life_log_entries WHERE parent_id={p} "
            f"ORDER BY date_start, id",
            (parent_id,),
        )
        return [db._unpack_life_log_entry(r) for r in db._rows(c.fetchall())]


def _render_story(parent_id: int) -> str:
    """Build the narrative card for a story (parent + numbered child events)."""
    parent = db.get_life_log_entry(parent_id)
    children = _children_of(parent_id)
    type_label = (parent.get("story_type") or "other").replace("_", " ").upper()
    date_range = parent.get("date_start") or ""
    if parent.get("date_end"):
        date_range = f"{parent['date_start']} → {parent['date_end']}"

    highlights = parent.get("highlights") or []
    h_block = "\n".join(f"• {h}" for h in highlights[:8]) or "(none)"
    if len(highlights) > 8:
        h_block += f"\n…and {len(highlights) - 8} more"

    events_block = "\n".join(
        f"  #{i+1}  {c['date_start']}  {c['description']}"
        for i, c in enumerate(children)
    ) or "(no child events)"

    return (
        f"\U0001f4d6 {type_label}\n\n"
        f"{parent.get('description') or '(no summary)'}\n"
        f"{date_range} · {len(children)} events\n\n"
        f"Highlights:\n{h_block}\n\n"
        f"Events (drop by number):\n{events_block}\n\n"
        f"Reply: yes / edit summary: <text> / drop #N / skip"
    )


async def present_next_story(reply: Callable[[str], Awaitable]):
    """Pop one story off the queue and send its narrative card.

    Reads pending_story_ids from temp_data; pops the first; sets it as
    current_story_id; transitions state to 'story_confirming'.
    If queue is empty, transitions to 'idle' with cleared temp_data.
    """
    state = db.get_state()
    temp = _temp(state)
    queue = temp.get("pending_story_ids") or []
    if not queue:
        await reply("✅ All stories reviewed.")
        db.set_state(state="idle", temp_data={})
        return
    parent_id = queue[0]
    temp["current_story_id"] = parent_id
    temp["pending_story_ids"] = queue[1:]
    db.set_state(state="story_confirming", temp_data=temp)
    await reply(_render_story(parent_id))


async def _advance_queue(reply) -> None:
    """Advance to the next pending story, update state, and send its card.

    Removes the current story from the front of the queue (if present) so
    both the syncstories flow (current removed by present_next_story) and the
    direct-setup flow (current still in queue) work correctly.
    """
    state = db.get_state()
    temp = _temp(state)
    current_id = temp.get("current_story_id")
    queue = list(temp.get("pending_story_ids") or [])
    # Remove current from queue head if it's still there (setup via _setup_queue)
    if queue and queue[0] == current_id:
        queue = queue[1:]
    if queue:
        next_id = queue[0]
        temp["current_story_id"] = next_id
        temp["pending_story_ids"] = queue[1:]
        db.set_state(state="story_confirming", temp_data=temp)
        await reply(_render_story(next_id))
    else:
        db.set_state(state="idle", temp_data={})
        await reply("✅ All stories reviewed.")


async def handle_story_confirming(text: str, reply):
    """User reply during story_confirming. Handles yes / edit summary / skip;
    drop #N is stubbed (Task 15 wires it)."""
    text_l = (text or "").strip().lower()
    state = db.get_state()
    temp = _temp(state)
    sid = temp.get("current_story_id")
    if not sid:
        await reply("No active story. Run /syncstories to start.")
        return

    if text_l == "yes":
        db.set_state(state="story_why_mattered", temp_data=temp)
        await reply("Why did this matter? (one sentence)")
        return

    if text_l == "skip":
        db.dismiss_story(sid)
        await _advance_queue(reply)
        return

    if text_l.startswith("edit summary:"):
        new_summary = text.split(":", 1)[1].strip()
        db.update_story_metadata(sid, summary=new_summary)
        await reply(_render_story(sid))
        return

    if text_l.startswith("drop #"):
        try:
            n = int(text_l.split("#", 1)[1].strip().split()[0])
        except (ValueError, IndexError):
            await reply("Reply: yes / edit summary: <text> / drop #N / skip")
            return
        children = _children_of(sid)
        if n < 1 or n > len(children):
            await reply(
                f"Story only has {len(children)} events; valid drop targets "
                f"are #1–{len(children)}."
            )
            return
        child_id = children[n - 1]["id"]
        db.drop_event_from_story(child_id)
        await reply(f"✓ Dropped event #{n}. Story is now:")
        await reply(_render_story(sid))
        return

    await reply("Reply: yes / edit summary: <text> / drop #N / skip")


async def handle_story_why_mattered(text: str, reply):
    """User's free-text answer for why this story mattered."""
    state = db.get_state()
    temp = _temp(state)
    sid = temp.get("current_story_id")
    if not sid:
        await reply("No active story. Run /syncstories to start.")
        return
    db.update_story_metadata(sid, why_mattered=text.strip())
    parent = db.get_life_log_entry(sid)
    questions = (parent.get("extras") or {}).get("_suggested_extras_questions") or []
    if not questions:
        # No optional follow-ups — confirm and advance
        db.confirm_story(sid)
        await _advance_queue(reply)
        return
    db.set_state(state="story_extras_optin", temp_data=temp)
    qlist = "\n".join(f"  • {q}" for q in questions[:3])
    await reply(
        f"\U0001f4cc Want to add more details?\n{qlist}\n\nReply yes to answer them, or skip."
    )


async def handle_story_extras_optin(text: str, reply):
    """yes → start Q&A loop; skip → confirm + advance."""
    state = db.get_state()
    temp = _temp(state)
    sid = temp.get("current_story_id")
    if not sid:
        await reply("No active story.")
        return

    text_l = (text or "").strip().lower()
    if text_l in ("skip", "no", "n"):
        db.confirm_story(sid)
        await _advance_queue(reply)
        return

    if text_l in ("yes", "y", "ok"):
        parent = db.get_life_log_entry(sid)
        questions = (parent.get("extras") or {}).get(
            "_suggested_extras_questions"
        ) or []
        if not questions:
            db.confirm_story(sid)
            await _advance_queue(reply)
            return
        temp["extras_qa_remaining"] = list(questions[:3])
        db.set_state(state="story_extras_qa", temp_data=temp)
        await reply(temp["extras_qa_remaining"][0])
        return

    await reply("Reply yes to answer the optional details, or skip to move on.")


async def handle_story_extras_qa(text: str, reply):
    """One round of structured Q&A; loops until questions are exhausted."""
    from ai_life_log import parse_extras_answer

    state = db.get_state()
    temp = _temp(state)
    sid = temp.get("current_story_id")
    questions = temp.get("extras_qa_remaining") or []
    if not sid or not questions:
        await reply("No active question.")
        return

    current_q = questions[0]
    parent = db.get_life_log_entry(sid)
    parsed = parse_extras_answer(parent.get("story_type") or "other", current_q, text)

    # Merge parsed into existing extras, removing the internal questions key
    existing = parent.get("extras") or {}
    existing.pop("_suggested_extras_questions", None)
    existing.update(parsed)
    db.update_story_metadata(sid, extras=existing)

    remaining = questions[1:]
    if remaining:
        temp["extras_qa_remaining"] = remaining
        db.set_state(state="story_extras_qa", temp_data=temp)
        await reply(remaining[0])
        return

    # Done with Q&A — confirm and advance
    temp.pop("extras_qa_remaining", None)
    db.confirm_story(sid)
    await _advance_queue(reply)
