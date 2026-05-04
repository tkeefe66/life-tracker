# Story-Driven Proposals — Design

**Status:** Draft for implementation
**Date:** 2026-05-04
**Supersedes for the proposal-review surface:** the per-event `Proposals` tab + `/syncproposals` flow

## Goal

Replace the per-event proposal review (a flat sheet of calendar events to confirm one-by-one) with a **story-driven flow**: related events are clustered into narrative "stories" (trips, interview cycles, conferences, holidays, dating arcs, family visits), reviewed first as bulk cards in a Sheet, then confirmed one-by-one in Telegram with a short narrative prompt that captures *why each story mattered*. The result is a richer 30-year memoir than a flat event log can produce.

## Non-Goals

- Replacing `/log` (singleton entry creation stays as today; manual stories from `/log` is V2).
- Automatic background clustering — V1 is on-demand via `/buildstories`.
- Back-filling already-confirmed Life Log entries into stories — V2.
- Split / merge / move-event-between-stories controls in Telegram — V2.
- Performance tuning beyond a few-minute clustering pass.

## End-to-End Flow

```
[Calendar ingest]                       (existing, unchanged)
     │
     ▼
[Pending proposals in DB]               (existing — ~400+ today)
     │
     │  user types /buildstories
     ▼
[AI clustering pass]                    (NEW)
     • date-proximity pre-cluster (consecutive calendar dates with no gap, OR same-date events, fall in the same group; one full empty day between events splits the cluster)
     • flight detection from titles → trip anchors / highlights
     • per-cluster Claude call: classifies story_type + summary + highlights
     ▼
[Stories tab in Sheet]                  (NEW — replaces today's per-event Proposals tab)
     • parent card row + indented child event rows
     • Decision cell only on parent (yes / skip / blank)
     │
     │  user fills decisions, runs /syncstories
     ▼
[Surviving stories enter Telegram queue]   (NEW)
     │
     │  for each, bot sends narrative card and asks:
     │    1. yes / edit summary: <text> / drop #N / skip
     │    2. "Why did this matter?" (B-tier enrichment, every story)
     │    3. (optional) opt-in to type-specific structured questions
     ▼
[Confirmed: parent + children written to Life Log]
     │
     ▼
[Life Log + People + Habits sync to Sheets]   (existing /sync)
```

## Decisions (locked)

| # | Decision | Choice |
|---|---|---|
| 1 | Story shape in Life Log | **Parent + children** — story is parent row, events are child rows linked via `parent_id`. |
| 2 | Review venue | **Hybrid** — Sheet for bulk yes/skip triage; Telegram for narrative confirmation + enrichment of survivors. |
| 3 | Clustering approach | **AI with seeded types** — date-proximity pre-cluster, then Claude classifies with seeded examples (`trip`, `interview_cycle`, `conference`, `holiday_weekend`, `dating_arc`, `project_milestone`, `family_visit`) and may invent new types. **Flights are called out** as both clustering anchors and trip highlights. |
| 4 | Captured story metadata | **B-tier minimum, C-tier opt-in.** Every story gets `summary` + `why_mattered` + auto-extracted `highlights`. Per-type structured fields (`extras`) are optional, captured only when the user opts in for that story. |
| 5 | Singletons + existing pending | **Everything is a story.** A singleton is a story of N=1. Existing 400+ pending get re-processed by `/buildstories` into stories. The current per-event Proposals review surface is retired. |
| 6 | Trigger | **On-demand** `/buildstories` for V1, pending only. Auto-batch and confirmed-entry back-fill are V2. |
| 7 | AI mistake recovery | **Drop individual events** — `drop #N` in Telegram returns the event to the unclustered pool. No split/merge for V1. |

## Data Model

Single self-referential table extension — no new "stories" table.

### `life_log_entries` — new columns

