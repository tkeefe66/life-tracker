# MUST DO FIRST

## Backup encryption — resolved 2026-08-06, and what it cost

The blocking reminder that used to head this file has been discharged.
`scripts/verify_backup.py` reports `VERIFIED` against
`on-track-backups/20260806T185121.dump.enc` — 538 KB ciphertext, decrypts to a
403 KB dump, `pg_restore` reads 24 tables. That is the first encrypted backup
ever proven readable.

**It was worse than the reminder feared.** `BACKUP_ENCRYPTION_KEY` on the
Railway web service held the literal text
`python3 -c "from cryptography.fernet import Fernet; print(...)"` — the command
that *prints* a key, pasted instead of its output. `_is_encryption_configured()`
only checks truthiness, so encryption read as enabled while `Fernet(key)` raised
on every run. **No backup of any kind existed between 2026-08-03 and
2026-08-06.** The last good one before that was plaintext.

Three things let it hide for three days, all now closed:

- The failure alert fired once, on the status *transition*, then went quiet.
  `jobs/backup_db.py` now re-alerts every `REALERT_AFTER_DAYS` (7) while a
  failure is still live, and says how long it has been broken.
- Every cron job was firing on **UTC** wall-clock, not `TIMEZONE` — the 4 AM
  backup ran at 10 PM Denver. See "Cron jobs" below.
- `tests/test_backup_db.py` read the ambient `BACKUP_ENCRYPTION_KEY` from
  `.env`, so the same malformed value made four tests fail on `main` with
  nothing pointing at the cause. An autouse fixture now neutralizes it.

**Still owed by a human:** store the key somewhere other than Railway (a
password manager). Fernet has no recovery path — a wiped or compromised Railway
project takes every encrypted backup with it, at the moment backups matter most.

Re-run `venv/bin/python scripts/verify_backup.py` after anything touches the
backup path. Success = newest key ends `.dump.enc` and the run prints
`VERIFIED`. If it ever reports `InvalidToken`, the Railway key and the local
`.env` key have diverged — stop and tell the owner.

---

**Read the rest of this before running anything in this repo.** Written
2026-08-03 after a security audit and a 14-task hardening pass, both of which
hit traps documented here. Every warning below is something that actually went
wrong, not a hypothetical.

Read `CLAUDE.md` too — that's the project guide. This file is only the things
that will bite you in the first ten minutes.

---

## 1. The local environment lies to you

On 2026-08-03 all three of these were true simultaneously, and the result was
that "tests pass locally" had stopped meaning anything at all:

- `venv/` was Python **3.9**. Production runs **3.11** (`nixpacks.toml` installs
  `python311`; `.python-version` says `3.11`).
- A bare `pytest` resolved to **Homebrew's system Python**, not the venv — with
  `fastapi 0.136.3` installed, while `requirements.txt` pinned `0.128.8`. So the
  suite was passing against a different framework version than production
  installed.
- `pytest` and `pytest-asyncio` are **not in `requirements.txt`** (runtime deps
  only; there is no `requirements-dev.txt`), so a clean venv can't run the suite
  until you install them by hand.

### Rebuild before you trust anything

```bash
cd "/Users/tomkeefe/Code Apps/life-tracker"
/opt/homebrew/opt/python@3.11/bin/python3.11 -m venv --clear venv
venv/bin/pip install -r requirements.txt
venv/bin/pip install pytest pytest-asyncio   # deliberately not tracked
venv/bin/python --version                    # must print 3.11.x
venv/bin/pytest tests/ -q                    # expect 639 passed
```

```bash
cd frontend && npm install && npm test -- --run && npm run build
```

Expected: 639 Python tests, 113 frontend tests, clean build.

### Always invoke by path

**`venv/bin/pytest`, `venv/bin/python`, `venv/bin/pip` — never bare.**
`source venv/bin/activate` has silently failed in this environment. A bare
`pip install` lands in the wrong interpreter and makes every subsequent
verification meaningless.

---

## 2. Any script that imports `config.py` talks to PRODUCTION

`config.py` calls `load_dotenv()`, and a real `.env` with live credentials sits
in the working directory. **Unsetting an environment variable in your shell does
not isolate you** — `load_dotenv()` repopulates `os.environ` inside the
subprocess regardless.

This is not theoretical. On 2026-08-03 an agent ran
`env -u BACKUP_S3_BUCKET python scripts/verify_backup.py` intending to exercise
the "not configured" error path. It connected to the live Backblaze bucket and
downloaded a real 401 KB production database backup. Read-only, and cleaned up
after itself, but entirely by luck of which code path it hit.

**To isolate from production, monkeypatch in-process. Do not rely on the shell
environment.**

### Never run these

| Script | Why |
|---|---|
| `scripts/cleardb.py` | Wipes all data. |
| `scripts/drop_v1_archive.py` | Drops eleven tables. The only `DROP` in the codebase. |
| `scripts/verify_backup.py` | Hits the production bucket and downloads a real backup. |
| `scripts/simplefin_backfill.py` | Writes to the database. |

`--help` is safe on all of them. Nothing else is. If a task needs one run, that
is a human's decision, not yours.

---

## 3. Invariants you must not break

These are load-bearing. Each exists because something went wrong once.

