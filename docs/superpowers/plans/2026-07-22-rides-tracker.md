# Rides Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development. Implement area by area, tests first, one commit per area.

**Goal:** Track Uber/Lyft rides and spend from Gmail receipts, with an AI work/personal pre-flag and a user override that excludes work rides and teaches future classification.

**Spec:** `docs/superpowers/specs/2026-07-22-rides-tracker-design.md` — read it first; it is the authority on behavior.

## Global Constraints

- `database.py` only SQL; `config.py` only env; `ai_metrics.py` only Claude calls, `_call_json` pattern, `MODEL` unchanged.
- Rides are **not** in `METRICS` — no target, no hit/miss, no scorecard ledger row. Do not add one.
- A ride is excluded ONLY when `user_is_work` is true. `ai_is_work` never excludes on its own ("flag but still count until confirmed").
- Existing delivery behavior must not regress: the delivery path (including tip-only recovery and cluster dedupe) keeps working exactly as today, and all existing tests in `tests/test_scan_gmail.py` stay green.
- Backend `pytest tests/ -v`; frontend `cd frontend && npm test -- --run && npm run build`. No commits with failing checks.

---

### Area 1: Rules (`receipts.py`)

- [ ] Tests first (`tests/test_receipts.py`):

```python
def test_ride_domains_and_classify_ride():
    from receipts import classify_ride
    assert classify_ride("noreply@uber.com", "Your Sunday morning trip with Uber") == ("ride", "Uber")
    assert classify_ride("no-reply@lyft.com", "Your ride with Lyft") == ("ride", "Lyft")
    assert classify_ride("noreply@uber.com", "Your Monday order with Uber Eats")[0] == "not_ride"
    assert classify_ride("noreply@uber.com", "50% off your next ride")[0] == "not_ride"
    assert classify_ride("someone@example.com", "Your trip")[0] == "not_ride"
    assert classify_ride("noreply@uber.com", "Reservation confirmed for Saturday")[0] == "ambiguous"


def test_extract_ride_time():
    from receipts import extract_ride_time
    assert extract_ride_time("Jul 19, 2026 4:03 AM Thanks for riding") == "2026-07-19T04:03"
    assert extract_ride_time("Jul 19, 2026 11:34 PM charge summary") == "2026-07-19T23:34"
    assert extract_ride_time("no timestamp here") is None
    assert extract_ride_time(None) is None
```

- [ ] Implement in `receipts.py` (keep `DELIVERY_DOMAINS` and existing functions untouched):

```python
RIDE_DOMAINS = {"uber.com": "Uber", "lyft.com": "Lyft"}

_ORDER_WORDS_RE = re.compile(r"\b(order|eats)\b", re.IGNORECASE)
_RIDE_TIME_RE = re.compile(
    r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})[, ]+(\d{1,2}):(\d{2})\s*([AP]M)"
)
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _ride_domain(sender: str) -> str:
    m = re.search(r"@([\w.-]+)", sender)
    if not m:
        return ""
    domain = m.group(1).lower().rstrip(">")
    for known in RIDE_DOMAINS:
        if domain == known or domain.endswith("." + known):
            return known
    return ""


def classify_ride(sender: str, subject: str) -> tuple:
    """('ride'|'not_ride'|'ambiguous', service)."""
    domain = _ride_domain(sender)
    if not domain:
        return "not_ride", ""
    service = RIDE_DOMAINS[domain]
    if _PROMO_RE.search(subject):
        return "not_ride", service
    if _ORDER_WORDS_RE.search(subject):
        return "not_ride", service
    if _RIDE_RE.search(subject):
        return "ride", service
    return "ambiguous", service


def extract_ride_time(snippet):
    """'Jul 19, 2026 4:03 AM' -> '2026-07-19T04:03'; None if absent."""
    m = _RIDE_TIME_RE.search(snippet or "")
    if not m:
        return None
    mon, day, year, hour, minute, ampm = m.groups()
    if mon not in _MONTHS:
        return None
    h = int(hour) % 12 + (12 if ampm.upper() == "PM" else 0)
    return f"{year}-{_MONTHS[mon]:02d}-{int(day):02d}T{h:02d}:{minute}"
```

