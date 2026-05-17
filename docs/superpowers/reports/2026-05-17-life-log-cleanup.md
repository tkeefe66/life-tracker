# Life Log Cleanup Analysis — 2026-05-17

**Status: READY TO CLEAN UP**

Two weeks have passed since the M10 architectural cutover from the old accomplishments/focus/later
bot to the Life Log memoir substrate. The new system (Life Log + Stories + People + Habits) is live:
recent commits are entirely in the story-review, `buildstories`, `syncstories`, and calendar-proposal
space, and all new handlers are registered and wired. No blockers were found that would prevent
removing the dead code identified below.

---

## Dead Functions in bot.py

All functions below exist in `bot.py` but have **no registered command handler** and **no reachable
caller** in the current codebase. They were left in place to keep the cutover commit (`01e4d47`)
small. Evidence: grep across all `.py` files shows each function name appears only at its own
definition (and in the dead state branches listed further below).

### Unregistered command handlers

| Function | Line | Evidence |
|---|---|---|
| `log_command` (old version) | 308 | Not registered; `lifelog_log_command` (from `handlers/log_command.py`) is the registered handler at line 1054. Grep: only definition at line 308. |
| `update_command` | 343 | Not in `create_application()`. Grep: definition only. Commit `01e4d47` removed its registration. |
| `work_command` | 507 | Not in `create_application()`. Grep: definition only. |
| `personal_command` | 520 | Not in `create_application()`. Grep: definition only. |
| `focus_command` | 533 | Not in `create_application()`. Grep: definition only. |
| `later_command` | 686 | Not in `create_application()`. Grep: definition only. |
| `calendarsync_command` | 986 | Defined inside `if _CALENDAR_JOBS_AVAILABLE:` block but never passed to `add_handler()`. Grep: definition only. |

### Unscheduled job

| Function | Line | Evidence |
|---|---|---|
| `daily_prompt_job` | 162 | Not in `create_application()` job queue. Grep: definition only; no `run_daily` call references it. |

### Dead helpers (only called from dead code)

| Function | Line | Evidence |
|---|---|---|
| `_parse_log_date` | 295 | Only caller is the dead old `log_command` at line 317. Grep: two hits — definition + one call at line 317, both dead. |
| `_start_collection` | 148 | Called only from dead functions: old `log_command` (line 340), `update_command` (line 378), `daily_prompt_job` (line 185). Grep: no other callers. |
| `_prompt_work` | 116 | Called from `_start_collection` (dead) and the dead `collecting_work`/`collecting_personal` state branches. Grep: no live callers. |
| `_prompt_personal` | 126 | Same as `_prompt_work`. |
| `_prompt_focus` | 135 | Called from `_start_collection` (dead), `update_command` (dead), and dead `collecting_focus` branch. |
| `_handle_freeform_message` | 739 | The idle-state handler no longer calls this — it now returns a static help string. The only remaining calls are within the unreachable `confirming_freeform` state block (lines 955–968). Grep: definition + two internal calls within dead block. |
| `_format_freeform_preview` | 699 | Called only from `_handle_freeform_message` (dead) and the `confirming_freeform` state block (lines 955, 957). Both dead. |
| `_save_freeform_entries` | 722 | Called only from the `confirming_freeform` state block (line 955). Dead. |

### Dead state branches in `handle_message`

The following `elif state == "..."` branches in `handle_message` can no longer be entered — the
states they handle are only set by functions that are themselves dead (no registered handler, no
active caller). A state persisted in the DB from before the cutover could in theory hit them once,
but no new user session can reach them.

| State | Lines | Entry point (dead) |
|---|---|---|
| `collecting_work_only` | 832–838 | `work_command` (not registered) |
| `collecting_personal_only` | 841–847 | `personal_command` (not registered) |
| `collecting_work` | 850–853 | `_start_collection` via dead callers |
| `collecting_personal` | 855–868 | transition from `collecting_work` (dead) |
| `collecting_focus` | 870–872 | transition from `collecting_personal` (dead) or dead callers |
| `collecting_later_item` | 929–930 | `later_command` (not registered) |
| `collecting_later_date` | 937–946 | `collecting_later_item` block (dead) |
| `confirming_freeform` | 948–968 | `_handle_freeform_message` (idle path no longer calls it) |