| Column | Type | Nullable | Purpose |
|---|---|---|---|
| `parent_id` | INT FK → `life_log_entries.id` | yes | NULL = parent (or singleton); set = child event of that story |
| `story_type` | TEXT | yes | `trip` / `interview_cycle` / `conference` / `holiday_weekend` / `dating_arc` / `project_milestone` / `family_visit` / `other` / NULL (set only on parents) |
| `why_mattered` | TEXT | yes | B-tier enrichment captured in Telegram (parents only) |
| `highlights` | TEXT (JSON) | yes | List of short strings auto-extracted from child events, optionally edited by the user |
| `extras` | TEXT (JSON) | yes | C-tier structured bag, schema varies by `story_type` |

### Status semantics

- Values unchanged: `proposed` / `confirmed` / `dismissed`.
- Confirming or dismissing a parent flips all `parent_id = parent.id` rows in the same transaction.
- Querying pending stories: `WHERE status = 'proposed' AND parent_id IS NULL`.
- Singletons are parents with zero children — same query, same flow.

### `extras` schema per type (initial)

| story_type | extras keys |
|---|---|
| `trip` | `travel_mode` (flight/drive/train/mixed), `who_came` (list), `most_memorable` (text) |
| `interview_cycle` | `outcome` (hired/no_offer/passed/withdrew/in_progress), `role` (text), `company` (text), `rounds` (int) |
| `conference` | `event_name` (text), `role` (attendee/speaker/organizer), `who_with` (list) |
| `holiday_weekend` | `host` (text), `key_meal` (text), `who_with` (list) |
| `dating_arc` | `partner` (text), `started_or_ended` (start/end), `notes` (text) |
| `project_milestone` | `project` (text), `milestone` (text), `outcome` (text) |
| `family_visit` | `host` (text), `who_with` (list), `occasion` (text or null) |
| `other` | free-form `notes` (text) |

All `extras` values are optional; missing keys are simply absent from the JSON.

### Migration

One migration each for Postgres and SQLite that adds the four columns. Idempotent (checked twice in tests). Existing 400+ pending rows are unaffected until `/buildstories` runs. `parent_id` foreign key allows NULL.

## Components / Module Map

| Module | Responsibility | Status |
|---|---|---|
| `services/story_clustering.py` | Pre-cluster pending proposals by date proximity (gap ≤ 1 day), detect flights, hand each cluster to AI classifier, validate AI output, emit story candidates ready to write | **new** |
| `ai_life_log.cluster_into_story()` | One Claude call per cluster: input = list of events + flights + active categories + seeded story types; output = `{story_type, summary, highlights, event_id_refs, suggested_extras_questions}`; degrades to singleton-stories on parse failure | **new** in `ai_life_log.py` |
| `ai_life_log.parse_extras_answer()` | One Claude call per C-tier follow-up question; parses free-text reply into the `extras` schema for the story's type | **new** in `ai_life_log.py` |
| `database` story helpers | `save_story_parent`, `assign_child_to_story`, `get_pending_stories_with_children`, `confirm_story`, `dismiss_story`, `drop_event_from_story`, `update_story_metadata` | extend `database.py` |
| `google_sheets.sync_stories_to_sheet()` | Renders the Stories tab — parent row + indented child rows, Decision cell on parent only | extend `google_sheets.py` |
| `google_sheets.read_story_decisions()` | Reads parent Decision column; ignores child rows | extend `google_sheets.py` |
| `handlers/buildstories.py` — `/buildstories` | Triggers a clustering pass, writes Stories tab, reports counts via Telegram | **new** |
| `handlers/syncstories.py` — `/syncstories` | Reads sheet decisions, marks each story as surviving or dismissed, enqueues survivors into the Telegram review queue | **new** |
| `handlers/story_review.py` | State machine: `story_confirming` → `story_why_mattered` → `story_extras_optin` → (optional) `story_extras_qN` → next story; handles `drop #N` mid-flow | **new** |
| `bot.py` state machine | Adds new states, queue helpers (`pending_story_ids` + `current_story_id` in `temp_data`), retires per-event proposal states | edit |
| `_COMMANDS_TEXT` | Adds `/buildstories`, `/syncstories`, optional `/showstory <id>`, `/pushstories`. Removes `/proposals`, `/syncproposals`, `/pushproposals` (replaced). Keeps `/dismissbirthdays`, `/calendarbackfill`. | edit |

