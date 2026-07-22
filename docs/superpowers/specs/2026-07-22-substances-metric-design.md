# Substances Metric

**Date:** 2026-07-22
**Status:** Approved

## Problem

The user wants to track drug use as a fifth metric — a daily yes/no manual
check-in like Gym — scored against an abstinence-oriented weekly ceiling, but
kept out of externally-transmitted surfaces (AI reflection, Telegram push).

## Decisions

- Label: **Substances**. Metric key: `substances`.
- Direction: ceiling; default target **0** (any logged day is a miss until the
  target is raised in Settings).
- Binary daily check-in (no level), stored as `checkins` rows with
  `type = "substances"` — no schema change.
- Visibility: full treatment in Scorecard and insights (ledger, meter, streak,
  trend chart, heatmap with caution tint, noticings). **Excluded** from the AI
  weekly reflection and the Telegram weekly push.

## Design

### 1. Metric definition (`metrics.py`)

- `METRICS` gains `"substances": {"label": "Substances", "direction":
  "ceiling", "default_target": 0, "private": True}`.
- Existing entries implicitly `private: False` (use `.get("private")` at read
  sites — do not rewrite the other entries).
- Everything driven by `METRICS` follows automatically: `seed_default_targets`,
  `build_scorecard`, `streaks`, noticings loops.

### 2. Counting (`app/scorecard.py`)

- `counts_for_week`: `"substances": sum(1 for c in checkins if c["type"] ==
  "substances")`.
- `today_snapshot`: gains `"substances": any(c["type"] == "substances" for c
  in checkins)`.
- `_date_lists` (insights pattern window): `"substances": [c["date"] for c in
  checkins if c["type"] == "substances"]`.

### 3. API (`app/routes.py`)

- `CheckinBody.type` and the delete route's path `Literal` gain
  `"substances"`. No level validation applies (level stays alcohol-only).

### 4. Privacy enforcement

- `jobs/weekly_push.py`: when building the Telegram message, skip metrics
  whose `METRICS[key].get("private")` is true.
- `GET /api/reflection` (`app/routes.py`): before calling
  `ai_metrics.weekly_reflection`, strip private metrics from the card's
  `metrics` dict and drop noticing strings containing a private metric's
  label. `ai_metrics.py` itself is unchanged.

### 5. Frontend

- **Today** (`Today.tsx`): third manual item, rendered like Gym — dot, title
  "Substances", subtitle "Tap to log a day" / "Logged — tap to undo". Posts
  `{type: "substances", date}` / `DELETE /checkins/substances?date=`. Uses the
  `substances` boolean from `/today`.
- **Order arrays**: `STRIP_ORDER`/`STRIP_LABELS` (Today) and `ORDER`
  (Scorecard) gain `substances` (last position). `LABELS` in Settings gains
  `Substances`.
- Heatmap caution tint and over-meter styling follow automatically from
  `direction === "ceiling"`.

## Testing

- pytest: substances check-in roundtrip via API (post, snapshot flag, delete);
  scorecard counts + hit logic at target 0 (0 days = hit, 1 day = miss);
  weekly push message contains no "Substances"; reflection prompt (via mock)
  contains no "Substances" even when a substances noticing exists.
- vitest/build: no new pure logic — build + existing suite.

## Out of Scope

- Levels/quantity, per-substance breakdown, retroactive PIN/lock UI,
  hiding from the on-screen insights.
