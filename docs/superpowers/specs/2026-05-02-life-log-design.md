# Life Log — Design

**Status:** Approved (brainstorming complete) — ready for implementation planning
**Date:** 2026-05-02
**Owner:** Tom Keefe

---

## 1. Vision

Build a 30-year **memoir substrate** — a low-friction system that captures the meaningful events of a life so that decades from now the user can ask: *"what did I do the weekend of my 35th birthday?"*, *"when did I last see Sprink?"*, *"how many trips did I take in 2025?"*

The data model is also shaped to support a future **insights layer** — pattern detection ("you've ordered Uber 14 times this week") and self-improvement nudges. That layer is **out of scope for this spec** but informs storage choices: keep a parallel raw `activity_log` so that future analysis has full data even when the curated `life_log` does not.

This effectively repositions the existing bot. Today it captures daily work/personal accomplishments. The user already maintains a separate spreadsheet of memorable life events — voting with their feet that the daily-capture model does not serve memoir goals. We deprecate most of the existing capture flows and rebuild around the Life Log.

## 2. Goals & Non-Goals

### Goals
- Frictionless capture of meaningful life events from calendar (passive) and ad-hoc text/voice (active)
- People as first-class entities — the user can ask "when did I last see X" and get an instant answer
- Rich treatment for relationships specifically (status arcs: dating prospect → dating → broke up)
- Multi-source ingestion: calendar now, Gmail later, photos/location later
- 30-year durability via Google Sheets as the canonical export
- Telegram natural-language queries as the primary retrieval interface

### Non-Goals (explicit YAGNI)
- Multi-user — single-user app forever
- Web view / map view — planned for a future phase, not MVP
- Gmail integration — planned, not now
- Photos, voice memos for `/log` — text first; voice later
- Insights / pattern detection — future payoff; data model supports it but no UI
- Replacing the habits feature — habits stay untouched

## 3. Three Streams (architectural separation)

The system maintains three logically separate data streams that must not be conflated:

| Stream | Purpose | Cadence | Source |
|---|---|---|---|
| **Life Log** | Curated memoir-worthy events. The thing the user reviews in 30 years. | Sparse (~30–50/year) | AI-proposed from calendar + manual `/log` |
| **Activity Log** | Raw ingested events from any source — every calendar event, every Gmail signal. Untouched, kept for future insights. | Continuous | Calendar, Gmail (later) |
| **Habits** | Behavioral accountability — daily check-ins, streaks, miss reasons. | Daily | User check-in via existing flows |

Keeping them separate preserves debug-ability (raw data is always inspectable) and unblocks the future insights layer without polluting the memoir.

## 4. Data Model

### `life_log_entries`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `date_start` | DATE NOT NULL | The event start (or single date) |
| `date_end` | DATE NULL | For multi-day events (trips, weddings) |
| `categories` | TEXT[] (PG) / JSON (SQLite) | 1–3 from the fixed list. Common pairings: Wedding+Vacation, Bachelor Party+Vacation, Visitors+Outdoors |
| `description` | TEXT NOT NULL | The user's words. Short, e.g. "Spinkel Wedding - London 1 week, Spain 2 weeks" |
| `location` | TEXT NULL | Free-text (e.g. "London", "Mount Quandary, CO") |
| `notes` | TEXT NULL | Optional richer detail |
| `status` | TEXT | `confirmed` / `upcoming` / `proposed` (awaiting user confirm) / `dismissed` |
| `source` | TEXT | `calendar` / `manual` / `import_spreadsheet` / `import_calendar_history` |
| `source_id` | TEXT NULL | Calendar event ID, etc. — for dedup |
| `ai_proposed_at` | TIMESTAMP NULL | |
| `user_confirmed_at` | TIMESTAMP NULL | |
| `created_at` | TIMESTAMP | |

