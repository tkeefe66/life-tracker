# Story-Driven Proposals — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-event Proposals review with a story-driven flow: AI clusters pending calendar events into narrative stories (trip, interview cycle, conference, etc.), user bulk-triages in a Sheet, then confirms and enriches survivors in Telegram.

**Architecture:** Self-referential extension of `life_log_entries` (`parent_id`, `story_type`, `why_mattered`, `highlights`, `extras`). New `services/story_clustering.py` runs date-proximity pre-clustering then per-cluster Claude calls with `event_id_refs` validation. Hybrid review: a "Stories" sheet tab for bulk yes/skip, a Telegram state machine (`story_confirming` → `story_why_mattered` → `story_extras_optin` → optional Q&A) for narrative confirmation.

**Tech Stack:** Python 3.11+, python-telegram-bot 21.9, anthropic SDK, gspread, psycopg2 (prod) / sqlite3 (dev), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-05-04-story-driven-proposals-design.md`

---

## File Structure

**New files:**
- `services/story_clustering.py` — pre-clustering, flight detection, AI orchestration, validation
- `handlers/buildstories.py` — `/buildstories` Telegram command
- `handlers/syncstories.py` — `/syncstories` Telegram command + queue setup
- `handlers/story_review.py` — Telegram narrative state machine (multi-state)
- `handlers/showstory.py` — `/showstory <id>` debug dump
- `handlers/pushstories.py` — `/pushstories` retry sheet write
- `tests/test_story_clustering.py`
- `tests/test_database_stories.py`
- `tests/test_google_sheets_stories.py`
- `tests/test_buildstories_handler.py`
- `tests/test_syncstories_handler.py`
- `tests/test_story_review_handler.py`
- `tests/test_stories_e2e.py`
- `tests/fixtures/clustering/golden_clusters.json`
- `docs/superpowers/eval/clustering-eval-template.md`

**Modified files:**
- `database.py` — add 4 columns to `life_log_entries` (Postgres + SQLite); add story helpers
- `ai_life_log.py` — add `cluster_into_story` and `parse_extras_answer`
- `google_sheets.py` — add `sync_stories_to_sheet`, `read_story_decisions`, retire/clear old Proposals tab usage
- `bot.py` — register new handlers, retire `/proposals` / `/syncproposals` / `/pushproposals`, update `_COMMANDS_TEXT`, add new states to `handle_message` dispatcher
- `tests/conftest.py` — add `postgres_db` fixture (env-gated)
- `CLAUDE.md` — document new commands and module map

---

## Task 1: Add env-gated Postgres test fixture

**Why:** Both Postgres-only bugs hit this session (JSON serialization of `payload`; `date` not JSON-serializable) would have been caught by running the existing DB tests against Postgres. Add a fixture that lets a test parametrize over engines; on machines without Postgres available, the Postgres run is auto-skipped with a clear message.

**Files:**
- Modify: `tests/conftest.py`
- Test: implicit — used by Tasks 2 & 4

- [ ] **Step 1: Add the fixture to `tests/conftest.py`**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def postgres_db(monkeypatch):
    """Postgres-backed DB. Skipped unless TEST_POSTGRES_URL is set.

    Set TEST_POSTGRES_URL to a connection string pointing at an empty Postgres
    instance the test is allowed to wipe. Each test gets a fresh schema by
    dropping every public-schema table at fixture entry.
    """
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set; skipping Postgres test")

    import psycopg2
    conn = psycopg2.connect(url)
    conn.autocommit = True
    with conn.cursor() as c:
        c.execute(
            "DO $$ DECLARE r RECORD; BEGIN "
            "FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname='public') LOOP "
            "EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE'; "
            "END LOOP; END $$;"
        )
    conn.close()

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_PATH", "")
    import importlib
    import config
    importlib.reload(config)
    import database
    importlib.reload(database)
    database.initialize_db()
    yield url
```

- [ ] **Step 2: Verify the fixture skips cleanly without Postgres**

Run:
```bash
cd /Users/tomkeefe/Desktop/ClaudeApps/weekly-updates
unset TEST_POSTGRES_URL
pytest tests/test_lifelog_db.py -v
```
Expected: existing SQLite tests still pass; no new tests fail because we haven't added any Postgres-parametrized tests yet.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add env-gated postgres_db fixture for cross-engine tests"
```

---

## Task 2: Schema migration — 4 new columns on life_log_entries

**Files:**
- Modify: `database.py:165-200` (Postgres CREATE TABLE), `database.py:375-400` (SQLite CREATE TABLE), `database.py:247-271` (Postgres ALTER block), `database.py:440-460` (SQLite ALTER block — confirm exact line range when editing)
- Test: `tests/test_database_stories.py` (new)

- [ ] **Step 1: Write failing test for columns existing on a fresh DB**

Create `tests/test_database_stories.py`:

```python
"""Tests for story-related schema and helpers."""
import database


def _columns(c, table: str) -> set:
    """Return the set of column names on a table, engine-agnostic."""
    if database.USE_POSTGRES:
        c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s",
            (table,),
        )
    else:
        c.execute(f"PRAGMA table_info({table})")
    rows = c.fetchall()
    if database.USE_POSTGRES:
        return {r["column_name"] for r in rows}
    return {r["name"] for r in rows}


def test_life_log_entries_has_story_columns_sqlite(temp_db_path):
    with database._cursor() as c:
        cols = _columns(c, "life_log_entries")
    assert {"parent_id", "story_type", "why_mattered", "highlights", "extras"} <= cols


def test_life_log_entries_has_story_columns_postgres(postgres_db):
    with database._cursor() as c:
        cols = _columns(c, "life_log_entries")
    assert {"parent_id", "story_type", "why_mattered", "highlights", "extras"} <= cols
```

- [ ] **Step 2: Run the test, confirm both SQLite + Postgres tests fail**

Run: `pytest tests/test_database_stories.py -v`
Expected: SQLite test FAILS with assertion error (columns missing); Postgres test SKIPS unless `TEST_POSTGRES_URL` is set.

- [ ] **Step 3: Add the columns to Postgres `_init_postgres` migration block**

In `database.py`, find the Postgres block that runs `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (~line 248). Add a new block right after the `habits` migration:

```python
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
```

- [ ] **Step 4: Add the columns to SQLite `_init_sqlite` migration block**

In `database.py`, find the SQLite `_add_col` calls section (~line 440-460). Add:

```python
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
```

- [ ] **Step 5: Run the tests, confirm they pass on SQLite (and on Postgres if set)**

Run: `pytest tests/test_database_stories.py -v`
Expected: SQLite test PASS; Postgres test PASS or SKIP.

- [ ] **Step 6: Run twice in a row to verify idempotency**

Add a test:

```python
def test_migration_is_idempotent_sqlite(temp_db_path):
    # initialize_db was called once by the fixture; call again
    database.initialize_db()
    database.initialize_db()
    with database._cursor() as c:
        cols = _columns(c, "life_log_entries")
    assert "parent_id" in cols  # no exceptions raised, columns still present
```

Run: `pytest tests/test_database_stories.py::test_migration_is_idempotent_sqlite -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add database.py tests/test_database_stories.py
git commit -m "feat(db): add parent_id, story_type, why_mattered, highlights, extras columns

New columns on life_log_entries support story-driven proposal review.
parent_id is self-referential FK; story metadata fields hold AI-generated
+ user-enriched narrative data. Idempotent migration tested on SQLite and
(when TEST_POSTGRES_URL is set) Postgres."
```

---

## Task 3: DB story save helpers

**Files:**
- Modify: `database.py` (append after existing `save_proposal` function ~line 1149)
- Test: `tests/test_database_stories.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_database_stories.py`:

```python
def test_save_story_parent_returns_id(temp_db_path):
    sid = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip",
        summary="Vermont ski trip with Sarah and Tom",
        highlights=["JFK→BTV flight", "Skied Killington"],
        location="Killington, VT",
    )
    assert isinstance(sid, int) and sid > 0

    entry = database.get_life_log_entry(sid)
    assert entry["status"] == "proposed"
    assert entry["parent_id"] is None
    assert entry["story_type"] == "trip"
    assert entry["description"] == "Vermont ski trip with Sarah and Tom"
    assert entry["highlights"] == ["JFK→BTV flight", "Skied Killington"]
    assert entry["date_start"] == "2024-03-12"


def test_assign_child_to_story(temp_db_path):
    parent_id = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    child_id = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Skiing day",
        location="Killington", source="calendar", source_id="evt-1",
    )
    database.assign_child_to_story(child_id, parent_id)

    child = database.get_life_log_entry(child_id)
    assert child["parent_id"] == parent_id
    assert child["status"] == "proposed"
```

- [ ] **Step 2: Run the tests, confirm both fail with `AttributeError`**

Run: `pytest tests/test_database_stories.py -v -k "save_story_parent or assign_child"`
Expected: FAIL — `module 'database' has no attribute 'save_story_parent'`.

- [ ] **Step 3: Implement `save_story_parent` and `assign_child_to_story`**

Append to `database.py` (place near `save_proposal`):

```python
def save_story_parent(
    date_start: str, date_end, story_type: str,
    summary: str, highlights: list, location,
    extras: dict = None,
) -> int:
    """Insert a parent story row (status='proposed', parent_id NULL).

    Children get attached separately via assign_child_to_story.
    """
    p = _p()
    extras_val = json.dumps(extras) if extras else None
    highlights_val = json.dumps(highlights or [])
    with _cursor(write=True) as c:
        if USE_POSTGRES:
            c.execute(
                f"""INSERT INTO life_log_entries
                    (date_start, date_end, categories, description, location, status,
                     parent_id, story_type, highlights, extras, ai_proposed_at)
                    VALUES ({p},{p},{p},{p},{p},'proposed',
                            NULL,{p},{p},{p}, CURRENT_TIMESTAMP)
                    RETURNING id""",
                (date_start, date_end, "[]", summary, location,
                 story_type, highlights_val, extras_val),
            )
            return c.fetchone()["id"]
        else:
            c.execute(
                """INSERT INTO life_log_entries
                   (date_start, date_end, categories, description, location, status,
                    parent_id, story_type, highlights, extras, ai_proposed_at)
                   VALUES (?,?,?,?,?,'proposed', NULL,?,?,?, CURRENT_TIMESTAMP)""",
                (date_start, date_end, "[]", summary, location,
                 story_type, highlights_val, extras_val),
            )
            return c.lastrowid


def assign_child_to_story(child_id: int, parent_id: int):
    """Set parent_id on an existing entry, making it a child of the given story."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE life_log_entries SET parent_id={p} WHERE id={p}",
            (parent_id, child_id),
        )
```

Also update `_unpack_life_log_entry` to deserialize `highlights` and `extras`:

```python
def _unpack_life_log_entry(row):
    if row is None:
        return None
    row["categories"] = _deserialize_categories(row.get("categories"))
    raw_h = row.get("highlights")
    row["highlights"] = json.loads(raw_h) if isinstance(raw_h, str) and raw_h else []
    raw_e = row.get("extras")
    row["extras"] = json.loads(raw_e) if isinstance(raw_e, str) and raw_e else {}
    return _normalize_row_dates(row)
```

- [ ] **Step 4: Run the tests, confirm they pass on SQLite**

Run: `pytest tests/test_database_stories.py -v -k "save_story_parent or assign_child"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_stories.py
git commit -m "feat(db): save_story_parent + assign_child_to_story helpers"
```

---

## Task 4: DB story read helpers (cross-engine test)

**Files:**
- Modify: `database.py`
- Test: `tests/test_database_stories.py`

- [ ] **Step 1: Write failing test (parametrized over engines)**

Append to `tests/test_database_stories.py`:

```python
import pytest


@pytest.fixture(params=["sqlite", "postgres"])
def any_db(request, temp_db_path, postgres_db):
    """Run the test once per engine. Postgres run skips unless TEST_POSTGRES_URL set."""
    return request.param


def test_get_pending_stories_with_children_returns_parents_only(any_db):
    parent_id = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    child_id = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Ski day",
        location="Killington", source="calendar", source_id="evt-1",
    )
    database.assign_child_to_story(child_id, parent_id)
    # Also create a singleton (no children, parent_id NULL, status='proposed')
    singleton_id = database.save_story_parent(
        date_start="2024-04-01", date_end=None,
        story_type="other", summary="Random one-off", highlights=[], location=None,
    )

    stories = database.get_pending_stories_with_children()
    by_id = {s["id"]: s for s in stories}
    assert parent_id in by_id
    assert singleton_id in by_id
    assert child_id not in by_id  # children excluded from top-level list
    assert [c["id"] for c in by_id[parent_id]["children"]] == [child_id]
    assert by_id[singleton_id]["children"] == []
```

