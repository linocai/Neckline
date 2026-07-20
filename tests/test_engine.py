"""回测引擎端到端单测(plan 0.7)。用隔离 Parquet + DB 灌一段小样本数据,验证
Strategy → Broker → Portfolio → Report 全链路接起来且遵守 T+1 / 前视截断。
"""

from __future__ import annotations

from datetime import date
from typing import List

import pytest

from neckline.backtest import BacktestContext, BacktestEngine, Broker, Order, Strategy
from neckline.backtest.engine import load_adjusted_daily
from neckline.strategy.dummy import DummyStrategy
from tests.conftest import business_days, insert_trade_cal, write_daily_fixture

pytestmark = pytest.mark.usefixtures("isolated_env")

CODES = ["600001.SH", "600002.SH", "600003.SH", "600004.SH"]


def _seed_market(settings, days: List[date]) -> None:
    insert_trade_cal(settings, days)
    import neckline.calendar as cal

    cal.reset_cache()
    price = {c: 10.0 + i for i, c in enumerate(CODES)}
    for d in days:
        rows = []
        for c in CODES:
            p = price[c]
            rows.append(
                {"ts_code": c, "open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "pre_close": p, "vol": 10000.0}
            )
            price[c] = round(p * 1.001, 2)  # 小幅漂移,避免所有价格恒定导致边界情形
        write_daily_fixture(settings, "daily", d, rows)


class TestEngineEndToEnd:
    def test_equity_curve_length_matches_trading_days(self, isolated_env):
        days = business_days(date(2024, 1, 2), 10)
        _seed_market(isolated_env, days)
        strat = DummyStrategy(n_positions=2, hold_days=2, min_price=1.0)
        engine = BacktestEngine(strat, start=days[0], end=days[-1], initial_cash=100_000, parquet_dir=isolated_env.parquet_dir)
        report = engine.run()
        assert report.n_trading_days == len(days)
        assert len(report.equity_curve) == len(days)

    def test_no_trade_violates_t1(self, isolated_env):
        days = business_days(date(2024, 1, 2), 10)
        _seed_market(isolated_env, days)
        strat = DummyStrategy(n_positions=2, hold_days=2, min_price=1.0)
        engine = BacktestEngine(strat, start=days[0], end=days[-1], initial_cash=100_000, parquet_dir=isolated_env.parquet_dir)
        report = engine.run()
        assert len(report.closed_trades) > 0, "该场景应至少产生几笔完整回合,否则测试没测到东西"
        for t in report.closed_trades:
            assert t.buy_date < t.sell_date, f"T+1 违规:{t.ts_code} 买{t.buy_date} 卖{t.sell_date}"

    def test_orders_decided_on_day_t_execute_on_t_plus_1(self, isolated_env):
        """自定义策略:只在第一天下单,断言成交日是第二天而非第一天(逐日循环的
        "决策用 T 日数据、成交在 T+1"契约,是无前视偏差的关键结构保证)。"""
        days = business_days(date(2024, 1, 2), 5)
        _seed_market(isolated_env, days)

        class BuyOnceStrategy(Strategy):
            def __init__(self):
                self.fired = False

            def on_day(self, context: BacktestContext) -> List[Order]:
                if not self.fired and context.trade_date == days[0]:
                    self.fired = True
                    return [Order(ts_code="600001.SH", side="buy", target_value=5000)]
                return []

        strat = BuyOnceStrategy()
        engine = BacktestEngine(strat, start=days[0], end=days[-1], initial_cash=100_000, parquet_dir=isolated_env.parquet_dir)
        engine.run()
        buy_trades = [t for t in engine.last_execution_results if t.status == "filled" and t.order.side == "buy"]
        assert len(buy_trades) == 1
        # 决策在 days[0](T 日),持仓落的 buy_date 必须是 days[1](T+1)——证明策略
        # 决策与成交严格分离在两个不同交易日,不是"当天算当天成交"。
        assert engine.last_portfolio.positions["600001.SH"].buy_date == days[1]
        assert engine.strategy.fired is True

    def test_empty_range_raises_clear_error(self, isolated_env):
        """start > end → trading_days_between 恒返回空列表(不受日历兜底近似影响,
        比挑一个"应该没有交易日"的日期区间更可靠——工作日近似兜底会把任意工作日
        都当交易日,不适合用来构造"空交易日"场景)。"""
        strat = DummyStrategy()
        engine = BacktestEngine(strat, start=date(2024, 1, 10), end=date(2024, 1, 2), parquet_dir=isolated_env.parquet_dir)
        with pytest.raises(ValueError):
            engine.run()

    def test_report_stats_are_internally_consistent(self, isolated_env):
        days = business_days(date(2024, 1, 2), 15)
        _seed_market(isolated_env, days)
        strat = DummyStrategy(n_positions=2, hold_days=3, min_price=1.0)
        engine = BacktestEngine(strat, start=days[0], end=days[-1], initial_cash=100_000, parquet_dir=isolated_env.parquet_dir)
        report = engine.run()
        assert report.n_trades == len(report.closed_trades)
        assert report.final_equity == pytest.approx(report.equity_curve["equity"][-1])
        if report.n_trades > 0:
            assert 0.0 <= report.win_rate <= 1.0


class TestQfqIntegration:
    """plan 0.5"回测统一用前复权价":engine 内部一次性把 [start,end] 全区间复权,
    锚点固定在 end——同一只票前后两天的复权价必须能直接相减比较(见 engine.py
    docstring)。无 adj_factor 数据时优雅降级为未复权价(不崩)。"""

    def test_no_adj_factor_degrades_to_raw_prices(self, isolated_env):
        days = business_days(date(2024, 1, 2), 5)
        _seed_market(isolated_env, days)  # 只写 daily,不写 adj_factor
        strat = DummyStrategy(n_positions=1, hold_days=2, min_price=1.0)
        engine = BacktestEngine(strat, start=days[0], end=days[-1], initial_cash=100_000, parquet_dir=isolated_env.parquet_dir)
        report = engine.run()  # 不应抛异常,应正常跑完
        assert report.n_trading_days == len(days)

    def test_anchor_day_adjusted_price_equals_raw_price(self, isolated_env):
        """锚点在区间末尾:最后一天的复权价必须与原始价严格相等(前复权定义)。"""
        days = business_days(date(2024, 1, 2), 5)
        insert_trade_cal(isolated_env, days)
        import neckline.calendar as cal

        cal.reset_cache()
        # 600001.SH:第 3 天(days[2])发生除权,adj_factor 从 100 跳到 120
        raw_closes = {days[0]: 10.0, days[1]: 10.2, days[2]: 8.6, days[3]: 8.7, days[4]: 8.8}
        adj_factors = {days[0]: 100.0, days[1]: 100.0, days[2]: 120.0, days[3]: 120.0, days[4]: 120.0}
        for d in days:
            c = raw_closes[d]
            write_daily_fixture(
                isolated_env, "daily", d,
                [{"ts_code": "600001.SH", "open": c, "high": c, "low": c, "close": c, "pre_close": c, "vol": 1000.0}],
            )
            write_daily_fixture(
                isolated_env, "adj_factor", d, [{"ts_code": "600001.SH", "adj_factor": adj_factors[d]}]
            )

        strat = DummyStrategy(n_positions=1, hold_days=10, min_price=1.0)
        engine = BacktestEngine(strat, start=days[0], end=days[-1], initial_cash=100_000, parquet_dir=isolated_env.parquet_dir)
        adjusted = engine._load_adjusted_daily(days[0], days[-1])
        last_row = adjusted.filter((adjusted["ts_code"] == "600001.SH") & (adjusted["trade_date"] == days[-1]))
        assert last_row["close"][0] == pytest.approx(raw_closes[days[-1]])

        first_row = adjusted.filter((adjusted["ts_code"] == "600001.SH") & (adjusted["trade_date"] == days[0]))
        # 除权前的价格应被向下折算(120/100 的比例),不再等于原始价
        expected_adjusted_first = raw_closes[days[0]] * (adj_factors[days[0]] / adj_factors[days[-1]])
        assert first_row["close"][0] == pytest.approx(expected_adjusted_first)
        assert first_row["close"][0] != pytest.approx(raw_closes[days[0]])

    def test_injected_adjusted_daily_matches_self_loaded(self, isolated_env):
        """注入预复权缓存(阶段 1 网格提速用)必须与引擎自算的复权结果完全一致——
        否则同窗口复用会引入 P&L 漂移。灌含 adj_factor 的样本,同策略跑两次:一次
        让引擎自算、一次注入 `load_adjusted_daily` 产物,断言净值/回合逐笔相等。"""
        days = business_days(date(2024, 1, 2), 8)
        insert_trade_cal(isolated_env, days)
        import neckline.calendar as cal

        cal.reset_cache()
        price = {c: 10.0 + i for i, c in enumerate(CODES)}
        for k, d in enumerate(days):
            rows_d, rows_a = [], []
            for c in CODES:
                p = price[c]
                rows_d.append({"ts_code": c, "open": p, "high": p * 1.02, "low": p * 0.98,
                               "close": p, "pre_close": p, "vol": 10000.0})
                # 第 4 天起除权跳变一档,确保锚点前后复权价确实被缩放
                rows_a.append({"ts_code": c, "adj_factor": 100.0 if k < 4 else 115.0})
                price[c] = round(p * 1.003, 2)
            write_daily_fixture(isolated_env, "daily", d, rows_d)
            write_daily_fixture(isolated_env, "adj_factor", d, rows_a)

        def _run(inject):
            strat = DummyStrategy(n_positions=2, hold_days=2, min_price=1.0)
            adj = load_adjusted_daily(days[0], days[-1], parquet_dir=isolated_env.parquet_dir) if inject else None
            eng = BacktestEngine(strat, start=days[0], end=days[-1], initial_cash=100_000,
                                 parquet_dir=isolated_env.parquet_dir, adjusted_daily=adj)
            return eng.run()

        a, b = _run(False), _run(True)
        assert a.final_equity == pytest.approx(b.final_equity)
        assert a.n_trades == b.n_trades and a.n_trades > 0
        assert [round(t.pnl, 6) for t in a.closed_trades] == [round(t.pnl, 6) for t in b.closed_trades]

    def test_broker_fills_use_adjusted_price_not_raw(self, isolated_env):
        """买入价应是复权后的开盘价,不是原始开盘价(锚点前的日子两者应不同)。"""
        days = business_days(date(2024, 1, 2), 5)
        insert_trade_cal(isolated_env, days)
        import neckline.calendar as cal

        cal.reset_cache()
        raw_opens = {days[0]: 10.0, days[1]: 10.0, days[2]: 8.0, days[3]: 8.1, days[4]: 8.2}
        adj_factors = {days[0]: 100.0, days[1]: 100.0, days[2]: 120.0, days[3]: 120.0, days[4]: 120.0}
        for d in days:
            o = raw_opens[d]
            write_daily_fixture(
                isolated_env, "daily", d,
                [{"ts_code": "600001.SH", "open": o, "high": o, "low": o, "close": o, "pre_close": o, "vol": 1000.0}],
            )
            write_daily_fixture(
                isolated_env, "adj_factor", d, [{"ts_code": "600001.SH", "adj_factor": adj_factors[d]}]
            )

        class BuyOnFirstDay(Strategy):
            def on_day(self, context: BacktestContext) -> List[Order]:
                if context.trade_date == days[0]:
                    return [Order(ts_code="600001.SH", side="buy", shares=100)]
                return []

        engine = BacktestEngine(
            BuyOnFirstDay(), start=days[0], end=days[-1], initial_cash=100_000,
            broker=Broker(slippage_bp=0),
            parquet_dir=isolated_env.parquet_dir,
        )
        engine.run()
        buy_fill = [r for r in engine.last_execution_results if r.status == "filled"][0]
        # 成交发生在 days[1](T+1),该日原始开盘价 10.0;但复权锚点在 days[-1]
        # (adj_factor=120),复权后应为 10.0*(100/120)=8.333...,不是原始的 10.0
        # (Broker 成交价四舍五入到分,断言也按同样精度比较)
        raw_open_day1 = raw_opens[days[1]]
        expected_qfq_open = round(raw_open_day1 * (adj_factors[days[1]] / adj_factors[days[-1]]), 2)
        assert buy_fill.fill_price == pytest.approx(expected_qfq_open)
        assert buy_fill.fill_price != pytest.approx(raw_open_day1)
