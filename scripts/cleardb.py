"""
Clear all data from the database and reinitialize the schema.
Run from the project root: python scripts/cleardb.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_URL, DATABASE_PATH

TABLES = [
    "habit_logs",
    "habits",
    "accomplishments",
    "weekly_focus",
    "later_items",
    "focus_summary_cache",
    "later_org_cache",
    "calendar_sync_log",
    "conversation_state",
]


def cleardb():
    confirm = input("This will DELETE ALL DATA. Type 'yes' to confirm: ").strip()
    if confirm != "yes":
        print("Aborted.")
        return

    if DATABASE_URL:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        for table in TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        conn.commit()
        cur.close()
        conn.close()
        print("PostgreSQL tables dropped.")
    else:
        import sqlite3
        if os.path.exists(DATABASE_PATH):
            conn = sqlite3.connect(DATABASE_PATH)
            for table in TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.commit()
            conn.close()
        print(f"SQLite tables dropped ({DATABASE_PATH}).")

    import database as db
    db.initialize_db()
    print("Database reinitialized.")


if __name__ == "__main__":
    cleardb()