### `people`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `name` | TEXT NOT NULL | Canonical name (e.g. "Spinkel") |
| `aliases` | TEXT[] / JSON | Other names this person goes by ("Sprink") |
| `relationship_type` | TEXT | `family` / `friend` / `dating_prospect` / `dating` / `married` / `acquaintance` / `colleague` / `other` |
| `status` | TEXT | `active` / `ended` / `lost_touch` |
| `first_seen` | DATE | First entry mentioning them |
| `last_seen` | DATE | Most recent entry |
| `start_date` | DATE NULL | For romantic relationships — when it started |
| `end_date` | DATE NULL | For romantic relationships — when it ended |
| `notes` | TEXT NULL | "Met at Hinge", "old college friend", etc. |
| `created_at` | TIMESTAMP | |

### `life_log_people` (join table)
| Column | Type |
|---|---|
| `entry_id` | INTEGER FK life_log_entries(id) |
| `person_id` | INTEGER FK people(id) |
| UNIQUE(entry_id, person_id) | |

### `activity_log`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `source` | TEXT | `calendar` / `gmail_uber` / `gmail_doordash` / etc. |
| `source_id` | TEXT | Dedup key |
| `event_type` | TEXT | `calendar_event` / `email_receipt` / etc. |
| `occurred_at` | TIMESTAMP | |
| `payload` | JSONB / JSON | Full structured payload from source |
| `ingested_at` | TIMESTAMP | |
| `promoted_to_life_log` | BOOLEAN | True if this generated a life_log_entries row |

### `categories` (config table)
| Column | Type | Notes |
|---|---|---|
| `name` | TEXT PK | Vacation, Relationship, ... |
| `active` | BOOLEAN | Allows soft-delete |
| `usage_count` | INTEGER | For the AI's monthly recommendation: "you've never used Pet — drop?" |
| `created_at` | TIMESTAMP | |

**Initial categories:** Vacation, Relationship, Outdoors, Skiing, Concert, Wedding, Bachelor Party, Life Event, Visitors, Tattoo, Move/Housing, Job/Career, Health, Achievement, Pet, Loss

## 5. Capture Flows

### 5.1 Continuous calendar ingestion (passive, hybrid timing)

A background job watches the calendar continuously and routes events by **confidence**:

- **High-confidence → real-time propose on calendar add.** Triggers: multi-day events, named locations far from home, strong category keywords (wedding / ski / concert / trip). Bot pings within minutes of the event being added to the calendar.
- **Category-matched single events → day-after propose.** Triggers: event title or description matches a category keyword but lower confidence (e.g. "Megan dinner"). Bot pings the morning after the event ended, so we know it actually happened.
- **Maybes → Sunday digest.** Anything else that the AI thinks *might* be Life Log–worthy but is unsure. Batched into the Sunday weekly digest (which replaces the current weekly accomplishments summary).

**Every** ingested calendar event lands in `activity_log` regardless of category — this is the raw source-of-truth mirror. Events that get promoted to Life Log additionally create a `life_log_entries` row, and the `activity_log` row's `promoted_to_life_log` flag is set true. Events that are dismissed (skipped or never proposed) stay in `activity_log` only. This separation means the future insights layer always has full data even if the user dismisses something.

**Proposal Telegram message format:**

```
📅 Spinkel Wedding
May 5–12 · London → Spain
Suggested: Wedding + Vacation
With: Sprink, Emily (from attendees)

[Confirm] [Edit] [Skip]
```

On Confirm → entry saved with status=confirmed.
On Skip → activity_log row marked dismissed; never re-proposed.
On Edit → guided correction flow (reuse existing freeform-correction pattern).

### 5.2 `/log` command (ad-hoc capture)

Plain text or voice transcript. AI parses with the same prompt scaffold as the existing `parse_freeform_message`, but tuned for Life Log entries (categories, people, location, dates).

```
/log Met Megan at Goldens in Golden tonight
```

Bot responds:
```
Got it. Here's what I understood:

🟢 Met Megan at Goldens in Golden
Today · Golden, CO
Suggested: Relationship
👤 Megan (NEW — first mention)

[Confirm] [Edit category/people/date] [Skip]
```

