# Social Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development. Implement area by area, tests first, one commit per area.

**Goal:** Manually add social events (name/date/cost), rename or overturn detected ones, feed those overrides back into AI classification, and surface weekly social spend.

**Spec:** `docs/superpowers/specs/2026-07-22-social-editing-design.md` — read it; it is the authority on behavior. This plan pins the code shape.

## Global Constraints

- `database.py` only SQL; `config.py` only env; `ai_metrics.py` only Claude calls, `_call_json` pattern, `MODEL` unchanged.
- Schema changes need a REAL migration (both engines), following the `delivery_orders.amount` precedent already in `_init_v2_tables()`.
- Resolution is done in SQL so every caller agrees: title = `COALESCE(user_title, title)`, social = `COALESCE(user_is_social, is_social)`.
- The calendar scan must keep overwriting `title` and must never touch `user_title`, `user_is_social`, `source`, or `amount`.
- Manual events: `gcal_event_id = "manual:" + uuid4().hex`, `source='manual'`, `is_social` true, `start_at = f"{date}T12:00:00"`, `end_at = f"{date}T13:00:00"`.
- Future dates rejected (reuse `_parse_date` in `app/routes.py`); `amount` must be a non-negative number or null.
- Backend `pytest tests/ -v`; frontend `cd frontend && npm test -- --run && npm run build`. No commits with failing checks.

---

### Area 1: Schema + DB layer (`database.py`)

