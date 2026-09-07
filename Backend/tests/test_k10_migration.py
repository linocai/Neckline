from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from neckline.k10 import migration
from neckline.k10.schema import schema_version


@pytest.fixture
def legacy(tmp_path: Path) -> Path:
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE llm_providers(id INTEGER PRIMARY KEY, name TEXT, api_key TEXT);
            INSERT INTO llm_providers VALUES(1,'test-connection','synthetic-key');
            CREATE TABLE devices(token TEXT PRIMARY KEY);
            INSERT INTO devices VALUES('synthetic-device');
            CREATE TABLE app_settings(id INTEGER PRIMARY KEY, tavily_api_key TEXT,
                review_col_map TEXT, llm_default_provider TEXT, llm_task_routes TEXT,
                push_kinds TEXT, updated_at TEXT);
            INSERT INTO app_settings VALUES(1,'synthetic-search-key','{}','old-provider','{}',
                '{"report_ready":false,"k10_analysis":false}', '2026-09-01');
            CREATE TABLE k9_selection_batches(batch_id TEXT PRIMARY KEY);
            CREATE TABLE k9_selection_candidates(batch_id TEXT REFERENCES k9_selection_batches(batch_id));
            INSERT INTO k9_selection_batches VALUES('legacy-batch');
            INSERT INTO k9_selection_candidates VALUES('legacy-batch');
            CREATE TABLE fact_packs(id TEXT PRIMARY KEY);
            CREATE TABLE reviews(week TEXT PRIMARY KEY, result_json TEXT);
            INSERT INTO reviews VALUES('2026-W01','{"privateLedger":true}');
        """)
    return path


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_cutover_preserves_connections_removes_retired_domain_and_restores(legacy, tmp_path):
    backup = tmp_path / "immutable-backup.sqlite"
    original_tables = _tables(legacy)
    receipt = migration.migrate_to_v3(target=legacy, confirmed_target=legacy, backup=backup, writers_stopped=True)
    assert schema_version(legacy) == 2
    assert {"k10_tasks", "k10_observations", "k10_opportunities", "k10_company_windows", "k10_publication_batches"} <= _tables(legacy)
    assert "k10_plan_revisions" not in _tables(legacy)
    assert not {"reviews", "fact_packs", "k9_selection_batches", "k9_selection_candidates"} & _tables(legacy)
    with sqlite3.connect(legacy) as conn:
        assert conn.execute("SELECT api_key FROM llm_providers").fetchone() == ("synthetic-key",)
        assert conn.execute("SELECT token FROM devices").fetchone() == ("synthetic-device",)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(app_settings)")}
        assert not {"review_col_map", "llm_default_provider", "llm_task_routes"} & columns
        assert conn.execute("SELECT tavily_api_key,push_kinds FROM app_settings").fetchone() == (
            "synthetic-search-key", '{"k10_analysis": false}',
        )
    assert _tables(backup) == original_tables
    assert receipt.backup_sha256 == migration.file_sha256(backup)
    migration.restore_backup(target=legacy, confirmed_target=legacy, backup=backup,
                             expected_sha256=receipt.backup_sha256, writers_stopped=True)
    assert _tables(legacy) == original_tables
    with sqlite3.connect(legacy) as conn:
        assert conn.execute("SELECT result_json FROM reviews").fetchone() == ('{"privateLedger":true}',)


def test_migration_error_keeps_original_and_verified_backup(legacy, tmp_path):
    before = migration.file_sha256(legacy)
    def fail(_):
        raise RuntimeError("synthetic failure")
    backup = tmp_path / "backup.sqlite"
    with pytest.raises(RuntimeError, match="synthetic"):
        migration.migrate_to_v3(target=legacy, confirmed_target=legacy, backup=backup,
                               writers_stopped=True, initialize_common=fail)
    assert migration.file_sha256(legacy) == before
    assert backup.exists()
    assert not list(tmp_path.glob(".k10-migrate-*"))


@pytest.mark.parametrize("blocker", ["not_stopped", "wrong_target", "wal", "backup_exists", "unknown_table"])
def test_cutover_refuses_unverified_boundary(legacy, tmp_path, blocker):
    backup = tmp_path / "backup.sqlite"
    confirmed = legacy
    if blocker == "wrong_target":
        confirmed = tmp_path / "other.sqlite"
        confirmed.touch()
    if blocker == "wal":
        Path(str(legacy) + "-wal").touch()
    if blocker == "backup_exists":
        backup.write_bytes(b"must not overwrite")
    if blocker == "unknown_table":
        with sqlite3.connect(legacy) as conn:
            conn.execute("CREATE TABLE unclassified_user_data(id INTEGER)")
    before = migration.file_sha256(legacy)
    with pytest.raises((ValueError, FileExistsError)):
        migration.migrate_to_v3(target=legacy, confirmed_target=confirmed, backup=backup,
                               writers_stopped=blocker != "not_stopped")
    assert migration.file_sha256(legacy) == before
    if blocker == "backup_exists":
        assert backup.read_bytes() == b"must not overwrite"


def test_restore_requires_exact_backup_hash(legacy, tmp_path):
    backup = tmp_path / "backup.sqlite"
    backup.write_bytes(legacy.read_bytes())
    before = migration.file_sha256(legacy)
    with pytest.raises(ValueError, match="哈希"):
        migration.restore_backup(target=legacy, confirmed_target=legacy, backup=backup,
                                 expected_sha256="not-the-backup", writers_stopped=True)
    assert migration.file_sha256(legacy) == before


def test_target_change_during_prepare_cannot_be_overwritten(legacy, tmp_path):
    def another_writer(_stage):
        from contextlib import closing
        with closing(sqlite3.connect(legacy)) as conn, conn:
            conn.execute("UPDATE devices SET token='new-live-write'")
    with pytest.raises(ValueError, match="迁移期间变化"):
        migration.migrate_to_v3(target=legacy, confirmed_target=legacy, backup=tmp_path / "backup.sqlite",
                               writers_stopped=True, initialize_common=another_writer)
    with sqlite3.connect(legacy) as conn:
        assert conn.execute("SELECT token FROM devices").fetchone() == ("new-live-write",)
    assert "reviews" in _tables(legacy)
