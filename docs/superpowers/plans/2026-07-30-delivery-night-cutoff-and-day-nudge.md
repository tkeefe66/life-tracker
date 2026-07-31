# Delivery Night Cutoff + Day Nudge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the 4 AM night cutoff to delivery orders and add a ±1-day manual nudge (new `user_date` column) to both deliveries and rides, surfaced as an inline action strip in the Day log.

**Architecture:** Bucketing resolves in SQL — `COALESCE(user_date, <effective-date expr>)` — inside the two range queries, exposed as computed `day` and `auto_day` fields so `app/scorecard.py` stops re-deriving dates in Python. Two PATCH routes validate the nudge against the automatic day via a pure `metrics.py` helper. The frontend expands an inline action strip under delivery/ride rows (ride tap no longer instantly toggles work).

**Tech Stack:** FastAPI + pydantic v2, SQLite/Postgres dual-dialect raw SQL, React + TypeScript (Vite), pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-07-30-delivery-night-cutoff-and-day-nudge-design.md`

## Global Constraints

- All SQL lives in `database.py`; all env reads in `config.py`; no Claude calls involved here.
- `user_date` is a USER column: `jobs/scan_gmail.py` must never read or write it (Override pattern rule 3). No scan changes in this plan at all.
- `ordered_at` and `ride_at` are never rewritten; the cutoff and override are query-time only. The ingest dedupe cluster key `(service, substr(ordered_at,1,10), subject)` in `find_delivery_order` stays on the RAW day — do not touch it.
- Valid `user_date` values are exactly `{automatic day − 1, automatic day + 1}`; storing the automatic day itself means `NULL` (override cleared). Future days are rejected with 400.
- Date comparisons on ISO `YYYY-MM-DD` strings are lexicographic — safe in both SQL and TS.
- The effective-date SQL must keep using wall-clock substrings (`_effective_date_expr`), never `date(ts, '-4 hours')` — SQLite normalizes offset-bearing strings through UTC (see the 2026-07-29 spec).
- Verify with the real suites: `pytest tests/ -v` and, in `frontend/`, `npm test -- --run && npx tsc --noEmit && npm run build`.
- Run `pytest` from the repo root with the venv active: `source venv/bin/activate`.

---

### Task 1: `user_date` columns + migrations

**Files:**
- Modify: `database.py` (schema + migration section, after the `is_cancellation` block near line 749)
- Test: `tests/test_database_v2.py`

**Interfaces:**
- Produces: `delivery_orders.user_date TEXT NULL`, `rides.user_date TEXT NULL` — later tasks read/write these.

- [ ] **Step 1: Write the failing test**

Follow the existing fixture style in `tests/test_database_v2.py` (fresh SQLite DB per test via `conftest.py`). Add:

```python
def test_user_date_columns_exist():
    # Migration adds a nullable user_date to both tables; a plain insert
    # leaves it NULL and the range queries tolerate it.
    db.add_delivery_order("ud-col-1", "Uber Eats", "2026-07-15T12:00:00-06:00", "Order", 10.0)
    with db._cursor() as c:
        cols = [r["name"] for r in c.execute("PRAGMA table_info(delivery_orders)").fetchall()]
        assert "user_date" in cols
        cols = [r["name"] for r in c.execute("PRAGMA table_info(rides)").fetchall()]
        assert "user_date" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_database_v2.py::test_user_date_columns_exist -v`
Expected: FAIL — `assert "user_date" in cols`

- [ ] **Step 3: Add the migrations**

In `database.py`'s migration section, directly after the `is_cancellation` block (the one ending `ALTER TABLE rides ADD COLUMN is_cancellation INTEGER`), add:

```python
        # user_date: nullable TEXT ('YYYY-MM-DD') on delivery_orders and
        # rides — the user's ±1-day nudge when the automatic night cutoff
        # got it wrong. A USER column: the scan never reads or writes it
        # (Override + Learning rule 3). Resolved as
        # COALESCE(user_date, <effective date>) inside the range queries;
        # NULL means "trust the automatic day", which is why moving an item
        # back to its automatic day stores NULL rather than the date.
        if USE_POSTGRES:
            c.execute("ALTER TABLE delivery_orders ADD COLUMN IF NOT EXISTS user_date TEXT")
            c.execute("ALTER TABLE rides ADD COLUMN IF NOT EXISTS user_date TEXT")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(delivery_orders)").fetchall()]
            if "user_date" not in cols:
                c.execute("ALTER TABLE delivery_orders ADD COLUMN user_date TEXT")
            cols = [r["name"] for r in c.execute("PRAGMA table_info(rides)").fetchall()]
            if "user_date" not in cols:
                c.execute("ALTER TABLE rides ADD COLUMN user_date TEXT")