The `any_db` fixture combines both — but note pytest's `request.getfixturevalue` is needed to actually trigger the right backing fixture. Use this simpler pattern instead:

```python
def test_get_pending_stories_with_children_sqlite(temp_db_path):
    _run_pending_stories_check()


def test_get_pending_stories_with_children_postgres(postgres_db):
    _run_pending_stories_check()


def _run_pending_stories_check():
    parent_id = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    child_id = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Ski day",
        location="Killington", source="calendar", source_id="evt-1",
    )
    database.assign_child_to_story(child_id, parent_id)
    singleton_id = database.save_story_parent(
        date_start="2024-04-01", date_end=None,
        story_type="other", summary="Random", highlights=[], location=None,
    )
    stories = database.get_pending_stories_with_children()
    by_id = {s["id"]: s for s in stories}
    assert parent_id in by_id and singleton_id in by_id
    assert child_id not in by_id
    assert [c["id"] for c in by_id[parent_id]["children"]] == [child_id]
    assert by_id[singleton_id]["children"] == []
```

- [ ] **Step 2: Run, confirm SQLite test fails (Postgres SKIPs without env)**

Run: `pytest tests/test_database_stories.py -v -k "pending_stories"`
Expected: SQLite FAILs with AttributeError on `get_pending_stories_with_children`.

- [ ] **Step 3: Implement `get_pending_stories_with_children`**

Append to `database.py`:

```python
def get_pending_stories_with_children() -> list:
    """Return all pending parent stories, each with `children` list attached.

    A "pending parent" is a row with status='proposed' AND parent_id IS NULL.
    Children are rows whose parent_id == that row's id, ordered by date_start.
    """
    with _cursor() as c:
        c.execute(
            "SELECT * FROM life_log_entries "
            "WHERE status='proposed' AND parent_id IS NULL "
            "ORDER BY date_start, id"
        )
        parents = [_unpack_life_log_entry(r) for r in _rows(c.fetchall())]
        if not parents:
            return []
        parent_ids = [p["id"] for p in parents]

        # Fetch all children in one query
        if USE_POSTGRES:
            c.execute(
                "SELECT * FROM life_log_entries WHERE parent_id = ANY(%s) "
                "ORDER BY date_start, id",
                (parent_ids,),
            )
        else:
            qs = ",".join("?" for _ in parent_ids)
            c.execute(
                f"SELECT * FROM life_log_entries WHERE parent_id IN ({qs}) "
                f"ORDER BY date_start, id",
                parent_ids,
            )
        children = [_unpack_life_log_entry(r) for r in _rows(c.fetchall())]

    by_parent = {p["id"]: [] for p in parents}
    for ch in children:
        by_parent[ch["parent_id"]].append(ch)
    for p in parents:
        p["children"] = by_parent[p["id"]]
    return parents
```

- [ ] **Step 4: Run the tests, confirm SQLite passes**

Run: `pytest tests/test_database_stories.py -v -k "pending_stories"`
Expected: SQLite PASS, Postgres PASS or SKIP.

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_stories.py
git commit -m "feat(db): get_pending_stories_with_children with cross-engine test"
```

---

## Task 5: DB state transitions — confirm/dismiss/drop + metadata update

**Files:**
- Modify: `database.py`
- Test: `tests/test_database_stories.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_database_stories.py`:

```python
def test_confirm_story_flips_parent_and_children(temp_db_path):
    parent_id = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    child_id = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Ski",
        location="VT", source="calendar", source_id="evt-x",
    )
    database.assign_child_to_story(child_id, parent_id)

    database.confirm_story(parent_id)

    parent = database.get_life_log_entry(parent_id)
    child = database.get_life_log_entry(child_id)
    assert parent["status"] == "confirmed"
    assert child["status"] == "confirmed"


def test_dismiss_story_flips_parent_and_children(temp_db_path):
    parent_id = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    child_id = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Ski",
        location="VT", source="calendar", source_id="evt-y",
    )
    database.assign_child_to_story(child_id, parent_id)

    database.dismiss_story(parent_id)

    parent = database.get_life_log_entry(parent_id)
    child = database.get_life_log_entry(child_id)
    assert parent["status"] == "dismissed"
    assert child["status"] == "dismissed"


def test_drop_event_from_story_returns_child_to_unclustered(temp_db_path):
    parent_id = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    child_id = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Ski",
        location="VT", source="calendar", source_id="evt-z",
    )
    database.assign_child_to_story(child_id, parent_id)

    database.drop_event_from_story(child_id)

    child = database.get_life_log_entry(child_id)
    assert child["parent_id"] is None
    assert child["status"] == "proposed"  # back in the unclustered pool


def test_update_story_metadata_writes_all_fields(temp_db_path):
    parent_id = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=["one"], location="VT",
    )
    database.update_story_metadata(
        parent_id,
        summary="Vermont ski trip — knee surgery comeback",
        why_mattered="First trip after surgery — felt like reclaiming something.",
        highlights=["JFK→BTV flight", "Pow day at Killington"],
        extras={"travel_mode": "flight", "who_came": ["Sarah", "Tom"]},
    )
    e = database.get_life_log_entry(parent_id)
    assert "knee surgery" in e["description"]
    assert "reclaiming" in e["why_mattered"]
    assert e["highlights"] == ["JFK→BTV flight", "Pow day at Killington"]
    assert e["extras"]["travel_mode"] == "flight"
```

- [ ] **Step 2: Run, confirm all four fail with AttributeError**

Run: `pytest tests/test_database_stories.py -v -k "confirm_story or dismiss_story or drop_event or update_story_metadata"`
Expected: 4 FAILs.

- [ ] **Step 3: Implement the four helpers**

Append to `database.py`:

```python
def confirm_story(parent_id: int):
    """Flip parent + all children to status='confirmed' atomically."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE life_log_entries SET status='confirmed', "
            f"user_confirmed_at=CURRENT_TIMESTAMP "
            f"WHERE id={p} OR parent_id={p}",
            (parent_id, parent_id),
        )


def dismiss_story(parent_id: int):
    """Flip parent + all children to status='dismissed' atomically."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE life_log_entries SET status='dismissed' "
            f"WHERE id={p} OR parent_id={p}",
            (parent_id, parent_id),
        )


