"""
Database layer — supports PostgreSQL (Railway) and SQLite (local dev).
Set DATABASE_URL to use PostgreSQL. Leave it blank to fall back to SQLite.
"""

import datetime
import json
import logging
from contextlib import contextmanager
from datetime import date, timedelta

import pytz

from config import DATABASE_URL, DATABASE_PATH, TIMEZONE


def _today() -> date:
    return datetime.datetime.now(pytz.timezone(TIMEZONE)).date()

logger = logging.getLogger(__name__)

MAX_MISSED_DAYS = 30
USE_POSTGRES = bool(DATABASE_URL)

# ── Connection helpers ────────────────────────────────────────────────────────

@contextmanager
def _cursor(write=False):
    """Yield a dict-like cursor. Commits on exit if write=True."""
    if USE_POSTGRES:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
            if write:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
    else:
        import sqlite3
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            yield cur
            if write:
                conn.commit()
        finally:
            cur.close()
            conn.close()


def _p():
    """Return the correct placeholder character for the active database."""
    return "%s" if USE_POSTGRES else "?"


def _serial():
    return "SERIAL" if USE_POSTGRES else "INTEGER"


def _bool_type():
    return "BOOLEAN" if USE_POSTGRES else "INTEGER"


# ── Schema ────────────────────────────────────────────────────────────────────

def initialize_db():
    serial = _serial()
    bool_t = _bool_type()

    if USE_POSTGRES:
        _init_postgres(serial, bool_t)
    else:
        _init_sqlite(bool_t)

    logger.info("Database initialized (%s)", "PostgreSQL" if USE_POSTGRES else "SQLite")


