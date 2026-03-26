import json
import logging
import os
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
    for name, cols in [(SHEET_WEEKLY, 6), (SHEET_LATER, 4), (SHEET_HABITS, 12)]:
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


def _group_by_week_and_date(entries: list, category: str) -> dict:
    """Returns {week_start: {date_str: [content, ...]}}"""
    result = {}
    for e in entries:
        if e["category"] != category:
            continue
        d = date.fromisoformat(e["date"])
        week = _monday_of(d)
        result.setdefault(week, {}).setdefault(e["date"], []).append(e["content"])
    return result


# ── Tab 1: Weekly Reviews ─────────────────────────────────────────────────────

def _format_content(content: str) -> str:
    """Format content as bullet points within a cell using newlines."""
    import re
    # Split on existing newlines or bullet/dash patterns
    lines = re.split(r'\n+|(?<=[.!?])\s+(?=[A-Z])', content.strip())
    bullets = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Remove existing bullet prefixes to normalise
        line = re.sub(r'^[-•*]\s*', '', line)
        bullets.append(f"- {line}")
    return "\n".join(bullets) if bullets else content


def _accomplishment_rows(entries_by_date: dict, week_start: str) -> list:
    """Build day-by-day rows for one category within a week."""
    rows = []
    start_date = date.fromisoformat(week_start)
    for offset in range(7):
        day_date = start_date + timedelta(days=offset)
        if day_date > _today():
            break
        day_str = day_date.isoformat()
        if day_str in entries_by_date:
            label = day_date.strftime("%A %b %d")
            all_content = "\n".join(entries_by_date[day_str])
            rows.append(["", label, _format_content(all_content)])
    if not rows:
        rows.append(["", "", "—"])
    return rows


def _habit_week_rows(habit: dict, week_start: str, week_logs: list) -> list:
    """One row per habit showing day-by-day status for a week."""
    days_scheduled = habit["days_of_week"]
    start_date = date.fromisoformat(week_start)

    logs_by_date = {log["date"]: log for log in week_logs if log["habit_id"] == habit["id"]}

    day_cells = []
    for offset in range(7):
        day_date = start_date + timedelta(days=offset)
        day_str = day_date.isoformat()
        if offset not in days_scheduled:
            day_cells.append("—")
        elif day_date > _today():
            day_cells.append("·")
        elif day_str in logs_by_date:
            log = logs_by_date[day_str]
            if log["completed"]:
                day_cells.append("✅")
            else:
                reason = f" ({log['miss_reason']})" if log.get("miss_reason") else ""
                day_cells.append(f"❌{reason}")
        else:
            day_cells.append("?")

    scheduled_past = sum(
        1 for i, d in enumerate((start_date + timedelta(days=o) for o in range(7)))
        if i in days_scheduled and d <= _today()
    )
    completed = sum(1 for d in day_cells if d == "✅")
    pct = f"{completed}/{scheduled_past}" if scheduled_past else "—"

    return [["", habit["name"], f"{day_cells[0]} {day_cells[1]} {day_cells[2]} {day_cells[3]} {day_cells[4]} {day_cells[5]} {day_cells[6]}  ({pct})"]]


