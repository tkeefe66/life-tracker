"""jobs/backup_db: weekly PostgreSQL backup to an off-Railway destination.

Never exercises a real S3-compatible client or a real pg_dump — the upload and
dump steps are isolated behind small functions the tests monkeypatch, per the
plan's testing note ("do not test the actual upload")."""
import pytest


def test_backup_skipped_when_sqlite(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    called = []
    monkeypatch.setattr(backup_db, "_upload", lambda *a, **k: called.append(a))
    assert db.USE_POSTGRES is False  # temp_db_path fixture forces SQLite

    backup_db.run()

    assert called == []
    assert db.get_setting("backup_last_status") is None  # never even attempted


def test_backup_skipped_when_unconfigured(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "BACKUP_S3_BUCKET", "")
    called = []
    monkeypatch.setattr(backup_db, "_upload", lambda *a, **k: called.append(a))

    backup_db.run()  # must not raise

    assert called == []
    assert db.get_setting("backup_last_status") == "error: not configured"


def test_backup_uploads_with_expected_key_and_prunes(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "BACKUP_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ACCESS_KEY", "key")
    monkeypatch.setattr(backup_db, "BACKUP_S3_SECRET_KEY", "secret")

    plausible_dump = b"x" * (backup_db.MIN_DUMP_BYTES + 1)
    monkeypatch.setattr(backup_db, "_dump_to_file", lambda path: open(path, "wb").write(plausible_dump))
    upload_calls = []
    monkeypatch.setattr(backup_db, "_upload", lambda path, key: upload_calls.append(key))
    monkeypatch.setattr(backup_db, "_verify_uploaded", lambda key: True)
    prune_calls = []
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: prune_calls.append(True))

    backup_db.run()

    assert len(upload_calls) == 1
    assert upload_calls[0].startswith("on-track-backups/")
    assert upload_calls[0].endswith(".dump")
    assert prune_calls == [True]
    assert db.get_setting("backup_last_status") == "ok"
    assert db.get_setting("backup_last_run") is not None


def test_backup_refuses_to_upload_an_implausibly_small_dump(temp_db_path, monkeypatch):
    """A pg_dump that exits 0 but produces a truncated/empty file must not be
    uploaded — and, critically, must not cause _prune_old_backups to run and
    delete the last-known-good backups on the strength of a bad one."""
    import database as db
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "BACKUP_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ACCESS_KEY", "key")
    monkeypatch.setattr(backup_db, "BACKUP_S3_SECRET_KEY", "secret")

    monkeypatch.setattr(backup_db, "_dump_to_file", lambda path: open(path, "wb").write(b"truncated"))
    upload_calls = []
    monkeypatch.setattr(backup_db, "_upload", lambda path, key: upload_calls.append(key))
    prune_calls = []
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: prune_calls.append(True))

    backup_db.run()  # must not raise

    assert upload_calls == []
    assert prune_calls == []
    assert db.get_setting("backup_last_status") == "error: see logs"


def test_backup_does_not_prune_unless_upload_is_confirmed_in_listing(temp_db_path, monkeypatch):
    """If the just-uploaded key doesn't show up in a post-upload listing, treat
    the upload as unverified and skip pruning — never delete good backups on
    the strength of an upload we can't confirm actually landed."""
    import database as db
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "BACKUP_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ACCESS_KEY", "key")
    monkeypatch.setattr(backup_db, "BACKUP_S3_SECRET_KEY", "secret")

    plausible_dump = b"x" * (backup_db.MIN_DUMP_BYTES + 1)
    monkeypatch.setattr(backup_db, "_dump_to_file", lambda path: open(path, "wb").write(plausible_dump))
    monkeypatch.setattr(backup_db, "_upload", lambda path, key: None)
    monkeypatch.setattr(backup_db, "_verify_uploaded", lambda key: False)
    prune_calls = []
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: prune_calls.append(True))

    backup_db.run()  # must not raise

    assert prune_calls == []
    assert db.get_setting("backup_last_status") == "error: see logs"


def test_backup_records_safe_status_on_failure(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "BACKUP_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ACCESS_KEY", "key")
    monkeypatch.setattr(backup_db, "BACKUP_S3_SECRET_KEY", "secret")

    def boom(path):
        # Mimics a real pg_dump failure whose message could carry a connection
        # string with a password — the job must never store this raw.
        raise RuntimeError("connection to server at postgres://user:hunter2@host failed")
    monkeypatch.setattr(backup_db, "_dump_to_file", boom)

    backup_db.run()  # must not raise

    status = db.get_setting("backup_last_status")
    assert status == "error: see logs"
    assert "hunter2" not in status
    assert "postgres://" not in status


# ── H2: pg_dump must never see the credential-bearing URL in argv ────────────

def test_pg_dump_invocation_never_puts_the_password_in_argv(tmp_path, monkeypatch):
    """DATABASE_URL embeds the password. Passing it to pg_dump as a positional
    argument puts the password in /proc/<pid>/cmdline and `ps` for any other
    process on the box to read. pg_dump must be invoked with -h/-p/-U/-d flags
    instead, and the password supplied only via the subprocess env (PGPASSWORD)
    — never as an argv element, and never via check=True (whose CalledProcessError
    embeds the full argv it was given)."""
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "DATABASE_URL", "postgresql://dbuser:hunter2@dbhost.example.com:5432/mydb")

    captured = {}

    class _FakeCompletedProcess:
        returncode = 0
        stderr = b""

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeCompletedProcess()

    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)

    dump_path = str(tmp_path / "out.dump")
    backup_db._dump_to_file(dump_path)

    args = captured["args"]
    assert "hunter2" not in args
    assert not any("hunter2" in str(a) for a in args)
    assert "postgresql://" not in " ".join(str(a) for a in args)
    assert "check" not in captured["kwargs"]  # never check=True — see docstring above

    # The password must reach pg_dump only through the environment.
    env = captured["kwargs"].get("env")
    assert env is not None
    assert env.get("PGPASSWORD") == "hunter2"

    # And the connection details pg_dump needs are passed as explicit flags.
    assert "-h" in args and args[args.index("-h") + 1] == "dbhost.example.com"
    assert "-p" in args and args[args.index("-p") + 1] == "5432"
    assert "-U" in args and args[args.index("-U") + 1] == "dbuser"
    assert "-d" in args and args[args.index("-d") + 1] == "mydb"


def test_pg_dump_failure_without_check_true_is_raised_from_our_own_exception(tmp_path, monkeypatch):
    """With check=True removed, a non-zero exit must still surface as a raised
    exception (so run()'s try/except and safe_status() still catch it) — but
    one we construct ourselves, never one built from a library that embeds argv."""
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "DATABASE_URL", "postgresql://dbuser:hunter2@dbhost.example.com:5432/mydb")

    class _FakeCompletedProcess:
        returncode = 1
        stderr = b"pg_dump: error: some failure"

    monkeypatch.setattr(backup_db.subprocess, "run", lambda *a, **k: _FakeCompletedProcess())

    dump_path = str(tmp_path / "out.dump")
    with pytest.raises(Exception) as exc_info:
        backup_db._dump_to_file(dump_path)
    assert "hunter2" not in str(exc_info.value)
