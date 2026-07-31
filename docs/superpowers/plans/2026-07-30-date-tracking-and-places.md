# Date Tracking + Places Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track romantic dates as an unscored series — rule-detected from calendar titles containing "date" plus manual tagging — excluded from the social metric, with spend/frequency/top-places analytics on Insights, and a general `location` field on calendar events.

**Architecture:** Four new nullable columns on `calendar_events` (`is_date`/`location` gcal-owned, `user_is_date`/`user_location` user-owned) resolved in SQL per the Override + Learning pattern. Detection is a pure word-boundary regex (`metrics.title_is_date`), no AI. Social count/spend queries gain a NOT-resolved-date condition; a new `get_date_events_range` feeds a `dates` block on the insights payload and `date`-kind spend items.

**Tech Stack:** FastAPI + pydantic v2, SQLite/Postgres dual-dialect raw SQL, React + TypeScript, pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-07-30-date-tracking-and-places-design.md`

## Global Constraints

- All SQL in `database.py`; env reads in `config.py`; Claude calls in `ai_metrics.py` — **this feature adds NO Claude call** (detection is a regex, user's explicit choice).
- Scan-owned vs user-owned columns: the calendar upsert may overwrite `title`, `start_at`, `end_at`, `recurring_event_id`, `location`, `is_date` — it must NEVER touch `user_title`, `user_is_social`, `user_removed`, `user_is_date`, `user_location`, `amount`.
- Resolution in SQL, never per-call-site Python: resolved date = `COALESCE(user_is_date, is_date) IS TRUE`; resolved location = `COALESCE(NULLIF(user_location, ''), location)`. The `IS NOT TRUE` idiom already used for `user_removed` works on both dialects.
- Dates are NOT in `METRICS` — no target row, no scorecard entry. Telegram push and `/api/reflection` stay date-free by construction; a regression test locks this.
- Dates are EXCLUDED from the social count and social spend (user decision, reversed once — final answer is excluded).
- Day-log six-category cap: dates render in the event slot with a `date` chip, NOT a new category. Adding the chip is the sanctioned "deliberate design event" the StatusChip comment demands.
- Charts: wide viewBox + `width:100%; height:auto`, never a fixed pixel height; the dates mini-chart has no target line (unscored).
- Money format: `$16.31` / `$20` trimmed via the existing `money()` helper; null-check, never truthiness.
- Verify: `pytest tests/ -v` (venv active) and, in `frontend/`, `npm test -- --run && npx tsc --noEmit && npm run build`.

---

### Task 1: `metrics.title_is_date` pure rule

**Files:**
- Modify: `metrics.py` (after `nudge_user_date`)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `metrics.title_is_date(title: str) -> bool` — word-boundary, case-insensitive match on "date".

- [ ] **Step 1: Write the failing test**

`tests/test_metrics.py` imports names via `from metrics import ...` at top; this test imports locally to keep the diff small:

```python
def test_title_is_date():
    from metrics import title_is_date
    assert title_is_date("Date night") is True
    assert title_is_date("date w/ Alex") is True
    assert title_is_date("DATE — Bar Dough") is True
    assert title_is_date("Second date?") is True
    assert title_is_date("Update sync") is False
    assert title_is_date("Candidate interview") is False
    assert title_is_date("Mandate review") is False
    assert title_is_date("Dates with friends") is False  # plural is not the word "date"
    assert title_is_date("") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_metrics.py::test_title_is_date -v`
Expected: FAIL — ImportError

- [ ] **Step 3: Implement**

In `metrics.py` (add `import re` to the top imports):

```python
def title_is_date(title: str) -> bool:
    """Rule-based date detection — the user's explicit scope: 'only look for
    things that say Date, besides that it's just manual'. A word-boundary
    match so 'Update sync' / 'Candidate interview' never fire; deliberately
    NOT AI (see the 2026-07-30 date-tracking spec's rejected alternatives).
    Plural 'dates' doesn't match: the signal is the literal word."""
    return bool(re.search(r"\bdate\b", title or "", re.IGNORECASE))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_metrics.py::test_title_is_date -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add metrics.py tests/test_metrics.py
git commit -m "feat: rule-based date detection from event titles"
```

---

### Task 2: Columns, upsert, manual insert, overrides

**Files:**
- Modify: `database.py` — migration section (after the `user_date` block), `upsert_calendar_event` (~line 947), `add_manual_social_event` (~line 1064), `set_event_overrides` allowed set (~line 1088)
- Test: `tests/test_database_v2.py`

**Interfaces:**
- Consumes: `metrics.title_is_date` is NOT called here — the caller (scan/route) passes values in; `database.py` stays rule-free.
- Produces:
  - Columns: `calendar_events.is_date` (nullable bool, gcal-owned), `user_is_date` (nullable bool, user), `location` (nullable TEXT, gcal-owned), `user_location` (nullable TEXT, user).
  - `upsert_calendar_event(gcal_event_id, title, start_at, end_at, recurring_event_id=None, location=None, is_date=None)` — upsert overwrites `location`/`is_date` like `title`.
  - `add_manual_social_event(event_id, title, start_at, end_at, amount=None, location=None, is_date=False)` — stores location in `location`, a truthy `is_date` in `user_is_date` (user-asserted), NULL otherwise.
  - `set_event_overrides` accepts `user_is_date` and `user_location` keys.

- [ ] **Step 1: Write the failing tests**

```python
def test_calendar_event_date_and_location_columns(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev-d1", "Date night", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00", location="Bar Dough", is_date=True)
    row = db.get_event("ev-d1")
    assert bool(row["is_date"]) is True
    assert row["location"] == "Bar Dough"
    assert row["user_is_date"] is None
    assert row["user_location"] is None