def drop_event_from_story(child_id: int):
    """Return a child event to the unclustered pool by nulling its parent_id."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE life_log_entries SET parent_id=NULL WHERE id={p}",
            (child_id,),
        )


def update_story_metadata(
    parent_id: int,
    summary: str = None,
    why_mattered: str = None,
    highlights: list = None,
    extras: dict = None,
):
    """Update any subset of summary/why_mattered/highlights/extras on a parent story."""
    p = _p()
    sets, params = [], []
    if summary is not None:
        sets.append(f"description={p}")
        params.append(summary)
    if why_mattered is not None:
        sets.append(f"why_mattered={p}")
        params.append(why_mattered)
    if highlights is not None:
        sets.append(f"highlights={p}")
        params.append(json.dumps(highlights))
    if extras is not None:
        sets.append(f"extras={p}")
        params.append(json.dumps(extras))
    if not sets:
        return
    params.append(parent_id)
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE life_log_entries SET {', '.join(sets)} WHERE id={p}",
            tuple(params),
        )
```

- [ ] **Step 4: Run all four tests, confirm pass**

Run: `pytest tests/test_database_stories.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_stories.py
git commit -m "feat(db): confirm_story / dismiss_story / drop_event_from_story / update_story_metadata"
```

---

## Task 6: Pre-clustering by date proximity

**Files:**
- Create: `services/story_clustering.py`
- Test: `tests/test_story_clustering.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_story_clustering.py`:

```python
"""Tests for story clustering pre-pass and helpers."""
from services.story_clustering import precluster_by_date


def _ev(id_, date_start, title=""):
    """Build a minimal event dict matching what get_pending_stories returns."""
    return {"id": id_, "date_start": date_start, "description": title,
            "title": title, "source_id": f"evt-{id_}"}


def test_precluster_groups_consecutive_dates():
    events = [
        _ev(1, "2024-03-12"),
        _ev(2, "2024-03-13"),
        _ev(3, "2024-03-14"),
    ]
    clusters = precluster_by_date(events)
    assert len(clusters) == 1
    assert [e["id"] for e in clusters[0]] == [1, 2, 3]


def test_precluster_splits_on_two_day_gap():
    events = [
        _ev(1, "2024-03-12"),
        _ev(2, "2024-03-13"),
        _ev(3, "2024-03-16"),  # gap of 3 calendar days
        _ev(4, "2024-03-17"),
    ]
    clusters = precluster_by_date(events)
    assert len(clusters) == 2
    assert [e["id"] for e in clusters[0]] == [1, 2]
    assert [e["id"] for e in clusters[1]] == [3, 4]


def test_precluster_one_day_gap_keeps_together():
    """Date diff of 1 day between two events still in same cluster (spec §clustering)."""
    events = [
        _ev(1, "2024-03-12"),
        _ev(2, "2024-03-14"),  # diff of 2 → split
    ]
    assert len(precluster_by_date(events)) == 2

    events = [
        _ev(1, "2024-03-12"),
        _ev(2, "2024-03-13"),  # diff of 1 → same group
    ]
    assert len(precluster_by_date(events)) == 1


def test_precluster_handles_same_day():
    events = [_ev(1, "2024-03-12"), _ev(2, "2024-03-12")]
    clusters = precluster_by_date(events)
    assert len(clusters) == 1


def test_precluster_empty_returns_empty():
    assert precluster_by_date([]) == []
```

- [ ] **Step 2: Run, confirm import error**

Run: `pytest tests/test_story_clustering.py -v -k "precluster"`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.story_clustering'`.

- [ ] **Step 3: Implement `precluster_by_date`**

Create `services/story_clustering.py`:

```python
"""Story clustering — pre-cluster events by date proximity, then call AI per cluster."""
import datetime
import logging

logger = logging.getLogger(__name__)


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s[:10])


def precluster_by_date(events: list, max_gap_days: int = 1) -> list:
    """Group events into clusters by date proximity.

    Two events fall in the same cluster if their `date_start` values differ by
    at most `max_gap_days` calendar days. A larger gap splits the cluster.

    Input events are expected to be dicts with a `date_start` ISO string.
    Output is a list of clusters, each cluster a list of events in date order.
    """
    if not events:
        return []
    sorted_events = sorted(events, key=lambda e: e["date_start"])
    clusters = [[sorted_events[0]]]
    for prev, curr in zip(sorted_events, sorted_events[1:]):
        gap = (_parse_date(curr["date_start"]) - _parse_date(prev["date_start"])).days
        if gap <= max_gap_days:
            clusters[-1].append(curr)
        else:
            clusters.append([curr])
    return clusters
```

- [ ] **Step 4: Run, confirm pass**

Run: `pytest tests/test_story_clustering.py -v -k "precluster"`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/story_clustering.py tests/test_story_clustering.py
git commit -m "feat(stories): date-proximity preclustering"
```

---

## Task 7: Flight detection + orphan-highlight rejection

**Files:**
- Modify: `services/story_clustering.py`
- Test: `tests/test_story_clustering.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_story_clustering.py`:

```python
from services.story_clustering import is_flight, drop_orphan_highlights


def test_is_flight_matches_common_patterns():
    assert is_flight("JFK → BTV flight")
    assert is_flight("Flight to Boston")
    assert is_flight("AA 1234 BOS-LAX")
    assert is_flight("United UA456 to SFO")


def test_is_flight_rejects_non_flights():
    assert not is_flight("Skiing at Killington")
    assert not is_flight("Dinner with Sarah")
    assert not is_flight("Onboarding meeting")


def test_drop_orphan_highlights_removes_unreferenced():
    cluster_event_ids = {1, 2, 3}
    candidate = {
        "highlights": ["A", "B", "C"],
        "event_id_refs": [1, 999, 2],  # 999 is orphan
    }
    out = drop_orphan_highlights(candidate, cluster_event_ids)
    assert out["highlights"] == ["A", "C"]
    assert out["event_id_refs"] == [1, 2]


def test_drop_orphan_highlights_handles_missing_refs():
    """If event_id_refs is shorter/longer than highlights, align by index."""
    cluster_event_ids = {1, 2}
    candidate = {
        "highlights": ["A", "B", "C"],
        "event_id_refs": [1, 2],  # only 2 refs for 3 highlights
    }
    out = drop_orphan_highlights(candidate, cluster_event_ids)
    # Highlight without a ref is dropped (we can't validate it).
    assert out["highlights"] == ["A", "B"]
    assert out["event_id_refs"] == [1, 2]
```

- [ ] **Step 2: Run, confirm fails with ImportError**

Run: `pytest tests/test_story_clustering.py -v -k "flight or orphan"`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement both helpers**

Append to `services/story_clustering.py`:

```python
import re

_FLIGHT_RE = re.compile(
    r"\b(flight|fly|flying|flew|airline)\b"
    r"|\b[A-Z]{3}\s*[-→]\s*[A-Z]{3}\b"      # IATA code dash IATA code
    r"|\b(?:AA|UA|DL|BA|AC|JB|WN|NK|F9)\s*\d{1,4}\b",  # carrier code + flight num
    re.IGNORECASE,
)


def is_flight(title: str) -> bool:
    """True if the event title looks like a flight."""
    return bool(title and _FLIGHT_RE.search(title))


def drop_orphan_highlights(candidate: dict, cluster_event_ids: set) -> dict:
    """Drop highlights whose event_id_refs aren't in the cluster.

    `candidate` is the AI's output for one cluster:
      {"highlights": [...], "event_id_refs": [...]}
    Highlights and refs are aligned by index; if there are more highlights than
    refs, the extras are dropped (we cannot validate them).
    """
    highlights = candidate.get("highlights") or []
    refs = candidate.get("event_id_refs") or []
    pairs = list(zip(highlights, refs))  # truncates to shorter
    kept = [(h, r) for h, r in pairs if r in cluster_event_ids]
    if len(kept) < len(pairs):
        logger.warning(
            "Dropped %d orphan highlight(s) referencing events not in cluster",
            len(pairs) - len(kept),
        )
    out = dict(candidate)
    out["highlights"] = [h for h, _ in kept]
    out["event_id_refs"] = [r for _, r in kept]
    return out
```

- [ ] **Step 4: Run, confirm pass**

Run: `pytest tests/test_story_clustering.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/story_clustering.py tests/test_story_clustering.py
git commit -m "feat(stories): flight detection + orphan-highlight rejection"
```

---

## Task 8: AI cluster_into_story call

**Files:**
- Modify: `ai_life_log.py`
- Test: `tests/test_story_clustering.py` (extend with mocked AI)

- [ ] **Step 1: Write failing test**

Append to `tests/test_story_clustering.py`:

```python
import json
from unittest.mock import MagicMock


def test_cluster_into_story_returns_expected_shape(mock_anthropic):
    from ai_life_log import cluster_into_story

    payload = {
        "story_type": "trip",
        "summary": "Vermont ski trip with Sarah and Tom",
        "highlights": ["JFK→BTV flight", "Skied Killington"],
        "event_id_refs": [1, 2],
        "suggested_extras_questions": [
            "What was the mode of travel?",
            "Who came on the trip?",
        ],
        "location": "Killington, VT",
    }
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text=json.dumps(payload))
    ]

    events = [
        {"id": 1, "date_start": "2024-03-12", "title": "JFK->BTV flight",
         "description": "", "location": "", "source_id": "e1"},
        {"id": 2, "date_start": "2024-03-13", "title": "Skiing Killington",
         "description": "", "location": "Killington, VT", "source_id": "e2"},
    ]
    out = cluster_into_story(events, active_categories=["Vacation", "Skiing"])
    assert out["story_type"] == "trip"
    assert out["summary"].startswith("Vermont")
    assert out["event_id_refs"] == [1, 2]


def test_cluster_into_story_falls_back_to_singletons_on_parse_failure(mock_anthropic):
    from ai_life_log import cluster_into_story

    mock_anthropic.messages.create.return_value.content = [MagicMock(text="not json")]

    events = [
        {"id": 1, "date_start": "2024-03-12", "title": "Random event",
         "description": "", "location": "", "source_id": "e1"},
    ]
    out = cluster_into_story(events, active_categories=[])
    # Fall-through default: story_type="other", singleton-ish
    assert out["story_type"] == "other"
    assert out["event_id_refs"] == [1]
```

- [ ] **Step 2: Run, confirm fails — no `cluster_into_story` yet**

Run: `pytest tests/test_story_clustering.py -v -k "cluster_into_story"`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implement `cluster_into_story`**

Append to `ai_life_log.py`:

```python
SEEDED_STORY_TYPES = (
    "trip", "interview_cycle", "conference", "holiday_weekend",
    "dating_arc", "project_milestone", "family_visit", "other",
)


def cluster_into_story(events: list, active_categories: list) -> dict:
    """Classify a date-proximity cluster into a story candidate.

    Input events are dicts with id, date_start, title, description, location.
    Returns:
        {
          "story_type": str,
          "summary": str,
          "highlights": [str],          # ordered, aligned with event_id_refs
          "event_id_refs": [int],       # which child event each highlight came from
          "suggested_extras_questions": [str],
          "location": str,
        }

    On AI parse failure, returns a "other"-type singleton story spanning all
    input events (no highlights), so the caller can still proceed.
    """
    types_str = ", ".join(SEEDED_STORY_TYPES)
    cats_str = ", ".join(active_categories)
    events_str = "\n".join(
        f"  - id={e['id']} | {e['date_start']} | "
        f"{e.get('title') or e.get('description') or '(no title)'} | "
        f"loc={e.get('location') or '(none)'}"
        for e in events
    )

    prompt = f"""You are classifying a tightly-clustered group of calendar events
as a single "story" for a personal Life Log (a 30-year memoir).

Seeded story types (you may invent a new type if none of these fit, but prefer these):
{types_str}

Active Life Log categories: {cats_str}

Events in this cluster:
{events_str}

Return ONLY a JSON object — no markdown fences:
{{
  "story_type": "one of the seeded types or a new lowercase_with_underscores name",
  "summary": "one-line memoir-style summary, 5-15 words",
  "highlights": ["short bullet 1", "short bullet 2", ...],
  "event_id_refs": [1, 2, ...],
  "suggested_extras_questions": ["question to capture trip details", ...],
  "location": "primary location or empty string"
}}

CRITICAL rules:
- highlights[i] MUST describe event with id=event_id_refs[i]. Same length, aligned by index.
- Do NOT invent details not present in the event titles or descriptions.
- If an event title looks like a flight (e.g. "JFK->BTV", "AA 1234"), include it as a highlight.
- Keep summary tight (memoir-style, not the raw calendar titles).
- 1-3 suggested_extras_questions, type-specific (e.g. for trip: travel mode, who came; for interview_cycle: outcome, role).
"""

    cluster_event_ids = [e["id"] for e in events]
    fallback = {
        "story_type": "other",
        "summary": (events[0].get("title") or events[0].get("description") or "(untitled)")
                   if events else "(empty)",
        "highlights": [],
        "event_id_refs": cluster_event_ids,
        "suggested_extras_questions": [],
        "location": (events[0].get("location") or "") if events else "",
    }
    return _call_json(prompt, max_tokens=800, default=fallback)
```

- [ ] **Step 4: Run, confirm pass**

Run: `pytest tests/test_story_clustering.py -v -k "cluster_into_story"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai_life_log.py tests/test_story_clustering.py
git commit -m "feat(ai): cluster_into_story Claude call with event_id_refs grounding"
```

---

## Task 9: End-to-end clustering pipeline + parse_extras_answer

**Files:**
- Modify: `services/story_clustering.py`, `ai_life_log.py`
- Test: `tests/test_story_clustering.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_story_clustering.py`:

```python
def test_run_clustering_pipeline_writes_parents_and_assigns_children(
    temp_db_path, mock_anthropic
):
    import database
    from services.story_clustering import run_clustering

    # Two events 2 days apart -> separate clusters; AI returns story per cluster
    e1 = database.save_proposal(
        date_start="2024-03-12", date_end=None,
        categories=["Vacation"], description="Vermont arrival",
        location="VT", source="calendar", source_id="evt-1",
    )
    e2 = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Skiing Killington",
        location="VT", source="calendar", source_id="evt-2",
    )
    e3 = database.save_proposal(
        date_start="2024-04-15", date_end=None,
        categories=["Concert"], description="Phish at MSG",
        location="NYC", source="calendar", source_id="evt-3",
    )

    # Configure mock_anthropic to return a different shape for each call
    responses = [
        # Cluster 1 (e1 + e2)
        json.dumps({
            "story_type": "trip", "summary": "Vermont weekend",
            "highlights": ["Vermont arrival", "Skied Killington"],
            "event_id_refs": [e1, e2],
            "suggested_extras_questions": ["mode of travel?"],
            "location": "VT",
        }),
        # Cluster 2 (e3)
        json.dumps({
            "story_type": "other", "summary": "Phish at MSG",
            "highlights": ["Phish at MSG"], "event_id_refs": [e3],
            "suggested_extras_questions": [], "location": "NYC",
        }),
    ]
    iter_responses = iter(responses)
    def _next(*a, **kw):
        m = MagicMock()
        m.content = [MagicMock(text=next(iter_responses))]
        return m
    mock_anthropic.messages.create.side_effect = _next

    n_stories = run_clustering()
    assert n_stories == 2

    stories = database.get_pending_stories_with_children()
    assert len(stories) == 2
    by_type = {s["story_type"]: s for s in stories}
    assert "trip" in by_type and "other" in by_type
    assert {c["id"] for c in by_type["trip"]["children"]} == {e1, e2}
    assert {c["id"] for c in by_type["other"]["children"]} == {e3}
```

- [ ] **Step 2: Run, confirm fails — `run_clustering` not implemented**

Run: `pytest tests/test_story_clustering.py::test_run_clustering_pipeline_writes_parents_and_assigns_children -v`
Expected: FAIL.

- [ ] **Step 3: Implement `run_clustering`**

Append to `services/story_clustering.py`:

```python
import database
from ai_life_log import cluster_into_story


def run_clustering() -> int:
    """Run a full clustering pass over all currently-pending un-assigned events.

    For every event with status='proposed' AND parent_id IS NULL, assemble
    date-proximity clusters, classify each via Claude, persist parents + assign
    children. Returns the number of parent stories created.

    Re-running is safe: events already attached to a parent (parent_id NOT NULL)
    are excluded; no parent is re-created for the same group.
    """
    with database._cursor() as c:
        c.execute(
            "SELECT * FROM life_log_entries "
            "WHERE status='proposed' AND parent_id IS NULL "
            "AND story_type IS NULL "  # exclude existing parents
            "ORDER BY date_start, id"
        )
        rows = database._rows(c.fetchall())
    events = [database._unpack_life_log_entry(r) for r in rows]
    if not events:
        return 0

    # Pre-cluster, then AI-classify each
    clusters = precluster_by_date(events)
    active = [cat["name"] for cat in database.get_active_categories()]
    n_parents = 0

    for cluster in clusters:
        # Hand the AI a normalized view (id, date_start, title, description, location)
        ai_input = [
            {"id": e["id"], "date_start": e["date_start"],
             "title": e.get("description") or "",
             "description": e.get("description") or "",
             "location": e.get("location") or ""}
            for e in cluster
        ]
        candidate = cluster_into_story(ai_input, active_categories=active)
        cluster_ids = {e["id"] for e in cluster}
        candidate = drop_orphan_highlights(candidate, cluster_ids)

        # Persist parent
        date_start = min(e["date_start"] for e in cluster)
        date_end = max(e["date_start"] for e in cluster)
        if date_end == date_start:
            date_end = None
        parent_id = database.save_story_parent(
            date_start=date_start, date_end=date_end,
            story_type=candidate.get("story_type") or "other",
            summary=candidate.get("summary") or "(untitled)",
            highlights=candidate.get("highlights") or [],
            location=candidate.get("location") or None,
            extras={
                "_suggested_extras_questions":
                    candidate.get("suggested_extras_questions") or []
            },
        )
        # Attach children
        for e in cluster:
            database.assign_child_to_story(e["id"], parent_id)
        n_parents += 1

    logger.info("run_clustering: created %d parent stories", n_parents)
    return n_parents
```

- [ ] **Step 4: Add `parse_extras_answer` in `ai_life_log.py`**

Append to `ai_life_log.py`:

```python
def parse_extras_answer(story_type: str, question: str, answer: str) -> dict:
    """Turn a free-text answer into a small dict that fits the story_type's extras schema.

    The schema varies by type; this is a soft contract — the caller merges the
    returned dict into the story's existing `extras`. Returns {} on parse failure.
    """
    prompt = f"""You are extracting structured data from a one-sentence answer
