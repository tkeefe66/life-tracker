"""All Claude calls for the Life Log feature.

Keeping these separate from ai_summarize.py preserves the existing
weekly-accomplishments AI logic untouched while we build the Life Log.
ai_summarize.py becomes deprecated once the cutover is complete.
"""
import json
import logging
import re

import anthropic

from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

# Recurring birthdays don't belong in the Life Log corpus — they swamp
# real memoir events. A future "Birthdays" tab will let the user opt in
# to tracking specific people's birthdays. For now: filter at ingest.
_BIRTHDAY_RE = re.compile(r"\b(birthday|bday|b-day)\b|🎂", re.IGNORECASE)

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _call_raw(prompt: str, max_tokens: int = 800) -> str:
    msg = _get_client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _strip_fences(s: str) -> str:
    """Remove markdown code fences if present."""
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _call_json(prompt: str, max_tokens: int = 800, default=None):
    """Call Claude, expect JSON, return parsed dict/list. Returns `default` on parse failure."""
    raw = ""
    try:
        raw = _call_raw(prompt, max_tokens=max_tokens)
        return json.loads(_strip_fences(raw))
    except Exception as e:
        logger.error("ai_life_log JSON parse failed: %s | raw=%r", e, raw)
        return default if default is not None else {}


def propose_from_calendar_event(
    title: str,
    start: str,
    end,
    attendees: list,
    description: str,
    location: str,
    active_categories: list,
) -> dict:
    """
    Classify a calendar event for Life Log promotion.

    Returns:
        {
          "confidence": "high" | "matched" | "maybe" | "skip",
          "categories": [str],     # subset of active_categories, [] if skip
          "description": str,      # cleaned event description for the entry
          "location": str,         # extracted location
          "people": [str],         # extracted person names
          "reason": str            # short human-readable reason
        }
    """
    if title and _BIRTHDAY_RE.search(title):
        return {
            "confidence": "skip", "categories": [], "description": "",
            "location": "", "people": [],
            "reason": "Birthday — skipped (future Birthdays tab will opt-in).",
        }

    cats_str = ", ".join(active_categories)
    attendees_str = ", ".join(attendees) if attendees else "none"

    prompt = f"""You are filtering calendar events for a personal Life Log — a 30-year memoir
of memorable moments. Most calendar events (meetings, dentist, standups) are NOT memoir-worthy.
Only events that someone might want to remember in 30 years should be promoted.

Categories available: {cats_str}

Confidence levels:
- "high": multi-day trips, named events matching strong category keywords (wedding, concert,
  bachelor party, vacation), out-of-town travel — propose immediately
- "matched": single events that clearly fit a category but lower stakes (e.g. "Megan dinner"
  → Relationship; "Ski Killington Saturday" → Skiing) — propose day-after
- "maybe": might be memorable but unsure — batch into Sunday digest
- "skip": work meetings, recurring routines, doctor appointments, anything not memoir-worthy

Event:
- Title: {title}
- Start: {start}
- End: {end or "(none)"}
- Attendees: {attendees_str}
- Location: {location or "(none)"}
- Description: {description or "(none)"}

Return ONLY a JSON object — no markdown fences, no explanation:
{{
  "confidence": "high" | "matched" | "maybe" | "skip",
  "categories": ["one or more from the active list"],
  "description": "concise one-line memoir-style description",
  "location": "extracted location or empty string",
  "people": ["names of people involved beyond just attendees, if mentioned"],
  "reason": "one short sentence justifying the confidence"
}}

Rules:
- If confidence is "skip", categories MUST be [].
- description should read like a memoir entry, not the raw calendar title.
  Example: "Trip to Vermont with Mom and Dad" not "Vermont Trip".
- people: extract names from title/description/attendees. Strip emails — just first names
  unless the title uses last names.
"""

    return _call_json(prompt, max_tokens=500, default={
        "confidence": "skip", "categories": [], "description": "",
        "location": "", "people": [], "reason": "AI parse failed",
    })


