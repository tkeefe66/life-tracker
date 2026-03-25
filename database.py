"""
Database layer — supports PostgreSQL (Railway) and SQLite (local dev).
Set DATABASE_URL to use PostgreSQL. Leave it blank to fall back to SQLite.
"""

import json
import logging
from contextlib import contextmanager
from datetime import date, timedelta

from config import DATABASE_URL, DATABASE_PATH

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
        # Add new columns safely
        for col, defn in [
            ("later_item_draft", "TEXT"),
            ("temp_data", "TEXT DEFAULT '{}'"),
        ]:
            c.execute(f"ALTER TABLE conversation_state ADD COLUMN IF NOT EXISTS {col} {defn}")

        p = _p()
        c.execute(
            f"INSERT INTO conversation_state (id, state, bot_start_date) VALUES (1, 'idle', {p}) "
            f"ON CONFLICT(id) DO NOTHING",
            (date.today().isoformat(),),
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
        for col, defn in [("later_item_draft", "TEXT"), ("temp_data", "TEXT DEFAULT '{}'")]:
            _add_col(c, "conversation_state", col, defn)

        c.execute(
            "INSERT OR IGNORE INTO conversation_state (id, state, bot_start_date) VALUES (1, 'idle', ?)",
            (date.today().isoformat(),),
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
    bot_start = date.fromisoformat(state.get("bot_start_date") or date.today().isoformat())
    cutoff = date.today() - timedelta(days=MAX_MISSED_DAYS)
    start = max(bot_start, cutoff)
    yesterday = date.today() - timedelta(days=1)
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
    if row and isinstance(row.get("days_of_week"), str):
        row["days_of_week"] = json.loads(row["days_of_week"])
    return row


def save_habit(name: str, description: str, days_of_week: list) -> int:
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"INSERT INTO habits (name, description, days_of_week) VALUES ({p},{p},{p}) RETURNING id",
            (name, description, json.dumps(days_of_week)),
        ) if USE_POSTGRES else c.execute(
            "INSERT INTO habits (name, description, days_of_week) VALUES (?,?,?)",
            (name, description, json.dumps(days_of_week)),
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
    return [h for h in get_all_active_habits() if weekday in h["days_of_week"]]


def get_unlogged_habits_for_date(date_str: str) -> list:
    d = date.fromisoformat(date_str)
    scheduled = get_active_habits_for_weekday(d.weekday())
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
