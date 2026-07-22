# Substances Metric Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development. Two tasks; controller merges/deploys.

**Goal:** Fifth metric "Substances" — daily yes/no check-in like Gym, ceiling target defaulting to 0, full on-screen treatment, excluded from AI reflection and Telegram push.

**Spec:** `docs/superpowers/specs/2026-07-22-substances-metric-design.md`

## Global Constraints

- Metric key `substances`, label `Substances`, `direction: ceiling`, `default_target: 0`, `private: True`. Other `METRICS` entries are NOT rewritten — read the flag with `.get("private")`.
- Privacy is enforced in exactly two places: `jobs/weekly_push.py` message builder and the `/api/reflection` route. On-screen surfaces include the metric everywhere.
- No DB schema change — `checkins.type = "substances"`, no level.
- This branch lands AFTER `delivery-spend`; `Scorecard.tsx`/`Settings.tsx`/`app/scorecard.py` may already contain delivery-spend code — integrate additively, never revert it.
- Backend `pytest tests/ -v`; frontend `cd frontend && npm test -- --run && npm run build`. TDD per area; no commits with failing checks.

---

### Task 1: Backend

**Files:** `metrics.py`, `app/scorecard.py`, `app/routes.py`, `jobs/weekly_push.py`
**Tests:** `tests/test_api_routes.py`, `tests/test_weekly_push.py`, `tests/test_metrics.py`

- [ ] **Tests first** (adapt to each file's existing fixtures/style):

```python
# test_metrics.py
def test_substances_metric_defined():
    from metrics import METRICS
    m = METRICS["substances"]
    assert (m["label"], m["direction"], m["default_target"], m.get("private")) == \
        ("Substances", "ceiling", 0, True)


# test_api_routes.py
def test_substances_checkin_roundtrip(temp_db_path):
    client = _client(temp_db_path)
    assert client.post("/api/checkins", json={"type": "substances"}).status_code == 200
    snap = client.get("/api/today").json()
    assert snap["substances"] is True
    card = client.get("/api/scorecard").json()
    assert card["metrics"]["substances"]["count"] == 1
    assert card["metrics"]["substances"]["hit"] is False  # ceiling 0: any day is a miss
    assert client.delete("/api/checkins/substances").status_code == 200
    assert client.get("/api/today").json()["substances"] is False
    assert client.get("/api/scorecard").json()["metrics"]["substances"]["hit"] is True


def test_reflection_excludes_private_metrics(temp_db_path, mock_anthropic):
    # canned response per this file's existing reflection tests
    client = _client(temp_db_path)
    past = (datetime.date.today() - datetime.timedelta(days=8)).isoformat()
    client.post("/api/checkins", json={"type": "substances", "date": past})
    client.get("/api/reflection")
    prompt = mock_anthropic.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Substances" not in prompt


# test_weekly_push.py
def test_push_text_excludes_private_metrics():
    from jobs.weekly_push import format_scorecard_text
    import metrics as m
    card = m.build_scorecard(datetime.date(2026, 7, 13),
                             {"gym": 3, "substances": 2}, {})
    text = format_scorecard_text(card)
    assert "Gym" in text
    assert "Substances" not in text
```

(For the reflection test, set the mock's canned response exactly as the existing reflection tests in the file do; note `weekly_reflections` caching — use a fresh DB so no cache exists. `datetime` import as needed per file.)

- [ ] **Implement:**

`metrics.py` — add to `METRICS` (after `alcohol`):

```python
    "substances": {"label": "Substances", "direction": "ceiling", "default_target": 0, "private": True},
```

`app/scorecard.py` — in `counts_for_week`: add `"substances": sum(1 for c in checkins if c["type"] == "substances")`. In `today_snapshot`: add `"substances": any(c["type"] == "substances" for c in checkins)`. In `_date_lists`: add `"substances": [c["date"] for c in checkins if c["type"] == "substances"]`.

`app/routes.py` — `CheckinBody.type: Literal["gym", "alcohol", "substances"]`; delete route path type likewise. In `get_reflection`, replace the card/noticings handoff with:

```python
    card = scorecard_for_week(week_start)
    private_labels = [m["label"] for m in metrics.METRICS.values() if m.get("private")]
    card = {**card, "metrics": {k: v for k, v in card["metrics"].items()
                                if not metrics.METRICS.get(k, {}).get("private")}}
    notes = [n for n in insights(12)["noticings"]
             if not any(lbl in n for lbl in private_labels)]
    text = ai_metrics.weekly_reflection(card, notes)
```

`jobs/weekly_push.py` — `format_scorecard_text` iterates `card["metrics"].items()` and skips `metrics.METRICS.get(key, {}).get("private")` entries.

- [ ] Full suite green → commit `feat(api): substances metric — private ceiling check-in`.

---

### Task 2: Frontend

**Files:** `frontend/src/screens/Today.tsx`, `frontend/src/screens/Scorecard.tsx`, `frontend/src/screens/Settings.tsx`

- [ ] `Today.tsx`: `TodayData` gains `substances: boolean`. Add a `toggleSubstances` handler mirroring `toggleGym` (`POST {type: "substances", date}` / `DELETE /checkins/substances?date=`). Render a third item between Gym and Alcohol styled exactly like the Gym button:

```tsx
        <button className={`item${data.substances ? " done" : ""}`} onClick={toggleSubstances}>
          <span className="dot">{data.substances ? "✓" : ""}</span>
          <span className="txt">
            <span className="t">Substances</span>
            <span className="s">{data.substances ? "Logged — tap to undo" : "Tap to log a day"}</span>
          </span>
        </button>
```

Add `"substances"` (last) to `STRIP_ORDER` and `STRIP_LABELS` (`substances: "Subst."`).

- [ ] `Scorecard.tsx`: add `"substances"` (last) to `ORDER`. Everything else (meter, trend, heatmap caution, streaks) follows from the API data.
- [ ] `Settings.tsx`: add `substances: "Substances"` to `LABELS`.
- [ ] `cd frontend && npm test -- --run && npm run build` green → commit `feat(frontend): substances check-in and scorecard row`.
