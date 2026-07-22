# Frontend Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Day navigation with backfill logging, week-navigable Scorecard with SVG trend charts, weekday heatmap, computed noticings, and a cached AI weekly reflection.

**Architecture:** Frontend-heavy change to the React SPA (no router — tab state in `App.tsx`). Backend gains date validation, a `date` param on `/today`, a pure-math insights layer in `metrics.py` wired through `app/scorecard.py` into two new routes (`/insights`, `/reflection`), and one new table (`weekly_reflections`). Charts are hand-rolled SVG/DOM styled by the existing OKLCH tokens.

**Tech Stack:** FastAPI + pydantic, SQLite/Postgres via `database.py`, React 18 + Vite + TypeScript, vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-07-22-frontend-redesign-design.md`

## Global Constraints

- **No new frontend dependencies** — charts are hand-rolled SVG/DOM only.
- **`ai_metrics.py` is the only place with Claude calls**; keep the `_call_json()` pattern; `MODEL` stays `claude-haiku-4-5-20251001`.
- **`database.py` is the only place with SQL**; `config.py` the only env reader.
- Pure math goes in `metrics.py` (no DB, no I/O); DB wiring in `app/scorecard.py`.
- All frontend colors via existing OKLCH custom-property tokens (`--accent`, `--accent-soft`, `--over`, `--surface-2`, `--ink-2`, `--muted`, `--line`) in `frontend/src/styles.css`.
- Week = Monday–Sunday local time (`TIMEZONE`). Weekday arrays are Monday-first (`[Mon..Sun]`, Python `date.weekday()` order).
- Past dates allowed without limit; future dates rejected with 400.
- Backend tests: `pytest tests/ -v`. Frontend: `cd frontend && npm test` and `npm run build`.
- Commit after every task. No commits with failing tests.

---

## Phase 1 — Backfill + Navigation

### Task 1: Backend date validation + dated `/today`

**Files:**
- Modify: `app/routes.py` (routes `get_today`, `post_checkin`, `delete_checkin`)
- Modify: `app/scorecard.py:58-68` (`today_snapshot`)
- Test: `tests/test_api_routes.py`

**Interfaces:**
- Produces: `GET /api/today?date=YYYY-MM-DD` (optional param; 400 on malformed or future date; response unchanged in shape, `date` field echoes the requested day). `POST /api/checkins` and `DELETE /api/checkins/{type}` reject future/malformed dates with 400. `today_snapshot(day: Optional[date] = None) -> dict`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_routes.py`:

```python
def test_checkin_rejects_future_or_malformed_date(temp_db_path):
    client = _client(temp_db_path)
    future = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    assert client.post("/api/checkins", json={"type": "gym", "date": future}).status_code == 400
    assert client.post("/api/checkins", json={"type": "gym", "date": "not-a-date"}).status_code == 400
    assert client.delete(f"/api/checkins/gym?date={future}").status_code == 400
    assert client.delete("/api/checkins/gym?date=garbage").status_code == 400


def test_checkin_past_date_lands_on_that_day(temp_db_path):
    client = _client(temp_db_path)
    past = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    assert client.post("/api/checkins", json={"type": "gym", "date": past}).status_code == 200
    snap = client.get(f"/api/today?date={past}").json()
    assert snap["date"] == past
    assert snap["gym"] is True
    assert client.get("/api/today").json()["gym"] is False
    assert client.delete(f"/api/checkins/gym?date={past}").status_code == 200
    assert client.get(f"/api/today?date={past}").json()["gym"] is False


def test_today_rejects_future_or_malformed_date(temp_db_path):
    client = _client(temp_db_path)
    future = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    assert client.get(f"/api/today?date={future}").status_code == 400
    assert client.get("/api/today?date=garbage").status_code == 400
```

Add `import datetime` at the top of `tests/test_api_routes.py` if not present.

