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
from config import CALENDAR_SCAN_HOUR, GMAIL_SCAN_INTERVAL_HOURS, TIMEZONE, WEEKLY_PUSH_HOUR

logging.basicConfig(
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    db.initialize_db()
    db.seed_default_targets()

    from jobs.scan_calendar import run as scan_calendar
    from jobs.scan_gmail import run as scan_gmail
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
    scheduler.add_job(scan_calendar, CronTrigger(hour=CALENDAR_SCAN_HOUR, minute=0), id="scan_calendar")
    scheduler.add_job(weekly_push, CronTrigger(day_of_week="mon", hour=WEEKLY_PUSH_HOUR, minute=0), id="weekly_push")
    scheduler.start()
    logger.info("On Track started — gmail every %dh, calendar daily @%02d:00, push Mon @%02d:00",
                GMAIL_SCAN_INTERVAL_HOURS, CALENDAR_SCAN_HOUR, WEEKLY_PUSH_HOUR)
    yield
    scheduler.shutdown(wait=False)


app = create_app(lifespan=lifespan)
