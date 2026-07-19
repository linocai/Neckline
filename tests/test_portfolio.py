"""Portfolio 单测(plan 0.7):T+1 锁定、持仓/现金账、已实现盈亏。"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.backtest.portfolio import Portfolio


class TestT1Lock:
    def test_cannot_sell_same_day_as_buy(self):
        p = Portfolio(100_000)
        p.apply_buy("600001.SH", 100, 10.0, fees=5.0, trade_date=date(2024, 1, 2))
        assert p.can_sell("600001.SH", date(2024, 1, 2)) is False

    def test_can_sell_next_trading_day(self):
        p = Portfolio(100_000)
        p.apply_buy("600001.SH", 100, 10.0, fees=5.0, trade_date=date(2024, 1, 2))
        assert p.can_sell("600001.SH", date(2024, 1, 3)) is True

    def test_cannot_sell_without_position(self):
        p = Portfolio(100_000)
        assert p.can_sell("600001.SH", date(2024, 1, 3)) is False


class TestBuySell:
    def test_buy_deducts_cash_including_fees(self):
        p = Portfolio(100_000)
        p.apply_buy("600001.SH", 100, 10.0, fees=5.0, trade_date=date(2024, 1, 2))
        assert p.cash == pytest.approx(100_000 - 1000 - 5)
        assert p.positions["600001.SH"].shares == 100

    def test_buy_insufficient_cash_raises(self):
        p = Portfolio(100)
        with pytest.raises(ValueError):
            p.apply_buy("600001.SH", 100, 10.0, fees=5.0, trade_date=date(2024, 1, 2))

    def test_sell_full_position_closes_it_and_records_pnl(self):
        p = Portfolio(100_000)
        p.apply_buy("600001.SH", 100, 10.0, fees=5.0, trade_date=date(2024, 1, 2))
        closed = p.apply_sell("600001.SH", 100, 11.0, fees=6.0, trade_date=date(2024, 1, 3))
        assert "600001.SH" not in p.positions
        assert closed.pnl == pytest.approx(100 * (11.0 - 10.0) - 5.0 - 6.0)
        assert p.closed_trades[-1] is closed

    def test_sell_partial_position_keeps_remainder(self):
        p = Portfolio(100_000)
        p.apply_buy("600001.SH", 200, 10.0, fees=10.0, trade_date=date(2024, 1, 2))
        p.apply_sell("600001.SH", 100, 11.0, fees=5.0, trade_date=date(2024, 1, 3))
        assert p.positions["600001.SH"].shares == 100

    def test_sell_more_than_held_raises(self):
        p = Portfolio(100_000)
        p.apply_buy("600001.SH", 100, 10.0, fees=5.0, trade_date=date(2024, 1, 2))
        with pytest.raises(ValueError):
            p.apply_sell("600001.SH", 200, 11.0, fees=5.0, trade_date=date(2024, 1, 3))

    def test_sell_without_position_raises(self):
        p = Portfolio(100_000)
        with pytest.raises(ValueError):
            p.apply_sell("600001.SH", 100, 11.0, fees=5.0, trade_date=date(2024, 1, 3))


class TestValuation:
    def test_total_equity_cash_plus_market_value(self):
        p = Portfolio(100_000)
        p.apply_buy("600001.SH", 100, 10.0, fees=5.0, trade_date=date(2024, 1, 2))
        equity = p.total_equity({"600001.SH": 12.0})
        assert equity == pytest.approx(p.cash + 100 * 12.0)

    def test_missing_price_falls_back_to_buy_price(self):
        p = Portfolio(100_000)
        p.apply_buy("600001.SH", 100, 10.0, fees=5.0, trade_date=date(2024, 1, 2))
        equity = p.total_equity({})  # 停牌/取不到价
        assert equity == pytest.approx(p.cash + 100 * 10.0)
