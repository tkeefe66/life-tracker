# Social Editing — Implementation Report

Branch: `social-editing` (worktree: `/Users/tomkeefe/Code Apps/weekly-updates/.claude/worktrees/agent-a6495c1541db7f0f7`)

## Commits (one per area, all with Co-Authored-By trailer)

1. `ae2cc6d` feat(db): social overrides, manual events, migration
2. `b7a1340` feat(api): social create/patch/delete + social spend
3. `a27c68a` feat(ai): classification learns from user overrides
4. `490748e` feat(frontend): add and edit social events

## Area 1 — Schema + DB layer (`database.py`)

- Migration in `_init_v2_tables()` adding `user_title` (TEXT), `user_is_social` (BOOLEAN/INTEGER),
  `source` (TEXT DEFAULT 'gcal'), `amount` (REAL) to `calendar_events` — Postgres via
  `ADD COLUMN IF NOT EXISTS`, SQLite via `PRAGMA table_info` guard, mirroring the
  `delivery_orders.amount` precedent exactly.
- `get_social_events_range` / `get_events_for_day` now resolve
  `COALESCE(user_title, title) AS title` and filter on
  `COALESCE(user_is_social, is_social)`, and also SELECT `source`, `amount`. Column names
  kept identical (`title`) so existing callers (scorecard, insights, today) needed no changes.
- New functions: `add_manual_social_event`, `get_event`, `set_event_overrides` (dict-of-updates
  partial UPDATE — only mentioned columns are touched, confirmed by test), `delete_event`,
  `get_classification_examples`.
- `upsert_calendar_event` was **not** touched — it still only writes `title`/`start_at`/`end_at`,
  never `user_title`/`user_is_social`/`source`/`amount`, which is what makes the rename-survival
  behavior work.

### TDD evidence
Wrote 12 new tests in `tests/test_database_v2.py` first, confirmed all failed with
`AttributeError: module 'database' has no attribute ...` (red), then implemented until green.
Notable test: `test_user_rename_survives_calendar_scan_upsert` — sets a classification, applies
`set_event_overrides(ev, {"user_title": ...})`, then calls `upsert_calendar_event` again with a
different Google title (simulating the nightly scan), and asserts the **resolved** title in
`get_social_events_range` is still the user's rename while the raw `title` column in `get_event`
did update to the new Google value. This is the critical-correctness test called out in the task.

## Area 2 — API (`app/routes.py`, `app/scorecard.py`)

- `POST /api/social` — creates a manual event (`gcal_event_id = "manual:" + uuid4().hex`,
  `is_social` true, `source='manual'`), reuses `_parse_date` for future-date rejection, `amount`
  validated `ge=0` by pydantic (422 on negative).
- `PATCH /api/social/{event_id}` — 404 if unknown; builds a dict of only the fields present in
  the body and passes to `set_event_overrides`.
- `DELETE /api/social/{event_id}` — 404 if unknown, 400 if `source != "manual"` (gcal events are
  turned off via `PATCH is_social:false`, never deleted).
- `scorecard_for_week` adds `social_spend` = sum of `amount` (NULL as 0) over the week's
  `_occurred`-filtered social events, mirroring `delivery_spend`.

### TDD evidence
14 new tests added to `tests/test_api_routes.py` (create/today passthrough, future-date 400,
negative-amount 422, rename via PATCH visible on next GET, `is_social:false` dropping the
scorecard count, delete-manual success, delete-gcal 400, 404s on PATCH/DELETE, `social_spend`
summing). Ran red (all failed with 404/KeyError) before implementing routes, then green.

## Area 3 — Learning (`ai_metrics.py`, `jobs/scan_calendar.py`)

- `classify_social_event(..., examples=None)` — when `examples` truthy, inserts a
  `The user has corrected past classifications:` block with
  `- "<title>" IS[ NOT] social` lines above the `Title:` line; omitted entirely otherwise.
  `_call_json` pattern and `MODEL` unchanged.
- `jobs/scan_calendar.py` fetches `db.get_classification_examples()` once per run (before the
  loop) and passes `examples=examples` to every `classify_social_event` call.
- Had to update the two pre-existing `scan_calendar` test lambdas (`test_scan_classifies_new_events`,
  `test_scan_does_not_reclassify`) to accept the new `examples=None` kwarg — they were calling the
  mocked function positionally with 4 args and broke once the job started passing `examples=`.

### TDD evidence
3 new ai_metrics tests (examples present → block + lines appear; examples None/empty → block
omitted) and 1 new scan_calendar test (`test_scan_passes_classification_examples_to_each_call`,
seeds a prior override, asserts every classification call in the run received a non-None
`examples` list containing it). Confirmed red (missing kwarg / `assert None is not None`) before
implementing.

## Area 4 — Frontend (`Today.tsx`, `Scorecard.tsx`, `styles.css`)

