---
name: backup-verification
description: Use when touching jobs/backup_db.py, scripts/verify_backup.py, or any BACKUP_* env var; when asked whether the backup is good, readable, or restorable; after any change to the backup path; or when investigating a backup-failure alert or an InvalidToken / "no recent backup" symptom.
---

# Backup Verification

## Overview

**A backup job reporting `ok` proves an upload happened. It does not prove the
artifact is readable.** `jobs/backup_db.py` checks that `pg_dump` exited 0, the
file clears `MIN_DUMP_BYTES`, the upload landed, and the key appears in a
post-upload listing. It never decrypts and never calls `pg_restore`. Only
`scripts/verify_backup.py` does that.

So there is exactly one answer to "is the backup good?": **run the verifier.**
Status text, deploy logs, and a green Settings screen are all compatible with a
bucket full of permanently unreadable files.

**Baseline failure this skill exists to prevent (2026-08-03 → 2026-08-06):**
`BACKUP_ENCRYPTION_KEY` on the Railway web service held the literal text of the
key-*generation* command — `python3 -c "from cryptography.fernet import Fernet;
print(...)"` — pasted instead of the key it prints. `_is_encryption_configured()`
only checks truthiness, so encryption read as enabled while `Fernet(key)` raised
on every run. **No backup of any kind existed for three days.** The alert fired
once on the status transition and then went quiet, and silence is what a healthy
backup looks like.

## Before you run anything

`MUST-DO-FIRST.md` lists `scripts/verify_backup.py` under "Never run these" — it
hits the production Backblaze bucket and downloads a real database backup. That
listing is about running it *incidentally*. When the task IS backup verification,
it is the sanctioned tool and section 1 of the same file tells you to re-run it
after anything touches the backup path.

Split by blast radius:

| Action | Needs owner go-ahead? |
|---|---|
| `scripts/verify_backup.py` (read-only: B2 → decrypt → `pg_restore --list`) | No — this is the point of the skill |
| Forcing `jobs.backup_db.run()` in the container (real `pg_dump`, real upload, real prune, may fire a Telegram "recovered") | Yes |
| Setting a `BACKUP_*` variable (triggers a redeploy) | Yes |
| Any restore | Yes — human-run only, see `scripts/verify_backup.py`'s docstring |

## The drill

### 1. Check the key is well-formed before spending a real dump on it

```bash
railway variables --project 01974f5d-7239-436f-9ccb-6fdca902e506 \
  --environment production --service 04543772-a23b-4576-9a41-6492d172e740 --kv \
  | grep BACKUP_ENCRYPTION_KEY
```

A valid Fernet key is 32 url-safe base64 bytes and nothing else — generate with
`Fernet.generate_key()`. **Never paste the generating command into the env var.**
Anything containing `python`, `import`, `print`, or quotes is the outage above.

Setting the variable triggers a redeploy. **Wait for SUCCESS before step 2** —
the old container is still serving the old value.

```bash
railway deployment list --project 01974f5d-7239-436f-9ccb-6fdca902e506 \
  --environment production --service 04543772-a23b-4576-9a41-6492d172e740 --limit 1
```

### 2. Force one backup run (don't wait for the daily cron)

`backup_db` is cron-only in `main.py` — unlike gmail/bank/calendar it has no
`next_run_time=now`, so `railway redeploy` does **not** force it.

```bash
railway ssh --project 01974f5d-7239-436f-9ccb-6fdca902e506 \
  --environment production --service 04543772-a23b-4576-9a41-6492d172e740 \
  '/opt/venv/bin/python -c "import jobs.backup_db as b; b.run()"'
```

**`/opt/venv/bin/python`, absolute, always.** The container's default `python` on
PATH is the Nix one and has none of the app's dependencies —
`ModuleNotFoundError: No module named 'pytz'`. No `LD_LIBRARY_PATH` juggling is
needed; the venv path alone is the fix.

Then confirm the stored status:

```bash
railway ssh --project 01974f5d-7239-436f-9ccb-6fdca902e506 \
  --environment production --service 04543772-a23b-4576-9a41-6492d172e740 \
  '/opt/venv/bin/python -c "import database as d; print(d.get_setting(\"backup_last_status\"))"'
```

