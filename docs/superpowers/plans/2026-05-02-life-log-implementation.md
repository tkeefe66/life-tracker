# Life Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the daily-accomplishments capture model with a Life Log — a 30-year memoir substrate fed by Google Calendar (passive) and a `/log` command (active), with people as first-class entities and Telegram natural-language queries for retrieval.

**Architecture:** Three logical streams kept physically separate — `life_log_entries` (curated memoir), `activity_log` (raw source mirror, for future insights), `habits` (existing, untouched). New AI module `ai_life_log.py` for all Life Log Claude calls. New `handlers/` directory for Life Log Telegram handlers (avoids growing the already-large `bot.py`). Sheets stays the durable canonical store via new `Life Log` and `People` tabs.

**Tech Stack:** Python 3.11+, python-telegram-bot 21.9, anthropic SDK (Claude Haiku 4.5), psycopg2 (Postgres prod) / sqlite3 (local), gspread for Sheets, pytest (new) for tests.

**Spec:** `docs/superpowers/specs/2026-05-02-life-log-design.md`

---

## Milestones

The plan groups tasks into logical milestones. Each milestone produces something testable.

- **M0:** Test infrastructure (3 tasks)
- **M1:** Schema + DB access functions (5 tasks)
- **M2:** AI module — `ai_life_log.py` (5 tasks)
- **M3:** `/log` command + people entity flow (6 tasks)
- **M4:** Calendar passive ingestion (5 tasks)
- **M5:** Relationship arc tracking (3 tasks)
- **M6:** Sheets sync — Life Log + People tabs (4 tasks)
- **M7:** Telegram natural-language queries (5 tasks)
- **M8:** Spreadsheet backfill script (3 tasks)
- **M9:** Calendar history backfill script (3 tasks)
- **M10:** Cutover — deprecate old commands & Sheet tabs (3 tasks)

---

## File Structure

### New files
| Path | Responsibility |
|---|---|
| `tests/conftest.py` | pytest fixtures: in-memory SQLite, mock anthropic, mock telegram bot |
| `tests/test_lifelog_db.py` | Tests for Life Log DB functions |
| `tests/test_ai_life_log.py` | Tests for AI parsing/proposal calls (mocked) |
| `tests/test_log_command.py` | Tests for `/log` command handler |
| `tests/test_lifelog_proposals.py` | Tests for calendar proposal flow |
| `tests/test_lifelog_queries.py` | Tests for natural-language query handler |
| `tests/test_lifelog_sheets.py` | Tests for Sheets sync (mocked gspread) |
| `tests/test_imports.py` | Tests for backfill scripts (mocked) |
| `pytest.ini` | Pytest config |
| `ai_life_log.py` | All Life Log Claude calls — propose, parse, extract, query |
| `handlers/__init__.py` | Package init |
| `handlers/log_command.py` | `/log` command handler + freeform parsing for Life Log |
| `handlers/lifelog_proposals.py` | Confirm/edit/skip handlers for AI proposals |
| `handlers/lifelog_queries.py` | Natural-language query handler |
| `handlers/people.py` | `/people` command — list, merge, rename |
| `jobs/lifelog_realtime.py` | On calendar add → high-confidence proposals |
| `jobs/lifelog_dayafter.py` | Morning ping for day-after proposals |
| `jobs/lifelog_sunday.py` | Sunday digest of "maybes" |
| `jobs/lifelog_categories_review.py` | Monthly category recommendation |
| `services/lifelog_query_service.py` | Tool-using LLM for natural language Q&A |
| `scripts/import_life_log_spreadsheet.py` | One-time spreadsheet → Life Log import |
| `scripts/import_calendar_history.py` | Calendar history scan |

### Modified files
| Path | Changes |
|---|---|
| `requirements.txt` | Add `pytest`, `pytest-asyncio` |
| `database.py` | Add new tables + access functions for `life_log_entries`, `people`, `life_log_people`, `activity_log`, `categories` |
| `google_sheets.py` | Add `Life Log` and `People` tab sync; deprecate Weekly Reviews + Later writes |
| `bot.py` | Register new handlers + jobs; remove old `/update` `/work` `/personal` `/focus` `/later` handlers in M10 |
| `jobs/daily_calendar.py` | Replace target — write to `activity_log` and feed proposal queue instead of `later_items` |
| `config.py` | Add `LIFE_LOG_IMPORT_SHEET_ID` for backfill source spreadsheet |
| `CLAUDE.md` | Update with Life Log architecture; remove deprecated commands |

### Files kept untouched
- `services/calendar_service.py` (reused as-is)
- `jobs/daily_ai_status.py` (becomes deprecated but no need to touch)
- `jobs/monthly_forward.py` (kept — calendar-forward planning still useful)
- All habit-related code paths

---

## M0 — Test Infrastructure

### Task 0.1: Add pytest dependencies and config

**Files:**
- Modify: `requirements.txt`
- Create: `pytest.ini`

- [ ] **Step 1: Add pytest deps to requirements.txt**

```
# append to requirements.txt
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 2: Create pytest.ini**

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
addopts = -v --tb=short
```

- [ ] **Step 3: Install deps**

Run: `pip install -r requirements.txt`
Expected: pytest 8.x installed.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt pytest.ini
git commit -m "chore: add pytest and pytest-asyncio for test infra"
```

### Task 0.2: Create test fixtures

**Files:**
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`

- [ ] **Step 1: Create empty package init**

```bash
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 2: Write conftest.py with shared fixtures**

```python
"""Shared pytest fixtures."""
import os
import tempfile
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def temp_db_path(monkeypatch):
    """Fresh SQLite DB per test, isolated from local.db."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_PATH", path)
    monkeypatch.setenv("DATABASE_URL", "")  # force SQLite
    # Force re-import of config so DATABASE_PATH picks up
    import importlib
    import config
    importlib.reload(config)
    import database
    importlib.reload(database)
    database.initialize_db()
    yield path
    os.unlink(path)


@pytest.fixture
def mock_anthropic(monkeypatch):
    """Replace anthropic.Anthropic with a mock that returns canned JSON."""
    from unittest.mock import MagicMock
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="{}")]
    mock_client.messages.create.return_value = mock_response

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: mock_client)
    # Reset the cached client in ai_life_log if it was already imported
    try:
        import ai_life_log
        ai_life_log._client = None
    except ImportError:
        pass
    return mock_client


@pytest.fixture
def mock_bot():
    """Mock telegram Bot for handler tests."""
    bot = MagicMock()
    bot.send_message = MagicMock(return_value=None)
    return bot
```

- [ ] **Step 3: Verify fixtures load**

Run: `pytest tests/ -v`
Expected: 0 tests collected, no errors loading conftest.py.

- [ ] **Step 4: Commit**

```bash
git add tests/__init__.py tests/conftest.py
git commit -m "test: add pytest fixtures for DB, anthropic, and telegram bot"
```

### Task 0.3: Smoke test that DB initializes

**Files:**
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Write smoke test**

```python
"""Smoke test — DB and config load successfully."""

def test_database_initializes(temp_db_path):
    import database
    state = database.get_state()
    assert state.get("state") == "idle"
```

- [ ] **Step 2: Run and verify**

Run: `pytest tests/test_smoke.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_smoke.py
git commit -m "test: add smoke test verifying DB initializes"
```

---

## M1 — Schema + DB Access Functions

### Task 1.1: Add `categories` table and seed initial values

**Files:**
- Modify: `database.py` (add to schema init functions)
- Create: `tests/test_lifelog_db.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for Life Log database functions."""
import database as db


def test_categories_seeded(temp_db_path):
    cats = db.get_active_categories()
    names = {c["name"] for c in cats}
    assert "Vacation" in names
    assert "Relationship" in names
    assert "Bachelor Party" in names
    assert "Loss" in names
    assert len(cats) == 16


def test_get_category_usage_count_zero_initially(temp_db_path):
    cats = db.get_active_categories()
    for c in cats:
        assert c["usage_count"] == 0
