"""Wipe all DB data. Prompts for CONFIRM. Works on SQLite and Postgres."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db

TABLES = [
    "checkins", "delivery_orders", "calendar_events", "targets", "app_settings",
    # v1 archive tables
    "life_log_entries", "life_log_people", "people", "activity_log", "categories",
    "habits", "habit_logs", "accomplishments", "weekly_focus", "later_items",
    "focus_summary_cache", "later_org_cache", "conversation_state",
]


def main():
    answer = input("This deletes ALL data. Type CONFIRM to proceed: ")
    if answer.strip() != "CONFIRM":
        print("Aborted.")
        return
    db.initialize_db()
    with db._cursor(write=True) as c:
        for table in TABLES:
            try:
                c.execute(f"DELETE FROM {table}")
            except Exception as e:
                print(f"  skip {table}: {e}")
    print("Done.")


if __name__ == "__main__":
    main()
