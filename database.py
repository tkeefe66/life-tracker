"""
Database layer — supports PostgreSQL (Railway) and SQLite (local dev).
Set DATABASE_URL to use PostgreSQL. Leave it blank to fall back to SQLite.
"""

import datetime
import logging
from contextlib import contextmanager
from datetime import date

import pytz

from config import DATABASE_URL, DATABASE_PATH, TIMEZONE


def _today() -> date:
    return datetime.datetime.now(pytz.timezone(TIMEZONE)).date()

logger = logging.getLogger(__name__)

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

    _init_v2_tables()

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
                entry_date TEXT,
                pending_dates TEXT DEFAULT '[]',
                later_item_draft TEXT,
                temp_data TEXT DEFAULT '{}',
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
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS people (
                id {serial} PRIMARY KEY,
                name TEXT NOT NULL,
                aliases TEXT[] DEFAULT '{{}}',
                relationship_type TEXT,
                status TEXT DEFAULT 'active',
                first_seen DATE,
                last_seen DATE,
                start_date DATE,
                end_date DATE,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_people_name_lower ON people(LOWER(name))")
        c.execute("""
            CREATE TABLE IF NOT EXISTS life_log_people (
                entry_id INTEGER NOT NULL REFERENCES life_log_entries(id) ON DELETE CASCADE,
                person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                PRIMARY KEY (entry_id, person_id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_people_person_id ON life_log_people(person_id)")
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS activity_log (
                id {serial} PRIMARY KEY,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TIMESTAMP,
                payload JSONB,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                promoted_to_life_log {bool_t} DEFAULT FALSE,
                UNIQUE(source, source_id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_activity_log_occurred_at ON activity_log(occurred_at)")
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
        for col, defn in [
            ("parent_id", "INTEGER REFERENCES life_log_entries(id) ON DELETE SET NULL"),
            ("story_type", "TEXT"),
            ("why_mattered", "TEXT"),
            ("highlights", "TEXT"),  # JSON-encoded list
            ("extras", "TEXT"),       # JSON-encoded dict
        ]:
            c.execute(f"ALTER TABLE life_log_entries ADD COLUMN IF NOT EXISTS {col} {defn}")
        c.execute(
            "CREATE INDEX IF NOT EXISTS ix_life_log_entries_parent_id "
            "ON life_log_entries(parent_id)"
        )
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
    def _add_col(c, table, col, defn):
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
        except Exception:
            pass

    with _cursor(write=True) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS accomplishments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL, category TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS weekly_focus (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL, content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
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
        c.execute("""
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                aliases TEXT DEFAULT '[]',
                relationship_type TEXT,
                status TEXT DEFAULT 'active',
                first_seen TEXT,
                last_seen TEXT,
                start_date TEXT,
                end_date TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_people_name_lower ON people(LOWER(name))")
        c.execute("""
            CREATE TABLE IF NOT EXISTS life_log_people (
                entry_id INTEGER NOT NULL REFERENCES life_log_entries(id) ON DELETE CASCADE,
                person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                PRIMARY KEY (entry_id, person_id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_people_person_id ON life_log_people(person_id)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT,
                payload TEXT,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                promoted_to_life_log INTEGER DEFAULT 0,
                UNIQUE(source, source_id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_activity_log_occurred_at ON activity_log(occurred_at)")
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
        for col, defn in [
            ("parent_id", "INTEGER REFERENCES life_log_entries(id)"),
            ("story_type", "TEXT"),
            ("why_mattered", "TEXT"),
            ("highlights", "TEXT"),
            ("extras", "TEXT"),
        ]:
            _add_col(c, "life_log_entries", col, defn)
        try:
            c.execute(
                "CREATE INDEX IF NOT EXISTS ix_life_log_entries_parent_id "
                "ON life_log_entries(parent_id)"
            )
        except Exception:
            pass
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


# ── v2 schema ─────────────────────────────────────────────────────────────────

def _init_v2_tables():
    serial = _serial()
    bool_t = _bool_type()
    with _cursor(write=True) as c:
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS checkins (
                id {serial} PRIMARY KEY,
                date TEXT NOT NULL,
                type TEXT NOT NULL,
                level INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, type)
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS delivery_orders (
                id {serial} PRIMARY KEY,
                gmail_message_id TEXT NOT NULL UNIQUE,
                service TEXT NOT NULL,
                subject TEXT DEFAULT '',
                ordered_at TEXT NOT NULL,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id {serial} PRIMARY KEY,
                gcal_event_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                is_social {bool_t},
                confidence REAL,
                classified_at TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS targets (
                metric TEXT PRIMARY KEY,
                direction TEXT NOT NULL,
                value INTEGER NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS weekly_reflections (
                id {serial} PRIMARY KEY,
                week_start TEXT NOT NULL UNIQUE,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        if USE_POSTGRES:
            c.execute("ALTER TABLE delivery_orders ADD COLUMN IF NOT EXISTS amount REAL")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(delivery_orders)").fetchall()]
            if "amount" not in cols:
                c.execute("ALTER TABLE delivery_orders ADD COLUMN amount REAL")

        if USE_POSTGRES:
            c.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS user_title TEXT")
            c.execute(f"ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS user_is_social {bool_t}")
            c.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'gcal'")
            c.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS amount REAL")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(calendar_events)").fetchall()]
            for name, defn in (("user_title", "TEXT"), ("user_is_social", bool_t),
                               ("source", "TEXT DEFAULT 'gcal'"), ("amount", "REAL")):
                if name not in cols:
                    c.execute(f"ALTER TABLE calendar_events ADD COLUMN {name} {defn}")


# ── Check-ins ─────────────────────────────────────────────────────────────────

def record_checkin(day, type_, level=None):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO checkins (date, type, level) VALUES ({p}, {p}, {p})
                ON CONFLICT(date, type) DO UPDATE SET level = excluded.level""",
            (day, type_, level),
        )


def delete_checkin(day, type_):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"DELETE FROM checkins WHERE date = {p} AND type = {p}", (day, type_))


def get_checkins_range(start, end):
    p = _p()
    with _cursor() as c:
        c.execute(
            f"SELECT date, type, level FROM checkins WHERE date >= {p} AND date <= {p} ORDER BY date",
            (start, end),
        )
        return [dict(r) for r in c.fetchall()]


# ── Delivery orders ───────────────────────────────────────────────────────────

def has_delivery_order(gmail_message_id):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT 1 FROM delivery_orders WHERE gmail_message_id = {p}", (gmail_message_id,))
        return c.fetchone() is not None


def add_delivery_order(gmail_message_id, service, ordered_at, subject, amount=None):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO delivery_orders (gmail_message_id, service, ordered_at, subject, amount)
                VALUES ({p}, {p}, {p}, {p}, {p}) ON CONFLICT(gmail_message_id) DO NOTHING""",
            (gmail_message_id, service, ordered_at, subject, amount),
        )
        return c.rowcount > 0


def find_delivery_order(service, day, subject):
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""SELECT id, amount FROM delivery_orders
                WHERE service = {p} AND substr(ordered_at, 1, 10) = {p} AND subject = {p}""",
            (service, day, subject),
        )
        row = c.fetchone()
        return dict(row) if row else None


