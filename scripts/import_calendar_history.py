"""
Calendar-history backfill — go as far back as Google Calendar allows.
For each year, classifies events and creates proposals (status='proposed').
The user reviews them via Telegram with yes #N / skip #N replies.

Usage:
    python -m scripts.import_calendar_history --start-year 2018
    python -m scripts.import_calendar_history --year 2024  # one year
    python -m scripts.import_calendar_history --dry-run
"""
import argparse
import datetime
import logging

import pytz

import database as db
from ai_life_log import propose_from_calendar_event
from config import TIMEZONE
from services.calendar_service import is_configured, _get_service, GOOGLE_CALENDAR_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


def _fetch_year_events(year: int) -> list:
    if not is_configured():
        raise SystemExit("Calendar not configured.")
    tz = pytz.timezone(TIMEZONE)
    time_min = datetime.datetime(year, 1, 1, tzinfo=tz).isoformat()
    time_max = datetime.datetime(year, 12, 31, 23, 59, tzinfo=tz).isoformat()

    service = _get_service()
    events = []
    page_token = None
    while True:
        result = service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=2500,
            pageToken=page_token,
        ).execute()
        for item in result.get("items", []):
            if item.get("status") == "cancelled":
                continue
            start = item.get("start", {})
            end = item.get("end", {})
            attendees_raw = item.get("attendees", []) or []
            attendees = [
                (a.get("displayName") or a.get("email", "").split("@")[0])
                for a in attendees_raw if not a.get("self")
            ]
            events.append({
                "event_id": item["id"],
                "title": item.get("summary", "(No title)"),
                "start_datetime": start.get("dateTime") or start.get("date", ""),
                "end_datetime": end.get("dateTime") or end.get("date", ""),
                "description": item.get("description", ""),
                "location": item.get("location", ""),
                "is_recurring": bool(item.get("recurringEventId")),
                "attendees": attendees,
            })
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return events


def import_year(year: int, dry_run: bool = False):
    events = _fetch_year_events(year)
    active = [c["name"] for c in db.get_active_categories()]
    promoted = 0
    for event in events:
        if db.get_activity_by_source_id("calendar", event["event_id"]):
            continue

        # Always record activity
        if not dry_run:
            db.record_activity(
                source="calendar",
                source_id=event["event_id"],
                event_type="calendar_event",
                occurred_at=event["start_datetime"] or None,
                payload=event,
            )

        parsed = propose_from_calendar_event(
            title=event["title"], start=event["start_datetime"],
            end=event["end_datetime"], attendees=event.get("attendees", []),
            description=event.get("description", ""), location=event.get("location", ""),
            active_categories=active,
        )

        if parsed.get("confidence") not in ("high", "matched"):
            continue
        if dry_run:
            logger.info("[dry-run] Would propose: %s (%s)", parsed["description"], parsed["confidence"])
            continue

        date_start = event["start_datetime"][:10]
        date_end = event["end_datetime"][:10] if event.get("end_datetime") else None
        if date_end == date_start:
            date_end = None

        db.save_proposal(
            date_start=date_start, date_end=date_end,
            categories=parsed["categories"],
            description=parsed["description"] or event["title"],
            location=parsed.get("location") or event.get("location"),
            source="calendar", source_id=event["event_id"],
        )
        promoted += 1

    logger.info("Year %d: %d events scanned, %d proposals created", year, len(events), promoted)
    return promoted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, help="Scan from this year through current year")
    ap.add_argument("--year", type=int, help="Scan a single year")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db.initialize_db()
    current = datetime.date.today().year

    if args.year:
        years = [args.year]
    elif args.start_year:
        years = list(range(args.start_year, current + 1))
    else:
        raise SystemExit("Specify --year YEAR or --start-year YEAR")

    total = 0
    for y in years:
        total += import_year(y, dry_run=args.dry_run)
    logger.info("Done — %d total proposals", total)

    if total and not args.dry_run:
        logger.info(
            "Review them in Telegram with /proposals (paginated, %d per page) "
            "or 'yes all' / 'skip all' to bulk-handle.", 10
        )


if __name__ == "__main__":
    main()
