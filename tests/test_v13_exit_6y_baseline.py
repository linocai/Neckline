"""v1.3-① 六年真回测基线实证(可选,数据缺失自动 skip;§五 v1.3-①-B 护栏「跑六年回测」)。

新增 `MomentumConfig.max_hold_days_profit`/`time_exit_only_if_unprofitable` 加载吃默认 → K1
现役 config 六年回测(2021-01-01~2026-07-17)必须逐位不变:**N=1288 / total_return −20.53%**。

依赖 `research/_cache/k3_panel.parquet`(1.9G,不入 CI)+ `research/lab.py` + 全量 parquet
backfill;任一缺失 → `pytest.skip`(CI 干净跳过,本地施工期实证)。同时验证两档启用后回测
条件退出分支确实被走到(硬上限豁免续命单出现),锚定 h9 V1 语义在**引擎口径**下活着。
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

# 六年真回测 ~90s(读 1.9G panel + 全量 qfq),照 IntegrationSmokeTests 惯例默认跳过——
# 施工期已手动实证(N=1288 / −20.53% 逐位吻合)。按需实证:
#     NECKLINE_RUN_6Y=1 python -m pytest tests/test_v13_exit_6y_baseline.py
# 数据缺失(CI / 新克隆无 1.9G k3_panel)同样跳过。逐位不变的**逻辑层**护栏见
# test_v13_exit_guardrail.py(始终运行,快)。
pytestmark = pytest.mark.skipif(
    not (os.environ.get("NECKLINE_RUN_6Y") and _K3_PANEL.exists() and _LAB.exists()),
    reason="六年真回测默认跳过(NECKLINE_RUN_6Y=1 且数据存在时实证;~90s / 1.9G 数据)",
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