```

- [ ] **Step 2: Run test — should fail with AttributeError**

Run: `pytest tests/test_lifelog_db.py::test_categories_seeded -v`
Expected: FAIL — `db.get_active_categories` does not exist.

- [ ] **Step 3: Add `categories` table to both `_init_postgres` and `_init_sqlite` in `database.py`**

In `_init_postgres` after the `calendar_sync_log` block, add:

```python
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS categories (
                name TEXT PRIMARY KEY,
                active BOOLEAN DEFAULT TRUE,
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
```

In `_init_sqlite` after the `calendar_sync_log` block, add:

```python
        c.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                name TEXT PRIMARY KEY,
                active INTEGER DEFAULT 1,
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
```

After both schema blocks (just before the `INSERT INTO conversation_state`), add the seed:

```python
        # Seed initial categories
        INITIAL_CATEGORIES = [
            "Vacation", "Relationship", "Outdoors", "Skiing", "Concert",
            "Wedding", "Bachelor Party", "Life Event", "Visitors", "Tattoo",
            "Move/Housing", "Job/Career", "Health", "Achievement", "Pet", "Loss",
        ]
        for name in INITIAL_CATEGORIES:
            if USE_POSTGRES:
                c.execute(
                    f"INSERT INTO categories (name) VALUES ({_p()}) ON CONFLICT DO NOTHING",
                    (name,),
                )
            else:
                c.execute(
                    "INSERT OR IGNORE INTO categories (name) VALUES (?)",
                    (name,),
                )
```

- [ ] **Step 4: Add `get_active_categories()` function to `database.py`**

Add near the end of `database.py`:

```python
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
```

- [ ] **Step 5: Run test, verify pass**

Run: `pytest tests/test_lifelog_db.py -v`
Expected: PASS for both tests.

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_lifelog_db.py
git commit -m "feat(lifelog): add categories table with 16 seeded values"
```

### Task 1.2: Add `life_log_entries` table and CRUD

**Files:**
- Modify: `database.py`
- Modify: `tests/test_lifelog_db.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_lifelog_db.py`:

```python
def test_save_life_log_entry(temp_db_path):
    entry_id = db.save_life_log_entry(
        date_start="2026-05-02",
        date_end=None,
        categories=["Concert"],
        description="Dead & Co at The Sphere",
        location="Las Vegas, NV",
        notes=None,
        status="confirmed",
        source="manual",
        source_id=None,
    )
    assert entry_id > 0
    entry = db.get_life_log_entry(entry_id)
    assert entry["description"] == "Dead & Co at The Sphere"
    assert entry["categories"] == ["Concert"]
    assert entry["status"] == "confirmed"


def test_save_life_log_entry_multi_category(temp_db_path):
    entry_id = db.save_life_log_entry(
        date_start="2025-05-05",
        date_end="2025-05-19",
        categories=["Wedding", "Vacation"],
        description="Spinkel Wedding - London + Spain",
        location="London → Spain",
        notes=None,
        status="confirmed",
        source="manual",
        source_id=None,
    )
    entry = db.get_life_log_entry(entry_id)
    assert set(entry["categories"]) == {"Wedding", "Vacation"}


def test_get_life_log_entries_by_date_range(temp_db_path):
    db.save_life_log_entry(
        date_start="2025-01-15", date_end=None, categories=["Skiing"],
        description="A", location=None, notes=None,
        status="confirmed", source="manual", source_id=None,
    )
    db.save_life_log_entry(
        date_start="2025-06-15", date_end=None, categories=["Skiing"],
        description="B", location=None, notes=None,
        status="confirmed", source="manual", source_id=None,
    )
    db.save_life_log_entry(
        date_start="2024-12-01", date_end=None, categories=["Skiing"],
        description="C", location=None, notes=None,
        status="confirmed", source="manual", source_id=None,
    )
    entries = db.get_life_log_entries_in_range("2025-01-01", "2025-12-31")
    descriptions = [e["description"] for e in entries]
    assert descriptions == ["A", "B"]
```

- [ ] **Step 2: Run — should fail**

Run: `pytest tests/test_lifelog_db.py -v`
Expected: 3 new tests fail with AttributeError.

- [ ] **Step 3: Add table to both `_init_postgres` and `_init_sqlite`**

In `_init_postgres`:
```python
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
```

In `_init_sqlite`:
```python
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
```

- [ ] **Step 4: Add CRUD functions**

Append to `database.py` after the categories functions:

```python
# ── Life Log: Entries ─────────────────────────────────────────────────────────

def _serialize_categories(categories: list) -> str | list:
    """Postgres uses array; SQLite stores JSON."""
    return categories if USE_POSTGRES else json.dumps(categories)


def _deserialize_categories(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    return json.loads(raw)


def _unpack_life_log_entry(row: dict) -> dict:
    if row is None:
        return None
    row["categories"] = _deserialize_categories(row.get("categories"))
    return row


def save_life_log_entry(
    date_start: str, date_end: str | None, categories: list, description: str,
    location: str | None, notes: str | None, status: str, source: str,
    source_id: str | None,
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


def get_life_log_entry(entry_id: int) -> dict | None:
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
    """All confirmed entries across all time, ordered by date."""
    with _cursor() as c:
        c.execute(
            "SELECT * FROM life_log_entries WHERE status IN ('confirmed','upcoming') "
            "ORDER BY date_start, id"
        )
        return [_unpack_life_log_entry(r) for r in _rows(c.fetchall())]


def update_life_log_entry(
    entry_id: int, categories: list, description: str,
    location: str | None, notes: str | None,
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
```

- [ ] **Step 5: Run tests, verify pass**

Run: `pytest tests/test_lifelog_db.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_lifelog_db.py
git commit -m "feat(lifelog): add life_log_entries table and CRUD"
```

### Task 1.3: Add `people` table and `life_log_people` join

**Files:**
- Modify: `database.py`
- Modify: `tests/test_lifelog_db.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
def test_save_person(temp_db_path):
    person_id = db.save_person(
        name="Megan", aliases=[], relationship_type="dating_prospect",
        first_seen="2026-02-15", notes="Met at Goldens",
    )
    p = db.get_person(person_id)
    assert p["name"] == "Megan"
    assert p["relationship_type"] == "dating_prospect"
    assert p["status"] == "active"


def test_link_entry_to_person(temp_db_path):
    eid = db.save_life_log_entry(
        date_start="2026-02-15", date_end=None, categories=["Relationship"],
        description="Met Megan at Goldens", location="Golden, CO",
        notes=None, status="confirmed", source="manual", source_id=None,
    )
    pid = db.save_person(
        name="Megan", aliases=[], relationship_type="dating_prospect",
        first_seen="2026-02-15", notes=None,
    )
    db.link_entry_to_people(eid, [pid])
    people = db.get_people_for_entry(eid)
    assert [p["name"] for p in people] == ["Megan"]


def test_find_person_by_name_or_alias(temp_db_path):
    pid = db.save_person(
        name="Spinkel", aliases=["Sprink"], relationship_type="friend",
        first_seen="2024-01-01", notes=None,
    )
    assert db.find_person_by_name("Spinkel")["id"] == pid
    assert db.find_person_by_name("Sprink")["id"] == pid
    assert db.find_person_by_name("Sprink ")["id"] == pid
    assert db.find_person_by_name("Unknown") is None
```

- [ ] **Step 2: Run — should fail**

Expected: failures with AttributeError on missing functions.

- [ ] **Step 3: Add tables to schema**

In `_init_postgres`:
```python
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
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS life_log_people (
                entry_id INTEGER NOT NULL REFERENCES life_log_entries(id) ON DELETE CASCADE,
                person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                PRIMARY KEY (entry_id, person_id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_people_person_id ON life_log_people(person_id)")
```

In `_init_sqlite`:
```python
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
```

- [ ] **Step 4: Add people CRUD functions**

Append to `database.py`:

```python
# ── Life Log: People ──────────────────────────────────────────────────────────

def _unpack_person(row: dict) -> dict | None:
    if row is None:
        return None
    raw = row.get("aliases")
    if isinstance(raw, str):
        row["aliases"] = json.loads(raw)
    elif raw is None:
        row["aliases"] = []
    return row


def save_person(
    name: str, aliases: list, relationship_type: str | None,
    first_seen: str | None, notes: str | None,
) -> int:
    p = _p()
    aliases_val = aliases if USE_POSTGRES else json.dumps(aliases)
    with _cursor(write=True) as c:
        if USE_POSTGRES:
            c.execute(
                f"""INSERT INTO people (name, aliases, relationship_type, first_seen,
                    last_seen, notes, status)
                    VALUES ({p},{p},{p},{p},{p},{p},'active') RETURNING id""",
                (name, aliases_val, relationship_type, first_seen, first_seen, notes),
            )
            return c.fetchone()["id"]
        else:
            c.execute(
                """INSERT INTO people (name, aliases, relationship_type, first_seen,
                   last_seen, notes, status)
                   VALUES (?,?,?,?,?,?,'active')""",
                (name, aliases_val, relationship_type, first_seen, first_seen, notes),
            )
            return c.lastrowid


def get_person(person_id: int) -> dict | None:
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT * FROM people WHERE id={p}", (person_id,))
        return _unpack_person(_row(c.fetchone()))


def find_person_by_name(name: str) -> dict | None:
    """Match by name OR alias, case-insensitive, trimmed."""
    name = (name or "").strip()
    if not name:
        return None
    p = _p()
    with _cursor() as c:
        if USE_POSTGRES:
            c.execute(
                f"SELECT * FROM people WHERE LOWER(name)=LOWER({p}) OR {p} = ANY(aliases)",
                (name, name),
            )
            row = c.fetchone()
            if row:
                return _unpack_person(_row(row))
            # case-insensitive alias check (separate query for portability)
            c.execute("SELECT * FROM people WHERE EXISTS (SELECT 1 FROM unnest(aliases) a WHERE LOWER(a)=LOWER(%s))", (name,))
            return _unpack_person(_row(c.fetchone()))
        else:
            c.execute("SELECT * FROM people")
            for row in _rows(c.fetchall()):
                p_row = _unpack_person(row)
                if p_row["name"].lower() == name.lower():
                    return p_row
                if any(a.lower() == name.lower() for a in p_row["aliases"]):
                    return p_row
            return None


def get_all_people() -> list:
    with _cursor() as c:
        c.execute("SELECT * FROM people ORDER BY name")
        return [_unpack_person(r) for r in _rows(c.fetchall())]


def link_entry_to_people(entry_id: int, person_ids: list[int]):
    p = _p()
    with _cursor(write=True) as c:
        for pid in person_ids:
            if USE_POSTGRES:
                c.execute(
                    f"INSERT INTO life_log_people (entry_id, person_id) VALUES ({p},{p}) "
                    f"ON CONFLICT DO NOTHING",
                    (entry_id, pid),
                )
            else:
                c.execute(
                    "INSERT OR IGNORE INTO life_log_people (entry_id, person_id) VALUES (?,?)",
                    (entry_id, pid),
                )


def get_people_for_entry(entry_id: int) -> list:
    p = _p()
    with _cursor() as c:
        c.execute(
            f"SELECT p.* FROM people p "
            f"JOIN life_log_people lp ON lp.person_id = p.id "
            f"WHERE lp.entry_id = {p} ORDER BY p.name",
            (entry_id,),
        )
        return [_unpack_person(r) for r in _rows(c.fetchall())]


def get_entries_for_person(person_id: int) -> list:
    p = _p()
    with _cursor() as c:
        c.execute(
            f"SELECT e.* FROM life_log_entries e "
            f"JOIN life_log_people lp ON lp.entry_id = e.id "
            f"WHERE lp.person_id = {p} ORDER BY e.date_start",
            (person_id,),
        )
        return [_unpack_life_log_entry(r) for r in _rows(c.fetchall())]


def update_person_last_seen(person_id: int, last_seen: str):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE people SET last_seen={p} WHERE id={p} "
            f"AND (last_seen IS NULL OR last_seen < {p})",
            (last_seen, person_id, last_seen),
        )


def set_person_relationship_status(person_id: int, status: str, end_date: str | None = None):
    p = _p()
    with _cursor(write=True) as c:
        if end_date:
            c.execute(
                f"UPDATE people SET status={p}, end_date={p} WHERE id={p}",
                (status, end_date, person_id),
            )
        else:
            c.execute(
                f"UPDATE people SET status={p} WHERE id={p}",
                (status, person_id),
            )


def merge_people(keep_id: int, merge_id: int):
    """Move all entry links from merge_id to keep_id, then delete merge_id."""
    p = _p()
    with _cursor(write=True) as c:
        if USE_POSTGRES:
            c.execute(
                f"UPDATE life_log_people SET person_id={p} WHERE person_id={p} "
                f"AND entry_id NOT IN (SELECT entry_id FROM life_log_people WHERE person_id={p})",
                (keep_id, merge_id, keep_id),
            )
            c.execute(f"DELETE FROM life_log_people WHERE person_id={p}", (merge_id,))
            c.execute(f"DELETE FROM people WHERE id={p}", (merge_id,))
        else:
            c.execute(
                "UPDATE OR IGNORE life_log_people SET person_id=? WHERE person_id=?",
                (keep_id, merge_id),
            )
            c.execute("DELETE FROM life_log_people WHERE person_id=?", (merge_id,))
            c.execute("DELETE FROM people WHERE id=?", (merge_id,))
```

- [ ] **Step 5: Run tests, verify pass**

Run: `pytest tests/test_lifelog_db.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_lifelog_db.py
git commit -m "feat(lifelog): add people table, link table, and CRUD"
```

### Task 1.4: Add `activity_log` table and write-only API

**Files:**
- Modify: `database.py`
- Modify: `tests/test_lifelog_db.py`

- [ ] **Step 1: Write failing tests**

```python
def test_record_activity(temp_db_path):
    db.record_activity(
        source="calendar",
        source_id="event_abc",
        event_type="calendar_event",
        occurred_at="2026-05-02T18:00:00",
        payload={"title": "Dinner", "attendees": ["Megan"]},
    )
    rows = db.get_activity_by_source_id("calendar", "event_abc")
    assert rows[0]["payload"]["title"] == "Dinner"


def test_record_activity_dedup_by_source_id(temp_db_path):
    db.record_activity("calendar", "event_xyz", "calendar_event",
                       "2026-05-02T18:00:00", {"title": "A"})
    db.record_activity("calendar", "event_xyz", "calendar_event",
                       "2026-05-02T18:00:00", {"title": "A updated"})
    rows = db.get_activity_by_source_id("calendar", "event_xyz")
    assert len(rows) == 1
    assert rows[0]["payload"]["title"] == "A"  # first write wins; idempotent


def test_mark_activity_promoted(temp_db_path):
    db.record_activity("calendar", "ev1", "calendar_event",
                       "2026-05-02T00:00:00", {"x": 1})
    rows = db.get_activity_by_source_id("calendar", "ev1")
    db.mark_activity_promoted(rows[0]["id"])
    rows2 = db.get_activity_by_source_id("calendar", "ev1")
    assert rows2[0]["promoted_to_life_log"] in (True, 1)
```

- [ ] **Step 2: Run — should fail**

Expected: AttributeError on missing functions.

- [ ] **Step 3: Add table to schema**

In `_init_postgres`:
```python
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
```

In `_init_sqlite`:
```python
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
```

- [ ] **Step 4: Add functions**

Append to `database.py`:

```python
# ── Activity Log ──────────────────────────────────────────────────────────────

def _serialize_payload(payload: dict) -> str | dict:
    return payload if USE_POSTGRES else json.dumps(payload)


def _deserialize_payload(raw):
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def record_activity(
    source: str, source_id: str, event_type: str,
    occurred_at: str | None, payload: dict,
):
    """Idempotent insert — first write wins per (source, source_id)."""
    p = _p()
    payload_val = _serialize_payload(payload)
    with _cursor(write=True) as c:
        if USE_POSTGRES:
            c.execute(
                f"""INSERT INTO activity_log
                    (source, source_id, event_type, occurred_at, payload)
                    VALUES ({p},{p},{p},{p},{p}) ON CONFLICT DO NOTHING""",
                (source, source_id, event_type, occurred_at, payload_val),
            )
        else:
            c.execute(
                """INSERT OR IGNORE INTO activity_log
                   (source, source_id, event_type, occurred_at, payload)
                   VALUES (?,?,?,?,?)""",
                (source, source_id, event_type, occurred_at, payload_val),
            )


def get_activity_by_source_id(source: str, source_id: str) -> list:
    p = _p()
    with _cursor() as c:
        c.execute(
            f"SELECT * FROM activity_log WHERE source={p} AND source_id={p}",
            (source, source_id),
        )
        rows = _rows(c.fetchall())
        for r in rows:
            r["payload"] = _deserialize_payload(r.get("payload"))
        return rows


def mark_activity_promoted(activity_id: int):
    p = _p()
    val = "TRUE" if USE_POSTGRES else "1"
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE activity_log SET promoted_to_life_log={val} WHERE id={p}",
            (activity_id,),
        )
```

- [ ] **Step 5: Run tests, verify pass**

Run: `pytest tests/test_lifelog_db.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_lifelog_db.py
git commit -m "feat(lifelog): add activity_log table for raw source mirror"
```

### Task 1.5: Add proposal-queue helpers (entries with status='proposed')

**Files:**
- Modify: `database.py`
- Modify: `tests/test_lifelog_db.py`

Proposed entries are saved as `life_log_entries` rows with `status='proposed'` and `ai_proposed_at` set. They become `confirmed` when user accepts. Rather than a separate table, we just query by status.

- [ ] **Step 1: Write failing tests**

```python
def test_save_proposal_and_confirm(temp_db_path):
    pid = db.save_proposal(
        date_start="2026-05-15", date_end="2026-05-22",
        categories=["Vacation"], description="Trip to Vegas",
        location="Las Vegas, NV", source="calendar", source_id="ev_vegas",
    )
    pending = db.get_pending_proposals()
    assert len(pending) == 1
    assert pending[0]["id"] == pid

    db.confirm_proposal(pid)
    assert db.get_life_log_entry(pid)["status"] == "confirmed"
    assert db.get_pending_proposals() == []


def test_dismiss_proposal(temp_db_path):
    pid = db.save_proposal(
        date_start="2026-05-15", date_end=None, categories=["Concert"],
        description="X", location=None, source="calendar", source_id="ev_x",
    )
    db.dismiss_proposal(pid)
    assert db.get_life_log_entry(pid)["status"] == "dismissed"
    assert db.get_pending_proposals() == []
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Add functions**

Append:
```python
def save_proposal(
    date_start: str, date_end: str | None, categories: list, description: str,
    location: str | None, source: str, source_id: str | None,
) -> int:
    """Save a Life Log entry proposal with status='proposed' and ai_proposed_at set."""
    p = _p()
    cats = _serialize_categories(categories)
    with _cursor(write=True) as c:
        if USE_POSTGRES:
            c.execute(
                f"""INSERT INTO life_log_entries
                    (date_start, date_end, categories, description, location, status,
                     source, source_id, ai_proposed_at)
                    VALUES ({p},{p},{p},{p},{p}, 'proposed', {p},{p}, CURRENT_TIMESTAMP)
                    RETURNING id""",
                (date_start, date_end, cats, description, location, source, source_id),
            )
            return c.fetchone()["id"]
        else:
            c.execute(
                """INSERT INTO life_log_entries
                   (date_start, date_end, categories, description, location, status,
                    source, source_id, ai_proposed_at)
                   VALUES (?,?,?,?,?, 'proposed', ?,?, CURRENT_TIMESTAMP)""",
                (date_start, date_end, cats, description, location, source, source_id),
            )
            return c.lastrowid


def get_pending_proposals() -> list:
    with _cursor() as c:
        c.execute(
            "SELECT * FROM life_log_entries WHERE status='proposed' "
            "ORDER BY ai_proposed_at"
        )
        return [_unpack_life_log_entry(r) for r in _rows(c.fetchall())]


def confirm_proposal(entry_id: int):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE life_log_entries SET status='confirmed', "
            f"user_confirmed_at=CURRENT_TIMESTAMP WHERE id={p}",
            (entry_id,),
        )