in a Telegram chat about a personal Life Log entry.

Story type: {story_type}
Question we asked: "{question}"
User's answer: "{answer}"

Return ONLY a JSON object — no markdown fences. Use lowercase_with_underscore keys.
Examples per story type:

  trip: {{"travel_mode": "flight"}}, {{"who_came": ["Sarah", "Tom"]}}, {{"most_memorable": "..."}}
  interview_cycle: {{"outcome": "no_offer"}}, {{"role": "PM"}}, {{"company": "Acme"}}, {{"rounds": 4}}
  conference: {{"event_name": "..."}}, {{"role": "speaker"}}, {{"who_with": [...]}}
  holiday_weekend: {{"host": "Mom"}}, {{"key_meal": "..."}}, {{"who_with": [...]}}
  dating_arc: {{"partner": "..."}}, {{"started_or_ended": "start"}}, {{"notes": "..."}}
  project_milestone: {{"project": "..."}}, {{"milestone": "..."}}, {{"outcome": "..."}}
  family_visit: {{"host": "..."}}, {{"who_with": [...]}}, {{"occasion": "..."}}
  other: {{"notes": "..."}}

Pick ONE key/value that best captures the answer. Return {{}} if the answer
doesn't add structured information."""
    return _call_json(prompt, max_tokens=200, default={})
```

- [ ] **Step 5: Run all tests**

Run: `pytest tests/test_story_clustering.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add services/story_clustering.py ai_life_log.py tests/test_story_clustering.py
git commit -m "feat(stories): run_clustering pipeline + parse_extras_answer"
```

---

## Task 10: Sheet rendering — sync_stories_to_sheet

**Files:**
- Modify: `google_sheets.py`
- Test: `tests/test_google_sheets_stories.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/test_google_sheets_stories.py`:

```python
"""Tests for the Stories tab rendering."""
from unittest.mock import MagicMock, patch


def test_build_stories_rows_emits_parent_then_children():
    from google_sheets import _build_stories_rows

    stories = [
        {"id": 100, "story_type": "trip", "date_start": "2024-03-12",
         "date_end": "2024-03-17", "description": "Vermont ski trip",
         "highlights": ["JFK→BTV flight", "Skied Killington"],
         "children": [
             {"id": 101, "date_start": "2024-03-12", "description": "JFK→BTV flight"},
             {"id": 102, "date_start": "2024-03-13", "description": "Skiing"},
         ]},
        {"id": 200, "story_type": "other", "date_start": "2024-04-01",
         "date_end": None, "description": "Phish at MSG",
         "highlights": [],
         "children": []},
    ]
    rows = _build_stories_rows(stories)
    # Row 0 = header; row 1 = parent #100; row 2 = child 101; row 3 = child 102
    # row 4 = parent #200 (singleton, no children)
    assert rows[0][0] == "Type"  # header sentinel
    parent_row = rows[1]
    assert parent_row[0] == "trip"
    assert "Vermont" in parent_row[3]
    assert parent_row[4] == "2"  # # events
    assert parent_row[5] == "100"  # parent id
    assert rows[2][3].startswith("  └")  # indent marker on child desc
    assert rows[4][0] == "other"
    assert rows[4][4] == "0"  # singleton has 0 children


def test_sync_stories_to_sheet_clears_and_writes():
    from google_sheets import sync_stories_to_sheet
    fake_sheet = MagicMock()
    fake_spreadsheet = MagicMock()
    fake_spreadsheet.worksheet.return_value = fake_sheet
    fake_spreadsheet.url = "https://example/sheet"

    with patch("google_sheets._get_spreadsheet", return_value=fake_spreadsheet), \
         patch("google_sheets._ensure_sheets"):
        url = sync_stories_to_sheet([
            {"id": 1, "story_type": "trip", "date_start": "2024-03-12",
             "date_end": "2024-03-17", "description": "Trip",
             "highlights": [], "children": []},
        ])
    fake_sheet.clear.assert_called_once()
    fake_sheet.update.assert_called()
    assert url == "https://example/sheet"
```

- [ ] **Step 2: Run, confirm fails — functions don't exist**

Run: `pytest tests/test_google_sheets_stories.py -v`
Expected: FAILs — ImportError on `_build_stories_rows`.

- [ ] **Step 3: Implement in `google_sheets.py`**

Append to `google_sheets.py`:

```python
SHEET_STORIES = "Stories"

_STORIES_HEADER = [
    "Type", "Date Range", "Summary", "Description", "# Events", "ID", "Decision",
]


def _build_stories_rows(stories: list) -> list:
    """Build sheet rows: parent row, then indented child rows; flat header at top."""
    rows = [_STORIES_HEADER]
    for s in stories:
        date_range = s.get("date_start") or ""
        if s.get("date_end"):
            date_range = f"{s['date_start']} → {s['date_end']}"
        children = s.get("children") or []
        rows.append([
            s.get("story_type") or "other",
            date_range,
            s.get("description") or "",
            "; ".join(s.get("highlights") or []),
            str(len(children)),
            str(s.get("id", "")),
            "",  # Decision — user fills
        ])
        for ch in children:
            rows.append([
                "",  # type blank on child
                ch.get("date_start") or "",
                "",
                f"  └ {ch.get('description') or ''}",
                "",
                str(ch.get("id", "")),
                "",  # Decision blank on child
            ])
    return rows


def sync_stories_to_sheet(stories: list) -> str:
    """Full rebuild of the Stories tab."""
    spreadsheet = _get_spreadsheet()
    _ensure_sheets(spreadsheet)
    if SHEET_STORIES not in {ws.title for ws in spreadsheet.worksheets()}:
        spreadsheet.add_worksheet(title=SHEET_STORIES, rows=5000, cols=7)
    sheet = spreadsheet.worksheet(SHEET_STORIES)
    rows = _build_stories_rows(stories)
    sheet.clear()
    if rows:
        sheet.update("A1", rows)
    logger.info("Rebuilt Stories sheet (%d stories)", len(stories))
    return spreadsheet.url
```

- [ ] **Step 4: Run, confirm pass**

Run: `pytest tests/test_google_sheets_stories.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add google_sheets.py tests/test_google_sheets_stories.py
git commit -m "feat(sheets): sync_stories_to_sheet renders parent+children cards"
```

---

## Task 11: Sheet read — read_story_decisions

**Files:**
- Modify: `google_sheets.py`
- Test: `tests/test_google_sheets_stories.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_google_sheets_stories.py`:

```python
def test_read_story_decisions_only_picks_parent_rows():
    from google_sheets import read_story_decisions
    fake_sheet = MagicMock()
    # Row 0 header, row 1 parent (decision yes), row 2 child (ignored even
    # if user wrote in decision col), row 3 parent (decision skip)
    fake_sheet.get_all_values.return_value = [
        ["Type", "Date Range", "Summary", "Description", "# Events", "ID", "Decision"],
        ["trip", "2024-03-12 → 2024-03-17", "Vermont", "...", "2", "100", "yes"],
        ["", "2024-03-12", "", "  └ flight", "", "101", "yes"],   # child — ignore
        ["other", "2024-04-01", "Phish", "", "0", "200", "skip"],
    ]
    fake_spreadsheet = MagicMock()
    fake_spreadsheet.worksheet.return_value = fake_sheet
    with patch("google_sheets._get_spreadsheet", return_value=fake_spreadsheet), \
         patch("google_sheets._ensure_sheets"):
        decisions = read_story_decisions()
    assert decisions == [
        {"id": 100, "decision": "confirm"},
        {"id": 200, "decision": "skip"},
    ]
```

- [ ] **Step 2: Run, confirm fails**

Run: `pytest tests/test_google_sheets_stories.py::test_read_story_decisions_only_picks_parent_rows -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implement `read_story_decisions`**

Append to `google_sheets.py`:

```python
def read_story_decisions() -> list:
    """Return parent-row decisions from the Stories sheet.

    A row is a parent if its Type column (col 0) is non-empty. Child rows
    have an empty Type and are ignored even if the user wrote in their
    Decision column.

    Returns: [{"id": int, "decision": "confirm" | "skip"}]
    Anything other than yes/y/confirm/skip/n/no/dismiss in the Decision col is
    ignored (the row stays pending).
    """
    spreadsheet = _get_spreadsheet()
    _ensure_sheets(spreadsheet)
    sheet = spreadsheet.worksheet(SHEET_STORIES)
    all_rows = sheet.get_all_values()
    if len(all_rows) < 2:
        return []

    out = []
    for row in all_rows[1:]:  # skip header
        if len(row) < 7:
            continue
        type_cell = row[0].strip()
        if not type_cell:
            continue  # child row — ignore
        id_cell, decision_cell = row[5].strip(), row[6].strip()
        if not id_cell or not decision_cell:
            continue
        try:
            entry_id = int(id_cell)
        except ValueError:
            continue
        lc = decision_cell.lower()
        if lc in ("yes", "y", "confirm", "ok"):
            out.append({"id": entry_id, "decision": "confirm"})
        elif lc in ("skip", "n", "no", "dismiss"):
            out.append({"id": entry_id, "decision": "skip"})
    return out
```

- [ ] **Step 4: Run, confirm pass**

Run: `pytest tests/test_google_sheets_stories.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add google_sheets.py tests/test_google_sheets_stories.py
git commit -m "feat(sheets): read_story_decisions parses parent decision column"
```

---

## Task 12: /buildstories handler

**Files:**
- Create: `handlers/buildstories.py`
- Test: `tests/test_buildstories_handler.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/test_buildstories_handler.py`:

```python
"""Tests for /buildstories command handler."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_buildstories_runs_clustering_and_pushes_sheet(
    temp_db_path, mock_anthropic
):
    import database, json
    from handlers.buildstories import buildstories_command

    e1 = database.save_proposal(
        date_start="2024-03-12", date_end=None,
        categories=["Vacation"], description="Vermont arrival",
        location="VT", source="calendar", source_id="evt-1",
    )
    e2 = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Skiing",
        location="VT", source="calendar", source_id="evt-2",
    )

    mock_anthropic.messages.create.return_value.content = [MagicMock(text=json.dumps({
        "story_type": "trip", "summary": "Vermont weekend",
        "highlights": ["Vermont arrival", "Skiing"], "event_id_refs": [e1, e2],
        "suggested_extras_questions": [], "location": "VT",
    }))]

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = []

    with patch("handlers.buildstories.sync_stories_to_sheet",
               return_value="https://example/sheet"):
        await buildstories_command(update, context)

    # Two messages expected: "running…" then "done"
    assert update.message.reply_text.call_count >= 2
    last_msg = update.message.reply_text.call_args_list[-1].args[0]
    assert "1" in last_msg  # one parent story created
```

- [ ] **Step 2: Run, confirm ImportError**