def _init_postgres(serial, bool_t):
    with _cursor(write=True) as c:
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS accomplishments (
                id {serial} PRIMARY KEY,
                date TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS weekly_focus (
                id {serial} PRIMARY KEY,
                week_start TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS later_items (
                id {serial} PRIMARY KEY,
                content TEXT NOT NULL,
                target_date TEXT,
                source TEXT DEFAULT 'manual',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS habits (
                id {serial} PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                days_of_week TEXT NOT NULL,
                active {bool_t} DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS habit_logs (
                id {serial} PRIMARY KEY,
                habit_id INTEGER NOT NULL REFERENCES habits(id),
                date TEXT NOT NULL,
                completed {bool_t},
                miss_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(habit_id, date)
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS focus_summary_cache (
                week_start TEXT PRIMARY KEY,
                summary_text TEXT NOT NULL,
                entry_count INTEGER NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS later_org_cache (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                groups_json TEXT NOT NULL,
                item_count INTEGER NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS conversation_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT NOT NULL DEFAULT 'idle',
                entry_date TEXT,
                pending_dates TEXT DEFAULT '[]',
                later_item_draft TEXT,
                temp_data TEXT DEFAULT '{{}}',
                bot_start_date TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS calendar_sync_log (
                id {serial} PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                name TEXT PRIMARY KEY,
                active BOOLEAN DEFAULT TRUE,
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS life_log_entries (
                id {serial} PRIMARY KEY,
                date_start DATE NOT NULL,
                date_end DATE,
                categories TEXT[] NOT NULL DEFAULT '{{}}',
                description TEXT NOT NULL,
                location TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'confirmed',
                source TEXT NOT NULL DEFAULT 'manual',
                source_id TEXT,
                ai_proposed_at TIMESTAMP,
                user_confirmed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_entries_date_start ON life_log_entries(date_start)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_entries_status ON life_log_entries(status)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_entries_source_id ON life_log_entries(source_id)")
        # Seed initial categories
        INITIAL_CATEGORIES = [
            "Vacation", "Relationship", "Outdoors", "Skiing", "Concert",
            "Wedding", "Bachelor Party", "Life Event", "Visitors", "Tattoo",
            "Move/Housing", "Job/Career", "Health", "Achievement", "Pet", "Loss",
        ]
        for name in INITIAL_CATEGORIES:
            c.execute(
                f"INSERT INTO categories (name) VALUES ({_p()}) ON CONFLICT DO NOTHING",
                (name,),
            )
        # Add new columns safely
        for col, defn in [
            ("later_item_draft", "TEXT"),
            ("temp_data", "TEXT DEFAULT '{}'"),
        ]:
            c.execute(f"ALTER TABLE conversation_state ADD COLUMN IF NOT EXISTS {col} {defn}")
        for col, defn in [
            ("status", "TEXT DEFAULT 'pending'"),
            ("ai_status", "TEXT"),
            ("ai_notes", "TEXT"),
            ("event_id", "TEXT"),
            ("end_date", "TEXT"),
        ]:
            c.execute(f"ALTER TABLE later_items ADD COLUMN IF NOT EXISTS {col} {defn}")
        for col, defn in [
            ("sheet_synced", "BOOLEAN DEFAULT FALSE"),
            ("sheet_deleted", "BOOLEAN DEFAULT FALSE"),
        ]:
            c.execute(f"ALTER TABLE accomplishments ADD COLUMN IF NOT EXISTS {col} {defn}")
            c.execute(f"ALTER TABLE weekly_focus ADD COLUMN IF NOT EXISTS {col} {defn}")
        for col, defn in [
            ("recurrence_type", "TEXT DEFAULT 'weekly'"),
            ("recurrence_config", "TEXT DEFAULT '{}'"),
        ]:
            c.execute(f"ALTER TABLE habits ADD COLUMN IF NOT EXISTS {col} {defn}")
        # Migrate Archie Meds to monthly_date (1st of every month)
        c.execute(
            "UPDATE habits SET recurrence_type='monthly_date', "
            "recurrence_config='{\"day\": 1}', days_of_week='[]' "
            "WHERE LOWER(name)='archie meds' AND recurrence_type='weekly'"
        )

        p = _p()
        c.execute(
            f"INSERT INTO conversation_state (id, state, bot_start_date) VALUES (1, 'idle', {p}) "
            f"ON CONFLICT(id) DO NOTHING",
            (_today().isoformat(),),
        )


def _init_sqlite(bool_t):
    import sqlite3

    def _add_col(c, table, col, defn):
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
        except Exception:
            pass

    with _cursor(write=True) as c:
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS accomplishments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL, category TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS weekly_focus (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL, content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS later_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL, target_date TEXT,
                source TEXT DEFAULT 'manual',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, description TEXT NOT NULL,
                days_of_week TEXT NOT NULL,
                active {bool_t} DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS habit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL REFERENCES habits(id),
                date TEXT NOT NULL, completed {bool_t},
                miss_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(habit_id, date)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS focus_summary_cache (
                week_start TEXT PRIMARY KEY,
                summary_text TEXT NOT NULL, entry_count INTEGER NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS later_org_cache (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                groups_json TEXT NOT NULL, item_count INTEGER NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversation_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT NOT NULL DEFAULT 'idle',
                entry_date TEXT, pending_dates TEXT DEFAULT '[]',
                later_item_draft TEXT, temp_data TEXT DEFAULT '{}',
                bot_start_date TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS calendar_sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                name TEXT PRIMARY KEY,
                active INTEGER DEFAULT 1,
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS life_log_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date_start TEXT NOT NULL,
                date_end TEXT,
                categories TEXT NOT NULL DEFAULT '[]',
                description TEXT NOT NULL,
                location TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'confirmed',
                source TEXT NOT NULL DEFAULT 'manual',
                source_id TEXT,
                ai_proposed_at TEXT,
                user_confirmed_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_entries_date_start ON life_log_entries(date_start)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_entries_status ON life_log_entries(status)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_entries_source_id ON life_log_entries(source_id)")
        # Seed initial categories
        INITIAL_CATEGORIES = [
            "Vacation", "Relationship", "Outdoors", "Skiing", "Concert",
            "Wedding", "Bachelor Party", "Life Event", "Visitors", "Tattoo",
            "Move/Housing", "Job/Career", "Health", "Achievement", "Pet", "Loss",
        ]
        for name in INITIAL_CATEGORIES:
            c.execute(
                "INSERT OR IGNORE INTO categories (name) VALUES (?)",
                (name,),
            )
        for col, defn in [("later_item_draft", "TEXT"), ("temp_data", "TEXT DEFAULT '{}'")]:
            _add_col(c, "conversation_state", col, defn)
        for col, defn in [
            ("status", "TEXT DEFAULT 'pending'"),
            ("ai_status", "TEXT"),
            ("ai_notes", "TEXT"),
            ("event_id", "TEXT"),
            ("end_date", "TEXT"),
        ]:
            _add_col(c, "later_items", col, defn)
        for table in ["accomplishments", "weekly_focus"]:
            _add_col(c, table, "sheet_synced", "INTEGER DEFAULT 0")
            _add_col(c, table, "sheet_deleted", "INTEGER DEFAULT 0")
        _add_col(c, "habits", "recurrence_type", "TEXT DEFAULT 'weekly'")
        _add_col(c, "habits", "recurrence_config", "TEXT DEFAULT '{}'")
        # Migrate Archie Meds to monthly_date (1st of every month)
        c.execute(
            "UPDATE habits SET recurrence_type='monthly_date', "
            "recurrence_config='{\"day\": 1}', days_of_week='[]' "
            "WHERE name LIKE 'Archie Meds' AND (recurrence_type IS NULL OR recurrence_type='weekly')"
        )

        c.execute(
            "INSERT OR IGNORE INTO conversation_state (id, state, bot_start_date) VALUES (1, 'idle', ?)",
            (_today().isoformat(),),
        )


def _row(r):
    return dict(r) if r else None


def _rows(rs):
    return [dict(r) for r in rs]


# ── Conversation state ────────────────────────────────────────────────────────

def get_state() -> dict:
    p = _p()
    with _cursor() as c:
        c.execute("SELECT * FROM conversation_state WHERE id = 1")
        return _row(c.fetchone()) or {}


def set_state(state: str, current_date: str = None, pending_dates: list = None,
              later_item_draft: str = None, temp_data: dict = None):
    p = _p()
    pending_json = json.dumps(pending_dates if pending_dates is not None else [])
    temp_json = json.dumps(temp_data) if temp_data is not None else "{}"
    with _cursor(write=True) as c:
        c.execute(
            f"""UPDATE conversation_state
               SET state={p}, entry_date={p}, pending_dates={p},
                   later_item_draft={p}, temp_data={p}, updated_at=CURRENT_TIMESTAMP
               WHERE id=1""",
            (state, current_date, pending_json, later_item_draft, temp_json),
        )


# ── Accomplishments ───────────────────────────────────────────────────────────

def save_accomplishment(entry_date: str, category: str, content: str):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"INSERT INTO accomplishments (date, category, content) VALUES ({p},{p},{p})",
            (entry_date, category, content),
        )


def has_any_entry_for_date(entry_date: str) -> bool:
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT COUNT(*) FROM accomplishments WHERE date={p}", (entry_date,))
        row = c.fetchone()
        count = row[0] if not USE_POSTGRES else row["count"]
        return count > 0


def get_accomplishments_for_week(week_start: str) -> list:
    p = _p()
    start = date.fromisoformat(week_start)
    end = (start + timedelta(days=6)).isoformat()
    with _cursor() as c:
        c.execute(
            f"SELECT * FROM accomplishments WHERE date>={p} AND date<={p} ORDER BY date, category",
            (week_start, end),
        )
        return _rows(c.fetchall())


def get_all_accomplishments() -> list:
    with _cursor() as c:
        c.execute("SELECT * FROM accomplishments ORDER BY date, category")
        return _rows(c.fetchall())


def get_missed_dates() -> list:
    state = get_state()
    bot_start = date.fromisoformat(state.get("bot_start_date") or _today().isoformat())
    cutoff = _today() - timedelta(days=MAX_MISSED_DAYS)
    start = max(bot_start, cutoff)
    yesterday = _today() - timedelta(days=1)
    missed = []
    current = start
    while current <= yesterday:
        if not has_any_entry_for_date(current.isoformat()):
            missed.append(current.isoformat())
        current += timedelta(days=1)
    return missed


# ── Weekly focus ──────────────────────────────────────────────────────────────

def save_weekly_focus(week_start: str, content: str):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"INSERT INTO weekly_focus (week_start, content) VALUES ({p},{p})",
            (week_start, content),
        )


def get_weekly_focus(week_start: str) -> list:
    p = _p()
    with _cursor() as c:
        c.execute(
            f"SELECT * FROM weekly_focus WHERE week_start={p} ORDER BY created_at DESC",
            (week_start,),
        )
        return _rows(c.fetchall())


def get_all_focus_entries() -> list:
    with _cursor() as c:
        c.execute("SELECT * FROM weekly_focus ORDER BY week_start")
        return _rows(c.fetchall())


# ── Sheet sync tracking ───────────────────────────────────────────────────────

def get_all_accomplishments_for_sync() -> list:
    """All accomplishments not deleted from Sheets, ordered for sync."""
    with _cursor() as c:
        if USE_POSTGRES:
            c.execute("SELECT * FROM accomplishments WHERE sheet_deleted=FALSE ORDER BY date, category")
        else:
            c.execute("SELECT * FROM accomplishments WHERE sheet_deleted=0 ORDER BY date, category")
        return _rows(c.fetchall())


def get_all_focus_for_sync() -> list:
    """All focus entries not deleted from Sheets, ordered for sync."""
    with _cursor() as c:
        if USE_POSTGRES:
            c.execute("SELECT * FROM weekly_focus WHERE sheet_deleted=FALSE ORDER BY week_start, created_at")
        else:
            c.execute("SELECT * FROM weekly_focus WHERE sheet_deleted=0 ORDER BY week_start, created_at")
        return _rows(c.fetchall())


def _bulk_update(table: str, column: str, set_true: bool, ids: list):
    if not ids:
        return
    with _cursor(write=True) as c:
        if USE_POSTGRES:
            pg_val = "TRUE" if set_true else "FALSE"
            c.execute(f"UPDATE {table} SET {column}={pg_val} WHERE id = ANY(%s)", (ids,))
        else:
            sqlite_val = 1 if set_true else 0
            placeholders = ",".join(["?"] * len(ids))
            c.execute(
                f"UPDATE {table} SET {column}={sqlite_val} WHERE id IN ({placeholders})",
                list(ids),
            )


def mark_accomplishments_synced(ids: list):
    _bulk_update("accomplishments", "sheet_synced", True, ids)


def mark_accomplishments_sheet_deleted(ids: list):
    _bulk_update("accomplishments", "sheet_deleted", True, ids)


def mark_focus_synced(ids: list):
    _bulk_update("weekly_focus", "sheet_synced", True, ids)


def mark_focus_sheet_deleted(ids: list):
    _bulk_update("weekly_focus", "sheet_deleted", True, ids)


def update_accomplishment_fields(entry_id: int, category: str, content: str):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE accomplishments SET category={p}, content={p} WHERE id={p}",
            (category, content, entry_id),
        )


def update_focus_content(entry_id: int, content: str):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE weekly_focus SET content={p} WHERE id={p}",
            (content, entry_id),
        )


# ── Focus summary cache ───────────────────────────────────────────────────────

def get_cached_summary(week_start: str):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT * FROM focus_summary_cache WHERE week_start={p}", (week_start,))
        return _row(c.fetchone())


def save_cached_summary(week_start: str, summary_text: str, entry_count: int):
    p = _p()
    with _cursor(write=True) as c:
        if USE_POSTGRES:
            c.execute(
                f"""INSERT INTO focus_summary_cache (week_start, summary_text, entry_count)
                    VALUES ({p},{p},{p})
                    ON CONFLICT(week_start) DO UPDATE SET
                        summary_text=EXCLUDED.summary_text,
                        entry_count=EXCLUDED.entry_count,
                        generated_at=CURRENT_TIMESTAMP""",
                (week_start, summary_text, entry_count),
            )
        else:
            c.execute(
                """INSERT INTO focus_summary_cache (week_start, summary_text, entry_count)
                   VALUES (?,?,?)
                   ON CONFLICT(week_start) DO UPDATE SET
                       summary_text=excluded.summary_text,
                       entry_count=excluded.entry_count,
                       generated_at=CURRENT_TIMESTAMP""",
                (week_start, summary_text, entry_count),
            )


# ── Later items ───────────────────────────────────────────────────────────────

def save_later_item(content: str, target_date: str, source: str = "manual"):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"INSERT INTO later_items (content, target_date, source) VALUES ({p},{p},{p})",
            (content, target_date, source),
        )


def get_all_later_items() -> list:
    with _cursor() as c:
        c.execute("SELECT * FROM later_items ORDER BY created_at")
        return _rows(c.fetchall())


def get_later_item_count() -> int:
    with _cursor() as c:
        c.execute("SELECT COUNT(*) FROM later_items")
        row = c.fetchone()
        return row[0] if not USE_POSTGRES else row["count"]


# ── Later org cache ───────────────────────────────────────────────────────────

def get_cached_later_org():
    with _cursor() as c:
        c.execute("SELECT * FROM later_org_cache WHERE id=1")
        return _row(c.fetchone())


def save_cached_later_org(groups_json: str, item_count: int):
    p = _p()
    with _cursor(write=True) as c:
        if USE_POSTGRES:
            c.execute(
                f"""INSERT INTO later_org_cache (id, groups_json, item_count)
                    VALUES (1,{p},{p})
                    ON CONFLICT(id) DO UPDATE SET
                        groups_json=EXCLUDED.groups_json,
                        item_count=EXCLUDED.item_count,
                        generated_at=CURRENT_TIMESTAMP""",
                (groups_json, item_count),
            )
        else:
            c.execute(
                """INSERT INTO later_org_cache (id, groups_json, item_count)
                   VALUES (1,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       groups_json=excluded.groups_json,
                       item_count=excluded.item_count,
                       generated_at=CURRENT_TIMESTAMP""",
                (groups_json, item_count),
            )


# ── Habits ────────────────────────────────────────────────────────────────────

def _unpack_habit(row: dict) -> dict:
    if row:
        if isinstance(row.get("days_of_week"), str):
            row["days_of_week"] = json.loads(row["days_of_week"])
        if isinstance(row.get("recurrence_config"), str):
            row["recurrence_config"] = json.loads(row["recurrence_config"] or "{}")
        elif not row.get("recurrence_config"):
            row["recurrence_config"] = {}
        if not row.get("recurrence_type"):
            row["recurrence_type"] = "weekly"
    return row


def _habit_scheduled_for_date(habit: dict, d: date) -> bool:
    """Return True if the habit is scheduled to occur on the given date."""
    rtype = habit.get("recurrence_type") or "weekly"
    config = habit.get("recurrence_config") or {}
    if rtype == "weekly":
        return d.weekday() in (habit.get("days_of_week") or [])
    elif rtype == "monthly_date":
        return d.day == config.get("day", 1)
    elif rtype == "monthly_weekday":
        if d.weekday() != config.get("weekday", 0):
            return False
        return (d.day - 1) // 7 + 1 == config.get("week", 1)
    return False


def save_habit(name: str, description: str, days_of_week: list,
               recurrence_type: str = "weekly", recurrence_config: dict = None) -> int:
    p = _p()
    recurrence_config = recurrence_config or {}
    with _cursor(write=True) as c:
        c.execute(
            f"INSERT INTO habits (name, description, days_of_week, recurrence_type, recurrence_config) "
            f"VALUES ({p},{p},{p},{p},{p}) RETURNING id",
            (name, description, json.dumps(days_of_week), recurrence_type, json.dumps(recurrence_config)),
        ) if USE_POSTGRES else c.execute(
            "INSERT INTO habits (name, description, days_of_week, recurrence_type, recurrence_config) "
            "VALUES (?,?,?,?,?)",
            (name, description, json.dumps(days_of_week), recurrence_type, json.dumps(recurrence_config)),
        )
        if USE_POSTGRES:
            return c.fetchone()["id"]
        else:
            return c.lastrowid


def get_habit(habit_id: int):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT * FROM habits WHERE id={p}", (habit_id,))
        return _unpack_habit(_row(c.fetchone()))


def get_all_active_habits() -> list:
    with _cursor() as c:
        c.execute("SELECT * FROM habits WHERE active=TRUE ORDER BY created_at"
                  if USE_POSTGRES else
                  "SELECT * FROM habits WHERE active=1 ORDER BY created_at")
        return [_unpack_habit(r) for r in _rows(c.fetchall())]


def get_active_habits_for_weekday(weekday: int) -> list:
    """Legacy: filter by weekday only. Use get_habits_scheduled_for_date for full recurrence support."""
    return [h for h in get_all_active_habits() if weekday in (h.get("days_of_week") or [])]


def get_habits_scheduled_for_date(date_str: str) -> list:
    """Return all active habits scheduled for the given date, supporting all recurrence types."""
    d = date.fromisoformat(date_str)
    return [h for h in get_all_active_habits() if _habit_scheduled_for_date(h, d)]


def get_unlogged_habits_for_date(date_str: str) -> list:
    d = date.fromisoformat(date_str)
    scheduled = get_habits_scheduled_for_date(date_str)
    if not scheduled:
        return []
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT habit_id FROM habit_logs WHERE date={p}", (date_str,))
        logged_ids = {r["habit_id"] for r in _rows(c.fetchall())}
    return [h for h in scheduled if h["id"] not in logged_ids]


def log_habit(habit_id: int, date_str: str, completed: bool, miss_reason: str = None):
    p = _p()
    with _cursor(write=True) as c:
        if USE_POSTGRES:
            c.execute(
                f"""INSERT INTO habit_logs (habit_id, date, completed, miss_reason)
                    VALUES ({p},{p},{p},{p})
                    ON CONFLICT(habit_id, date) DO UPDATE SET
                        completed=EXCLUDED.completed, miss_reason=EXCLUDED.miss_reason""",
                (habit_id, date_str, completed, miss_reason),
            )
        else:
            c.execute(
                """INSERT INTO habit_logs (habit_id, date, completed, miss_reason)
                   VALUES (?,?,?,?)
                   ON CONFLICT(habit_id, date) DO UPDATE SET
                       completed=excluded.completed, miss_reason=excluded.miss_reason""",
                (habit_id, date_str, 1 if completed else 0, miss_reason),
            )


def get_habit_logs_for_week(week_start: str) -> list:
    p = _p()
    start = date.fromisoformat(week_start)
    end = (start + timedelta(days=6)).isoformat()
    with _cursor() as c:
        c.execute(
            f"SELECT * FROM habit_logs WHERE date>={p} AND date<={p} ORDER BY date",
            (week_start, end),
        )
        return _rows(c.fetchall())


def get_all_habit_logs() -> list:
    with _cursor() as c:
        c.execute("SELECT * FROM habit_logs ORDER BY date")
        return _rows(c.fetchall())


def deactivate_habit(habit_id: int):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE habits SET active={'FALSE' if USE_POSTGRES else '0'} WHERE id={p}",
            (habit_id,),
        )


# ── Later items (extended) ────────────────────────────────────────────────────

def save_later_item_full(content: str, target_date: str, source: str = "manual",
                          event_id: str = None, end_date: str = None) -> int:
    p = _p()
    with _cursor(write=True) as c:
        if USE_POSTGRES:
            c.execute(
                f"""INSERT INTO later_items (content, target_date, source, event_id, end_date)
                    VALUES ({p},{p},{p},{p},{p}) RETURNING id""",
                (content, target_date, source, event_id, end_date),
            )
            return c.fetchone()["id"]
        else:
            c.execute(
                "INSERT INTO later_items (content, target_date, source, event_id, end_date) VALUES (?,?,?,?,?)",
                (content, target_date, source, event_id, end_date),
            )
            return c.lastrowid


def update_later_item_ai(item_id: int, ai_status: str, ai_notes: str):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE later_items SET ai_status={p}, ai_notes={p} WHERE id={p}",
            (ai_status, ai_notes, item_id),
        )


def get_later_items_pending_ai() -> list:
    """Return later items that have no AI assessment yet."""
    with _cursor() as c:
        c.execute(
            "SELECT * FROM later_items WHERE ai_status IS NULL ORDER BY created_at"
        )
        return _rows(c.fetchall())


def delete_later_items_matching(keywords: list) -> int:
    """Delete later items whose content matches any of the given keywords (case-insensitive).
    Returns the number of rows deleted."""
    if not keywords:
        return 0
    p = _p()
    with _cursor(write=True) as c:
        deleted = 0
        for kw in keywords:
            if USE_POSTGRES:
                c.execute(
                    f"DELETE FROM later_items WHERE LOWER(content) LIKE {p}",
                    (f"%{kw.lower()}%",),
                )
            else:
                c.execute(
                    "DELETE FROM later_items WHERE LOWER(content) LIKE ?",
                    (f"%{kw.lower()}%",),
                )
            deleted += c.rowcount
        return deleted


# ── Calendar sync log ─────────────────────────────────────────────────────────

def is_event_synced(event_id: str) -> bool:
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT 1 FROM calendar_sync_log WHERE event_id={p}", (event_id,))
        return c.fetchone() is not None


def mark_event_synced(event_id: str):
    p = _p()
    with _cursor(write=True) as c:
        if USE_POSTGRES:
            c.execute(
                f"INSERT INTO calendar_sync_log (event_id) VALUES ({p}) ON CONFLICT DO NOTHING",
                (event_id,),
            )
        else:
            c.execute(
                "INSERT OR IGNORE INTO calendar_sync_log (event_id) VALUES (?)",
                (event_id,),
            )


# ── Life Log: Categories ──────────────────────────────────────────────────────

def get_active_categories() -> list:
    """Return all categories currently active, ordered by name."""
    with _cursor() as c:
        if USE_POSTGRES:
            c.execute("SELECT * FROM categories WHERE active=TRUE ORDER BY name")
        else:
            c.execute("SELECT * FROM categories WHERE active=1 ORDER BY name")
        return _rows(c.fetchall())


def add_category(name: str):
    p = _p()
    with _cursor(write=True) as c:
        if USE_POSTGRES:
            c.execute(
                f"INSERT INTO categories (name) VALUES ({p}) ON CONFLICT DO NOTHING",
                (name,),
            )
        else:
            c.execute(
                "INSERT OR IGNORE INTO categories (name) VALUES (?)",
                (name,),
            )


def deactivate_category(name: str):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE categories SET active={'FALSE' if USE_POSTGRES else '0'} WHERE name={p}",
            (name,),
        )


def increment_category_usage(name: str):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE categories SET usage_count = usage_count + 1 WHERE name={p}",
            (name,),
        )


# ── Life Log: Entries ─────────────────────────────────────────────────────────

def _serialize_categories(categories: list):
    """Postgres uses array; SQLite stores JSON."""
    return categories if USE_POSTGRES else json.dumps(categories)


def _deserialize_categories(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    return json.loads(raw)


def _unpack_life_log_entry(row):
    if row is None:
        return None
    row["categories"] = _deserialize_categories(row.get("categories"))
    return row


def save_life_log_entry(
    date_start, date_end, categories, description,
    location, notes, status, source, source_id,
) -> int:
    p = _p()
    cats = _serialize_categories(categories)
    with _cursor(write=True) as c:
        if USE_POSTGRES:
            c.execute(
                f"""INSERT INTO life_log_entries
                    (date_start, date_end, categories, description, location, notes,
                     status, source, source_id, user_confirmed_at)
                    VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p}, CURRENT_TIMESTAMP)
                    RETURNING id""",
                (date_start, date_end, cats, description, location, notes,
                 status, source, source_id),
            )
            return c.fetchone()["id"]
        else:
            c.execute(
                """INSERT INTO life_log_entries
                   (date_start, date_end, categories, description, location, notes,
                    status, source, source_id, user_confirmed_at)
                   VALUES (?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)""",
                (date_start, date_end, cats, description, location, notes,
                 status, source, source_id),
            )
            return c.lastrowid


def get_life_log_entry(entry_id: int):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT * FROM life_log_entries WHERE id={p}", (entry_id,))
        return _unpack_life_log_entry(_row(c.fetchone()))


def get_life_log_entries_in_range(date_from: str, date_to: str) -> list:
    p = _p()
    with _cursor() as c:
        c.execute(
            f"SELECT * FROM life_log_entries "
            f"WHERE date_start>={p} AND date_start<={p} AND status='confirmed' "
            f"ORDER BY date_start, id",
            (date_from, date_to),
        )
        return [_unpack_life_log_entry(r) for r in _rows(c.fetchall())]


def get_all_life_log_entries() -> list:
    """All confirmed/upcoming entries across all time, ordered by date."""
    with _cursor() as c:
        c.execute(
            "SELECT * FROM life_log_entries WHERE status IN ('confirmed','upcoming') "
            "ORDER BY date_start, id"
        )
        return [_unpack_life_log_entry(r) for r in _rows(c.fetchall())]


def update_life_log_entry(
    entry_id: int, categories: list, description: str,
    location, notes,
):
    p = _p()
    cats = _serialize_categories(categories)
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE life_log_entries SET categories={p}, description={p}, "
            f"location={p}, notes={p} WHERE id={p}",
            (cats, description, location, notes, entry_id),
        )


def set_entry_status(entry_id: int, status: str):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE life_log_entries SET status={p} WHERE id={p}",
            (status, entry_id),
        )