### Dead branches in `skip_command` (lines 392–431)

`skip_command` is still registered and needed for the habits and Life Log flows. However, six of its
eight `elif` branches are unreachable because the states they guard can no longer be entered:

- `collecting_work` → `collecting_personal` (lines 392–394)
- `collecting_personal` → next date / `collecting_focus` (lines 396–404)
- `collecting_focus` → idle (lines 406–409)
- `collecting_later_item` → idle (lines 416–418)
- `collecting_later_date` → idle + save (lines 420–425)
- `confirming_freeform` → idle (lines 429–431)

The surviving live branches are `collecting_habit_check` / `collecting_habit_reason` /
`confirming_habit` and the closing `"idle"` guard.

### Partially-dead registered jobs/commands (not safe to delete, but worth noting)

- **`weekly_summary_job`** (line 188, scheduled Sunday): still runs and calls
  `_sync_to_sheets_with_ai()` (active), but its Telegram summary output reads from `accomplishments`
  and `weekly_focus` tables which are no longer written to — so the Telegram message will always show
  "no accomplishments recorded this week." Consider replacing its body with a Life Log digest or
  removing the Telegram summary portion.

- **`status_command`** (line 434, registered): reads `accomplishments` and `weekly_focus` tables —
  will always show zero entries under the new system. The command itself is harmless but misleading.

---

## Dead Modules

### `jobs/daily_calendar.py`

- **What it does:** Fetches upcoming Google Calendar events and writes them to `later_items`. Sends
  a Telegram summary.
- **Why it's dead:** The job was retired in commit `0065b75` ("retire calendar→Later job, keep
  monthly_forward"). Its entry points `run_daily_calendar_sync` and `run_bulk_calendar_sync` are
  imported in `bot.py` lines 50–51 inside a `try/except` block, but the resulting job wrappers
  (`calendar_sync_job`, `calendarsync_command`) are either commented out or unregistered in
  `create_application()`.
- **Grep evidence:** Only import site is `bot.py:50`. No other file imports this module.
- **Safe to delete:** Yes, pending confirmation that `later_items` data is no longer needed.

### `jobs/daily_ai_status.py`

- **What it does:** For each `later_item` without an AI status, calls Claude to suggest
  pending/habit/logged and writes the result back.
- **Why it's dead:** Its entry point `run_daily_ai_status` is imported in `bot.py:51` (same
  try/except block as above), but the resulting `ai_status_job` wrapper is commented out in
  `create_application()` (lines 1124–1128 show the commented `ai_status_job` registration).
- **Grep evidence:** Only import site is `bot.py:51`. No other file imports this module.
- **Safe to delete:** Yes.

### `ai_summarize.py` — NOT dead (still has live callers)

This file was noted as a candidate but has two active callers:

1. `parse_habit` — called by `habit_command` (line 623, registered handler).
2. `_call` — called by `jobs/monthly_forward.py` (line 17, active scheduled job).

`parse_freeform_message` (line 240) is dead — only called from `_handle_freeform_message` (dead).
`summarize_focus_and_extract_later` and other old functions appear to have no callers outside the
file itself. However, the module cannot be deleted because `parse_habit` and `_call` are live.

**Recommendation:** Keep the file; remove `parse_freeform_message` and `summarize_focus_and_extract_later` in the cleanup PR.

---

## DB Tables No Longer Written To

> **Note:** Dropping tables in production is a separate, deliberate decision and is NOT recommended
> here. This section only identifies tables that receive no new writes under the current code.
> Existing data in these tables is safe as long as the tables exist.

### Tables confirmed to have no active write paths

| Table | Write function(s) | Status of callers |
|---|---|---|
| `accomplishments` | `db.save_accomplishment()` | All call sites are in dead state handlers (`collecting_work`, `collecting_personal`, `collecting_work_only`, `collecting_personal_only`) and dead `confirming_freeform` block. No active path writes to this table. |
| `weekly_focus` | `db.save_weekly_focus()` | All call sites are in dead state handlers (`collecting_focus`, `confirming_freeform`) and dead `focus_command`. No active path writes. |
| `later_items` | `db.save_later_item()`, `db.save_later_item_full()` | `save_later_item` is called only from dead state handlers and dead `confirming_freeform` block. `save_later_item_full` is called only from `jobs/daily_calendar.py` (dead module, see above). No active path writes. |
| `focus_summary_cache` | `db.save_cached_summary()` | Zero callers found anywhere in the codebase. The function is defined at `database.py:712` but never called. Table receives no writes. |
| `later_org_cache` | `db.save_cached_later_org()` | Zero callers found anywhere in the codebase. The function is defined at `database.py:769` but never called. Table receives no writes. |

