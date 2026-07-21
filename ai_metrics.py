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


def _call_json(prompt: str, max_tokens: int = 300, default=None):
    raw = ""
    try:
        msg = _get_client().messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        return json.loads(_strip_fences(raw))
    except Exception as e:
        logger.error("ai_metrics JSON call failed: %s | raw=%r", e, raw)
        return default if default is not None else {}


def classify_receipt(sender: str, subject: str) -> bool:
    """True if this email is a receipt/confirmation for a FOOD DELIVERY ORDER
    (not a ride, promo, refund notice, or account email)."""
    prompt = f"""You classify emails. Is this email a receipt or confirmation for a food
delivery ORDER the user placed (Uber Eats, DoorDash, Grubhub, etc.)?

Promotions, ride receipts, refunds, password resets, and newsletters are NOT orders.

From: {sender}
Subject: {subject}

Reply with only JSON: {{"is_order": true|false}}"""
    result = _call_json(prompt, default={"is_order": False})
    return bool(result.get("is_order", False))


def classify_social_event(title: str, description: str, location: str, attendees: list) -> dict:
    """Classify a calendar event as social (spending leisure time with other people)
    or not. Returns {"is_social": bool, "confidence": float}."""
    prompt = f"""You classify calendar events. "Social" means leisure time spent with other
people: dinners, drinks, parties, dates, hangouts, group activities, weddings.

NOT social: work meetings, appointments (doctor, dentist), errands, solo activities
(gym, haircut), reminders, flights, focus blocks.

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