- `Today.tsx`: `SocialEvent` type gains `gcal_event_id`, `source`, `amount`. "+ Add social event"
  affordance toggles an inline form (name, optional cost, Save/Cancel) that POSTs to `/social`
  with `date: data.date`. Each social row in "Noticed quietly" is now a button that opens an
  inline editor (name input prefilled, "Counts as social" checkbox default checked, cost field,
  Cancel, Delete [only `source === "manual"`], Save → PATCH). All mutations call `refresh()`;
  failures set the existing `error` state via try/catch (never throw uncaught).
- `Scorecard.tsx`: `Card` gains `social_spend: number`; social row's `.m-sub` appends
  `· $X spent` when `card.social_spend > 0`, formatted identically to the delivery-spend line.
- `styles.css`: minimal additions (`button.quiet-btn`, `.social-form` and its children,
  `.add-social-btn`) using only existing OKLCH tokens (`--surface`, `--surface-2`, `--line`,
  `--accent`, `--accent-ink`, `--danger`, `--r`, `--muted`) — no new color values introduced, both
  themes inherit automatically.
- Per spec/plan, no new pure logic was added to `lib.ts`, so no new vitest unit tests were
  required — verified via `npm test -- --run` (existing 12 tests) + `npm run build`
  (`tsc --noEmit && vite build`), both green.

## Verification commands + results (from this worktree)

```
./venv/bin/pytest tests/ -q
→ 115 passed, 2 warnings in ~1.6s   (was 89 passed at baseline before this work)

cd frontend && npm test -- --run
→ Test Files 1 passed (1), Tests 12 passed (12)

cd frontend && npm run build
→ tsc --noEmit && vite build — succeeded, 40 modules transformed, no type errors
```

Lint: `./venv/bin/ruff check` on every changed backend file — all clean, no findings.

## Files changed

- `database.py`
- `app/routes.py`
- `app/scorecard.py`
- `ai_metrics.py`
- `jobs/scan_calendar.py`
- `frontend/src/screens/Today.tsx`
- `frontend/src/screens/Scorecard.tsx`
- `frontend/src/styles.css`
- `tests/test_database_v2.py`
- `tests/test_api_routes.py`
- `tests/test_ai_metrics.py`
- `tests/test_scan_calendar.py`

## Self-review findings

- All 4 areas implemented per the plan's code shapes; spec's "Out of Scope" list (no
  write-back to Google Calendar, no rule engine, no per-person tracking, no editing
  non-social events, no retroactive re-classification) was respected — nothing added
  beyond it.
- Full backend suite green (115/115) and frontend green (12/12 tests + clean build) from
  this worktree, confirmed on a final combined re-run after all 4 commits.
- The scan-overwrite-survival test and the two override-resolution tests
  (`user_is_social=False` removes, `=True` adds) genuinely exercise SQL resolution, not
  just mock behavior — confirmed by reading the SQL and running them red-then-green.
- Two pre-existing `scan_calendar` tests needed a compatible-signature update (added
  `examples=None` to their monkeypatched lambdas) since the job now always passes
  `examples=`; this is a necessary consequence of Area 3, not scope creep — call it out
  in case reviewers want it as a separate note.
- `git status --short` is clean (no stray files); each commit's diff is scoped to its area
  (checked via `git status --short` mid-flight after each commit).
- One design judgment call: the frontend "Counts as social" checkbox always defaults to
  checked/true when opening the editor, since the row can only be present in the list if
  it currently resolves to social — this matches the spec's description ("a 'Counts as
  social' toggle") without needing an extra round-trip to fetch the raw `is_social` value.

## Concerns

- None blocking. The only note-worthy side effect is the two `scan_calendar` test lambda
  signature updates described above — pre-existing tests, not new scope, but flagging in
  case it's reviewed as an unexpected diff.

---

## Review fix wave

Code review of the above found 6 issues (3 IMPORTANT, 3 MINOR). All fixed on
`social-editing`, one commit, tests added.

### 1 (IMPORTANT) — Editor persisted overrides the user never made

- `database.py`: `get_social_events_range` / `get_events_for_day` now also SELECT
  `COALESCE(user_is_social, is_social) AS is_social`, cast to a real Python `bool`
  (`_social_rows` helper — SQLite returns 0/1 ints) so the frontend can read the
  checkbox's true initial state instead of guessing.
