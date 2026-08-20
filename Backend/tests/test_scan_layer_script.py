"""`scripts/scan_layer.py` CLI 单测(plan §五 V2-④,体例照
`tests/test_activate_pack_script.py`:把 `scripts/` 塞进 `sys.path`,直接
`import scan_layer` 当模块用,不经子进程;核心函数一律显式传 `db_path`/
`parquet_dir`,不碰真实 `data/`)。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scan_layer as scan_layer_script  # noqa: E402

from neckline.db import connection  # noqa: E402
from neckline.facts import limitmap as cluster  # noqa: E402
from tests.conftest import insert_stock_basic, insert_trade_cal, write_daily_fixture  # noqa: E402

D0 = date(2024, 6, 3)


def _limit_row(code: str, consec: int = 1) -> dict:
    return {
        "ts_code": code, "board": "MAIN", "status": "limit_up",
        "limit_pct": 0.10, "limit_up_price": 11.0, "limit_down_price": 9.0,
        "is_limit_up": True, "is_limit_down": False, "is_zaban": False,
        "consec_limit_up_days": consec,
    }


def _refresh_args(*, date_from=None, date_to=None, year=None, db=None, parquet_dir=None) -> SimpleNamespace:
    return SimpleNamespace(date_from=date_from, date_to=date_to, year=year, db=db, parquet_dir=parquet_dir)


def _verify_args(*, date_from=None, date_to=None, db=None) -> SimpleNamespace:
    return SimpleNamespace(date_from=date_from, date_to=date_to, db=db)


def test_resolve_days_defaults_to_today():
    args = _refresh_args()
    assert scan_layer_script.resolve_days(args) == [date.today()]


def test_resolve_days_from_to(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [date(2024, 6, 3), date(2024, 6, 4), date(2024, 6, 5)])
    args = _refresh_args(date_from="20240603", date_to="20240605")
    assert scan_layer_script.resolve_days(args) == [date(2024, 6, 3), date(2024, 6, 4), date(2024, 6, 5)]


def test_run_batch_end_to_end_writes_all_three_tables_and_returns_zero(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    insert_stock_basic(env, [
        {"ts_code": "600001.SH", "industry": "半导体"},
        {"ts_code": "600002.SH", "industry": "半导体"},
    ])
    write_daily_fixture(env, "limit_derived", D0, [_limit_row("600001.SH"), _limit_row("600002.SH")])
    write_daily_fixture(env, "daily", D0, [
        {"ts_code": "600001.SH", "open": 10, "high": 11, "low": 10, "close": 11, "pre_close": 10, "vol": 1.0, "amount": 100.0},
        {"ts_code": "600002.SH", "open": 10, "high": 11, "low": 10, "close": 11, "pre_close": 10, "vol": 1.0, "amount": 100.0},
    ])

    rc = scan_layer_script.run_batch([D0], db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert rc == 0

    with connection(env.db_path) as conn:
        n_cluster = conn.execute("SELECT COUNT(*) FROM limit_cluster_daily WHERE trade_date=?", ("20240603",)).fetchone()[0]
        n_leader = conn.execute("SELECT COUNT(*) FROM leader_structure_daily WHERE trade_date=?", ("20240603",)).fetchone()[0]
    assert n_cluster == 2   # 同日簇:600001+600002 共享半导体行业
    assert n_leader == 2


def test_run_batch_never_touches_real_project_db(isolated_env, monkeypatch):
    """`run_batch` 显式传 `db_path`/`parquet_dir`,不该落到真实项目库(§3.8「测试
    隔离」纪律)——把 `neckline.db` 模块级 `settings` 换成指向一个哨兵路径的
    替身(`Settings` 是 frozen dataclass,不能改单字段,照 conftest docstring
    "替身对象 + monkeypatch 模块级 settings 名字"的既定手法),确认显式传参
    路径下 `run_batch` 全程不会碰到那个哨兵路径。"""
    import dataclasses

    import neckline.db as db_mod

    poison_db = Path("/nonexistent/should-not-be-touched.db")
    poisoned_settings = dataclasses.replace(db_mod.settings, db_path=poison_db)
    monkeypatch.setattr(db_mod, "settings", poisoned_settings)

    env = isolated_env
    insert_trade_cal(env, [D0])
    rc = scan_layer_script.run_batch([D0], db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert rc == 0
    assert not poison_db.exists()


def test_cmd_verify_ok_on_clean_data(isolated_env):
    env = isolated_env
    insert_trade_cal(env, [D0])
    insert_stock_basic(env, [{"ts_code": "600001.SH", "industry": "半导体"}, {"ts_code": "600002.SH", "industry": "半导体"}])
    write_daily_fixture(env, "limit_derived", D0, [_limit_row("600001.SH"), _limit_row("600002.SH")])
    write_daily_fixture(env, "daily", D0, [
        {"ts_code": "600001.SH", "open": 10, "high": 11, "low": 10, "close": 11, "pre_close": 10, "vol": 1.0, "amount": 100.0},
        {"ts_code": "600002.SH", "open": 10, "high": 11, "low": 10, "close": 11, "pre_close": 10, "vol": 1.0, "amount": 100.0},
    ])
    scan_layer_script.run_batch([D0], db_path=env.db_path, parquet_dir=env.parquet_dir)

    rc = scan_layer_script.cmd_verify(_verify_args(date_from="20240603", date_to="20240603", db=env.db_path))
    assert rc == 0


def test_cmd_verify_catches_injected_key_inconsistency(isolated_env):
    """直接改库塞一个不自洽的 `cluster_size`,验证 verify 真的会抓到(不是摆设)。"""
    env = isolated_env
    insert_trade_cal(env, [D0])
    with connection(env.db_path) as conn:
        conn.execute(
            "INSERT INTO limit_cluster_daily "
            "(trade_date, cluster_key, ts_code, cluster_kind, cluster_size, consecutive_days, "
            " anchor_industry, anchor_concept, computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("20240603", "K1", "600001.SH", "same_day", 99, 1, "半导体", None, "2024-01-01T00:00:00+00:00"),
        )
    rc = scan_layer_script.cmd_verify(_verify_args(date_from="20240603", date_to="20240603", db=env.db_path))
    assert rc == 1


def test_cmd_verify_empty_db_reports_reason_not_crash(isolated_env):
    env = isolated_env
    rc = scan_layer_script.cmd_verify(_verify_args(db=env.db_path))
    assert rc == 1