**Why in-container and not locally:** `DATABASE_URL` points at
`postgres.railway.internal`, and `DATABASE_PUBLIC_URL` on the Postgres service
has an empty host — there is no TCP proxy, so Postgres is simply unreachable from
a laptop. **Do not create one to work around this.** A proxy permanently exposes a
financial database to the internet to save one round trip.

### 3. Verify from the laptop — NOT from the container

```bash
cd "/Users/tomkeefe/Code Apps/life-tracker"
venv/bin/python scripts/verify_backup.py
```

The verifier reads B2 and never touches the database, so the laptop is the right
place to run it. **Running it inside the container defeats the entire drill.**
In-container it decrypts with the Railway key that encrypted the file, so it
passes by construction. Run from the laptop it decrypts with the local `.env`
key — and *that* is the check: the two keys must be identical.

Success looks exactly like this (real 2026-08-06 output):

```
Newest backup: on-track-backups/20260806T185121.dump.enc
Downloaded 538020 bytes
Decrypted OK (403449 bytes)
pg_restore read the dump: 24 tables with data

VERIFIED: the newest backup exists, decrypts, and is readable.
```

Two things must both hold: the newest key ends **`.dump.enc`** (a `.dump` means
the newest backup predates encryption — the run still prints VERIFIED), and the
run prints **`VERIFIED`**.

## Reading the result

| Symptom | Meaning |
|---|---|
| `InvalidToken` | The Railway key and the local `.env` key have diverged. **Stop and tell the owner.** Re-run once to rule out a bad transfer, then stop — do not guess at keys. This is the failure mode the whole drill exists to catch: it produces backups that look healthy in every dashboard and are permanently unreadable |
| Newest key is `.dump`, not `.dump.enc` | Encryption is not actually in effect for new runs, whatever the env var says |
| `No backups found` / newest key is days old | A gap. Treat as urgent — SimpleFIN keeps a rolling 90 days, so data lost to a prolonged gap is genuinely unrecoverable |
| `VERIFIED` | The file exists, decrypts, and is a well-formed dump. It does **not** prove a restore into a live database succeeds |

For the destructive restore runbook — scratch DB first, pre-restore safety dump,
cleanup of all three plaintext copies — read the module docstring in
`scripts/verify_backup.py`. It is deliberately unscripted. Don't duplicate it,
don't automate it.

## Facts that stay true between drills

- **Fernet has no recovery path.** The key must live somewhere other than Railway
  (a password manager), or a wiped or compromised Railway project takes every
  encrypted backup with it, at the moment backups matter most.
- **Unset `BACKUP_ENCRYPTION_KEY` is not a failure.** Backups still run,
  unencrypted, with a logged warning — deliberate, because a backup gap is
  unrecoverable and an unencrypted backup is not.
- **Alerting:** `_set_status_and_alert` fires on a status *change*, plus a
  reminder every `REALERT_AFTER_DAYS` (7) while a failure is still live. The
  reminder exists because change-only alerting went silent for three days. Never
  reduce it back to change-only.
- **The alert message is built only from the closed-set status value**
  (`services/safe_status.py`) — never `str(exception)`. A `pg_dump` or S3 failure
  can embed `DATABASE_URL` or the S3 credentials in its message, and Telegram is
  an outbound path like any other.

## Adding backup tests

`tests/test_backup_db.py` has an autouse `no_ambient_encryption_key` fixture that
neutralizes whatever `BACKUP_ENCRYPTION_KEY` the developer's `.env` happens to
hold. Without it the malformed value above made three unrelated tests fail on
`main` for three days with nothing pointing at the cause
(`test_backup_uploads_with_expected_key_and_prunes`,
`test_alert_fires_on_recovery_back_to_ok`, `test_first_successful_run_sends_no_alert`
— every test whose path reaches `_encrypt_file`).

**Never reintroduce ambient-env dependence.** A test that needs a key sets one
explicitly via `monkeypatch.setattr(backup_db, "BACKUP_ENCRYPTION_KEY", key)`.

## Red flags

- Concluding "the backup is fine" from `backup_last_status`, a Settings screen, or
  a deploy log
- Running `scripts/verify_backup.py` through `railway ssh` or `railway run`
- Bare `python` in a `railway ssh` command
- Creating a Railway TCP proxy to reach Postgres from a laptop
- Putting anything but raw base64 in `BACKUP_ENCRYPTION_KEY`
- Treating `InvalidToken` as a transient error worth retrying more than once
