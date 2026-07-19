"""Broker 撮合单测(plan 0.7 验收铁律):涨停买不进、跌停卖不出、停牌跳过、
滑点 + 手续费。手工构造执行日切片,不依赖真实数据。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from neckline.backtest.broker import LOT_SIZE, Broker
from neckline.backtest.portfolio import Portfolio
from neckline.backtest.strategy import Order

EXEC_DAY = date(2024, 1, 3)


def _exec_slice(rows):
    return pl.DataFrame(rows)


def _limit_slice(rows):
    if not rows:
        return pl.DataFrame(schema={"ts_code": pl.Utf8, "status": pl.Utf8})
    return pl.DataFrame(rows)


class TestLimitUpBlocksBuy:
    def test_buy_blocked_when_limit_up(self):
        broker = Broker()
        p = Portfolio(1_000_000)
        orders = [Order(ts_code="600001.SH", side="buy", target_value=10_000)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 11.0}])
        limit_slice = _limit_slice([{"ts_code": "600001.SH", "status": "limit_up"}])
        results = broker.execute(orders, exec_slice, limit_slice, p, EXEC_DAY)
        assert results[0].status == "blocked_limit_up"
        assert "600001.SH" not in p.positions
        assert p.cash == 1_000_000  # 完全没扣钱

    def test_buy_succeeds_when_not_limit_up(self):
        broker = Broker(slippage_bp=0)
        p = Portfolio(1_000_000)
        orders = [Order(ts_code="600001.SH", side="buy", target_value=10_000)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 10.0}])
        limit_slice = _limit_slice([])
        results = broker.execute(orders, exec_slice, limit_slice, p, EXEC_DAY)
        assert results[0].status == "filled"
        assert "600001.SH" in p.positions


class TestLimitDownBlocksSell:
    def test_sell_blocked_when_limit_down(self):
        broker = Broker()
        p = Portfolio(1_000_000)
        p.apply_buy("600001.SH", 100, 10.0, fees=5.0, trade_date=date(2024, 1, 2))
        orders = [Order(ts_code="600001.SH", side="sell", shares=100)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 9.0}])
        limit_slice = _limit_slice([{"ts_code": "600001.SH", "status": "limit_down"}])
        results = broker.execute(orders, exec_slice, limit_slice, p, EXEC_DAY)
        assert results[0].status == "blocked_limit_down"
        assert "600001.SH" in p.positions  # 持仓原封不动

    def test_sell_succeeds_when_not_limit_down(self):
        broker = Broker(slippage_bp=0)
        p = Portfolio(1_000_000)
        p.apply_buy("600001.SH", 100, 10.0, fees=5.0, trade_date=date(2024, 1, 2))
        orders = [Order(ts_code="600001.SH", side="sell", shares=100)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 11.0}])
        limit_slice = _limit_slice([])
        results = broker.execute(orders, exec_slice, limit_slice, p, EXEC_DAY)
        assert results[0].status == "filled"
        assert "600001.SH" not in p.positions


class TestSuspensionSkipped:
    def test_buy_skipped_when_no_row_for_code(self):
        broker = Broker()
        p = Portfolio(1_000_000)
        orders = [Order(ts_code="600002.SH", side="buy", target_value=10_000)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 10.0}])  # 600002 停牌,当日无行
        limit_slice = _limit_slice([])
        results = broker.execute(orders, exec_slice, limit_slice, p, EXEC_DAY)
        assert results[0].status == "suspended"
        assert p.cash == 1_000_000

    def test_sell_skipped_when_suspended(self):
        broker = Broker()
        p = Portfolio(1_000_000)
        p.apply_buy("600002.SH", 100, 10.0, fees=5.0, trade_date=date(2024, 1, 2))
        orders = [Order(ts_code="600002.SH", side="sell", shares=100)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 10.0}])  # 600002 当日无行
        limit_slice = _limit_slice([])
        results = broker.execute(orders, exec_slice, limit_slice, p, EXEC_DAY)
        assert results[0].status == "suspended"
        assert "600002.SH" in p.positions  # 未成交,持仓不变


class TestT1EnforcedByBroker:
    def test_sell_same_day_as_buy_rejected(self):
        broker = Broker()
        p = Portfolio(1_000_000)
        p.apply_buy("600001.SH", 100, 10.0, fees=5.0, trade_date=EXEC_DAY)  # 买入日 == 执行日
        orders = [Order(ts_code="600001.SH", side="sell", shares=100)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 11.0}])
        results = broker.execute(orders, exec_slice, _limit_slice([]), p, EXEC_DAY)
        assert results[0].status == "no_position"
        assert "600001.SH" in p.positions


class TestSlippageAndFees:
    def test_buy_fill_price_includes_unfavorable_slippage(self):
        broker = Broker(slippage_bp=100)  # 1% 滑点,数值大便于断言
        p = Portfolio(1_000_000)
        orders = [Order(ts_code="600001.SH", side="buy", target_value=100_000)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 10.0}])
        results = broker.execute(orders, exec_slice, _limit_slice([]), p, EXEC_DAY)
        assert results[0].fill_price == pytest.approx(10.0 * 1.01)  # 买入价上浮

    def test_sell_fill_price_includes_unfavorable_slippage(self):
        broker = Broker(slippage_bp=100)
        p = Portfolio(1_000_000)
        p.apply_buy("600001.SH", 100, 10.0, fees=5.0, trade_date=date(2024, 1, 2))
        orders = [Order(ts_code="600001.SH", side="sell", shares=100)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 10.0}])
        results = broker.execute(orders, exec_slice, _limit_slice([]), p, EXEC_DAY)
        assert results[0].fill_price == pytest.approx(10.0 * 0.99)  # 卖出价下浮

    def test_buy_shares_rounded_down_to_lot_size(self):
        broker = Broker(slippage_bp=0)
        p = Portfolio(1_000_000)
        # target_value 换算出 250 股 → 应向下取整到 200(整百股)
        orders = [Order(ts_code="600001.SH", side="buy", target_value=2500.0)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 10.0}])
        results = broker.execute(orders, exec_slice, _limit_slice([]), p, EXEC_DAY)
        assert results[0].shares % LOT_SIZE == 0
        assert results[0].shares == 200

    def test_sell_stamp_duty_only_applies_to_sell_not_buy(self):
        broker = Broker(commission_rate=0, min_commission=0, transfer_fee_rate=0, stamp_duty_rate=0.0005, slippage_bp=0)
        p = Portfolio(1_000_000)
        buy_orders = [Order(ts_code="600001.SH", side="buy", target_value=10_000)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 10.0}])
        buy_results = broker.execute(buy_orders, exec_slice, _limit_slice([]), p, EXEC_DAY)
        assert buy_results[0].fees == pytest.approx(0.0)  # 买入不收印花税,佣金/过户费也设 0

        sell_orders = [Order(ts_code="600001.SH", side="sell", shares=buy_results[0].shares)]
        sell_results = broker.execute(sell_orders, exec_slice, _limit_slice([]), p, date(2024, 1, 4))
        expected_stamp = buy_results[0].shares * 10.0 * 0.0005
        assert sell_results[0].fees == pytest.approx(expected_stamp)

    def test_min_commission_floor_applies(self):
        broker = Broker(commission_rate=0.00025, min_commission=5.0, transfer_fee_rate=0, stamp_duty_rate=0, slippage_bp=0)
        p = Portfolio(1_000_000)
        # 100 股 * 10 元 = 1000 元,佣金率 0.025% = 0.25 元,应被 5 元下限顶住
        orders = [Order(ts_code="600001.SH", side="buy", target_value=1000.0)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 10.0}])
        results = broker.execute(orders, exec_slice, _limit_slice([]), p, EXEC_DAY)
        assert results[0].fees == pytest.approx(5.0)


class TestInsufficientCash:
    def test_buy_capped_by_available_cash(self):
        broker = Broker(slippage_bp=0, commission_rate=0, min_commission=0, transfer_fee_rate=0)
        p = Portfolio(1050.0)  # 只够买 100 股(10 元/股)加一点余量,不够 target_value 要求的 200 股
        orders = [Order(ts_code="600001.SH", side="buy", target_value=2000.0)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 10.0}])
        results = broker.execute(orders, exec_slice, _limit_slice([]), p, EXEC_DAY)
        assert results[0].status == "filled"
        assert results[0].shares == 100

    def test_buy_rejected_when_cash_below_one_lot(self):
        broker = Broker(slippage_bp=0)
        p = Portfolio(100.0)  # 连 100 股都买不起
        orders = [Order(ts_code="600001.SH", side="buy", target_value=2000.0)]
        exec_slice = _exec_slice([{"ts_code": "600001.SH", "open": 10.0}])
        results = broker.execute(orders, exec_slice, _limit_slice([]), p, EXEC_DAY)
        assert results[0].status == "insufficient_cash"
