# Rides Tracker — Implementation Report

Branch: `rides-tracker` (checked out from a commit identical to `main`)
Worktree: `/Users/tomkeefe/Code Apps/weekly-updates/.claude/worktrees/agent-a928ed302067827a1`

Spec: `docs/superpowers/specs/2026-07-22-rides-tracker-design.md`
Plan: `docs/superpowers/plans/2026-07-22-rides-tracker.md`

## Baseline

Before any changes: 121 backend tests (pytest), 20 frontend tests (vitest) — both green.
Set up a fresh venv in the worktree (`python3 -m venv venv && venv/bin/pip install -r
requirements.txt`) and ran `npm install` in `frontend/` (partially pre-populated from an
earlier tool call; completed cleanly). Confirmed baseline counts before touching code.

## Area 1 — Rules (`receipts.py`)

**Commit:** `095f0e3 feat(receipts): ride classification and ride-time parsing`

Added (verbatim per plan, no deviation):
- `RIDE_DOMAINS = {"uber.com": "Uber", "lyft.com": "Lyft"}`
- `classify_ride(sender, subject) -> ("ride"|"not_ride"|"ambiguous", service)`
- `extract_ride_time(snippet) -> "YYYY-MM-DDTHH:MM" | None` — parses the ride
  timestamp Uber/Lyft print at the top of the receipt snippet (e.g. `"Jul 19, 2026
  4:03 AM"`).
- `_ride_domain`, `_ORDER_WORDS_RE`, `_RIDE_TIME_RE`, `_MONTHS` helpers.
- `DELIVERY_DOMAINS` and all existing delivery functions left untouched.

**TDD evidence:** wrote `test_ride_domains_and_classify_ride` and
`test_extract_ride_time` in `tests/test_receipts.py` first; ran red (`ImportError:
cannot import name 'classify_ride'`), implemented, ran green (12/12 in the file, 123/121
overall).

Files: `receipts.py`, `tests/test_receipts.py`

## Area 2 — Schema + DB (`database.py`)

**Commit:** `13e86e1 feat(db): rides table and queries`

- New `rides` table in `_init_v2_tables()` (CREATE TABLE IF NOT EXISTS — no column
  migration needed, matches the "new table" pattern used for `weekly_reflections`).
  Added `ix_rides_ride_key` index.
- Functions, all following the `delivery_orders` placeholder/style conventions
  (`_p()`, `USE_POSTGRES`-agnostic SQL): `has_ride`, `add_ride` (truthy iff inserted,
  `ON CONFLICT(gmail_message_id) DO NOTHING`), `find_ride_by_key(service, ride_key)`,
  `set_ride_amount`, `set_ride_classification(ride_id, is_work, confidence)`,
  `set_ride_work_override(ride_id, is_work)` (returns `rowcount > 0` so the API can
  produce a real 404), `get_rides_range(start_day, end_day)`, `get_ride_examples(limit=10)`.
- Added `_ride_bool_rows()` helper (same pattern as the existing `_social_rows()` for
  `calendar_events`) so SQLite's 0/1 ints for `ai_is_work`/`user_is_work` come back as
  real `bool`/`None` — this surfaced during Area 4's API test (`is True` failed against
  a raw `1`), fixed here since it's DB-layer behavior; committed with Area 4 since that's
  where the test caught it.

**TDD evidence:** 10 new tests written first in `tests/test_database_v2.py` (add+fetch
by range, range excludes outside week, `has_ride` dedupe, `find_ride_by_key` hit/miss,
`set_ride_amount`, `set_ride_classification`, `set_ride_work_override` incl. "unknown id
→ False", `get_ride_examples` newest-first/capped/only-overridden, `initialize_db()`
called twice idempotent) — ran red (`AttributeError: module 'database' has no attribute
'add_ride'`), implemented, ran green (37/37 in the file, 133/133 overall).

Files: `database.py`, `tests/test_database_v2.py`

## Area 3 — AI + ingestion (`ai_metrics.py`, `jobs/scan_gmail.py`, `services/gmail_service.py`)

