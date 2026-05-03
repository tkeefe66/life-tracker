"""Handler for the /log command — manual Life Log entry capture."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /log [text]."""
    raise NotImplementedError("Implemented in Task 3.2")
