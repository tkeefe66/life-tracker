"""Scheduled job: weekly PostgreSQL backup to an off-Railway destination
(BACKUP_HOUR, default 4am, every Sunday — see main.py's scheduler).

Skips silently (with a logged warning) when the app isn't using PostgreSQL, or
when any BACKUP_S3_* env var is unset, so local dev and an un-configured
deploy are unaffected. Retains the last RETENTION dumps at the destination.

Follows the redaction boundary (services/safe_status.py): a real pg_dump
failure or S3 error can embed DATABASE_URL or the S3 credentials in its
message, so only safe_status(e) is ever stored — never str(e)."""
import datetime
import logging
import os
import subprocess
import tempfile

import pytz

import database as db
from config import (
    BACKUP_S3_ACCESS_KEY,
    BACKUP_S3_BUCKET,
    BACKUP_S3_ENDPOINT,
    BACKUP_S3_SECRET_KEY,
    DATABASE_URL,
    TIMEZONE,
)
from services.safe_status import safe_status

logger = logging.getLogger(__name__)

RETENTION = 8
BACKUP_PREFIX = "on-track-backups/"


def _now_iso() -> str:
    return datetime.datetime.now(pytz.timezone(TIMEZONE)).isoformat()


def _using_postgres() -> bool:
    """Seam for tests — monkeypatch this rather than database.USE_POSTGRES
    directly, which also gates every other DB call (get_setting/set_setting
    included) and would break the test's own status bookkeeping."""
    return db.USE_POSTGRES


def _is_configured() -> bool:
    return bool(BACKUP_S3_BUCKET and BACKUP_S3_ENDPOINT and BACKUP_S3_ACCESS_KEY and BACKUP_S3_SECRET_KEY)


def _s3_client():
    """Lazy import — boto3 is only required once backups are actually configured,
    so an un-configured deploy never needs it installed to boot."""
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=BACKUP_S3_ENDPOINT,
        aws_access_key_id=BACKUP_S3_ACCESS_KEY,
        aws_secret_access_key=BACKUP_S3_SECRET_KEY,
    )


def _dump_to_file(path: str) -> None:
    """Runs pg_dump against DATABASE_URL, writing a custom-format dump to `path`.
    Raises on failure — the caller wraps this in safe_status(), never str(e),
    since a pg_dump error message can embed DATABASE_URL itself."""
    with open(path, "wb") as f:
        subprocess.run(
            ["pg_dump", DATABASE_URL, "--format=custom"],
            stdout=f, stderr=subprocess.PIPE, check=True,
        )


def _upload(local_path: str, key: str) -> None:
    """Isolated so tests can assert it's called with the expected key without
    exercising a real S3-compatible client."""
    _s3_client().upload_file(local_path, BACKUP_S3_BUCKET, key)


def _prune_old_backups() -> None:
    """Keeps only the most recent RETENTION dumps at the destination."""
    client = _s3_client()
    resp = client.list_objects_v2(Bucket=BACKUP_S3_BUCKET, Prefix=BACKUP_PREFIX)
    objects = sorted(resp.get("Contents", []), key=lambda o: o["Key"])
    for obj in objects[:-RETENTION] if len(objects) > RETENTION else []:
        client.delete_object(Bucket=BACKUP_S3_BUCKET, Key=obj["Key"])


def run():
    if not _using_postgres():
        logger.info("Backup skipped: not using PostgreSQL (local SQLite dev)")
        return
    if not _is_configured():
        logger.warning("Backup skipped: BACKUP_S3_* env vars not fully set")
        db.set_setting("backup_last_status", "error: not configured")
        return
    try:
        stamp = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y%m%dT%H%M%S")
        key = f"{BACKUP_PREFIX}{stamp}.dump"
        fd, tmp_path = tempfile.mkstemp(suffix=".dump")
        os.close(fd)
        try:
            _dump_to_file(tmp_path)
            _upload(tmp_path, key)
            _prune_old_backups()
        finally:
            os.unlink(tmp_path)
        db.set_setting("backup_last_run", _now_iso())
        db.set_setting("backup_last_status", "ok")
        logger.info("Backup uploaded: %s", key)
    except Exception as e:
        logger.exception("Backup failed")
        db.set_setting("backup_last_run", _now_iso())
        db.set_setting("backup_last_status", safe_status(e))