def dismiss_proposal(entry_id: int):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE life_log_entries SET status='dismissed' WHERE id={p}",
            (entry_id,),
        )
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_lifelog_db.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_lifelog_db.py
git commit -m "feat(lifelog): add proposal queue helpers (save/confirm/dismiss)"
```

---

## M2 — AI Module (`ai_life_log.py`)

### Task 2.1: Create `ai_life_log.py` skeleton with shared `_call`

**Files:**
- Create: `ai_life_log.py`
- Create: `tests/test_ai_life_log.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for ai_life_log."""
import json
from unittest.mock import MagicMock


def test_call_strips_markdown_fences(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text='```json\n{"foo": 1}\n```')
    ]
    import ai_life_log
    result = ai_life_log._call_json("test prompt")
    assert result == {"foo": 1}


def test_call_handles_extra_whitespace(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text='   \n{"foo": 2}\n  ')
    ]
    import ai_life_log
    result = ai_life_log._call_json("test")
    assert result == {"foo": 2}


def test_call_returns_default_on_parse_failure(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text='not json at all')
    ]
    import ai_life_log
    result = ai_life_log._call_json("test", default={"fallback": True})
    assert result == {"fallback": True}
```

- [ ] **Step 2: Run — should fail**

Run: `pytest tests/test_ai_life_log.py -v`
Expected: ImportError or AttributeError.

- [ ] **Step 3: Create `ai_life_log.py`**

```python
"""All Claude calls for the Life Log feature.

Keeping these separate from ai_summarize.py preserves the existing
weekly-accomplishments AI logic untouched while we build the Life Log.
ai_summarize.py becomes deprecated once the cutover is complete.
"""
import json
import logging
import re

import anthropic

from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _call_raw(prompt: str, max_tokens: int = 800) -> str:
    msg = _get_client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _strip_fences(s: str) -> str:
    """Remove markdown code fences if present."""
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _call_json(prompt: str, max_tokens: int = 800, default=None):
    """Call Claude, expect JSON, return parsed dict/list. Returns `default` on parse failure."""
    raw = ""
    try:
        raw = _call_raw(prompt, max_tokens=max_tokens)
        return json.loads(_strip_fences(raw))
    except Exception as e:
        logger.error("ai_life_log JSON parse failed: %s | raw=%r", e, raw)
        return default if default is not None else {}
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_ai_life_log.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai_life_log.py tests/test_ai_life_log.py
git commit -m "feat(lifelog): add ai_life_log module with JSON-call helper"
```

### Task 2.2: Add `propose_from_calendar_event` — classify a single calendar event

**Files:**
- Modify: `ai_life_log.py`
- Modify: `tests/test_ai_life_log.py`

- [ ] **Step 1: Write failing tests**

```python
def test_propose_high_confidence_wedding(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "high",
        "categories": ["Wedding", "Vacation"],
        "description": "Spinkel Wedding",
        "location": "London, UK",
        "people": ["Sprink", "Emily"],
        "reason": "Multi-day named wedding event"
    }))]
    import ai_life_log
    result = ai_life_log.propose_from_calendar_event(
        title="Spinkel Wedding",
        start="2025-05-05",
        end="2025-05-12",
        attendees=["Spinkel", "Emily"],
        description="",
        location="London",
        active_categories=["Wedding", "Vacation", "Skiing"],
    )
    assert result["confidence"] == "high"
    assert "Wedding" in result["categories"]
    assert "Vacation" in result["categories"]


def test_propose_returns_skip_for_noise(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "skip",
        "categories": [],
        "description": "",
        "location": "",
        "people": [],
        "reason": "Standup meeting — not Life Log worthy"
    }))]
    import ai_life_log
    result = ai_life_log.propose_from_calendar_event(
        title="Daily standup", start="2026-05-02T09:00:00",
        end="2026-05-02T09:15:00", attendees=[],
        description="", location="", active_categories=["Vacation"],
    )
    assert result["confidence"] == "skip"
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Add function to `ai_life_log.py`**

```python
def propose_from_calendar_event(
    title: str,
    start: str,
    end: str | None,
    attendees: list[str],
    description: str,
    location: str,
    active_categories: list[str],
) -> dict:
    """
    Classify a calendar event for Life Log promotion.

    Returns:
        {
          "confidence": "high" | "matched" | "maybe" | "skip",
          "categories": [str],     # subset of active_categories, [] if skip
          "description": str,      # cleaned event description for the entry
          "location": str,         # extracted location
          "people": [str],         # extracted person names
          "reason": str            # short human-readable reason
        }
    """
    cats_str = ", ".join(active_categories)
    attendees_str = ", ".join(attendees) if attendees else "none"

    prompt = f"""You are filtering calendar events for a personal Life Log — a 30-year memoir
of memorable moments. Most calendar events (meetings, dentist, standups) are NOT memoir-worthy.
Only events that someone might want to remember in 30 years should be promoted.

Categories available: {cats_str}

Confidence levels:
- "high": multi-day trips, named events matching strong category keywords (wedding, concert,
  bachelor party, vacation), out-of-town travel — propose immediately
- "matched": single events that clearly fit a category but lower stakes (e.g. "Megan dinner"
  → Relationship; "Ski Killington Saturday" → Skiing) — propose day-after
- "maybe": might be memorable but unsure — batch into Sunday digest
- "skip": work meetings, recurring routines, doctor appointments, anything not memoir-worthy

Event:
- Title: {title}
- Start: {start}
- End: {end or "(none)"}
- Attendees: {attendees_str}
- Location: {location or "(none)"}
- Description: {description or "(none)"}

Return ONLY a JSON object — no markdown fences, no explanation:
{{
  "confidence": "high" | "matched" | "maybe" | "skip",
  "categories": ["one or more from the active list"],
  "description": "concise one-line memoir-style description",
  "location": "extracted location or empty string",
  "people": ["names of people involved beyond just attendees, if mentioned"],
  "reason": "one short sentence justifying the confidence"
}}

Rules:
- If confidence is "skip", categories MUST be [].
- description should read like a memoir entry, not the raw calendar title.
  Example: "Trip to Vermont with Mom and Dad" not "Vermont Trip".
- people: extract names from title/description/attendees. Strip emails — just first names
  unless the title uses last names.
"""

    return _call_json(prompt, max_tokens=500, default={
        "confidence": "skip", "categories": [], "description": "",
        "location": "", "people": [], "reason": "AI parse failed",
    })
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_ai_life_log.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add ai_life_log.py tests/test_ai_life_log.py
git commit -m "feat(lifelog): add propose_from_calendar_event AI classifier"
```

### Task 2.3: Add `parse_log_command` — turn freeform text into a Life Log entry

**Files:**
- Modify: `ai_life_log.py`
- Modify: `tests/test_ai_life_log.py`

- [ ] **Step 1: Write failing tests**

```python
def test_parse_log_command_extracts_entry(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "categories": ["Relationship"],
        "description": "Met Megan at Goldens",
        "location": "Golden, CO",
        "date_start": "2026-05-02",
        "date_end": None,
        "people": ["Megan"],
        "questions": []
    }))]
    import ai_life_log
    result = ai_life_log.parse_log_command(
        "Met Megan at Goldens in Golden tonight",
        today="2026-05-02",
        active_categories=["Relationship", "Vacation"],
    )
    assert result["categories"] == ["Relationship"]
    assert result["people"] == ["Megan"]
    assert result["date_start"] == "2026-05-02"


def test_parse_log_command_with_correction(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "categories": ["Skiing"],
        "description": "Skied at Killington",
        "location": "Killington, VT",
        "date_start": "2026-05-01",
        "date_end": None,
        "people": [],
        "questions": []
    }))]
    import ai_life_log
    result = ai_life_log.parse_log_command(
        "Skied at Killington yesterday",
        today="2026-05-02",
        active_categories=["Skiing"],
        correction="Actually it was Vermont not Colorado",
    )
    assert result["location"] == "Killington, VT"
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Add function**

```python
def parse_log_command(
    text: str,
    today: str,
    active_categories: list[str],
    correction: str | None = None,
) -> dict:
    """
    Parse a /log command into a structured Life Log entry.

    Returns:
        {
          "categories": [str],
          "description": str,
          "location": str | None,
          "date_start": str (YYYY-MM-DD),
          "date_end": str | None (YYYY-MM-DD),
          "people": [str],
          "questions": [str]   # ambiguities to surface to user
        }
    """
    cats_str = ", ".join(active_categories)
    correction_block = (
        f"\n\nThe user provided a correction to your previous interpretation:\n"
        f'"{correction}"\nRevise accordingly.'
        if correction else ""
    )

    prompt = f"""Today is {today}. The user typed a /log command for their personal Life Log
(a 30-year memoir of memorable life events).

Parse it into ONE Life Log entry. Extract people, location, date(s), and pick 1-3 categories.

Available categories: {cats_str}

Return ONLY a JSON object — no markdown fences:
{{
  "categories": ["one or more from the list above"],
  "description": "short memoir-style description (5-15 words)",
  "location": "place or null",
  "date_start": "YYYY-MM-DD",
  "date_end": "YYYY-MM-DD or null (only for multi-day events)",
  "people": ["first names mentioned"],
  "questions": ["only if you genuinely cannot determine something important"]
}}

Rules:
- "tonight" / "today" → date_start = {today}
- "yesterday" → day before {today}
- Multi-day phrasing ("over the weekend", "for a week") → use date_end
- People: strip honorifics, use first names unless full name is given
- description: memoir voice, not action-log voice. "Trip to Vegas with Sprink" beats "Vegas trip"
- If you cannot determine the category confidently, leave categories empty and add a question
- Don't invent details not in the text

Message: "{text}"{correction_block}
"""

    return _call_json(prompt, max_tokens=500, default={
        "categories": [], "description": text[:100], "location": None,
        "date_start": today, "date_end": None, "people": [], "questions": [],
    })
```

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```bash
git add ai_life_log.py tests/test_ai_life_log.py
git commit -m "feat(lifelog): add parse_log_command for /log AI extraction"
```

### Task 2.4: Add `recommend_category_changes` — monthly review

**Files:**
- Modify: `ai_life_log.py`
- Modify: `tests/test_ai_life_log.py`

- [ ] **Step 1: Write failing test**

```python
def test_recommend_category_changes(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "recommendations": [
            {"action": "drop", "name": "Pet", "reason": "0 entries in 6 months"},
            {"action": "merge", "from": "Outdoors", "into": "Hiking", "reason": "Hiking now dominates"}
        ]
    }))]
    import ai_life_log
    result = ai_life_log.recommend_category_changes(
        category_usage=[
            {"name": "Vacation", "usage_count": 12},
            {"name": "Pet", "usage_count": 0},
            {"name": "Outdoors", "usage_count": 3},
        ],
        recent_descriptions=["Hiked Mt Quandary", "Hiked Bierstadt"],
    )
    assert any(r["action"] == "drop" for r in result["recommendations"])
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Add function**

```python
def recommend_category_changes(
    category_usage: list[dict],
    recent_descriptions: list[str],
) -> dict:
    """
    Periodic review: suggest merges, drops, or new categories based on usage patterns.

    Returns:
        {"recommendations": [
            {"action": "drop"|"merge"|"add", ... }
        ]}
    """
    usage_str = "\n".join(f"- {c['name']}: {c['usage_count']} entries" for c in category_usage)
    desc_str = "\n".join(f"- {d}" for d in recent_descriptions[:50])

    prompt = f"""Review the user's Life Log category usage and recent entries.
Recommend changes to the category list — drops, merges, or new additions.

Current categories with usage counts:
{usage_str}

Recent entry descriptions (sample):
{desc_str}

Return ONLY a JSON object:
{{
  "recommendations": [
    {{"action": "drop", "name": "X", "reason": "why"}},
    {{"action": "merge", "from": "X", "into": "Y", "reason": "why"}},
    {{"action": "add", "name": "X", "reason": "why"}}
  ]
}}

Rules:
- Only recommend dropping if usage is 0 over a long period
- Only recommend merging if there is clear conceptual overlap
- Only recommend adding if 5+ entries in recent descriptions don't fit existing categories
- It's fine to return an empty list if no changes are warranted
"""

    return _call_json(prompt, max_tokens=600, default={"recommendations": []})
```

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```bash
git add ai_life_log.py tests/test_ai_life_log.py
git commit -m "feat(lifelog): add recommend_category_changes for periodic review"
```

### Task 2.5: Add `extract_entry_from_existing_text` for spreadsheet backfill

**Files:**
- Modify: `ai_life_log.py`
- Modify: `tests/test_ai_life_log.py`

- [ ] **Step 1: Write failing test**

```python
def test_extract_from_spreadsheet_row(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "categories": ["Wedding", "Vacation"],
        "description": "Spinkel Wedding - London 1 week, Spain 2 weeks",
        "location": "London → Spain",
        "people": ["Spinkel", "Emily"],
    }))]
    import ai_life_log
    result = ai_life_log.extract_entry_from_existing_text(
        original_category="Wedding + Vacation",
        original_description="Spinkel Wedding - London 1 week, Spain 2 weeks (Barcelona, Malaga, Majorca)",
        active_categories=["Wedding", "Vacation"],
    )
    assert "Wedding" in result["categories"]
    assert "Vacation" in result["categories"]
    assert "Spinkel" in result["people"]
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Add function**

```python
def extract_entry_from_existing_text(
    original_category: str,
    original_description: str,
    active_categories: list[str],
) -> dict:
    """
    Used during one-time spreadsheet backfill. The user's existing sheet has
    free-text categories like "Wedding + Vacation" and descriptions packed with
    people/places. Extract structured data.

    Returns:
        {
          "categories": [str],
          "description": str,    # cleaned-up description
          "location": str | None,
          "people": [str],
        }
    """
    cats_str = ", ".join(active_categories)

    prompt = f"""Extract structured Life Log data from a spreadsheet row.

Original category text: "{original_category}"
Description: "{original_description}"

Available structured categories: {cats_str}

Return ONLY a JSON object:
{{
  "categories": ["matching categories from the available list — pick 1-3"],
  "description": "cleaned description (keep the user's voice, just fix obvious issues)",
  "location": "extracted location or null",
  "people": ["names mentioned in the description"]
}}

Rules:
- Map original category text to available categories. "Wedding + Vacation" → both.
  "Outdoors" stays as Outdoors. Unknown → closest match or "Life Event" as fallback.