def parse_log_command(
    text: str,
    today: str,
    active_categories: list,
    correction=None,
) -> dict:
    """
    Parse a /log command into a structured Life Log entry.

    Returns:
        {
          "categories": [str],
          "description": str,
          "location": str | None,
          "date_start": str (YYYY-MM-DD),
          "date_end": str | None (YYYY-MM-DD),
          "people": [str],
          "questions": [str]   # ambiguities to surface to user
        }
    """
    cats_str = ", ".join(active_categories)
    correction_block = (
        f"\n\nThe user provided a correction to your previous interpretation:\n"
        f'"{correction}"\nRevise accordingly.'
        if correction else ""
    )

    prompt = f"""Today is {today}. The user typed a /log command for their personal Life Log
(a 30-year memoir of memorable life events).

Parse it into ONE Life Log entry. Extract people, location, date(s), and pick 1-3 categories.

Available categories: {cats_str}

Return ONLY a JSON object — no markdown fences:
{{
  "categories": ["one or more from the list above"],
  "description": "short memoir-style description (5-15 words)",
  "location": "place or null",
  "date_start": "YYYY-MM-DD",
  "date_end": "YYYY-MM-DD or null (only for multi-day events)",
  "people": ["first names mentioned"],
  "questions": ["only if you genuinely cannot determine something important"],
  "relationship_event": null
}}

Rules:
- "tonight" / "today" → date_start = {today}
- "yesterday" → day before {today}
- Multi-day phrasing ("over the weekend", "for a week") → use date_end
- People: strip honorifics, use first names unless full name is given
- description: memoir voice, not action-log voice. "Trip to Vegas with Sprink" beats "Vegas trip"
- If you cannot determine the category confidently, leave categories empty and add a question
- Don't invent details not in the text
- If the message indicates ending a romantic relationship ("broke up", "ended things", "split up"),
  set "relationship_event" to {{"action": "end", "person": "X"}}.
- If it indicates starting a new dating relationship ("started dating X", "official with X", "we're official"),
  set "relationship_event" to {{"action": "start", "person": "X"}}.
- Otherwise, set "relationship_event" to null.

Message: "{text}"{correction_block}
"""

    return _call_json(prompt, max_tokens=500, default={
        "categories": [], "description": text[:100], "location": None,
        "date_start": today, "date_end": None, "people": [], "questions": [],
        "relationship_event": None,
    })


def recommend_category_changes(
    category_usage: list,
    recent_descriptions: list,
) -> dict:
    """
    Periodic review: suggest merges, drops, or new categories based on usage patterns.

    Returns:
        {"recommendations": [
            {"action": "drop"|"merge"|"add", ... }
        ]}
    """
    usage_str = "\n".join(f"- {c['name']}: {c['usage_count']} entries" for c in category_usage)
    desc_str = "\n".join(f"- {d}" for d in recent_descriptions[:50])

    prompt = f"""Review the user's Life Log category usage and recent entries.
Recommend changes to the category list — drops, merges, or new additions.

Current categories with usage counts:
{usage_str}

Recent entry descriptions (sample):
{desc_str}

Return ONLY a JSON object:
{{
  "recommendations": [
    {{"action": "drop", "name": "X", "reason": "why"}},
    {{"action": "merge", "from": "X", "into": "Y", "reason": "why"}},
    {{"action": "add", "name": "X", "reason": "why"}}
  ]
}}

Rules:
- Only recommend dropping if usage is 0 over a long period
- Only recommend merging if there is clear conceptual overlap
- Only recommend adding if 5+ entries in recent descriptions don't fit existing categories
- It's fine to return an empty list if no changes are warranted
"""

    return _call_json(prompt, max_tokens=600, default={"recommendations": []})


