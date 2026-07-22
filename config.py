import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Web app login password (single-user). Required.
APP_PASSWORD = os.environ["APP_PASSWORD"]

# Telegram is now an OPTIONAL send-only notification channel.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0") or "0")

TIMEZONE = os.getenv("TIMEZONE", "America/Denver")

# Google OAuth2 (shared by Calendar + Gmail; refresh token must carry both scopes)
GOOGLE_CALENDAR_CLIENT_ID = os.getenv("GOOGLE_CALENDAR_CLIENT_ID", "")
GOOGLE_CALENDAR_CLIENT_SECRET = os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET", "")
GOOGLE_CALENDAR_REFRESH_TOKEN = os.getenv("GOOGLE_CALENDAR_REFRESH_TOKEN", "")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

# Database: PostgreSQL if DATABASE_URL is set, otherwise SQLite (local dev fallback)
_raw_db_url = os.getenv("DATABASE_URL", "")
if _raw_db_url.startswith("postgres://"):
    _raw_db_url = _raw_db_url.replace("postgres://", "postgresql://", 1)
DATABASE_URL = _raw_db_url
DATABASE_PATH = os.getenv("DATABASE_PATH", "weekly_updates.db")

# Job schedules
GMAIL_SCAN_INTERVAL_HOURS = int(os.getenv("GMAIL_SCAN_INTERVAL_HOURS", "4"))
GMAIL_SCAN_LOOKBACK_DAYS = int(os.getenv("GMAIL_SCAN_LOOKBACK_DAYS", "7"))
CALENDAR_SCAN_HOUR = int(os.getenv("CALENDAR_SCAN_HOUR", "6"))
WEEKLY_PUSH_HOUR = int(os.getenv("WEEKLY_PUSH_HOUR", "9"))