def set_delivery_amount(order_id, amount):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE delivery_orders SET amount = {p} WHERE id = {p}", (amount, order_id))


def get_delivery_orders_range(start_day, end_day):
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""SELECT id, gmail_message_id, service, subject, ordered_at, amount FROM delivery_orders
                WHERE substr(ordered_at, 1, 10) >= {p} AND substr(ordered_at, 1, 10) <= {p}
                ORDER BY ordered_at""",
            (start_day, end_day),
        )
        return [dict(r) for r in c.fetchall()]


# ── Calendar events ───────────────────────────────────────────────────────────

def upsert_calendar_event(gcal_event_id, title, start_at, end_at):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO calendar_events (gcal_event_id, title, start_at, end_at)
                VALUES ({p}, {p}, {p}, {p})
                ON CONFLICT(gcal_event_id) DO UPDATE
                SET title = excluded.title, start_at = excluded.start_at, end_at = excluded.end_at""",
            (gcal_event_id, title, start_at, end_at),
        )


def event_needs_classification(gcal_event_id):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT is_social FROM calendar_events WHERE gcal_event_id = {p}", (gcal_event_id,))
        row = c.fetchone()
        return row is not None and row["is_social"] is None


def set_event_classification(gcal_event_id, is_social, confidence):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""UPDATE calendar_events
                SET is_social = {p}, confidence = {p}, classified_at = CURRENT_TIMESTAMP
                WHERE gcal_event_id = {p}""",
            (is_social, confidence, gcal_event_id),
        )


def _social_true():
    return "TRUE" if USE_POSTGRES else "1"


def _social_rows(rows):
    """Cast the resolved is_social column to a real bool — SQLite returns 0/1 ints."""
    out = [dict(r) for r in rows]
    for r in out:
        r["is_social"] = bool(r["is_social"])
    return out


def get_social_events_range(start_day, end_day):
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""SELECT gcal_event_id, COALESCE(user_title, title) AS title,
                       COALESCE(user_is_social, is_social) AS is_social, start_at, end_at,
                       source, amount
                FROM calendar_events
                WHERE COALESCE(user_is_social, is_social) = {_social_true()}
                  AND substr(end_at, 1, 10) >= {p} AND substr(end_at, 1, 10) <= {p}
                ORDER BY start_at""",
            (start_day, end_day),
        )
        return _social_rows(c.fetchall())