If Megan is new → after confirm, bot asks one follow-up:
```
First time logging Megan. What's the relationship?
[Dating prospect] [Friend] [Acquaintance] [Other]
```
Saves to `people` table with appropriate `relationship_type`.

### 5.3 Relationship arc tracking (richer)

For Relationship-category entries, bot tracks the arc explicitly:
- First entry mentioning a person with category=Relationship → bot asks status
- Subsequent entries can update status: `/log broke up with Megan` → bot detects, asks "End relationship with Megan? (sets end_date today)"
- People with `relationship_type` ∈ {dating_prospect, dating, married} get their own arc view: timeline of all entries, start/end dates, duration

### 5.4 Categories evolution

Monthly background job: AI reviews `categories.usage_count` over the past N months and recommends:
- **Merge:** "You've created 3 entries tagged Outdoors and 5 tagged Hiking — merge?"
- **Drop:** "You've never tagged Pet — remove from active list?"
- **Add:** "You've created 6 'Trip with Friends' entries in Notes — add as a category?"

User confirms via Telegram. No auto-changes.

## 6. Output / Retrieval

### 6.1 Canonical store
- **Postgres** = working store (all queries hit this)
- **Google Sheets** = durable canonical export (30-year archive)
- Sheets tabs: **Life Log**, **People**, **Habits** (existing)
- Old tabs (Weekly Reviews, Later) are dropped

### 6.2 Sheets schema

**Life Log tab columns:**
`Date | End Date | Categories | Description | People | Location | Notes | Status | Source | ID`

(Categories as comma-separated string for human readability. People as comma-separated. ID column matches Postgres for read-back-edit support like the current Weekly Reviews tab.)

**People tab columns:**
`Name | Aliases | Type | Status | First Seen | Last Seen | Start Date | End Date | Notes | ID`

### 6.3 Telegram queries

Natural-language query layer powered by an LLM with structured output. Example queries the system must support:
- "When did I last see Sprink?" → "Nov 2025 in London (Spinkel Wedding) — 6 months ago"
- "Show 2025 trips" → list of Vacation/Bachelor Party entries from 2025
- "Who haven't I seen in 6+ months?" → list of people sorted by last_seen
- "Timeline with Megan" → chronological list of all entries linked to Megan
- "How many concerts in 2025?" → count
- "What was I doing in March 2024?" → entries from that month

Implementation: tool-using LLM call. Tools = read-only DB queries (`get_entries`, `get_people`, `count_entries_by_category`, etc.). LLM picks the right tool, formats the answer.

## 7. What's deprecated / replaced

| Old | New |
|---|---|
| `/update` `/work` `/personal` `/focus` commands | Removed |
| `accomplishments` table | Kept as-is (read-only, archive) — no new writes |
| `weekly_focus` table | Kept as-is (read-only, archive) — no new writes |
| `/later` long-term goals concept | Removed. Future Life Log entries (status=upcoming) replace its main use. Goals concept dies. |
| `later_items` table | Kept (read-only, archive) — no new writes |
| Calendar → `/later` flow | Calendar → Life Log proposal queue |
| `_handle_freeform_message` (current AI parsing) | Repurposed to drive `/log` parsing for Life Log |
| Weekly Sunday summary job | Repurposed as Sunday Life Log digest (the "maybes") |
| Sheets: Weekly Reviews tab | Stop writing. Tab kept for archive. |
| Sheets: Later tab | Stop writing. Tab kept for archive. |
| `/habit` family + Habits tab + monthly recurrence | **Untouched** |
| Calendar OAuth / `services/calendar_service.py` | **Reused** for Life Log ingestion |

## 8. Backfill

### 8.1 One-time spreadsheet import
A standalone script (`scripts/import_life_log_spreadsheet.py`) reads the user's existing Google Sheet (separate spreadsheet ID — to be supplied) with columns Year / Month / Category / Description, and:
1. Creates `life_log_entries` rows with `source='import_spreadsheet'`, `status='confirmed'`
2. Date = first of the month (since spreadsheet only has month granularity)
3. AI extracts people from description text → creates `people` rows (status='active', relationship_type=null pending review)
4. AI extracts location from description text
5. After import, sends Telegram message: "Imported N entries, M new people. Review people list to merge duplicates and set relationship types."

