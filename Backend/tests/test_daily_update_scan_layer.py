"""`scripts/daily_update.py::update_scan_layer` 接线单测(plan §五 V2-④)。

**只测接线,不测三张表本身的算法正确性**(那是 `tests/test_scan_*.py` 的活)——
本测试确认:①调用链真的按 cluster→corr→leader 顺序落了三张表;②无现役包时
只 WARNING、不抛异常;③真异常(如表被破坏)只 WARNING、不阻断(`daily_update.py`
「尽力而为」的既定纪律,同 `update_suspend_list`/`update_concept_boards` 两位
先例)。

**`daily_update.py` 本身向来没有单测**(`update_industry_strength` 等既有
`update_*` 函数也是——它们不接受 `db_path` 参数,只能靠 monkeypatch
`neckline.db` 模块级 `settings`〔`isolated_env` 不覆盖它,见项目 CLAUDE.md
「测试隔离」条〕才能安全指向隔离库,这里首次搭这套夹具,后续同类测试可复用)。
"""

from __future__ import annotations

import dataclasses
import logging
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import daily_update  # noqa: E402

import neckline.db as db_mod  # noqa: E402
from neckline.db import connection  # noqa: E402
from tests.conftest import insert_stock_basic, insert_trade_cal, write_daily_fixture  # noqa: E402

D0 = date(2024, 6, 3)


@pytest.fixture
def db_redirected(isolated_env, monkeypatch):
    """把 `neckline.db` 模块级 `settings` 也指向隔离库(`isolated_env` 默认不
    覆盖它),让不显式传 `db_path` 的 `update_scan_layer` 安全落在隔离库里。"""
    monkeypatch.setattr(db_mod, "settings", dataclasses.replace(db_mod.settings, db_path=isolated_env.db_path))
    return isolated_env


def _limit_row(code: str) -> dict:
    return {
        "ts_code": code, "board": "MAIN", "status": "limit_up",
        "limit_pct": 0.10, "limit_up_price": 11.0, "limit_down_price": 9.0,
        "is_limit_up": True, "is_limit_down": False, "is_zaban": False,
        "consec_limit_up_days": 1,
    }


def test_update_scan_layer_writes_all_three_tables_in_order(db_redirected, caplog):
    env = db_redirected
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

    with caplog.at_level(logging.INFO):
        daily_update.update_scan_layer(D0)

    with connection(env.db_path) as conn:
        n_cluster = conn.execute("SELECT COUNT(*) FROM limit_cluster_daily WHERE trade_date=?", ("20240603",)).fetchone()[0]
        n_leader = conn.execute("SELECT COUNT(*) FROM leader_structure_daily WHERE trade_date=?", ("20240603",)).fetchone()[0]
    assert n_cluster == 2
    assert n_leader == 2
    assert any("scan_layer" in r.message for r in caplog.records)


def test_update_scan_layer_warns_without_raising_when_no_active_pack(db_redirected, caplog):
    env = db_redirected
    insert_trade_cal(env, [D0])
    with caplog.at_level(logging.WARNING):
        daily_update.update_scan_layer(D0)   # 无涨停数据、无现役包,不应抛异常
    assert any("无现役策略包" in r.message for r in caplog.records)


def test_update_scan_layer_swallows_exceptions(db_redirected, monkeypatch, caplog):
    """真异常(如 cluster.py 内部炸了)只 WARNING,不向上抛(「尽力而为」纪律,
    不阻断 `daily_update.py` 主增量)。"""
    import neckline.facts.limitmap as cluster_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟数据管线故障")

    monkeypatch.setattr(cluster_mod, "refresh_limit_clusters", _boom)
    with caplog.at_level(logging.WARNING):
        daily_update.update_scan_layer(D0)   # 不应抛出
    assert any("日更异常" in r.message for r in caplog.records)
