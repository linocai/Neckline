"""库结构 v6：v1～v5 升上来之后三张 KX 表消失、股票池和板块表在，其他表的数据还在；升级失败整体回滚。"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pytest

from neckline.morning import db
from tests.kx_support import KX_TABLES, make_legacy_db

_SECTOR_TABLES = {"sector_concepts", "sector_members", "sector_concepts_meta", "market_daily"}
_V6_TABLES = {"stock_pool", "stock_pool_meta", "sector_events"} | _SECTOR_TABLES


def _version(path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def _names(path) -> set[str]:
    with closing(sqlite3.connect(path)) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _structure(path) -> list[tuple]:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name").fetchall()


def _expect_refused(path) -> None:
    with pytest.raises(db.NotReady):
        with db.read(path):
            pass
    with pytest.raises(db.NotReady):
        with db.write(path):
            pass


def _legacy(path, version: int) -> None:
    if version < 5:
        make_legacy_db(path, version)
        return
    make_legacy_db(path, 4)
    with closing(sqlite3.connect(path)) as conn:
        for name in KX_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {name}")
        conn.execute("PRAGMA user_version=5")
        conn.commit()


def test_schema_version_is_six():
    assert db.SCHEMA_VERSION == 6


def test_a_fresh_database_has_no_kx_tables(tmp_path):
    path = tmp_path / "fresh.db"
    db.init_schema(path)
    with db.read(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert conn.execute("SELECT count(*) FROM filing_reads").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM evaluations").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM stock_pool").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM stock_pool_meta").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM sector_concepts").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM market_daily").fetchone()[0] == 0
    assert KX_TABLES.isdisjoint(_names(path))
    assert _V6_TABLES <= _names(path)


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5])
def test_each_older_database_upgrades_to_v6_and_keeps_other_rows(tmp_path, version):
    path = tmp_path / f"v{version}.db"
    _legacy(path, version)
    _expect_refused(path)
    if 2 <= version <= 4:
        assert set(KX_TABLES) <= _names(path)
    if version == 5:
        assert KX_TABLES.isdisjoint(_names(path))
        assert "stock_pool" not in _names(path)

    db.init_schema(path)
    assert _version(path) == 6
    assert KX_TABLES.isdisjoint(_names(path))
    assert _V6_TABLES <= _names(path)
    with db.read(path) as conn:
        assert conn.execute("SELECT token FROM devices").fetchone()[0] == "abcd"
        assert db.latest_report(conn)["report_id"] == "r1"
        assert db.latest_report(conn)["content"] == {"entries": []}
        if version >= 4:
            assert conn.execute("SELECT count(*) FROM filing_reads").fetchone()[0] == 1
        else:
            assert conn.execute("SELECT count(*) FROM filing_reads").fetchone()[0] == 0
    with db.write(path) as conn:
        db.upsert_device(conn, token="ef01", platform="ios", at=datetime(2026, 10, 20, tzinfo=timezone.utc))
    db.init_schema(path)  # 已经是 v6：什么都不改
    with db.read(path) as conn:
        assert db.device_tokens(conn) == ["abcd", "ef01"]


def test_every_upgrade_path_matches_a_fresh_database(tmp_path):
    fresh = tmp_path / "fresh.db"
    db.init_schema(fresh)
    upgraded = []
    for version in (1, 2, 3, 4, 5):
        path = tmp_path / f"v{version}.db"
        _legacy(path, version)
        db.init_schema(path)
        upgraded.append(path)
    assert all(_structure(path) == _structure(fresh) for path in upgraded)
    assert all(_version(path) == 6 for path in upgraded)


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_a_failed_upgrade_rolls_back_as_a_whole(tmp_path, monkeypatch, version):
    path = tmp_path / "legacy.db"
    make_legacy_db(path, version)
    before_names = _names(path)
    before_version = _version(path)
    monkeypatch.setattr(db, "V4_DDL", db.V4_DDL + "\nTHIS IS NOT SQL;")
    with pytest.raises(sqlite3.OperationalError):
        db.init_schema(path)
    assert _version(path) == before_version
    assert _names(path) == before_names
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT count(*) FROM devices").fetchone()[0] == 1
        if version >= 2:
            assert conn.execute("SELECT count(*) FROM kx_pool").fetchone()[0] == 2
            assert conn.execute("SELECT count(*) FROM kx_picks").fetchone()[0] == 1
        if version == 1:
            assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'kx_%'").fetchall()
            assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='filing_reads'").fetchall()
    monkeypatch.undo()
    db.init_schema(path)
    assert _version(path) == 6 and KX_TABLES.isdisjoint(_names(path))
    assert _V6_TABLES <= _names(path)


def test_a_failed_v5_upgrade_rolls_back_without_adding_pool_tables(tmp_path, monkeypatch):
    path = tmp_path / "v5.db"
    _legacy(path, 5)
    before_names = _names(path)
    monkeypatch.setattr(db, "V6_DDL", db.V6_DDL + "\nTHIS IS NOT SQL;")
    with pytest.raises(sqlite3.OperationalError):
        db.init_schema(path)
    assert _version(path) == 5
    assert _names(path) == before_names
    assert "stock_pool" not in _names(path)
    assert "sector_events" not in _names(path)
    assert _SECTOR_TABLES.isdisjoint(_names(path))
    with pytest.raises(db.NotReady):
        with db.read(path):
            pass


@pytest.mark.parametrize("version", [0, 7])
def test_init_schema_refuses_other_versions(tmp_path, version):
    path = tmp_path / "x.db"
    conn = sqlite3.connect(path)
    conn.executescript(db.DDL)
    conn.execute(f"PRAGMA application_id={db.APPLICATION_ID}")
    conn.execute(f"PRAGMA user_version={version}")
    conn.commit()
    conn.close()
    with pytest.raises(db.NotReady):
        db.init_schema(path)
    assert _version(path) == version


def test_init_schema_refuses_a_foreign_application_id(tmp_path):
    path = tmp_path / "x.db"
    conn = sqlite3.connect(path)
    conn.executescript(db.DDL)
    conn.execute("PRAGMA application_id=1")
    conn.execute("PRAGMA user_version=4")
    conn.commit()
    conn.close()
    with pytest.raises(db.NotReady):
        db.init_schema(path)