```

Also add `user_date TEXT` to the `CREATE TABLE IF NOT EXISTS delivery_orders` and `rides` statements themselves (new installs shouldn't depend on the migration), keeping column order: append after the last column before `detected_at`/existing tail so existing `SELECT *`-free queries are unaffected.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_database_v2.py::test_user_date_columns_exist -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_v2.py
git commit -m "feat(db): user_date nudge column on delivery_orders and rides"
```

---

### Task 2: Delivery range query — cutoff + `day`/`auto_day` + helpers

**Files:**
- Modify: `database.py:886-895` (`get_delivery_orders_range`), plus new helpers next to it
- Test: `tests/test_database_v2.py`

**Interfaces:**
- Consumes: `_effective_date_expr(ts_expr)` (exists), `user_date` column (Task 1).
- Produces:
  - `get_delivery_orders_range(start_day, end_day)` rows gain `user_date`, `auto_day` (str, cutoff-only day), `day` (str, resolved day); filtering switches to resolved day.
  - `get_delivery_auto_day(order_id) -> str | None` (None = no such row).
  - `set_delivery_user_date(order_id, user_date) -> bool` (False = no such row; `user_date` may be None to clear).

- [ ] **Step 1: Write the failing tests**

```python
def test_delivery_night_cutoff_buckets_previous_day():
    # 12:49 AM belongs to the previous day; the trailing -06:00 offset is
    # wall-clock metadata, never a conversion instruction.
    db.add_delivery_order("cut-1", "Uber Eats", "2026-07-30T00:49:00-06:00", "Your order", 28.21)
    rows = db.get_delivery_orders_range("2026-07-29", "2026-07-29")
    assert len(rows) == 1
    assert rows[0]["day"] == "2026-07-29"
    assert rows[0]["auto_day"] == "2026-07-29"
    assert db.get_delivery_orders_range("2026-07-30", "2026-07-30") == []


def test_delivery_at_cutoff_stays_on_its_day():
    # 04:00 exactly is NOT before the cutoff.
    db.add_delivery_order("cut-2", "DoorDash", "2026-07-30T04:00:00-06:00", "Order", 12.0)
    assert db.get_delivery_orders_range("2026-07-30", "2026-07-30")[0]["day"] == "2026-07-30"


def test_delivery_user_date_wins_over_cutoff():
    db.add_delivery_order("cut-3", "Uber Eats", "2026-07-30T01:00:00-06:00", "Order", 15.0)
    row = db.get_delivery_orders_range("2026-07-29", "2026-07-29")[0]
    assert db.set_delivery_user_date(row["id"], "2026-07-30") is True
    assert db.get_delivery_orders_range("2026-07-29", "2026-07-29") == []
    got = db.get_delivery_orders_range("2026-07-30", "2026-07-30")[0]
    assert got["day"] == "2026-07-30"
    assert got["auto_day"] == "2026-07-29"   # automatic day still reported
    # Clearing restores the automatic day.
    db.set_delivery_user_date(row["id"], None)
    assert db.get_delivery_orders_range("2026-07-29", "2026-07-29")[0]["day"] == "2026-07-29"


def test_delivery_auto_day_helper():
    db.add_delivery_order("cut-4", "Uber Eats", "2026-07-30T02:00:00-06:00", "Order", 9.0)
    row_id = db.get_delivery_orders_range("2026-07-29", "2026-07-29")[0]["id"]
    assert db.get_delivery_auto_day(row_id) == "2026-07-29"
    assert db.get_delivery_auto_day(999999) is None
    assert db.set_delivery_user_date(999999, "2026-07-29") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_database_v2.py -k "delivery_night or delivery_at_cutoff or delivery_user_date or delivery_auto_day" -v`
Expected: FAIL — `KeyError: 'day'` / missing attribute errors

- [ ] **Step 3: Implement**

Replace `get_delivery_orders_range` and add the helpers beside it:

```python
def get_delivery_orders_range(start_day, end_day):
    """Buckets AND filters by the RESOLVED day: COALESCE(user_date, effective
    date of ordered_at) — the night cutoff (_effective_date_expr) plus the
    user's ±1-day nudge. Both the resolved `day` and the cutoff-only
    `auto_day` are exposed so callers (and the UI's nudge affordance) never
    re-derive the CASE logic. `ordered_at` itself is never rewritten."""
    p = _p()
    eff = _effective_date_expr("ordered_at")
    day_expr = f"COALESCE(user_date, {eff})"
    with _cursor() as c:
        c.execute(
            f"""SELECT id, gmail_message_id, service, subject, ordered_at, amount, user_date,
                       {eff} AS auto_day, {day_expr} AS day
                FROM delivery_orders
                WHERE {day_expr} >= {p} AND {day_expr} <= {p}
                ORDER BY ordered_at""",
            (start_day, end_day),
        )
        return [dict(r) for r in c.fetchall()]


def get_delivery_auto_day(order_id):
    """The order's automatic (cutoff-only) day, ignoring user_date — the
    anchor the ±1 nudge validation measures against. None = unknown id."""
    p = _p()
    eff = _effective_date_expr("ordered_at")
    with _cursor() as c:
        c.execute(f"SELECT {eff} AS auto_day FROM delivery_orders WHERE id = {p}", (order_id,))
        row = c.fetchone()
        return row["auto_day"] if row else None


def set_delivery_user_date(order_id, user_date):
    """user_date=None clears the override. Returns False for an unknown id
    so the route can 404."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE delivery_orders SET user_date = {p} WHERE id = {p}", (user_date, order_id))
        return c.rowcount > 0
```

- [ ] **Step 4: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: new tests PASS. Existing tests that assert on delivery-row keys or bucketing may fail — fix ONLY by widening expectations to the new fields/behavior (e.g. a test seeding a 1 AM order and expecting it on its raw day now expects the previous day). Do not weaken cutoff assertions.

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_v2.py
git commit -m "feat(db): night cutoff + user_date resolution for delivery orders"
```

---

### Task 3: Rides range query — `user_date` + `day`/`auto_day` + helpers

**Files:**
- Modify: `database.py:1259-1281` (`get_rides_range`), new helpers beside `set_ride_work_override` (~line 1240)
- Test: `tests/test_database_v2.py`

**Interfaces:**
- Consumes: `_ride_true_ts_expr()`, `_effective_date_expr()`, `user_date` column (Task 1).
- Produces:
  - `get_rides_range` rows gain `user_date`, `auto_day`, `day`; filtering switches to resolved day.
  - `get_ride_auto_day(ride_id) -> str | None`
  - `set_ride_user_date(ride_id, user_date) -> bool`

- [ ] **Step 1: Write the failing tests**

Use the same ride-seeding style as the existing effective-date tests in `tests/test_database_v2.py` (`db.add_ride(gmail_message_id, service, ride_at, ride_key, subject, amount, ...)` — copy the exact call signature from a neighboring test).

```python
def test_ride_user_date_wins_over_cutoff():
    db.add_ride("rud-1", "Uber", "2026-07-30T02:34:00-06:00",
                "2026-07-30T02:34:00", "Your trip", 27.82)
    row = db.get_rides_range("2026-07-29", "2026-07-29")[0]
    assert row["day"] == "2026-07-29" and row["auto_day"] == "2026-07-29"
    assert db.set_ride_user_date(row["id"], "2026-07-30") is True
    assert db.get_rides_range("2026-07-29", "2026-07-29") == []
    got = db.get_rides_range("2026-07-30", "2026-07-30")[0]
    assert got["day"] == "2026-07-30" and got["auto_day"] == "2026-07-29"


def test_ride_auto_day_helper():
    db.add_ride("rud-2", "Uber", "2026-07-30T02:00:00-06:00",
                "2026-07-30T02:00:00", "Your trip", 10.0)
    ride_id = db.get_rides_range("2026-07-29", "2026-07-29")[0]["id"]
    assert db.get_ride_auto_day(ride_id) == "2026-07-29"
    assert db.get_ride_auto_day(999999) is None
    assert db.set_ride_user_date(999999, "2026-07-29") is False