- `frontend/src/lib.ts`: new pure `buildSocialPatch(state)` diffs the editor's current
  fields against what was loaded and returns **only** the fields that actually changed
  — `title` only if the trimmed text differs from the loaded title, `is_social` only if
  the checkbox was actually toggled, `amount` only if the number changed (or explicit
  `null` if a stored amount was cleared — see #3).
- `frontend/src/screens/Today.tsx`: `SocialEvent` gains `is_social: boolean`.
  `openEditSocial` now initializes `editIsSocial` from `e.is_social` (was hardcoded
  `true`) and records the loaded snapshot in new `editLoaded` state. `saveEditSocial`
  calls `buildSocialPatch` and only PATCHes when the resulting patch is non-empty —
  opening a detected event just to add a cost no longer writes `user_title` or
  `user_is_social` and no longer pollutes `get_classification_examples`.

### 2 (IMPORTANT) — Manual event before 1pm local didn't count toward the week

- `app/scorecard.py`: new `_social_counts(e)` helper —
  `e.get("source") == "manual" or _occurred(e["end_at"])` — replaces the bare
  `_occurred(...)` gate everywhere social events are filtered: `counts_for_week`,
  the `social_spend` sum in `scorecard_for_week`, and `_date_lists`. Manual events (a
  user-asserted fact) now count the instant they're created, regardless of the
  synthetic 12:00–13:00 span or what time of day it currently is.
- Added the plan-required test that had been dropped:
  `tests/test_api_routes.py::test_post_social_increments_scorecard_social_count` —
  POSTs a manual event with no explicit date/time and asserts the scorecard's social
  count increments by 1, robust to any time of day the suite runs (manual events skip
  the occurrence gate entirely, so there's no time-of-day dependency to be flaky about).
  Also added `tests/test_scorecard.py::test_manual_social_event_counts_before_it_would_have_occurred`
  at the `counts_for_week`/`scorecard_for_week` level, deliberately setting `end_at` to
  23:58–23:59 today (a time an occurrence gate would still exclude in a fresh run) to
  prove the exemption, and asserting `social_spend` picks it up too.

### 3 (IMPORTANT) — Cost and rename could not be cleared

- `app/routes.py`: `patch_social` now reads `body.model_fields_set` (pydantic v2 —
  confirmed installed version 2.13.4) instead of `is not None` checks, so
  `{"amount": null}` or `{"title": null}` explicitly clears the stored override while
  an **omitted** key leaves it untouched — the two cases were previously
  indistinguishable.
- `frontend/src/lib.ts` (`buildSocialPatch`): an emptied cost field now becomes
  `amount: null` in the diff (not omitted) whenever the loaded amount was non-null;
  if the field was already empty and stays empty, `amount` is omitted (no-op PATCH).
- Tests: `tests/test_api_routes.py::test_patch_social_clears_amount_with_explicit_null`,
  `::test_patch_social_omitted_amount_leaves_it_untouched`,
  `::test_patch_social_clears_title_override_with_explicit_null` — all written red
  first (asserted against the pre-fix code, confirmed failing), then green after the
  `model_fields_set` change. `lib.test.ts` covers the frontend diff/null logic directly
  (`buildSocialPatch` describe block, 8 cases).

### 4 (MINOR) — Add and edit forms could both be open at once

- `Today.tsx`: `openAddSocial` now calls `setEditingId(null)`; `openEditSocial` now
  calls `setAddingSocial(false)`. Each opening the other now closes it.

### 5 (MINOR) — `.quiet + .quiet` separator regression

- `Today.tsx`: the social-row wrapper `<div>` now carries `className="quiet-row"`.
- `styles.css`: `.quiet + .quiet` expanded to cover all four adjacency combinations
  (`.quiet + .quiet`, `.quiet + .quiet-row`, `.quiet-row + .quiet`,
  `.quiet-row + .quiet-row`) with the same `border-top: 1px solid var(--line)` — no new
  tokens, minimal selector-only fix. Restores the divider between the delivery list and
  social rows, and between consecutive social rows.

### 6 (MINOR) — `e.amount ? …` hid a $0 cost

- `Today.tsx`: changed to `e.amount !== null ? … : …`, consistent with the backend
  treating `0` as a real recorded amount (same pattern already used for delivery
  amounts elsewhere).

### Verify commands + output (from this worktree)

```
source venv/bin/activate && pytest tests/ -v
→ 121 passed, 2 warnings in 1.78s   (115 baseline + 6 new: 1 in test_scorecard.py,
   5 in test_api_routes.py, 1 in test_database_v2.py — one test folded two assertions)

cd frontend && npm test -- --run
→ Test Files 1 passed (1), Tests 20 passed (20)   (12 baseline + 8 new buildSocialPatch cases)

cd frontend && npm run build
→ tsc --noEmit && vite build — 40 modules transformed, no type errors, succeeded
```

### Files changed (this wave)

- `database.py` — `is_social` exposed on social event rows, cast to bool
- `app/routes.py` — `patch_social` uses `model_fields_set`
- `app/scorecard.py` — `_social_counts` exemption for manual events
- `frontend/src/lib.ts` — `buildSocialPatch` pure diff helper
- `frontend/src/screens/Today.tsx` — editor init, diff-based PATCH, mutual-exclusion,
  `is_social`, `quiet-row` wrapper, `!== null` amount check
- `frontend/src/styles.css` — `.quiet-row` separator selectors
- `tests/test_scorecard.py`, `tests/test_api_routes.py`, `tests/test_database_v2.py`,
  `frontend/src/lib.test.ts` — new tests
