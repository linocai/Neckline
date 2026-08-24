"""P1-A: 晚间 K9 只能消费完整、已冻结的当日事实包。"""

from __future__ import annotations

import sqlite3
from datetime import date

from neckline.facts import readiness
from tests import k9_env


def test_preflight_accepts_a_complete_frozen_pack_without_writing(isolated_env):
    day = k9_env.seed(isolated_env)
    before = isolated_env.db_path.stat().st_mtime_ns

    result = readiness.preflight(
        day, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir
    )

    assert result.ready, result.gaps
    assert result.pack_id
    assert isolated_env.db_path.stat().st_mtime_ns == before


def test_preflight_rejects_missing_derived_partition(isolated_env):
    day = k9_env.seed(isolated_env)
    derived = isolated_env.parquet_dir / "limit_derived" / f"year={day.year}" / f"{day:%Y%m%d}.parquet"
    derived.unlink()

    result = readiness.preflight(
        day, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir
    )

    assert not result.ready
    assert any("limit_derived" in gap for gap in result.gaps)


def test_preflight_rejects_pack_when_frozen_parquet_was_removed(isolated_env):
    day = k9_env.seed(isolated_env)
    from neckline.facts import store

    pack = store.load_pack(day, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    pack.path.unlink()

    result = readiness.preflight(
        day, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir
    )

    assert not result.ready
    assert any("冻结事实包" in gap for gap in result.gaps)


def test_preflight_refuses_a_database_without_schema_instead_of_migrating(tmp_path):
    db_path = tmp_path / "unmigrated.db"
    sqlite3.connect(db_path).close()

    result = readiness.preflight(date(2026, 8, 21), db_path=db_path, parquet_dir=tmp_path / "parquet")

    assert not result.ready
    assert any("数据库" in gap for gap in result.gaps)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_evening_gate_does_not_run_k9_when_a_required_partition_is_missing(isolated_env, tmp_path):
    """数据缺口只能生成 not_run，不能触发策略、LLM 或沿用旧清单。"""
    from neckline.k9 import store as k9_store
    from neckline.report import evening

    day = k9_env.seed(isolated_env)
    (isolated_env.parquet_dir / "moneyflow_dc" / f"year={day.year}" / f"{day:%Y%m%d}.parquet").unlink()
    params = tmp_path / "params.json"
    params.write_text(__import__("json").dumps(k9_env.raw_params()), encoding="utf-8")

    result = evening.run_evening_chain(
        day, report_date=day, k9_params_path=params, db_path=isolated_env.db_path,
        parquet_dir=isolated_env.parquet_dir,
    )

    assert result.status["k9"] == evening.STATUS_EMPTY
    assert result.stats["k9"]["reason"] == "readiness_failed"
    assert result.bundle.state.value == "not_run"
    assert k9_store.load_run(day, db_path=isolated_env.db_path) is None
