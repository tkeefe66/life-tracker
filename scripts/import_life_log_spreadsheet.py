"""
One-time backfill: import the user's existing Life Log spreadsheet
into the new life_log_entries / people tables.

The source sheet has columns: Year | Month | Category | Description.
Date granularity is month → date_start = first of month.

Usage:
    LIFE_LOG_IMPORT_SHEET_ID=<sheet_id> python -m scripts.import_life_log_spreadsheet

Or with a tab name:
    python -m scripts.import_life_log_spreadsheet --tab "Memory Log"
"""
import argparse
import json
import logging
import re

import gspread
from google.oauth2.service_account import Credentials

import database as db
from ai_life_log import extract_entry_from_existing_text
from config import (
    GOOGLE_SERVICE_ACCOUNT_FILE,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    LIFE_LOG_IMPORT_SHEET_ID,
)
from google_sheets import SCOPES, _fix_json_newlines

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


def _month_to_num(month: str) -> int:
    """'07 - July' → 7; 'July' → 7. Return 1 if unparseable."""
    m = re.match(r"^\s*(\d{1,2})", month)
    if m:
        return int(m.group(1))
    months = ["january", "february", "march", "april", "may", "june",
              "july", "august", "september", "october", "november", "december"]
    for i, name in enumerate(months, start=1):
        if name in month.lower():
            return i
    return 1


def _import_one_row(year: str, month: str, category: str, description: str, active_categories: list):
    if not description.strip():
        return

    parsed = extract_entry_from_existing_text(
        original_category=category,
        original_description=description,
        active_categories=active_categories,
    )

    cats = parsed.get("categories") or ["Life Event"]
    month_num = _month_to_num(month)
    date_start = f"{year}-{month_num:02d}-01"

    entry_id = db.save_life_log_entry(
        date_start=date_start,
        date_end=None,
        categories=cats,
        description=parsed.get("description") or description,
        location=parsed.get("location"),
        notes=None,
        status="confirmed",
        source="import_spreadsheet",
        source_id=None,
    )
    for c in cats:
        db.increment_category_usage(c)

    for name in parsed.get("people", []):
        existing = db.find_person_by_name(name)
        if existing:
            db.link_entry_to_people(entry_id, [existing["id"]])
            db.update_person_last_seen(existing["id"], date_start)
        else:
            pid = db.save_person(
                name=name, aliases=[], relationship_type=None,
                first_seen=date_start, notes=None,
            )
            db.link_entry_to_people(entry_id, [pid])

    logger.info("Imported: %s-%02d %s", year, month_num, description[:60])


def _open_source_sheet(tab_name) -> gspread.Worksheet:
    if not LIFE_LOG_IMPORT_SHEET_ID:
        raise SystemExit("Set LIFE_LOG_IMPORT_SHEET_ID before running.")
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        try:
            info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        except json.JSONDecodeError:
            info = json.loads(_fix_json_newlines(GOOGLE_SERVICE_ACCOUNT_JSON))
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(LIFE_LOG_IMPORT_SHEET_ID)
    return sheet.worksheet(tab_name) if tab_name else sheet.sheet1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tab", default=None, help="Tab name (default: first sheet)")
    args = ap.parse_args()

    db.initialize_db()
    active = [c["name"] for c in db.get_active_categories()]

    ws = _open_source_sheet(args.tab)
    rows = ws.get_all_values()
    if len(rows) < 2:
        logger.warning("Source sheet has no data rows")
        return

    header = [h.strip().lower() for h in rows[0]]
    try:
        i_year = header.index("year")
        i_month = header.index("month")
        i_cat = header.index("category")
        i_desc = header.index("description")
    except ValueError as e:
        raise SystemExit(f"Source sheet header missing expected column: {e}")

    imported = 0
    for r in rows[1:]:
        if len(r) <= max(i_year, i_month, i_cat, i_desc):
            continue
        try:
            _import_one_row(
                year=r[i_year], month=r[i_month],
                category=r[i_cat], description=r[i_desc],
                active_categories=active,
            )
            imported += 1
        except Exception as e:
            logger.error("Skipping row %r — %s", r, e)

    logger.info("Done — imported %d rows", imported)


if __name__ == "__main__":
    main()