- Don't editorialize the description — preserve the user's words.
- People: extract names. "Mom and Dad" → ["Mom", "Dad"]. "with Sprink/Emily" → ["Sprink", "Emily"].
"""

    return _call_json(prompt, max_tokens=400, default={
        "categories": [], "description": original_description,
        "location": None, "people": [],
    })
```

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```bash
git add ai_life_log.py tests/test_ai_life_log.py
git commit -m "feat(lifelog): add extract_entry_from_existing_text for backfill"
```

---

## M3 — `/log` Command + People Entity Flow

### Task 3.1: Create `handlers/__init__.py` and stub `log_command.py`

**Files:**
- Create: `handlers/__init__.py` (empty)
- Create: `handlers/log_command.py`

- [ ] **Step 1: Create empty package init**

```bash
mkdir -p handlers
touch handlers/__init__.py
```

- [ ] **Step 2: Create stub `log_command.py`**

```python
"""Handler for the /log command — manual Life Log entry capture."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /log [text]."""
    raise NotImplementedError("Implemented in Task 3.2")
```

- [ ] **Step 3: Smoke check imports**

Run: `python -c "from handlers.log_command import log_command; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add handlers/__init__.py handlers/log_command.py
git commit -m "scaffold: handlers package with log_command stub"
```

### Task 3.2: Implement `/log` text-only flow with confirm/skip

**Files:**
- Modify: `handlers/log_command.py`
- Create: `tests/test_log_command.py`

This task uses the existing conversation_state machinery for the confirm step. We add new states: `lifelog_confirming` (waiting for confirm/edit/skip).

- [ ] **Step 1: Write failing tests**

```python
"""Tests for /log command handler."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_log_command_no_args_prompts_user(temp_db_path, mock_anthropic, mock_bot):
    from handlers.log_command import log_command
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = []
    await log_command(update, context)
    update.message.reply_text.assert_called_once()
    args, _ = update.message.reply_text.call_args
    assert "What happened" in args[0] or "Tell me" in args[0]


@pytest.mark.asyncio
async def test_log_command_with_text_calls_ai_and_shows_preview(temp_db_path, mock_anthropic, mock_bot):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "categories": ["Relationship"],
        "description": "Met Megan at Goldens",
        "location": "Golden, CO",
        "date_start": "2026-05-02",
        "date_end": None,
        "people": ["Megan"],
        "questions": [],
    }))]
    from handlers.log_command import log_command
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = ["Met", "Megan", "at", "Goldens"]

    import database as db
    await log_command(update, context)

    state = db.get_state()
    assert state["state"] == "lifelog_confirming"
    temp = json.loads(state["temp_data"])
    assert temp["parsed"]["description"] == "Met Megan at Goldens"
    update.message.reply_text.assert_called()
```

- [ ] **Step 2: Run — should fail**

Run: `pytest tests/test_log_command.py -v`
Expected: NotImplementedError or assertion failures.

- [ ] **Step 3: Implement `log_command`**

Replace `handlers/log_command.py`:

```python
"""Handler for the /log command — manual Life Log entry capture."""
import datetime
import json
import logging

import pytz
from telegram import Update
from telegram.ext import ContextTypes

import database as db
from ai_life_log import parse_log_command
from config import TIMEZONE

logger = logging.getLogger(__name__)


def _today_str() -> str:
    return datetime.datetime.now(pytz.timezone(TIMEZONE)).date().isoformat()


def _format_preview(parsed: dict) -> str:
    cats = ", ".join(parsed.get("categories", [])) or "(none)"
    location = parsed.get("location") or "(none)"
    people = ", ".join(parsed.get("people", [])) or "(none)"
    date_start = parsed.get("date_start", "")
    date_end = parsed.get("date_end")
    date_label = f"{date_start} → {date_end}" if date_end else date_start

    lines = [
        "Here's what I understood:",
        "",
        f"📝 *{parsed.get('description', '')}*",
        f"📅 {date_label}",
        f"🏷  {cats}",
        f"👥 {people}",
        f"📍 {location}",
    ]

    questions = parsed.get("questions", [])
    if questions:
        lines.append("")
        lines.append("❓ I wasn't sure about:")
        for q in questions:
            lines.append(f"• {q}")

    lines.append("")
    lines.append("Reply *Yes* to save, *No* to cancel, or send a correction.")
    return "\n".join(lines)


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /log [text]."""
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "📝 *What happened?*\n\n"
            "Tell me what to log and I'll figure out the category, people, and date.\n\n"
            "Examples:\n"
            "• `/log Met Megan at Goldens in Golden`\n"
            "• `/log Skied Killington with Justin yesterday`\n"
            "• `/log Spinkel Wedding next week in London`",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text("Reading your message… 🤔")

    active_cats = [c["name"] for c in db.get_active_categories()]
    parsed = parse_log_command(text, today=_today_str(), active_categories=active_cats)

    db.set_state(
        "lifelog_confirming",
        temp_data={"original_text": text, "parsed": parsed},
    )

    await update.message.reply_text(_format_preview(parsed), parse_mode="Markdown")
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_log_command.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add handlers/log_command.py tests/test_log_command.py
git commit -m "feat(lifelog): implement /log command with AI parse and preview"
```

### Task 3.3: Handle confirm/cancel/correction in `lifelog_confirming` state

**Files:**
- Modify: `handlers/log_command.py`
- Modify: `tests/test_log_command.py`

This handler will be wired into bot.py's main `handle_message` dispatcher in M10. For now, expose a `handle_confirm_response` function that the dispatcher will call.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_confirm_yes_saves_entry_and_links_people(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "categories": ["Relationship"],
        "description": "Met Megan at Goldens",
        "location": "Golden, CO",
        "date_start": "2026-05-02",
        "date_end": None,
        "people": ["Megan"],
        "questions": [],
    }))]

    import database as db
    from handlers.log_command import handle_confirm_response

    db.set_state(
        "lifelog_confirming",
        temp_data={
            "original_text": "Met Megan at Goldens",
            "parsed": {
                "categories": ["Relationship"],
                "description": "Met Megan at Goldens",
                "location": "Golden, CO",
                "date_start": "2026-05-02",
                "date_end": None,
                "people": ["Megan"],
                "questions": [],
            },
        },
    )

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot = MagicMock()

    handled = await handle_confirm_response(update, context, "yes")
    assert handled is True

    entries = db.get_all_life_log_entries()
    assert len(entries) == 1
    assert entries[0]["description"] == "Met Megan at Goldens"

    people = db.get_all_people()
    assert any(p["name"] == "Megan" for p in people)

    state = db.get_state()
    # State should advance to person-onboarding (new person), not idle
    assert state["state"] in ("lifelog_new_person", "idle")


@pytest.mark.asyncio
async def test_confirm_no_cancels(temp_db_path, mock_anthropic):
    import database as db
    from handlers.log_command import handle_confirm_response

    db.set_state(
        "lifelog_confirming",
        temp_data={"original_text": "x", "parsed": {
            "categories": [], "description": "x", "location": None,
            "date_start": "2026-05-02", "date_end": None, "people": [], "questions": [],
        }},
    )

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    handled = await handle_confirm_response(update, context, "no")
    assert handled is True
    assert db.get_state()["state"] == "idle"
    assert db.get_all_life_log_entries() == []
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Implement `handle_confirm_response`**

Append to `handlers/log_command.py`:

```python
_YES = {"yes", "y", "yep", "yeah", "save", "ok", "looks good"}
_NO = {"no", "n", "cancel", "stop", "nevermind"}


def _link_or_create_people(entry_id: int, names: list[str], date_start: str) -> list[dict]:
    """Find or create people, link to entry, return new (just-created) people only."""
    new_people = []
    for raw_name in names:
        name = raw_name.strip()
        if not name:
            continue
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
            new_people.append(db.get_person(pid))
    return new_people


async def handle_confirm_response(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """
    Called by the main message dispatcher when state is lifelog_confirming.
    Returns True if the message was handled.
    """
    state_data = db.get_state()
    temp = json.loads(state_data.get("temp_data") or "{}")
    parsed = temp.get("parsed", {})
    text_lc = text.strip().lower()

    if text_lc in _YES:
        if not parsed.get("categories"):
            await update.message.reply_text(
                "Can't save — no category was determined. Try `/log` again with more detail.",
            )
            db.set_state("idle")
            return True

        entry_id = db.save_life_log_entry(
            date_start=parsed["date_start"],
            date_end=parsed.get("date_end"),
            categories=parsed["categories"],
            description=parsed["description"],
            location=parsed.get("location"),
            notes=None,
            status="confirmed",
            source="manual",
            source_id=None,
        )
        for cat in parsed["categories"]:
            db.increment_category_usage(cat)

        new_people = _link_or_create_people(
            entry_id, parsed.get("people", []), parsed["date_start"]
        )

        await update.message.reply_text(
            f"✅ Saved!\n\n📝 *{parsed['description']}*",
            parse_mode="Markdown",
        )

        # If new people were created, kick off onboarding for the first one
        if new_people:
            first = new_people[0]
            remaining_ids = [p["id"] for p in new_people[1:]]
            db.set_state(
                "lifelog_new_person",
                temp_data={"current_person_id": first["id"], "pending_person_ids": remaining_ids},
            )
            await _ask_relationship_type(update, first)
        else:
            db.set_state("idle")

        return True

    if text_lc in _NO:
        db.set_state("idle")
        await update.message.reply_text("Cancelled — nothing was saved.")
        return True

    # Treat as a correction — re-parse with feedback
    await update.message.reply_text("Got it — re-reading with your correction… 🤔")
    active_cats = [c["name"] for c in db.get_active_categories()]
    new_parsed = parse_log_command(
        temp.get("original_text", ""),
        today=_today_str(),
        active_categories=active_cats,
        correction=text,
    )
    db.set_state(
        "lifelog_confirming",
        temp_data={"original_text": temp.get("original_text", ""), "parsed": new_parsed},
    )
    await update.message.reply_text(_format_preview(new_parsed), parse_mode="Markdown")
    return True


async def _ask_relationship_type(update: Update, person: dict):
    """Onboard a newly-created person — ask relationship type."""
    await update.message.reply_text(
        f"👤 First time logging *{person['name']}*. What's the relationship?\n\n"
        "Reply with one:\n"
        "• `family`\n"
        "• `friend`\n"
        "• `dating prospect`\n"
        "• `dating`\n"
        "• `colleague`\n"
        "• `acquaintance`\n"
        "• `other`\n\n"
        "_(Type /skip to leave blank for now)_",
        parse_mode="Markdown",
    )
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_log_command.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add handlers/log_command.py tests/test_log_command.py
git commit -m "feat(lifelog): handle confirm/cancel/correction for /log preview"
```

### Task 3.4: Handle `lifelog_new_person` onboarding

**Files:**
- Modify: `handlers/log_command.py`
- Modify: `tests/test_log_command.py`

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_new_person_onboarding_sets_relationship_type(temp_db_path):
    import database as db
    from handlers.log_command import handle_new_person_response

    pid = db.save_person(
        name="Megan", aliases=[], relationship_type=None,
        first_seen="2026-05-02", notes=None,
    )
    db.set_state(
        "lifelog_new_person",
        temp_data={"current_person_id": pid, "pending_person_ids": []},
    )

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    handled = await handle_new_person_response(update, context, "dating prospect")
    assert handled is True

    p = db.get_person(pid)
    assert p["relationship_type"] == "dating_prospect"
    assert db.get_state()["state"] == "idle"
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Implement handler**

Append to `handlers/log_command.py`:

```python
_RELATIONSHIP_TYPES = {
    "family": "family",
    "friend": "friend",
    "dating prospect": "dating_prospect",
    "dating": "dating",
    "colleague": "colleague",
    "acquaintance": "acquaintance",
    "other": "other",
}


async def handle_new_person_response(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Handle the relationship-type reply during person onboarding."""
    state_data = db.get_state()
    temp = json.loads(state_data.get("temp_data") or "{}")
    person_id = temp.get("current_person_id")
    pending_ids = temp.get("pending_person_ids", [])

    text_lc = text.strip().lower()
    if text_lc == "/skip":
        rel_type = None
    elif text_lc in _RELATIONSHIP_TYPES:
        rel_type = _RELATIONSHIP_TYPES[text_lc]
    else:
        await update.message.reply_text(
            "Pick one: family, friend, dating prospect, dating, colleague, acquaintance, other "
            "(or /skip)."
        )
        return True

    if rel_type:
        db.set_person_relationship_status(person_id, "active")
        # Set relationship_type via people update — need a small DB function
        from database import _cursor, _p
        with _cursor(write=True) as c:
            c.execute(f"UPDATE people SET relationship_type={_p()} WHERE id={_p()}",
                      (rel_type, person_id))

    person = db.get_person(person_id)
    label = rel_type or "(no type)"
    await update.message.reply_text(f"✅ {person['name']} → {label}")

    if pending_ids:
        next_id, *rest = pending_ids
        next_person = db.get_person(next_id)
        db.set_state(
            "lifelog_new_person",
            temp_data={"current_person_id": next_id, "pending_person_ids": rest},
        )
        await _ask_relationship_type(update, next_person)
    else:
        db.set_state("idle")
        await update.message.reply_text("All done! 🎉")

    return True
```

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add handlers/log_command.py tests/test_log_command.py
git commit -m "feat(lifelog): handle new-person onboarding flow"
```

### Task 3.5: Wire `/log` and confirm-state into `bot.py`

**Files:**
- Modify: `bot.py`
- Create: `tests/test_bot_wiring.py`

This task adds the new handlers and dispatcher entries WITHOUT removing old ones (M10 handles removal).

- [ ] **Step 1: Write failing test**

```python
"""Verify bot.py registers new Life Log handlers."""

def test_create_application_registers_log_command(temp_db_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")
    import importlib
    import config
    importlib.reload(config)
    import bot
    importlib.reload(bot)

    app = bot.create_application()
    cmds = []
    for handler_group in app.handlers.values():
        for h in handler_group:
            for cmd in getattr(h, "commands", []) or []:
                cmds.append(cmd)
    assert "log" in cmds
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Wire `/log` into `bot.py`**

In `bot.py`, near the top imports:
```python
from handlers.log_command import (
    log_command as lifelog_log_command,
    handle_confirm_response as lifelog_handle_confirm,
    handle_new_person_response as lifelog_handle_new_person,
)
```

In `create_application()`, add the command handler:
```python
    app.add_handler(CommandHandler("log", lifelog_log_command))
```

In `handle_message()`, BEFORE the existing state checks, add:
```python
    if state == "lifelog_confirming":
        if await lifelog_handle_confirm(update, context, text):
            return
    if state == "lifelog_new_person":
        if await lifelog_handle_new_person(update, context, text):
            return
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_bot_wiring.py tests/test_log_command.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot_wiring.py
git commit -m "feat(lifelog): wire /log command and state handlers into bot.py"
```

### Task 3.6: Update `_COMMANDS_TEXT` in bot.py to include `/log`

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Update `_COMMANDS_TEXT` constant in `bot.py`**

Add a new section. Replace the existing `_COMMANDS_TEXT` block with one that adds:

```
*🧠 Life Log*
• /log \\[text\\] — Log a memorable moment (AI extracts category, people, date)
• /people — List people in your Life Log
```

(Insert it as a new section before the *⚙️ Admin* section.)

- [ ] **Step 2: Manually verify the message renders**

Run: `python -c "from bot import _COMMANDS_TEXT; print(_COMMANDS_TEXT)"`
Expected: full command listing including the new Life Log section.

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "docs(lifelog): add /log to bot command listing"
```

---

## M4 — Calendar Passive Ingestion

### Task 4.1: New job — `jobs/lifelog_realtime.py` (high-confidence proposals)

**Files:**
- Create: `jobs/lifelog_realtime.py`
- Create: `tests/test_lifelog_realtime.py`

The realtime job runs every ~15 minutes. It fetches recently-added calendar events, runs the AI classifier, and sends Telegram proposals for `confidence == "high"` events.

- [ ] **Step 1: Write failing test**

```python
"""Tests for jobs.lifelog_realtime."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_realtime_proposes_high_confidence(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "high",
        "categories": ["Wedding", "Vacation"],
        "description": "Spinkel Wedding",
        "location": "London",
        "people": ["Sprink", "Emily"],
        "reason": "Multi-day named wedding"
    }))]

    fake_events = [{
        "event_id": "ev1",
        "title": "Spinkel Wedding",
        "start_datetime": "2025-05-05",
        "end_datetime": "2025-05-12",
        "description": "",
        "location": "London",
        "is_recurring": False,
        "attendees": ["Sprink", "Emily"],
    }]

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("jobs.lifelog_realtime._fetch_recent_calendar_events", return_value=fake_events):
        from jobs.lifelog_realtime import run_realtime_proposals
        await run_realtime_proposals(bot)

    import database as db
    proposals = db.get_pending_proposals()
    assert len(proposals) == 1
    assert proposals[0]["categories"] == ["Wedding", "Vacation"]

    bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_realtime_skips_already_seen_events(temp_db_path, mock_anthropic):
    import database as db
    db.record_activity("calendar", "ev_seen", "calendar_event",
                       "2026-05-02T00:00:00", {"title": "Already seen"})

    bot = MagicMock()
    bot.send_message = AsyncMock()

    fake_events = [{
        "event_id": "ev_seen",
        "title": "X",
        "start_datetime": "2026-05-02",
        "end_datetime": "",
        "description": "",
        "location": "",
        "is_recurring": False,
        "attendees": [],
    }]
    with patch("jobs.lifelog_realtime._fetch_recent_calendar_events", return_value=fake_events):
        from jobs.lifelog_realtime import run_realtime_proposals
        await run_realtime_proposals(bot)

    # No new proposal because event was already in activity_log
    assert db.get_pending_proposals() == []
    bot.send_message.assert_not_called()
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Implement `jobs/lifelog_realtime.py`**