**Commit:** `8dedd65 feat(scan): ingest Uber/Lyft rides with work classification`

- `ai_metrics.classify_work_ride(service, subject, snippet="", examples=None) -> dict`
  — mirrors `classify_social_event`'s shape exactly: `_call_json`, `MODEL` unchanged,
  guarded float coercion, safe default `{"is_work": False, "confidence": 0.0}`, and the
  same examples-block-present/absent prompt pattern.
- `services/gmail_service.py`: `_SENDERS` now built from
  `sorted(set(DELIVERY_DOMAINS) | set(RIDE_DOMAINS))` — a stable, deterministic query
  string that adds `lyft.com` (and re-adds `uber.com`, already present) to the Gmail
  query's sender list. `_query()` shape otherwise unchanged.
- `jobs/scan_gmail.py`: restructured per the plan's shape. Per candidate, in order:
  1. Skip if `db.has_delivery_order(...)` **or** `db.has_ride(...)`.
  2. Try the ride path first (`receipts.classify_ride`) — **before** `is_followup`, so
     ride receipts are never accidentally swallowed by delivery follow-up rules.
     - Cluster key = `receipts.extract_ride_time(snippet)` if present, else
       `f"{day}|{subject}|{amount}"` fallback.
     - If `db.find_ride_by_key(service, key)` hits, update the amount (later email
       wins) and skip — this is the charge-summary/thanks-for-riding dedupe.
     - Otherwise insert via `db.add_ride`, then call `ai_metrics.classify_work_ride`
       once and store the verdict via `db.set_ride_classification`.
  3. If not a ride, fall through to the **unmodified** delivery block (follow-up /
     tip-only recovery / ambiguous-AI / order path — byte-identical logic to before).
  4. `gmail_last_result` gains `f"· {rides_added} new rides"`; log line gains the same
     figure.

**Critical correctness point (ride dedupe by time, not subject):** two dedicated tests
prove this:
- `test_ride_cluster_dedupe_by_ride_time_not_subject` — a "Thanks for riding" and a
  "charge summary" email, **same subject**, same parsed ride time (`Jul 19, 2026
  4:03 AM` in both snippets) → asserts exactly **one** row in `rides`, and that the
  amount was updated to the later email's total ($16.50, not $14.00).
- `test_two_distinct_rides_same_morning_identical_subject_two_rows` — two genuinely
  separate trips, **identical subject** ("Your Sunday morning trip with Uber"), but
  **different** parsed ride times (`7:02 AM` vs `9:17 AM`) → asserts exactly **two**
  rows, with both distinct amounts ($9.00 and $11.00) present. This is the test that
  would fail if dedupe ever regressed to keying on subject instead of ride time.

Also added: `test_ride_candidate_stored_as_ride_not_delivery_order` (ride never lands
in `delivery_orders`, and never goes through the delivery AI path — `ai_calls == []`),
`test_delivery_candidate_still_becomes_order_alongside_rides` (delivery behavior
unregressed with a ride candidate present in the same batch), `test_scan_writes_last_result_includes_ride_count`,
`test_ride_dedupe_skip_when_already_stored` (classification runs exactly once, on first
ingestion), `test_query_uses_union_of_delivery_and_ride_domains`.

**Existing-test impact:** `test_scan_stores_orders_and_uses_ai_for_ambiguous` and
`test_scan_skips_already_seen` both include a `"Your Tuesday trip with Uber"` candidate
that was previously silently discarded (`not_order`) and is now correctly routed to the
ride path. Their delivery-order assertions are untouched and still pass; I added
`monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride", ...)` to both so the
new ride-classification call doesn't hit the real Anthropic client during the test (same
mocking discipline the file already uses for `classify_receipt`).

**TDD evidence:** wrote all ai_metrics + scan_gmail ride tests first, confirmed red
(`AttributeError: no attribute 'classify_work_ride'`, then post-implementation of
ai_metrics but pre-scan_gmail: `assert 0 == 1` / `AssertionError: assert '1 new rides'
in ...`), implemented `ai_metrics.classify_work_ride` (16/16 green in
`test_ai_metrics.py`), then `gmail_service.py` + `scan_gmail.py` (20/20 green in
`test_scan_gmail.py`, 145/145 overall).