def extract_entry_from_existing_text(
    original_category: str,
    original_description: str,
    active_categories: list,
) -> dict:
    """
    Used during one-time spreadsheet backfill. The user's existing sheet has
    free-text categories like "Wedding + Vacation" and descriptions packed with
    people/places. Extract structured data.

    Returns:
        {
          "categories": [str],
          "description": str,    # cleaned-up description
          "location": str | None,
          "people": [str],
        }
    """
    cats_str = ", ".join(active_categories)

    prompt = f"""Extract structured Life Log data from a spreadsheet row.

Original category text: "{original_category}"
Description: "{original_description}"

Available structured categories: {cats_str}

Return ONLY a JSON object:
{{
  "categories": ["matching categories from the available list — pick 1-3"],
  "description": "cleaned description (keep the user's voice, just fix obvious issues)",
  "location": "extracted location or null",
  "people": ["names mentioned in the description"]
}}

Rules:
- Map original category text to available categories. "Wedding + Vacation" → both.
  "Outdoors" stays as Outdoors. Unknown → closest match or "Life Event" as fallback.
- Don't editorialize the description — preserve the user's words.
- People: extract names. "Mom and Dad" → ["Mom", "Dad"]. "with Sprink/Emily" → ["Sprink", "Emily"].
"""

    return _call_json(prompt, max_tokens=400, default={
        "categories": [], "description": original_description,
        "location": None, "people": [],
    })


SEEDED_STORY_TYPES = (
    "trip", "interview_cycle", "conference", "holiday_weekend",
    "dating_arc", "project_milestone", "family_visit", "other",
)


def cluster_into_story(events: list, active_categories: list) -> dict:
    """Classify a date-proximity cluster into a story candidate.

    Input events are dicts with id, date_start, title, description, location.
    Returns:
        {
          "story_type": str,
          "summary": str,
          "highlights": [str],          # ordered, aligned with event_id_refs
          "event_id_refs": [int],       # which child event each highlight came from
          "suggested_extras_questions": [str],
          "location": str,
        }

    On AI parse failure, returns a "other"-type singleton story spanning all
    input events (no highlights), so the caller can still proceed.
    """
    types_str = ", ".join(SEEDED_STORY_TYPES)
    cats_str = ", ".join(active_categories)
    events_str = "\n".join(
        f"  - id={e['id']} | {e['date_start']} | "
        f"{e.get('title') or e.get('description') or '(no title)'} | "
        f"loc={e.get('location') or '(none)'}"
        for e in events
    )

    prompt = f"""You are classifying a tightly-clustered group of calendar events
as a single "story" for a personal Life Log (a 30-year memoir).

Seeded story types (you may invent a new type if none of these fit, but prefer these):
{types_str}

Active Life Log categories: {cats_str}

Events in this cluster:
{events_str}

Return ONLY a JSON object — no markdown fences:
{{
  "story_type": "one of the seeded types or a new lowercase_with_underscores name",
  "summary": "one-line memoir-style summary, 5-15 words",
  "highlights": ["short bullet 1", "short bullet 2", ...],
  "event_id_refs": [1, 2, ...],
  "suggested_extras_questions": ["question to capture trip details", ...],
  "location": "primary location or empty string"
}}

CRITICAL rules:
- highlights[i] MUST describe event with id=event_id_refs[i]. Same length, aligned by index.
- Do NOT invent details not present in the event titles or descriptions.
- If an event title looks like a flight (e.g. "JFK->BTV", "AA 1234"), include it as a highlight.
- Keep summary tight (memoir-style, not the raw calendar titles).
- 1-3 suggested_extras_questions, type-specific (e.g. for trip: travel mode, who came; for interview_cycle: outcome, role).
"""

    cluster_event_ids = [e["id"] for e in events]
    fallback = {
        "story_type": "other",
        "summary": (events[0].get("title") or events[0].get("description") or "(untitled)")
                   if events else "(empty)",
        "highlights": [],
        "event_id_refs": cluster_event_ids,
        "suggested_extras_questions": [],
        "location": (events[0].get("location") or "") if events else "",
    }
    return _call_json(prompt, max_tokens=800, default=fallback)
