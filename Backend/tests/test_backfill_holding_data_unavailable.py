"""`scripts/oneoff/backfill_holding_data_unavailable.py` 单测(v1.4-①-B 部署补丁,🔴)。

锁三件事:① 判据是**从 daily 分区推导**的(有行=0 / 无行=1),不是拍的;② **分区不可读
→ 留 NULL**(「不知道」不许冒充「知道」);③ 幂等 + 演练不写库。
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "oneoff"))

from backfill_holding_data_unavailable import backfill  # noqa: E402

from tests.conftest import write_daily_fixture


def _seed(settings, *, with_partition_for=("20260727", "20260728")):
    """两只持仓:`600001.SH` 每天都有 EOD 行;`002036.SZ` 停牌、一行都没有。
    `holding_eod_check` 各两行,`data_unavailable` 全 NULL(模拟旧代码写的快照)。"""
    from neckline.db import init_schema

    db = settings.db_path
    init_schema(db_path=db)
    for td in with_partition_for:
        d = date(int(td[:4]), int(td[4:6]), int(td[6:]))
        write_daily_fixture(settings, "daily", d, [
            {"ts_code": "600001.SH", "close": 10.0, "open": 10.0, "high": 10.0,
             "low": 10.0, "pre_close": 10.0, "vol": 1.0, "amount": 1.0},
        ])
    conn = sqlite3.connect(str(db))
    try:
        for pid, code in ((1, "600001.SH"), (2, "002036.SZ")):
            conn.execute(
                "INSERT INTO positions (id, ts_code, buy_price, qty, buy_date, status, "
                "created_at, updated_at) VALUES (?,?,?,?,?, 'open', ?, ?)",
                (pid, code, 10.0, 100, "20260722", "2026-07-27T06:00:00+00:00",
                 "2026-07-27T06:00:00+00:00"),
            )
            for td in ("20260727", "20260728"):
                conn.execute(
                    "INSERT INTO holding_eod_check (position_id, trade_date, d_count, net_float, "
                    "time_exit_state, max_hold_effective, k4_hits_json, has_strong, scenario_review, "
                    "created_at) VALUES (?,?,?,?, 'holding', 5, '[]', 0, 0, ?)",
                    (pid, td, 4, (100.0 if code == "600001.SH" else None),
                     "2026-07-28T08:00:00+00:00"),
                )
        conn.commit()
    finally:
        conn.close()
    return db


def _read(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_derives_from_daily_partition(isolated_env):
    """有 EOD 行 → 0;无 EOD 行 → 1。**判据来自分区实况**,不看 net_float 是否为空
    (net_float 为空还有别的原因,不能拿它当停牌的证据)。"""
    db = _seed(isolated_env)
    rep = backfill(db, isolated_env.parquet_dir, confirm=True)
    assert rep.integrity == "ok" and len(rep.filled) == 4
    got = dict(_read(db, "SELECT position_id || '@' || trade_date, data_unavailable "
                         "FROM holding_eod_check ORDER BY position_id, trade_date"))
    assert got == {"1@20260727": 0, "1@20260728": 0, "2@20260727": 1, "2@20260728": 1}


def test_missing_partition_leaves_null(isolated_env):
    """该日 daily 分区不存在 → **留 NULL**,不推导(「不知道」不许冒充「知道」)。"""
    db = _seed(isolated_env, with_partition_for=("20260727",))
    rep = backfill(db, isolated_env.parquet_dir, confirm=True)
    assert len(rep.skipped_no_partition) == 2          # 20260728 的两行
    got = dict(_read(db, "SELECT position_id || '@' || trade_date, data_unavailable "
                         "FROM holding_eod_check ORDER BY position_id, trade_date"))
    assert got["1@20260728"] is None and got["2@20260728"] is None
    assert got["1@20260727"] == 0 and got["2@20260727"] == 1


def test_dry_run_writes_nothing(isolated_env):
    db = _seed(isolated_env)
    before = _read(db, "SELECT * FROM holding_eod_check ORDER BY position_id, trade_date")
    rep = backfill(db, isolated_env.parquet_dir, confirm=False)
    assert rep.dry_run is True and len(rep.filled) == 4
    assert _read(db, "SELECT * FROM holding_eod_check ORDER BY position_id, trade_date") == before


def test_idempotent(isolated_env):
    """已有值的行不再碰(二次运行零改动)。"""
    db = _seed(isolated_env)
    backfill(db, isolated_env.parquet_dir, confirm=True)
    before = _read(db, "SELECT * FROM holding_eod_check ORDER BY position_id, trade_date")
    rep2 = backfill(db, isolated_env.parquet_dir, confirm=True)
    assert rep2.filled == [] and rep2.already_set == 4
    assert _read(db, "SELECT * FROM holding_eod_check ORDER BY position_id, trade_date") == before


def test_provider_flips_after_backfill(isolated_env):
    """端到端:回填后 `data_unavailable_provider` 对停牌票返回 True —— 这正是盘前
    `scan_time_exits` 决定「推不推」所依赖的那一位。"""
    from neckline.report.holding_store import data_unavailable_provider
    from neckline.sentinel import positions as pos_store

    db = _seed(isolated_env)
    pos = {p.ts_code: p for p in pos_store.load_open_positions(db_path=db)}
    assert data_unavailable_provider(db_path=db)(pos["002036.SZ"]) is False   # 回填前:保守 False
    backfill(db, isolated_env.parquet_dir, confirm=True)
    provider = data_unavailable_provider(db_path=db)
    assert provider(pos["002036.SZ"]) is True
    assert provider(pos["600001.SH"]) is False