Files: `ai_metrics.py`, `jobs/scan_gmail.py`, `services/gmail_service.py`,
`tests/test_ai_metrics.py`, `tests/test_scan_gmail.py`

## Area 4 — API + frontend (`app/routes.py`, `app/scorecard.py`, `Today.tsx`)

**Commit:** `4d6421c feat(app): rides API, weekly ride spend, and Today ride toggles`
(amended once, after the initial `fdda632`, to fold in a coordinator-requested
delivery-amount rendering fix — see addendum below)

- `GET /api/rides?days=60` (clamped 1–365) → `{"rides": [...]}` newest-first, each row
  spreads the DB row plus resolved `is_work: bool(user_is_work)` (i.e.
  `COALESCE(user_is_work, false)` — an AI flag alone never resolves to work).
- `PATCH /api/rides/{id}` — `{is_work: bool}` → sets `user_is_work` via
  `db.set_ride_work_override`; real 404 on unknown id (backed by the DB layer's
  `rowcount > 0` return).
- `app/scorecard.py`: `scorecard_for_week` gains `rides_count` and `rides_spend` via a
  new `_personal_rides()` helper — filters to rides where `user_is_work` is not
  confirmed `True` (AI-flagged-but-unconfirmed rides still count and still spend);
  spend sums `amount or 0`, rounded to 2dp. `today_snapshot` gains
  `"rides": [...]` (same day-scoped `get_rides_range` + resolved `is_work` shape as the
  list endpoint) for `Today.tsx` to render.
- **Confirmed `rides` never touches `METRICS`, `targets`, or the hit/miss ledger** —
  `test_rides_not_in_today_snapshot_metrics_ledger` asserts `"rides" not in
  db.get_targets()`, and the scorecard test asserts `"rides" not in card["metrics"]`.