Run: `pytest tests/test_buildstories_handler.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the handler**

Create `handlers/buildstories.py`:

```python
"""Handler for /buildstories — cluster pending proposals and push to Sheet."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db
from services.story_clustering import run_clustering
from google_sheets import sync_stories_to_sheet

logger = logging.getLogger(__name__)


async def buildstories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cluster all currently-pending un-assigned events into stories.

    Idempotent: re-running picks up only events that aren't already attached
    to a parent story.
    """
    pending_count_before = len(db.get_pending_stories_with_children())
    await update.message.reply_text(
        "📚 Building stories from pending events… this may take a few minutes."
    )

    try:
        n_new = run_clustering()
    except Exception as e:
        logger.error("run_clustering failed: %s", e, exc_info=True)
        await update.message.reply_text(
            f"❌ Clustering failed.\n\n`{type(e).__name__}: {e}`",
            parse_mode="Markdown",
        )
        return

    stories = db.get_pending_stories_with_children()
    sheet_url = ""
    sheet_error = ""
    try:
        sheet_url = sync_stories_to_sheet(stories)
    except Exception as e:
        logger.error("sync_stories_to_sheet failed: %s", e, exc_info=True)
        sheet_error = f"{type(e).__name__}: {e}"

    msg = (
        f"✅ Built {n_new} new stories.\n"
        f"📊 {len(stories)} total stories pending in your Sheet.\n"
    )
    if sheet_url:
        msg += (
            f"\n📝 [Open Stories tab]({sheet_url})\n\n"
            f"Mark each story `yes` or `skip` in the Decision column, "
            f"then run /syncstories."
        )
    elif sheet_error:
        msg += (
            f"\n⚠️ Sheet write failed: `{sheet_error}`\n"
            f"Stories are saved in the DB. Run /pushstories to retry."
        )
    await update.message.reply_text(msg, parse_mode="Markdown")
```

- [ ] **Step 4: Run, confirm pass**

Run: `pytest tests/test_buildstories_handler.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add handlers/buildstories.py tests/test_buildstories_handler.py
git commit -m "feat(handlers): /buildstories cluster + sheet push command"
```

---

## Task 13: /syncstories handler + queue setup

**Files:**
- Create: `handlers/syncstories.py`
- Test: `tests/test_syncstories_handler.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/test_syncstories_handler.py`:

```python
"""Tests for /syncstories — apply sheet decisions, enqueue survivors."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_syncstories_dismisses_skips_and_enqueues_yeses(temp_db_path):
    import database
    from handlers.syncstories import syncstories_command

    s1 = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    s2 = database.save_story_parent(
        date_start="2024-04-01", date_end=None,
        story_type="other", summary="Phish", highlights=[], location=None,
    )

    decisions = [
        {"id": s1, "decision": "confirm"},  # survives → enqueued
        {"id": s2, "decision": "skip"},      # dismissed
    ]

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    with patch("handlers.syncstories.read_story_decisions", return_value=decisions):
        await syncstories_command(update, context)

    # s2 should be dismissed
    assert database.get_life_log_entry(s2)["status"] == "dismissed"
    # s1 should still be 'proposed' (it'll flip to confirmed inside Telegram review)
    assert database.get_life_log_entry(s1)["status"] == "proposed"

    # Queue contains s1 only
    state = database.get_state()
    temp = state.get("temp_data") or {}
    if isinstance(temp, str):
        import json
        temp = json.loads(temp)
    assert s1 in temp.get("pending_story_ids", [])
    assert s2 not in temp.get("pending_story_ids", [])
```

- [ ] **Step 2: Run, confirm ImportError**

Run: `pytest tests/test_syncstories_handler.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `syncstories_command`**

Create `handlers/syncstories.py`:

```python
"""Handler for /syncstories — apply sheet decisions and start Telegram review."""
import json
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db
from google_sheets import read_story_decisions

logger = logging.getLogger(__name__)


async def syncstories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Read the Stories sheet, dismiss skips, enqueue confirms for Telegram review."""
    await update.message.reply_text("📋 Reading Stories tab from Google Sheets…")
    try:
        decisions = read_story_decisions()
    except Exception as e:
        logger.error("read_story_decisions failed: %s", e, exc_info=True)
        await update.message.reply_text(f"❌ Couldn't read sheet: {e}")
        return

    if not decisions:
        await update.message.reply_text(
            "No decisions to apply. Open the *Stories* tab, mark each story "
            "`yes` or `skip`, then run /syncstories.",
            parse_mode="Markdown",
        )
        return

    survivors, dismissed, missing = [], 0, 0
    for d in decisions:
        entry = db.get_life_log_entry(d["id"])
        if entry is None or entry["status"] != "proposed" or entry["parent_id"] is not None:
            missing += 1
            continue
        if d["decision"] == "skip":
            db.dismiss_story(d["id"])
            dismissed += 1
        elif d["decision"] == "confirm":
            survivors.append(d["id"])

    # Save the survivor queue into conversation_state.temp_data
    state = db.get_state()
    temp = state.get("temp_data") or {}
    if isinstance(temp, str):
        try:
            temp = json.loads(temp)
        except Exception:
            temp = {}
    temp["pending_story_ids"] = survivors
    temp["current_story_id"] = None  # main loop sets this when it picks one
    db.set_state(
        state="story_confirming" if survivors else "idle",
        temp_data=temp,
    )

    await update.message.reply_text(
        f"✅ Decisions applied:\n"
        f"• {len(survivors)} stories surviving — review in Telegram next\n"
        f"• {dismissed} dismissed\n"
        f"• {missing} skipped (already handled or no longer pending)\n"
    )
    if survivors:
        # Trigger first story review — the dispatcher will pick it up
        from handlers.story_review import present_next_story
        await present_next_story(update.message.reply_text)
```

- [ ] **Step 4: Run, confirm pass**

Note: this will fail until Task 14 implements `present_next_story`. Mock it for now:

Update Step 1's test to patch `present_next_story`:

```python
    with patch("handlers.syncstories.read_story_decisions", return_value=decisions), \
         patch("handlers.syncstories.present_next_story", new=AsyncMock()):
        await syncstories_command(update, context)
```

Run: `pytest tests/test_syncstories_handler.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add handlers/syncstories.py tests/test_syncstories_handler.py
git commit -m "feat(handlers): /syncstories applies decisions + enqueues survivors"
```

---

## Task 14: Story review state machine — confirming + why_mattered

**Files:**
- Create: `handlers/story_review.py`
- Test: `tests/test_story_review_handler.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/test_story_review_handler.py`:

```python
"""Tests for the Telegram narrative state machine."""
from unittest.mock import AsyncMock, MagicMock, patch
import json
import pytest


def _setup_queue(database, story_ids: list, current_id=None):
    temp = {"pending_story_ids": story_ids, "current_story_id": current_id}
    database.set_state(state="story_confirming", temp_data=temp)


@pytest.mark.asyncio
async def test_present_next_story_sends_narrative_card(temp_db_path):
    import database
    from handlers.story_review import present_next_story

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-17",
        story_type="trip", summary="Vermont ski trip",
        highlights=["JFK→BTV flight", "Skied Killington"], location="VT",
    )
    cid = database.save_proposal(
        date_start="2024-03-12", date_end=None,
        categories=["Vacation"], description="JFK→BTV flight",
        location="VT", source="calendar", source_id="e1",
    )
    database.assign_child_to_story(cid, sid)
    _setup_queue(database, [sid])

    reply = AsyncMock()
    await present_next_story(reply)
    reply.assert_called_once()
    msg = reply.call_args.args[0]
    assert "TRIP" in msg.upper()
    assert "Vermont" in msg
    assert "#1" in msg  # numbered events list
    state = database.get_state()
    temp = state["temp_data"]
    if isinstance(temp, str):
        temp = json.loads(temp)
    assert temp["current_story_id"] == sid
    assert state["state"] == "story_confirming"


@pytest.mark.asyncio
async def test_handle_confirming_yes_advances_to_why_mattered(temp_db_path):
    import database
    from handlers.story_review import handle_story_confirming

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="Vermont", highlights=[], location="VT",
    )
    _setup_queue(database, [sid], current_id=sid)

    reply = AsyncMock()
    await handle_story_confirming("yes", reply)

    state = database.get_state()
    assert state["state"] == "story_why_mattered"
    reply.assert_called_with("Why did this matter? (one sentence)")


@pytest.mark.asyncio
async def test_handle_confirming_skip_dismisses_and_advances(temp_db_path):
    import database
    from handlers.story_review import handle_story_confirming

    sid1 = database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="A", highlights=[], location=None,
    )
    sid2 = database.save_story_parent(
        date_start="2024-04-01", date_end=None,
        story_type="other", summary="B", highlights=[], location=None,
    )
    _setup_queue(database, [sid1, sid2], current_id=sid1)

    reply = AsyncMock()
    await handle_story_confirming("skip", reply)

    assert database.get_life_log_entry(sid1)["status"] == "dismissed"
    state = database.get_state()
    temp = state["temp_data"]
    if isinstance(temp, str):
        temp = json.loads(temp)
    assert temp["current_story_id"] == sid2  # advanced to next
    assert state["state"] == "story_confirming"


@pytest.mark.asyncio
async def test_handle_why_mattered_records_text_and_advances_to_extras_optin(
    temp_db_path
):
    import database
    from handlers.story_review import handle_story_why_mattered

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="Vermont", highlights=[], location=None,
        extras={"_suggested_extras_questions": ["mode of travel?"]},
    )
    _setup_queue(database, [sid], current_id=sid)
    database.set_state(state="story_why_mattered",
                       temp_data={"pending_story_ids": [sid], "current_story_id": sid})

    reply = AsyncMock()
    await handle_story_why_mattered(
        "First trip after my surgery — meant a lot.", reply
    )

    e = database.get_life_log_entry(sid)
    assert "surgery" in e["why_mattered"]
    state = database.get_state()
    assert state["state"] == "story_extras_optin"
```

- [ ] **Step 2: Run, confirm fails — handlers don't exist**

Run: `pytest tests/test_story_review_handler.py -v -k "present_next or confirming or why_mattered"`
Expected: 4 FAILs.

- [ ] **Step 3: Implement `handlers/story_review.py` (confirming + why_mattered)**

Create `handlers/story_review.py`:

```python
"""Telegram narrative state machine for story review."""
import json
import logging
from typing import Awaitable, Callable

import database as db

logger = logging.getLogger(__name__)


def _temp(state: dict) -> dict:
    t = state.get("temp_data") or {}
    if isinstance(t, str):
        try:
            t = json.loads(t)
        except Exception:
            t = {}
    return t


def _advance_or_finish(reply, finish_msg="✅ All stories reviewed."):
    """Pop the next story off the queue and present it, or finish."""
    state = db.get_state()
    temp = _temp(state)
    queue = temp.get("pending_story_ids") or []
    if queue:
        temp["current_story_id"] = queue[0]
        temp["pending_story_ids"] = queue[1:]
        db.set_state(state="story_confirming", temp_data=temp)
        return _render_story(temp["current_story_id"])
    db.set_state(state="idle", temp_data={})
    return finish_msg


def _render_story(parent_id: int) -> str:
    """Render a story's narrative card with numbered events for `drop #N`."""
    parent = db.get_life_log_entry(parent_id)
    children = sorted(
        (db.get_life_log_entry(c["id"]) for c in
         _children_of(parent_id)),
        key=lambda c: c["date_start"],
    )
    type_label = (parent.get("story_type") or "other").replace("_", " ").upper()
    date_range = parent.get("date_start") or ""
    if parent.get("date_end"):
        date_range = f"{parent['date_start']} → {parent['date_end']}"
    highlights = parent.get("highlights") or []
    h_block = "\n".join(f"• {h}" for h in highlights[:8]) or "(none)"
    if len(highlights) > 8:
        h_block += f"\n…and {len(highlights) - 8} more"
    events_block = "\n".join(
        f"  #{i+1}  {c['date_start']}  {c['description']}"
        for i, c in enumerate(children)
    ) or "(no child events)"

    return (
        f"📖 {type_label}\n\n"
        f"{parent.get('description') or '(no summary)'}\n"
        f"{date_range} · {len(children)} events\n\n"
        f"Highlights:\n{h_block}\n\n"
        f"Events (drop by number):\n{events_block}\n\n"
        f"Reply: yes / edit summary: <text> / drop #N / skip"
    )


def _children_of(parent_id: int) -> list:
    p = db._p()
    with db._cursor() as c:
        c.execute(
            f"SELECT id FROM life_log_entries WHERE parent_id={p} "
            f"ORDER BY date_start, id",
            (parent_id,),
        )
        return db._rows(c.fetchall())


async def present_next_story(reply: Callable[[str], Awaitable]):
    """Pop one story off the queue and send its narrative card."""
    state = db.get_state()
    temp = _temp(state)
    queue = temp.get("pending_story_ids") or []
    if not queue:
        await reply("✅ All stories reviewed.")
        db.set_state(state="idle", temp_data={})
        return
    parent_id = queue[0]
    temp["current_story_id"] = parent_id
    temp["pending_story_ids"] = queue[1:]
    db.set_state(state="story_confirming", temp_data=temp)
    await reply(_render_story(parent_id))