```python
"""
lifelog_realtime — runs every ~15 minutes.

Fetches recently-added calendar events, ingests them into activity_log,
and sends real-time Telegram proposals for any classified as "high confidence".
"""
import logging

from telegram import Bot

import database as db
from ai_life_log import propose_from_calendar_event
from config import TELEGRAM_CHAT_ID
from services.calendar_service import get_events_rolling_window, is_configured

logger = logging.getLogger(__name__)


def _fetch_recent_calendar_events() -> list[dict]:
    """Fetch the next 30 days of calendar events. Override in tests."""
    return get_events_rolling_window(days=30)


def _format_proposal_message(parsed: dict, event: dict, entry_id: int) -> str:
    cats = " + ".join(parsed["categories"]) or "(no category)"
    people = ", ".join(parsed.get("people", [])) or "(none)"
    location = parsed.get("location") or event.get("location", "") or "(none)"

    start = event["start_datetime"][:10] if event["start_datetime"] else ""
    end = event["end_datetime"][:10] if event["end_datetime"] else ""
    date_label = f"{start} → {end}" if end and end != start else start

    return (
        f"📅 *{parsed.get('description', event['title'])}*\n"
        f"🗓 {date_label}\n"
        f"🏷 {cats}\n"
        f"👥 {people}\n"
        f"📍 {location}\n\n"
        f"Reply *yes #{entry_id}* to confirm, *skip #{entry_id}* to dismiss, "
        f"or *edit #{entry_id} <new text>* to revise."
    )


async def run_realtime_proposals(bot: Bot):
    if not is_configured():
        logger.info("lifelog_realtime: calendar not configured, skipping")
        return

    events = _fetch_recent_calendar_events()
    active_cats = [c["name"] for c in db.get_active_categories()]

    new_proposals = 0
    for event in events:
        # Already ingested? skip
        if db.get_activity_by_source_id("calendar", event["event_id"]):
            continue

        # Always record raw activity
        db.record_activity(
            source="calendar",
            source_id=event["event_id"],
            event_type="calendar_event",
            occurred_at=event["start_datetime"] or None,
            payload=event,
        )

        # Classify
        parsed = propose_from_calendar_event(
            title=event["title"],
            start=event["start_datetime"],
            end=event["end_datetime"],
            attendees=event.get("attendees", []),
            description=event.get("description", ""),
            location=event.get("location", ""),
            active_categories=active_cats,
        )

        if parsed.get("confidence") != "high":
            continue  # day-after / sunday jobs handle the rest

        # Save proposal
        date_start = event["start_datetime"][:10]
        date_end = event["end_datetime"][:10] if event.get("end_datetime") else None
        if date_end == date_start:
            date_end = None

        entry_id = db.save_proposal(
            date_start=date_start,
            date_end=date_end,
            categories=parsed["categories"],
            description=parsed["description"] or event["title"],
            location=parsed.get("location") or event.get("location"),
            source="calendar",
            source_id=event["event_id"],
        )

        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=_format_proposal_message(parsed, event, entry_id),
            parse_mode="Markdown",
        )
        new_proposals += 1

    logger.info("lifelog_realtime: %d new proposals", new_proposals)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_lifelog_realtime.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jobs/lifelog_realtime.py tests/test_lifelog_realtime.py
git commit -m "feat(lifelog): realtime job for high-confidence calendar proposals"
```

### Task 4.2: Update `services/calendar_service.py` to expose attendees

**Files:**
- Modify: `services/calendar_service.py`

The current `get_events_rolling_window` doesn't return attendees. Add them.

- [ ] **Step 1: Modify the function**

In `services/calendar_service.py`, inside the loop building `events`, change the appended dict to:

```python
        attendees_raw = item.get("attendees", []) or []
        attendees = [
            (a.get("displayName") or a.get("email", "").split("@")[0])
            for a in attendees_raw
            if not a.get("self")
        ]

        events.append({
            "event_id": item["id"],
            "title": item.get("summary", "(No title)"),
            "start_datetime": start_dt,
            "end_datetime": end_dt,
            "description": item.get("description", ""),
            "location": item.get("location", ""),
            "is_recurring": bool(item.get("recurringEventId")),
            "attendees": attendees,
        })
```

- [ ] **Step 2: Quick smoke check**

Run: `python -c "from services.calendar_service import is_configured; print(is_configured())"`
Expected: prints True or False without import error.

- [ ] **Step 3: Commit**

```bash
git add services/calendar_service.py
git commit -m "feat(calendar): include attendees in event payload"
```

### Task 4.3: New job — `jobs/lifelog_dayafter.py`

**Files:**
- Create: `jobs/lifelog_dayafter.py`
- Create: `tests/test_lifelog_dayafter.py`

Runs once per day at 9am. Looks at yesterday's calendar events and sends day-after proposals for `confidence == "matched"`.

- [ ] **Step 1: Write failing test**

```python
"""Tests for jobs.lifelog_dayafter."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_dayafter_proposes_matched(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "matched",
        "categories": ["Skiing"],
        "description": "Skied at Killington",
        "location": "Killington, VT",
        "people": ["Justin"],
        "reason": "Single ski outing",
    }))]
    fake = [{
        "event_id": "ski1",
        "title": "Ski Killington",
        "start_datetime": "2026-05-01T09:00:00",
        "end_datetime": "2026-05-01T17:00:00",
        "description": "",
        "location": "Killington",
        "is_recurring": False,
        "attendees": ["Justin"],
    }]
    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("jobs.lifelog_dayafter._fetch_yesterdays_events", return_value=fake):
        from jobs.lifelog_dayafter import run_dayafter_proposals
        await run_dayafter_proposals(bot)

    import database as db
    proposals = db.get_pending_proposals()
    assert len(proposals) == 1
    bot.send_message.assert_called_once()
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Implement**

```python
"""
lifelog_dayafter — runs daily at 9am.

Looks at yesterday's calendar events. For events the realtime job didn't
already propose (because confidence was 'matched' or below), classifies
them now that they've actually happened, and proposes the matched ones.
"""
import datetime
import logging

import pytz
from telegram import Bot

import database as db
from ai_life_log import propose_from_calendar_event
from config import TELEGRAM_CHAT_ID, TIMEZONE
from services.calendar_service import is_configured, _get_service, GOOGLE_CALENDAR_ID
from jobs.lifelog_realtime import _format_proposal_message

logger = logging.getLogger(__name__)


def _fetch_yesterdays_events() -> list[dict]:
    if not is_configured():
        return []
    tz = pytz.timezone(TIMEZONE)
    today = datetime.datetime.now(tz).date()
    yesterday = today - datetime.timedelta(days=1)
    time_min = datetime.datetime.combine(yesterday, datetime.time.min, tzinfo=tz).isoformat()
    time_max = datetime.datetime.combine(yesterday, datetime.time.max, tzinfo=tz).isoformat()

    service = _get_service()
    result = service.events().list(
        calendarId=GOOGLE_CALENDAR_ID,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=250,
    ).execute()

    events = []
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
    return events


async def run_dayafter_proposals(bot: Bot):
    events = _fetch_yesterdays_events()
    active_cats = [c["name"] for c in db.get_active_categories()]
    new_proposals = 0

    for event in events:
        # Already promoted to a proposal? skip
        seen = db.get_activity_by_source_id("calendar", event["event_id"])
        if seen and seen[0].get("promoted_to_life_log"):
            continue
        # Record activity if not already
        if not seen:
            db.record_activity(
                source="calendar",
                source_id=event["event_id"],
                event_type="calendar_event",
                occurred_at=event["start_datetime"] or None,
                payload=event,
            )

        parsed = propose_from_calendar_event(
            title=event["title"],
            start=event["start_datetime"],
            end=event["end_datetime"],
            attendees=event.get("attendees", []),
            description=event.get("description", ""),
            location=event.get("location", ""),
            active_categories=active_cats,
        )

        if parsed.get("confidence") != "matched":
            continue

        date_start = event["start_datetime"][:10]
        entry_id = db.save_proposal(
            date_start=date_start,
            date_end=None,
            categories=parsed["categories"],
            description=parsed["description"] or event["title"],
            location=parsed.get("location") or event.get("location"),
            source="calendar",
            source_id=event["event_id"],
        )
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=_format_proposal_message(parsed, event, entry_id),
            parse_mode="Markdown",
        )
        new_proposals += 1

    logger.info("lifelog_dayafter: %d new proposals", new_proposals)
```

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add jobs/lifelog_dayafter.py tests/test_lifelog_dayafter.py
git commit -m "feat(lifelog): day-after job for matched calendar proposals"
```

### Task 4.4: New job — `jobs/lifelog_sunday.py` (weekly digest of maybes)

**Files:**
- Create: `jobs/lifelog_sunday.py`
- Create: `tests/test_lifelog_sunday.py`

Runs Sunday at 5pm. Looks at the past week's events not yet promoted. For events classified `maybe`, sends a single digest message.

- [ ] **Step 1: Write failing test**

```python
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_sunday_digest_batches_maybes(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "maybe",
        "categories": ["Visitors"],
        "description": "Brunch with Alex",
        "location": "Snooze",
        "people": ["Alex"],
        "reason": "ambiguous personal event",
    }))]
    fake = [
        {
            "event_id": f"ev{i}",
            "title": f"Brunch {i}",
            "start_datetime": f"2026-04-{20+i:02d}T11:00:00",
            "end_datetime": f"2026-04-{20+i:02d}T12:00:00",
            "description": "",
            "location": "Snooze",
            "is_recurring": False,
            "attendees": ["Alex"],
        }
        for i in range(3)
    ]
    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("jobs.lifelog_sunday._fetch_past_week_events", return_value=fake):
        from jobs.lifelog_sunday import run_sunday_digest
        await run_sunday_digest(bot)

    import database as db
    proposals = db.get_pending_proposals()
    assert len(proposals) == 3
    # One digest message, not 3 separate messages
    assert bot.send_message.call_count == 1
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Implement**

```python
"""
lifelog_sunday — runs Sunday at 5pm.

Reviews the past 7 days of calendar events. For any events not yet promoted,
classifies them. Sends a single digest message summarizing all "maybe"
candidates, with quick confirm/skip references.
"""
import datetime
import logging

import pytz
from telegram import Bot

import database as db
from ai_life_log import propose_from_calendar_event
from config import TELEGRAM_CHAT_ID, TIMEZONE
from services.calendar_service import is_configured, _get_service, GOOGLE_CALENDAR_ID

logger = logging.getLogger(__name__)


def _fetch_past_week_events() -> list[dict]:
    if not is_configured():
        return []
    tz = pytz.timezone(TIMEZONE)
    now = datetime.datetime.now(tz)
    time_max = now.isoformat()
    time_min = (now - datetime.timedelta(days=7)).isoformat()

    service = _get_service()
    result = service.events().list(
        calendarId=GOOGLE_CALENDAR_ID,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=250,
    ).execute()

    events = []
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
    return events


