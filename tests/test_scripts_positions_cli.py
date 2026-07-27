"""持仓台账 CLI 单测(`scripts/positions.py`)。

本文件因 2026-07-27 独立审计 🔵-6 而建:**CLI 清仓此前不经熔断评估** —— 熔断评估只挂在
API 端点 `POST /positions/{id}/close`,用 CLI 补录的第 3 笔止损不会当场触发熔断,要等下一次
API 清仓才被尾链带出;而运维/应急场景恰恰常用 CLI。锁死双向:①三笔止损经 CLI 补录 → 当场
触发熔断;②不到阈值 → 不触发;③熔断评估异常被吞、绝不影响「清仓已记账」这个事实;
④`--reason` 白名单(argparse choices)与 store 层白名单防线互补。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import positions as cli  # noqa: E402
from neckline.sentinel import circuit  # noqa: E402
from neckline.sentinel.positions import (  # noqa: E402
    CLOSE_REASON_CODES,
    CLOSE_REASON_MANUAL,
    STATUS_CLOSED,
    get_position,
    open_position,
)
from tests.conftest import seed_active_rule_v1  # noqa: E402


@pytest.fixture
def cli_env(isolated_env, monkeypatch):
    """把 `neckline.db` 的模块级 settings 换成隔离替身 —— CLI 走默认 db_path(不带
    db_path 参数),故必须从这一层拦截,才不会碰真实 `data/neckline.db`。"""
    import neckline.db as db_mod

    monkeypatch.setattr(db_mod, "settings", isolated_env)
    seed_active_rule_v1(isolated_env)
    return isolated_env


def _args(pid: int, price: float, day: str, reason=None) -> argparse.Namespace:
    return argparse.Namespace(position_id=pid, sell_price=price, sell_date=day, close_reason=reason)


def _open_and_close(env, code: str, sell_price: float, reason=None) -> int:
    pid = open_position(code, 10.0, 100, date(2026, 7, 20), db_path=env.db_path)
    assert cli.cmd_close(_args(pid, sell_price, "20260722", reason)) == 0
    return pid


def test_cli_close_records_and_evaluates_circuit(cli_env):
    """审计 🔵-6:三笔 −6% 止损经 **CLI** 补录 → 第三笔当场触发熔断(不必等下次 API 清仓)。"""
    for i in range(2):
        _open_and_close(cli_env, f"60000{i}.SH", 9.4)
        assert circuit.is_locked(db_path=cli_env.db_path) is False   # 前两笔不触发
    _open_and_close(cli_env, "600002.SH", 9.4)
    ep = circuit.current_locked_episode(db_path=cli_env.db_path)
    assert ep is not None and ep.trigger_reason == circuit.TRIGGER_CONSECUTIVE_STOPS


def test_cli_close_no_circuit_when_below_threshold(cli_env):
    """阴性方向:两笔止损 + 一笔主动离场(断链)→ 不触发熔断(CLI 不制造假熔断)。"""
    _open_and_close(cli_env, "600000.SH", 9.4)
    _open_and_close(cli_env, "600001.SH", 9.4)
    _open_and_close(cli_env, "600002.SH", 10.5, reason=CLOSE_REASON_MANUAL)
    assert circuit.is_locked(db_path=cli_env.db_path) is False


def test_cli_close_survives_circuit_failure(cli_env, monkeypatch):
    """熔断评估异常必须被吞:清仓**已记账**这个事实不受影响(§3.8 只记账,记账优先)。"""
    def _boom(*a, **k):
        raise RuntimeError("circuit boom")

    monkeypatch.setattr(circuit, "evaluate_after_close", _boom)
    pid = open_position("600009.SH", 10.0, 100, date(2026, 7, 20), db_path=cli_env.db_path)
    assert cli.cmd_close(_args(pid, 9.4, "20260722")) == 0          # 退出码仍 0
    assert get_position(pid, db_path=cli_env.db_path).status == STATUS_CLOSED


def test_cli_close_reason_passthrough(cli_env):
    """`--reason` 合法码原样落库(与 store 层「信用户标注」语义一致)。"""
    pid = _open_and_close(cli_env, "600007.SH", 10.6, reason=CLOSE_REASON_MANUAL)
    assert get_position(pid, db_path=cli_env.db_path).close_reason == CLOSE_REASON_MANUAL


def test_cli_reason_choices_reject_illegal_code(cli_env, monkeypatch, capsys):
    """CLI 侧写不出非法码:`--reason stop_loss`(小写)被 argparse choices 直接挡下,
    非零退出。choices 取自 `CLOSE_REASON_CODES` 单一源,不是抄的字面量。"""
    monkeypatch.setattr(sys, "argv",
                        ["positions.py", "close", "1", "9.0", "20260722", "--reason", "stop_loss"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code != 0
    assert "STOP_LOSS" in capsys.readouterr().err          # 报错里列出合法白名单
    assert "stop_loss" not in CLOSE_REASON_CODES