- `Today.tsx`: added `Ride` interface and `rides: Ride[]` to `TodayData`. Rides render
  in "Noticed quietly" as `"{service} ride"` with amount and time, reusing the existing
  `.quiet`/`.quiet-btn` styling (no new CSS, per plan). A `" · work?"` marker appears
  when `ai_is_work === true && user_is_work === null` (AI flagged, user hasn't decided).
  Tapping a ride calls `toggleRideWork` → `PATCH /rides/{id}` with `is_work: !r.is_work`
  (toggles the *resolved* value) and refreshes.

**TDD evidence:** 5 new tests in `tests/test_api_routes.py` (shape/order/days-clamp,
resolved `is_work` reacts to AI-flag-then-user-override, PATCH sets override, PATCH
404s on unknown id, scorecard rides_count/rides_spend excludes confirmed-work but
includes AI-flagged-unconfirmed) + 3 new tests in `tests/test_scorecard.py`
(scorecard-level rides_count/spend, rides absent from METRICS/targets,
`today_snapshot` includes the day's rides) — all written first, confirmed red
(`KeyError: 'rides'`, `404 == 200`), implemented, confirmed green (153/153 overall).
One red→green cycle mid-area: the first pass of `get_rides_range` returned raw
SQLite 0/1 ints for `ai_is_work`, failing `assert body["rides"][0]["ai_is_work"] is
True`; fixed with the `_ride_bool_rows()` helper in `database.py` (see Area 2 note),
staged and committed together with this area since that's where the test caught it.

Files: `app/routes.py`, `app/scorecard.py`, `frontend/src/screens/Today.tsx`,
`tests/test_api_routes.py`, `tests/test_scorecard.py` (+ `database.py` bool-cast fix)

**Addendum (coordinator-requested, folded into this same commit via amend):** Today's
"Noticed quietly" delivery rows rendered service + time only, with no dollar figure,
even though `amount` was already flowing through the backend unchanged
(`db.get_delivery_orders_range` selects `amount`, `today_snapshot` passes `deliveries`
through untouched — verified before touching anything, no backend change was needed).
Fixed in `Today.tsx`: `TodayData`'s `deliveries` item type gained `amount: number |
null`; the delivery row now shows `d.amount != null && \`$${d.amount.toFixed(2).replace(/\.00$/, "")} · \`}`
before the time — a null check (not truthiness), so a genuine `$0` would still render,
matching the exact convention used by `Settings.tsx`'s detected-orders list and
`Scorecard.tsx`'s spend lines. Also switched the ride row (added earlier in this same
area) from its ad hoc `!== null ? ... : ""` ternary to the identical `!= null &&`
convention so the whole list reads uniformly. Reverified: 20/20 frontend tests, clean
`tsc --noEmit` + `vite build`, 153/153 backend tests (unaffected, as expected for a
frontend-only change).

## Final Verification

```
$ venv/bin/pytest tests/ -q
======================= 153 passed, 2 warnings in 2.1s ========================
```
(the 2 warnings are pre-existing infra noise — urllib3/LibreSSL and google.api_core's
Python-3.9 deprecation notice — unrelated to this change; +32 tests over the 121
baseline: 2 receipts + 10 db + 5 ai_metrics + 7 scan_gmail + 5 api_routes + 3 scorecard)

```
$ cd frontend && npx vitest run
 Test Files  1 passed (1)
      Tests  20 passed (20)
$ npm run build
✓ built in 226ms   (tsc --noEmit clean, vite build clean)
```

## Self-Review

- All 4 areas implemented per plan, each with a red→green TDD cycle before its commit.
- Existing delivery-order behavior unregressed: every pre-existing test in
  `tests/test_scan_gmail.py` still passes, including the tip-only recovery, refund
  follow-up, order/tip ordering, and same-day-different-dayparts dedupe tests. The two
  tests whose fixture data now also produces a ride got a `classify_work_ride` mock
  added (to avoid a live Anthropic call) but their original delivery-order assertions
  are untouched.
- Ride-dedupe test (`test_ride_cluster_dedupe_by_ride_time_not_subject`) and the
  identical-subject/different-time test
  (`test_two_distinct_rides_same_morning_identical_subject_two_rows`) both genuinely
  assert the behavior the spec calls out as the critical correctness point — they use
  a shared subject line across both scenarios and differ only in parsed ride time, so
  either one would fail if dedupe regressed to subject-keying.
- Work-override test coverage genuinely exercises "flag but still count until
  confirmed": `test_get_rides_resolved_is_work_reflects_user_override` and
  `test_scorecard_rides_count_and_spend_exclude_confirmed_work_include_ai_flagged`
  both set `ai_is_work=True` via `set_ride_classification` first and assert the ride
  STILL resolves to personal/counted, then confirm via `set_ride_work_override` and
  assert it flips to excluded — this is the one behavior most likely to be got wrong
  by a naive "if ai_is_work: exclude" implementation, and the tests would catch that.
- Full backend suite (153) + frontend tests (20) + frontend build all green from the
  worktree, run after every area and once more at the end. Output is pristine — no
  skipped, xfailed, or flaky tests; the only warnings are pre-existing infra noise.
- `rides` is confirmed absent from `metrics.METRICS`, `targets`, and
  `scorecard["metrics"]` by dedicated tests — no accidental hit/miss ledger row.
- Followed the analogous social-override pattern closely: `get_ride_examples` /
  `classify_work_ride`'s examples block mirror `get_classification_examples` /
  `classify_social_event` almost verbatim (prompt shape, "newest override first"
  ordering, present/absent block tests). `set_ride_work_override`'s "return whether a
  row was touched" mirrors `set_event_overrides`' pattern of the API layer trusting the
  DB layer for the true/false-existed signal.
- `metrics.py`, `config.py` untouched — no new env var was needed (`GMAIL_SCAN_LOOKBACK_DAYS`
  is reused as specified), and rides needed no pure-computation changes since there's no
  hit/miss math for a tracking-only series.

## Concerns

- None blocking. Two minor judgment calls worth flagging:
  1. `database.py`'s SQLite-bool-cast fix (`_ride_bool_rows`) was implemented during
     Area 4 rather than Area 2, because the test that caught the gap
     (`ai_is_work is True` over the wire) is API-shaped and only exists in Area 4. The
     change itself is DB-layer and small; I judged it more honest to commit it where
     the failing test lived rather than back-patch Area 2's already-landed commit.
  2. Postgres-specific SQL (the `bool_t`/`serial` DDL, `ON CONFLICT` clauses) was not
     exercised against a live Postgres instance — only SQLite, per the existing test
     harness (`temp_db_path` fixture). This matches how `delivery_orders` and
     `calendar_events` were originally tested in this repo, so it's consistent with
     established practice, but it's not itself proof the Postgres path is bug-free in
     production.

No pushes were made. Branch `rides-tracker` has 4 new commits on top of the baseline,
all authored in this worktree.

## Review fix wave

Applied a code-review's fixes for two amount-accuracy bugs plus five minor cleanups.
TDD: failing tests written first for issues 1, 2, 3, 4, and 7; confirmed red, then
made green with the minimal fix.

### 1. IMPORTANT — "later email wins" inverted to "processed-last wins" (`jobs/scan_gmail.py`)

Root cause: Gmail's `messages.list` returns newest-first (`services/gmail_service.py`
does no sorting). The first candidate for a ride key inserted the row; every later
candidate for that key unconditionally overwrote `amount` via `db.set_ride_amount`.
Within one scan this meant the OLDER (chronologically earlier) email — processed
last, since it's later in Gmail's newest-first list — always won. It was also
persistent: the losing candidate's `gmail_message_id` was never recorded anywhere,
so it re-entered the ride branch and re-applied its (wrong) amount on every
subsequent scan inside the 30-day lookback.