```

If `add_ride`'s real signature differs (keyword args, extra params), match the neighboring tests — the two seeded facts that matter are the ISO `ride_key` (02:34 true time) and the amounts.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_database_v2.py -k "ride_user_date or ride_auto_day" -v`
Expected: FAIL — `KeyError: 'day'` / missing attributes

- [ ] **Step 3: Implement**

In `get_rides_range`, build the resolved expression once and use it for SELECT + WHERE (docstring: append a sentence — "`user_date`, the ±1-day nudge, wins over the computed effective date; `auto_day` reports the cutoff-only day."):

```python
    p = _p()
    ts_expr = _ride_true_ts_expr()
    eff_date_expr = _effective_date_expr(ts_expr)
    day_expr = f"COALESCE(user_date, {eff_date_expr})"
    with _cursor() as c:
        c.execute(
            f"""SELECT id, service, ride_at, ride_key, subject, amount, ai_is_work, ai_confidence,
                       user_is_work, is_cancellation, user_date, {ts_expr} AS ride_time,
                       {eff_date_expr} AS auto_day, {day_expr} AS day
                FROM rides
                WHERE {day_expr} >= {p} AND {day_expr} <= {p}
                ORDER BY {ts_expr}""",
            (start_day, end_day),
        )
        return _ride_bool_rows(c.fetchall())
```

Helpers, next to `set_ride_work_override`:

```python
def get_ride_auto_day(ride_id):
    """The ride's automatic (cutoff-only) day from its TRUE timestamp,
    ignoring user_date — the anchor for ±1 nudge validation. None = unknown id."""
    p = _p()
    eff = _effective_date_expr(_ride_true_ts_expr())
    with _cursor() as c:
        c.execute(f"SELECT {eff} AS auto_day FROM rides WHERE id = {p}", (ride_id,))
        row = c.fetchone()
        return row["auto_day"] if row else None


def set_ride_user_date(ride_id, user_date):
    """user_date=None clears the override. Returns False for an unknown id
    so the route can 404."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE rides SET user_date = {p} WHERE id = {p}", (user_date, ride_id))
        return c.rowcount > 0
```

- [ ] **Step 4: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: PASS (widen any key-set assertions on ride rows to include the new fields — same rule as Task 2).

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_v2.py
git commit -m "feat(db): user_date nudge resolution for rides"
```

---

### Task 4: `app/scorecard.py` groups by the resolved `day`

**Files:**
- Modify: `app/scorecard.py:114,160,165,238,247`
- Test: `tests/test_scorecard.py`

**Interfaces:**
- Consumes: `day` field from both range queries (Tasks 2–3).
- Produces: no signature changes — `week_days`, `spend`, `insights`, `counts_for_week` outputs re-bucket automatically.

- [ ] **Step 1: Write the failing test**

```python
import datetime


def test_monday_1am_delivery_counts_previous_week():
    # Match this file's existing fixture usage (fresh DB via conftest). A
    # Monday 01:00 order counts in the PREVIOUS week (as its Sunday), not
    # the week starting that Monday.
    db.add_delivery_order("wk-1", "Uber Eats", "2026-07-27T01:00:00-06:00", "Order", 20.0)
    prev = scorecard.counts_for_week(datetime.date(2026, 7, 20))
    cur = scorecard.counts_for_week(datetime.date(2026, 7, 27))
    assert prev["delivery"] == 1
    assert cur["delivery"] == 0


