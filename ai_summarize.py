import json
import logging
import re
from datetime import datetime, date
import anthropic
from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _call(prompt: str, max_tokens: int = 600) -> str:
    message = _get_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


# ── Focus summarization + later item extraction (one API call) ────────────────

def summarize_focus_and_extract_later(raw_entries: list) -> dict:
    """
    Reads all raw focus entries for a week. Returns:
    {
        "next_week_summary": str,   — cleaned, grouped, deduplicated next-week items
        "later_items": [{"content": str, "target_date": str}, ...]
    }
    One API call handles both tasks.
    """
    if not raw_entries:
        return {"next_week_summary": "", "later_items": []}

    combined = "\n\n---\n\n".join(
        f"Entry {i+1}:\n{e}" for i, e in enumerate(raw_entries)
    )

    prompt = f"""You are helping someone organize their weekly priorities from raw notes.

Some items are for NEXT WEEK. Others mention a specific future timeframe beyond next week
(e.g. "by Q3", "in June", "next month", "end of year", "in 3 months").

Your tasks:
1. Separate items into: (a) next-week priorities, (b) longer-term items with a future timeframe
2. For next-week items: remove duplicates, merge related items, group by theme, keep it scannable
3. For longer-term items: extract a clean description and the target timeframe

Return ONLY a JSON object — no markdown fences, no explanation:
{{
  "next_week_summary": "**Theme Name**\\n• item\\n• item\\n\\n**Theme Name**\\n• item",
  "later_items": [
    {{"content": "item description", "target_date": "Q3 2026"}},
    ...
  ]
}}

Rules:
- If no later items exist, return an empty array for later_items
- Preserve the person's voice — don't over-formalize
- If a timeframe is vague ("soon", "eventually"), treat it as next-week
- Keep next_week_summary brief — this is a Monday morning glance

Raw entries:
{combined}"""

    try:
        raw = _call(prompt, max_tokens=600)
        # Strip any accidental markdown fences
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(raw)
        return {
            "next_week_summary": result.get("next_week_summary", ""),
            "later_items": result.get("later_items", []),
        }
    except Exception as e:
        logger.error("Focus summarization failed: %s", e)
        # Fallback: treat everything as next-week, no later items
        return {"next_week_summary": "\n".join(f"• {e}" for e in raw_entries), "later_items": []}


# ── Habit parsing ────────────────────────────────────────────────────────────

def parse_habit(text: str) -> dict:
    """
    Parse a natural language habit description into structured data.
    Returns:
    {
        "name": str,               short name (e.g. "Gym")
        "description": str,        full description (e.g. "Go to the gym for 1 hour")
        "days": [int, ...],        0=Mon, 1=Tue, ..., 6=Sun
        "confirmation_text": str   human-readable summary for user to confirm
    }
    """
    prompt = f"""Parse this habit description into structured data.

Description: "{text}"

Days of week mapping: Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5, Sunday=6

Return ONLY a JSON object — no markdown fences, no explanation:
{{
  "name": "short habit name (2-4 words)",
  "description": "clear one-line description of what to do",
  "days": [list of integers for scheduled days],
  "confirmation_text": "human-friendly summary, e.g. 'Gym (1 hour) every Tuesday & Thursday'"
}}

If specific days are not mentioned, pick sensible defaults based on frequency:
- "twice a week" or "two days a week" → pick Tuesday & Thursday [1, 3]
- "three times a week" → pick Monday, Wednesday, Friday [0, 2, 4]
- "once a week" → pick Monday [0]
- "on weekends" → [5, 6]

Examples:
- "go to gym for an hour on tues and thurs" → days: [1, 3]
- "go to the gym two days a week" → days: [1, 3]
- "read for 30 minutes every weekday" → days: [0, 1, 2, 3, 4]
- "meditate every morning" → days: [0, 1, 2, 3, 4, 5, 6]
- "call mom every sunday" → days: [6]"""

    raw = ""
    try:
        raw = _call(prompt, max_tokens=200)
        logger.info("Habit parse raw response: %r", raw)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            raise ValueError("No JSON object found in response")
        return json.loads(match.group())
    except Exception as e:
        logger.error("Habit parsing failed: %s | raw: %r", e, raw)
        return None


# ── Later items organization ──────────────────────────────────────────────────

