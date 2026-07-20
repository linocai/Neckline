"""母战法可配置策略单测(plan 1.3/1.4/1.7/1.8)。锁死退出决策(止损/回落止盈/时间
退出)、冷却、仓位纪律、次周减半(P10 挂起项)四类路径依赖逻辑;on_day 的建仓纪律
用手工构造的 BacktestContext 断言(不跑真实 Parquet,快)。"""

from __future__ import annotations

from datetime import date

import polars as pl

from neckline.backtest.portfolio import ClosedTrade, Portfolio, Position
from neckline.backtest.strategy import BacktestContext
from neckline.strategy.momentum import MomentumConfig, MomentumStrategy


def _empty_strategy(cfg: MomentumConfig) -> MomentumStrategy:
    """不含候选的空面板 strategy(只测退出/纪律辅助方法,不测选股)。"""
    empty = pl.DataFrame(
        schema={
            "ts_code": pl.Utf8, "trade_date": pl.Date, "board": pl.Utf8, "close": pl.Float64,
            "amount_ma20": pl.Float64, "ma20": pl.Float64, "is_st": pl.Boolean,
            "above_ma20_bullish": pl.Boolean, "vol_ratio_5": pl.Float64, "ret_1d": pl.Float64,
            "ma10": pl.Float64, "dist_from_high_20d": pl.Float64,
        }
    )
    return MomentumStrategy(empty, cfg, initial_cash=120000.0)


class TestExitReason:
    def _pos(self, buy_price=10.0, buy_date=date(2024, 1, 2)):
        return Position(ts_code="X", shares=1000, buy_price=buy_price, buy_date=buy_date)

    def test_stop_loss_on_close_breach(self):
        s = _empty_strategy(MomentumConfig(stop_pct=0.05, max_hold_days=99))
        pos = self._pos()
        # 收盘 9.4 < 10×0.95=9.5 → 止损
        r = s._exit_reason("X", pos, date(2024, 1, 3), {"X": 9.4}, {"X": 9.4})
        assert r is not None and "止损" in r

    def test_stop_loss_on_low_gap_through(self):
        # 收盘未破(9.6>9.5)但最低价 9.3 击穿 → 仍止损(跳空/盘中破位按 low 判)
        s = _empty_strategy(MomentumConfig(stop_pct=0.05, max_hold_days=99))
        r = s._exit_reason("X", self._pos(), date(2024, 1, 3), {"X": 9.6}, {"X": 9.3})
        assert r is not None and "止损" in r

    def test_no_stop_above_line(self):
        s = _empty_strategy(MomentumConfig(stop_pct=0.05, max_hold_days=99))
        r = s._exit_reason("X", self._pos(), date(2024, 1, 3), {"X": 9.8}, {"X": 9.6})
        assert r is None

    def test_take_profit_retrace(self):
        s = _empty_strategy(MomentumConfig(stop_pct=None, take_profit_retrace=0.08, max_hold_days=99))
        pos = self._pos()
        s._peak_close["X"] = 12.0  # 峰值 12,回落 8% → 触发线 11.04
        r = s._exit_reason("X", pos, date(2024, 1, 5), {"X": 11.0}, {"X": 11.0})
        assert r is not None and "回落止盈" in r
        # 未回落够(11.5>11.04)→ 不卖
        assert s._exit_reason("X", pos, date(2024, 1, 5), {"X": 11.5}, {"X": 11.5}) is None

    def test_time_exit(self):
        # 无止损无止盈,持有满 max_hold_days=3 → 时间退出。买入 1/2,到 1/4 共 3 交易日。
        s = _empty_strategy(MomentumConfig(stop_pct=None, max_hold_days=3))
        pos = self._pos(buy_date=date(2024, 1, 2))
        assert s._exit_reason("X", pos, date(2024, 1, 4), {"X": 10.5}, {"X": 10.4}) is not None
        # 1/3 只 2 交易日,未满
        assert s._exit_reason("X", pos, date(2024, 1, 3), {"X": 10.5}, {"X": 10.4}) is None

    def test_stop_priority_over_time(self):
        # 止损优先于时间退出的措辞(破位即止损,不管持有天数)
        s = _empty_strategy(MomentumConfig(stop_pct=0.05, max_hold_days=1))
        r = s._exit_reason("X", self._pos(), date(2024, 1, 4), {"X": 9.0}, {"X": 9.0})
        assert "止损" in r