(Note: `datetime.date.today()` is UTC-agnostic system time while the server uses `TIMEZONE`; ±3/−10-day offsets keep the tests correct in any timezone.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_routes.py -v -k "future or past_date"`
Expected: FAIL — future-date requests currently return 200.

- [ ] **Step 3: Implement**

In `app/routes.py`, add below `CheckinBody`:

```python
def _parse_date(value: str) -> datetime.date:
    try:
        d = datetime.date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    if d > _local_today():
        raise HTTPException(status_code=400, detail="date cannot be in the future")
    return d
```

Replace the three handlers:

```python
@router.get("/today")
def get_today(date: Optional[str] = None):
    day = _parse_date(date) if date else None
    return today_snapshot(day)


@router.post("/checkins")
def post_checkin(body: CheckinBody):
    if body.type == "alcohol" and body.level is None:
        raise HTTPException(status_code=400, detail="alcohol check-in requires level 1-3")
    day = (_parse_date(body.date) if body.date else _local_today()).isoformat()
    db.record_checkin(day, body.type, body.level)
    return {"ok": True}


@router.delete("/checkins/{type}")
def delete_checkin(type: Literal["gym", "alcohol"], date: Optional[str] = None):
    day = (_parse_date(date) if date else _local_today()).isoformat()
    db.delete_checkin(day, type)
    return {"ok": True}
```

In `app/scorecard.py`, change `today_snapshot` signature and first line:

```python
def today_snapshot(day: Optional[date] = None) -> dict:
    d = (day or _local_today()).isoformat()
    checkins = db.get_checkins_range(d, d)
    alcohol = next((c for c in checkins if c["type"] == "alcohol"), None)
    return {
        "date": d,
        "gym": any(c["type"] == "gym" for c in checkins),
        "alcohol_level": alcohol["level"] if alcohol else None,
        "deliveries": db.get_delivery_orders_range(d, d),
        "social_events": db.get_events_for_day(d),
    }
```

Add `from typing import Optional` to `app/scorecard.py` imports.

- [ ] **Step 4: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: all PASS (existing `/today` tests unaffected — no-param behavior unchanged).

- [ ] **Step 5: Commit**

```bash
git add app/routes.py app/scorecard.py tests/test_api_routes.py
git commit -m "feat(api): dated /today and check-ins with future-date rejection"
```

---

### Task 2: Date helpers in `lib.ts`

**Files:**
- Modify: `frontend/src/lib.ts`
- Test: `frontend/src/lib.test.ts`

**Interfaces:**
- Produces: `addDays(iso: string, delta: number): string` (ISO in/out, local-safe); `relativeDayLabel(iso: string, todayIso: string): string` → `"Today"` / `"Yesterday"` / `"Mon, Jul 14"` (`", 2025"` appended when the year differs from today's year).

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/lib.test.ts`:

```ts
import { addDays, relativeDayLabel } from "./lib";

describe("addDays", () => {
  it("steps forward and back across month boundaries", () => {
    expect(addDays("2026-07-01", -1)).toBe("2026-06-30");
    expect(addDays("2026-06-30", 1)).toBe("2026-07-01");
    expect(addDays("2026-01-01", -1)).toBe("2025-12-31");
  });
});

describe("relativeDayLabel", () => {
  it("labels today and yesterday", () => {
    expect(relativeDayLabel("2026-07-22", "2026-07-22")).toBe("Today");
    expect(relativeDayLabel("2026-07-21", "2026-07-22")).toBe("Yesterday");
  });
  it("labels older days with weekday and month", () => {
    expect(relativeDayLabel("2026-07-14", "2026-07-22")).toBe("Tue, Jul 14");
  });
  it("appends the year when it differs", () => {
    expect(relativeDayLabel("2025-12-31", "2026-07-22")).toBe("Wed, Dec 31, 2025");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test`
Expected: FAIL — `addDays` is not exported.

- [ ] **Step 3: Implement**

Append to `frontend/src/lib.ts`:

```ts
export function addDays(iso: string, delta: number): string {
  const d = parseDay(iso);
  d.setDate(d.getDate() + delta);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

export function relativeDayLabel(iso: string, todayIso: string): string {
  if (iso === todayIso) return "Today";
  if (iso === addDays(todayIso, -1)) return "Yesterday";
  const d = parseDay(iso);
  const base = `${DAYS[d.getDay()].slice(0, 3)}, ${MONTHS[d.getMonth()]} ${d.getDate()}`;
  return d.getFullYear() === parseDay(todayIso).getFullYear()
    ? base
    : `${base}, ${d.getFullYear()}`;
}
```

(`DAYS`, `MONTHS`, `parseDay` already exist in `lib.ts`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib.ts frontend/src/lib.test.ts
git commit -m "feat(frontend): addDays and relativeDayLabel helpers"
```

---

### Task 3: Day navigation on the Today screen

**Files:**
- Create: `frontend/src/components/DayNav.tsx`
- Modify: `frontend/src/screens/Today.tsx`
- Modify: `frontend/src/styles.css` (append)

**Interfaces:**
- Consumes: Task 1's `GET /today?date=`, dated mutations; Task 2's `addDays`, `relativeDayLabel`.
- Produces: `DayNav` component: `{ date: string; todayIso: string; onPrev(): void; onNext(): void }`.

- [ ] **Step 1: Create `frontend/src/components/DayNav.tsx`**

```tsx
import { dayLabel, relativeDayLabel } from "../lib";

interface Props {
  date: string;
  todayIso: string;
  onPrev: () => void;
  onNext: () => void;
}

export default function DayNav({ date, todayIso, onPrev, onNext }: Props) {
  const isToday = date === todayIso;
  return (
    <div className={`navhead${isToday ? "" : " past"}`}>
      <button className="nav-btn" aria-label="Previous day" onClick={onPrev}>‹</button>
      <div className="nav-label">
        <h2>{relativeDayLabel(date, todayIso)}</h2>
        <p className="sub">{dayLabel(date)}</p>
      </div>
      <button className="nav-btn" aria-label="Next day" onClick={onNext} disabled={isToday}>›</button>
    </div>
  );
}
```

- [ ] **Step 2: Rewire `frontend/src/screens/Today.tsx`**

Replace state, refresh, mutations, and header:

```tsx
export default function Today() {
  const [data, setData] = useState<TodayData | null>(null);
  const [week, setWeek] = useState<Card | null>(null);
  const [selected, setSelected] = useState<string | null>(null); // null = today
  const [todayIso, setTodayIso] = useState<string | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    apiGet<TodayData>(`/today${selected ? `?date=${selected}` : ""}`)
      .then((d) => {
        setData(d);
        if (!selected) setTodayIso(d.date);
      })
      .catch((e) => setError(e.message));
    apiGet<Card>(`/scorecard${selected ? `?week_start=${selected}` : ""}`)
      .then(setWeek)
      .catch(() => setWeek(null));
  }, [selected]);
  useEffect(refresh, [refresh]);
```

Mutations send the displayed date:

```tsx
  const toggleGym = async () => {
    try {
      if (data.gym) await apiSend("DELETE", `/checkins/gym?date=${data.date}`);
      else await apiSend("POST", "/checkins", { type: "gym", date: data.date });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const logAlcohol = async (level: number) => {
    try {
      await apiSend("POST", "/checkins", { type: "alcohol", level, date: data.date });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const undoAlcohol = async () => {
    try {
      await apiSend("DELETE", `/checkins/alcohol?date=${data.date}`);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };
```

Replace the `screen-head` block with:

```tsx
      <DayNav
        date={data.date}
        todayIso={todayIso ?? data.date}
        onPrev={() => setSelected(addDays(data.date, -1))}
        onNext={() => {
          const next = addDays(data.date, 1);
          setSelected(next === todayIso ? null : next);
        }}
      />
```

Imports: add `import DayNav from "../components/DayNav";` and `addDays` from `../lib`.

Copy changes for past-day correctness: gym subtitle `data.gym ? "Logged — tap to undo" : "Tap to log a session"`; the "Noticed quietly" empty state `"Nothing this day."`; section label `"This week"` → keep (the strip already shows the selected day's week via `week_start`).

- [ ] **Step 3: Append CSS to `frontend/src/styles.css`**

```css
/* ── Day/week navigation header ── */
.navhead { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.navhead .nav-label { flex: 1; text-align: center; }
.navhead .nav-label h2 { margin: 0; }
.navhead .nav-label .sub { margin: 2px 0 0; }
.nav-btn {
  width: 40px; height: 40px; border-radius: 10px;
  border: 1px solid var(--line); background: var(--surface);
  color: var(--ink); font-size: 20px; line-height: 1; cursor: pointer;
}
.nav-btn:disabled { opacity: 0.3; cursor: default; }
.navhead.past .nav-label {
  background: var(--accent-soft); border-radius: 10px; padding: 4px 8px;
}
```

- [ ] **Step 4: Verify**

Run: `cd frontend && npm test && npm run build`
Expected: tests PASS, build succeeds. Then `pytest tests/ -v` still green.
Manual check (`uvicorn main:app --reload --port 8080` + `npm run dev`): ‹ steps back, › disabled on today, past-day header tinted, gym toggle on yesterday persists after navigating away and back.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/DayNav.tsx frontend/src/screens/Today.tsx frontend/src/styles.css
git commit -m "feat(frontend): day navigation with past-day check-in backfill"
```

---

### Task 4: Week navigation on the Scorecard

**Files:**
- Create: `frontend/src/components/WeekNav.tsx`
- Modify: `frontend/src/screens/Scorecard.tsx`

**Interfaces:**
- Consumes: existing `GET /scorecard?week_start=`; `addDays`, `weekLabel` from `lib.ts`.
- Produces: `WeekNav` component: `{ weekStart: string; isCurrent: boolean; onPrev(): void; onNext(): void }`.

- [ ] **Step 1: Create `frontend/src/components/WeekNav.tsx`**

```tsx
import { weekLabel } from "../lib";

interface Props {
  weekStart: string;
  isCurrent: boolean;
  onPrev: () => void;
  onNext: () => void;
}

export default function WeekNav({ weekStart, isCurrent, onPrev, onNext }: Props) {
  return (
    <div className={`navhead${isCurrent ? "" : " past"}`}>
      <button className="nav-btn" aria-label="Previous week" onClick={onPrev}>‹</button>
      <div className="nav-label">
        <h2>{isCurrent ? "This week" : "Week of"}</h2>
        <p className="sub">{weekLabel(weekStart)}</p>
      </div>
      <button className="nav-btn" aria-label="Next week" onClick={onNext} disabled={isCurrent}>›</button>
    </div>
  );
}
```

- [ ] **Step 2: Rewire `frontend/src/screens/Scorecard.tsx`**

Replace state and effect:

```tsx
  const [card, setCard] = useState<Card | null>(null);
  const [currentWeekStart, setCurrentWeekStart] = useState<string | null>(null);
  const [weekStart, setWeekStart] = useState<string | null>(null); // null = current
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState("");
  const [historyFailed, setHistoryFailed] = useState(false);

  useEffect(() => {
    apiGet<Card>(`/scorecard${weekStart ? `?week_start=${weekStart}` : ""}`)
      .then((c) => {
        setCard(c);
        if (!weekStart) setCurrentWeekStart(c.week_start);
      })
      .catch((e) => setError(e.message));
  }, [weekStart]);

  useEffect(() => {
    apiGet<History>("/history?weeks=8").then(setHistory).catch(() => setHistoryFailed(true));
  }, []);
```

Replace the `screen-head` block with:

```tsx
      <WeekNav
        weekStart={card.week_start}
        isCurrent={card.week_start === (currentWeekStart ?? card.week_start)}
        onPrev={() => setWeekStart(addDays(card.week_start, -7))}
        onNext={() => {
          const next = addDays(card.week_start, 7);
          setWeekStart(next === currentWeekStart ? null : next);
        }}
      />
```

Imports: `import WeekNav from "../components/WeekNav";`, add `addDays` to the `../lib` import. The streak line should only render on the current week (streaks are always relative to now): change to `{streak > 0 && weekStart === null && ` · ${streak}-week streak`}`.

- [ ] **Step 3: Verify**

Run: `cd frontend && npm test && npm run build`
Expected: PASS + build success. Manual: ‹ shows prior weeks with correct counts, › disabled on current week.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/WeekNav.tsx frontend/src/screens/Scorecard.tsx
git commit -m "feat(frontend): scorecard week navigation"
```

---

## Phase 2 — Computed Insights

### Task 5: Pure insight math in `metrics.py`

**Files:**
- Modify: `metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces (all pure, no DB/I-O):
  - `WEEKDAY_NAMES: list[str]` — `["Monday", ..., "Sunday"]`
  - `weekday_counts(dates: list[str]) -> list[int]` — 7 ints, Monday-first
  - `trend_direction(series: list[int]) -> str | None` — `"up"|"down"|"flat"`; `None` if `len < 6`. Rule: over the last 6 entries, `delta = mean(last 3) − mean(first 3)`; `up` if `delta ≥ 1`, `down` if `delta ≤ −1`, else `flat`.
  - `weekday_skew(dates: list[str]) -> tuple[int, float] | None` — `(weekday_index, share)` when one weekday holds ≥ 40% of events, with ≥ 4 total events and max count ≥ 3; else `None`.
  - `co_occurrence(dates_a: list[str], dates_b: list[str]) -> float | None` — Jaccard overlap of the day sets; `None` unless both have ≥ 4 distinct days.
  - `noticings(date_lists: dict[str, list[str]], series: dict[str, list[int]]) -> list[str]` — ≤ 3 statements, priority: co-occurrence (alcohol×delivery, fires at ≥ 0.5), weekday skew per metric, trend per metric.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metrics.py`:

```python
from metrics import (
    co_occurrence, noticings, trend_direction, weekday_counts, weekday_skew,
)


def test_weekday_counts_monday_first():
    # 2026-07-20 is a Monday, 2026-07-26 a Sunday
    assert weekday_counts(["2026-07-20", "2026-07-20", "2026-07-26"]) == [2, 0, 0, 0, 0, 0, 1]
    assert weekday_counts([]) == [0] * 7


def test_trend_direction():
    assert trend_direction([1, 1]) is None
    assert trend_direction([0, 0, 0, 2, 2, 2]) == "up"
    assert trend_direction([3, 3, 3, 1, 1, 1]) == "down"
    assert trend_direction([2, 2, 2, 2, 2, 2]) == "flat"
    assert trend_direction([9, 9, 0, 0, 0, 2, 2, 2]) == "up"  # only last 6 count


def test_weekday_skew():
    sundays = ["2026-07-05", "2026-07-12", "2026-07-19"]
    assert weekday_skew(sundays + ["2026-07-20"]) == (6, 0.75)
    assert weekday_skew(sundays) is None                     # < 4 events
    assert weekday_skew(["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23"]) is None  # no cluster


def test_co_occurrence():
    a = ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"]
    b = ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-05"]
    assert co_occurrence(a, b) == 0.6  # 3 shared / 5 union
    assert co_occurrence(a[:3], b) is None


def test_noticings_caps_at_three_and_prioritizes():
    shared = ["2026-07-04", "2026-07-11", "2026-07-18", "2026-07-25"]
    date_lists = {"alcohol": shared, "delivery": shared, "gym": [], "social": []}
    series = {"gym": [0, 0, 0, 2, 2, 2], "social": [3, 3, 3, 1, 1, 1],
              "delivery": [1] * 6, "alcohol": [1] * 6}
    out = noticings(date_lists, series)
    assert len(out) == 3
    assert "same day" in out[0]          # co-occurrence first
    assert out[1].startswith("Delivery") or out[1].startswith("Alcohol")  # skew next


def test_noticings_silent_on_sparse_data():
    assert noticings({"gym": ["2026-07-20"]}, {"gym": [1, 1]}) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_metrics.py -v`
Expected: FAIL with ImportError on the new names.

- [ ] **Step 3: Implement in `metrics.py`**

Change the import line to `from datetime import date, timedelta`, then append:

```python
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def weekday_counts(dates):
    """ISO date strings -> counts per weekday, Monday-first."""
    out = [0] * 7
    for d in dates:
        out[date.fromisoformat(d).weekday()] += 1
    return out


def trend_direction(series):
    """Weekly counts oldest-first. None if fewer than 6 weeks."""
    if len(series) < 6:
        return None
    recent = series[-6:]
    delta = sum(recent[3:]) / 3 - sum(recent[:3]) / 3
    if delta >= 1:
        return "up"
    if delta <= -1:
        return "down"
    return "flat"


def weekday_skew(dates):
    """(weekday_index, share) when one weekday dominates; else None.
    Thresholds: >= 4 total events, max weekday >= 3 events and >= 40% share."""
    counts = weekday_counts(dates)
    total = sum(counts)
    if total < 4:
        return None
    mx = max(counts)
    if mx < 3 or mx / total < 0.4:
        return None
    return counts.index(mx), mx / total


def co_occurrence(dates_a, dates_b):
    """Jaccard overlap of two day sets; None unless both have >= 4 distinct days."""
    a, b = set(dates_a), set(dates_b)
    if len(a) < 4 or len(b) < 4:
        return None
    return len(a & b) / len(a | b)


def noticings(date_lists, series):
    """<= 3 plain-language statements. Priority: co-occurrence, weekday skew, trend."""
    out = []
    j = co_occurrence(date_lists.get("alcohol", []), date_lists.get("delivery", []))
    if j is not None and j >= 0.5:
        out.append("Alcohol days and delivery orders often land on the same day.")
    for key in METRICS:
        skew = weekday_skew(date_lists.get(key, []))
        if skew:
            day, share = skew
            out.append(f"{METRICS[key]['label']} cluster on {WEEKDAY_NAMES[day]}s ({round(share * 100)}% of them).")
    for key in METRICS:
        t = trend_direction(series.get(key, []))
        if t in ("up", "down"):
            out.append(f"{METRICS[key]['label']} trending {t} over the last six weeks.")
    return out[:3]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metrics.py tests/test_metrics.py
git commit -m "feat(metrics): pure insight math — weekday counts, trend, skew, co-occurrence, noticings"
```

---

### Task 6: `GET /api/insights` endpoint

**Files:**
- Modify: `app/scorecard.py`
- Modify: `app/routes.py`
- Test: `tests/test_api_routes.py`

**Interfaces:**
- Consumes: Task 5's `metrics` functions; existing `history()`, `_occurred`, `db.get_*_range`.
- Produces: `insights(weeks: int) -> dict` in `app/scorecard.py`; route `GET /api/insights?weeks=12` (weeks clamped 1–52) returning:

```json
{
  "weeks": [<scorecard cards, oldest-first, completed weeks only>],
  "streaks": {"gym": 2, ...},
  "weekday_counts": {"gym": [0,0,0,0,0,0,0], ...},
  "noticings": ["...", "..."]
}
```

`weekday_counts`/`noticings` use a fixed 8-completed-week pattern window regardless of `weeks`; trends use the `weeks`-long series.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_routes.py`:

```python
def test_insights_shape_and_weekday_counts(temp_db_path):
    client = _client(temp_db_path)
    past = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    client.post("/api/checkins", json={"type": "gym", "date": past})
    ins = client.get("/api/insights?weeks=12").json()
    assert set(ins.keys()) == {"weeks", "streaks", "weekday_counts", "noticings"}
    assert len(ins["weeks"]) == 12
    assert sum(ins["weekday_counts"]["gym"]) == 1
    assert isinstance(ins["noticings"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_routes.py::test_insights_shape_and_weekday_counts -v`
Expected: FAIL 404.

- [ ] **Step 3: Implement**

Append to `app/scorecard.py`:

```python
PATTERN_WEEKS = 8


def _date_lists(start: date, end: date) -> dict:
    """Per-metric ISO day lists for events inside [start, end]."""
    s, e = start.isoformat(), end.isoformat()
    checkins = db.get_checkins_range(s, e)
    social = [ev for ev in db.get_social_events_range(s, e) if _occurred(ev["end_at"])]
    return {
        "gym": [c["date"] for c in checkins if c["type"] == "gym"],
        "alcohol": [c["date"] for c in checkins if c["type"] == "alcohol"],
        "delivery": [o["ordered_at"][:10] for o in db.get_delivery_orders_range(s, e)],
        "social": [ev["end_at"][:10] for ev in social],
    }


def insights(weeks: int) -> dict:
    hist = history(weeks)
    series = {k: [w["metrics"][k]["count"] for w in hist["weeks"]] for k in metrics.METRICS}
    this_monday = metrics.week_bounds(_local_today())[0]
    dates = _date_lists(this_monday - timedelta(weeks=PATTERN_WEEKS), this_monday - timedelta(days=1))
    return {
        "weeks": hist["weeks"],
        "streaks": hist["streaks"],
        "weekday_counts": {k: metrics.weekday_counts(v) for k, v in dates.items()},
        "noticings": metrics.noticings(dates, series),
    }
```

In `app/routes.py`: add `insights` to the `from app.scorecard import ...` line and append:

```python
@router.get("/insights")
def get_insights(weeks: int = 12):
    return insights(min(max(weeks, 1), 52))
```

- [ ] **Step 4: Run the backend suite**

Run: `pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/scorecard.py app/routes.py tests/test_api_routes.py
git commit -m "feat(api): /insights endpoint — series, weekday counts, streaks, noticings"
```

---

### Task 7: TrendChart component + Trends section

**Files:**
- Create: `frontend/src/components/TrendChart.tsx`
- Modify: `frontend/src/screens/Scorecard.tsx`
- Modify: `frontend/src/styles.css` (append)

**Interfaces:**
- Consumes: Task 6's `GET /insights?weeks=12`.
- Produces: `TrendChart` component: `{ points: { count: number; hit: boolean }[]; target: number }`. Scorecard now reads streaks from insights and drops the `/history` fetch and the `.hist` dot strip (TrendChart supersedes it).

- [ ] **Step 1: Create `frontend/src/components/TrendChart.tsx`**

```tsx
interface Point { count: number; hit: boolean }
interface Props { points: Point[]; target: number }

const W = 240, H = 56, PAD = 2, TOP = 8;

export default function TrendChart({ points, target }: Props) {
  if (points.length === 0) return null;
  const max = Math.max(target, ...points.map((p) => p.count), 1);
  const bw = (W - PAD * 2) / points.length;
  const y = (v: number) => H - (v / max) * (H - TOP);
  return (
    <svg className="trend" viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`Weekly counts, last ${points.length} weeks`}>
      {points.map((p, i) => (
        <rect key={i} className={p.hit ? "hit" : "miss"}
          x={PAD + i * bw + 1} y={y(p.count)}
          width={Math.max(bw - 2, 1)} height={Math.max(H - y(p.count), p.count > 0 ? 2 : 0)}
          rx="1.5" />
      ))}
      <line className="target" x1={0} x2={W} y1={y(target)} y2={y(target)} />
    </svg>
  );
}
```

- [ ] **Step 2: Integrate into `frontend/src/screens/Scorecard.tsx`**

Replace the `History` interface/fetch with:

```tsx
interface Insights {
  weeks: Card[];
  streaks: Record<string, number>;
  weekday_counts: Record<string, number[]>;
  noticings: string[];
}
```

```tsx
  const [insights, setInsights] = useState<Insights | null>(null);

  useEffect(() => {
    apiGet<Insights>("/insights?weeks=12").then(setInsights).catch(() => setInsights(null));
  }, []);
```

Streak source becomes `insights?.streaks[key] ?? 0`. Delete the `.hist` block inside the ledger rows, the `historyFailed` state, and the explanatory footnote.

Below the ledger add:

```tsx
      {insights && hasAnyData(insights) && (
        <>
          <p className="section-label">Trends · last 12 weeks</p>
          <div className="trends">
            {ORDER.map((key) => (
              <div className="trend-row" key={key}>
                <span className="trend-name">{card.metrics[key].label}</span>
                <TrendChart
                  points={insights.weeks.map((w) => ({
                    count: w.metrics[key].count, hit: w.metrics[key].hit,
                  }))}
                  target={card.metrics[key].target}
                />
              </div>
            ))}
          </div>
        </>
      )}
      {insights && !hasAnyData(insights) && (
        <p className="footnote">Not enough history yet — insights appear after a few weeks.</p>
      )}
```

Add at module level:

```tsx
function hasAnyData(ins: Insights): boolean {
  return ins.weeks.some((w) => Object.values(w.metrics).some((m) => m.count > 0));
}
```

Import `TrendChart`.

- [ ] **Step 3: Append CSS to `frontend/src/styles.css`**

```css
/* ── Trends ── */
.trends { display: grid; gap: 14px; }
.trend-row .trend-name { display: block; font-size: 13px; color: var(--ink-2); margin-bottom: 4px; }
.trend { width: 100%; height: 56px; display: block; }
.trend rect.hit { fill: var(--accent); }
.trend rect.miss { fill: var(--over); }
.trend line.target { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 3 3; }
```

- [ ] **Step 4: Verify**

Run: `cd frontend && npm test && npm run build`
Expected: PASS + build. Manual: 12 bars per metric, dashed target line, hit bars accent / miss bars over-toned.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TrendChart.tsx frontend/src/screens/Scorecard.tsx frontend/src/styles.css
git commit -m "feat(frontend): 12-week SVG trend charts on scorecard"
```

---

### Task 8: WeekdayHeatmap + Noticings sections

**Files:**
- Create: `frontend/src/components/WeekdayHeatmap.tsx`
- Modify: `frontend/src/screens/Scorecard.tsx`
- Modify: `frontend/src/styles.css` (append)

**Interfaces:**
- Consumes: `insights.weekday_counts` (Monday-first) and `insights.noticings` from Task 7's fetch.
- Produces: `WeekdayHeatmap` component: `{ rows: { label: string; counts: number[]; caution: boolean }[] }` (`caution` = ceiling metric → `--over` tint instead of `--accent`).

- [ ] **Step 1: Create `frontend/src/components/WeekdayHeatmap.tsx`**

```tsx
import type { CSSProperties } from "react";

const DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];

interface Row { label: string; counts: number[]; caution: boolean }

export default function WeekdayHeatmap({ rows }: { rows: Row[] }) {
  return (
    <div className="heatmap">
      <div className="hm-row">
        <span className="hm-label" />
        {DAY_LETTERS.map((d, i) => (
          <span key={i} className="hm-day">{d}</span>
        ))}
      </div>
      {rows.map((r) => {
        const max = Math.max(...r.counts, 1);
        return (
          <div className="hm-row" key={r.label}>
            <span className="hm-label">{r.label}</span>
            {r.counts.map((c, i) => (
              <span key={i}
                className={`hm-cell${r.caution ? " over" : ""}`}
                style={{ "--pct": `${Math.round((c / max) * 100)}%` } as CSSProperties}
                title={`${c}`} />
            ))}
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Integrate into `frontend/src/screens/Scorecard.tsx`**

Below the Trends section (inside the same `insights && hasAnyData(insights)` fragment):

```tsx
          <p className="section-label">Patterns · by weekday, last 8 weeks</p>
          <WeekdayHeatmap
            rows={ORDER.map((key) => ({
              label: card.metrics[key].label,
              counts: insights.weekday_counts[key] ?? [0, 0, 0, 0, 0, 0, 0],
              caution: card.metrics[key].direction === "ceiling",
            }))}
          />

          {insights.noticings.length > 0 && (
            <>
              <p className="section-label">Noticings</p>
              {insights.noticings.map((n) => (
                <p className="quiet" key={n}><span>{n}</span></p>
              ))}
            </>
          )}
```

Import `WeekdayHeatmap`.

- [ ] **Step 3: Append CSS to `frontend/src/styles.css`**

```css
/* ── Weekday heatmap ── */
.heatmap { display: grid; gap: 6px; }
.hm-row { display: grid; grid-template-columns: minmax(90px, 1fr) repeat(7, 28px); gap: 6px; align-items: center; }
.hm-day { font-size: 11px; color: var(--muted); text-align: center; }
.hm-label { font-size: 13px; color: var(--ink-2); }
.hm-cell {
  height: 28px; border-radius: 6px; border: 1px solid var(--line);
  background: color-mix(in oklch, var(--accent) var(--pct, 0%), var(--surface-2));
}
.hm-cell.over { background: color-mix(in oklch, var(--over) var(--pct, 0%), var(--surface-2)); }
```

- [ ] **Step 4: Verify**

Run: `cd frontend && npm test && npm run build`
Expected: PASS + build. Manual: heatmap intensity matches logged days in both light and dark themes; ceiling metrics tint with the over color; noticings section hidden when empty.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/WeekdayHeatmap.tsx frontend/src/screens/Scorecard.tsx frontend/src/styles.css
git commit -m "feat(frontend): weekday heatmap and noticings on scorecard"
```

---

## Phase 3 — AI Weekly Reflection

### Task 9: `weekly_reflections` table + DB functions

**Files:**
- Modify: `database.py` (`_init_v2_tables` + new functions after the settings section)
- Test: `tests/test_database_v2.py`

**Interfaces:**
- Produces: `db.get_reflection(week_start: str) -> str | None`; `db.save_reflection(week_start: str, text: str) -> None` (upsert on `week_start`). Table `weekly_reflections(id, week_start UNIQUE, text, created_at)` — new table via `CREATE TABLE IF NOT EXISTS` works on both engines (the column-migration warning in CLAUDE.md doesn't apply to whole new tables).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_database_v2.py` (match the file's existing import pattern for the reloaded `database` module):

```python
def test_reflection_roundtrip(temp_db_path):
    import database as db
    assert db.get_reflection("2026-07-13") is None
    db.save_reflection("2026-07-13", "A solid week.")
    assert db.get_reflection("2026-07-13") == "A solid week."
    db.save_reflection("2026-07-13", "Revised.")
    assert db.get_reflection("2026-07-13") == "Revised."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_database_v2.py::test_reflection_roundtrip -v`
Expected: FAIL — `get_reflection` does not exist.

- [ ] **Step 3: Implement**

In `_init_v2_tables()` (`database.py:502`), add after the `app_settings` create:

```python
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS weekly_reflections (
                id {serial} PRIMARY KEY,
                week_start TEXT NOT NULL UNIQUE,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
```

After the settings functions, add:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_database_v2.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_v2.py
git commit -m "feat(db): weekly_reflections table with get/save"
```

---

### Task 10: `ai_metrics.weekly_reflection()`

**Files:**
- Modify: `ai_metrics.py`
- Test: `tests/test_ai_metrics.py`

**Interfaces:**
- Consumes: a scorecard card dict (`metrics.build_scorecard` shape) and a `noticings` list of strings.
- Produces: `weekly_reflection(card: dict, noticings: list) -> str` — 2–3 sentence paragraph, `""` on any failure (never raises).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ai_metrics.py`:

```python
def _card():
    return {
        "week_start": "2026-07-13", "week_end": "2026-07-19",
        "metrics": {
            "gym": {"label": "Gym sessions", "count": 3, "target": 3, "direction": "floor", "hit": True},
            "delivery": {"label": "Delivery orders", "count": 2, "target": 1, "direction": "ceiling", "hit": False},
        },
    }


def test_weekly_reflection_returns_text(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        type("T", (), {"text": '{"reflection": "You held the line on gym."}'})()
    ]
    import ai_metrics
    assert ai_metrics.weekly_reflection(_card(), ["a pattern"]) == "You held the line on gym."
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Gym sessions: 3" in prompt and "a pattern" in prompt


def test_weekly_reflection_empty_on_garbage(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [type("T", (), {"text": "not json"})()]
    import ai_metrics
    assert ai_metrics.weekly_reflection(_card(), []) == ""
```

(Follow the existing tests in this file for how `mock_anthropic` is used; adjust the canned-response style to match if it differs.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ai_metrics.py -v -k reflection`
Expected: FAIL — attribute does not exist.

- [ ] **Step 3: Implement in `ai_metrics.py`**

```python
def weekly_reflection(card: dict, noticings: list) -> str:
    """2-3 sentence reflection on a completed week. Returns "" on any failure."""
    lines = []
    for m in card["metrics"].values():
        sign = "≤" if m["direction"] == "ceiling" else "≥"
        outcome = "hit" if m["hit"] else "missed"
        lines.append(f"- {m['label']}: {m['count']} (target {sign}{m['target']}) — {outcome}")
    summary = "\n".join(lines)
    patterns = "\n".join(f"- {n}" for n in noticings) if noticings else "- (none)"
    prompt = f"""You write a short weekly reflection for a personal habit tracker.

Week {card['week_start']} to {card['week_end']}:
{summary}

Patterns noticed:
{patterns}

Write 2-3 sentences: honest, warm, specific to the numbers. Address the reader as
"you". No emojis, no bullet points, no advice-column clichés.

Reply with only JSON: {{"reflection": "..."}}"""
    result = _call_json(prompt, max_tokens=300, default={"reflection": ""})
    return str(result.get("reflection") or "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ai_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ai_metrics.py tests/test_ai_metrics.py
git commit -m "feat(ai): weekly_reflection via _call_json"
```

---

### Task 11: `GET /api/reflection` with caching

**Files:**
- Modify: `app/routes.py`
- Test: `tests/test_api_routes.py`

**Interfaces:**
- Consumes: Task 9's `db.get_reflection`/`db.save_reflection`, Task 10's `ai_metrics.weekly_reflection`, Task 6's `insights`.
- Produces: `GET /api/reflection` → `{"week_start": "...", "text": "..."}` for the last completed week; **204 No Content** when generation fails (client hides the card). At most one AI call per week — cached forever after first success; failures are not cached (retried next request).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_routes.py`:

```python
def test_reflection_generates_once_then_caches(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        type("T", (), {"text": '{"reflection": "Steady week."}'})()
    ]
    client = _client(temp_db_path)
    first = client.get("/api/reflection")
    assert first.status_code == 200
    assert first.json()["text"] == "Steady week."
    second = client.get("/api/reflection")
    assert second.json() == first.json()
    assert mock_anthropic.messages.create.call_count == 1


def test_reflection_204_on_generation_failure(temp_db_path, mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [type("T", (), {"text": "garbage"})()]
    client = _client(temp_db_path)
    assert client.get("/api/reflection").status_code == 204
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_routes.py -v -k reflection`
Expected: FAIL 404.

- [ ] **Step 3: Implement in `app/routes.py`**

Add imports: `from fastapi import Response` (extend the existing fastapi import line), `import ai_metrics`, and add `insights` (already from Task 6) — the scorecard import line becomes:

```python
from app.scorecard import _local_today, history, insights, scorecard_for_week, today_snapshot
```

Append route:

```python
@router.get("/reflection")
def get_reflection():
    week_start = metrics.week_bounds(_local_today())[0] - datetime.timedelta(weeks=1)
    ws = week_start.isoformat()
    cached = db.get_reflection(ws)
    if cached:
        return {"week_start": ws, "text": cached}
    card = scorecard_for_week(week_start)
    text = ai_metrics.weekly_reflection(card, insights(12)["noticings"])
    if not text:
        return Response(status_code=204)
    db.save_reflection(ws, text)
    return {"week_start": ws, "text": text}
```

- [ ] **Step 4: Run the backend suite**

Run: `pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes.py tests/test_api_routes.py
git commit -m "feat(api): cached /reflection endpoint for last completed week"
```

---

### Task 12: Reflection card on the Scorecard

**Files:**
- Modify: `frontend/src/api.ts` (204 handling)
- Modify: `frontend/src/screens/Scorecard.tsx`
- Modify: `frontend/src/styles.css` (append)

**Interfaces:**
- Consumes: Task 11's `GET /reflection` (200 JSON or 204).
- Produces: `apiGet` returns `null` on 204. Reflection card renders only when text exists.

- [ ] **Step 1: Handle 204 in `frontend/src/api.ts`**

In `handle<T>`, before the `resp.json()` return:

```ts
  if (resp.status === 204) return null as T;
```

- [ ] **Step 2: Add the card to `frontend/src/screens/Scorecard.tsx`**

```tsx
interface Reflection { week_start: string; text: string }
```

```tsx
  const [reflection, setReflection] = useState<Reflection | null>(null);

  useEffect(() => {
    apiGet<Reflection | null>("/reflection").then(setReflection).catch(() => setReflection(null));
  }, []);
```

At the bottom of the screen (after Noticings, outside the `hasAnyData` guard):

```tsx
      {reflection && (
        <>
          <p className="section-label">Last week</p>
          <p className="reflection">{reflection.text}</p>
        </>
      )}
```

- [ ] **Step 3: Append CSS to `frontend/src/styles.css`**

```css
/* ── Reflection ── */
.reflection {
  background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
  padding: 14px 16px; margin: 0; font-size: 14px; line-height: 1.55; color: var(--ink-2);
}
```

- [ ] **Step 4: Verify everything**

Run: `cd frontend && npm test && npm run build`, then `pytest tests/ -v`
Expected: all PASS. Manual: card shows last week's paragraph; with `ANTHROPIC_API_KEY` broken the Scorecard renders normally with no card and no error.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.ts frontend/src/screens/Scorecard.tsx frontend/src/styles.css
git commit -m "feat(frontend): weekly reflection card"
```
