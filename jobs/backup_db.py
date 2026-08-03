"""Scheduled job: weekly PostgreSQL backup to an off-Railway destination
(BACKUP_HOUR, default 4am, daily — see main.py's scheduler).

Skips silently (with a logged warning) when the app isn't using PostgreSQL, or
when any BACKUP_S3_* env var is unset, so local dev and an un-configured
deploy are unaffected. Retains the last RETENTION dumps at the destination.

Follows the redaction boundary (services/safe_status.py): a real pg_dump
failure or S3 error can embed DATABASE_URL or the S3 credentials in its
message, so only safe_status(e) is ever stored — never str(e).

Credential handling for pg_dump specifically goes further than the redaction
boundary requires elsewhere: DATABASE_URL itself carries the password, and
passing it to pg_dump as a positional argument would put the password in
/proc/<pid>/cmdline and `ps` for any other process on the box to read — a
leak that happens on every successful run, not just on failure. So pg_dump is
invoked with -h/-p/-U/-d flags (never the raw URL) and the password is passed
only via the subprocess environment (PGPASSWORD), never as an argv element.
subprocess.run is also called without check=True: CalledProcessError's str()
embeds the full argv it was given, so a non-zero exit is instead detected by
inspecting returncode and raised as our own exception with a message we fully
control (never one a library assembled from the command line)."""
import datetime
import logging
import os
import subprocess
import tempfile
from urllib.parse import parse_qs, unquote, urlparse

import pytz

import database as db
from config import (
    BACKUP_S3_ACCESS_KEY,
    BACKUP_S3_BUCKET,
    BACKUP_S3_ENDPOINT,
    BACKUP_S3_SECRET_KEY,
    DATABASE_URL,
    PGDATABASE,
    PGHOST,
    PGPASSWORD,
    PGPORT,
    PGSSLMODE,
    PGUSER,
    TIMEZONE,
)
from services.safe_status import NOT_CONFIGURED, PG_DUMP_VERSION_MISMATCH, safe_status
from services.telegram_notify import notify_background

logger = logging.getLogger(__name__)

# Counts dumps, not calendar time — so its meaning follows the schedule. The
# job runs daily (main.py), so this is 30 days of history. It was 8 back when
# the schedule was weekly; raising it is what keeps the switch to daily from
# silently shortening history from eight weeks to eight days. A dump is ~390 KB,
# so a month of them costs ~12 MB at the destination.
RETENTION = 30
BACKUP_PREFIX = "on-track-backups/"

# Conservative floor for "this dump is real, not truncated/empty." Even an
# essentially-empty custom-format pg_dump carries header/TOC overhead well
# past this; a file smaller than this means pg_dump exited 0 without actually
# writing a usable dump, and it must never be uploaded or (worse) allowed to
# trigger pruning of the last-known-good backups.
MIN_DUMP_BYTES = 512


class BackupDumpError(Exception):
    """Raised when pg_dump exits non-zero. Message is ours alone — never
    str(CalledProcessError), which embeds the full argv it was given."""


class BackupVersionMismatchError(BackupDumpError):
    """Raised specifically when pg_dump's stderr indicates it refused to dump
    because the server's Postgres major version is newer than pg_dump's own
    (nixpacks.toml pins the unversioned "postgresql" package, which tracks
    whatever major nixpkgs currently ships — it can drift behind Railway's
    Postgres plugin version). Kept distinct from the generic BackupDumpError
    so run() can record a diagnosable status instead of the generic
    "error: see logs"."""


class BackupTooSmallError(Exception):
    """Raised when the dump file is implausibly small for a real database dump."""


class BackupUnverifiedError(Exception):
    """Raised when the just-uploaded key doesn't show up in a post-upload
    listing — the upload can't be trusted, so pruning must not run."""


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


