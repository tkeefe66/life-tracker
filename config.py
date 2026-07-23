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

# Session lifetime, in days. The session token itself is random
# (secrets.token_urlsafe) and stored server-side — APP_PASSWORD can no longer be
# used to compute a valid cookie offline.
SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "14"))

# Absolute session lifetime cap, in days, regardless of sliding renewal. Sliding
# renewal only ever advances expires_at, never created_at — without a cap, an
# actively-used (or stolen and replayed) cookie would renew forever.
SESSION_MAX_DAYS = int(os.getenv("SESSION_MAX_DAYS", "60"))

# Weekly database backup — an off-Railway S3-compatible destination (e.g.
# Cloudflare R2, Backblaze B2). All optional: jobs/backup_db.py logs a warning
# and no-ops when any is unset, so local dev and an un-configured deploy are
# unaffected.
BACKUP_S3_BUCKET = os.getenv("BACKUP_S3_BUCKET", "")
BACKUP_S3_ENDPOINT = os.getenv("BACKUP_S3_ENDPOINT", "")
BACKUP_S3_ACCESS_KEY = os.getenv("BACKUP_S3_ACCESS_KEY", "")
BACKUP_S3_SECRET_KEY = os.getenv("BACKUP_S3_SECRET_KEY", "")
BACKUP_HOUR = int(os.getenv("BACKUP_HOUR", "4"))

# Discrete PostgreSQL connection vars — Railway's Postgres plugin sets these
# directly, already decoded (no percent-encoding to strip). jobs/backup_db.py
# prefers them over parsing DATABASE_URL: urlparse() never percent-decodes
# the userinfo, so a password containing "@", "%", or "/" would otherwise
# reach pg_dump literally percent-encoded and authentication would fail
# permanently. All optional — unset unless Railway (or the operator) provides
# them; jobs/backup_db.py falls back to parsing DATABASE_URL when any is
# missing.
PGHOST = os.getenv("PGHOST", "")
PGPORT = os.getenv("PGPORT", "")
PGUSER = os.getenv("PGUSER", "")
PGPASSWORD = os.getenv("PGPASSWORD", "")
PGDATABASE = os.getenv("PGDATABASE", "")
# Optional even within the discrete-var path — e.g. sslmode. Also used to
# carry sslmode forward when falling back to parsing DATABASE_URL's query
# string, whose sslmode= (etc.) is otherwise silently dropped by the rebuilt
# connection.
PGSSLMODE = os.getenv("PGSSLMODE", "")