- **`database.py` is the only file with SQL.** `config.py` is the only file
  reading `os.environ` (the `scripts/` one-offs are the documented exception).
  `ai_metrics.py` is the only file calling Claude.
- **The redaction boundary.** Never store `str(exception)` in `app_settings` or
  put it on any outbound path. Use `logger.exception(...)` server-side, then
  `services.safe_status.safe_status(e)` for the stored value. The SimpleFIN
  access URL *is* a credential and HTTP libraries put URLs into exception
  messages — this leaked for real on 2026-07-23.
- **Never lower the `httpx`/`httpcore` log levels** pinned to `WARNING` in
  `main.py`. Same leak, different path. A test pins the *explicit* level, not
  the effective one — that distinction is the point.
- **Telegram is an outbound path.** Security alerts added in 2026-08-03 build
  their message text only from closed-set `safe_status` values. Keep it that way.
- **Pin dependencies with `==`.** `nixpacks.toml` reinstalls from
  `requirements.txt` on every deploy and no lockfile is committed, so a `>=`
  floor lets an unverified release reach production between deploys.
- **Don't edit `CLAUDE.md` without the owner's explicit confirmation.**

---

## 4. Two gotchas that will waste your afternoon

**Date-dependent tests.** The app's week runs Monday–Sunday. A test that dates a
fixture "yesterday" and queries a one-week window passes six days out of seven
and fails on Mondays. One such test existed and was fixed on 2026-08-03; if you
write date fixtures, derive them from `metrics.week_bounds()`, not from
`today - 1 day`.

**macOS bytecode caching defeats mutation testing.** When you edit code to prove
a test actually fails, set a fresh `PYTHONPYCACHEPREFIX` per run:

```bash
PYTHONPYCACHEPREFIX=/tmp/pycache-mut1 venv/bin/pytest tests/test_x.py -k y -v
```

macOS caches bytecode outside the tree, and a same-size edit within the same
second silently reuses the stale `.pyc`. Deleting `__pycache__` does nothing.
Without this your mutation looks inert and you'll conclude the code is fine.

**The local browser cannot log in.** The session cookie is `Secure`
(`app/auth.py`), so a real browser won't send it over a plain-HTTP dev server.
Any UI or CSP verification needs a deploy. Don't burn turns fighting it.

**Cron jobs: never construct a bare `CronTrigger`.** Use `main._cron(...)`.
`AsyncIOScheduler(timezone=...)` does **not** retag a trigger that already
exists — `add_job()` applies the scheduler's timezone only when it builds the
trigger itself. A standalone `CronTrigger` captures the *process's* local zone,
which in the Railway container is UTC. From the first deploy until 2026-08-06
every cron job silently ran on UTC wall-clock: backup 4 AM → 10 PM Denver,
calendar scan 6 AM → midnight, Monday push 9 AM → Monday 3 AM. Interval jobs
(gmail, bank) were never affected. Locked by `tests/test_scheduler.py`, which
forces `TZ=UTC` so a Denver laptop can't make the assertion vacuous.

---

## 5. Outstanding work as of 2026-08-03

Done on 2026-08-03:

- [x] **Pushed and deployed.** fastapi 0.136.3 / starlette 1.3.1 are live and
      serving. Verified in production: body cap returns 413, full CSP present,
      `Cache-Control: no-store, private` on `/api/*`, all data routes 401
      without a session, `/docs` and `/openapi.json` 404.
- [x] **`BACKUP_ENCRYPTION_KEY` set** on the Railway web service and added to
      the local `.env`. A copy is in the owner's password manager.
- [x] **Unencrypted backup verified** — 401 KB, 24 tables, `pg_restore` parses.
      The *encrypted* path is still unverified: see the blocking section at the
      top of this file.

Still open:

- [ ] **Verify an ENCRYPTED backup** — the blocking item at the top.
- [ ] **Backblaze:** confirm the bucket is private, and decide on key scope. The
      current `BACKUP_S3_ACCESS_KEY` can **list and download** — confirmed
      accidentally — so a leak of it exposes every backup.
- [ ] **Browser-check the CSP on a deploy** — all five screens, Money especially
      (hand-rolled SVG charts). Look for `Refused to load` in the console.
      Cannot be done locally: the `Secure` cookie blocks login over plain HTTP.
- [ ] **`scripts/drop_v1_archive.py --export-only`**, read the JSON, then
      `--export-and-drop`. Eleven unused v1 tables including a rolodex of named
      people with relationship notes.
- [ ] **Before making the repo public:** `scripts/simplefin_backfill.py` has real
      bank names and account last-4 digits hardcoded, and they are in git
      history. Needs a history rewrite, not a deletion.
- [ ] `inventory.tomkeefe.ai` appears in Certificate Transparency alongside this
      app. Never audited.

---

## 6. Where the record lives

- `SECURITY-AUDIT.md` — the five-lens audit. Verdict: safe on a public hostname,
  no critical findings. Read the "Checked and clean" section before re-auditing
  anything.
- `docs/superpowers/plans/2026-08-03-security-hardening.md` — the plan that
  produced the current state, corrected in place as review found defects in it.
- `docs/superpowers/specs/` and `plans/` — one pair per feature, chronological.
  Several encode rejected alternatives that aren't recoverable from the code.
  Read the relevant pair before changing a subsystem.