The Stories tab in the Sheet is a renamed/cleared *Proposals* tab on first run — no orphan tab cleanup needed.

## UX Detail

### Sheet — Stories tab card layout

Parent row + N indented child rows. Decision cell only on parents. Bold/indent are best-effort gspread formatting; data remains readable if formatting fails.

```
TYPE        DATE RANGE       SUMMARY                                # EVENTS  DECISION
trip        Mar 12–17 2022   Vermont ski trip with Sarah & Tom      5
  └ event   Mar 12           JFK→BTV flight
  └ event   Mar 13           Skiing at Killington
  └ event   Mar 14           Skiing at Killington — pow day
  └ event   Mar 16           Inn at Long Trail dinner
  └ event   Mar 17           BTV→JFK flight
interview…  Aug 3–28 2023    Acme PM interview cycle (4 rounds)     4
  └ event   Aug 3            Acme phone screen — Jane
  ...
```

Decision values: `yes` / `skip` / blank. Anything else is ignored on the parent row.

### Telegram — narrative confirmation

```
Bot:  📖 Story 1 of 8 — TRIP

      Vermont ski trip with Sarah & Tom
      Mar 12–17, 2022 · Killington, VT · 5 events

      Highlights:
      ✈ JFK→BTV flight (Mar 12)
      ✈ BTV→JFK flight (Mar 17)
      ⛷ Two days skiing Killington
      🍽 Dinner at Inn at Long Trail

      Events (drop by number):
        #1  Mar 12  JFK→BTV flight
        #2  Mar 13  Skiing at Killington
        #3  Mar 14  Skiing at Killington — pow day
        #4  Mar 16  Inn at Long Trail dinner
        #5  Mar 17  BTV→JFK flight

      Reply: yes / edit summary: <text> / drop #N / skip

User: yes

Bot:  Why did this matter? (one sentence)

User: First trip after my knee surgery — felt like a comeback weekend.

Bot:  📌 Want to add more? Trip details I can capture:
       • mode of travel
       • who came (beyond what's in the calendar)
       • most memorable moment
      Reply yes to answer them, or skip to move on.

User: skip                ← (or "yes" → 1–3 short follow-up questions)

Bot:  ✅ Logged. Next up:

      📖 Story 2 of 8 — INTERVIEW CYCLE …
```

### `drop #N` flow

`drop #N` removes child #N from the cluster (sets its `parent_id` back to NULL → returns to the unclustered pool, picked up next time `/buildstories` runs), updates the story's highlights, then re-presents the story for confirmation.

```
Bot:  ✓ Dropped event #3 (Mar 14 — Skiing). Story is now 4 events:
      [updated highlights]
      Reply: yes / edit summary / drop #N / skip
```

### `edit summary: <text>`

Rewrites the parent's `description` field. Highlights stay as-is. Re-presents for final confirmation.

## Error Handling & Edge Cases

