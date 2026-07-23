"""jobs/backup_db: weekly PostgreSQL backup to an off-Railway destination.

Never exercises a real S3-compatible client or a real pg_dump — the upload and
dump steps are isolated behind small functions the tests monkeypatch, per the
plan's testing note ("do not test the actual upload")."""


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

    monkeypatch.setattr(backup_db, "_dump_to_file", lambda path: open(path, "wb").write(b"dump"))
    upload_calls = []
    monkeypatch.setattr(backup_db, "_upload", lambda path, key: upload_calls.append(key))
    prune_calls = []
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: prune_calls.append(True))

    backup_db.run()

    assert len(upload_calls) == 1
    assert upload_calls[0].startswith("on-track-backups/")
    assert upload_calls[0].endswith(".dump")
    assert prune_calls == [True]
    assert db.get_setting("backup_last_status") == "ok"
    assert db.get_setting("backup_last_run") is not None


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