def get_events_for_day(day):
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""SELECT gcal_event_id, COALESCE(user_title, title) AS title,
                       COALESCE(user_is_social, is_social) AS is_social, start_at, end_at,
                       source, amount
                FROM calendar_events
                WHERE COALESCE(user_is_social, is_social) = {_social_true()} AND substr(start_at, 1, 10) = {p}
                ORDER BY start_at""",
            (day,),
        )
        return _social_rows(c.fetchall())


def add_manual_social_event(event_id, title, start_at, end_at, amount=None):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO calendar_events
                    (gcal_event_id, title, start_at, end_at, is_social, source, confidence, classified_at, amount)
                VALUES ({p}, {p}, {p}, {p}, {_social_true()}, 'manual', 1.0, CURRENT_TIMESTAMP, {p})""",
            (event_id, title, start_at, end_at, amount),
        )


def get_event(event_id):
    p = _p()
    with _cursor() as c:
        c.execute("SELECT * FROM calendar_events WHERE gcal_event_id = " + p, (event_id,))
        row = c.fetchone()
        return dict(row) if row else None


def set_event_overrides(event_id, updates: dict):
    """Partial UPDATE from a dict of column->value. Only mentioned columns are touched."""
    if not updates:
        return
    p = _p()
    allowed = {"user_title", "user_is_social", "amount"}
    cols = [k for k in updates if k in allowed]
    if not cols:
        return
    set_clause = ", ".join(f"{col} = {p}" for col in cols)
    values = [updates[col] for col in cols] + [event_id]
    with _cursor(write=True) as c:
        c.execute(f"UPDATE calendar_events SET {set_clause} WHERE gcal_event_id = {p}", values)


def delete_event(event_id):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"DELETE FROM calendar_events WHERE gcal_event_id = {p}", (event_id,))


def get_classification_examples(limit=10):
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""SELECT COALESCE(user_title, title) AS title, user_is_social
                FROM calendar_events
                WHERE user_is_social IS NOT NULL
                ORDER BY id DESC LIMIT {p}""",
            (limit,),
        )
        return [dict(r) for r in c.fetchall()]


# ── Targets & settings ────────────────────────────────────────────────────────

def seed_default_targets():
    from metrics import METRICS
    p = _p()
    with _cursor(write=True) as c:
        for key, meta in METRICS.items():
            c.execute(
                f"""INSERT INTO targets (metric, direction, value) VALUES ({p}, {p}, {p})
                    ON CONFLICT(metric) DO NOTHING""",
                (key, meta["direction"], meta["default_target"]),
            )


def get_targets():
    with _cursor() as c:
        c.execute("SELECT metric, direction, value FROM targets")
        return {r["metric"]: {"direction": r["direction"], "value": r["value"]} for r in c.fetchall()}


def set_target(metric, value):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE targets SET value = {p} WHERE metric = {p}", (value, metric))


def get_setting(key, default=None):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT value FROM app_settings WHERE key = {p}", (key,))
        row = c.fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO app_settings (key, value) VALUES ({p}, {p})
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )


# ── Weekly reflections ────────────────────────────────────────────────────────

def get_reflection(week_start):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT text FROM weekly_reflections WHERE week_start = {p}", (week_start,))
        row = c.fetchone()
        return row["text"] if row else None


def save_reflection(week_start, text):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO weekly_reflections (week_start, text) VALUES ({p}, {p})
                ON CONFLICT(week_start) DO UPDATE SET text = excluded.text""",
            (week_start, text),
        )