def test_week_days_groups_delivery_by_resolved_day():
    db.add_delivery_order("wk-2", "Uber Eats", "2026-07-30T00:49:00-06:00", "Order", 28.21)
    out = scorecard.week_days(datetime.date(2026, 7, 27))
    by_date = {d["date"]: d for d in out["days"]}
    assert any(i["kind"] == "delivery" for i in by_date["2026-07-29"]["items"])
    assert not any(i["kind"] == "delivery" for i in by_date["2026-07-30"]["items"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scorecard.py -k "monday_1am or resolved_day" -v`
Expected: `counts_for_week` may already PASS (its filtering happens inside the Task 2 query — if so, keep the test as a regression lock); `week_days` FAILS (still grouping on `ordered_at[:10]`).

- [ ] **Step 3: Implement**

Five line edits, all `ordered_at[:10]`/Python-effective-date → the SQL-resolved `day`:

- Line 114 (`_date_lists`): `"delivery": [o["day"] for o in db.get_delivery_orders_range(s, e)],`
- Line 160 (`spend`): `week_orders = [o for o in orders if ws_iso <= o["day"] <= we_iso]`
- Line 165 (`spend`): `week_rides = [r for r in rides if ws_iso <= r["day"] <= we_iso]` — and trim the comment above it to: `# Per-week split on the SQL-resolved day (night cutoff + user nudge) — must agree with get_rides_range's outer filter.`
- Line 238 (`week_days`): `if o["day"] == d_iso:`
- Line 247 (`week_days`): `if r["day"] == d_iso:` — replace the three-line comment above with `# Grouped by the SQL-resolved day — cutoff + user nudge, see get_rides_range.`

`spend()` items keep `"at": o["ordered_at"]` / `r["ride_time"]` (display time is unchanged by design, spec decision 8).

- [ ] **Step 4: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/scorecard.py tests/test_scorecard.py
git commit -m "feat: scorecard groups deliveries and rides by resolved day"
```

---

### Task 5: Nudge validation helper + PATCH routes

**Files:**
- Modify: `metrics.py` (new pure function after `effective_date`), `app/routes.py:237-276`
- Test: `tests/test_metrics.py`, `tests/test_api_routes.py`

**Interfaces:**
- Consumes: `db.get_delivery_auto_day`, `db.set_delivery_user_date`, `db.get_ride_auto_day`, `db.set_ride_user_date` (Tasks 2–3); `_local_today()` (exists in routes.py).
- Produces:
  - `metrics.nudge_user_date(auto_day: date, requested: date, today: date) -> str | None` — raises `ValueError` on invalid.
  - `PATCH /api/deliveries/{order_id}` body `{"day": "YYYY-MM-DD"}` → `{"ok": True}`.
  - `PATCH /api/rides/{ride_id}` body now accepts optional `is_work` and/or optional `day`.

- [ ] **Step 1: Write the failing metrics tests**

```python
def test_nudge_user_date():
    from datetime import date
    today = date(2026, 7, 30)
    auto = date(2026, 7, 29)
    # ±1 stores the date; the auto day itself clears (None).
    assert metrics.nudge_user_date(auto, date(2026, 7, 30), today) == "2026-07-30"
    assert metrics.nudge_user_date(auto, date(2026, 7, 28), today) == "2026-07-28"
    assert metrics.nudge_user_date(auto, auto, today) is None
    # Out of range and future are rejected.
    import pytest
    with pytest.raises(ValueError):
        metrics.nudge_user_date(auto, date(2026, 7, 27), today)
    with pytest.raises(ValueError):
        metrics.nudge_user_date(date(2026, 7, 30), date(2026, 7, 31), today)
```

- [ ] **Step 2: Run to verify failure, then implement the helper**

Run: `pytest tests/test_metrics.py::test_nudge_user_date -v` → FAIL (no attribute).

Add to `metrics.py` after `effective_date`:

```python
def nudge_user_date(auto_day, requested, today):
    """Value to store in `user_date` for a ±1-day nudge: None clears the
    override (requested == the automatic day), an ISO string stores it
    (requested is exactly one day off the automatic day). Anything else —
    including any future day — raises ValueError. Pure: the caller supplies
    `today` so this stays clock-free."""
    if requested > today:
        raise ValueError("cannot move an item into the future")
    if requested == auto_day:
        return None
    if abs((requested - auto_day).days) == 1:
        return requested.isoformat()
    raise ValueError("day must be within one day of the automatic day")
```

Re-run: PASS.

- [ ] **Step 3: Write the failing route tests**

In `tests/test_api_routes.py`, following its existing authed-client fixture style:

```python
def test_patch_delivery_day_nudge(client):
    db.add_delivery_order("api-1", "Uber Eats", "2026-07-30T01:00:00-06:00", "Order", 15.0)
    row = db.get_delivery_orders_range("2026-07-29", "2026-07-29")[0]
    # NOTE: if today's real date < 2026-07-30 these seeds are future-dated;
    # follow this file's existing convention for freezing/choosing dates
    # (seed relative to the mocked or real today so the nudge target isn't
    # in the future).
    r = client.patch(f"/api/deliveries/{row['id']}", json={"day": "2026-07-30"})
    assert r.status_code == 200
    assert db.get_delivery_orders_range("2026-07-30", "2026-07-30")[0]["day"] == "2026-07-30"
    # Moving back to the automatic day clears the override (user_date NULL).
    r = client.patch(f"/api/deliveries/{row['id']}", json={"day": "2026-07-29"})
    assert r.status_code == 200
    assert db.get_delivery_orders_range("2026-07-29", "2026-07-29")[0]["user_date"] is None


def test_patch_delivery_day_validation(client):
    db.add_delivery_order("api-2", "Uber Eats", "2026-07-30T01:00:00-06:00", "Order", 15.0)
    row = db.get_delivery_orders_range("2026-07-29", "2026-07-29")[0]
    assert client.patch(f"/api/deliveries/{row['id']}", json={"day": "2026-07-27"}).status_code == 400
    assert client.patch(f"/api/deliveries/{row['id']}", json={"day": "not-a-date"}).status_code == 400
    assert client.patch("/api/deliveries/999999", json={"day": "2026-07-29"}).status_code == 404


def test_patch_ride_day_and_work(client):
    db.add_ride("api-r1", "Uber", "2026-07-30T02:34:00-06:00",
                "2026-07-30T02:34:00", "Your trip", 27.82)
    ride = db.get_rides_range("2026-07-29", "2026-07-29")[0]
    # day alone
    assert client.patch(f"/api/rides/{ride['id']}", json={"day": "2026-07-30"}).status_code == 200
    assert db.get_rides_range("2026-07-30", "2026-07-30")[0]["day"] == "2026-07-30"
    # is_work alone still works (back-compat)
    assert client.patch(f"/api/rides/{ride['id']}", json={"is_work": True}).status_code == 200
    assert db.get_rides_range("2026-07-30", "2026-07-30")[0]["user_is_work"] is True
    # empty body is a 400, unknown id a 404
    assert client.patch(f"/api/rides/{ride['id']}", json={}).status_code == 400
    assert client.patch("/api/rides/999999", json={"day": "2026-07-29"}).status_code == 404
```

- [ ] **Step 4: Run to verify failure**

Run: `pytest tests/test_api_routes.py -k "patch_delivery or patch_ride_day" -v`
Expected: FAIL — 405 (no PATCH /deliveries route) / 422 (RidePatch requires is_work)

- [ ] **Step 5: Implement the routes**

In `app/routes.py` (it already imports `metrics`? If not, add `import metrics`). Replace the `RidePatch` class and `patch_ride`, and add the delivery route after `get_deliveries`:

```python
def _validated_user_date(auto_day: str, requested_day: str) -> "str | None":
    """Shared PATCH-body validation: parse, then apply the pure ±1 rule.
    Raises HTTPException(400) with an actionable message."""
    try:
        requested = datetime.date.fromisoformat(requested_day)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")
    try:
        return metrics.nudge_user_date(datetime.date.fromisoformat(auto_day), requested, _local_today())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class DeliveryPatch(BaseModel):
    day: str


@router.patch("/deliveries/{order_id}")
def patch_delivery(order_id: int, body: DeliveryPatch):
    auto = db.get_delivery_auto_day(order_id)
    if auto is None:
        raise HTTPException(status_code=404, detail="order not found")
    db.set_delivery_user_date(order_id, _validated_user_date(auto, body.day))
    return {"ok": True}


class RidePatch(BaseModel):
    is_work: "bool | None" = None
    day: "str | None" = None


@router.patch("/rides/{ride_id}")
def patch_ride(ride_id: int, body: RidePatch):
    provided = body.model_fields_set
    if not provided:
        raise HTTPException(status_code=400, detail="nothing to update: send is_work and/or day")
    if "day" in provided:
        auto = db.get_ride_auto_day(ride_id)
        if auto is None:
            raise HTTPException(status_code=404, detail="ride not found")
        db.set_ride_user_date(ride_id, _validated_user_date(auto, body.day))
    if "is_work" in provided:
        if body.is_work is None:
            raise HTTPException(status_code=400, detail="is_work must be true or false")
        if not db.set_ride_work_override(ride_id, body.is_work):
            raise HTTPException(status_code=404, detail="ride not found")
    return {"ok": True}
```

(Use the file's actual annotation style — if it doesn't quote unions, write `bool | None` bare; match neighbors.)

- [ ] **Step 6: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add metrics.py app/routes.py tests/test_metrics.py tests/test_api_routes.py
git commit -m "feat(api): ±1-day nudge PATCH for deliveries and rides"
```

---

### Task 6: `lib.ts` nudge helpers

**Files:**
- Modify: `frontend/src/lib.ts`
- Test: `frontend/src/lib.test.ts`

**Interfaces:**
- Consumes: `addDays(iso, delta)` (exists in lib.ts).
- Produces:
  - `nudgeOptions(autoDay: string, viewedDay: string, todayIso: string): string[]`
  - `nudgeLabel(iso: string): string` — e.g. `"Jul 29"`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/lib.test.ts`, matching its existing describe/it style:

```typescript
describe("nudgeOptions", () => {
  it("offers auto±1 minus the current day, no future", () => {
    // Row on its automatic day: both neighbors offered, future filtered.
    expect(nudgeOptions("2026-07-29", "2026-07-29", "2026-07-30"))
      .toEqual(["2026-07-28", "2026-07-30"]);
    // Same, but "tomorrow" would be the future — only the previous day offered.
    expect(nudgeOptions("2026-07-30", "2026-07-30", "2026-07-30"))
      .toEqual(["2026-07-29"]);
    // Row already nudged forward: moving back (to auto or auto-1) offered.
    expect(nudgeOptions("2026-07-29", "2026-07-30", "2026-07-30"))
      .toEqual(["2026-07-28", "2026-07-29"]);
  });
});

describe("nudgeLabel", () => {
  it("formats a short month-day", () => {
    expect(nudgeLabel("2026-07-29")).toBe("Jul 29");
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm test -- --run`
Expected: FAIL — `nudgeOptions is not defined`

- [ ] **Step 3: Implement**

In `lib.ts`, near the other day helpers:

```typescript
/** Valid day-nudge targets for a Day log row: the automatic day and its two
 * neighbors (the server's ±1 bound; landing on the automatic day clears the
 * override), minus the day the row currently sits on, minus the future.
 * ISO strings compare lexicographically, so <= is a real date compare. */
export function nudgeOptions(autoDay: string, viewedDay: string, todayIso: string): string[] {
  return [addDays(autoDay, -1), autoDay, addDays(autoDay, 1)]
    .filter((d) => d !== viewedDay && d <= todayIso);
}

/** Short label for a nudge button: "Jul 29". Noon anchor sidesteps TZ edges,
 * same trick as the other label helpers here. */
export function nudgeLabel(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
```

(Match the exact noon-anchor idiom the existing `dayLabel`/`weekLabel` helpers use — copy theirs if it differs.)

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm test -- --run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib.ts frontend/src/lib.test.ts
git commit -m "feat(frontend): nudge target computation helpers"
```

---

### Task 7: Day log action strip (Today.tsx + styles)

**Files:**
- Modify: `frontend/src/screens/Today.tsx`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: `nudgeOptions`/`nudgeLabel` (Task 6); `PATCH /api/deliveries/{id}` and `PATCH /api/rides/{id}` (Task 5); `day`/`auto_day`/`id` now present on `/api/today` delivery and ride rows (they flow through `today_snapshot` untouched).
- Produces: delivery rows tappable; ride tap opens the strip instead of instantly toggling work.

- [ ] **Step 1: Extend the row types**

In `Today.tsx`: `deliveries` entries gain `id: number; day: string; auto_day: string`; `Ride` gains `day: string; auto_day: string`. Key delivery entries by `delivery:${d.id}` instead of `ordered_at`.

- [ ] **Step 2: Add strip state + actions**

```typescript
// One action strip open at a time, keyed "delivery:3" / "ride:7" — same
// per-day reset rule as the filter strip.
const [openStrip, setOpenStrip] = useState<string | null>(null);
```

Add `setOpenStrip(null)` to the existing `useEffect` that resets `removed`/`activeCategory` on `data?.date` change.

```typescript
const moveDelivery = async (id: number, day: string) => {
  try {
    await apiSend("PATCH", `/deliveries/${id}`, { day });
    setOpenStrip(null);
    refresh();
  } catch (e) {
    setError((e as Error).message);
  }
};

const moveRide = async (id: number, day: string) => {
  try {
    await apiSend("PATCH", `/rides/${id}`, { day });
    setOpenStrip(null);
    refresh();
  } catch (e) {
    setError((e as Error).message);
  }
};
```

- [ ] **Step 3: Rework the delivery and ride entries**

Delivery rows become interactive; both render the strip beneath the row when open (wrap in `day-log-entry` like social rows do). Ride tap now toggles the strip — `toggleRideWork` moves INTO the strip (keep the function; it's now called from the strip's work button, and should also `setOpenStrip(null)` on success):

```tsx
const todayForNudge = todayIso ?? data.date;

const deliveryEntries: LogEntry[] = data.deliveries.map((d) => {
  const category = categoryForKind("delivery");
  const key = `delivery:${d.id}`;
  return {
    key,
    timeIso: d.ordered_at,
    category,
    node: (
      <div className="day-log-entry" key={key}>
        <DayLogRow
          category={category}
          name={`${d.service} order`}
          meta={dayLogRowMeta(d.ordered_at, d.amount)}
          interactive
          onClick={() => setOpenStrip(openStrip === key ? null : key)}
          dimmed={isDimmed(category, activeCategory)}
        />
        {openStrip === key && (
          <div className="day-log-actions">
            {nudgeOptions(d.auto_day, data.date, todayForNudge).map((day) => (
              <button key={day} type="button" onClick={() => moveDelivery(d.id, day)}>
                {day < data.date ? "‹ " : ""}Move to {nudgeLabel(day)}{day > data.date ? " ›" : ""}
              </button>
            ))}
          </div>
        )}
      </div>
    ),
  };
});
```

Ride rows: same shape, plus the work toggle as the strip's first button:

```tsx
{openStrip === key && (
  <div className="day-log-actions">
    <button type="button" onClick={() => toggleRideWork(r)}>
      {r.is_work ? "Mark as personal" : "Mark as work"}
    </button>
    {nudgeOptions(r.auto_day, data.date, todayForNudge).map((day) => (
      <button key={day} type="button" onClick={() => moveRide(r.id, day)}>
        {day < data.date ? "‹ " : ""}Move to {nudgeLabel(day)}{day > data.date ? " ›" : ""}
      </button>
    ))}
  </div>
)}
```

Import `nudgeOptions, nudgeLabel` from `../lib`.

- [ ] **Step 4: Style the strip**

In `styles.css`, next to the `.social-form` rules (the strip is its sibling pattern): a `.day-log-actions` row — small horizontal button group, indented to align under the row name, using existing button tokens. Follow the file's OKLCH token conventions; both themes must work (check the dark theme block for anything `.social-form` overrides and mirror it).

- [ ] **Step 5: Verify**

Run: `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`
Expected: all PASS/clean. Then a manual look (`npm run dev` + backend on :8080): tap the 12:49 AM Uber Eats order on Jul 29, move it to Jul 30, confirm it leaves the day and appears on the 30th; move it back; confirm a ride's strip shows the work toggle.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/screens/Today.tsx frontend/src/styles.css
git commit -m "feat(frontend): day-log action strip — move day, work toggle"
```

---

### Task 8: Full verification + repo guide update

**Files:**
- Modify: `CLAUDE.md` (night-cutoff bullets), `docs/superpowers/specs/2026-07-29-ride-cancellation-and-effective-date-design.md` (deferred-decision note)

- [ ] **Step 1: Run everything**

Run: `pytest tests/ -v` and `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`
Expected: all green. Fix anything that isn't before proceeding.

- [ ] **Step 2: Update the docs — REQUIRES USER CONFIRMATION for CLAUDE.md**

CLAUDE.md edits need explicit user sign-off (global rule). Propose these exact changes and wait:
- Metrics section: "**The night cutoff applies to rides only**" bullet → now covers deliveries too, and mention `user_date` (±1 nudge, user column, resolved via COALESCE).
- Database table notes for `delivery_orders` and `rides`: add `user_date` (nullable, user-set, sync never touches; resolved day exposed as `day`).
- In the 2026-07-29 spec's "Deferred decisions" section, add one line: "Resolved 2026-07-30 — see 2026-07-30-delivery-night-cutoff-and-day-nudge-design.md."

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-07-29-ride-cancellation-and-effective-date-design.md
git commit -m "docs: night cutoff now covers deliveries; user_date nudge column"
```