| Failure | Handling |
|---|---|
| Clustering AI call fails (network, rate-limit, malformed JSON) | Log full traceback. Cluster falls back to N singleton stories so nothing is lost. Telegram reports "X clusters classified, Y degraded to singletons due to AI errors." |
| Sheet write fails | Surface exception class + message in Telegram, point at `/pushstories` (sibling of today's `/pushproposals`). DB is source of truth; sheet is reproducible. |
| User abandons mid-Telegram flow | `temp_data.pending_story_ids` persists. Next message asks "Resume reviewing N remaining stories? yes / clear." |
| `drop #N` with N out of range | Polite Telegram error: "Story only has 4 events; valid drop targets are #1–4." No state change. |
| All events in a story dropped via `drop #N` | Parent auto-dismisses. Remaining events in unclustered pool. |
| Same calendar event clustered into two stories on consecutive `/buildstories` runs | `parent_id` is overwritten on each pass for events with `status='proposed'`. Confirmed/dismissed events are skipped. |
| AI invents a highlight that doesn't match any underlying event (the ID 39 problem) | `cluster_into_story` returns `event_id_refs` per highlight. Clustering layer drops orphan highlights and logs a warning. |
| Postgres `date`/`datetime` in story rows | Already covered by `_normalize_row_dates` (added 2026-05-04). |
| Telegram message exceeds 4096 chars | Truncate highlights to top 8 + "…and N more"; full list still in DB and visible via `/showstory <id>`. |

## Testing Strategy

Use TDD. Pytest matches the existing stack.

| Layer | What to test | How |
|---|---|---|
| `services/story_clustering` | Date-proximity pre-clustering (date diff of 0 or 1 day → same group; date diff ≥ 2 days → split); flight detection from event titles; orphan-highlight rejection | Pure unit tests with hand-built event fixtures — no AI, no DB |
| `ai_life_log.cluster_into_story` | JSON shape contract; degrades to singleton stories on parse failure | Mock the Anthropic client; assert on call args + handling of malformed responses |
| Database story helpers | `parent_id` set correctly; `confirm_story` flips parent + children atomically; `drop_event_from_story` nulls the child's `parent_id`; child status follows parent on confirm/dismiss | Integration tests against **both SQLite and Postgres** via a `pytest` fixture (testcontainers or local Postgres URL). Postgres-only run skips with clear message if Docker absent. |
| `google_sheets` story helpers | Parent + child row order preserved; Decision parsing accepts `yes`/`skip`/blank; child rows ignored on read | Mock `gspread`; assert on row arrays passed in |
| Telegram state machine | State transitions; `drop #N` validation (in-range, out-of-range); resume-after-abandonment; queue exit on `clear` | Feed `Update` objects through handlers; assert on state and replies |
| End-to-end happy path | `/buildstories` over a fixture of ~20 pending events → N stories → sheet decisions applied → Telegram queue drains → all parents+children land as `confirmed` | One integration test, run **once per engine in the CI matrix** (SQLite + Postgres) |
| Migration | Adding the four columns is idempotent; existing rows unaffected; `parent_id` allows NULL | Migration test runs twice on each engine |
| **Clustering quality regression** | ~15 hand-crafted event clusters with expected `story_type` + `event_id_refs` form a golden-fixtures suite. Catches drift if the prompt changes. | Pytest, mocked AI returning the latest prompt-derived classifications; fixtures committed under `tests/fixtures/clustering/`. |

### Manual eval runbook (post-deploy)

After the first `/buildstories` over the real 400+ pending data:

1. Random-sample 20 stories from the Stories tab.
2. For each: rate `story_type` correctness (right / wrong / borderline), summary accuracy (accurate / partly / wrong), and highlight grounding (all real / 1 hallucinated / multiple hallucinated).
3. If ≥ 3/20 stories have hallucinated highlights or ≥ 4/20 are mistyped, the prompt needs a revision before further clustering passes.
4. Record results in `docs/superpowers/eval/clustering-eval-YYYY-MM-DD.md`.

### Out of scope for V1 tests

Performance / load testing — single-user bot, current scale is hundreds, max foreseeable a few thousand pending. Clustering is bounded by AI call count, which is rate-limited by Anthropic anyway. Acceptable to take a few minutes; `/pushstories` retry exists if it stalls.

## Open Questions

None remaining for V1. V2 backlog:
- Automatic background batch clustering trigger.
- Back-fill clustering over already-confirmed entries.
- Split / merge / move-event-between-stories controls in Telegram.
- `/log` extension for manually creating multi-event stories.
- Birthdays: dedicated tab + per-person opt-in (referenced in `memory/project_birthdays_tab.md`).
