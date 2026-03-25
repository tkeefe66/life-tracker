import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

HABIT_REMINDER_HOUR = int(os.getenv("HABIT_REMINDER_HOUR", "7"))
HABIT_REMINDER_MINUTE = int(os.getenv("HABIT_REMINDER_MINUTE", "0"))

DAILY_PROMPT_HOUR = int(os.getenv("DAILY_PROMPT_HOUR", "18"))
DAILY_PROMPT_MINUTE = int(os.getenv("DAILY_PROMPT_MINUTE", "0"))
WEEKLY_SUMMARY_HOUR = int(os.getenv("WEEKLY_SUMMARY_HOUR", "17"))
TIMEZONE = os.getenv("TIMEZONE", "America/New_York")

GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")

DATABASE_PATH = os.getenv("DATABASE_PATH", "weekly_updates.db")
