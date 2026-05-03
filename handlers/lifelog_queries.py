"""Handler for /ask — natural-language queries against the Life Log."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from services.lifelog_query_service import answer_query

logger = logging.getLogger(__name__)


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text(
            "🔍 *Ask your Life Log anything*\n\n"
            "Examples:\n"
            "• `/ask When did I last see Sprink?`\n"
            "• `/ask How many trips did I take in 2025?`\n"
            "• `/ask Show me everything with Mom`\n"
            "• `/ask Who haven't I seen in 6 months?`",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text("🤔 Thinking...")
    try:
        answer = answer_query(question)
    except Exception as e:
        logger.error("Query failed: %s", e, exc_info=True)
        await update.message.reply_text(f"Sorry — query failed: {e}")
        return

    await update.message.reply_text(answer)
