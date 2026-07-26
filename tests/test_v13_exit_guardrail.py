"""v1.3-① 退出规则护栏 + 回测两档条件退出单测(§五 v1.3-①-A/B)。

承 `test_k3_oversold_guardrail` 姿势:新增 `MomentumConfig.max_hold_days_profit`/
`time_exit_only_if_unprofitable` **一律默认 None/False = 与 K1 逐位相同**——默认档时间
退出无条件在 `max_hold_days` 触发,净浮盈**不被咨询**、豁免态 `_eff_max` 永不被写。

两档条件退出(启用 config)镜像 `research/h9_exit_reform.py::_sim_one` 的 V1:D5 恰达时算
收盘净浮盈(扣双边费,引擎既有 Broker fee 模型),>0 一次性豁免续持至硬上限、≤0 照旧退出。

冻结基线(改动前后逐位吻合,施工期用真 k3_panel 六年回测实证):**K1 N=1288 /
total_return −20.53% / final_equity 95361.50**(本文件锁语义层,六年数据不入 CI,见
`tests/test_v13_exit_6y_baseline.py` 的可选实证 skip 机制)。
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from neckline.backtest.portfolio import Position
from neckline.calendar import trading_days_between
from neckline.strategy.momentum import MomentumConfig, MomentumStrategy


def _empty_strategy(cfg: MomentumConfig) -> MomentumStrategy:
    """空面板 strategy(只测退出决策,不测选股),同 test_momentum._empty_strategy。"""
    empty = pl.DataFrame(
        schema={
            "ts_code": pl.Utf8, "trade_date": pl.Date, "board": pl.Utf8, "close": pl.Float64,
            "amount_ma20": pl.Float64, "ma20": pl.Float64, "is_st": pl.Boolean,
            "above_ma20_bullish": pl.Boolean, "vol_ratio_5": pl.Float64, "ret_1d": pl.Float64,
            "ma10": pl.Float64, "dist_from_high_20d": pl.Float64,
        }
    )
    return MomentumStrategy(empty, cfg, initial_cash=120000.0)


_BUY = date(2024, 1, 2)


def _date_at_held(n: int, buy: date = _BUY) -> date:
    """返回使 `trading_days_between(buy, d)` 恰为 n 的交易日(闭区间口径)。"""
    d = buy
    while len(trading_days_between(buy, d)) < n:
        d += timedelta(days=1)
    return d


def _pos(buy_price=10.0, shares=1000, buy_fees=5.0, buy=_BUY) -> Position:
    return Position(ts_code="X", shares=shares, buy_price=buy_price, buy_date=buy, buy_fees=buy_fees)


# —— K1 逐位不变护栏 ————————————————————————————————————————————————————

class TestK1ExitBitIdentical:
    def test_new_fields_default_off(self):
        cfg = MomentumConfig()
        assert cfg.max_hold_days_profit is None
        assert cfg.time_exit_only_if_unprofitable is False

    def test_time_exit_unconditional_when_disabled_profit(self):
        """默认档 + D5 大幅浮盈:仍无条件时间退出(不豁免),且 _eff_max 未被写。"""
        s = _empty_strategy(MomentumConfig(stop_pct=None, take_profit_retrace=None, max_hold_days=5))
        pos = _pos()
        d5 = _date_at_held(5)
        s._peak_close["X"] = 20.0  # 大幅浮盈,若两档启用会豁免;默认档不看
        r = s._exit_reason("X", pos, d5, {"X": 20.0}, {"X": 20.0})
        assert r is not None and "时间退出" in r
        assert s._eff_max == {}      # 默认档绝不触碰豁免态(逐位不变前提)

    def test_time_exit_unconditional_when_disabled_loss(self):
        """默认档 + D5 浮亏(未破止损):同样无条件时间退出——与浮盈单结果一致(net float 不参与)。"""
        s = _empty_strategy(MomentumConfig(stop_pct=None, take_profit_retrace=None, max_hold_days=5))
        d5 = _date_at_held(5)
        r = s._exit_reason("X", _pos(), d5, {"X": 9.6}, {"X": 9.6})
        assert r is not None and "时间退出" in r
        assert s._eff_max == {}

    def test_disabled_never_uses_fee_broker_for_exit(self):
        """默认档下 D4(未到)不退、D5 退,行为与 v1.3 前完全一致(护栏:新分支不进)。"""
        s = _empty_strategy(MomentumConfig(stop_pct=None, take_profit_retrace=None, max_hold_days=5))
        assert s._exit_reason("X", _pos(), _date_at_held(4), {"X": 12.0}, {"X": 12.0}) is None
        assert s._exit_reason("X", _pos(), _date_at_held(5), {"X": 12.0}, {"X": 12.0}) is not None


# —— v1.3 两档条件退出(对拍 h9 V1 口径)————————————————————————————————————

def _v13_cfg(**over) -> MomentumConfig:
    base = dict(stop_pct=0.05, take_profit_retrace=0.08, max_hold_days=5,
                max_hold_days_profit=15, time_exit_only_if_unprofitable=True)
    base.update(over)
    return MomentumConfig(**base)


class TestTwoTierConditionalExit:
    def test_d5_profit_exempts_no_exit(self):
        """D5 收盘净浮盈 >0 → 豁免时间退出(返 None)+ _eff_max 抬到硬上限 15。"""
        s = _empty_strategy(_v13_cfg())
        pos = _pos()
        s._peak_close["X"] = 11.0            # peak==cur,无回落;cur>止损线 → 只走时间退出路径
        r = s._exit_reason("X", pos, _date_at_held(5), {"X": 11.0}, {"X": 11.0})
        assert r is None                      # 豁免,不退
        assert s._eff_max["X"] == 15          # 一次性豁免续命到硬上限

    def test_d5_nonprofit_time_exits(self):
        """D5 收盘净浮盈 ≤0(浮亏、未破止损)→ 照旧时间退出。"""
        s = _empty_strategy(_v13_cfg())
        r = s._exit_reason("X", _pos(), _date_at_held(5), {"X": 9.6}, {"X": 9.6})
        assert r is not None and "时间退出" in r and "硬上限" not in r
        assert "X" not in s._eff_max          # 未豁免

    def test_exempt_then_holds_to_hard_cap(self):
        """豁免后 D6..D14 不时间退出(交回落/止损),D15 硬上限无条件退出。"""
        s = _empty_strategy(_v13_cfg())
        pos = _pos()
        s._peak_close["X"] = 11.0
        assert s._exit_reason("X", pos, _date_at_held(5), {"X": 11.0}, {"X": 11.0}) is None
        # D6..D14:仍无退出理由(peak==cur 无回落、cur>止损)
        for n in range(6, 15):
            assert s._exit_reason("X", pos, _date_at_held(n), {"X": 11.0}, {"X": 11.0}) is None
        # D15:硬上限强退
        r15 = s._exit_reason("X", pos, _date_at_held(15), {"X": 11.0}, {"X": 11.0})
        assert r15 is not None and "硬上限" in r15

    def test_exempt_then_stop_loss_still_wins(self):
        """豁免续命段仍受 -5% 止损管:豁免后跌破止损线 → 止损离场(优先于时间退出)。"""
        s = _empty_strategy(_v13_cfg())
        pos = _pos()
        s._peak_close["X"] = 11.0
        assert s._exit_reason("X", pos, _date_at_held(5), {"X": 11.0}, {"X": 11.0}) is None
        r = s._exit_reason("X", pos, _date_at_held(8), {"X": 9.0}, {"X": 9.0})   # 跌破 9.5
        assert r is not None and "止损" in r

    def test_suspended_on_d5_not_exempt(self):
        """D5 停牌无收盘价(cur=None)→ 不豁免 → 时间退出(与 h9 _sim_one 停牌不豁免同口径)。"""
        s = _empty_strategy(_v13_cfg())
        r = s._exit_reason("X", _pos(), _date_at_held(5), {}, {})   # 无价
        assert r is not None and "时间退出" in r
        assert "X" not in s._eff_max

    def test_breakeven_boundary_flips_on_fees(self):
        """盈亏平衡线附近:扣双边费后净浮盈由正翻负 → 判向从豁免翻成时间退出(费用口径敏感)。"""
        # buy 10.0 × 1000 股,buy_fees=5;卖出费 ≈ 印花税(万5)+过户(万0.1)+佣金(≥5元地板)。
        # cur=10.02:毛浮盈 =20,扣 buy_fees 5 + 卖出费(约 5+~5.5+0.1≈10.6)→ 净浮盈 ≈ +4 >0 → 豁免。
        s1 = _empty_strategy(_v13_cfg())
        s1._peak_close["X"] = 10.02
        assert s1._exit_reason("X", _pos(), _date_at_held(5), {"X": 10.02}, {"X": 10.02}) is None
        # cur=10.01:毛浮盈 =10,扣费后净浮盈 <0 → 不豁免 → 时间退出。
        s2 = _empty_strategy(_v13_cfg())
        s2._peak_close["X"] = 10.01
        r = s2._exit_reason("X", _pos(), _date_at_held(5), {"X": 10.01}, {"X": 10.01})
        assert r is not None and "时间退出" in r

    def test_net_float_helper_matches_broker_fee_model(self):
        """_d5_net_float 用引擎既有 Broker._sell_fees(与 h9 §2 对拍口径),非 neckline/fees 估算。"""
        from neckline.backtest.broker import Broker
        s = _empty_strategy(_v13_cfg())
        pos = _pos(buy_price=10.0, shares=1000, buy_fees=5.0)
        cur = 11.0
        expected_sell_fee = Broker()._sell_fees(1000 * cur)
        expected = 1000 * (cur - 10.0) - 5.0 - expected_sell_fee
        assert abs(s._d5_net_float(pos, cur) - expected) < 1e-9