### Tables that are still read but no longer written (creates misleading output)

| Table | Read by | Impact |
|---|---|---|
| `accomplishments` | `weekly_summary_job` (line 194), `status_command` (line 436), `update_command` (dead) | `weekly_summary_job` and `status_command` will always show zero entries |
| `weekly_focus` | `weekly_summary_job` (line 195), `status_command` (line 437) | Focus section will always be blank |

### Dropping is a separate decision

Do not drop these tables without:
1. Confirming no production data in them needs to be archived elsewhere
2. Running a migration to remove the schema from `database.py` initialization
3. Removing the table names from `_handle_cleardb_confirm` and `scripts/cleardb.py`

---

## Sanity Check — New Life Log System

**Assessment: Live and healthy.**

Evidence from `git log --oneline`:
- The 10 most recent commits are all Life Log/Stories features: `syncstories`, `pushstories`,
  `showstory`, story review state machine, `buildstories`, story clustering, and sheet integration.
- The story handlers (`handlers/buildstories.py`, `handlers/syncstories.py`,
  `handlers/story_review.py`, etc.) are all registered in `create_application()`.
- The three calendar proposal jobs (`lifelog_realtime`, `lifelog_dayafter`, `lifelog_sunday`) are
  registered and scheduled.
- `handlers/log_command.py` (the new `/log` flow) and `handlers/lifelog_queries.py` (the `/ask`
  flow) are imported and wired.

No commits in the last two weeks touch the old accomplishments/focus/later system.

---

## Suggested Next-Step PR

**Title:** `cleanup: remove dead accomplishments-era code from bot.py and jobs/`

**Scope:**
1. Delete `jobs/daily_calendar.py` and `jobs/daily_ai_status.py` (dead modules).
2. In `bot.py`, delete the following dead functions:
   - `log_command` (old version, lines 308–340)
   - `update_command` (lines 343–378)
   - `work_command` (lines 507–517)
   - `personal_command` (lines 520–530)
   - `focus_command` (lines 533–544)
   - `later_command` (lines 686–695)
   - `calendarsync_command` (lines 986–996)
   - `daily_prompt_job` (lines 162–185)
   - `_parse_log_date` (lines 295–305)
   - `_start_collection` (lines 148–157)
   - `_prompt_work`, `_prompt_personal`, `_prompt_focus` (lines 116–143)
   - `_handle_freeform_message`, `_format_freeform_preview`, `_save_freeform_entries` (lines 699–777)
3. In `bot.py`, remove dead state branches from `handle_message` (`collecting_work_only`,
   `collecting_personal_only`, `collecting_work`, `collecting_personal`, `collecting_focus`,
   `collecting_later_item`, `collecting_later_date`, `confirming_freeform`).
4. In `bot.py`'s `skip_command`, remove the six dead state branches while keeping the three
   habit-related branches.
5. In `bot.py`'s `_handle_cleardb_confirm`, remove the try/except imports of the dead calendar
   jobs (lines 50–51 in the try block) and the `calendar_sync_job`/`ai_status_job` wrappers (lines
   973–984) if they are no longer needed after the module deletions.
6. In `ai_summarize.py`, remove `parse_freeform_message` and `summarize_focus_and_extract_later`
   (and any other functions with zero callers). Keep `_call` and `parse_habit`.
7. Clean up `weekly_summary_job` to either remove it (if weekly summaries are now handled
   differently) or replace its accomplishments-reading logic with a Life Log digest.
8. Clean up `status_command` to show Life Log entry counts instead of accomplishments.

**Do not include in this PR:** DB schema changes (`database.py` table definitions, `initialize_db`).
That is a separate, careful production migration with its own checklist.

**Estimated risk:** Low. All removed code is definitively unreachable by any registered command or
scheduled job. The try/except import block means removing the dead job files won't crash startup —
`_CALENDAR_JOBS_AVAILABLE` will still be `True` as long as the live calendar jobs import cleanly.
