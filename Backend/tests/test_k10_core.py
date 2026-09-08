from __future__ import annotations

import sqlite3

import pytest

from neckline.k10 import schema
from neckline.k10.schema import SchemaUnavailable, initialize_schema, rollback_schema, schema_version


def _k10_tables(path):
    with sqlite3.connect(path) as conn:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'k10_%'"
        )}


def test_k10_schema_is_explicit_idempotent_and_rolls_back_without_touching_shared_tables(tmp_path):
    path = tmp_path / "isolated.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE shared_fixture(value TEXT)")

    assert initialize_schema(path) == 7
    first = _k10_tables(path)
    assert "k10_schema_migrations" in first
    assert "k10_tasks" in first
    assert initialize_schema(path) == 7
    assert _k10_tables(path) == first

    assert rollback_schema(path) == 0
    assert _k10_tables(path) == set()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='shared_fixture'").fetchone()


def test_k10_read_before_controlled_migration_never_creates_a_file(tmp_path):
    path = tmp_path / "never-created.db"
    with pytest.raises(SchemaUnavailable):
        schema_version(path)
    assert not path.exists()


def test_k10_read_rejects_existing_database_without_k10_migration(tmp_path):
    path = tmp_path / "other.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE unrelated(value TEXT)")
    with pytest.raises(SchemaUnavailable, match="未建立"):
        schema_version(path)


def test_failed_migration_leaves_no_partial_k10_schema_and_can_retry(tmp_path, monkeypatch):
    path = tmp_path / "failed.db"
    real_apply = schema._apply_v1

    def fail_after_one_table(conn):
        conn.execute("CREATE TABLE k10_partial_should_roll_back(value TEXT)")
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(schema, "_apply_v1", fail_after_one_table)
    with pytest.raises(RuntimeError, match="injected"):
        initialize_schema(path)
    assert _k10_tables(path) == set()

    monkeypatch.setattr(schema, "_apply_v1", real_apply)
    assert initialize_schema(path) == 7
    assert "k10_tasks" in _k10_tables(path)
