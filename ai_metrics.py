"""All Claude calls for On Track v2. Model choice is cost-sensitive — this runs many
times per day; do not change MODEL without checking cost."""
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


def _strip_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


_DECODER = json.JSONDecoder()


def _extract_json_object(s: str) -> dict:
    """Pull the first complete JSON object out of a model response.

    Models routinely wrap the answer in ```json fences and/or pad it with prose
    ("...``` \n\n This is a ride receipt"), which plain json.loads rejects with
    "Extra data". Rather than regex the braces (which breaks on a `}` inside a
    string value), walk each `{` and let the real JSON decoder tell us where the
    object ends — raw_decode stops at the close brace and ignores the rest.
    Raises ValueError when no JSON object is present, so the caller's failure
    path (log + default) is unchanged for genuinely unparseable responses."""
    stripped = _strip_fences(s)
    for text in (stripped, s):
        start = text.find("{")
        while start != -1:
            try:
                parsed, _ = _DECODER.raw_decode(text, start)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
            start = text.find("{", start + 1)
    raise ValueError("no JSON object found in response")


def _call_json(prompt: str, max_tokens: int = 300, default=None):
    raw = ""
    try:
        msg = _get_client().messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        return _extract_json_object(raw)
    except Exception as e:
        # `raw` is model output generated from personal content (and, once bank
        # data flows through this same helper, transaction descriptions) — cap
        # what lands in logs rather than dumping the full response.
        logger.error("ai_metrics JSON call failed: %s | raw=%r", e, raw[:40])
        return default if default is not None else {}


def classify_receipt(sender: str, subject: str, snippet: str = "") -> bool:
    """True if this email is a receipt/confirmation for a FOOD DELIVERY ORDER
    (not a ride, promo, refund notice, tip receipt, or account email)."""
    prompt = f"""You classify emails. Is this email a receipt or confirmation for a food
delivery ORDER the user placed (Uber Eats, DoorDash, Grubhub, etc.)?

Promotions, ride receipts, refunds, password resets, and newsletters are NOT orders.
Tip receipts, refund adjustments, and cancellation notices for an EXISTING order
are NOT new orders.

From: {sender}
Subject: {subject}
Preview: {snippet[:200]}

Reply with only JSON: {{"is_order": true|false}}"""
    result = _call_json(prompt, default={"is_order": False})
    return bool(result.get("is_order", False))


def classify_social_event(title: str, description: str, location: str, attendees: list, examples=None) -> dict:
    """Classify a calendar event as social (spending leisure time with other people)
    or not. Returns {"is_social": bool, "confidence": float}.

    `examples` — recent user corrections (see database.get_classification_examples) —
    are folded into the prompt so the model learns from past overrides."""
    example_block = ""
    if examples:
        lines = "\n".join(
            f'- "{e["title"]}" IS {"" if e["user_is_social"] else "NOT "}social' for e in examples
        )
        example_block = f"\nThe user has corrected past classifications:\n{lines}\n"

    prompt = f"""You classify calendar events. "Social" means leisure time spent with other
people: dinners, drinks, parties, dates, hangouts, group activities, weddings.

NOT social: work meetings, appointments (doctor, dentist), errands, solo activities
(gym, haircut), reminders, flights, focus blocks.
{example_block}
Title: {title}
Description: {description[:300]}
Location: {location}
Attendees: {", ".join(attendees) if attendees else "(none listed)"}

Reply with only JSON: {{"is_social": true|false, "confidence": 0.0-1.0}}"""
    result = _call_json(prompt, default={"is_social": False, "confidence": 0.0})

    # Guard confidence coercion: if confidence is non-numeric, default to 0.0
    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "is_social": bool(result.get("is_social", False)),
        "confidence": confidence,
    }


def classify_work_ride(service: str, subject: str, snippet: str = "", examples=None) -> dict:
    """Work vs personal ride. Returns {"is_work": bool, "confidence": float}.

    `examples` — recent user corrections (see database.get_ride_examples) — are
    folded into the prompt so the model learns from past overrides."""
    example_block = ""
    if examples:
        lines = "\n".join(
            f'- "{e["subject"]}" IS {"" if e["user_is_work"] else "NOT "}work' for e in examples
        )
        example_block = f"\nThe user has corrected past classifications:\n{lines}\n"
    prompt = f"""You classify ride-hailing receipts as WORK travel or PERSONAL.

Work rides usually involve airports, hotels, conference venues, out-of-town
addresses, or weekday business hours while travelling. Personal rides are
local trips, nights out, and errands.
{example_block}
Service: {service}
Subject: {subject}
Preview: {snippet[:200]}

Reply with only JSON: {{"is_work": true|false, "confidence": 0.0-1.0}}"""
    result = _call_json(prompt, default={"is_work": False, "confidence": 0.0})
    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {"is_work": bool(result.get("is_work", False)), "confidence": confidence}


def weekly_reflection(card: dict, noticings: list) -> str:
    """2-3 sentence reflection on a completed week. Returns "" on any failure."""
    lines = []
    for m in card["metrics"].values():
        sign = "≤" if m["direction"] == "ceiling" else "≥"
        outcome = "hit" if m["hit"] else "missed"
        lines.append(f"- {m['label']}: {m['count']} (target {sign}{m['target']}) — {outcome}")
    summary = "\n".join(lines)
    patterns = "\n".join(f"- {n}" for n in noticings) if noticings else "- (none)"
    prompt = f"""You write a short weekly reflection for a personal habit tracker.

Week {card['week_start']} to {card['week_end']}:
{summary}

Patterns noticed:
{patterns}

Write 2-3 sentences: honest, warm, specific to the numbers. Address the reader as
"you". No emojis, no bullet points, no advice-column clichés.

Reply with only JSON: {{"reflection": "..."}}"""
    result = _call_json(prompt, max_tokens=300, default={"reflection": ""})
    return str(result.get("reflection") or "")