Fix:
- `database.find_ride_by_key` now also selects `ride_at` (the timestamp that
  produced the currently stored amount).
- `database.set_ride_amount` gained an optional `ride_at` param — when a candidate's
  amount wins, its own timestamp is written back into `ride_at`, keeping the
  "what currently backs the stored amount" bookkeeping correct for the *next*
  comparison (needed for 3+ email chains, and for the reverse arrival order where
  a genuinely later email is ingested in a later scan).
- `jobs/scan_gmail.py`'s ride branch now only overwrites when
  `cand["ordered_at"] > existing["ride_at"]` (strict — ties keep the stored value),
  instead of unconditionally overwriting whenever `amount is not None`.

Tests added (`tests/test_scan_gmail.py`):
- `test_ride_amount_later_email_wins_within_single_run` — feeds both emails in ONE
  `run()` call in newest-first order (charge summary before receipt, matching real
  Gmail ordering); asserts the genuinely later email's amount ($16.50) wins even
  though it's processed first.
- `test_ride_amount_stable_across_repeated_scans` — runs the same newest-first scan
  twice; asserts the amount is identical and correct both times (guards against the
  persistent re-pinning described above).

Both failed pre-fix (`14.0 == 16.5` — the older email's amount won) and pass post-fix.

### 2. IMPORTANT — fallback dedupe key included `amount` (`jobs/scan_gmail.py`)

Root cause: when the ride timestamp couldn't be parsed from the snippet, the
fallback key was `f"{day}|{subject}|{amount}"`. Two duplicate emails for one trip
(receipt vs. adjusted charge summary) routinely carry different totals, so they
produced two different keys and created TWO rows for one trip — defeating dedupe
exactly in the case it exists to handle.

Fix: dropped `amount` from the fallback key — `f"{day}|{cand['subject']}"`. This is
inherited from the spec's own fallback-key shape; the spec's stated intent was
dedupe, so the code (not the spec's example key) was wrong and is what got fixed.

Test added: `test_ride_fallback_key_dedupes_without_amount_in_key` — two duplicate
candidates with no parseable ride time and different totals ($14.00 / $16.50);
asserts exactly ONE ride is stored (failed pre-fix with `2 == 1`, i.e. two rows) and
that the genuinely later email's amount still wins post-fix (exercises issue 1's
fix on the fallback-key path too).

### 3. MINOR — `_RIDE_TIME_RE` required uppercase AM/PM (`receipts.py`)

Added `re.IGNORECASE` to the compiled pattern. Test added:
`test_extract_ride_time_lowercase_ampm` in `tests/test_receipts.py` — asserts a
lowercase `4:03 am` / `11:34 pm` snippet still parses to the same ISO string as the
uppercase form. Failed pre-fix (`None == '2026-07-19T04:03'`), passes post-fix.

### 4. MINOR — no 12:xx AM/PM coverage (`receipts.py`)

`extract_ride_time`'s hour math was already correct; only untested. Added
`test_extract_ride_time_noon_and_midnight` asserting `"Jan 1, 2026 12:05 AM"` →
`"2026-01-01T00:05"` and `"Jan 1, 2026 12:05 PM"` → `"2026-01-01T12:05"`. Passed
immediately (no code change needed) — confirms the math was sound, closes the gap.

### 5. MINOR — `GET /api/rides` leaked `ai_confidence` (`app/routes.py`)

`get_rides` spread the whole DB row (`{**r, "is_work": ...}`). Replaced with an
explicit field projection matching the spec's enumerated response shape (`id`,
`service`, `ride_at`, `subject`, `amount`, `ai_is_work`, `user_is_work`, `is_work`),
mirroring how `get_deliveries` two functions above already projects explicitly.
Tightened `test_get_rides_shape_order_and_days_clamp` from a subset check (`<=`) to
an exact `==` on `row.keys()`, so a future regression re-adding `ai_confidence` (or
any other column) would fail the test.

