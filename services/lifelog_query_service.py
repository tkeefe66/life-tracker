"""Natural-language query layer using Claude's tool-use API."""
import json
import logging

import anthropic

import database as db
from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

_TOOLS = [
    {
        "name": "find_person",
        "description": "Find a person by name or alias.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "get_entries_for_person",
        "description": "All Life Log entries linked to a person, oldest first.",
        "input_schema": {
            "type": "object",
            "properties": {"person_id": {"type": "integer"}},
            "required": ["person_id"],
        },
    },
    {
        "name": "list_all_people",
        "description": "List all people with their last_seen dates.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_entries_in_range",
        "description": "All Life Log entries in a date range (inclusive).",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "get_entries_by_category",
        "description": "All Life Log entries that include a given category.",
        "input_schema": {
            "type": "object",
            "properties": {"category": {"type": "string"}},
            "required": ["category"],
        },
    },
]


def _tool_dispatch(name: str, params: dict) -> str:
    try:
        if name == "find_person":
            p = db.find_person_by_name(params["name"])
            return json.dumps(p, default=str) if p else "null"
        if name == "get_entries_for_person":
            entries = db.get_entries_for_person(params["person_id"])
            return json.dumps(entries, default=str)
        if name == "list_all_people":
            people = db.get_all_people()
            return json.dumps(people, default=str)
        if name == "get_entries_in_range":
            entries = db.get_life_log_entries_in_range(params["date_from"], params["date_to"])
            return json.dumps(entries, default=str)
        if name == "get_entries_by_category":
            cat = params["category"]
            all_entries = db.get_all_life_log_entries()
            matching = [e for e in all_entries if cat in (e.get("categories") or [])]
            return json.dumps(matching, default=str)
    except Exception as e:
        logger.error("Tool dispatch error for %s: %s", name, e)
        return json.dumps({"error": str(e)})
    return json.dumps({"error": f"unknown tool {name}"})


_SYSTEM = """You are a helpful assistant answering questions about a personal Life Log
(a 30-year memoir of meaningful events). Use the available tools to look up data, then
give a short, friendly natural-language answer. Cite specific dates and details from the data.
If the user asks an unanswerable question, say so plainly."""


def answer_query(question: str) -> str:
    """Answer a natural-language question about the Life Log using tool-use."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = [{"role": "user", "content": question}]

    for _ in range(5):  # cap tool-call rounds
        resp = client.messages.create(
            model=MODEL,
            max_tokens=800,
            system=_SYSTEM,
            tools=_TOOLS,
            messages=messages,
        )
        logger.debug("LLM stop_reason=%s", resp.stop_reason)

        if resp.stop_reason == "end_turn":
            return "".join(
                b.text for b in resp.content if getattr(b, "type", None) == "text"
            ).strip()

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    logger.debug("Calling tool %s with %s", block.name, block.input)
                    result = _tool_dispatch(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop_reason
        logger.warning("Unexpected stop_reason: %s", resp.stop_reason)
        break

    return "Sorry — couldn't formulate an answer."
