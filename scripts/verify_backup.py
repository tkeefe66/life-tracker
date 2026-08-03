"""One-off: verify the most recent database backup is readable.

Read-only. Downloads the newest dump from the backup bucket, decrypts it if
BACKUP_ENCRYPTION_KEY is set, and runs `pg_restore --list` to prove the file
is a structurally valid custom-format dump whose table-of-contents parses.
Never writes to any database.

    python scripts/verify_backup.py

Run it after changing anything about the backup path, and periodically
otherwise -- an untested backup is not a backup.

WHAT THIS DOES NOT PROVE: that a restore into a live database succeeds. It
proves the file exists, decrypts, and is a well-formed dump. That is the
large majority of what goes wrong, and it is provable without risk.

── FULL RESTORE RUNBOOK (destructive — a human runs this, deliberately) ──

Deliberately not scripted: a restore script that exists is one that can be
run by accident, and this restores over live financial data.

1. Verify first:
       python scripts/verify_backup.py

2. Download and decrypt the dump you want:
       python scripts/verify_backup.py --keep /tmp/restore.dump

3. Restore into a SCRATCH database first and confirm it looks right. Never
   restore straight into production:
       createdb ontrack_restore_test
       pg_restore -d ontrack_restore_test --no-owner /tmp/restore.dump
       psql ontrack_restore_test -c "SELECT count(*) FROM bank_transactions;"

4. Only if step 3 looks correct, restore into production. Take a fresh dump
   of the current state first -- you are about to overwrite it:
       pg_dump --format=custom -h $PGHOST -U $PGUSER -d $PGDATABASE \\
           > /tmp/pre-restore-safety.dump
       pg_restore -d "$DATABASE_URL" --clean --if-exists --no-owner /tmp/restore.dump

5. Delete /tmp/restore.dump and the scratch database when done -- both hold
   the full unencrypted contents of the database.
"""
import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import BACKUP_ENCRYPTION_KEY, BACKUP_S3_BUCKET  # noqa: E402
from jobs.backup_db import BACKUP_PREFIX, _is_encryption_configured, _s3_client  # noqa: E402


def _newest_backup_key() -> str:
    client = _s3_client()
    resp = client.list_objects_v2(Bucket=BACKUP_S3_BUCKET, Prefix=BACKUP_PREFIX)
    contents = resp.get("Contents", [])
    if not contents:
        raise SystemExit(f"No backups found under {BACKUP_PREFIX} — nothing to verify.")
    # Keys are timestamp-prefixed (YYYYmmddTHHMMSS), so lexical max is newest.
    return max(obj["Key"] for obj in contents)


def _download(key: str, path: str) -> None:
    _s3_client().download_file(BACKUP_S3_BUCKET, key, path)


def _decrypt(src_path: str, dst_path: str) -> None:
    from cryptography.fernet import Fernet

    with open(src_path, "rb") as f:
        token = f.read()
    with open(dst_path, "wb") as f:
        f.write(Fernet(BACKUP_ENCRYPTION_KEY.encode()).decrypt(token))


def _pg_restore_list(path: str) -> str:
    result = subprocess.run(
        ["pg_restore", "--list", path], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SystemExit(
            f"pg_restore could not read the dump (exit {result.returncode}).\n"
            f"{result.stderr[:2000]}"
        )
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        metavar="PATH",
        help="also write the decrypted dump here (for a deliberate restore)",
    )
    args = parser.parse_args()

    if not BACKUP_S3_BUCKET:
        raise SystemExit("BACKUP_S3_* not configured — nothing to verify.")

    key = _newest_backup_key()
    print(f"Newest backup: {key}")

    workdir = tempfile.mkdtemp(prefix="verify-backup-")
    downloaded = os.path.join(workdir, "downloaded")
    dump_path = downloaded

    try:
        _download(key, downloaded)
        print(f"Downloaded {os.path.getsize(downloaded)} bytes")

        if key.endswith(".enc"):
            if not _is_encryption_configured():
                raise SystemExit(
                    "Backup is encrypted but BACKUP_ENCRYPTION_KEY is not set. "
                    "Without the key this backup cannot be read — set it and retry."
                )
            dump_path = os.path.join(workdir, "decrypted.dump")
            _decrypt(downloaded, dump_path)
            print(f"Decrypted OK ({os.path.getsize(dump_path)} bytes)")
        elif _is_encryption_configured():
            print(
                "NOTE: newest backup is unencrypted but a key is configured — "
                "this dump predates encryption being enabled."
            )

        toc = _pg_restore_list(dump_path)
        tables = [line for line in toc.splitlines() if " TABLE DATA " in line]
        print(f"pg_restore read the dump: {len(tables)} tables with data")
        if not tables:
            raise SystemExit(
                "Dump parsed but contains NO table data — this backup is useless. "
                "Investigate before trusting it."
            )

        if args.keep:
            with open(dump_path, "rb") as src, open(args.keep, "wb") as dst:
                dst.write(src.read())
            print(f"Decrypted dump written to {args.keep}")
            print("It holds the full unencrypted database — delete it when done.")

        print("\nVERIFIED: the newest backup exists, decrypts, and is readable.")
    finally:
        for name in os.listdir(workdir):
            os.unlink(os.path.join(workdir, name))
        os.rmdir(workdir)


if __name__ == "__main__":
    main()
