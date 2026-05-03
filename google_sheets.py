import json
import logging
import os
import re
from datetime import date, timedelta

import gspread
import pytz
from google.oauth2.service_account import Credentials

from config import GOOGLE_SERVICE_ACCOUNT_FILE, GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEETS_ID, TIMEZONE

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _today() -> date:
    import datetime
    return datetime.datetime.now(pytz.timezone(TIMEZONE)).date()

SHEET_WEEKLY = "Weekly Reviews"
SHEET_LATER = "Later"
SHEET_HABITS = "Habits"
SHEET_LIFE_LOG = "Life Log"
SHEET_PEOPLE = "People"


# ── Client ────────────────────────────────────────────────────────────────────

def _fix_json_newlines(s: str) -> str:
    """Escape actual newlines inside JSON string values (Railway expands \\n in env vars)."""
    result = []
    in_string = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and in_string and i + 1 < len(s):
            result.append(c)
            result.append(s[i + 1])
            i += 2
            continue
        if c == '"':
            in_string = not in_string
        if c == '\n' and in_string:
            result.append('\\n')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


def _get_spreadsheet() -> gspread.Spreadsheet:
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        try:
            info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        except json.JSONDecodeError:
            info = json.loads(_fix_json_newlines(GOOGLE_SERVICE_ACCOUNT_JSON))
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(GOOGLE_SHEETS_ID)


def _ensure_sheets(spreadsheet: gspread.Spreadsheet):
    existing = {ws.title for ws in spreadsheet.worksheets()}
    for name, cols in [
        (SHEET_WEEKLY, 6), (SHEET_LATER, 4), (SHEET_HABITS, 12),
        (SHEET_LIFE_LOG, 10), (SHEET_PEOPLE, 10),
    ]:
        if name not in existing:
            spreadsheet.add_worksheet(title=name, rows=5000, cols=cols)
            logger.info("Created sheet: %s", name)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _week_label(week_start: str) -> str:
    start = date.fromisoformat(week_start)
    end = start + timedelta(days=6)
    return f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"


