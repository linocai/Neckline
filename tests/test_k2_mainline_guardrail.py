"""K2-B4.0 护栏:K1 现役大脑「逐位不变」单测(承 test_report_consistency / test_momentum
姿势)。K2 研究给 `build_entry_mask` 新增可选字段 `require_mainline_member`(主线成员
mask)、给 `MomentumConfig` 新增 `take_profit_fixed`(B5 固定止盈)——**新增字段一律
默认关闭,默认 = 与 K1 逐位相同**(§五B B4.0/B5.1 边界铁律)。

本文件锁死:① K1 现役 config 在合成盘上的选股集合**不因新增字段而改变**;② 新字段
默认值确为「关闭」;③ 默认关闭时 `build_entry_mask` **不引用** `is_mainline_member`
列(可作用在无该列的面板上);④ 显式打开时才 AND 该列。任一断言破 = K1 被动了,红线。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from neckline.strategy import brain
from neckline.strategy.momentum import MomentumConfig, build_entry_mask
from tests.conftest import seed_active_rule_v1

pytestmark = pytest.mark.usefixtures("isolated_env")

_D = date(2024, 3, 4)


def _row(code, board, st=False, pull=True, member=None):
    r = {
        "ts_code": code, "trade_date": _D, "board": board, "close": 10.0,
        "amount_ma20": 50000.0, "ma20": 9.0, "is_st": st, "above_ma20_bullish": True,
        "vol_ratio_5": 1.5, "ret_1d": -0.005 if pull else 0.03,
        "ma10": 9.5 if pull else 10.5, "dist_from_high_20d": -0.02,
    }
    if member is not None:
        r["is_mainline_member"] = member
    return r


def _k1_panel_no_member_col() -> pl.DataFrame:
    """K1 时代的面板不含 is_mainline_member 列——默认关闭时 build_entry_mask 必须能
    在这样的面板上工作(证明默认不引用新列)。"""
    return pl.DataFrame([
        _row("600001.SH", "MAIN"),            # 主板回调 → K1 入选
        _row("600002.SH", "MAIN", pull=False),# 主板未回调 → 剔除
        _row("300001.SZ", "GEM"),             # 创业板 → forbid_high_elasticity 剔除
        _row("688001.SH", "STAR"),            # 科创板 → forbid_high_elasticity 剔除
        _row("600003.SH", "MAIN", st=True),   # ST → base_universe 剔除
    ])


def _k1_cfg() -> MomentumConfig:
    return MomentumConfig(**brain.active_config())


class TestK1BitIdentical:
    def test_k1_selection_unchanged_on_synthetic(self, isolated_env):
        """K1 现役 config 在合成盘上的选股 = 仅主板回调票,一位不变。"""
        seed_active_rule_v1(isolated_env)
        cfg = _k1_cfg()
        panel = _k1_panel_no_member_col()
        selected = set(panel.filter(build_entry_mask(cfg))["ts_code"].to_list())
        assert selected == {"600001.SH"}

    def test_new_fields_default_off(self, isolated_env):
        """新增研究字段默认必须「关闭」= 与 K1 逐位相同的前提。"""
        cfg = _k1_cfg()
        assert cfg.require_mainline_member is False
        assert cfg.take_profit_fixed is None
        assert cfg.high_elasticity_half is False

    def test_default_off_does_not_reference_member_column(self, isolated_env):
        """默认关闭时 build_entry_mask 不引用 is_mainline_member——能作用在无该列的
        面板上不报错,且结果与 K1 一致。"""
        cfg = _k1_cfg()
        assert cfg.require_mainline_member is False
        panel = _k1_panel_no_member_col()
        assert "is_mainline_member" not in panel.columns
        selected = set(panel.filter(build_entry_mask(cfg))["ts_code"].to_list())
        assert selected == {"600001.SH"}

    def test_require_member_true_ands_the_column(self, isolated_env):
        """显式打开 require_mainline_member=True 时,才 AND is_mainline_member 列:
        主板回调票里,非成员被剔、成员保留。"""
        panel = pl.DataFrame([
            _row("600001.SH", "MAIN", member=True),   # 主板回调 + 成员 → 入选
            _row("600010.SH", "MAIN", member=False),  # 主板回调 + 非成员 → 被 mask 剔除
        ])
        base_cfg = MomentumConfig(**{**brain.active_config(), "require_mainline_member": False})
        on_cfg = MomentumConfig(**{**brain.active_config(), "require_mainline_member": True})
        # 关闭:两只都在(不看成员列)
        off_sel = set(panel.filter(build_entry_mask(base_cfg))["ts_code"].to_list())
        assert off_sel == {"600001.SH", "600010.SH"}
        # 打开:只留成员
        on_sel = set(panel.filter(build_entry_mask(on_cfg))["ts_code"].to_list())
        assert on_sel == {"600001.SH"}
