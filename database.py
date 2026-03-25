import sqlite3
import json
import logging
from datetime import date, timedelta
from config import DATABASE_PATH

logger = logging.getLogger(__name__)

MAX_MISSED_DAYS = 30

DAYS_MAP = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _connect():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _add_column_if_missing(c, table, column, definition):
    """Safely add a column to an existing table (no-op if already exists)."""
    try:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except Exception:
        pass


def initialize_db():
    conn = _connect()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS accomplishments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS weekly_focus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS later_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            target_date TEXT,
            source TEXT DEFAULT 'manual',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            days_of_week TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS habit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id INTEGER NOT NULL REFERENCES habits(id),
            date TEXT NOT NULL,
            completed INTEGER,
            miss_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(habit_id, date)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS focus_summary_cache (
            week_start TEXT PRIMARY KEY,
            summary_text TEXT NOT NULL,
            entry_count INTEGER NOT NULL,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS later_org_cache (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            groups_json TEXT NOT NULL,
            item_count INTEGER NOT NULL,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS conversation_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            state TEXT NOT NULL DEFAULT 'idle',
            current_date TEXT,
            pending_dates TEXT DEFAULT '[]',
            later_item_draft TEXT,
            temp_data TEXT DEFAULT '{}',
            bot_start_date TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Schema migrations for existing databases
    _add_column_if_missing(c, "conversation_state", "later_item_draft", "TEXT")
    _add_column_if_missing(c, "conversation_state", "temp_data", "TEXT DEFAULT '{}'")

    c.execute(
        "INSERT OR IGNORE INTO conversation_state (id, state, bot_start_date) VALUES (1, 'idle', ?)",
        (date.today().isoformat(),),
    )

    conn.commit()
    conn.close()
    logger.info("Database initialized")


# ── Conversation state ────────────────────────────────────────────────────────

def get_state() -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM conversation_state WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else {}


def set_state(state: str, current_date: str = None, pending_dates: list = None,
              later_item_draft: str = None, temp_data: dict = None):
    pending_json = json.dumps(pending_dates if pending_dates is not None else [])
    temp_json = json.dumps(temp_data) if temp_data is not None else "{}"
    conn = _connect()
    conn.execute(
        """UPDATE conversation_state
           SET state=?, current_date=?, pending_dates=?, later_item_draft=?,
               temp_data=?, updated_at=CURRENT_TIMESTAMP
           WHERE id=1""",
        (state, current_date, pending_json, later_item_draft, temp_json),
    )
    conn.commit()
    conn.close()


# ── Accomplishments ───────────────────────────────────────────────────────────

def save_accomplishment(entry_date: str, category: str, content: str):
    conn = _connect()
    conn.execute(
        "INSERT INTO accomplishments (date, category, content) VALUES (?, ?, ?)",
        (entry_date, category, content),
    )
    conn.commit()
    conn.close()


def has_any_entry_for_date(entry_date: str) -> bool:
    conn = _connect()
    count = conn.execute(
        "SELECT COUNT(*) FROM accomplishments WHERE date=?", (entry_date,)
    ).fetchone()[0]
    conn.close()
    return count > 0


def get_accomplishments_for_week(week_start: str) -> list:
    start = date.fromisoformat(week_start)
    end = (start + timedelta(days=6)).isoformat()
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM accomplishments WHERE date >= ? AND date <= ? ORDER BY date, category",
        (week_start, end),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_accomplishments() -> list:
    conn = _connect()
    rows = conn.execute("SELECT * FROM accomplishments ORDER BY date, category").fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
    conn = _connect()
    conn.execute(
        "INSERT INTO weekly_focus (week_start, content) VALUES (?, ?)",
        (week_start, content),
    )
    conn.commit()
    conn.close()


def get_weekly_focus(week_start: str) -> list:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM weekly_focus WHERE week_start=? ORDER BY created_at DESC", (week_start,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_focus_entries() -> list:
    conn = _connect()
    rows = conn.execute("SELECT * FROM weekly_focus ORDER BY week_start").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Focus summary cache ───────────────────────────────────────────────────────

def get_cached_summary(week_start: str):
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM focus_summary_cache WHERE week_start=?", (week_start,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_cached_summary(week_start: str, summary_text: str, entry_count: int):
    conn = _connect()
    conn.execute(
        """INSERT INTO focus_summary_cache (week_start, summary_text, entry_count)
           VALUES (?, ?, ?)
           ON CONFLICT(week_start) DO UPDATE SET
               summary_text=excluded.summary_text,
               entry_count=excluded.entry_count,
               generated_at=CURRENT_TIMESTAMP""",
        (week_start, summary_text, entry_count),
    )
    conn.commit()
    conn.close()


# ── Later items ───────────────────────────────────────────────────────────────

def save_later_item(content: str, target_date: str, source: str = "manual"):
    conn = _connect()
    conn.execute(
        "INSERT INTO later_items (content, target_date, source) VALUES (?, ?, ?)",
        (content, target_date, source),
    )
    conn.commit()
    conn.close()


def get_all_later_items() -> list:
    conn = _connect()
    rows = conn.execute("SELECT * FROM later_items ORDER BY created_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_later_item_count() -> int:
    conn = _connect()
    count = conn.execute("SELECT COUNT(*) FROM later_items").fetchone()[0]
    conn.close()
    return count


# ── Later org cache ───────────────────────────────────────────────────────────

def get_cached_later_org():
    conn = _connect()
    row = conn.execute("SELECT * FROM later_org_cache WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else None


def save_cached_later_org(groups_json: str, item_count: int):
    conn = _connect()
    conn.execute(
        """INSERT INTO later_org_cache (id, groups_json, item_count)
           VALUES (1, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               groups_json=excluded.groups_json,
               item_count=excluded.item_count,
               generated_at=CURRENT_TIMESTAMP""",
        (groups_json, item_count),
    )
    conn.commit()
    conn.close()


# ── Habits ────────────────────────────────────────────────────────────────────

def save_habit(name: str, description: str, days_of_week: list) -> int:
    conn = _connect()
    c = conn.execute(
        "INSERT INTO habits (name, description, days_of_week) VALUES (?, ?, ?)",
        (name, description, json.dumps(days_of_week)),
    )
    habit_id = c.lastrowid
    conn.commit()
    conn.close()
    return habit_id


def get_habit(habit_id: int):
    conn = _connect()
    row = conn.execute("SELECT * FROM habits WHERE id=?", (habit_id,)).fetchone()
    conn.close()
    if not row:
        return None
    h = dict(row)
    h["days_of_week"] = json.loads(h["days_of_week"])
    return h


def get_all_active_habits() -> list:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM habits WHERE active=1 ORDER BY created_at"
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        h = dict(r)
        h["days_of_week"] = json.loads(h["days_of_week"])
        result.append(h)
    return result


def get_active_habits_for_weekday(weekday: int) -> list:
    """weekday: 0=Monday … 6=Sunday"""
    habits = get_all_active_habits()
    return [h for h in habits if weekday in h["days_of_week"]]


def get_unlogged_habits_for_date(date_str: str) -> list:
    """Active habits scheduled for date_str that have no log entry yet."""
    d = date.fromisoformat(date_str)
    scheduled = get_active_habits_for_weekday(d.weekday())
    if not scheduled:
        return []
    conn = _connect()
    logged_ids = {
        row[0] for row in conn.execute(
            "SELECT habit_id FROM habit_logs WHERE date=?", (date_str,)
        ).fetchall()
    }
    conn.close()
    return [h for h in scheduled if h["id"] not in logged_ids]


def log_habit(habit_id: int, date_str: str, completed: bool, miss_reason: str = None):
    conn = _connect()
    conn.execute(
        """INSERT INTO habit_logs (habit_id, date, completed, miss_reason)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(habit_id, date) DO UPDATE SET
               completed=excluded.completed,
               miss_reason=excluded.miss_reason""",
        (habit_id, date_str, 1 if completed else 0, miss_reason),
    )
    conn.commit()
    conn.close()


def get_habit_logs_for_week(week_start: str) -> list:
    start = date.fromisoformat(week_start)
    end = (start + timedelta(days=6)).isoformat()
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM habit_logs WHERE date >= ? AND date <= ? ORDER BY date",
        (week_start, end),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_habit_logs() -> list:
    conn = _connect()
    rows = conn.execute("SELECT * FROM habit_logs ORDER BY date").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def deactivate_habit(habit_id: int):
    conn = _connect()
    conn.execute("UPDATE habits SET active=0 WHERE id=?", (habit_id,))
    conn.commit()
    conn.close()