async def run_sunday_digest(bot: Bot):
    events = _fetch_past_week_events()
    active_cats = [c["name"] for c in db.get_active_categories()]

    proposals_made = []
    for event in events:
        seen = db.get_activity_by_source_id("calendar", event["event_id"])
        if seen and seen[0].get("promoted_to_life_log"):
            continue
        if not seen:
            db.record_activity(
                source="calendar",
                source_id=event["event_id"],
                event_type="calendar_event",
                occurred_at=event["start_datetime"] or None,
                payload=event,
            )

        parsed = propose_from_calendar_event(
            title=event["title"],
            start=event["start_datetime"],
            end=event["end_datetime"],
            attendees=event.get("attendees", []),
            description=event.get("description", ""),
            location=event.get("location", ""),
            active_categories=active_cats,
        )

        if parsed.get("confidence") != "maybe":
            continue

        date_start = event["start_datetime"][:10]
        entry_id = db.save_proposal(
            date_start=date_start,
            date_end=None,
            categories=parsed["categories"],
            description=parsed["description"] or event["title"],
            location=parsed.get("location") or event.get("location"),
            source="calendar",
            source_id=event["event_id"],
        )
        proposals_made.append((entry_id, parsed, event))

    if not proposals_made:
        logger.info("lifelog_sunday: no maybes to digest")
        return

    lines = ["📋 *Sunday digest — possibly Life Log–worthy this week:*", ""]
    for entry_id, parsed, event in proposals_made:
        cats = " + ".join(parsed["categories"]) or "?"
        date = event["start_datetime"][:10]
        desc = parsed.get("description", event["title"])
        lines.append(f"*#{entry_id}* — {desc}  ({cats}, {date})")
    lines.append("")
    lines.append("Reply *yes #N* / *skip #N* per item, or *yes all* / *skip all*.")

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text="\n".join(lines),
        parse_mode="Markdown",
    )
    logger.info("lifelog_sunday: sent digest with %d items", len(proposals_made))
```

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add jobs/lifelog_sunday.py tests/test_lifelog_sunday.py
git commit -m "feat(lifelog): sunday digest job for maybe-candidates"
```

### Task 4.5: Handler — `handlers/lifelog_proposals.py` for yes/skip/edit replies

**Files:**
- Create: `handlers/lifelog_proposals.py`
- Create: `tests/test_lifelog_proposals.py`

Parses replies like `yes #5`, `skip #5`, `edit #5 new text`, `yes all`, `skip all`. Returns True if message was handled.

- [ ] **Step 1: Write failing test**

```python
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_yes_n_confirms_proposal(temp_db_path):
    import database as db
    pid = db.save_proposal(
        date_start="2026-05-02", date_end=None, categories=["Concert"],
        description="Test", location=None, source="calendar", source_id="ev1",
    )
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    from handlers.lifelog_proposals import handle_proposal_reply
    handled = await handle_proposal_reply(update, context, f"yes #{pid}")
    assert handled is True
    assert db.get_life_log_entry(pid)["status"] == "confirmed"


@pytest.mark.asyncio
async def test_skip_n_dismisses(temp_db_path):
    import database as db
    pid = db.save_proposal(
        date_start="2026-05-02", date_end=None, categories=["Concert"],
        description="X", location=None, source="calendar", source_id="ev2",
    )
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    from handlers.lifelog_proposals import handle_proposal_reply
    handled = await handle_proposal_reply(update, context, f"skip #{pid}")
    assert handled is True
    assert db.get_life_log_entry(pid)["status"] == "dismissed"


@pytest.mark.asyncio
async def test_yes_all_confirms_all_pending(temp_db_path):
    import database as db
    p1 = db.save_proposal(
        date_start="2026-05-01", date_end=None, categories=["Concert"],
        description="A", location=None, source="calendar", source_id="ev1",
    )
    p2 = db.save_proposal(
        date_start="2026-05-02", date_end=None, categories=["Visitors"],
        description="B", location=None, source="calendar", source_id="ev2",
    )
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    from handlers.lifelog_proposals import handle_proposal_reply
    handled = await handle_proposal_reply(update, context, "yes all")
    assert handled is True
    assert db.get_life_log_entry(p1)["status"] == "confirmed"
    assert db.get_life_log_entry(p2)["status"] == "confirmed"


@pytest.mark.asyncio
async def test_unrelated_text_returns_false(temp_db_path):
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    from handlers.lifelog_proposals import handle_proposal_reply
    handled = await handle_proposal_reply(update, context, "some random message")
    assert handled is False
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Implement**

```python
"""Handler for replies to Life Log proposals (yes #N, skip #N, edit #N <text>, yes all, skip all)."""
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)


_YES_N = re.compile(r"^\s*(yes|y|confirm|save|ok)\s+#?(\d+)\s*$", re.IGNORECASE)
_SKIP_N = re.compile(r"^\s*(skip|no|dismiss)\s+#?(\d+)\s*$", re.IGNORECASE)
_EDIT_N = re.compile(r"^\s*edit\s+#?(\d+)\s+(.+)$", re.IGNORECASE | re.DOTALL)
_YES_ALL = re.compile(r"^\s*yes\s+all\s*$", re.IGNORECASE)
_SKIP_ALL = re.compile(r"^\s*skip\s+all\s*$", re.IGNORECASE)


async def _confirm_one(entry_id: int) -> dict | None:
    entry = db.get_life_log_entry(entry_id)
    if entry is None or entry["status"] != "proposed":
        return None
    db.confirm_proposal(entry_id)
    for cat in entry["categories"]:
        db.increment_category_usage(cat)
    if entry.get("source_id"):
        rows = db.get_activity_by_source_id(entry["source"], entry["source_id"])
        if rows:
            db.mark_activity_promoted(rows[0]["id"])
    return entry


async def handle_proposal_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Returns True if the message was a recognized proposal reply."""
    m = _YES_N.match(text)
    if m:
        entry_id = int(m.group(2))
        entry = await _confirm_one(entry_id)
        if entry is None:
            await update.message.reply_text(f"No pending proposal #{entry_id}.")
        else:
            await update.message.reply_text(f"✅ Confirmed: *{entry['description']}*", parse_mode="Markdown")
        return True

    m = _SKIP_N.match(text)
    if m:
        entry_id = int(m.group(2))
        entry = db.get_life_log_entry(entry_id)
        if entry is None or entry["status"] != "proposed":
            await update.message.reply_text(f"No pending proposal #{entry_id}.")
        else:
            db.dismiss_proposal(entry_id)
            await update.message.reply_text(f"⏭ Skipped #{entry_id}.")
        return True

    m = _EDIT_N.match(text)
    if m:
        entry_id = int(m.group(1))
        new_desc = m.group(2).strip()
        entry = db.get_life_log_entry(entry_id)
        if entry is None or entry["status"] != "proposed":
            await update.message.reply_text(f"No pending proposal #{entry_id}.")
        else:
            db.update_life_log_entry(
                entry_id, entry["categories"], new_desc, entry.get("location"), entry.get("notes")
            )
            await _confirm_one(entry_id)
            await update.message.reply_text(f"✅ Edited & confirmed: *{new_desc}*", parse_mode="Markdown")
        return True

    if _YES_ALL.match(text):
        pending = db.get_pending_proposals()
        for p in pending:
            await _confirm_one(p["id"])
        await update.message.reply_text(f"✅ Confirmed all {len(pending)} proposals.")
        return True

    if _SKIP_ALL.match(text):
        pending = db.get_pending_proposals()
        for p in pending:
            db.dismiss_proposal(p["id"])
        await update.message.reply_text(f"⏭ Skipped all {len(pending)} proposals.")
        return True

    return False
```

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Wire into `bot.py` `handle_message`**

In `bot.py` `handle_message`, BEFORE the `if state == "idle":` block, add:

```python
    # Proposal replies are stateless — handle in any state
    from handlers.lifelog_proposals import handle_proposal_reply
    if await handle_proposal_reply(update, context, text):
        return
```

- [ ] **Step 6: Commit**

```bash
git add handlers/lifelog_proposals.py tests/test_lifelog_proposals.py bot.py
git commit -m "feat(lifelog): proposal-reply handler (yes/skip/edit/all) wired to bot"
```

---

## M5 — Relationship Arc Tracking

### Task 5.1: Detect "broke up with X" / "ended with X" in `/log` flow

**Files:**
- Modify: `ai_life_log.py`
- Modify: `tests/test_ai_life_log.py`

We extend `parse_log_command` to also detect relationship-end intent.

- [ ] **Step 1: Write failing test**

```python
def test_parse_log_detects_breakup(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "categories": ["Relationship"],
        "description": "Broke up with Megan",
        "location": None,
        "date_start": "2026-05-02",
        "date_end": None,
        "people": ["Megan"],
        "questions": [],
        "relationship_event": {"action": "end", "person": "Megan"}
    }))]
    import ai_life_log
    result = ai_life_log.parse_log_command(
        "Broke up with Megan today",
        today="2026-05-02",
        active_categories=["Relationship"],
    )
    assert result.get("relationship_event") == {"action": "end", "person": "Megan"}
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Update prompt in `parse_log_command`**

In `ai_life_log.py`, update the prompt for `parse_log_command` to add:

```python
# (Insert this block in the JSON shape near the bottom)
  "relationship_event": null  // OR {"action": "end" | "start" | "milestone", "person": "Name"}
```

And in the rules section:
```
- If the message indicates ending a romantic relationship ("broke up", "ended things"),
  set "relationship_event" to {"action": "end", "person": "X"}.
- If it indicates starting a new dating relationship ("started dating X", "official with X"),
  set {"action": "start", "person": "X"}.
- Otherwise, leave relationship_event as null.
```

Also update the default return to include `"relationship_event": None`.

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add ai_life_log.py tests/test_ai_life_log.py
git commit -m "feat(lifelog): detect relationship start/end events in /log"
```

### Task 5.2: Apply `relationship_event` in confirm flow

**Files:**
- Modify: `handlers/log_command.py`
- Modify: `tests/test_log_command.py`

When confirming a Life Log entry with a `relationship_event`, update the corresponding `people` row.

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_confirm_breakup_sets_person_status_ended(temp_db_path):
    import database as db
    from handlers.log_command import handle_confirm_response

    pid = db.save_person(
        name="Megan", aliases=[], relationship_type="dating",
        first_seen="2026-02-15", notes=None,
    )
    db.set_state(
        "lifelog_confirming",
        temp_data={
            "original_text": "Broke up with Megan today",
            "parsed": {
                "categories": ["Relationship"],
                "description": "Broke up with Megan",
                "location": None,
                "date_start": "2026-05-02",
                "date_end": None,
                "people": ["Megan"],
                "questions": [],
                "relationship_event": {"action": "end", "person": "Megan"},
            },
        },
    )
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await handle_confirm_response(update, context, "yes")

    p = db.get_person(pid)
    assert p["status"] == "ended"
    assert p["end_date"] == "2026-05-02"
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Update `handle_confirm_response`**

In `handlers/log_command.py`, after the `_link_or_create_people(...)` line in the YES branch, add:

```python
        rel_event = parsed.get("relationship_event")
        if rel_event and rel_event.get("person"):
            person = db.find_person_by_name(rel_event["person"])
            if person:
                action = rel_event.get("action")
                if action == "end":
                    db.set_person_relationship_status(
                        person["id"], "ended", end_date=parsed["date_start"],
                    )
                elif action == "start":
                    # Update relationship_type to dating, set start_date
                    from database import _cursor, _p as _ph
                    with _cursor(write=True) as c:
                        c.execute(
                            f"UPDATE people SET relationship_type='dating', "
                            f"start_date={_ph()}, status='active' WHERE id={_ph()}",
                            (parsed["date_start"], person["id"]),
                        )
```

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add handlers/log_command.py tests/test_log_command.py
git commit -m "feat(lifelog): apply relationship_event on confirm to update person status"
```

### Task 5.3: `/people` command — list, view, merge

**Files:**
- Create: `handlers/people.py`
- Create: `tests/test_people_command.py`

- [ ] **Step 1: Write failing tests**

```python
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_people_command_lists_all(temp_db_path):
    import database as db
    db.save_person("Megan", [], "dating", "2026-02-15", None)
    db.save_person("Sprink", [], "friend", "2024-01-01", None)

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = []

    from handlers.people import people_command
    await people_command(update, context)

    update.message.reply_text.assert_called_once()
    args, _ = update.message.reply_text.call_args
    assert "Megan" in args[0]
    assert "Sprink" in args[0]


@pytest.mark.asyncio
async def test_people_merge(temp_db_path):
    import database as db
    keep = db.save_person("Spinkel", [], "friend", "2024-01-01", None)
    merge = db.save_person("Sprink", [], "friend", "2024-01-01", None)

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = ["merge", str(merge), "into", str(keep)]

    from handlers.people import people_command
    await people_command(update, context)

    assert db.get_person(merge) is None
    assert db.get_person(keep) is not None
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Implement**

```python
"""Handler for the /people command — list, view, merge people in the Life Log."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)


