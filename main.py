"""On Track v2 entry point — FastAPI app + in-process APScheduler."""
import datetime
import logging
from contextlib import asynccontextmanager

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import database as db
from app.api import create_app
from config import (BACKUP_HOUR, CALENDAR_SCAN_HOUR, GMAIL_SCAN_INTERVAL_HOURS,
                    SIMPLEFIN_SYNC_INTERVAL_HOURS, TIMEZONE, WEEKLY_PUSH_HOUR)

logging.basicConfig(
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
    level=logging.INFO,
)

# httpx logs the full request URL at INFO, and the SimpleFIN access URL *is*
# the credential (services/simplefin_service.py) — so at INFO every sync
# publishes the bearer token to the deploy logs. This bypasses the redaction
# boundary described in CLAUDE.md completely: nothing is raised and nothing is
# stored, so neither safe_status() nor app_settings ever sees it. Prevent the
# line from being emitted rather than scrubbing it afterwards. Leaked in
# production on 2026-07-23; locked by a test in tests/test_simplefin_service.py.
# Never lower these.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    db.initialize_db()
    db.seed_default_targets()
    db.delete_expired_sessions(datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds"))

    from jobs.backup_db import run as backup_db
    from jobs.scan_calendar import run as scan_calendar
    from jobs.scan_gmail import run as scan_gmail
    from jobs.sync_bank import run as sync_bank
    from jobs.weekly_push import run as weekly_push

    scheduler = AsyncIOScheduler(timezone=pytz.timezone(TIMEZONE))
    # next_run_time=now: run once at startup so a deploy/restart refreshes
    # gmail_last_status immediately instead of waiting a full interval.
    scheduler.add_job(
        scan_gmail,
        IntervalTrigger(hours=GMAIL_SCAN_INTERVAL_HOURS),
        id="scan_gmail",
        next_run_time=datetime.datetime.now(pytz.timezone(TIMEZONE)),
    )
    # next_run_time=now, same reasoning as scan_gmail: a deploy should refresh
    # bank_last_status immediately rather than waiting a full interval. Also
    # matters more here — SimpleFIN keeps only a rolling 90 days, so a sync that
    # silently stops running loses history permanently.
    scheduler.add_job(
        sync_bank,
        IntervalTrigger(hours=SIMPLEFIN_SYNC_INTERVAL_HOURS),
        id="sync_bank",
        next_run_time=datetime.datetime.now(pytz.timezone(TIMEZONE)),
    )
    scheduler.add_job(scan_calendar, CronTrigger(hour=CALENDAR_SCAN_HOUR, minute=0), id="scan_calendar")
    scheduler.add_job(weekly_push, CronTrigger(day_of_week="mon", hour=WEEKLY_PUSH_HOUR, minute=0), id="weekly_push")
    scheduler.add_job(backup_db, CronTrigger(day_of_week="sun", hour=BACKUP_HOUR, minute=0), id="backup_db")
    scheduler.start()
    logger.info("On Track started — gmail every %dh, bank every %dh, calendar daily @%02d:00, "
                "push Mon @%02d:00, backup Sun @%02d:00",
                GMAIL_SCAN_INTERVAL_HOURS, SIMPLEFIN_SYNC_INTERVAL_HOURS,
                CALENDAR_SCAN_HOUR, WEEKLY_PUSH_HOUR, BACKUP_HOUR)
    yield
    scheduler.shutdown(wait=False)


app = create_app(lifespan=lifespan)