def _monday_of(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


# ── Tab 1: Weekly Reviews (append-only) ──────────────────────────────────────

_WEEKLY_HEADER = ["Date", "Day", "Week", "Category", "Content", "ID"]
_ID_COL = 6   # 1-indexed column F


def _parse_sheet_ids(col_values: list) -> set:
    """Extract non-empty ID strings from column F values (header already stripped)."""
    return {str(v).strip() for v in col_values if str(v).strip()}


def _is_old_format(col_f_row2: str) -> bool:
    """Return True if column F row 2 doesn't look like an entry ID (a:N or f:N)."""
    import re as _re
    return not _re.match(r'^[af]:\d+$', str(col_f_row2).strip())


def _weekly_entry_rows(all_accomplishments: list, all_focus: list, exclude_ids: set) -> list:
    """
    Build plain table rows for entries not already in the sheet.
    Returns list of [date_sort_key, row] pairs sorted chronologically.
    """
    pairs = []

    for acc in all_accomplishments:
        sheet_id = f"a:{acc['id']}"
        if sheet_id in exclude_ids or acc.get('sheet_deleted'):
            continue
        d = date.fromisoformat(acc['date'])
        pairs.append((acc['date'], [
            d.strftime('%-m/%-d/%y'),
            d.strftime('%A'),
            _week_label(_monday_of(d)),
            acc['category'].title(),
            acc['content'],
            sheet_id,
        ]))

    for focus in all_focus:
        sheet_id = f"f:{focus['id']}"
        if sheet_id in exclude_ids or focus.get('sheet_deleted'):
            continue
        d = date.fromisoformat(focus['week_start'])
        pairs.append((focus['week_start'], [
            d.strftime('%-m/%-d/%y'),
            d.strftime('%A'),
            _week_label(focus['week_start']),
            'Focus',
            focus['content'],
            sheet_id,
        ]))

    pairs.sort(key=lambda x: x[0])
    return [row for _, row in pairs]


_VALID_CATEGORIES = {"work", "personal", "focus"}


def _read_back_edits(sheet, all_accomplishments: list, all_focus: list) -> tuple:
    """
    Read all rows from Sheets and return any that differ from DB values.
    Returns (edited_acc: list[dict], edited_focus: list[dict]).
    """
    all_rows = sheet.get_all_values()
    if len(all_rows) < 2:
        return [], []

    acc_by_id = {str(a['id']): a for a in all_accomplishments}
    focus_by_id = {str(f['id']): f for f in all_focus}

    edited_acc = []
    edited_focus = []

    for row in all_rows[1:]:  # skip header
        if len(row) < 6:
            continue
        category_val = row[3].strip()
        content_val = row[4].strip()
        id_val = row[5].strip()

        if not id_val or not content_val:
            continue
        if not re.match(r'^[af]:\d+$', id_val):
            continue

        entry_type = id_val[0]
        entry_id = id_val[2:]

        if entry_type == 'a' and entry_id in acc_by_id:
            acc = acc_by_id[entry_id]
            sheet_cat = category_val.lower()
            if sheet_cat not in _VALID_CATEGORIES:
                sheet_cat = acc['category']  # keep DB value if invalid
            if sheet_cat != acc['category'].lower() or content_val != acc['content'].strip():
                edited_acc.append({'id': int(entry_id), 'category': sheet_cat, 'content': content_val})

        elif entry_type == 'f' and entry_id in focus_by_id:
            focus = focus_by_id[entry_id]
            if content_val != focus['content'].strip():
                edited_focus.append({'id': int(entry_id), 'content': content_val})

    return edited_acc, edited_focus


def _sync_weekly_reviews_append(
    sheet,
    all_accomplishments: list,
    all_focus: list,
) -> tuple:
    """
    Append-only sync for Weekly Reviews tab with read-back of user edits.
    Returns (new_acc_ids, new_focus_ids, deleted_acc_ids, deleted_focus_ids,
             edited_acc, edited_focus) where edited_* are lists of DB update dicts.
    """
    col_f = sheet.col_values(_ID_COL)  # full column F including header

    # Detect old-format sheet (no ID column) — clear once so we start fresh
    needs_header = not col_f or col_f[0] != "ID"
    if col_f and col_f[0] == "ID" and len(col_f) > 1 and _is_old_format(col_f[1]):
        logger.info("Weekly Reviews sheet is in old format — clearing for fresh append-only sync")
        sheet.clear()
        col_f = []
        needs_header = True

    if needs_header:
        sheet.update("A1", [_WEEKLY_HEADER])
        existing_ids = set()
        edited_acc, edited_focus = [], []
    else:
        existing_ids = _parse_sheet_ids(col_f[1:])  # skip header
        # Read back any edits the user made in Sheets
        edited_acc, edited_focus = _read_back_edits(sheet, all_accomplishments, all_focus)

    # Detect deletions: entries we thought were in the sheet but aren't anymore
    deleted_acc_ids = set()
    deleted_focus_ids = set()
    for acc in all_accomplishments:
        if acc.get('sheet_synced') and not acc.get('sheet_deleted'):
            if f"a:{acc['id']}" not in existing_ids:
                deleted_acc_ids.add(acc['id'])
    for focus in all_focus:
        if focus.get('sheet_synced') and not focus.get('sheet_deleted'):
            if f"f:{focus['id']}" not in existing_ids:
                deleted_focus_ids.add(focus['id'])

    # Build and append new rows
    new_rows = _weekly_entry_rows(all_accomplishments, all_focus, existing_ids)
    if new_rows:
        sheet.append_rows(new_rows, value_input_option="RAW")
        sheet.format("E:E", {"wrapStrategy": "WRAP"})

    # Collect IDs of newly appended entries
    new_acc_ids = {int(r[5][2:]) for r in new_rows if r[5].startswith("a:")}
    new_focus_ids = {int(r[5][2:]) for r in new_rows if r[5].startswith("f:")}

    logger.info(
        "Weekly Reviews: +%d appended, %d acc deleted, %d focus deleted, %d edits read back",
        len(new_rows), len(deleted_acc_ids), len(deleted_focus_ids),
        len(edited_acc) + len(edited_focus),
    )
    return new_acc_ids, new_focus_ids, deleted_acc_ids, deleted_focus_ids, edited_acc, edited_focus


# ── Tab 2: Later ──────────────────────────────────────────────────────────────

def _fmt_later_date(val: str) -> str:
    """Convert YYYY-MM-DD to M/D/YY; pass through anything that isn't that format."""
    if not val:
        return "—"
    try:
        d = date.fromisoformat(val)
        return d.strftime("%-m/%-d/%y")
    except ValueError:
        return val


def _build_later_rows(organized_groups: list) -> list:
    """
    organized_groups: [{"theme": str, "items": [{"content": str, "target_date": str,
                        "status": str, "ai_status": str, "ai_notes": str}]}]
    """
    rows = [["Item", "Target Date", "Status", "AI Status", "AI Notes"]]

    if not organized_groups:
        rows.append(["No later items yet. Use /later to add long-term goals.", "", "", "", ""])
        return rows

    for group in organized_groups:
        rows.append([f"── {group['theme'].upper()} ──", "", "", "", ""])
        for item in group.get("items", []):
            start = _fmt_later_date(item.get("target_date", "") or "")
            end = _fmt_later_date(item.get("end_date", "") or "")
            date_label = f"{start} – {end}" if end and end != "—" and end != start else start
            rows.append([
                item.get("content", ""),
                date_label,
                item.get("status", "pending") or "pending",
                item.get("ai_status", "") or "",
                item.get("ai_notes", "") or "",
            ])
        rows.append(["", "", "", "", ""])  # spacer

    return rows


# ── Tab 3: Habits grid ────────────────────────────────────────────────────────

def _build_habits_grid_rows(all_habits: list, all_logs: list) -> list:
    """
    Visual grid: each block = one week, rows = habits, cols = Mon–Sun.
    """
    if not all_habits:
        return [["No habits set up yet. Use /habit to add one.", "", "", "", "", "", "", "", ""]]

    # Collect all weeks with any log data, plus the current week
    weeks = set()
    for log in all_logs:
        weeks.add(_monday_of(date.fromisoformat(log["date"])))
    weeks.add(_monday_of(_today()))

    logs_by_week = {}
    for log in all_logs:
        w = _monday_of(date.fromisoformat(log["date"]))
        logs_by_week.setdefault(w, []).append(log)

    header = ["Habit", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Done"]
    rows = [header]

    for week_start in sorted(weeks, reverse=True):
        label = _week_label(week_start)
        rows.append([f"━━━ Week of {label} ━━━", "", "", "", "", "", "", "", ""])

        start_date = date.fromisoformat(week_start)
        week_logs = logs_by_week.get(week_start, [])
        logs_by_habit_date = {(l["habit_id"], l["date"]): l for l in week_logs}

        for habit in all_habits:
            days_sched = habit["days_of_week"]
            cells = []
            completed = 0
            scheduled_past = 0

            for offset in range(7):
                day_date = start_date + timedelta(days=offset)
                day_str = day_date.isoformat()

                if offset not in days_sched:
                    cells.append("—")
                elif day_date > _today():
                    cells.append("?")
                else:
                    scheduled_past += 1
                    log = logs_by_habit_date.get((habit["id"], day_str))
                    if log is None:
                        cells.append("?")
                    elif log["completed"]:
                        completed += 1
                        cells.append("Yes")
                    else:
                        cells.append("No")

            pct = f"{completed}/{scheduled_past}" if scheduled_past else "—"
            rows.append([habit["name"]] + cells + [pct])

        rows.append(["", "", "", "", "", "", "", "", ""])

    return rows


# ── Tab 4: Life Log ───────────────────────────────────────────────────────────

_LIFE_LOG_HEADER = [
    "Date", "End Date", "Categories", "Description", "People",
    "Location", "Notes", "Status", "Source", "ID",
]


def _build_life_log_rows(entries: list, people_by_entry: dict) -> list:
    rows = [_LIFE_LOG_HEADER]
    for e in entries:
        cats = ", ".join(e.get("categories") or [])
        people = ", ".join(people_by_entry.get(e["id"], []))
        rows.append([
            e.get("date_start", "") or "",
            e.get("date_end", "") or "",
            cats,
            e.get("description", "") or "",
            people,
            e.get("location", "") or "",
            e.get("notes", "") or "",
            e.get("status", "") or "",
            e.get("source", "") or "",
            str(e.get("id", "")),
        ])
    return rows


# ── Tab 5: People ─────────────────────────────────────────────────────────────

_PEOPLE_HEADER = [
    "Name", "Aliases", "Type", "Status", "First Seen", "Last Seen",
    "Start Date", "End Date", "Notes", "ID",
]


def _build_people_rows(people: list) -> list:
    rows = [_PEOPLE_HEADER]
    for p in people:
        aliases = ", ".join(p.get("aliases") or [])
        rows.append([
            p.get("name", ""),
            aliases,
            p.get("relationship_type") or "",
            p.get("status") or "",
            p.get("first_seen") or "",
            p.get("last_seen") or "",
            p.get("start_date") or "",
            p.get("end_date") or "",
            p.get("notes") or "",
            str(p.get("id", "")),
        ])
    return rows


# ── Public API ────────────────────────────────────────────────────────────────

def sync_to_sheets(
    all_accomplishments: list,
    all_focus: list,
    organized_later: list,
    all_habits: list = None,
    all_habit_logs: list = None,
) -> tuple:
    """
    Sync all three tabs to Google Sheets.

    Weekly Reviews tab: append-only — never overwrites existing rows.
    Later and Habits tabs: full rebuild.

    Returns (url, new_acc_ids, new_focus_ids, deleted_acc_ids, deleted_focus_ids).
    """
    spreadsheet = _get_spreadsheet()
    _ensure_sheets(spreadsheet)

    all_habits = all_habits or []
    all_habit_logs = all_habit_logs or []

    # Tab 1: Weekly Reviews — append-only with read-back
    weekly_sheet = spreadsheet.worksheet(SHEET_WEEKLY)
    new_acc_ids, new_focus_ids, deleted_acc_ids, deleted_focus_ids, edited_acc, edited_focus = \
        _sync_weekly_reviews_append(weekly_sheet, all_accomplishments, all_focus)

    # Tab 2: Later
    later_sheet = spreadsheet.worksheet(SHEET_LATER)
    later_rows = _build_later_rows(organized_later)
    later_sheet.clear()
    if later_rows:
        later_sheet.update("A1", later_rows)
    logger.info("Rebuilt Later sheet (%d groups)", len(organized_later))

    # Tab 3: Habits
    habits_sheet = spreadsheet.worksheet(SHEET_HABITS)
    habits_rows = _build_habits_grid_rows(all_habits, all_habit_logs)
    habits_sheet.clear()
    if habits_rows:
        habits_sheet.update("A1", habits_rows)
    logger.info("Rebuilt Habits sheet (%d habits)", len(all_habits))

    return spreadsheet.url, new_acc_ids, new_focus_ids, deleted_acc_ids, deleted_focus_ids, edited_acc, edited_focus


def sync_life_log_to_sheets(entries: list, people: list, people_by_entry: dict) -> str:
    """
    Full rebuild of Life Log + People tabs.

    Append-only with read-back is overkill for the early MVP — we'll add it
    once the Life Log entry volume justifies the complexity. For now, full
    rebuild is fine: thousands of rows max, fast Sheets API.
    """
    spreadsheet = _get_spreadsheet()
    _ensure_sheets(spreadsheet)

    life_log_sheet = spreadsheet.worksheet(SHEET_LIFE_LOG)
    rows = _build_life_log_rows(entries, people_by_entry)
    life_log_sheet.clear()
    if rows:
        life_log_sheet.update("A1", rows)
        life_log_sheet.format("D:D", {"wrapStrategy": "WRAP"})
        life_log_sheet.format("G:G", {"wrapStrategy": "WRAP"})
    logger.info("Life Log sheet rebuilt: %d entries", len(entries))

    people_sheet = spreadsheet.worksheet(SHEET_PEOPLE)
    p_rows = _build_people_rows(people)
    people_sheet.clear()
    if p_rows:
        people_sheet.update("A1", p_rows)
    logger.info("People sheet rebuilt: %d people", len(people))

    return spreadsheet.url


def sync_habits_to_sheets(all_habits: list, all_habit_logs: list) -> str:
    """Full rebuild of the Habits tab."""
    spreadsheet = _get_spreadsheet()
    _ensure_sheets(spreadsheet)
    habits_sheet = spreadsheet.worksheet(SHEET_HABITS)
    habits_rows = _build_habits_grid_rows(all_habits, all_habit_logs)
    habits_sheet.clear()
    if habits_rows:
        habits_sheet.update("A1", habits_rows)
    logger.info("Rebuilt Habits sheet (%d habits)", len(all_habits))
    return spreadsheet.url