async def handle_story_confirming(text: str, reply):
    """User reply during the story_confirming state."""
    text_l = (text or "").strip().lower()
    state = db.get_state()
    temp = _temp(state)
    sid = temp.get("current_story_id")
    if not sid:
        await reply("No active story. Run /syncstories to start.")
        return

    if text_l == "yes":
        db.set_state(state="story_why_mattered", temp_data=temp)
        await reply("Why did this matter? (one sentence)")
        return

    if text_l == "skip":
        db.dismiss_story(sid)
        next_msg = _advance_or_finish(reply)
        await reply(f"⏭ Skipped. {next_msg}")
        return

    if text_l.startswith("edit summary:"):
        new_summary = text.split(":", 1)[1].strip()
        db.update_story_metadata(sid, summary=new_summary)
        await reply(_render_story(sid))
        return

    if text_l.startswith("drop #"):
        # Handled in Task 15. Stub for now.
        await reply("Drop handled in next task.")
        return

    await reply("Reply: yes / edit summary: <text> / drop #N / skip")


async def handle_story_why_mattered(text: str, reply):
    """User's free-text answer for why this story mattered."""
    state = db.get_state()
    temp = _temp(state)
    sid = temp.get("current_story_id")
    if not sid:
        await reply("No active story. Run /syncstories to start.")
        return
    db.update_story_metadata(sid, why_mattered=text.strip())
    parent = db.get_life_log_entry(sid)
    questions = (parent.get("extras") or {}).get("_suggested_extras_questions") or []
    if not questions:
        # No optional follow-ups — confirm and advance
        db.confirm_story(sid)
        next_msg = _advance_or_finish(reply)
        await reply(f"✅ Logged. {next_msg}")
        return
    db.set_state(state="story_extras_optin", temp_data=temp)
    qlist = "\n".join(f"  • {q}" for q in questions[:3])
    await reply(
        f"📌 Want to add more details?\n{qlist}\n\nReply yes to answer them, or skip."
    )
```

- [ ] **Step 4: Run, confirm tests pass**

Run: `pytest tests/test_story_review_handler.py -v -k "present_next or confirming or why_mattered"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add handlers/story_review.py tests/test_story_review_handler.py
git commit -m "feat(handlers): story review state machine — confirming + why_mattered"
```

---

## Task 15: Story review state machine — extras opt-in + Q&A loop + drop #N

**Files:**
- Modify: `handlers/story_review.py`
- Test: `tests/test_story_review_handler.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_story_review_handler.py`:

```python
@pytest.mark.asyncio
async def test_drop_event_returns_to_unclustered_and_re_renders(temp_db_path):
    import database
    from handlers.story_review import handle_story_confirming

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-13",
        story_type="trip", summary="Trip",
        highlights=["A", "B"], location="VT",
    )
    c1 = database.save_proposal(
        date_start="2024-03-12", date_end=None,
        categories=["Vacation"], description="Day 1",
        location="VT", source="calendar", source_id="e1",
    )
    c2 = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Vacation"], description="Day 2",
        location="VT", source="calendar", source_id="e2",
    )
    database.assign_child_to_story(c1, sid)
    database.assign_child_to_story(c2, sid)
    _setup_queue(database, [sid], current_id=sid)

    reply = AsyncMock()
    await handle_story_confirming("drop #1", reply)

    # c1 returned to unclustered pool
    assert database.get_life_log_entry(c1)["parent_id"] is None
    # c2 still attached
    assert database.get_life_log_entry(c2)["parent_id"] == sid
    # Story re-rendered with 1 event remaining
    assert any("1 events" in c.args[0] for c in reply.call_args_list)


@pytest.mark.asyncio
async def test_drop_out_of_range_polite_error(temp_db_path):
    import database
    from handlers.story_review import handle_story_confirming

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="Trip", highlights=[], location=None,
    )
    c1 = database.save_proposal(
        date_start="2024-03-12", date_end=None,
        categories=["Vacation"], description="Only event",
        location=None, source="calendar", source_id="e1",
    )
    database.assign_child_to_story(c1, sid)
    _setup_queue(database, [sid], current_id=sid)

    reply = AsyncMock()
    await handle_story_confirming("drop #5", reply)
    msg = reply.call_args.args[0]
    assert "1" in msg and "drop" in msg.lower()
    # Nothing changed
    assert database.get_life_log_entry(c1)["parent_id"] == sid


@pytest.mark.asyncio
async def test_extras_optin_skip_confirms_and_advances(temp_db_path):
    import database
    from handlers.story_review import handle_story_extras_optin

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="Trip", highlights=[], location=None,
        extras={"_suggested_extras_questions": ["mode?"]},
    )
    _setup_queue(database, [sid], current_id=sid)
    database.set_state(state="story_extras_optin",
                       temp_data={"pending_story_ids": [sid],
                                  "current_story_id": sid})
    reply = AsyncMock()
    await handle_story_extras_optin("skip", reply)
    assert database.get_life_log_entry(sid)["status"] == "confirmed"


@pytest.mark.asyncio
async def test_extras_optin_yes_starts_qa_loop(temp_db_path, mock_anthropic):
    import database
    from handlers.story_review import (
        handle_story_extras_optin, handle_story_extras_qa,
    )
    from unittest.mock import MagicMock as MM

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="Trip", highlights=[], location=None,
        extras={"_suggested_extras_questions": ["mode of travel?", "who came?"]},
    )
    _setup_queue(database, [sid], current_id=sid)
    database.set_state(state="story_extras_optin",
                       temp_data={"pending_story_ids": [sid],
                                  "current_story_id": sid})
    reply = AsyncMock()
    await handle_story_extras_optin("yes", reply)
    state = database.get_state()
    assert state["state"] == "story_extras_qa"

    # Mock parse_extras_answer
    mock_anthropic.messages.create.return_value.content = [
        MM(text=json.dumps({"travel_mode": "flight"}))
    ]
    await handle_story_extras_qa("flight", reply)

    e = database.get_life_log_entry(sid)
    assert e["extras"].get("travel_mode") == "flight"
```

- [ ] **Step 2: Run, confirm fails**

Run: `pytest tests/test_story_review_handler.py -v -k "drop or extras"`
Expected: FAILs.

- [ ] **Step 3: Wire up `drop #N` in `handle_story_confirming` and add the new handlers**

Replace the `drop #` stub in `handlers/story_review.py` with:

```python
    if text_l.startswith("drop #"):
        # Parse the index
        try:
            n = int(text_l.split("#", 1)[1].strip().split()[0])
        except (ValueError, IndexError):
            await reply("Reply: yes / edit summary: <text> / drop #N / skip")
            return
        children = _children_of(sid)
        if n < 1 or n > len(children):
            await reply(
                f"Story only has {len(children)} events; valid drop targets "
                f"are #1–{len(children)}."
            )
            return
        # Resolve to child id by date order (matches numbering in _render_story)
        ordered = sorted(
            (db.get_life_log_entry(c["id"]) for c in children),
            key=lambda c: c["date_start"],
        )
        child_id = ordered[n - 1]["id"]
        db.drop_event_from_story(child_id)
        # Refresh the parent's highlights — pull the highlights for surviving children
        await reply(f"✓ Dropped event #{n}. Story is now:")
        await reply(_render_story(sid))
        return
```

Append to `handlers/story_review.py`:

```python
async def handle_story_extras_optin(text: str, reply):
    """yes → start Q&A loop; skip → confirm + advance."""
    state = db.get_state()
    temp = _temp(state)
    sid = temp.get("current_story_id")
    if not sid:
        await reply("No active story.")
        return

    text_l = (text or "").strip().lower()
    if text_l in ("skip", "no", "n"):
        db.confirm_story(sid)
        next_msg = _advance_or_finish(reply)
        await reply(f"✅ Logged. {next_msg}")
        return

    if text_l in ("yes", "y", "ok"):
        # Pull suggested questions from the parent's extras
        parent = db.get_life_log_entry(sid)
        questions = (parent.get("extras") or {}).get(
            "_suggested_extras_questions"
        ) or []
        if not questions:
            db.confirm_story(sid)
            next_msg = _advance_or_finish(reply)
            await reply(f"✅ Logged (no extras to capture). {next_msg}")
            return
        temp["extras_qa_remaining"] = list(questions[:3])
        db.set_state(state="story_extras_qa", temp_data=temp)
        await reply(temp["extras_qa_remaining"][0])
        return

    await reply("Reply yes to answer the optional details, or skip to move on.")


async def handle_story_extras_qa(text: str, reply):
    """One round of structured Q&A; loops until questions are exhausted."""
    from ai_life_log import parse_extras_answer

    state = db.get_state()
    temp = _temp(state)
    sid = temp.get("current_story_id")
    questions = temp.get("extras_qa_remaining") or []
    if not sid or not questions:
        await reply("No active question.")
        return

    current_q = questions[0]
    parent = db.get_life_log_entry(sid)
    parsed = parse_extras_answer(parent.get("story_type") or "other", current_q, text)

    # Merge parsed into existing extras
    existing = parent.get("extras") or {}
    existing.pop("_suggested_extras_questions", None)
    existing.update(parsed)
    db.update_story_metadata(sid, extras=existing)

    remaining = questions[1:]
    if remaining:
        temp["extras_qa_remaining"] = remaining
        db.set_state(state="story_extras_qa", temp_data=temp)
        await reply(remaining[0])
        return

    # Done with Q&A — confirm and advance
    temp.pop("extras_qa_remaining", None)
    db.confirm_story(sid)
    next_msg = _advance_or_finish(reply)
    await reply(f"✅ Logged. {next_msg}")
```

- [ ] **Step 4: Run, confirm pass**

Run: `pytest tests/test_story_review_handler.py -v -k "drop or extras"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add handlers/story_review.py tests/test_story_review_handler.py
git commit -m "feat(handlers): drop #N + extras opt-in + Q&A loop"
```

---

## Task 16: Resume-after-abandonment + /pushstories + /showstory

**Files:**
- Create: `handlers/pushstories.py`, `handlers/showstory.py`
- Modify: `handlers/story_review.py` (add resume helper)
- Test: `tests/test_story_review_handler.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_story_review_handler.py`:

```python
@pytest.mark.asyncio
async def test_resume_prompt_when_queue_present(temp_db_path):
    import database
    from handlers.story_review import maybe_offer_resume

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="X", highlights=[], location=None,
    )
    database.set_state(
        state="idle",  # user closed app; state was idle
        temp_data={"pending_story_ids": [sid], "current_story_id": None},
    )

    reply = AsyncMock()
    handled = await maybe_offer_resume(reply)
    assert handled is True
    msg = reply.call_args.args[0]
    assert "Resume" in msg or "resume" in msg
```

Create `tests/test_pushstories_handler.py`:

```python
"""Tests for /pushstories — retry sheet write."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_pushstories_writes_pending_to_sheet(temp_db_path):
    import database
    from handlers.pushstories import pushstories_command

    database.save_story_parent(
        date_start="2024-03-12", date_end=None,
        story_type="trip", summary="X", highlights=[], location=None,
    )

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    with patch("handlers.pushstories.sync_stories_to_sheet",
               return_value="https://example/sheet"):
        await pushstories_command(update, context)
    last_msg = update.message.reply_text.call_args_list[-1].args[0]
    assert "1" in last_msg  # one story written
```

Create `tests/test_showstory_handler.py`:

```python
"""Tests for /showstory <id>."""
from unittest.mock import AsyncMock, MagicMock
import pytest


@pytest.mark.asyncio
async def test_showstory_dumps_parent_and_children(temp_db_path):
    import database
    from handlers.showstory import showstory_command

    sid = database.save_story_parent(
        date_start="2024-03-12", date_end="2024-03-13",
        story_type="trip", summary="Trip", highlights=["a", "b"], location="VT",
    )
    cid = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Vacation"], description="Day 2",
        location="VT", source="calendar", source_id="e1",
    )
    database.assign_child_to_story(cid, sid)

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = [str(sid)]

    await showstory_command(update, context)
    msg = update.message.reply_text.call_args.args[0]
    assert "trip" in msg.lower()
    assert "Day 2" in msg
    assert "VT" in msg
```

- [ ] **Step 2: Run, confirm fails**