def test_upsert_overwrites_gcal_columns_never_user_columns(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev-d2", "Dinner", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00", location="Old Place", is_date=False)
    db.set_event_overrides("ev-d2", {"user_is_date": True, "user_location": "Corrected Venue"})
    # Re-upsert (a later scan) refreshes gcal-owned columns…
    db.upsert_calendar_event("ev-d2", "Dinner", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00", location="New Place", is_date=False)
    row = db.get_event("ev-d2")
    assert row["location"] == "New Place"
    # …but never the user's.
    assert bool(row["user_is_date"]) is True
    assert row["user_location"] == "Corrected Venue"


def test_manual_event_with_date_and_location(temp_db_path):
    db = _db(temp_db_path)
    db.add_manual_social_event("manual:d1", "Drinks", "2026-07-15T12:00:00",
                               "2026-07-15T13:00:00", 40.0, location="Wine Bar", is_date=True)
    row = db.get_event("manual:d1")
    assert bool(row["user_is_date"]) is True   # user-asserted, in the USER column
    assert row["is_date"] is None
    assert row["location"] == "Wine Bar"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_database_v2.py -k "date_and_location or never_user_columns or manual_event_with_date" -v`
Expected: FAIL — unexpected keyword argument / KeyError

- [ ] **Step 3: Implement**

Migration block, after the `user_date` block, using `bool_t` (already in scope):

```python
        # Date tracking + places (2026-07-30 spec). is_date/location are
        # GCAL-OWNED (the scan's upsert overwrites them, same footing as
        # title); user_is_date/user_location are USER columns the scan never
        # touches. Resolved in SQL: COALESCE(user_is_date, is_date) and
        # COALESCE(NULLIF(user_location, ''), location).
        if USE_POSTGRES:
            c.execute(f"ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS is_date {bool_t}")
            c.execute(f"ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS user_is_date {bool_t}")
            c.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS location TEXT")
            c.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS user_location TEXT")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(calendar_events)").fetchall()]
            for name, defn in (("is_date", bool_t), ("user_is_date", bool_t),
                               ("location", "TEXT"), ("user_location", "TEXT")):
                if name not in cols:
                    c.execute(f"ALTER TABLE calendar_events ADD COLUMN {name} {defn}")
```

`upsert_calendar_event` — extend signature and both SQL halves (docstring: append "`location`/`is_date` are Google's own data too — `is_date` is derived from the gcal title by the caller via metrics.title_is_date; a re-upsert overwrites both."):

```python
def upsert_calendar_event(gcal_event_id, title, start_at, end_at, recurring_event_id=None,
                          location=None, is_date=None):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO calendar_events
                    (gcal_event_id, title, start_at, end_at, recurring_event_id, location, is_date)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
                ON CONFLICT(gcal_event_id) DO UPDATE
                SET title = excluded.title, start_at = excluded.start_at, end_at = excluded.end_at,
                    recurring_event_id = excluded.recurring_event_id,
                    location = excluded.location, is_date = excluded.is_date""",
            (gcal_event_id, title, start_at, end_at, recurring_event_id, location, is_date),
        )
```

`add_manual_social_event`:

```python
def add_manual_social_event(event_id, title, start_at, end_at, amount=None,
                            location=None, is_date=False):
    """Manual events are user assertions, so a date flag lands in
    user_is_date (the USER column), not the scan-owned is_date."""
    p = _p()
    user_is_date = True if is_date else None
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO calendar_events
                    (gcal_event_id, title, start_at, end_at, is_social, source, confidence,
                     classified_at, amount, location, user_is_date)
                VALUES ({p}, {p}, {p}, {p}, {_social_true()}, 'manual', 1.0,
                        CURRENT_TIMESTAMP, {p}, {p}, {p})""",
            (event_id, title, start_at, end_at, amount, location, user_is_date),
        )
```

`set_event_overrides`: `allowed = {"user_title", "user_is_social", "amount", "user_removed", "user_is_date", "user_location"}`.

- [ ] **Step 4: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: PASS (existing upsert/manual-event callers use positional args up to `amount` only — the new params are keyword-tail, nothing breaks).

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_v2.py
git commit -m "feat(db): is_date/user_is_date + location/user_location on calendar_events"
```

---

### Task 3: Queries — social excludes dates, day includes them, dates range

**Files:**
- Modify: `database.py` — `get_social_events_range`, `get_events_for_day`, new `get_date_events_range` after them
- Test: `tests/test_database_v2.py`

**Interfaces:**
- Consumes: Task 2 columns.
- Produces:
  - `get_social_events_range` — unchanged shape, now excludes resolved dates.
  - `get_events_for_day` rows gain `is_date` (bool) and `location` (resolved, may be None); resolved-date events are included even though they're excluded from social counting.
  - `get_date_events_range(start_day, end_day)` — resolved-date, non-removed rows by `end_at` day (mirrors social counting semantics): `gcal_event_id, title (resolved), start_at, end_at, source, amount, location (resolved), is_date (always True)`.

