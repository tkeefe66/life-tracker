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