- [ ] Commit: `feat(receipts): ride classification and ride-time parsing`

### Area 2: Schema + DB (`database.py`)

- [ ] Tests first (`tests/test_database_v2.py`): add + fetch by range; `has_ride` dedupe by message id; `find_ride_by_key` hit/miss; `set_ride_amount`; `set_ride_work_override`; `get_ride_examples` returns only overridden rows newest-first, capped; `initialize_db()` twice is idempotent.

- [ ] `_init_v2_tables()` — new table (no column migration needed):

```python
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS rides (
                id {serial} PRIMARY KEY,
                gmail_message_id TEXT NOT NULL UNIQUE,
                service TEXT NOT NULL,
                ride_at TEXT NOT NULL,
                ride_key TEXT,
                subject TEXT DEFAULT '',
                amount REAL,
                ai_is_work {bool_t},
                ai_confidence REAL,
                user_is_work {bool_t},
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
```

- [ ] Functions (follow existing `delivery_orders` equivalents for style and placeholder handling):
  - `has_ride(gmail_message_id) -> bool`
  - `add_ride(gmail_message_id, service, ride_at, ride_key, subject, amount=None) -> bool` (truthy iff inserted)
  - `find_ride_by_key(service, ride_key) -> dict | None`
  - `set_ride_amount(ride_id, amount)`
  - `set_ride_classification(ride_id, is_work, confidence)`
  - `set_ride_work_override(ride_id, is_work)`
  - `get_rides_range(start_day, end_day) -> list[dict]` — filter `substr(ride_at, 1, 10)` between bounds, ORDER BY `ride_at`; each dict includes `id, service, ride_at, subject, amount, ai_is_work, user_is_work`
  - `get_ride_examples(limit=10)` — `WHERE user_is_work IS NOT NULL ORDER BY id DESC LIMIT ?`, returning `subject` + `user_is_work`

- [ ] Commit: `feat(db): rides table and queries`

### Area 3: AI + ingestion (`ai_metrics.py`, `jobs/scan_gmail.py`, `services/gmail_service.py`)

- [ ] Tests first:
  - `tests/test_ai_metrics.py`: `classify_work_ride` returns the parsed dict; prompt contains subject + snippet; examples block present when examples passed, absent when not; garbage JSON → `{"is_work": False, "confidence": 0.0}`.
  - `tests/test_scan_gmail.py`: a ride candidate is stored as a ride and NOT as a delivery order; a delivery candidate still becomes an order (existing tests green); charge-summary + "thanks for riding" with the same ride time → ONE ride, amount updated; two distinct rides same morning (different ride times, identical subject) → TWO rides; scan result string reports rides.

- [ ] `services/gmail_service.py`: the query's sender list becomes the union of `DELIVERY_DOMAINS` and `RIDE_DOMAINS` keys (sorted for a stable string). `_query()` shape otherwise unchanged.

- [ ] `ai_metrics.py`:

```python
def classify_work_ride(service: str, subject: str, snippet: str = "", examples=None) -> dict:
    """Work vs personal ride. Returns {"is_work": bool, "confidence": float}."""
    example_block = ""
    if examples:
        lines = "\n".join(
            f'- "{e["subject"]}" IS {"" if e["user_is_work"] else "NOT "}work' for e in examples
        )
        example_block = f"\nThe user has corrected past classifications:\n{lines}\n"
    prompt = f"""You classify ride-hailing receipts as WORK travel or PERSONAL.

Work rides usually involve airports, hotels, conference venues, out-of-town
addresses, or weekday business hours while travelling. Personal rides are
local trips, nights out, and errands.
{example_block}
Service: {service}
Subject: {subject}
Preview: {snippet[:200]}

Reply with only JSON: {{"is_work": true|false, "confidence": 0.0-1.0}}"""
    result = _call_json(prompt, default={"is_work": False, "confidence": 0.0})
    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {"is_work": bool(result.get("is_work", False)), "confidence": confidence}
```

