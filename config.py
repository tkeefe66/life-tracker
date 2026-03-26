import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

# Webhook URL for Railway production. If blank, bot falls back to polling (local dev).
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

HABIT_REMINDER_HOUR = int(os.getenv("HABIT_REMINDER_HOUR", "7"))
HABIT_REMINDER_MINUTE = int(os.getenv("HABIT_REMINDER_MINUTE", "0"))

DAILY_PROMPT_HOUR = int(os.getenv("DAILY_PROMPT_HOUR", "18"))
DAILY_PROMPT_MINUTE = int(os.getenv("DAILY_PROMPT_MINUTE", "0"))
WEEKLY_SUMMARY_HOUR = int(os.getenv("WEEKLY_SUMMARY_HOUR", "17"))
TIMEZONE = os.getenv("TIMEZONE", "America/Denver")

GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
_gsheets_creds_b64 = os.getenv("GSHEETS_CREDS", "")
GOOGLE_SERVICE_ACCOUNT_JSON = (
    __import__("base64").b64decode(_gsheets_creds_b64).decode() if _gsheets_creds_b64 else ""
)

# Google Calendar OAuth2
GOOGLE_CALENDAR_CLIENT_ID = os.getenv("GOOGLE_CALENDAR_CLIENT_ID", "")
GOOGLE_CALENDAR_CLIENT_SECRET = os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET", "")
GOOGLE_CALENDAR_REFRESH_TOKEN = os.getenv("GOOGLE_CALENDAR_REFRESH_TOKEN", "")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

# Database: PostgreSQL if DATABASE_URL is set, otherwise SQLite (local dev fallback)
_raw_db_url = os.getenv("DATABASE_URL", "")
if _raw_db_url.startswith("postgres://"):
    # Railway sometimes uses the older postgres:// prefix; psycopg2 requires postgresql://
    _raw_db_url = _raw_db_url.replace("postgres://", "postgresql://", 1)
DATABASE_URL = _raw_db_url

# SQLite path used only when DATABASE_URL is not set
DATABASE_PATH = os.getenv("DATABASE_PATH", "weekly_updates.db")
