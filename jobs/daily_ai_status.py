"""
daily_ai_status — runs once per day (after daily_calendar).

For each Later item without an AI assessment, calls the AI to:
  - Suggest a status (pending / habit / logged)
  - Add brief notes on why

Only processes net-new items (ai_status IS NULL). This keeps API costs low
and makes it easy to validate AI judgment by comparing ai_status vs user status.
"""

import json
import logging

import database as db
from ai_summarize import _call

logger = logging.getLogger(__name__)

_BATCH_PROMPT = """\
You are reviewing a list of calendar events and goals that someone has logged.
For each item, assess:
1. status — one of:
   - "pending"   → needs attention, no action taken yet
   - "habit"     → looks like a recurring commitment (would make a good habit)
   - "logged"    → already completed or past event, probably already tracked
2. notes — one short sentence explaining your reasoning

Return ONLY a JSON array — no markdown fences, no extra text:
[
  {{"id": <id>, "ai_status": "<status>", "ai_notes": "<one sentence>"}},
  ...
]

Items:
{items_text}"""


def run_daily_ai_status():
    """Synchronous — called from bot job wrapper."""
    pending = db.get_later_items_pending_ai()
    if not pending:
        logger.info("daily_ai_status: nothing to assess")
        return 0

    items_text = "\n".join(
        f"- id={item['id']} | {item['content']} | target: {item.get('target_date') or 'unset'} | source: {item.get('source', 'manual')}"
        for item in pending
    )

    prompt = _BATCH_PROMPT.format(items_text=items_text)

    try:
        raw = _call(prompt, max_tokens=800)
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        results = json.loads(raw)
    except Exception as e:
        logger.error("daily_ai_status AI call failed: %s", e)
        return 0

    updated = 0
    for result in results:
        try:
            db.update_later_item_ai(
                item_id=result["id"],
                ai_status=result.get("ai_status", "pending"),
                ai_notes=result.get("ai_notes", ""),
            )
            updated += 1
        except Exception as e:
            logger.error("Failed to update item %s: %s", result.get("id"), e)

    logger.info("daily_ai_status: assessed %d items", updated)
    return updated