- [ ] `jobs/scan_gmail.py` — keep the delivery block exactly as-is; add ride handling. Shape:

```python
        candidates = fetch_delivery_candidates()
        ride_examples = db.get_ride_examples()
        added = ai_checked = rides_added = 0
        for cand in candidates:
            if db.has_delivery_order(cand["gmail_message_id"]) or db.has_ride(cand["gmail_message_id"]):
                continue
            snippet = cand.get("snippet", "")
            amount = receipts.extract_amount(snippet)
            day = cand["ordered_at"][:10]

            ride_verdict, ride_service = receipts.classify_ride(cand["sender"], cand["subject"])
            if ride_verdict == "ride":
                key = receipts.extract_ride_time(snippet) or f"{day}|{cand['subject']}|{amount}"
                existing = db.find_ride_by_key(ride_service, key)
                if existing:
                    if amount is not None:
                        db.set_ride_amount(existing["id"], amount)
                    continue
                if db.add_ride(cand["gmail_message_id"], ride_service, cand["ordered_at"],
                               key, cand["subject"], amount):
                    rides_added += 1
                    stored = db.find_ride_by_key(ride_service, key)
                    verdict = ai_metrics.classify_work_ride(
                        ride_service, cand["subject"], snippet, ride_examples)
                    db.set_ride_classification(stored["id"], verdict["is_work"], verdict["confidence"])
                continue

            # ... existing delivery logic unchanged (followup / tip-only / order) ...
```

Note ordering: the ride check runs BEFORE `is_followup`, because ride emails must not be swallowed by follow-up rules; the delivery path keeps its own `is_followup` handling untouched.

- [ ] `gmail_last_result` becomes `f"{len(candidates)} candidates · {ai_checked} AI-checked · {added} new orders · {rides_added} new rides"`; the log line gains the same figure.

- [ ] Commit: `feat(scan): ingest Uber/Lyft rides with work classification`

### Area 4: API + frontend (`app/routes.py`, `app/scorecard.py`, `Today.tsx`)

- [ ] Tests first (`tests/test_api_routes.py`): `GET /api/rides` shape/order/clamp; `PATCH /api/rides/{id}` sets the override, 404 unknown; `rides_spend`/`rides_count` on the scorecard exclude `user_is_work=True` rides and INCLUDE `ai_is_work=True` ones that lack a user verdict.

- [ ] `app/routes.py`:

```python
class RidePatch(BaseModel):
    is_work: bool


@router.get("/rides")
def get_rides(days: int = 60):
    d = min(max(days, 1), 365)
    end = _local_today()
    start = end - datetime.timedelta(days=d)
    rides = db.get_rides_range(start.isoformat(), end.isoformat())
    rides.sort(key=lambda r: r["ride_at"], reverse=True)
    return {"rides": [{**r, "is_work": bool(r["user_is_work"])} for r in rides]}


@router.patch("/rides/{ride_id}")
def patch_ride(ride_id: int, body: RidePatch):
    if not db.set_ride_work_override(ride_id, body.is_work):
        raise HTTPException(status_code=404, detail="ride not found")
    return {"ok": True}
```

(Have `set_ride_work_override` return whether a row was updated so the 404 is real.)

- [ ] `app/scorecard.py`: `scorecard_for_week` adds `rides_count` and `rides_spend` over `db.get_rides_range(week bounds)` counting only rides where `user_is_work` is not true; spend sums `amount or 0`, rounded to 2dp.

- [ ] `Today.tsx`: `/today` already returns per-day passive detections; add `rides` to `today_snapshot` (`db.get_rides_range(day, day)`) and render each in "Noticed quietly" as `"{service} ride"` with amount and a `work?` marker when `ai_is_work` and `user_is_work` is null. Tapping a ride PATCHes `is_work` to the opposite of its resolved value and refreshes. Reuse existing `.quiet` styling; no new CSS unless needed.

- [ ] Commit: `feat(app): rides API, weekly ride spend, and Today ride toggles`