Run: `pytest tests/test_pushstories_handler.py tests/test_showstory_handler.py tests/test_story_review_handler.py::test_resume_prompt_when_queue_present -v`
Expected: 3 FAILs.

- [ ] **Step 3: Add `maybe_offer_resume` to `handlers/story_review.py`**

Append:

```python
async def maybe_offer_resume(reply) -> bool:
    """If a story queue is non-empty but state was idled, prompt to resume.

    Returns True if a resume prompt was sent (caller should not process the
    message further). Returns False to let the caller's normal handler run.
    """
    state = db.get_state()
    if state["state"] != "idle":
        return False
    temp = _temp(state)
    queue = temp.get("pending_story_ids") or []
    if not queue:
        return False
    db.set_state(state="story_resume_prompt", temp_data=temp)
    await reply(
        f"📚 You have {len(queue)} story review(s) in progress. "
        f"Resume? Reply *yes* to continue or *clear* to drop the queue.",
    )
    return True


async def handle_story_resume_prompt(text: str, reply):
    """Handle the 'yes/clear' reply after a resume prompt."""
    state = db.get_state()
    temp = _temp(state)
    text_l = (text or "").strip().lower()
    if text_l in ("yes", "y", "resume"):
        await present_next_story(reply)
        return
    if text_l in ("clear", "no", "n", "cancel"):
        db.set_state(state="idle", temp_data={})
        await reply("Queue cleared.")
        return
    await reply("Reply *yes* to resume or *clear* to drop the queue.")
```

- [ ] **Step 4: Implement `handlers/pushstories.py`**

Create:

```python
"""Handler for /pushstories — retry pushing pending stories to the Sheet."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db
from google_sheets import sync_stories_to_sheet

logger = logging.getLogger(__name__)


async def pushstories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stories = db.get_pending_stories_with_children()
    if not stories:
        await update.message.reply_text("No pending stories.")
        return

    await update.message.reply_text(
        f"📤 Pushing {len(stories)} stories to the Stories tab…"
    )
    try:
        url = sync_stories_to_sheet(stories)
    except Exception as e:
        logger.error("sync_stories_to_sheet failed: %s", e, exc_info=True)
        await update.message.reply_text(
            f"❌ Sheet write failed.\n\n`{type(e).__name__}: {e}`",
            parse_mode="Markdown",
        )
        return
    await update.message.reply_text(
        f"✅ Wrote {len(stories)} stories.\n📝 [Open]({url})",
        parse_mode="Markdown",
    )
```

- [ ] **Step 5: Implement `handlers/showstory.py`**

Create:

```python
"""Handler for /showstory <id> — dump story DB row + children."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)


async def showstory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /showstory <id>")
        return
    try:
        sid = int(args[0])
    except ValueError:
        await update.message.reply_text("ID must be an integer.")
        return
    parent = db.get_life_log_entry(sid)
    if parent is None:
        await update.message.reply_text(f"No entry with ID {sid}.")
        return

    parts = [
        f"📖 *Story {sid}* (status: {parent.get('status')})",
        f"• type: `{parent.get('story_type')}`",
        f"• date: `{parent.get('date_start')}` → `{parent.get('date_end')}`",
        f"• summary: {parent.get('description')!r}",
        f"• location: {parent.get('location')!r}",
        f"• why_mattered: {parent.get('why_mattered')!r}",
        f"• highlights: {parent.get('highlights')}",
        f"• extras: `{parent.get('extras')}`",
    ]
    p = db._p()
    with db._cursor() as c:
        c.execute(
            f"SELECT * FROM life_log_entries WHERE parent_id={p} ORDER BY date_start, id",
            (sid,),
        )
        children = [db._unpack_life_log_entry(r) for r in db._rows(c.fetchall())]
    if children:
        parts.append("\n👶 *Children:*")
        for ch in children:
            parts.append(
                f"  • #{ch['id']} {ch.get('date_start')} — "
                f"{ch.get('description')!r} @ {ch.get('location')!r}"
            )
    msg = "\n".join(parts)
    if len(msg) > 3800:
        msg = msg[:3800] + "\n…(truncated)"
    await update.message.reply_text(msg, parse_mode="Markdown")
```

- [ ] **Step 6: Run all three tests**

Run: `pytest tests/test_pushstories_handler.py tests/test_showstory_handler.py tests/test_story_review_handler.py::test_resume_prompt_when_queue_present -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add handlers/pushstories.py handlers/showstory.py handlers/story_review.py \
        tests/test_pushstories_handler.py tests/test_showstory_handler.py \
        tests/test_story_review_handler.py
git commit -m "feat(handlers): /pushstories, /showstory, resume-after-abandon prompt"
```

---

## Task 17: bot.py wiring — register, retire old, update _COMMANDS_TEXT, dispatch states

**Files:**
- Modify: `bot.py`
- Test: `tests/test_bot_wiring.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_bot_wiring.py`:

```python
def test_buildstories_and_syncstories_registered():
    from bot import create_application
    app = create_application()
    handlers = [h for grp in app.handlers.values() for h in grp]
    cmds = {getattr(h, "commands", set()) for h in handlers}
    flat = {c for s in cmds if s for c in s}
    assert "buildstories" in flat
    assert "syncstories" in flat
    assert "pushstories" in flat
    assert "showstory" in flat


def test_legacy_proposal_commands_removed():
    from bot import create_application
    app = create_application()
    handlers = [h for grp in app.handlers.values() for h in grp]
    flat = set()
    for h in handlers:
        for c in getattr(h, "commands", set()) or set():
            flat.add(c)
    assert "syncproposals" not in flat
    assert "pushproposals" not in flat
    assert "proposals" not in flat
```

- [ ] **Step 2: Run, confirm fails**

Run: `pytest tests/test_bot_wiring.py -v -k "buildstories or legacy"`
Expected: FAILs.

- [ ] **Step 3: Wire up handlers in `bot.py`**

In `bot.py`:

1. Add imports near the existing handler imports:
```python
from handlers.buildstories import buildstories_command
from handlers.syncstories import syncstories_command
from handlers.pushstories import pushstories_command
from handlers.showstory import showstory_command
from handlers import story_review
```

2. Remove the old imports/registrations:
```python
# Remove these lines from bot.py:
# from handlers.syncproposals import syncproposals_command
# from handlers.pushproposals import pushproposals_command
# from handlers.proposals_review import proposals_command
# from handlers.calendarbackfill import calendarbackfill_command  # keep this one
# app.add_handler(CommandHandler("syncproposals", syncproposals_command))
# app.add_handler(CommandHandler("pushproposals", pushproposals_command))
# app.add_handler(CommandHandler("proposals", proposals_command))
```

3. Add the new registrations in `create_application()`:
```python
    app.add_handler(CommandHandler("buildstories", buildstories_command))
    app.add_handler(CommandHandler("syncstories", syncstories_command))
    app.add_handler(CommandHandler("pushstories", pushstories_command))
    app.add_handler(CommandHandler("showstory", showstory_command))
```

4. Update `_COMMANDS_TEXT`:
```python
_COMMANDS_TEXT = (
    "🤖 *Weekly Updates Bot — Commands*\n\n"
    "*🧠 Life Log*\n"
    "• /log \\[text\\] — Log a memorable moment \\(AI extracts category, people, date\\)\n"
    "• /ask \\[question\\] — Natural\\-language query of your Life Log\n"
    "• /people — List people in your Life Log\n"
    "• /buildstories — Cluster pending events into stories \\(trip, interview, etc\\.\\)\n"
    "• /syncstories — Apply *Stories* tab decisions and start Telegram review\n"
    "• /pushstories — Re\\-push pending stories to the Sheet \\(retry after a failed sheet write\\)\n"
    "• /showstory \\[id\\] — Inspect a story's data\n"
    "• /dismissbirthdays — Bulk\\-dismiss pending birthday proposals\n"
    "• /calendarbackfill \\[start\\_year\\] \\[end\\_year\\] — Import calendar history\n\n"
    "*📊 Viewing & Syncing*\n"
    "• /status — This week's logged data\n"
    "• /sync — Push Life Log \\+ People \\+ Habits to Google Sheets\n"
    "• /summary — Generate weekly summary now\n\n"
    "*🏃 Habits*\n"
    "• /habit \\[description\\] — Add a habit via natural language\n"
    "• /habits — List active habits\n"
    "• /habitstop \\[name\\] — Deactivate a habit\n\n"
    "*⚙️ Admin*\n"
    "• /skip — Skip the current prompt in any active flow\n"
    "• /cleardb — Delete all data \\(requires CONFIRM\\)\n"
    "• /start — Show this message\n"
)
```

5. Add the new states to `handle_message` dispatcher (the central state-router in `bot.py`). Locate the existing state checks (lifelog_confirming, etc.) and append:

```python
    # Story review states (delegate to handlers/story_review.py)
    if state == "story_resume_prompt":
        await story_review.handle_story_resume_prompt(text, _make_reply(update))
        return
    if state == "story_confirming":
        await story_review.handle_story_confirming(text, _make_reply(update))
        return
    if state == "story_why_mattered":
        await story_review.handle_story_why_mattered(text, _make_reply(update))
        return
    if state == "story_extras_optin":
        await story_review.handle_story_extras_optin(text, _make_reply(update))
        return
    if state == "story_extras_qa":
        await story_review.handle_story_extras_qa(text, _make_reply(update))
        return
```

Where `_make_reply` is the existing reply helper in bot.py (already used by other handlers). If it doesn't exist with that exact name, build one:

```python
    def _make_reply(update):
        async def _reply(msg):
            await update.message.reply_text(msg, parse_mode="Markdown")
        return _reply
```

6. In the idle-state branch (where free-form text gets a generic hint), call `maybe_offer_resume` first:

```python
    if state == "idle":
        # First, see if there's an in-progress story queue to resume
        handled = await story_review.maybe_offer_resume(_make_reply(update))
        if handled:
            return
        # ... existing idle hint message ...
```

- [ ] **Step 4: Run, confirm pass**

Run: `pytest tests/test_bot_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot_wiring.py
git commit -m "feat(bot): wire story handlers, retire old proposal commands, dispatch states"
```

---

## Task 18: End-to-end happy path test (cross-engine)

**Files:**
- Create: `tests/test_stories_e2e.py`

- [ ] **Step 1: Write the test**

Create `tests/test_stories_e2e.py`:

```python
"""End-to-end happy path: ingest → cluster → sheet → telegram → confirmed."""
from unittest.mock import AsyncMock, MagicMock, patch
import json
import pytest


def _seed_pending_events(database):
    """Three calendar events: two clustered as a trip, one singleton."""
    e1 = database.save_proposal(
        date_start="2024-03-12", date_end=None,
        categories=["Vacation"], description="Vermont arrival",
        location="VT", source="calendar", source_id="evt-1",
    )
    e2 = database.save_proposal(
        date_start="2024-03-13", date_end=None,
        categories=["Skiing"], description="Skiing Killington",
        location="VT", source="calendar", source_id="evt-2",
    )
    e3 = database.save_proposal(
        date_start="2024-04-15", date_end=None,
        categories=["Concert"], description="Phish at MSG",
        location="NYC", source="calendar", source_id="evt-3",
    )
    return e1, e2, e3


def _ai_returns(*payloads):
    """Build a side_effect that returns one mocked AI response per call, in order."""
    iter_p = iter(payloads)
    def _next(*a, **kw):
        m = MagicMock()
        m.content = [MagicMock(text=json.dumps(next(iter_p)))]
        return m
    return _next


async def _e2e_run():
    import database
    from handlers.buildstories import buildstories_command
    from handlers.syncstories import syncstories_command
    from handlers.story_review import (
        handle_story_confirming, handle_story_why_mattered,
        handle_story_extras_optin,
    )

    e1, e2, e3 = _seed_pending_events(database)

    # Stage 1: /buildstories — AI returns one shape per cluster
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    ctx = MagicMock(); ctx.args = []

    with patch("ai_life_log._client", new=None):
        with patch("anthropic.Anthropic") as Anthropic:
            client = MagicMock()
            client.messages.create.side_effect = _ai_returns(
                {"story_type": "trip", "summary": "Vermont trip",
                 "highlights": ["Arrival", "Skiing"], "event_id_refs": [e1, e2],
                 "suggested_extras_questions": [], "location": "VT"},
                {"story_type": "other", "summary": "Phish at MSG",
                 "highlights": ["Phish at MSG"], "event_id_refs": [e3],
                 "suggested_extras_questions": [], "location": "NYC"},
            )
            Anthropic.return_value = client
            with patch("handlers.buildstories.sync_stories_to_sheet",
                       return_value="https://example/sheet"):
                await buildstories_command(update, ctx)

    stories = database.get_pending_stories_with_children()
    assert len(stories) == 2

    # Stage 2: /syncstories — confirm both
    decisions = [{"id": s["id"], "decision": "confirm"} for s in stories]
    with patch("handlers.syncstories.read_story_decisions", return_value=decisions), \
         patch("handlers.syncstories.present_next_story", new=AsyncMock()):
        await syncstories_command(update, ctx)

    # Stage 3: walk Telegram review for both stories
    reply = AsyncMock()
    for _ in range(2):
        await handle_story_confirming("yes", reply)
        await handle_story_why_mattered("It mattered.", reply)
        await handle_story_extras_optin("skip", reply)

    # All four entries (2 parents + 2 children + 1 singleton) should be confirmed
    e1_e = database.get_life_log_entry(e1)
    e2_e = database.get_life_log_entry(e2)
    e3_e = database.get_life_log_entry(e3)
    assert e1_e["status"] == "confirmed"
    assert e2_e["status"] == "confirmed"
    assert e3_e["status"] == "confirmed"


@pytest.mark.asyncio
async def test_e2e_sqlite(temp_db_path):
    await _e2e_run()


@pytest.mark.asyncio
async def test_e2e_postgres(postgres_db):
    await _e2e_run()
```