- [ ] Tests first in `tests/test_database_v2.py`: migration idempotent (call `initialize_db()` twice); `add_manual_social_event` then `get_social_events_range` returns it; `set_event_overrides` rename survives a subsequent `upsert_calendar_event` (resolved title stays the user's); `user_is_social=False` removes a detected event from `get_social_events_range`; `user_is_social=True` adds a non-social one; `get_classification_examples` returns only overridden rows, newest first, capped.

- [ ] Migration in `_init_v2_tables()`, mirroring the existing `delivery_orders.amount` block:

```python
        bool_t = _bool_type()
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
```

- [ ] Update the two social read queries to resolve overrides. Both currently filter `WHERE is_social = {_social_true()}`; both become `WHERE COALESCE(user_is_social, is_social) = {_social_true()}`, and both SELECT `COALESCE(user_title, title) AS title` plus `source`, `amount`. Keep column names identical so existing callers are unaffected:

```python
def get_social_events_range(start_day, end_day):
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""SELECT gcal_event_id, COALESCE(user_title, title) AS title, start_at, end_at,
                       source, amount
                FROM calendar_events
                WHERE COALESCE(user_is_social, is_social) = {_social_true()}
                  AND substr(end_at, 1, 10) >= {p} AND substr(end_at, 1, 10) <= {p}
                ORDER BY start_at""",
            (start_day, end_day),
        )
        return [dict(r) for r in c.fetchall()]
```

`get_events_for_day` gets the same treatment (its filter stays on `start_at`).

- [ ] New functions:
  - `add_manual_social_event(event_id, title, start_at, end_at, amount=None)` — INSERT with `is_social` true, `source='manual'`, `confidence=1.0`, `classified_at=CURRENT_TIMESTAMP`.
  - `get_event(event_id)` — full row dict or None (used for 404s and source checks).
  - `set_event_overrides(event_id, user_title=None, user_is_social=None, amount=None, _unset=object())` — build a partial UPDATE from only the arguments explicitly provided. Simplest correct approach: accept a dict of column→value and update exactly those keys; do NOT overwrite unmentioned columns with None.
  - `delete_event(event_id)`.
  - `get_classification_examples(limit=10)` — `SELECT COALESCE(user_title, title) AS title, user_is_social FROM calendar_events WHERE user_is_social IS NOT NULL ORDER BY id DESC LIMIT ?`.

### Area 2: API (`app/routes.py`)

- [ ] Tests first in `tests/test_api_routes.py`: create manual event → appears in `/today?date=` and increments the scorecard's social count; future date → 400; negative amount → 400/422; PATCH renames (subsequent GET shows new title); PATCH `is_social=false` drops it from the count; DELETE manual → gone; DELETE a gcal event → 400; PATCH/DELETE unknown id → 404; `social_spend` appears on the scorecard and sums amounts.

- [ ] Routes (pydantic bodies; `uuid4` imported at module level):

```python
class SocialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    date: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0)


class SocialPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    is_social: Optional[bool] = None
    amount: Optional[float] = Field(default=None, ge=0)


@router.post("/social")
def post_social(body: SocialCreate):
    day = (_parse_date(body.date) if body.date else _local_today()).isoformat()
    event_id = "manual:" + uuid4().hex
    db.add_manual_social_event(event_id, body.name, f"{day}T12:00:00", f"{day}T13:00:00", body.amount)
    return {"gcal_event_id": event_id, "title": body.name, "start_at": f"{day}T12:00:00",
            "end_at": f"{day}T13:00:00", "source": "manual", "amount": body.amount}


@router.patch("/social/{event_id}")
def patch_social(event_id: str, body: SocialPatch):
    if db.get_event(event_id) is None:
        raise HTTPException(status_code=404, detail="event not found")
    updates = {}
    if body.title is not None:
        updates["user_title"] = body.title
    if body.is_social is not None:
        updates["user_is_social"] = body.is_social
    if body.amount is not None:
        updates["amount"] = body.amount
    if updates:
        db.set_event_overrides(event_id, updates)
    return {"ok": True}


@router.delete("/social/{event_id}")
def delete_social(event_id: str):
    ev = db.get_event(event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="event not found")
    if ev.get("source") != "manual":
        raise HTTPException(status_code=400, detail="detected events can only be turned off, not deleted")
    db.delete_event(event_id)
    return {"ok": True}
```

(Match `set_event_overrides`'s real signature to whatever Area 1 defines — dict-of-updates is the recommended shape.)

- [ ] `app/scorecard.py`: `scorecard_for_week` adds `social_spend` = `round(sum(e["amount"] or 0 for e in social_events_of_the_week), 2)`, where the events are the `_occurred`-filtered social events already computed for the week. `today_snapshot`'s `social_events` rows now naturally include `gcal_event_id`, `source`, `amount` from the updated query — verify they pass through.

### Area 3: Learning (`ai_metrics.py`, `jobs/scan_calendar.py`)

- [ ] Tests first in `tests/test_ai_metrics.py`: prompt contains `'"Taco Tuesday" IS social'` when examples are passed; prompt contains no "corrected past classifications" block when examples are empty/None.

- [ ] `classify_social_event(title, description, location, attendees, examples=None)` — build and insert:

```python
    example_block = ""
    if examples:
        lines = "\n".join(
            f'- "{e["title"]}" IS {"" if e["user_is_social"] else "NOT "}social' for e in examples
        )
        example_block = f"\nThe user has corrected past classifications:\n{lines}\n"
```

Insert `{example_block}` into the prompt above the `Title:` line. Everything else unchanged.

- [ ] `jobs/scan_calendar.py`: fetch `examples = db.get_classification_examples()` once before the loop and pass `examples=examples` to each `classify_social_event` call.

### Area 4: Frontend (`Today.tsx`, `Scorecard.tsx`, `styles.css`)

- [ ] `Today.tsx`:
  - `TodayData.social_events` item type gains `gcal_event_id: string; source: string; amount: number | null`.
  - Add an "Add social event" button under the "Noticed quietly" list that toggles an inline form: name input, optional cost input, Save / Cancel. Save posts `{name, date: data.date, amount}` to `/social`, then `refresh()`.
  - Make each social event row tappable: opens an inline editor for that event with name input (prefilled), a "Counts as social" checkbox (default checked), cost input, Save (PATCH), Delete (only when `source === "manual"`), Cancel. After any mutation, `refresh()`.
  - Errors from these calls set the existing `error` state (do not crash the screen).
  - Reuse existing classes (`.item`, `.chips`, `.field-num`, `.quiet`) where sensible; add minimal new CSS only if needed, using existing OKLCH tokens.
- [ ] `Scorecard.tsx`: `Card` gains `social_spend: number`; on the social metric row's `.m-sub`, append `· $X spent` when `key === "social" && card.social_spend > 0`, formatted exactly like the existing delivery-spend line.
- [ ] Verify `cd frontend && npm test -- --run && npm run build`.

### Commits

One per area: `feat(db): social overrides, manual events, migration`, `feat(api): social create/patch/delete + social spend`, `feat(ai): classification learns from user overrides`, `feat(frontend): add and edit social events`.