class TestCooldownAndWeekLoss:
    def test_cooldown_set_on_loss_only(self):
        s = _empty_strategy(MomentumConfig(cooldown_days=10))
        pf = Portfolio(120000.0)
        # 亏损平仓
        pf.closed_trades.append(
            ClosedTrade("L", date(2024, 1, 2), date(2024, 1, 5), 1000, 10.0, 9.0, 0.0, 0.0)
        )
        # 盈利平仓
        pf.closed_trades.append(
            ClosedTrade("W", date(2024, 1, 2), date(2024, 1, 5), 1000, 10.0, 11.0, 0.0, 0.0)
        )
        s._consume_closed_trades(pf)
        assert "L" in s._cooldown_until       # 亏损设冷却
        assert "W" not in s._cooldown_until   # 盈利不设冷却

    def test_week_loss_accumulates_losses_only(self):
        s = _empty_strategy(MomentumConfig())
        pf = Portfolio(120000.0)
        pf.closed_trades.append(ClosedTrade("L", date(2024, 1, 2), date(2024, 1, 5), 1000, 10.0, 9.0, 0.0, 0.0))
        pf.closed_trades.append(ClosedTrade("W", date(2024, 1, 2), date(2024, 1, 5), 1000, 10.0, 11.0, 0.0, 0.0))
        s._consume_closed_trades(pf)
        iso = date(2024, 1, 5).isocalendar()
        assert s._week_loss[(iso[0], iso[1])] == -1000.0  # 只累计亏损那笔

    def test_incremental_consume_no_double_count(self):
        s = _empty_strategy(MomentumConfig(cooldown_days=5))
        pf = Portfolio(120000.0)
        pf.closed_trades.append(ClosedTrade("L", date(2024, 1, 2), date(2024, 1, 5), 1000, 10.0, 9.0, 0.0, 0.0))
        s._consume_closed_trades(pf)
        first = s._week_loss[tuple(date(2024, 1, 5).isocalendar()[:2])]
        s._consume_closed_trades(pf)  # 再消化一次,不应重复计入
        assert s._week_loss[tuple(date(2024, 1, 5).isocalendar()[:2])] == first


class TestWeekHalving:
    def test_halving_off_by_default(self):
        s = _empty_strategy(MomentumConfig(week_halving=False))
        assert s._effective_single_cap(date(2024, 3, 4)) == 20000.0

    def test_halving_triggers_on_prev_week_loss(self):
        s = _empty_strategy(MomentumConfig(week_halving=True, week_halving_threshold=0.05, single_cap=20000.0))
        # t 在某 ISO 周,上一 ISO 周(t-7天)亏损 ≥ 6000(5%×120000)
        t = date(2024, 3, 11)  # 周一
        prev = (t.fromordinal(t.toordinal() - 7)).isocalendar()
        s._week_loss[(prev[0], prev[1])] = -6500.0
        assert s._effective_single_cap(t) == 10000.0

    def test_no_halving_below_threshold(self):
        s = _empty_strategy(MomentumConfig(week_halving=True, week_halving_threshold=0.05))
        t = date(2024, 3, 11)
        prev = (t.fromordinal(t.toordinal() - 7)).isocalendar()
        s._week_loss[(prev[0], prev[1])] = -3000.0  # 只亏 2.5% < 5%
        assert s._effective_single_cap(t) == 20000.0

    def test_halving_year_boundary_no_crash(self):
        # 年初 week 1:iso[1]-1 会出界,减 7 天写法不崩且正确回看上一 ISO 周
        s = _empty_strategy(MomentumConfig(week_halving=True, week_halving_threshold=0.05))
        t = date(2024, 1, 3)  # 2024 ISO week 1
        prev = (t.fromordinal(t.toordinal() - 7)).isocalendar()
        s._week_loss[(prev[0], prev[1])] = -7000.0
        assert s._effective_single_cap(t) == 10000.0  # 正确命中上一 ISO 周(2023 末周)