### 8.2 Calendar history scan
Standalone script (`scripts/import_calendar_history.py`):
1. Pulls all calendar events as far back as the API allows (Google Calendar typically supports unbounded history)
2. Groups by year
3. For each year: AI batch-classifies events into "candidate Life Log" vs "noise"
4. For each candidate, sends a Telegram digest message per month: "Here are 8 candidate Life Log entries from March 2024 — confirm/skip each"
5. Confirmed entries get `source='import_calendar_history'`, `status='confirmed'`

The import is paced (not all at once) — user can pause anytime.

## 9. Migration phases

### Phase 1: Build new infrastructure (no user-visible change)
- New tables: `life_log_entries`, `people`, `life_log_people`, `activity_log`, `categories`
- New AI module: `ai_life_log.py` with `propose_from_calendar_event`, `parse_log_command`, `extract_people`, `extract_location`
- New Sheets tabs created (empty): Life Log, People
- Backfill scripts written and tested locally

### Phase 2: Backfill (user-visible only as one-time confirmations)
- Run spreadsheet import → user reviews extracted people in Telegram
- Run calendar history scan → user reviews proposals in monthly batches

### Phase 3: Switch active capture
- Calendar daily-sync job changes target: writes to Life Log proposal queue (not `/later`)
- New `/log` command added; old `/update` `/work` `/personal` `/focus` `/later` removed
- Sunday weekly summary repurposed as Life Log digest
- New Telegram query handler added for natural-language questions
- Old Sheets tabs marked archive (no new writes); new tabs become primary
- `_COMMANDS_TEXT` updated; user notified of the new command set

### Phase 4 (later, separate spec)
- Gmail integration → richer Activity Log
- Web view / timeline UI
- Insights layer ("make me better at life")

## 10. Open questions / deferred decisions

These are intentionally unresolved — to be decided during implementation, not now:

1. **Calendar ingestion confidence heuristics** — exact keyword/regex/duration thresholds for high-confidence vs maybe vs noise. To be tuned empirically against real calendar data.
2. **People disambiguation edge cases** — "Mom" is unambiguous (single user), but "Sarah" (multiple Sarahs over a lifetime) needs UX. Probably: bot asks "Existing Sarah (last seen Jul 2024)? Or new?"
3. **Voice transcription** — `/log` accepts text MVP. Voice via Telegram voice-note transcription is a quick follow-on.
4. **Sheets read-back edits** — should edits in the Life Log Sheet flow back to Postgres (like current Weekly Reviews tab)? Recommend yes for parity, but not critical for MVP.
5. **Calendar history scan pacing** — one-shot dump vs daily batches. Probably daily batches to avoid overwhelming.
6. **Activity Log retention** — keep forever? Postgres will grow. Probably fine for years; revisit at 100k+ rows.

## 11. Risks

- **AI proposal noise drives user to ignore Telegram.** Mitigation: aggressive confidence-tier routing — high-confidence-only by default, maybes only on Sunday. Tune over the first 2 weeks.
- **People extraction errors create duplicate `people` rows.** Mitigation: `/people` command with merge/rename, run a periodic dedup pass.
- **Calendar history scan generates hundreds of proposals all at once.** Mitigation: paced batching (one month per day), explicit pause command.
- **Sheet structure change breaks 30-year durability promise.** Mitigation: never delete columns from the Life Log tab; only add. Old tabs archived not dropped.
- **Single-user assumption baked into new code.** Acceptable — the existing system already has this; no plan to multi-tenant.

## 12. Success criteria for MVP

After Phase 3 ships, the system is successful if:
1. The user logs at least one entry via `/log` per week without friction
2. The user accepts at least one calendar-proposed entry per week
3. The user can answer "when did I last see X" via Telegram in <5 seconds
4. The Sheets Life Log tab is populated with both backfilled history and net-new entries
5. The user has stopped maintaining their parallel spreadsheet