def organize_later_items(items: list) -> list:
    """
    Takes all later items [{content, target_date, ...}] from the DB.
    Returns grouped list: [{"theme": str, "items": [{"content": str, "target_date": str}]}]
    Deduplicates and merges similar items.
    """
    if not items:
        return []

    def _date_label(item):
        start = item.get("target_date") or ""
        end = item.get("end_date") or ""
        if end and end != start:
            return f"{start} – {end}"
        return start or "no date"

    items_text = "\n".join(
        f"- {item['content']} | {_date_label(item)}"
        for item in items
    )

    prompt = f"""You are organizing someone's longer-term goals and focus areas.

Below is a list of items with target dates or date ranges. Some may be duplicates or near-duplicates.

Your tasks:
1. Remove exact and near-duplicate items (keep the most specific version)
2. Consolidate logistics/sub-components of the same trip or event into a SINGLE item.
   For example: "Flight to London", "Hotel in London", "Dinner reservation London" → one item: "Trip to London"
   Use the broadest date range across the merged items.
3. Group remaining items under short, intuitive theme headings (e.g. "Travel", "Work Projects", "Personal Goals")
4. Within each group, sort by target date (soonest first)

Key rule: prefer fewer, higher-level items over many granular logistics. Details belong in weekly accomplishments, not here.

Return ONLY a JSON array — no markdown fences, no explanation:
[
  {{
    "theme": "Theme Name",
    "items": [
      {{"content": "clean item description", "target_date": "target timeframe", "end_date": "end date or empty string"}},
      ...
    ]
  }},
  ...
]

Items:
{items_text}"""

    # Build a lookup of extra fields (status, ai_status, ai_notes) by content
    # so we can reattach them after the AI reorganizes
    extra_by_content = {
        item["content"]: {
            k: v.isoformat() if isinstance(v, datetime) else v
            for k, v in item.items()
            if k not in ("content", "target_date", "end_date")
        }
        for item in items
    }

    try:
        raw = _call(prompt, max_tokens=600)
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        groups = json.loads(raw)
        # Merge extra fields back onto each item
        for group in groups:
            for item in group.get("items", []):
                extra = extra_by_content.get(item.get("content"), {})
                item.update(extra)
        return groups
    except Exception as e:
        logger.error("Later items organization failed: %s", e)
        # Fallback: one group with all items
        return [{"theme": "Goals", "items": [
            {
                "content": item["content"],
                "target_date": item.get("target_date", ""),
                "status": item.get("status", "pending"),
                "ai_status": item.get("ai_status", ""),
                "ai_notes": item.get("ai_notes", ""),
            }
            for item in items
        ]}]


# ── Free-form message routing ─────────────────────────────────────────────────

def parse_freeform_message(text: str, today_str: str) -> dict:
    """
    Parse a free-form message and extract structured entries.
    Returns:
    {
        "entries": [
            {"type": "work",     "content": str, "date": "YYYY-MM-DD"},
            {"type": "personal", "content": str, "date": "YYYY-MM-DD"},
            {"type": "focus",    "content": str, "date": null},
            {"type": "later",    "content": str, "target_date": str}
        ]
    }
    Returns {"entries": []} if the message can't be classified.
    """
    prompt = f"""Today is {today_str}. Someone sent you a free-form message to log their day.

Your job: extract any loggable entries from the message. Each entry is one of:
- "work": work accomplishments (things they did at work, professional tasks, meetings, deliverables)
- "personal": personal accomplishments (gym, health, family, personal projects, hobbies)
- "focus": what they plan to focus on next week (priorities, goals)
- "later": a longer-term goal or plan with a specific future timeframe (e.g. "by Q3", "in June", "next year")

Rules:
- A single message may contain multiple types — extract all of them
- For work/personal entries, the date is almost always today ({today_str}) unless the message says "yesterday" or a specific day
- If the message mentions "yesterday", use the day before today
- For focus entries, date is null (they apply to next week)
- For later items, extract the target timeframe as a string (e.g. "Q3 2026", "June 2026")
- If nothing is loggable (e.g. a greeting, a question), return an empty entries array
- Keep content concise but complete — preserve the person's voice

Return ONLY a JSON object — no markdown fences, no explanation:
{{
  "entries": [
    {{"type": "work", "content": "...", "date": "{today_str}"}},
    {{"type": "personal", "content": "...", "date": "{today_str}"}},
    {{"type": "focus", "content": "...", "date": null}},
    {{"type": "later", "content": "...", "target_date": "Q3 2026"}}
  ]
}}

Message: "{text}"
"""

    try:
        raw = _call(prompt, max_tokens=400)
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(raw)
        return result
    except Exception as e:
        logger.error("Free-form message parsing failed: %s", e)
        return {"entries": []}