def _connection_params() -> dict:
    """Resolves the PostgreSQL connection details pg_dump needs, preferring
    Railway's discrete PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE (used only
    when ALL five are set) over parsing DATABASE_URL.

    Why: Railway provides those discrete vars already decoded, so there's
    nothing to unescape. DATABASE_URL's userinfo, by contrast, is never
    percent-decoded by urlparse() — a password containing "@", "%", or "/"
    (encoded as %40/%25/%2F in the URL) would otherwise reach pg_dump as the
    literal percent-encoded text and authentication would fail permanently.
    The fallback path applies unquote() to the username, password, and
    dbname to fix that.

    Returns a dict of {"host", "port", "user", "password", "dbname",
    "sslmode"} — each value stays a separate dict entry, exactly as pg_dump
    consumes it (as distinct flags / env vars). Never concatenates any of
    these into a single string; a credential-bearing string must never be
    constructed, even transiently."""
    if PGHOST and PGPORT and PGUSER and PGPASSWORD and PGDATABASE:
        return {
            "host": PGHOST,
            "port": PGPORT,
            "user": PGUSER,
            "password": PGPASSWORD,
            "dbname": PGDATABASE,
            "sslmode": PGSSLMODE or None,
        }

    parsed = urlparse(DATABASE_URL)
    sslmode = None
    if parsed.query:
        sslmode = parse_qs(parsed.query).get("sslmode", [None])[0]
    dbname = (parsed.path or "").lstrip("/")
    return {
        "host": parsed.hostname,
        "port": str(parsed.port) if parsed.port else None,
        "user": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password else None,
        "dbname": unquote(dbname) if dbname else None,
        "sslmode": sslmode,
    }


def _pg_dump_args() -> list:
    """Connection flags for pg_dump, from _connection_params(). Never
    includes the password — that goes through _pg_dump_env() instead. -w
    disables pg_dump's interactive password prompt: a missing/invalid
    password must fail fast with a clear error, not hang a scheduled job
    waiting on stdin."""
    params = _connection_params()
    args = ["pg_dump", "--format=custom", "-w"]
    if params["host"]:
        args += ["-h", params["host"]]
    if params["port"]:
        args += ["-p", str(params["port"])]
    if params["user"]:
        args += ["-U", params["user"]]
    if params["dbname"]:
        args += ["-d", params["dbname"]]
    return args


def _pg_dump_env() -> dict:
    """Subprocess environment for pg_dump: the real process env plus
    PGPASSWORD (and PGSSLMODE, when present) from _connection_params() — the
    only place the password goes. PGSSLMODE preserves DATABASE_URL's
    ?sslmode=... query param, which the -h/-p/-U/-d flags above otherwise
    silently drop (downgrading e.g. sslmode=require to libpq's default
    'prefer')."""
    env = os.environ.copy()
    params = _connection_params()
    if params["password"]:
        env["PGPASSWORD"] = params["password"]
    if params["sslmode"]:
        env["PGSSLMODE"] = params["sslmode"]
    return env


def _looks_like_pg_dump_version_mismatch(stderr_text: str) -> bool:
    """pg_dump refuses to dump from a server newer than itself and reports it
    via stderr mentioning both "server version" and "pg_dump version" — e.g.
    "pg_dump: error: aborting because of server version mismatch" plus a
    detail line "server version: 17.2; pg_dump version: 15.4". Only inspects
    vocabulary in pg_dump's own diagnostic text — never DATABASE_URL or a
    credential."""
    lower = stderr_text.lower()
    return "server version" in lower and "pg_dump version" in lower


def _pg_dump_client_version() -> str:
    """Best-effort diagnostic only — logged alongside a detected version
    mismatch so the client version is visible in the server logs without
    ever being stored in app_settings. Must never raise: a missing/broken
    pg_dump binary here must not crash the mismatch-detection path."""
    try:
        result = subprocess.run(["pg_dump", "--version"], capture_output=True, text=True)
        return (result.stdout or result.stderr or "").strip() or "unknown"
    except Exception:
        return "unknown"


def _dump_to_file(path: str) -> None:
    """Runs pg_dump against the resolved connection params, writing a
    custom-format dump to `path`. Raises BackupVersionMismatchError when
    stderr indicates a server-newer-than-client version mismatch, or the
    generic BackupDumpError otherwise on a non-zero exit — never
    subprocess.CalledProcessError, whose str() embeds the full argv."""
    with open(path, "wb") as f:
        result = subprocess.run(
            _pg_dump_args(), stdout=f, stderr=subprocess.PIPE, env=_pg_dump_env(),
        )
    if result.returncode != 0:
        stderr_text = result.stderr.decode("utf-8", "replace") if result.stderr else ""
        logger.error("pg_dump exited %d: %s", result.returncode, stderr_text[:2000])
        if _looks_like_pg_dump_version_mismatch(stderr_text):
            logger.error("pg_dump client version: %s", _pg_dump_client_version())
            raise BackupVersionMismatchError("pg_dump refused to dump: server version mismatch")
        raise BackupDumpError(f"pg_dump exited with status {result.returncode}")


