"""v1.3-① 六年真回测基线的**冻结数字载体**(§七 P4-54,2026-08-09 起如实改口)。

🔴 **这条基线在本仓已永久无法复跑**:它依赖的 `research/lab.py` 与
`research/_cache/k3_panel.parquet`(1.9G 面板,属 5.5G `research/_cache/` 缓存)已随
2026-08-09 策略研究档案整体迁出/删除(**面板已删且不可回滚**,见 PROJECT_PLAN §1.6)。
下面的 skipif 因此**恒真跳过** —— 这不是"数据暂缺",是结构性缺席。

**冻结基线数字(本文件是它在本仓的唯一载体,⛔ 别删这个文件)**:K1 现役 config
(两档时间退出字段吃默认)六年回测 2021-01-01~2026-07-17 = **N=1288 笔 /
total_return −20.53%**(施工期逐位实证,精确断言在 `test_k1_six_year_backtest_
bit_identical`)。「六年真回测逐位不变」这一层护栏自迁出起只剩:①
`test_v13_exit_guardrail.py` 的逻辑层护栏(始终运行,不依赖 research/);② 本文件
记载的冻结数字文字。

**怎么重建才能复跑**(档案 README 记了完整办法,耗时以小时计):
  1. 从档案根 `~/Lino/whynotme/Archive/Neckline量化研究档案_K2-K7/research/` 拷回
     `lab.py`(及其依赖的研究件)到本仓顶层 `research/`;
  2. 用本仓仍在的 `neckline/research/panel.py::load_or_build_panel`(回测面板引擎
     没搬走)+ 全量 parquet backfill 重建 `research/_cache/k3_panel.parquet`;
  3. `NECKLINE_RUN_6Y=1 python -m pytest tests/test_v13_exit_6y_baseline.py`。
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_K3_PANEL = _ROOT / "research" / "_cache" / "k3_panel.parquet"
_LAB = _ROOT / "research" / "lab.py"

# ⚠ 2026-08-09 起 `_K3_PANEL`/`_LAB` 两个路径在本仓**结构性不存在**(研究档案迁出,
# §七 P4-54)→ 本 skipif 恒真、全文件恒跳过;理由如实写在下面 reason 里,⛔ 不再说
# "数据缺失/默认跳过"那种听起来像暂时状态的话。重建复跑的办法见模块头。
pytestmark = pytest.mark.skipif(
    not (os.environ.get("NECKLINE_RUN_6Y") and _K3_PANEL.exists() and _LAB.exists()),
    reason=(
        "六年真回测基线(N=1288 / total_return −20.53%)在本仓已永久无法复跑:"
        "research/lab.py 与 1.9G k3_panel 已随 2026-08-09 策略档案迁出且面板不可回滚"
        "(§1.6 / §七 P4-54)。本文件保留为冻结数字的载体,重建复跑办法见模块头。"
    ),
)

_RUN_START = date(2021, 1, 1)
_FROZEN_END = date(2026, 7, 17)


def _load():
    sys.path.insert(0, str(_ROOT / "research"))
    sys.path.insert(0, str(_ROOT))
    import polars as pl
    import lab  # noqa: E402
    from neckline.strategy import brain
    from neckline.strategy.momentum import MomentumConfig
    panel = pl.read_parquet(_K3_PANEL).filter(pl.col("trade_date") <= _FROZEN_END)
    return lab, brain, MomentumConfig, panel


def test_k1_six_year_backtest_bit_identical(real_db_readonly_copy):
    """现役 K1 config(两新字段吃默认)六年回测 = N=1288 / total_return −20.53%(冻结基线)。

    §七 P4-25(v1.5-④-A4):`db_path` 显式传真库只读副本(见
    `conftest.py::real_db_readonly_copy`)——不裸调 `brain.active_config()`,
    避免命中 `neckline/db.py` 模块级 settings、直接读写真实开发库。"""
    lab, brain, MomentumConfig, panel = _load()
    cfg = MomentumConfig(**brain.active_config(db_path=real_db_readonly_copy))
    assert cfg.time_exit_only_if_unprofitable is False and cfg.max_hold_days_profit is None
    rep, _pf = lab.run_pf(cfg, _RUN_START, _FROZEN_END, panel=panel)
    assert rep.n_trades == 1288
    assert abs(rep.total_return - (-0.205321)) < 1e-4     # −20.53%,逐位吻合


def test_two_tier_engine_exercises_exempt_branch(real_db_readonly_copy):
    """两档启用(回落 8% + 浮盈豁免硬上限 15)六年回测:硬上限豁免续命单确实出现(分支活着)。"""
    lab, brain, MomentumConfig, panel = _load()
    v13 = dict(brain.active_config(db_path=real_db_readonly_copy))
    v13.update(take_profit_retrace=0.08, time_exit_only_if_unprofitable=True, max_hold_days_profit=15)
    _rep, pf = lab.run_pf(MomentumConfig(**v13), _RUN_START, _FROZEN_END, panel=panel)
    hard_cap = sum(1 for t in pf.closed_trades if "硬上限" in t.reason)
    assert hard_cap > 0                                   # 浮盈豁免续命到硬上限的单出现