class TestEntryMaskAndDiscipline:
    def _panel_day(self, d: date):
        """8 只候选,全部满足 base_universe + volprice + pullback;dist_from_high_20d 递减
        用于排序。另加 1 只 ST(应被 base_universe 剔除)、1 只缩量(volprice 剔除)。"""
        rows = []
        for i in range(8):
            rows.append({
                "ts_code": f"C{i}", "trade_date": d, "board": "MAIN", "close": 10.0,
                "amount_ma20": 50000.0, "ma20": 9.0, "is_st": False,
                "above_ma20_bullish": True, "vol_ratio_5": 1.5, "ret_1d": -0.005,
                "ma10": 9.5, "dist_from_high_20d": -0.01 * i,  # C0 最贴前高(最大)
            })
        rows.append({  # ST 应被剔除
            "ts_code": "STX", "trade_date": d, "board": "MAIN", "close": 10.0,
            "amount_ma20": 50000.0, "ma20": 9.0, "is_st": True,
            "above_ma20_bullish": True, "vol_ratio_5": 1.5, "ret_1d": -0.005,
            "ma10": 9.5, "dist_from_high_20d": 0.0,
        })
        rows.append({  # 缩量应被 volprice 剔除
            "ts_code": "SHRINK", "trade_date": d, "board": "MAIN", "close": 10.0,
            "amount_ma20": 50000.0, "ma20": 9.0, "is_st": False,
            "above_ma20_bullish": True, "vol_ratio_5": 0.7, "ret_1d": -0.005,
            "ma10": 9.5, "dist_from_high_20d": 0.0,
        })
        return pl.DataFrame(rows)

    def _ctx(self, strat, d, portfolio, codes):
        ms = pl.DataFrame({
            "ts_code": codes,
            "open": [10.0] * len(codes), "high": [10.2] * len(codes),
            "low": [9.9] * len(codes), "close": [10.0] * len(codes), "pre_close": [10.0] * len(codes),
        })
        return BacktestContext(trade_date=d, market_slice=ms, limit_slice=pl.DataFrame(),
                               portfolio=portfolio, history=lambda c, s, e: pl.DataFrame())

    def test_entry_mask_filters_st_and_shrink(self):
        d = date(2024, 3, 4)
        cfg = MomentumConfig(strength="volprice", buypoint="pullback")
        s = MomentumStrategy(self._panel_day(d), cfg, initial_cash=120000.0)
        selected = set(s._by_date.get(d)["ts_code"].to_list())
        assert selected == {f"C{i}" for i in range(8)}  # ST/缩量都被剔除

    def test_buy_respects_max_positions_and_exposure(self):
        d = date(2024, 3, 4)
        cfg = MomentumConfig(strength="volprice", buypoint="pullback",
                             single_cap=20000.0, max_positions=5, max_exposure_frac=0.60)
        s = MomentumStrategy(self._panel_day(d), cfg, initial_cash=120000.0)
        codes = [f"C{i}" for i in range(8)]
        pf = Portfolio(120000.0)
        orders = s.on_day(self._ctx(s, d, pf, codes))
        buys = [o for o in orders if o.side == "buy"]
        assert len(buys) <= cfg.max_positions
        assert all(o.target_value <= cfg.single_cap + 1e-6 for o in buys)
        # 敞口预算 = 0.60×120000 = 72000;累计不超
        assert sum(o.target_value for o in buys) <= 0.60 * 120000 + 1e-6
        # 排序:C0 最贴前高(dist 最大),rank_desc 默认 → C0 优先入选
        assert "C0" in [o.ts_code for o in buys]

    def test_cooldown_excludes_candidate(self):
        d = date(2024, 3, 4)
        cfg = MomentumConfig(strength="volprice", buypoint="pullback", cooldown_days=10)
        s = MomentumStrategy(self._panel_day(d), cfg, initial_cash=120000.0)
        s._cooldown_until["C0"] = date(2024, 3, 20)  # C0 冷却中
        codes = [f"C{i}" for i in range(8)]
        orders = s.on_day(self._ctx(s, d, Portfolio(120000.0), codes))
        assert "C0" not in [o.ts_code for o in orders if o.side == "buy"]

    def test_no_rebuy_held(self):
        d = date(2024, 3, 4)
        cfg = MomentumConfig(strength="volprice", buypoint="pullback")
        s = MomentumStrategy(self._panel_day(d), cfg, initial_cash=120000.0)
        pf = Portfolio(120000.0)
        pf.positions["C0"] = Position("C0", 1000, 10.0, date(2024, 3, 1))
        codes = [f"C{i}" for i in range(8)]
        orders = s.on_day(self._ctx(s, d, pf, codes))
        assert "C0" not in [o.ts_code for o in orders if o.side == "buy"]
