"""Pre-B92 import is retired; only new-generation rollback remains executable."""
from pathlib import Path
from contextlib import closing
import shutil
import sqlite3
import pytest
from neckline.fresh_start import initialize_fresh_database, RetiredDataError
from neckline.k10 import migration


def test_legacy_migration_command_cannot_open_old_database(tmp_path, monkeypatch):
    old = tmp_path / "old.sqlite"
    old.write_bytes(b"retired, must not read")
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("old database opened"))
    with pytest.raises(RetiredDataError):
        migration.main(["migrate", "--db", str(old), "--confirmed-target", str(old),
                        "--backup", str(tmp_path / "copy"), "--writers-stopped"])
    assert not (tmp_path / "copy").exists()


def fresh_pair(tmp_path):
    target = tmp_path / "new.sqlite"
    initialize_fresh_database(target=target)
    backup = tmp_path / "new-backup.sqlite"
    shutil.copyfile(target, backup)
    return target, backup


def test_new_generation_restore(tmp_path):
    target, backup = fresh_pair(tmp_path)
    digest = migration.file_sha256(backup)
    migration.restore_backup(target=target, confirmed_target=target, backup=backup,
                             expected_sha256=digest, writers_stopped=True)
    assert migration.file_sha256(target) == digest
    assert not list(tmp_path.glob(".k10-restore-*"))


@pytest.mark.parametrize("blocker", ["not_stopped", "wrong_target", "wal", "wrong_hash"])
def test_restore_refuses_unverified_boundary(tmp_path, blocker):
    target, backup = fresh_pair(tmp_path)
    confirmed = target
    digest = migration.file_sha256(backup)
    if blocker == "wrong_target":
        confirmed = backup
    if blocker == "wal":
        Path(str(target) + "-wal").touch()
    before = migration.file_sha256(target)
    with pytest.raises(ValueError):
        migration.restore_backup(target=target, confirmed_target=confirmed, backup=backup,
                                 expected_sha256="bad" if blocker == "wrong_hash" else digest,
                                 writers_stopped=blocker != "not_stopped")
    assert migration.file_sha256(target) == before


def test_restore_preserves_target_changed_during_copy(tmp_path, monkeypatch):
    target, backup = fresh_pair(tmp_path)
    real_copy = migration.shutil.copyfile
    def concurrent_write(source, stage):
        real_copy(source, stage)
        with closing(sqlite3.connect(target)) as conn, conn:
            conn.execute("INSERT INTO devices VALUES ('fresh-device','ios','now','now')")
    monkeypatch.setattr(migration.shutil, "copyfile", concurrent_write)
    with pytest.raises(ValueError, match="恢复期间变化"):
        migration.restore_backup(target=target, confirmed_target=target, backup=backup,
            expected_sha256=migration.file_sha256(backup), writers_stopped=True)
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT token FROM devices").fetchone()[0] == "fresh-device"
    assert not list(tmp_path.glob(".k10-restore-*"))
