# Date Tracking + Places

**Date:** 2026-07-30
**Status:** Approved in conversation

The user wants to track romantic dates — "how much I spend on dates, how
frequent I go on them, where do I go" — as a mirror concept to Social
tagging. Alongside it, a general `location` field on calendar events,
because "where do I go" is a property of any outing (sporting events,
social events, dates), and Google Calendar already sends a location we
currently drop at the `calendar_service.py` boundary.

## Decisions

1. **Dates are an UNSCORED series, like rides.** No `METRICS` entry, no
   target, no hit/miss, no ledger row. Counts, trends, spend, and venues
   only. Consequence (by construction, same as rides and bank): dates never
   reach the Telegram weekly push or the AI reflection — both build from
   `METRICS`-derived cards, and dates are not in `METRICS`. This holds only
   as long as dates stay out of `METRICS`.
2. **A date is a `calendar_events` row, not a new table.** "Fully separate"
   is delivered at the analytics level (own series, own spend kind, own
   panel, excluded from social) — but the storage anatomy of a date is
   identical to any outing (title, day, amount, place), so it reuses the
   calendar + manual-event machinery. Mirror columns per the Override +
   Learning pattern: `is_date` (derived, scan-owned) and `user_is_date`
   (user-owned, scan never touches), resolved in SQL as
   `COALESCE(user_is_date, is_date)`.
3. **Detection is a RULE, not AI — user's explicit choice** ("only look for
   things that say Date, besides that it's just manual"). A pure
   word-boundary match: the title contains the word "date",
   case-insensitive (`\bdate\b` — "Update sync", "Candidate interview",
   "Mandate review" never match), sets `is_date` at scan time. No Claude
   call, no confidence, no uncertain chip. Everything else is manual: a
   "Date" checkbox on the add/edit event form sets `user_is_date`; there is
   no example-feed loop because there is no classifier to teach.
4. **Dates are EXCLUDED from the social metric — user's explicit choice**
   (reversed from an earlier "also counts"; the final answer is excluded).
   Every social count/spend query gains `AND NOT resolved_date`. The social
   floor now means non-date social. Date columns never touch
   `user_is_social` and never feed the social classifier's examples.
5. **Location: gcal-owned `location` + user-owned `user_location`.**
   `location` TEXT is written by the calendar scan's upsert (same footing
   as `title`/`start_at` — a re-upsert overwrites it); `user_location` TEXT
   is the user's correction, scan never touches it; resolved as
   `COALESCE(NULLIF(user_location, ''), location)`. Manual events get a
   "Where" free-text input. The field exists on every event, not just
   dates — future "top places" views can slice any tag.
6. **Surfaces:**
   - **Day log:** a date renders as its event row with a "date" chip
     replacing the "social" chip — no new day-log category (the six-category
     cap from the 2026-07-30 day-log-redesign spec is a rule).
   - **Insights:** a Dates panel — weekly count trend, total and average
     spend, top places (grouped by resolved location, count + spend each).
   - **Spend views:** dates appear as their own kind (`date`, service label
     "Dates"); social spend excludes them. Every amount counts exactly once.
   - Add/edit event form: "Date" checkbox + "Where" input.
7. **API:** extend the existing event routes — `POST /social` accepts
   optional `location` and `is_date`; `PATCH /social/{id}` accepts optional
   `location` (→ `user_location`) and `is_date` (→ `user_is_date`),
   following the existing model_fields_set omitted-vs-null convention. A
   dates series endpoint feeds the Insights panel (shape decided in the
   plan: either a `dates` block on the existing insights payload or
   `GET /dates`).

## Rejected alternatives

- **Scored metric with a weekly target.** User chose unscored — dating
  frequency isn't a quota.
- **AI classification of dates (mirror of `classify_social_event`).**
  Title-based romance detection is unreliable and needs the whole
  uncertain-chip machinery; the user scoped detection to the literal word
  "date" — a regex, not a model.
- **A separate `dates` table.** Duplicates manual-event creation, amounts,
  overrides, and day-log plumbing; every calendar-detected date would need
  a mirrored row. Same-table flags give identical analytics separation.
- **Dates also counting toward social.** Explicitly reversed by the user.
- **A dedicated Dates screen in the nav.** Insights section chosen; nav
  width and the day-log category cap are already tight.
- **Detecting attended sporting events from tickets/emails.** Out of scope —
  a separate ingestion project. Calendar events (which carry location) are
  covered by this design.

## Invariants untouched

- Social classifier example feed: recurring-series-only rule unchanged;
  `user_is_date` never feeds it.
- Privacy boundaries: `format_scorecard_text` and `/api/reflection` remain
  bank-free AND date-free by construction (`METRICS`-derived only).
- `user_removed` ("didn't happen") applies to date events exactly as to
  social ones — a removed date drops out of counts with the same Undo row.

## Implementation

- `metrics.py`: `title_is_date(title)` pure rule (word-boundary,
  case-insensitive).
- `database.py`: migrations for `is_date`, `user_is_date` (nullable bool)
  and `location`, `user_location` (nullable TEXT) on `calendar_events`;
  upsert writes `location` and `is_date` (derived from `title_is_date` at
  scan time), never the user columns; social range queries exclude resolved
  dates; new `get_date_events_range`; resolution exposed as `is_date_resolved`
  (or folded into existing resolved fields — plan decides naming).
- `jobs/scan_calendar.py`: passes location through; sets `is_date` from the
  rule. No AI change.
- `app/scorecard.py` / routes: social counts/spend exclude dates; spend
  views gain the `date` kind; day/today payload carries `is_date`,
  `location` resolved fields; insights payload gains the dates panel data
  (weekly counts, spend, top places).
- Frontend: date chip on day-log event rows; "Date" checkbox + "Where"
  input on add/edit forms (patch-builder helpers in `lib.ts`); Dates panel
  on Insights.

## Testing

pytest: `title_is_date` (matches "Date night", "date w/ Alex", "DATE";
rejects "Update sync", "Candidate interview", "Mandate review");
`COALESCE` resolution precedence for both flag and location; scan sets
`is_date`/`location` but never user columns on re-upsert; social count
excludes a resolved date (flips a week's social count); dates range query
returns calendar + manual dates with amounts and resolved locations; spend
separation (no double counting); Telegram/reflection payloads contain no
date rows (regression lock on the by-construction rule). Frontend: patch
builders in `lib.test.ts`; `tsc --noEmit` + `vite build`; manual look.
