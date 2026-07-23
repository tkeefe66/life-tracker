# Rides Tracker (Uber / Lyft)

**Date:** 2026-07-22
**Status:** Approved

## Problem

The user wants to see how much they use and spend on ride-hailing. Ride
receipts already flow through the Gmail scan but are explicitly discarded as
"not orders." Work travel is a large share of rides and must be excludable,
since the app tracks personal behavior only.

## Decisions

- Rides are **tracking-only**: no weekly target, no hit/miss scoring, and
  **not** a member of `METRICS`. They are a tracked series surfaced in spend
  views.
- Work exclusion uses a **per-ride toggle** plus an **AI pre-flag**. Work-trip
  date ranges were considered and rejected.
- AI-flagged work rides are **flagged but still counted** until the user
  confirms — nothing is ever silently excluded.
- Lookback reuses `GMAIL_SCAN_LOOKBACK_DAYS` (currently 30).

## Design

### 1. Schema — new `rides` table

| Column | Type | Meaning |
|---|---|---|
| `id` | serial PK | |
| `gmail_message_id` | TEXT UNIQUE | dedupe key |
| `service` | TEXT | `Uber` or `Lyft` |
| `ride_at` | TEXT | local-tz ISO datetime (email time) |
| `ride_key` | TEXT | dedupe cluster key (see Ingestion), indexed |
| `subject` | TEXT | email subject |
| `amount` | REAL | total, nullable |
| `ai_is_work` | BOOLEAN | AI guess, nullable |
| `ai_confidence` | REAL | nullable |
| `user_is_work` | BOOLEAN | user override, nullable |
| `detected_at` | TIMESTAMP | default now |

Created via `CREATE TABLE IF NOT EXISTS` in `_init_v2_tables()` (new table, so
no column migration needed).

**Resolution:** a ride is work when `COALESCE(user_is_work, false)` — i.e.
only a *confirmed* user verdict excludes it. `ai_is_work` is advisory only and
never excludes on its own, per the "flag but still count" decision.
"Personal" totals = rides where the resolved work flag is false.

### 2. Ingestion — restructured `jobs/scan_gmail.py`

Today the scan fetches candidates from delivery senders and routes each to
order / not-order. It becomes a three-way route over one fetch:

- `receipts.DELIVERY_DOMAINS` is unchanged. New `receipts.RIDE_DOMAINS` maps
  `uber.com → Uber`, `lyft.com → Lyft`. The Gmail query's sender list is the
  union of both domain sets (adds `lyft.com`).
- New `receipts.classify_ride(sender, subject) -> tuple[str, str]` returning
  `("ride"|"not_ride"|"ambiguous", service)`. A subject matching the existing
  `_RIDE_RE` (`trip`, `ride`, `driver`) from a ride domain is a `ride`;
  promos are `not_ride`; otherwise `ambiguous`.
- Per candidate, in order: skip if `db.has_delivery_order(...)` **or**
  `db.has_ride(...)`; skip follow-ups (`is_followup`, unchanged — Uber's ride
  emails include "charge summary" duplicates, see below); try the delivery
  path first (unchanged behavior, including the tip-only recovery); if the
  candidate is not a delivery order, try the ride path.
- **Ride duplicate guard:** Uber sends both a "charge summary" and a "Thanks
  for riding" receipt for one trip. Subject is NOT a safe cluster key here —
  two separate trips the same morning both read "Your Sunday morning trip
  with Uber". Instead cluster on the **ride timestamp printed in the
  snippet**: ride receipts open with `"Jul 19, 2026 4:03 AM"`, which is
  unique per trip and identical across that trip's duplicate emails. New
  `receipts.extract_ride_time(snippet)` parses it to an ISO-ish key string
  (`"2026-07-19T04:03"`), returning None if absent. Cluster key =
  `(service, ride_time)`; when `ride_time` is None fall back to
  `(service, day, subject, amount)`. If a ride already exists for the key,
  update its `amount` (later email wins) instead of inserting.
- Ride amounts use the existing `receipts.extract_amount`.
- Each newly stored ride gets one `ai_metrics.classify_work_ride` call.
- Scan result string gains ride counts, e.g.
  `"... · 3 new rides"` appended to `gmail_last_result`.

### 3. AI — `ai_metrics.classify_work_ride`

`classify_work_ride(service, subject, snippet, examples=None) -> dict`
returning `{"is_work": bool, "confidence": float}`, following the existing
`classify_social_event` shape (`_call_json`, `MODEL` unchanged, guarded
float coercion, safe default `{"is_work": False, "confidence": 0.0}`).

Prompt guidance: work rides typically involve airports, hotels, conference
venues, out-of-town addresses, or weekday business hours in an unfamiliar
city; personal rides are local trips, nights out, and errands. The subject
and snippet are the only evidence available.

`examples` mirrors the social-override learning design: recent rides where
`user_is_work IS NOT NULL`, rendered as
`- "<subject>" IS work` / `IS NOT work`, omitted entirely when empty.

### 4. API (`app/routes.py`)

- `GET /api/rides?days=60` (clamped 1–365) → `{"rides": [...]}` newest-first.
  Each row: `id`, `service`, `ride_at`, `subject`, `amount`, `ai_is_work`,
  `user_is_work`, and resolved `is_work`.
- `PATCH /api/rides/{id}` — body `{is_work: bool}`; sets `user_is_work`;
  404 on unknown id.
- Weekly figures: `scorecard_for_week` adds `rides_count` and `rides_spend`
  (personal rides only — resolved work rides excluded from both).

### 5. Frontend (minimal in this project)

Rides appear in Today's "Noticed quietly" list for the viewed day (service,
amount, time), with a "work?" marker when `ai_is_work` is true and the user
has not decided. Tapping a ride toggles work/personal (PATCH), which also
teaches future classification. Rich spend presentation is deliberately
deferred to the Spend-surfaces project.

## Testing

- pytest: ride table roundtrip and dedupe; three-way scan routing (delivery
  order vs ride vs neither) with an existing delivery test staying green;
  ride cluster-dedupe (charge summary + receipt → one ride, amount updated);
  `classify_work_ride` prompt contents incl. examples block present/absent;
  `PATCH /api/rides/{id}` sets the override and 404s on unknown id;
  `rides_spend`/`rides_count` exclude confirmed-work rides but include
  AI-flagged-unconfirmed ones.
- Frontend: build + existing vitest.

## Out of Scope

- Work-trip date ranges, ride targets/scoring, the Spend tab and richer spend
  views (separate project), distance/duration parsing, non-Uber/Lyft services.

**Known limitation:** when a ride's snippet has no parseable timestamp, the
fallback cluster key is `(service, day, subject)` instead of the parsed ride
time. Two genuinely distinct trips on the same day with an identical subject
template and no parseable time will merge into one row. This is the
deliberate tradeoff that makes deduping multiple emails of a single trip
possible without a reliable per-trip timestamp — see
`test_ride_fallback_key_dedupes_without_amount_in_key` in `tests/test_scan_gmail.py`.