def _build_weekly_review_rows(all_accomplishments: list, focus_summaries: dict,
                               all_habits: list = None, all_habit_logs: list = None) -> list:
    """
    Builds Tab 1. For each week (newest first):
      🎯 NEXT WEEK'S FOCUS  (AI summary — keyed by the FOLLOWING week's Monday)
      💼 WORK ACCOMPLISHED
      🏆 PERSONAL ACCOMPLISHED

    focus_summaries: {week_start: summary_text}
    """
    all_habits = all_habits or []
    all_habit_logs = all_habit_logs or []

    work_by_week = _group_by_week_and_date(all_accomplishments, "work")
    personal_by_week = _group_by_week_and_date(all_accomplishments, "personal")

    # Group habit logs by week
    habit_logs_by_week = {}
    for log in all_habit_logs:
        w = _monday_of(date.fromisoformat(log["date"]))
        habit_logs_by_week.setdefault(w, []).append(log)

    # Collect all weeks with any data
    all_weeks = set(work_by_week.keys()) | set(personal_by_week.keys()) | set(habit_logs_by_week.keys())
    for focus_week in focus_summaries:
        implied = (date.fromisoformat(focus_week) - timedelta(days=7)).isoformat()
        all_weeks.add(implied)

    if not all_weeks:
        return [["No data yet. Use /update to log your first accomplishments!"]]

    rows = [["Section", "Day / Date", "Details"]]

    for week_start in sorted(all_weeks, reverse=True):
        label = _week_label(week_start)
        rows.append([f"━━━ Week of {label} ━━━", "", ""])
        rows.append(["", "", ""])

        # 🎯 Next week's focus — look up the FOLLOWING Monday
        next_monday = (date.fromisoformat(week_start) + timedelta(days=7)).isoformat()
        focus_text = focus_summaries.get(next_monday, "")

        rows.append(["🎯 NEXT WEEK'S FOCUS", "", ""])
        if focus_text:
            for line in focus_text.splitlines():
                if line.strip():
                    rows.append(["", "", line])
        else:
            rows.append(["", "", "—"])
        rows.append(["", "", ""])

        # 💼 Work accomplished
        rows.append(["💼 WORK ACCOMPLISHED", "", ""])
        rows.extend(_accomplishment_rows(work_by_week.get(week_start, {}), week_start))
        rows.append(["", "", ""])

        # 🏆 Personal accomplished
        rows.append(["🏆 PERSONAL ACCOMPLISHED", "", ""])
        rows.extend(_accomplishment_rows(personal_by_week.get(week_start, {}), week_start))
        rows.append(["", "", ""])

        # 📌 Habits
        if habit_logs_by_week:
            week_logs = habit_logs_by_week.get(week_start, [])
            if week_logs or any(
                week_start in [_monday_of(date.fromisoformat(l["date"])) for l in all_habit_logs]
            ):
                rows.append(["📌 HABITS", "", ""])
                for habit in all_habits:
                    habit_rows = _habit_week_rows(habit, week_start, week_logs)
                    rows.extend(habit_rows)
                rows.append(["", "", ""])

        # Spacer between weeks
        rows.append(["", "", ""])

    return rows


# ── Tab 2: Later ──────────────────────────────────────────────────────────────

def _build_later_rows(organized_groups: list) -> list:
    """
    organized_groups: [{"theme": str, "items": [{"content": str, "target_date": str}]}]
    """
    rows = [["Item", "Target Date"]]

    if not organized_groups:
        rows.append(["No later items yet. Use /later to add long-term goals.", ""])
        return rows

    for group in organized_groups:
        rows.append([f"── {group['theme'].upper()} ──", ""])
        for item in group.get("items", []):
            rows.append([item.get("content", ""), item.get("target_date", "—")])
        rows.append(["", ""])  # spacer

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
                    cells.append("·")
                else:
                    scheduled_past += 1
                    log = logs_by_habit_date.get((habit["id"], day_str))
                    if log is None:
                        cells.append("?")
                    elif log["completed"]:
                        completed += 1
                        cells.append("✅")
                    else:
                        cells.append("❌")

            pct = f"{completed}/{scheduled_past}" if scheduled_past else "—"
            rows.append([habit["name"]] + cells + [pct])

        rows.append(["", "", "", "", "", "", "", "", ""])

    return rows


# ── Public API ────────────────────────────────────────────────────────────────

def sync_to_sheets(
    all_accomplishments: list,
    focus_summaries: dict,
    organized_later: list,
    all_habits: list = None,
    all_habit_logs: list = None,
) -> str:
    """
    Rebuild both tabs from current data.

    focus_summaries: {week_start: summary_text}  — keyed by the TARGET week's Monday
    organized_later: output of organize_later_items()

    Returns spreadsheet URL.
    """
    spreadsheet = _get_spreadsheet()
    _ensure_sheets(spreadsheet)

    all_habits = all_habits or []
    all_habit_logs = all_habit_logs or []

    # Tab 1: Weekly Reviews
    weekly_sheet = spreadsheet.worksheet(SHEET_WEEKLY)
    weekly_rows = _build_weekly_review_rows(
        all_accomplishments, focus_summaries, all_habits, all_habit_logs
    )
    weekly_sheet.clear()
    if weekly_rows:
        weekly_sheet.update("A1", weekly_rows, value_input_option="RAW")
        weekly_sheet.format("C:C", {"wrapStrategy": "WRAP"})
    logger.info("Rebuilt Weekly Reviews sheet")

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

    return spreadsheet.url