def _assert_dump_is_plausible(path: str) -> None:
    size = os.path.getsize(path)
    if size < MIN_DUMP_BYTES:
        raise BackupTooSmallError(f"pg_dump output implausibly small ({size} bytes)")


def _upload(local_path: str, key: str) -> None:
    """Isolated so tests can assert it's called with the expected key without
    exercising a real S3-compatible client."""
    _s3_client().upload_file(local_path, BACKUP_S3_BUCKET, key)


def _verify_uploaded(key: str) -> bool:
    """Confirms `key` actually shows up in a post-upload listing. Isolated so
    tests can assert the prune-gating behavior without a real S3 client."""
    client = _s3_client()
    resp = client.list_objects_v2(Bucket=BACKUP_S3_BUCKET, Prefix=key)
    return any(obj["Key"] == key for obj in resp.get("Contents", []))


def _prune_old_backups() -> None:
    """Keeps only the most recent RETENTION dumps at the destination."""
    client = _s3_client()
    resp = client.list_objects_v2(Bucket=BACKUP_S3_BUCKET, Prefix=BACKUP_PREFIX)
    objects = sorted(resp.get("Contents", []), key=lambda o: o["Key"])
    for obj in objects[:-RETENTION] if len(objects) > RETENTION else []:
        client.delete_object(Bucket=BACKUP_S3_BUCKET, Key=obj["Key"])


def _set_status_and_alert(status: str) -> None:
    """Writes backup_last_status and notifies only when it CHANGES.

    The job runs daily, so alerting on every failing run would send a message
    every day for as long as the failure persists. Transitions are what carry
    information: ok -> error means something just broke, error -> ok means it
    just healed.

    The message is built only from `status`, which is always a closed-set
    value from services/safe_status.py -- never str(exception). A pg_dump or
    S3 failure can carry DATABASE_URL or the S3 credentials in its message,
    and a Telegram push is an outbound path like any other."""
    previous = db.get_setting("backup_last_status")
    db.set_setting("backup_last_status", status)
    if previous == status:
        return
    if status == "ok":
        notify_background("On Track: database backup recovered — latest run succeeded.")
    else:
        notify_background(
            f"On Track: database backup FAILED (status: {status}). "
            f"SimpleFIN only keeps 90 days, so a prolonged gap is unrecoverable."
        )


def run():
    if not _using_postgres():
        logger.info("Backup skipped: not using PostgreSQL (local SQLite dev)")
        return
    if not _is_configured():
        logger.warning("Backup skipped: BACKUP_S3_* env vars not fully set")
        _set_status_and_alert(NOT_CONFIGURED)
        return
    try:
        stamp = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y%m%dT%H%M%S")
        key = f"{BACKUP_PREFIX}{stamp}.dump"
        fd, tmp_path = tempfile.mkstemp(suffix=".dump")
        os.close(fd)
        try:
            _dump_to_file(tmp_path)
            _assert_dump_is_plausible(tmp_path)
            _upload(tmp_path, key)
            if not _verify_uploaded(key):
                raise BackupUnverifiedError(f"Uploaded key not found in post-upload listing: {key}")
            _prune_old_backups()
        finally:
            os.unlink(tmp_path)
        db.set_setting("backup_last_run", _now_iso())
        _set_status_and_alert("ok")
        logger.info("Backup uploaded: %s", key)
    except BackupVersionMismatchError as e:
        # Checked before the generic Exception handler below so this gets a
        # distinct, diagnosable status instead of falling through to
        # safe_status()'s generic "error: see logs".
        logger.exception("Backup failed: pg_dump version mismatch")
        db.set_setting("backup_last_run", _now_iso())
        _set_status_and_alert(PG_DUMP_VERSION_MISMATCH)
    except Exception as e:
        logger.exception("Backup failed")
        db.set_setting("backup_last_run", _now_iso())
        _set_status_and_alert(safe_status(e))
