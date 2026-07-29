"""K3-B2.0 护栏:K1 现役大脑「逐位不变」单测(承 test_k2_mainline_guardrail 姿势)。

K3 研究给 `MomentumConfig` 新增超跌买点 `buypoint="oversold"` 分支 + 一组默认关闭的
`oversold_*` 字段(depth/trend/pullback/confirm/vol)——**新增字段一律默认 None/off,
默认 buypoint="pullback" = 与 K1 逐位相同**(§五C B2.0 边界铁律②)。

本文件锁死:① 新 `oversold_*` 字段默认值确为「关闭」(None);② K1 现役 config 在合成
盘上的选股集合**不因新买点分支/新字段而改变**;③ 默认 pullback 路径 `build_entry_mask`
**不引用**任何 K3 扩展面板列(ma250/ma250_slope_up/consec_down_days/dist_from_ma250/
ret_5d_pct——可作用在无这些列的 K1 时代面板上);④ 显式 `buypoint="oversold"` 时才
引用 `buy_oversold` 信号(降势/升势+确认分支各选对)。任一断言破 = K1 被动了,红线。
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

# K3 扩展面板独有的列(K1 时代面板不含;默认 pullback 路径必须不引用它们)
_K3_ONLY_COLS = ("ma250", "ma250_slope_up", "ma60", "ma60_slope_up",
                 "dist_from_ma250", "dist_from_ma60", "consec_down_days",
                 "ret_5d_pct", "ret_10d_pct")


def _k1_row(code, board, st=False, pull=True):
    """K1 时代面板行(base_universe + pullback 所需列;**故意不含任何 K3 列**)。"""
    return {
        "ts_code": code, "trade_date": _D, "board": board, "close": 10.0,
        "amount_ma20": 50000.0, "ma20": 9.0, "is_st": st, "above_ma20_bullish": True,
        "vol_ratio_5": 1.5, "ret_1d": -0.005 if pull else 0.03,
        "ma10": 9.5 if pull else 10.5, "dist_from_high_20d": -0.02,
    }


def _k1_panel_no_k3_cols() -> pl.DataFrame:
    """K1 时代面板不含任何 K3 扩展列——默认 pullback 路径必须能在其上工作。"""
    return pl.DataFrame([
        _k1_row("600001.SH", "MAIN"),             # 主板回调 → K1 入选
        _k1_row("600002.SH", "MAIN", pull=False), # 主板未回调 → 剔除
        _k1_row("300001.SZ", "GEM"),              # 创业板 → forbid_high_elasticity 剔除
        _k1_row("688001.SH", "STAR"),             # 科创板 → forbid_high_elasticity 剔除
        _k1_row("600003.SH", "MAIN", st=True),    # ST → base_universe 剔除
    ])


def _k1_cfg(db_path) -> MomentumConfig:
    """§七 P4-25(v1.5-④-A4):`db_path` 显式必传(真库只读副本,见
    `conftest.py::real_db_readonly_copy`)——不许裸调 `brain.active_config()`,
    那会命中 `neckline/db.py` 自己的模块级 settings、直接读写真实开发库。"""
    return MomentumConfig(**brain.active_config(db_path=db_path))


class TestK1BitIdentical:
    def test_new_oversold_fields_default_off(self, isolated_env):
        """新增 oversold_* 字段默认必须「关闭」(None)= 与 K1 逐位相同的前提。"""
        cfg = MomentumConfig()
        assert cfg.buypoint == "pullback"
        assert cfg.oversold_depth_col is None
        assert cfg.oversold_depth_max is None
        assert cfg.oversold_trend is None
        assert cfg.oversold_pullback_max is None
        assert cfg.oversold_confirm is None
        assert cfg.oversold_confirm_vol is None
        assert cfg.oversold_vol_max is None

    def test_k1_selection_unchanged_on_synthetic(self, isolated_env, real_db_readonly_copy):
        """K1 现役 config 在合成盘上的选股 = 仅主板回调票,一位不变(新分支未污染)。"""
        seed_active_rule_v1(isolated_env)
        cfg = _k1_cfg(real_db_readonly_copy)
        assert cfg.buypoint == "pullback"
        panel = _k1_panel_no_k3_cols()
        selected = set(panel.filter(build_entry_mask(cfg))["ts_code"].to_list())
        assert selected == {"600001.SH"}

    def test_default_pullback_does_not_reference_k3_columns(self, isolated_env, real_db_readonly_copy):
        """默认 pullback 路径 build_entry_mask 不引用任何 K3 扩展列——能作用在无这些
        列的 K1 时代面板上不报错,且结果与 K1 一致。"""
        seed_active_rule_v1(isolated_env)
        cfg = _k1_cfg(real_db_readonly_copy)
        panel = _k1_panel_no_k3_cols()
        for c in _K3_ONLY_COLS:
            assert c not in panel.columns
        selected = set(panel.filter(build_entry_mask(cfg))["ts_code"].to_list())
        assert selected == {"600001.SH"}


# —— buypoint="oversold" 分支验证(含 K3 列的合成盘)————————————————————
def _os_row(code, board, *, ret_1d=0.0, ret_5d=0.0, ret_20d=0.0, close=10.0,
            ma5=10.0, ma10=10.0, ma250=10.0, slope_up=True, dist_high=-0.02,
            vol=1.5, consec=0, days=300):
    return {
        "ts_code": code, "trade_date": _D, "board": board, "close": close,
        "amount_ma20": 50000.0, "ma20": 9.0, "is_st": False, "above_ma20_bullish": True,
        "ret_1d": ret_1d, "ret_5d": ret_5d, "ret_20d": ret_20d,
        "ma5": ma5, "ma10": ma10, "ma250": ma250, "ma250_slope_up": slope_up,
        "dist_from_high_20d": dist_high, "vol_ratio_5": vol,
        "consec_down_days": consec, "days_since_listing": days,
    }


class TestOversoldBranch:
    def test_arm1_c4_downtrend_deep5(self, isolated_env):
        """臂① C4:ret_5d≤-0.10 & 降势(close<ma250 & ~slope_up)+ 主板 only + 非次新。"""
        panel = pl.DataFrame([
            _os_row("600001.SH", "MAIN", ret_5d=-0.15, close=9.0, ma250=12.0, slope_up=False),  # ✓ 降势深跌
            _os_row("600002.SH", "MAIN", ret_5d=-0.15, close=13.0, ma250=12.0, slope_up=True),   # ✗ 升势(close>ma250&升)
            _os_row("600003.SH", "MAIN", ret_5d=-0.05, close=9.0, ma250=12.0, slope_up=False),   # ✗ 跌不够深
            _os_row("300001.SZ", "GEM",  ret_5d=-0.15, close=9.0, ma250=12.0, slope_up=False),   # ✗ 创业板(高弹剔除)
            _os_row("600009.SH", "MAIN", ret_5d=-0.15, close=9.0, ma250=12.0, slope_up=False, days=60),  # ✗ 次新
        ])
        cfg = MomentumConfig(strength="none", buypoint="oversold", forbid_high_elasticity=True,
                             forbid_new_days=120, oversold_depth_col="ret_5d",
                             oversold_depth_max=-0.10, oversold_trend="down")
        sel = set(panel.filter(build_entry_mask(cfg))["ts_code"].to_list())
        assert sel == {"600001.SH"}

    def test_arm2_a6_deep20_no_trend(self, isolated_env):
        """臂② A6:ret_20d≤-0.20,不分趋势(trend=None)。"""
        panel = pl.DataFrame([
            _os_row("600001.SH", "MAIN", ret_20d=-0.25, slope_up=False),  # ✓
            _os_row("600002.SH", "MAIN", ret_20d=-0.25, slope_up=True),   # ✓ 不分趋势,升势也入
            _os_row("600003.SH", "MAIN", ret_20d=-0.10),                  # ✗ 跌不够深
        ])
        cfg = MomentumConfig(strength="none", buypoint="oversold", forbid_high_elasticity=True,
                             forbid_new_days=120, oversold_depth_col="ret_20d",
                             oversold_depth_max=-0.20)
        sel = set(panel.filter(build_entry_mask(cfg))["ts_code"].to_list())
        assert sel == {"600001.SH", "600002.SH"}

    def test_arm3_uptrend_pullback_reclaim_ma5(self, isolated_env):
        """臂③:升势(close>ma250&升)+ 回撤(dist_high≤-0.08)+ 放量阳线收复 MA5。"""
        panel = pl.DataFrame([
            # ✓ 升势 + 回撤 -10% + 今日阳线收于 MA5 上 + 放量
            _os_row("600001.SH", "MAIN", ret_1d=0.03, close=10.0, ma5=9.8, ma250=8.0,
                    slope_up=True, dist_high=-0.10, vol=1.8),
            # ✗ 回撤不够(-0.03)
            _os_row("600002.SH", "MAIN", ret_1d=0.03, close=10.0, ma5=9.8, ma250=8.0,
                    slope_up=True, dist_high=-0.03, vol=1.8),
            # ✗ 阴线(ret_1d<0)未收复
            _os_row("600003.SH", "MAIN", ret_1d=-0.01, close=10.0, ma5=9.8, ma250=8.0,
                    slope_up=True, dist_high=-0.10, vol=1.8),
            # ✗ 收于 MA5 下方
            _os_row("600004.SH", "MAIN", ret_1d=0.03, close=9.5, ma5=9.8, ma250=8.0,
                    slope_up=True, dist_high=-0.10, vol=1.8),
            # ✗ 量能不足(vol<1.5)
            _os_row("600005.SH", "MAIN", ret_1d=0.03, close=10.0, ma5=9.8, ma250=8.0,
                    slope_up=True, dist_high=-0.10, vol=1.2),
            # ✗ 降势(close<ma250)
            _os_row("600006.SH", "MAIN", ret_1d=0.03, close=10.0, ma5=9.8, ma250=12.0,
                    slope_up=False, dist_high=-0.10, vol=1.8),
        ])
        cfg = MomentumConfig(strength="none", buypoint="oversold", forbid_high_elasticity=True,
                             forbid_new_days=120, oversold_trend="up", oversold_pullback_max=-0.08,
                             oversold_confirm="reclaim_ma5", oversold_confirm_vol=1.5)
        sel = set(panel.filter(build_entry_mask(cfg))["ts_code"].to_list())
        assert sel == {"600001.SH"}

    def test_arm3_stabilize_low_volume(self, isolated_env):
        """臂③ 缩量止跌企稳:今日止跌(consec_down_days==0)+ 缩量(vol≤0.8)。"""
        panel = pl.DataFrame([
            # ✓ 升势回撤 + 今日止跌 + 缩量
            _os_row("600001.SH", "MAIN", ret_1d=0.005, close=10.0, ma250=8.0, slope_up=True,
                    dist_high=-0.10, vol=0.7, consec=0),
            # ✗ 仍在下跌(consec>0)
            _os_row("600002.SH", "MAIN", ret_1d=-0.01, close=10.0, ma250=8.0, slope_up=True,
                    dist_high=-0.10, vol=0.7, consec=2),
            # ✗ 放量(非缩量)
            _os_row("600003.SH", "MAIN", ret_1d=0.005, close=10.0, ma250=8.0, slope_up=True,
                    dist_high=-0.10, vol=1.5, consec=0),
        ])
        cfg = MomentumConfig(strength="none", buypoint="oversold", forbid_high_elasticity=True,
                             forbid_new_days=120, oversold_trend="up", oversold_pullback_max=-0.08,
                             oversold_confirm="stabilize", oversold_vol_max=0.8)
        sel = set(panel.filter(build_entry_mask(cfg))["ts_code"].to_list())
        assert sel == {"600001.SH"}