- [ ] **Step 1: Write the failing tests**

```python
def _seed_date_event(db, ev_id="ev-date", title="Date night", day="2026-07-15",
                     amount=60.0, location="Bar Dough"):
    db.upsert_calendar_event(ev_id, title, f"{day}T19:00:00-06:00",
                             f"{day}T21:00:00-06:00", location=location, is_date=True)
    db.set_event_classification(ev_id, True, 0.9)  # AI also thought it social
    if amount is not None:
        db.set_event_overrides(ev_id, {"amount": amount})


def test_social_range_excludes_resolved_dates(temp_db_path):
    db = _db(temp_db_path)
    _seed_date_event(db)
    db.upsert_calendar_event("ev-plain", "Trivia", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev-plain", True, 0.9)
    titles = [e["title"] for e in db.get_social_events_range("2026-07-14", "2026-07-20")]
    assert titles == ["Trivia"]
    # user_is_date=False un-dates a rule-flagged event — back into social.
    db.set_event_overrides("ev-date", {"user_is_date": False})
    titles = [e["title"] for e in db.get_social_events_range("2026-07-14", "2026-07-20")]
    assert sorted(titles) == ["Date night", "Trivia"]


def test_events_for_day_includes_dates_with_fields(temp_db_path):
    db = _db(temp_db_path)
    _seed_date_event(db)
    rows = db.get_events_for_day("2026-07-15")
    assert len(rows) == 1
    assert rows[0]["is_date"] is True
    assert rows[0]["location"] == "Bar Dough"
    # user_location wins over the gcal location.
    db.set_event_overrides("ev-date", {"user_location": "Actually Sunken City"})
    assert db.get_events_for_day("2026-07-15")[0]["location"] == "Actually Sunken City"


def test_events_for_day_includes_nonsocial_date(temp_db_path):
    # A date is shown on its day even if the AI said not-social with high
    # confidence — resolved date is an independent inclusion reason.
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev-ns", "Date — museum", "2026-07-15T14:00:00-06:00",
                             "2026-07-15T16:00:00-06:00", is_date=True)
    db.set_event_classification("ev-ns", False, 0.95)
    rows = db.get_events_for_day("2026-07-15")
    assert [r["gcal_event_id"] for r in rows] == ["ev-ns"]
    assert rows[0]["is_date"] is True


def test_get_date_events_range(temp_db_path):
    db = _db(temp_db_path)
    _seed_date_event(db)                                       # in range
    _seed_date_event(db, ev_id="ev-date2", day="2026-07-25")   # outside
    db.add_manual_social_event("manual:d2", "Drinks", "2026-07-16T12:00:00",
                               "2026-07-16T13:00:00", 40.0, location="Wine Bar", is_date=True)
    db.set_event_overrides("ev-date", {"user_removed": True})  # didn't happen
    rows = db.get_date_events_range("2026-07-14", "2026-07-20")
    assert [r["gcal_event_id"] for r in rows] == ["manual:d2"]
    assert rows[0]["location"] == "Wine Bar" and rows[0]["amount"] == 40.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_database_v2.py -k "excludes_resolved_dates or includes_dates_with_fields or nonsocial_date or get_date_events_range" -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Shared expressions (module-level, next to `_social_true`):

```python
def _resolved_date_expr():
    """Resolved date flag — user verdict wins over the title rule."""
    return "COALESCE(user_is_date, is_date)"


_RESOLVED_LOCATION = "COALESCE(NULLIF(user_location, ''), location)"
```

`get_social_events_range`: add to the WHERE (docstring: append "Excludes resolved DATES — the social floor means non-date social (2026-07-30 date-tracking spec, decision 4)."):

```sql
                  AND {_resolved_date_expr()} IS NOT TRUE
```

`get_events_for_day`: add to the SELECT list

```sql
                       COALESCE(user_is_date, is_date) IS TRUE AS is_date,
                       COALESCE(NULLIF(user_location, ''), location) AS location,
```

Wait — `IS TRUE` in a SELECT works on Postgres but SQLite returns 0/1 (fine, cast below). SQLite accepts `expr IS TRUE` from 3.23. Use exactly:

```python
            f"""SELECT gcal_event_id, COALESCE(user_title, title) AS title,
                       COALESCE(user_is_social, is_social) AS is_social, start_at, end_at,
                       source, amount,
                       ({_resolved_date_expr()} IS TRUE) AS is_date,
                       {_RESOLVED_LOCATION} AS location,
                       CASE WHEN user_is_social IS NULL AND user_removed IS NOT TRUE
                                 AND confidence IS NOT NULL AND confidence < {p}
                            THEN {social_true} ELSE {social_false} END AS uncertain
                FROM calendar_events
                WHERE user_removed IS NOT TRUE
                  AND substr(start_at, 1, 10) = {p}
                  AND (
                        COALESCE(user_is_social, is_social) = {social_true}
                        OR (user_is_social IS NULL AND confidence IS NOT NULL AND confidence < {p})
                        OR {_resolved_date_expr()} IS TRUE
                      )
                ORDER BY start_at"""
