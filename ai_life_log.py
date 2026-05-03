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