### 6. MINOR — `get_ride_examples` returned raw 0/1 from SQLite (`database.py`)

Normalized `user_is_work` to a real bool before returning, matching the
`_ride_bool_rows` pattern used by `get_rides_range` elsewhere in this layer. Added
`test_get_ride_examples_normalizes_bool_type` in `tests/test_database_v2.py`,
asserting `examples[0]["user_is_work"] is True` (identity check — `1 is True` is
`False` in Python, so this genuinely catches the raw-int leak). Failed pre-fix
(`1 is True`), passes post-fix.

### 7. MINOR — days clamp test didn't test the clamp (`app/routes.py` test only, no code change)

`min(max(days, 1), 365)` in `get_rides` was already correct; the test asserted
`days=0` → empty list, which holds whether or not clamping happens (a literal 0-day
window is also empty). Replaced with two real tests in `tests/test_api_routes.py`:
- `test_get_rides_days_lower_bound_clamps_to_1` — seeds a ride exactly 1 day back;
  `days=0` must include it (a literal 0-day window `[today, today]` would exclude
  it; the clamp-to-1 window `[yesterday, today]` includes it).
- `test_get_rides_days_upper_bound_clamps_to_365` — seeds one ride 200 days back and
  one 400 days back; `days=1000` must include the 200-day ride and exclude the
  400-day ride, proving the window is actually capped at 365 rather than passed
  through raw (400 < 1000, so it would be included under an unclamped window).

Both passed immediately against the existing implementation — confirms the
clamp logic itself was already correct, only its test coverage was weak.

### Verification

```
$ source venv/bin/activate && pytest tests/ -v
======================= 161 passed, 2 warnings in 2.20s ========================
```
(153 baseline + 8 new: 3 in `test_scan_gmail.py`, 2 in `test_receipts.py`,
2 in `test_api_routes.py`, 1 in `test_database_v2.py`.)

```
$ cd frontend && npm test -- --run
 Test Files  1 passed (1)
      Tests  20 passed (20)

$ npm run build
✓ 40 modules transformed.
✓ built in 230ms
```

No behavior changed beyond the issues listed above; the spec's Out-of-Scope list
stays binding. `database.py` remains the only file with SQL, `ai_metrics.py` the
only Claude caller, `MODEL` untouched.