- [ ] **Step 2: Run on SQLite first**

Run: `pytest tests/test_stories_e2e.py -v -k sqlite`
Expected: PASS.

- [ ] **Step 3: Run on Postgres if available**

Run: `TEST_POSTGRES_URL=postgres://... pytest tests/test_stories_e2e.py -v -k postgres`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_stories_e2e.py
git commit -m "test(stories): end-to-end happy path on SQLite + Postgres"
```

---

## Task 19: Golden-fixtures clustering regression test

**Files:**
- Create: `tests/fixtures/clustering/golden_clusters.json`
- Create: `tests/test_golden_clusters.py`

- [ ] **Step 1: Build the golden fixture file**

Create `tests/fixtures/clustering/golden_clusters.json`. Each entry is a cluster of events plus the AI-classified output we expect (or accept). 15 fixtures covering: trip with flights, multi-day trip, interview cycle (3 rounds same company), conference, holiday weekend, dating start, dating end, family visit, project milestone, concert series, single one-off, two singletons that should NOT cluster, mixed cluster (work + personal that should split), birthday-titled (should be filtered upstream), edge case (all-day event with weird datetime).

Layout:
```json
[
  {
    "name": "vermont_ski_trip",
    "events": [
      {"id": 1, "date_start": "2024-03-12", "title": "JFK->BTV flight",
       "description": "JFK->BTV flight", "location": ""},
      {"id": 2, "date_start": "2024-03-13", "title": "Skiing Killington",
       "description": "Skiing Killington", "location": "Killington, VT"},
      {"id": 3, "date_start": "2024-03-14", "title": "Skiing Killington",
       "description": "Skiing Killington", "location": "Killington, VT"},
      {"id": 4, "date_start": "2024-03-17", "title": "BTV->JFK flight",
       "description": "BTV->JFK flight", "location": ""}
    ],
    "expected_clusters": 1,
    "expected_story_type": "trip",
    "expected_highlight_ref_subset_of": [1, 2, 3, 4]
  }
  // ... 14 more
]
```

(The plan calls for 15 fixtures; the engineer should hand-build the rest from realistic patterns. Each must include `expected_clusters` and either `expected_story_type` or a list of acceptable types.)

- [ ] **Step 2: Write the regression test**

Create `tests/test_golden_clusters.py`:

```python
"""Regression test against hand-crafted golden clusters.

Catches drift if the prompt or fallback logic changes. Each fixture asserts:
- precluster_by_date produces the expected number of clusters
- AI returns one of the acceptable story_types
- All event_id_refs reference events that exist in the input cluster
"""
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "clustering" / "golden_clusters.json"


@pytest.fixture
def fixtures():
    return json.loads(FIXTURE_PATH.read_text())


def test_preclustering_matches_expected_clusters(fixtures):
    from services.story_clustering import precluster_by_date

    for fx in fixtures:
        clusters = precluster_by_date(fx["events"])
        assert len(clusters) == fx["expected_clusters"], (
            f"{fx['name']}: expected {fx['expected_clusters']} clusters, "
            f"got {len(clusters)}"
        )


def test_event_id_refs_are_grounded(fixtures, mock_anthropic):
    """For each fixture, every AI-returned event_id_ref must exist in the input."""
    from ai_life_log import cluster_into_story

    for fx in fixtures:
        cluster = fx["events"]
        cluster_ids = {e["id"] for e in cluster}
        # Use a deterministic fallback shape so we don't depend on real AI output
        mock_anthropic.messages.create.return_value.content = [MagicMock(
            text=json.dumps({
                "story_type": fx.get("expected_story_type") or "other",
                "summary": fx["name"],
                "highlights": [e["title"] for e in cluster],
                "event_id_refs": [e["id"] for e in cluster],
                "suggested_extras_questions": [],
                "location": "",
            })
        )]
        out = cluster_into_story(cluster, active_categories=[])
        assert set(out["event_id_refs"]) <= cluster_ids, (
            f"{fx['name']}: event_id_refs not grounded in cluster"
        )
```

- [ ] **Step 3: Run, confirm pass on a minimum-viable fixture set**

Run: `pytest tests/test_golden_clusters.py -v`
Expected: PASS (with at least 1 fixture; engineer adds more incrementally).

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/clustering/golden_clusters.json tests/test_golden_clusters.py
git commit -m "test(stories): golden-fixtures clustering regression suite"
```

---

## Task 20: Manual eval runbook + CLAUDE.md update

**Files:**
- Create: `docs/superpowers/eval/clustering-eval-template.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Create the eval template**

Create `docs/superpowers/eval/clustering-eval-template.md`:

```markdown
# Clustering Eval — YYYY-MM-DD

After the first `/buildstories` over real pending data, sample and rate.

## Method

1. Open the *Stories* tab.
2. Pick 20 stories at random (use a simple `rand()` sort or eyeball it).
3. For each, fill the table below.

## Rubric

| Story ID | Type correct? (Y/N/borderline) | Summary accurate? (full/partial/wrong) | Highlights grounded? (all real / 1 hallucinated / multi hallucinated) | Notes |
|---|---|---|---|---|
| 1 |  |  |  |  |
| 2 |  |  |  |  |
| ... |  |  |  |  |
| 20 |  |  |  |  |

## Decision rule

- ≥ 3/20 stories with hallucinated highlights → revise the `cluster_into_story` prompt before running again.
- ≥ 4/20 mistyped → revise seeded type list or prompt examples.
- Otherwise → ship. Re-run eval after any prompt change.

## Iterations

| Date | Sample size | Hallucinated highlights | Mistyped | Action taken |
|---|---|---|---|---|
|  |  |  |  |  |
```

- [ ] **Step 2: Update CLAUDE.md — module map, commands list, workflow notes**

In `/Users/tomkeefe/Desktop/ClaudeApps/weekly-updates/CLAUDE.md`:

1. Replace the `### Repo Layout` block's `handlers/` section with:
```
├── handlers/
│   ├── log_command.py              # /log command + lifelog_confirming state
│   ├── lifelog_proposals.py        # Legacy per-event proposal flow (retained for /dismissbirthdays)
│   ├── lifelog_queries.py          # /ask command
│   ├── people.py                   # /people command + merge flow
│   ├── buildstories.py             # /buildstories — cluster pending into stories
│   ├── syncstories.py              # /syncstories — apply Stories sheet decisions, start review
│   ├── story_review.py             # Telegram narrative state machine (multi-state)
│   ├── pushstories.py              # /pushstories — retry sheet write
│   ├── showstory.py                # /showstory <id> — debug dump
│   ├── dismissbirthdays.py         # /dismissbirthdays — bulk dismiss birthday proposals
│   ├── calendarbackfill.py         # /calendarbackfill — import calendar history
│   └── (legacy) syncproposals.py, pushproposals.py, proposals_review.py, showproposal.py
```

2. Add to the `## Telegram Commands` table the new commands and remove `/proposals`, `/syncproposals`, `/pushproposals`:

| Command | What it does |
|---------|--------------|
| `/buildstories` | Cluster pending calendar events into stories (parents + children) and push to the *Stories* tab |
| `/syncstories` | Apply Decision column from Stories tab; surviving stories enter a Telegram narrative review queue |
| `/pushstories` | Re-push pending stories to the Sheet (retry after a failed write) |
| `/showstory [id]` | Inspect a story (and its child events) by ID |

3. Update `## State Machine`:
```
idle
 ├─► lifelog_confirming        (waiting for /log preview confirm/correct/cancel)
 ├─► lifelog_new_person        (asking relationship type for newly-detected person)
 ├─► story_resume_prompt       (asks "resume queue? yes/clear")
 ├─► story_confirming          (yes / edit summary / drop #N / skip)
 ├─► story_why_mattered        (one-sentence "why mattered" capture)
 ├─► story_extras_optin        (yes → Q&A loop / skip → confirm + advance)
 ├─► story_extras_qa           (one structured question at a time)
 ├─► confirming_habit          ┐
 ├─► collecting_habit_check    ├ habit flows
 └─► collecting_habit_reason   ┘
```

4. Add a "Stories design" pointer in the Architecture section:
```
**Stories design spec:** `docs/superpowers/specs/2026-05-04-story-driven-proposals-design.md`
**Implementation plan:** `docs/superpowers/plans/2026-05-04-story-driven-proposals.md`
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/eval/clustering-eval-template.md CLAUDE.md
git commit -m "docs(stories): manual eval runbook + CLAUDE.md story commands/state machine"
```

---

## Plan Self-Review

**Spec coverage check:** every spec section maps to at least one task.
- Decisions table → Task 2 (schema), Task 6/7/8 (clustering type seeds + flights), Task 10/11 (sheet hybrid), Task 13–15 (Telegram review), Task 9 (singletons + existing pending: `run_clustering` only touches un-assigned proposals), Task 12 (`/buildstories` trigger), Task 15 (drop #N) ✓
- Data model → Task 2 + 3 + 4 + 5 ✓
- Components / module map → Tasks 6–17 ✓
- UX detail (sheet card + Telegram script) → Task 10 + 14 + 15 ✓
- Error handling → Task 12 (sheet error surface), Task 15 (drop out-of-range), Task 16 (resume), Task 9 (AI fallback to singletons), Task 7 (orphan-highlight rejection — ID 39 fix) ✓
- Testing strategy → Tasks 2–19, including Postgres parametrization (Task 1, 4, 18) and golden fixtures (Task 19) ✓
- Manual eval → Task 20 ✓

**Placeholder scan:** searched for "TBD/TODO/etc.". One soft-edge item: Task 19 says "engineer should hand-build the rest from realistic patterns" for fixtures 2–15 — this is OK because the structure of one is shown and they're judgement calls per fixture. Acceptable.

**Type/method consistency:**
- `save_story_parent` / `assign_child_to_story` / `confirm_story` / `dismiss_story` / `drop_event_from_story` / `update_story_metadata` named consistently across plan ✓
- `present_next_story` / `handle_story_confirming` / `handle_story_why_mattered` / `handle_story_extras_optin` / `handle_story_extras_qa` / `maybe_offer_resume` / `handle_story_resume_prompt` named consistently ✓
- `_BIRTHDAY_RE` already exists; `_FLIGHT_RE` introduced in Task 7 ✓
- `SHEET_STORIES` constant introduced in Task 10 ✓
- `_normalize_row_dates` (already present from this morning's fix) used implicitly via `_unpack_life_log_entry` ✓

**Scope check:** 20 tasks, ~2-4 hour units each = a 1-2 week implementation. Within a single plan's scope.

**Open question — left out intentionally:** The legacy per-event Proposals tab is left in place but unwritten-to. We could clear/rename it on first `/buildstories` run, but the spec defers this to "spec says cleared/renamed" — for V1 we just create a new *Stories* tab; engineer can add a one-line cleanup in Task 10 if desired.
