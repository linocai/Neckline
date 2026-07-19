"""回测引擎端到端单测(plan 0.7)。用隔离 Parquet + DB 灌一段小样本数据,验证
Strategy → Broker → Portfolio → Report 全链路接起来且遵守 T+1 / 前视截断。
"""

from __future__ import annotations

from datetime import date
from typing import List

import pytest

from neckline.backtest import BacktestContext, BacktestEngine, Order, Strategy
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