```

and extend `_social_rows_with_uncertain`'s cast loop to also cast `is_date` (`r["is_date"] = bool(r["is_date"])`).

New function:

```python
def get_date_events_range(start_day, end_day):
    """Resolved-date rows in [start_day, end_day] by end_at day — the same
    'has this occurred' boundary social counting uses — excluding
    user_removed. The dates series is unscored (not in METRICS); this feeds
    the Insights dates panel and date-kind spend items only."""
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""SELECT gcal_event_id, COALESCE(user_title, title) AS title,
                       start_at, end_at, source, amount,
                       {_RESOLVED_LOCATION} AS location
                FROM calendar_events
                WHERE user_removed IS NOT TRUE
                  AND {_resolved_date_expr()} IS TRUE
                  AND substr(end_at, 1, 10) >= {p} AND substr(end_at, 1, 10) <= {p}
                ORDER BY start_at""",
            (start_day, end_day),
        )
        return [dict(r) for r in c.fetchall()]
```

- [ ] **Step 4: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: PASS. If an existing test asserted a date-titled event counts as social, widen it per the new rule — but none should (no fixtures use "date" in titles).

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_v2.py
git commit -m "feat(db): dates excluded from social, date range query, resolved location"
```

---

### Task 4: Calendar scan passes location + is_date

**Files:**
- Modify: `jobs/scan_calendar.py:31-35`
- Test: `tests/test_scan_calendar.py`

**Interfaces:**
- Consumes: `metrics.title_is_date` (Task 1), `upsert_calendar_event(..., location=, is_date=)` (Task 2). `calendar_service.get_events_range` already returns `location` per event.
- Produces: every scanned event row carries `location` and rule-derived `is_date`.

- [ ] **Step 1: Write the failing test**

Follow `tests/test_scan_calendar.py`'s existing mocking style (it stubs `calendar_service.get_events_range` and `ai_metrics.classify_social_event` — copy the fixture/monkeypatch idiom from a neighboring test verbatim, including the event-dict shape with `event_id`, `title`, `start_datetime`, `end_datetime`, `description`, `location`, `attendees`, `recurring_event_id`):

```python
def test_scan_stores_location_and_date_flag(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_calendar
    events = [_event(event_id="ev1", title="Date night", location="Bar Dough"),
              _event(event_id="ev2", title="Trivia", location="")]
    # (use this file's existing event-builder/stub helpers; if there is no
    # _event helper, build the two dicts inline with every key the real
    # calendar_service returns)
    monkeypatch.setattr(scan_calendar.calendar_service, "get_events_range", lambda days_back: events)
    monkeypatch.setattr(scan_calendar.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_calendar.ai_metrics, "classify_social_event",
                        lambda *a, **k: {"is_social": True, "confidence": 0.9})
    scan_calendar.run()
    ev1 = db.get_event("ev1")
    assert bool(ev1["is_date"]) is True and ev1["location"] == "Bar Dough"
    ev2 = db.get_event("ev2")
    assert not ev2["is_date"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scan_calendar.py::test_scan_stores_location_and_date_flag -v`
Expected: FAIL — `is_date` is None / location None

- [ ] **Step 3: Implement**

In `jobs/scan_calendar.py`, add `import metrics` to the imports and extend the upsert call:

```python
            db.upsert_calendar_event(
                ev["event_id"], ev["title"], ev["start_datetime"], ev["end_datetime"],
                recurring_event_id=ev.get("recurring_event_id"),
                location=ev.get("location") or None,
                is_date=metrics.title_is_date(ev["title"]),
            )
```

- [ ] **Step 4: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobs/scan_calendar.py tests/test_scan_calendar.py
git commit -m "feat: calendar scan stores location and title-rule date flag"
```

---

### Task 5: Routes — create/patch date + location

**Files:**
- Modify: `app/routes.py:175-223` (`SocialCreate`, `SocialPatch`, `post_social`, `patch_social`)
- Test: `tests/test_api_routes.py`

**Interfaces:**
- Consumes: Task 2 (`add_manual_social_event(..., location=, is_date=)`, `set_event_overrides` new keys), Task 3 (`get_events_for_day` fields flow into `/api/today`).
- Produces: `POST /api/social` accepts optional `location` (str ≤300) and `is_date` (bool); `PATCH /api/social/{id}` accepts optional `is_date` (→ `user_is_date`) and `location` (→ `user_location`), model_fields_set convention.

- [ ] **Step 1: Write the failing tests**

```python
def test_post_social_with_date_and_location(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    resp = client.post("/api/social", json={
        "name": "Drinks", "date": "2026-07-15", "amount": 40.0,
        "location": "Wine Bar", "is_date": True,
    })
    assert resp.status_code == 200
    ev_id = resp.json()["gcal_event_id"]
    row = db.get_event(ev_id)
    assert bool(row["user_is_date"]) is True and row["location"] == "Wine Bar"
    # And it shows on its day with the resolved fields.
    day = client.get("/api/today?date=2026-07-15").json()
    assert day["social_events"][0]["is_date"] is True
    assert day["social_events"][0]["location"] == "Wine Bar"


def test_patch_social_date_and_location_overrides(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    db.upsert_calendar_event("ev-p1", "Dinner", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00", location="Old Place")
    db.set_event_classification("ev-p1", True, 0.9)
    assert client.patch("/api/social/ev-p1",
                        json={"is_date": True, "location": "New Place"}).status_code == 200
    row = db.get_event("ev-p1")
    assert bool(row["user_is_date"]) is True
    assert row["user_location"] == "New Place"
    # Explicit null clears the override (model_fields_set convention).
    assert client.patch("/api/social/ev-p1", json={"is_date": None}).status_code == 200
    assert db.get_event("ev-p1")["user_is_date"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_routes.py -k "post_social_with_date or date_and_location_overrides" -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
class SocialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    date: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0)
    location: Optional[str] = Field(default=None, max_length=300)
    is_date: Optional[bool] = None


class SocialPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    is_social: Optional[bool] = None
    amount: Optional[float] = Field(default=None, ge=0)
    # "This occurrence didn't happen" — distinct from is_social ("this event
    # type isn't social"). See the granularity spec for why the two were
    # conflated before and why that conflation poisoned the classifier.
    removed: Optional[bool] = None
    # Date tag + place (2026-07-30 date-tracking spec) — user columns.
    is_date: Optional[bool] = None
    location: Optional[str] = Field(default=None, max_length=300)
```

`post_social`: pass through and echo:

```python
    db.add_manual_social_event(event_id, body.name, start_at, end_at, body.amount,
                               location=body.location, is_date=bool(body.is_date))
    return {
        "gcal_event_id": event_id, "title": body.name,
        "start_at": start_at, "end_at": end_at,
        "source": "manual", "amount": body.amount,
        "location": body.location, "is_date": bool(body.is_date),
    }
```

`patch_social`: two more mappings in the `provided` block:

```python
    if "is_date" in provided:
        updates["user_is_date"] = body.is_date
    if "location" in provided:
        updates["user_location"] = body.location
```

- [ ] **Step 4: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routes.py tests/test_api_routes.py
git commit -m "feat(api): date tag and location on social create/patch"
```

---

### Task 6: Scorecard wiring — dates spend kind, insights dates block

**Files:**
- Modify: `app/scorecard.py` — `_spend_by_service`, `scorecard_for_week`, `spend`, `week_days`, `insights`; new `_dates_summary` helper
- Test: `tests/test_scorecard.py`

**Interfaces:**
- Consumes: `db.get_date_events_range` (Task 3), `_social_counts` (exists — occurred/manual gate), `metrics.week_bounds`.
- Produces:
  - Spend surfaces gain kind `"date"`, service `"Dates"`; social figures exclude dates automatically (query-level).
  - `insights(weeks)` payload gains `"dates": {"weekly": [{"week_start": iso, "count": int}...oldest-first, PATTERN_WEEKS long], "count": int, "total_spend": float, "avg_spend": float, "top_places": [{"place": str, "count": int, "spend": float}... max 5]}`.
  - `week_days` and `today_snapshot` day items include date events as kind `"date"` (they no longer arrive via the social query).

- [ ] **Step 1: Write the failing tests**

```python
def _seed_date(db, ev_id, day, amount, location):
    db.upsert_calendar_event(ev_id, "Date night", f"{day}T19:00:00-06:00",
                             f"{day}T21:00:00-06:00", location=location, is_date=True)
    db.set_event_classification(ev_id, True, 0.9)
    if amount is not None:
        db.set_event_overrides(ev_id, {"amount": amount})


def test_scorecard_date_spend_separated_from_social(temp_db_path):
    import database as db
    from app.scorecard import scorecard_for_week
    db.seed_default_targets()
    _seed_date(db, "sd1", "2026-07-15", 60.0, "Bar Dough")
    db.upsert_calendar_event("sp1", "Trivia", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00")
    db.set_event_classification("sp1", True, 0.9)
    db.set_event_overrides("sp1", {"amount": 20.0})
    card = scorecard_for_week(date(2026, 7, 13))
    assert card["metrics"]["social"]["count"] == 1          # the date doesn't count
    assert card["social_spend"] == 20.0                     # or spend
    assert card["dates_spend"] == 60.0
    rows = {(r["kind"], r["service"]): r["amount"] for r in card["spend_by_service"]}
    assert rows[("date", "Dates")] == 60.0
    assert rows[("social", "Social")] == 20.0


def test_week_days_date_items(temp_db_path):
    import database as db
    from app.scorecard import week_days
    db.seed_default_targets()
    _seed_date(db, "sd2", "2026-07-15", 60.0, "Bar Dough")
    out = week_days(date(2026, 7, 13))
    day = next(d for d in out["days"] if d["date"] == "2026-07-15")
    kinds = [i["kind"] for i in day["items"]]
    assert kinds == ["date"]
    assert day["total"] == 60.0


def test_insights_dates_block(temp_db_path):
    import database as db
    from app.scorecard import insights
    db.seed_default_targets()
    import datetime as dt
    import metrics
    # A day inside a completed recent week, so the 8-week insights window
    # is guaranteed to contain all three seeds.
    monday = metrics.week_bounds(dt.date.today())[0] - timedelta(weeks=2)
    d1 = monday.isoformat()
    _seed_date(db, "si1", d1, 60.0, "Bar Dough")
    _seed_date(db, "si2", (monday + timedelta(days=2)).isoformat(), 30.0, "Bar Dough")
    _seed_date(db, "si3", (monday + timedelta(days=3)).isoformat(), None, "Museum")
    out = insights(12)["dates"]
    assert out["count"] == 3
    assert out["total_spend"] == 90.0
    assert out["avg_spend"] == 45.0                          # 90 / 2 dates WITH amounts
    assert out["top_places"][0] == {"place": "Bar Dough", "count": 2, "spend": 90.0}
    assert out["top_places"][1] == {"place": "Museum", "count": 1, "spend": 0}
    assert sum(w["count"] for w in out["weekly"]) == 3
```

(Define the helper inline with the real idiom: `monday = metrics.week_bounds(datetime.date.today())[0] - timedelta(weeks=2)` — `import metrics` and `datetime` at the top of the test file already exist or add them.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scorecard.py -k "date_spend_separated or week_days_date_items or insights_dates_block" -v`
Expected: FAIL

- [ ] **Step 3: Implement**

`_spend_by_service` gains a `dates_spend` param mirroring `social_spend`:

```python
def _spend_by_service(orders: list, rides: list, social_spend: float, dates_spend: float = 0.0) -> list:
    ...
    if social_spend:
        by_service[("social", "Social")] = social_spend
    if dates_spend:
        by_service[("date", "Dates")] = dates_spend
```

`scorecard_for_week` — after the social lines:

```python
    dates = [e for e in db.get_date_events_range(ws.isoformat(), we.isoformat()) if _social_counts(e)]
    card["dates_count"] = len(dates)
    card["dates_spend"] = round(sum(e["amount"] or 0 for e in dates), 2)
    ...
    card["spend_by_service"] = _spend_by_service(orders, rides, card["social_spend"], card["dates_spend"])
```

`week_days` — add a dates source next to the social one:

```python
    dates = [e for e in db.get_date_events_range(start, end) if _social_counts(e)]
    ...
        for e in dates:
            if e["end_at"][:10] == d_iso:
                amount = e["amount"] or 0
                items.append({"kind": "date", "service": "Dates", "label": e["title"],
                              "at": e["end_at"], "amount": round(amount, 2), "is_work": False})
                day_total += amount
```

`spend()` — same pattern: fetch `dates` once for the window, split per week on `e["end_at"][:10]`, weekly rows gain `"dates": <total>`, `by_service` accumulates `("date", "Dates")` with raw amounts, and itemized entries append `{"kind": "date", "service": "Dates", "label": e["title"], "at": e["end_at"], "amount": ...}` for dates with amounts.

New `_dates_summary` + `insights` wiring:

```python
def _dates_summary(weeks: int) -> dict:
    """Unscored dates series for the Insights panel (2026-07-30 spec):
    weekly counts (completed weeks + current, oldest-first), totals, and
    top places by resolved location. avg_spend divides by dates that HAVE
    an amount — a date with no recorded cost shouldn't drag the average."""
    this_monday = metrics.week_bounds(_local_today())[0]
    week_starts = [this_monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    window_start = week_starts[0].isoformat()
    window_end = metrics.week_bounds(week_starts[-1])[1].isoformat()
    dates = [e for e in db.get_date_events_range(window_start, window_end) if _social_counts(e)]

    weekly = []
    for ws in week_starts:
        we = metrics.week_bounds(ws)[1]
        n = sum(1 for e in dates if ws.isoformat() <= e["end_at"][:10] <= we.isoformat())
        weekly.append({"week_start": ws.isoformat(), "count": n})

    with_amount = [e for e in dates if e["amount"]]
    total = round(sum(e["amount"] for e in with_amount), 2)
    places: dict = {}
    for e in dates:
        place = (e["location"] or "").strip()
        if not place:
            continue
        c, s = places.get(place, (0, 0.0))
        places[place] = (c + 1, s + (e["amount"] or 0))
    top_places = [{"place": p, "count": c, "spend": round(s, 2)}
                  for p, (c, s) in places.items()]
    top_places.sort(key=lambda r: (r["count"], r["spend"]), reverse=True)

    return {
        "weekly": weekly,
        "count": len(dates),
        "total_spend": total,
        "avg_spend": round(total / len(with_amount), 2) if with_amount else 0,
        "top_places": top_places[:5],
    }
```

In `insights()`, add `"dates": _dates_summary(PATTERN_WEEKS),` to the returned dict.

- [ ] **Step 4: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: PASS. Existing spend/week_days tests may assert exact item lists — extend expectations only where a seeded event legitimately became a date (none should; no fixture titles contain "date").

- [ ] **Step 5: Commit**

```bash
git add app/scorecard.py tests/test_scorecard.py
git commit -m "feat: dates series — spend kind, weekly counts, top places"
```

---

### Task 7: Telegram/reflection regression lock

**Files:**
- Test: `tests/test_weekly_push.py`, `tests/test_api_routes.py`

**Interfaces:**
- Consumes: `format_scorecard_text` (exists in `jobs/weekly_push.py`), `/api/reflection` route, Task 6's `dates_spend`/`dates_count` card keys.

The by-construction rule (dates not in `METRICS` ⇒ absent from both outbound AI/Telegram surfaces) gets a lock so a future card change can't silently leak it.

- [ ] **Step 1: Write the tests (they should pass immediately — they're locks, not TDD)**

In `tests/test_weekly_push.py`, following its existing card-building style (copy a neighboring test's card fixture):

```python
def test_scorecard_text_contains_no_date_lines(temp_db_path):
    # Build a card the way the existing format tests do, but seed a date
    # (dates_count/dates_spend present on the card). The rendered text must
    # not mention dates — they're METRICS-derived lines only.
    import database as db
    from app.scorecard import scorecard_for_week
    from jobs.weekly_push import format_scorecard_text
    db.seed_default_targets()
    db.upsert_calendar_event("push-d", "Date night", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00", location="Bar Dough", is_date=True)
    db.set_event_classification("push-d", True, 0.9)
    text = format_scorecard_text(scorecard_for_week(date(2026, 7, 13)))
    assert "date" not in text.lower().replace("update", "")
    assert "Bar Dough" not in text
```

In `tests/test_api_routes.py` (match the file's existing reflection-test mocking of `ai_metrics` — copy the monkeypatch idiom from the existing reflection test):

```python
def test_reflection_prompt_sees_no_dates(temp_db_path, monkeypatch):
    client = _client(temp_db_path)
    import database as db
    import ai_metrics
    db.seed_default_targets()
    db.upsert_calendar_event("refl-d", "Date night", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00", location="Bar Dough", is_date=True)
    db.set_event_classification("refl-d", True, 0.9)
    captured = {}
    def fake_reflect(card, noticings):
        captured["card"] = str(card); captured["noticings"] = str(noticings)
        return "a reflection"
    monkeypatch.setattr(ai_metrics, "weekly_reflection", fake_reflect)
    client.get("/api/reflection")
    blob = (captured.get("card", "") + captured.get("noticings", "")).lower()
    assert "bar dough" not in blob and "date night" not in blob
```

(Adapt the exact `ai_metrics` function name and call signature to what `/api/reflection` really calls — read the route first; the assertion style is the point.)

- [ ] **Step 2: Run them**

Run: `pytest tests/test_weekly_push.py -k no_date_lines tests/test_api_routes.py -k sees_no_dates -v`
Expected: PASS immediately. If either FAILS, a leak exists — stop and fix the leak, not the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_weekly_push.py tests/test_api_routes.py
git commit -m "test: lock dates out of Telegram push and reflection prompt"
```

---

### Task 8: Frontend — date chip, forms, patch builder

**Files:**
- Modify: `frontend/src/components/StatusChip.tsx`, `frontend/src/screens/Today.tsx`, `frontend/src/lib.ts`, `frontend/src/styles.css`
- Test: `frontend/src/lib.test.ts`

**Interfaces:**
- Consumes: `/api/today` social_events rows now carry `is_date: boolean`, `location: string | null`; POST/PATCH `/social` fields (Task 5).
- Produces: `StatusChip` gains `{ kind: "date" }`; `buildSocialPatch` handles date/location; add + edit forms gain a Date checkbox and Where input.

- [ ] **Step 1: Write the failing lib tests**

`buildSocialPatch` currently takes `SocialEditState` (see `lib.ts:78`) — extend state with `loadedIsDate: boolean`, `loadedLocation: string | null`, `isDate: boolean`, `locationText: string`, and the patch with `is_date?: boolean | null`, `location?: string | null`. Same only-if-changed semantics as title/amount:

```typescript
describe("buildSocialPatch date/location", () => {
  const base = {
    loadedTitle: "Dinner", loadedIsSocial: true, loadedAmount: null,
    loadedIsDate: false, loadedLocation: null,
    title: "Dinner", isSocial: true, amountText: "",
    isDate: false, locationText: "",
  };
  it("emits is_date only when changed", () => {
    expect(buildSocialPatch({ ...base, isDate: true })).toEqual({ is_date: true });
    expect(buildSocialPatch(base)).toEqual({});
  });
  it("emits location only when changed, empty clears to null", () => {
    expect(buildSocialPatch({ ...base, locationText: "Bar Dough" })).toEqual({ location: "Bar Dough" });
    expect(buildSocialPatch({ ...base, loadedLocation: "Bar Dough", locationText: "" }))
      .toEqual({ location: null });
    expect(buildSocialPatch({ ...base, loadedLocation: "Bar Dough", locationText: "Bar Dough" }))
      .toEqual({});
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm test -- --run`
Expected: FAIL — type errors / missing keys (vitest may surface as TS build errors in the test run; either failure mode counts)

- [ ] **Step 3: Implement lib.ts**

Extend `SocialEditState` and `SocialPatch` types with the fields above, and in `buildSocialPatch` add (mirroring the existing amount logic exactly — read it first and copy its trimming/null conventions):

```typescript
  if (state.isDate !== state.loadedIsDate) patch.is_date = state.isDate;
  const loc = state.locationText.trim();
  const loadedLoc = state.loadedLocation ?? "";
  if (loc !== loadedLoc) patch.location = loc === "" ? null : loc;
```

Run: `cd frontend && npm test -- --run` → PASS.

- [ ] **Step 4: StatusChip + Today.tsx**

`StatusChip.tsx`: add to the Props union `| { kind: "date" }` and, before the `social` branch:

```tsx
  if (props.kind === "date") {
    return <span className="chip chip-accent">date</span>;
  }
```

(Same visual family as the social chip — the label text differentiates; update the vocabulary comment to list `date` as the sanctioned 2026-07-30 addition.)

`Today.tsx`:
- `SocialEvent` interface gains `is_date: boolean; location: string | null;`
- Chip precedence in `socialEntries`: `removed` > `uncertain` > `is_date` ? `<StatusChip kind="date" />` : `<StatusChip kind="social" />`.
- Add-social form: after the amount input, a `Where (optional)` text input bound to new `socialLocation` state and a labeled checkbox `Date` bound to `socialIsDate`; `submitAddSocial` sends `location: socialLocation.trim() || undefined, is_date: socialIsDate || undefined`; `openAddSocial` resets both.
- Edit form: mirror — `editIsDate`/`editLocation` state seeded in `openEditSocial` from `e.is_date`/`e.location`, `editLoaded` gains `isDate`/`location`, `saveEditSocial` passes the extended state to `buildSocialPatch`.
- The date row's meta may show the place: extend the row `meta` for events to `dayLogRowMeta(...)` output unchanged (place display lives in the edit form and Insights — do NOT crowd the row).

- [ ] **Step 5: Verify + commit**

Run: `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`
Expected: clean.

```bash
git add frontend/src/components/StatusChip.tsx frontend/src/screens/Today.tsx frontend/src/lib.ts frontend/src/lib.test.ts frontend/src/styles.css
git commit -m "feat(frontend): date chip, date/location on event forms"
```

---

### Task 9: Frontend — Insights dates panel

**Files:**
- Modify: `frontend/src/screens/Insights.tsx`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: `insights` payload `dates` block (Task 6): `{weekly: {week_start, count}[], count, total_spend, avg_spend, top_places: {place, count, spend}[]}`; `money()` from lib.

- [ ] **Step 1: Implement the panel**

In `Insights.tsx`, extend `InsightsData` with the `dates` block type, then render a new section after the Patterns section (hide entirely when `insights.dates.count === 0` — secondary surfaces fail/empty quietly):

```tsx
{insights && insights.dates.count > 0 && (
  <section>
    <h2 className="section-label">Dates · last 8 weeks</h2>
    <div className="card dates-panel">
      <div className="dates-stats">
        <div><strong>{insights.dates.count}</strong> dates</div>
        <div><strong>{money(insights.dates.total_spend)}</strong> total</div>
        <div><strong>{money(insights.dates.avg_spend)}</strong> avg</div>
      </div>
      <svg className="dates-bars" viewBox="0 0 360 48" role="img"
           aria-label={`Dates per week, last ${insights.dates.weekly.length} weeks`}
           preserveAspectRatio="xMidYMid meet">
        {(() => {
          const weekly = insights.dates.weekly;
          const max = Math.max(1, ...weekly.map((w) => w.count));
          const bw = 356 / weekly.length;
          return weekly.map((w, i) => {
            const h = (w.count / max) * 40;
            return <rect key={w.week_start} x={2 + i * bw + 1} y={44 - h}
                         width={Math.max(bw - 2, 1)} height={Math.max(h, w.count > 0 ? 2 : 0)}
                         rx="1.5" className="dates-bar" />;
          });
        })()}
      </svg>
      {insights.dates.top_places.length > 0 && (
        <ul className="dates-places">
          {insights.dates.top_places.map((p) => (
            <li key={p.place}>
              <span>{p.place}</span>
              <span className="muted">{p.count}× {p.spend > 0 ? `· ${money(p.spend)}` : ""}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  </section>
)}
```

`styles.css` — next to the other Insights blocks, tokens only (both themes inherit):

```css
.dates-panel { display: flex; flex-direction: column; gap: 12px; }
.dates-stats { display: flex; gap: 16px; font-size: 0.85rem; color: var(--ink-2); }
.dates-stats strong { color: var(--ink); font-size: 1rem; }
.dates-bars { width: 100%; height: auto; }
.dates-bar { fill: var(--accent); }
.dates-places { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.dates-places li { display: flex; justify-content: space-between; font-size: 0.85rem; }
.dates-places .muted { color: var(--muted); }
```

(Adapt class/token names to what `styles.css` actually defines — `--ink-2`, `--muted`, `--accent` all exist; verify before inventing new ones. The bar uses the UI accent, not a `--chart-*` token: single-series, no colorblind-separation pairing to validate.)

- [ ] **Step 2: Verify + commit**

Run: `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`
Expected: clean. Manual look: seed a date via the UI ("Date" checkbox on add event), check the Insights panel renders in both themes.

```bash
git add frontend/src/screens/Insights.tsx frontend/src/styles.css
git commit -m "feat(frontend): Insights dates panel — counts, spend, top places"
```

---

### Task 10: Full verification + repo guide update

**Files:**
- Modify: `CLAUDE.md` — REQUIRES USER CONFIRMATION before editing

- [ ] **Step 1: Run everything**

`pytest tests/ -v`; `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`. All green before proceeding.

- [ ] **Step 2: Propose CLAUDE.md changes and WAIT for confirmation**

- Metrics section: add dates to the "not a metric" family (alongside rides and bank): unscored, rule-detected (`metrics.title_is_date` — regex, no AI), excluded from social, absent from Telegram/reflection by construction.
- Override + Learning section: note `is_date`/`user_is_date` and `location`/`user_location` follow rules 1–3 but have NO example-feed loop (rule-based, nothing to teach).
- `calendar_events` table row: document the four new columns and both resolutions.

- [ ] **Step 3: Commit (after confirmation)**

```bash
git add CLAUDE.md
git commit -m "docs: date tracking + places in the repo guide"
```