async def people_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []

    if len(args) >= 4 and args[0].lower() == "merge" and args[2].lower() == "into":
        try:
            merge_id = int(args[1])
            keep_id = int(args[3])
        except ValueError:
            await update.message.reply_text("Usage: /people merge <merge_id> into <keep_id>")
            return
        db.merge_people(keep_id=keep_id, merge_id=merge_id)
        await update.message.reply_text(f"✅ Merged person #{merge_id} into #{keep_id}.")
        return

    people = db.get_all_people()
    if not people:
        await update.message.reply_text("No people in your Life Log yet.")
        return

    lines = ["👥 *People in your Life Log:*", ""]
    for p in people:
        rel = p.get("relationship_type") or "—"
        status = p.get("status", "active")
        last_seen = p.get("last_seen") or "?"
        line = f"`#{p['id']}` *{p['name']}* ({rel}, {status}) — last seen {last_seen}"
        if p.get("aliases"):
            line += f"  _aliases: {', '.join(p['aliases'])}_"
        lines.append(line)
    lines.append("")
    lines.append("To merge duplicates: `/people merge <id> into <id>`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
```

- [ ] **Step 4: Wire into `bot.py`**

In `bot.py`:
```python
from handlers.people import people_command
```
And in `create_application()`:
```python
    app.add_handler(CommandHandler("people", people_command))
```

- [ ] **Step 5: Run tests, verify pass**

- [ ] **Step 6: Commit**

```bash
git add handlers/people.py tests/test_people_command.py bot.py
git commit -m "feat(lifelog): /people command for list and merge"
```

---

## M6 — Sheets Sync (Life Log + People Tabs)

### Task 6.1: Add Life Log tab writer to `google_sheets.py`

**Files:**
- Modify: `google_sheets.py`
- Create: `tests/test_lifelog_sheets.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for Life Log Sheets sync (mocked gspread)."""
from unittest.mock import MagicMock


def test_build_life_log_rows_basic():
    from google_sheets import _build_life_log_rows
    entries = [
        {
            "id": 1, "date_start": "2025-05-05", "date_end": "2025-05-12",
            "categories": ["Wedding", "Vacation"], "description": "Spinkel Wedding",
            "location": "London → Spain", "notes": None, "status": "confirmed",
            "source": "manual",
        },
    ]
    people_by_entry = {1: ["Sprink", "Emily"]}
    rows = _build_life_log_rows(entries, people_by_entry)
    # Header row + entry row
    assert len(rows) == 2
    assert rows[0][0] == "Date"
    assert "Wedding, Vacation" in rows[1][2]
    assert "Sprink, Emily" in rows[1][4]


def test_build_life_log_empty():
    from google_sheets import _build_life_log_rows
    rows = _build_life_log_rows([], {})
    assert len(rows) >= 1  # at least the header
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Add functions to `google_sheets.py`**

Add near the existing constants:
```python
SHEET_LIFE_LOG = "Life Log"
SHEET_PEOPLE = "People"
```

In `_ensure_sheets`, add `Life Log` and `People` to the list:
```python
    for name, cols in [
        (SHEET_WEEKLY, 6), (SHEET_LATER, 4), (SHEET_HABITS, 12),
        (SHEET_LIFE_LOG, 10), (SHEET_PEOPLE, 10),
    ]:
```

Add the new builder functions:
```python
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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_lifelog_sheets.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add google_sheets.py tests/test_lifelog_sheets.py
git commit -m "feat(lifelog): build Life Log and People sheet rows"
```

### Task 6.2: Add `sync_life_log_to_sheets` orchestration

**Files:**
- Modify: `google_sheets.py`

- [ ] **Step 1: Add the orchestration function**

In `google_sheets.py`:
```python
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
```

- [ ] **Step 2: Smoke check**

Run: `python -c "from google_sheets import sync_life_log_to_sheets; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add google_sheets.py
git commit -m "feat(lifelog): orchestrate Life Log + People sheet rebuild"
```

### Task 6.3: New `/sync` flow that includes Life Log

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Add new sync invocation in `_sync_to_sheets_with_ai`**

In `bot.py` `_sync_to_sheets_with_ai`, after the existing weekly/later/habits sync, add:

```python
    # Life Log + People tabs
    from google_sheets import sync_life_log_to_sheets
    life_log_entries = db.get_all_life_log_entries()
    all_people = db.get_all_people()
    people_by_entry = {
        e["id"]: [p["name"] for p in db.get_people_for_entry(e["id"])]
        for e in life_log_entries
    }
    sync_life_log_to_sheets(life_log_entries, all_people, people_by_entry)
```

- [ ] **Step 2: Smoke check**

Run: `python -c "from bot import _sync_to_sheets_with_ai; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "feat(lifelog): include Life Log + People in /sync flow"
```

### Task 6.4: Auto-sync on confirmation (optional, light-touch)

**Files:**
- Modify: `handlers/log_command.py` and `handlers/lifelog_proposals.py`

After a successful confirm, schedule a background sync so the Sheet stays fresh without the user manually running `/sync`. Use `context.application.create_task` to fire-and-forget.

- [ ] **Step 1: Add sync trigger to confirm paths**

In `handlers/log_command.py`, in the YES branch of `handle_confirm_response`, after the new-people block, add:

```python
        # Fire-and-forget sync
        try:
            from google_sheets import sync_life_log_to_sheets
            entries = db.get_all_life_log_entries()
            people = db.get_all_people()
            people_by_entry = {
                e["id"]: [p["name"] for p in db.get_people_for_entry(e["id"])]
                for e in entries
            }
            sync_life_log_to_sheets(entries, people, people_by_entry)
        except Exception as e:
            logger.warning("Auto-sync failed (non-fatal): %s", e)
```

In `handlers/lifelog_proposals.py`, after `_confirm_one`, add the same try-block.

- [ ] **Step 2: Smoke check**

Run: `pytest tests/test_log_command.py tests/test_lifelog_proposals.py -v`
Expected: PASS (Sheets calls fail in tests because no creds — caught by try/except).

- [ ] **Step 3: Commit**

```bash
git add handlers/log_command.py handlers/lifelog_proposals.py
git commit -m "feat(lifelog): auto-sync to Sheets on confirm (best-effort)"
```

---

## M7 — Telegram Natural-Language Queries

### Task 7.1: Tool-using query service

**Files:**
- Create: `services/lifelog_query_service.py`
- Create: `tests/test_lifelog_query_service.py`

The query service uses Claude's tool-use API to answer natural-language questions by calling read-only DB functions.

- [ ] **Step 1: Write failing tests**

```python
"""Tests for lifelog_query_service."""
import json
from unittest.mock import MagicMock

import pytest


def test_when_did_i_last_see_x(temp_db_path, mock_anthropic):
    import database as db
    pid = db.save_person("Megan", [], "dating", "2026-02-15", None)
    eid = db.save_life_log_entry(
        date_start="2026-04-20", date_end=None, categories=["Relationship"],
        description="Dinner with Megan", location="Denver", notes=None,
        status="confirmed", source="manual", source_id=None,
    )
    db.link_entry_to_people(eid, [pid])
    db.update_person_last_seen(pid, "2026-04-20")

    # First call: tool_use to find the person
    # Second call: final answer
    mock_anthropic.messages.create.side_effect = [
        MagicMock(
            stop_reason="tool_use",
            content=[
                MagicMock(type="text", text="Looking up Megan..."),
                MagicMock(type="tool_use", name="find_person", id="t1", input={"name": "Megan"}),
            ],
        ),
        MagicMock(
            stop_reason="end_turn",
            content=[MagicMock(type="text", text="Last seen Megan on 2026-04-20 (Dinner with Megan).")],
        ),
    ]

    from services.lifelog_query_service import answer_query
    answer = answer_query("When did I last see Megan?")
    assert "2026-04-20" in answer or "April 20" in answer
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Implement**

```python
"""Natural-language query layer using Claude's tool-use API."""
import json
import logging

import anthropic

import database as db
from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

_TOOLS = [
    {
        "name": "find_person",
        "description": "Find a person by name or alias.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "get_entries_for_person",
        "description": "All Life Log entries linked to a person, oldest first.",
        "input_schema": {
            "type": "object",
            "properties": {"person_id": {"type": "integer"}},
            "required": ["person_id"],
        },
    },
    {
        "name": "list_all_people",
        "description": "List all people with their last_seen dates.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_entries_in_range",
        "description": "All Life Log entries in a date range (inclusive).",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "get_entries_by_category",
        "description": "All Life Log entries that include a given category.",
        "input_schema": {
            "type": "object",
            "properties": {"category": {"type": "string"}},
            "required": ["category"],
        },
    },
]


def _tool_dispatch(name: str, params: dict) -> str:
    try:
        if name == "find_person":
            p = db.find_person_by_name(params["name"])
            return json.dumps(p, default=str) if p else "null"
        if name == "get_entries_for_person":
            entries = db.get_entries_for_person(params["person_id"])
            return json.dumps(entries, default=str)
        if name == "list_all_people":
            people = db.get_all_people()
            return json.dumps(people, default=str)
        if name == "get_entries_in_range":
            entries = db.get_life_log_entries_in_range(params["date_from"], params["date_to"])
            return json.dumps(entries, default=str)
        if name == "get_entries_by_category":
            cat = params["category"]
            all_entries = db.get_all_life_log_entries()
            matching = [e for e in all_entries if cat in (e.get("categories") or [])]
            return json.dumps(matching, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
    return json.dumps({"error": f"unknown tool {name}"})


_SYSTEM = """You are a helpful assistant answering questions about a personal Life Log
(a 30-year memoir of meaningful events). Use the available tools to look up data, then
give a short, friendly natural-language answer. Cite specific dates and details from the data.
If the user asks an unanswerable question, say so plainly."""


def answer_query(question: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = [{"role": "user", "content": question}]

    for _ in range(5):  # cap tool-call rounds
        resp = client.messages.create(
            model=MODEL,
            max_tokens=800,
            system=_SYSTEM,
            tools=_TOOLS,
            messages=messages,
        )

        if resp.stop_reason == "end_turn":
            return "".join(
                b.text for b in resp.content if getattr(b, "type", None) == "text"
            ).strip()

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    result = _tool_dispatch(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop_reason
        break

    return "Sorry — couldn't formulate an answer."
```

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add services/lifelog_query_service.py tests/test_lifelog_query_service.py
git commit -m "feat(lifelog): tool-using query service for natural-language Q&A"
```

### Task 7.2: `/ask` command handler

**Files:**
- Create: `handlers/lifelog_queries.py`
- Create: `tests/test_lifelog_queries.py`

- [ ] **Step 1: Write failing test**

```python
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_ask_command_runs_query(temp_db_path):
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = ["When", "did", "I", "last", "see", "Megan?"]

    with patch("handlers.lifelog_queries.answer_query", return_value="Last seen 2026-04-20"):
        from handlers.lifelog_queries import ask_command
        await ask_command(update, context)

    update.message.reply_text.assert_called_once()
    args, _ = update.message.reply_text.call_args
    assert "2026-04-20" in args[0]


@pytest.mark.asyncio
async def test_ask_command_no_args_prompts(temp_db_path):
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = []

    from handlers.lifelog_queries import ask_command
    await ask_command(update, context)

    update.message.reply_text.assert_called_once()
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Implement**

```python
"""Handler for /ask — natural-language queries against the Life Log."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from services.lifelog_query_service import answer_query

logger = logging.getLogger(__name__)


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text(
            "🔍 *Ask your Life Log anything*\n\n"
            "Examples:\n"
            "• `/ask When did I last see Sprink?`\n"
            "• `/ask How many trips did I take in 2025?`\n"
            "• `/ask Show me everything with Mom`\n"
            "• `/ask Who haven't I seen in 6 months?`",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text("🤔 Thinking...")
    try:
        answer = answer_query(question)
    except Exception as e:
        logger.error("Query failed: %s", e, exc_info=True)
        await update.message.reply_text(f"Sorry — query failed: {e}")
        return

    await update.message.reply_text(answer)
```

- [ ] **Step 4: Wire into `bot.py`**

```python
from handlers.lifelog_queries import ask_command
# ...
    app.add_handler(CommandHandler("ask", ask_command))
```

- [ ] **Step 5: Run tests, verify pass**

- [ ] **Step 6: Commit**

```bash
git add handlers/lifelog_queries.py tests/test_lifelog_queries.py bot.py
git commit -m "feat(lifelog): /ask command for natural-language queries"
```

### Task 7.3: Add `/ask` to `_COMMANDS_TEXT`

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Add to the Life Log section**

In `_COMMANDS_TEXT`:
```
• /ask \\[question\\] — Natural\\-language query of your Life Log
```

- [ ] **Step 2: Smoke verify**

Run: `python -c "from bot import _COMMANDS_TEXT; print(_COMMANDS_TEXT)"`

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "docs(lifelog): add /ask to bot command listing"
```

### Task 7.4: Register Life Log scheduler jobs

**Files:**
- Modify: `bot.py`
- Modify: `config.py`

Replace the existing `calendar_sync_job` (which writes to `/later`) with the new Life Log job set.

- [ ] **Step 1: Add config knobs**

Append to `config.py`:
```python
LIFELOG_REALTIME_INTERVAL_MIN = int(os.getenv("LIFELOG_REALTIME_INTERVAL_MIN", "15"))
LIFELOG_DAYAFTER_HOUR = int(os.getenv("LIFELOG_DAYAFTER_HOUR", "9"))
LIFELOG_SUNDAY_HOUR = int(os.getenv("LIFELOG_SUNDAY_HOUR", "17"))
```

- [ ] **Step 2: Update bot.py imports**

```python
from jobs.lifelog_realtime import run_realtime_proposals
from jobs.lifelog_dayafter import run_dayafter_proposals
from jobs.lifelog_sunday import run_sunday_digest
from config import (
    LIFELOG_REALTIME_INTERVAL_MIN,
    LIFELOG_DAYAFTER_HOUR,
    LIFELOG_SUNDAY_HOUR,
)
```

- [ ] **Step 3: Register jobs in `create_application`**

Add after the existing `calendar_jobs_available` block:

```python
    if _CALENDAR_JOBS_AVAILABLE:
        async def lifelog_realtime_job(context: ContextTypes.DEFAULT_TYPE):
            await run_realtime_proposals(context.bot)

        async def lifelog_dayafter_job(context: ContextTypes.DEFAULT_TYPE):
            await run_dayafter_proposals(context.bot)

        async def lifelog_sunday_job(context: ContextTypes.DEFAULT_TYPE):
            await run_sunday_digest(context.bot)

        app.job_queue.run_repeating(
            lifelog_realtime_job,
            interval=LIFELOG_REALTIME_INTERVAL_MIN * 60,
            first=60,
            name="lifelog_realtime",
        )
        app.job_queue.run_daily(
            lifelog_dayafter_job,
            time=datetime.time(hour=LIFELOG_DAYAFTER_HOUR, minute=0, tzinfo=tz),
            name="lifelog_dayafter",
        )
        app.job_queue.run_daily(
            lifelog_sunday_job,
            time=datetime.time(hour=LIFELOG_SUNDAY_HOUR, minute=0, tzinfo=tz),
            days=(6,),  # Sunday
            name="lifelog_sunday",
        )
```

- [ ] **Step 4: Smoke check**

Run: `python -c "from bot import create_application; print('ok')"`
Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
git add bot.py config.py
git commit -m "feat(lifelog): register realtime/dayafter/sunday scheduler jobs"
```

### Task 7.5: Disable old calendar→Later flow (cutover prep)

**Files:**
- Modify: `bot.py`

The existing `calendar_sync_job` (from `jobs/daily_calendar.py`) wrote to `/later`. New realtime/dayafter/sunday jobs replace it. Keep the old code in place but stop registering the job.

- [ ] **Step 1: Comment out the old job registration in `create_application`**

Find the block that registers `calendar_sync_job`:
```python
            app.job_queue.run_daily(
                calendar_sync_job,
                time=datetime.time(hour=0, minute=1, tzinfo=tz),
                name="calendar_sync",
            )
```
Comment it out:
```python
            # Replaced by lifelog_realtime + lifelog_dayafter + lifelog_sunday in M7.
            # app.job_queue.run_daily(
            #     calendar_sync_job,
            #     time=datetime.time(hour=0, minute=1, tzinfo=tz),
            #     name="calendar_sync",
            # )
```

Also stop registering `ai_status_job` and `monthly_forward_job` if you want to retire those too. For now, keep `monthly_forward_job` (still useful), drop `ai_status_job` (was only for Later items).

Comment out:
```python
            # ai_status_job retired with the /later concept (M10)
            # app.job_queue.run_daily(
            #     ai_status_job,
            #     time=datetime.time(hour=0, minute=15, tzinfo=tz),
            #     name="ai_status",
            # )
```

- [ ] **Step 2: Verify the bot still starts**

Run: `python -c "from bot import create_application; create_application(); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "refactor(lifelog): retire calendar→Later job, keep monthly_forward"
```

---

## M8 — Spreadsheet Backfill Script

### Task 8.1: Add `LIFE_LOG_IMPORT_SHEET_ID` config

**Files:**
- Modify: `config.py`
- Modify: `.env.example`

- [ ] **Step 1: Add to `config.py`**

```python
LIFE_LOG_IMPORT_SHEET_ID = os.getenv("LIFE_LOG_IMPORT_SHEET_ID", "")
```

- [ ] **Step 2: Add to `.env.example`**

```
# Spreadsheet ID for one-time Life Log backfill (the user's existing memory sheet)
LIFE_LOG_IMPORT_SHEET_ID=
```

- [ ] **Step 3: Commit**

```bash
git add config.py .env.example
git commit -m "config(lifelog): add LIFE_LOG_IMPORT_SHEET_ID for backfill"
```

### Task 8.2: Implement `scripts/import_life_log_spreadsheet.py`

**Files:**
- Create: `scripts/import_life_log_spreadsheet.py`
- Create: `tests/test_import_spreadsheet.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for spreadsheet backfill."""
import json
from unittest.mock import MagicMock, patch


def test_parse_spreadsheet_row_basic(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "categories": ["Vacation"],
        "description": "Vermont for July 4th with Mom and Dad",
        "location": "Vermont",
        "people": ["Mom", "Dad"],
    }))]
    from scripts.import_life_log_spreadsheet import _import_one_row
    import database as db
    _import_one_row(
        year="2025", month="07 - July",
        category="Vacation",
        description="Vermont for July 4th with Mom and Dad",
        active_categories=["Vacation"],
    )
    entries = db.get_all_life_log_entries()
    assert len(entries) == 1
    assert entries[0]["date_start"].startswith("2025-07")
    assert "Vacation" in entries[0]["categories"]
    people = db.get_all_people()
    assert {p["name"] for p in people} == {"Mom", "Dad"}
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Implement script**

```python
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
import logging
import re

import gspread

import database as db
from ai_life_log import extract_entry_from_existing_text
from config import LIFE_LOG_IMPORT_SHEET_ID
from google_sheets import _get_spreadsheet  # NOTE: re-uses the existing creds path

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


def _import_one_row(year: str, month: str, category: str, description: str, active_categories: list[str]):
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


def _open_source_sheet(tab_name: str | None) -> gspread.Worksheet:
    if not LIFE_LOG_IMPORT_SHEET_ID:
        raise SystemExit("Set LIFE_LOG_IMPORT_SHEET_ID before running.")
    # Open the user's existing memory sheet (different ID than the destination Sheet)
    from google_sheets import _get_spreadsheet
    # Reuse the credentials machinery but open by a different ID
    import gspread, json, os
    from google.oauth2.service_account import Credentials
    from config import GOOGLE_SERVICE_ACCOUNT_FILE, GOOGLE_SERVICE_ACCOUNT_JSON
    from google_sheets import SCOPES, _fix_json_newlines
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
```

- [ ] **Step 4: Run unit test, verify pass**

Run: `pytest tests/test_import_spreadsheet.py -v`
Expected: PASS for `test_parse_spreadsheet_row_basic`.

- [ ] **Step 5: Commit**

```bash
git add scripts/import_life_log_spreadsheet.py tests/test_import_spreadsheet.py
git commit -m "feat(lifelog): one-time spreadsheet backfill script"
```

### Task 8.3: Document the import flow in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a short section under "Quick Commands"**

```markdown
# One-time Life Log backfill from existing memory spreadsheet
LIFE_LOG_IMPORT_SHEET_ID=<source_sheet_id> python -m scripts.import_life_log_spreadsheet

# Optional: specify a tab name
python -m scripts.import_life_log_spreadsheet --tab "Memory Log"
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(lifelog): document spreadsheet backfill command"
```

---

## M9 — Calendar History Backfill Script

### Task 9.1: Implement `scripts/import_calendar_history.py`

**Files:**
- Create: `scripts/import_calendar_history.py`
- Create: `tests/test_import_calendar.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for calendar history backfill (mocked)."""
import json
from unittest.mock import MagicMock, patch


def test_import_creates_proposals(temp_db_path, mock_anthropic):
    fake_events = [{
        "event_id": "old1",
        "title": "Vermont trip",
        "start_datetime": "2024-07-01",
        "end_datetime": "2024-07-08",
        "description": "",
        "location": "Vermont",
        "is_recurring": False,
        "attendees": [],
    }]
    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "confidence": "high",
        "categories": ["Vacation"],
        "description": "Vermont trip",
        "location": "Vermont",
        "people": [],
        "reason": "Multi-day trip"
    }))]

    with patch(
        "scripts.import_calendar_history._fetch_year_events",
        return_value=fake_events,
    ):
        from scripts.import_calendar_history import import_year
        import database as db
        import_year(2024, dry_run=False)

    proposals = db.get_pending_proposals()
    assert len(proposals) == 1
    assert proposals[0]["categories"] == ["Vacation"]
```

- [ ] **Step 2: Run — should fail**

- [ ] **Step 3: Implement**

```python
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


def _fetch_year_events(year: int) -> list[dict]:
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


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add scripts/import_calendar_history.py tests/test_import_calendar.py
git commit -m "feat(lifelog): calendar-history backfill script"
```

### Task 9.2: Document the calendar backfill in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add to Quick Commands section**

```markdown
# Calendar history backfill — creates Life Log proposals you can confirm via Telegram
python -m scripts.import_calendar_history --start-year 2018  # all years from 2018-now
python -m scripts.import_calendar_history --year 2024        # one year
python -m scripts.import_calendar_history --start-year 2024 --dry-run  # preview
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(lifelog): document calendar history backfill"
```

### Task 9.3: Smoke test the AI-extraction quality (manual)

**Files:** N/A — operational task

- [ ] **Step 1: Run dry-run against a small year**

Run locally with real credentials:
```bash
python -m scripts.import_calendar_history --year 2024 --dry-run | head -30
```

- [ ] **Step 2: Eyeball the output**

Check: are events being correctly classified high/matched/maybe/skip? Are obvious noise events (standups, dentist) being skipped? If too many false positives, tune the prompt in Task 2.2.

- [ ] **Step 3: Iterate prompt as needed (no commit if no changes)**

If changes needed: edit `propose_from_calendar_event` in `ai_life_log.py`, update the test if assertions change, commit:
```bash
git add ai_life_log.py tests/test_ai_life_log.py
git commit -m "fix(lifelog): tune calendar event classification prompt"
```

---

## M10 — Cutover (deprecate old commands & sheet writes)

### Task 10.1: Remove old daily/weekly capture commands

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Remove handlers from `create_application`**

Delete (or comment out) these handler registrations:
```python
    app.add_handler(CommandHandler("update", update_command))
    app.add_handler(CommandHandler("work", work_command))
    app.add_handler(CommandHandler("personal", personal_command))
    app.add_handler(CommandHandler("focus", focus_command))
    app.add_handler(CommandHandler("later", later_command))
```

Also delete the `daily_prompt_job` registration:
```python
    app.job_queue.run_daily(
        daily_prompt_job, ...
    )
```

(Keep the function definitions in `bot.py` for now — removing them is a separate cleanup. They are dead code from the user's perspective once not registered.)

- [ ] **Step 2: Update `_COMMANDS_TEXT`**

Remove the *📝 Daily Logging* section entirely. Keep:
- 📊 Viewing & Syncing
- 🏃 Habits
- 🧠 Life Log (new)
- 📅 Calendar (drop /calendarsync — replaced by Life Log auto-ingestion)
- ⚙️ Admin

- [ ] **Step 3: Remove freeform fallback in `handle_message`**

In `handle_message`, the `if state == "idle":` branch currently calls `_handle_freeform_message`. Remove that — freeform text in idle state should now do nothing or send a hint. Replace with:

```python
    if state == "idle":
        await update.message.reply_text(
            "I'm not sure what to do with that.\n\n"
            "Try `/log <text>` to log a Life Log entry, or `/ask <question>` to query.",
            parse_mode="Markdown",
        )
        return
```

- [ ] **Step 4: Smoke check the bot still starts**

Run: `python -c "from bot import create_application; app = create_application(); print('ok')"`
Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "refactor(lifelog): remove deprecated /update /work /personal /focus /later commands"
```

### Task 10.2: Stop writing to old Sheets tabs

**Files:**
- Modify: `bot.py`
- Modify: `google_sheets.py`

- [ ] **Step 1: Replace `_sync_to_sheets_with_ai` body**

In `bot.py`, replace `_sync_to_sheets_with_ai` with a slimmer version focused on Life Log + Habits:

```python
async def _sync_to_sheets_with_ai(bot) -> tuple:
    """Sync Life Log, People, and Habits tabs."""
    from google_sheets import sync_life_log_to_sheets, sync_habits_to_sheets

    life_log_entries = db.get_all_life_log_entries()
    all_people = db.get_all_people()
    people_by_entry = {
        e["id"]: [p["name"] for p in db.get_people_for_entry(e["id"])]
        for e in life_log_entries
    }
    url = sync_life_log_to_sheets(life_log_entries, all_people, people_by_entry)

    all_habits = db.get_all_active_habits()
    all_habit_logs = db.get_all_habit_logs()
    sync_habits_to_sheets(all_habits, all_habit_logs)

    return url, 0  # second value retained for /sync caller signature
```

- [ ] **Step 2: Add `sync_habits_to_sheets` to `google_sheets.py`**

Extract the existing habits-grid logic into its own function:
```python
def sync_habits_to_sheets(all_habits: list, all_habit_logs: list) -> str:
    spreadsheet = _get_spreadsheet()
    _ensure_sheets(spreadsheet)
    habits_sheet = spreadsheet.worksheet(SHEET_HABITS)
    habits_rows = _build_habits_grid_rows(all_habits, all_habit_logs)
    habits_sheet.clear()
    if habits_rows:
        habits_sheet.update("A1", habits_rows)
    logger.info("Rebuilt Habits sheet (%d habits)", len(all_habits))
    return spreadsheet.url
```

- [ ] **Step 3: Smoke check**

Run: `python -c "from bot import _sync_to_sheets_with_ai; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add bot.py google_sheets.py
git commit -m "refactor(lifelog): /sync writes only Life Log + People + Habits"
```

### Task 10.3: Update `CLAUDE.md` to reflect the new architecture

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Rewrite the relevant sections**

Update the Telegram Commands table — remove old commands, add `/log`, `/ask`, `/people`. Update the Architecture and AI Layer sections to describe `ai_life_log.py` instead of (or in addition to) `ai_summarize.py`. Add a section "Life Log" at the top describing the memoir-substrate purpose. Replace State Machine doc with the new states (`lifelog_confirming`, `lifelog_new_person`).

- [ ] **Step 2: Verify by re-reading**

Read your updated CLAUDE.md and check it accurately describes the new system. Fix anything stale.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(lifelog): update CLAUDE.md to reflect new architecture"
```

---

## Self-Review (already performed)

**1. Spec coverage:**
- ✅ §3 Three Streams → tables created in M1; activity_log feeds insights layer
- ✅ §4 Data Model → all tables and columns implemented in M1
- ✅ §5.1 Continuous calendar ingestion (hybrid timing) → M4 tasks 4.1/4.3/4.4
- ✅ §5.2 `/log` command → M3
- ✅ §5.3 Relationship arc tracking → M5
- ✅ §5.4 Categories evolution → M2.4 + (background job registration deferred to ops; AI function shipped)
- ✅ §6 Output / retrieval → M6 (Sheets) + M7 (Telegram queries)
- ✅ §7 Deprecation → M10
- ✅ §8.1 Spreadsheet backfill → M8
- ✅ §8.2 Calendar history backfill → M9
- ✅ §9 Migration phases → M0 → M10 follow Phase 1 → 2 → 3

**2. Placeholder scan:** Searched for "TODO", "TBD", "implement later" — none in tasks. Test code is concrete with real assertions.

**3. Type consistency:**
- `parse_log_command` return shape matches what `handle_confirm_response` reads
- `propose_from_calendar_event` return shape matches what `_format_proposal_message` and proposal-save flow read
- `_link_or_create_people` returns `list[dict]` of new people; consumed correctly in handle_confirm_response
- DB function names consistent across tasks (verified `find_person_by_name`, `confirm_proposal`, `save_proposal`, `get_pending_proposals`)

**4. Known small gaps (acceptable, deferred to operational tuning):**
- Category-evolution job (M2.4 has the AI function, but no scheduled job registers it). Trivial to add later — the function is the hard part.
- Relationship-event detection requires the AI to populate `relationship_event` field — depends on prompt quality; tunable post-launch.
- Sheet read-back edits not implemented for Life Log tab (deferred per spec §10.4).

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-02-life-log-implementation.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
