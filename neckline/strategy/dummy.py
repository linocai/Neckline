"""Dummy 策略(plan 0.7 验收用):每日等权买入 N 只、持有 K 个交易日。

无任何选股逻辑(按 ts_code 排序取前 N 只未持有的),纯粹用来跑通回测引擎全链路
(涨跌停锁 / T+1 / 停牌 / 滑点手续费 / 净值曲线)。阶段 1 真信号见 §2.2 母战法,
不在本文件。
"""

from __future__ import annotations

from typing import List

import polars as pl

from neckline.backtest.strategy import BacktestContext, Order, Strategy
from neckline.calendar import trading_days_between


class DummyStrategy(Strategy):
    def __init__(self, n_positions: int = 10, hold_days: int = 5, min_price: float = 2.0) -> None:
        self.n_positions = n_positions
        self.hold_days = hold_days
        self.min_price = min_price

    def on_day(self, context: BacktestContext) -> List[Order]:
        orders: List[Order] = []

        # 1) 持有满 hold_days 交易日(买入日计第 1 天)→ 卖出
        selling: set = set()
        for ts_code, pos in context.portfolio.positions.items():
            held = len(trading_days_between(pos.buy_date, context.trade_date))
            if held >= self.hold_days and context.portfolio.can_sell(ts_code, context.trade_date):
                orders.append(
                    Order(ts_code=ts_code, side="sell", shares=pos.shares, reason=f"持有{held}交易日满{self.hold_days}日退出")
                )
                selling.add(ts_code)

        # 2) 补齐到 n_positions 只(等权,按当前总权益/N 定单只目标金额)
        held_after = len(context.portfolio.positions) - len(selling)
        open_slots = self.n_positions - held_after
        if open_slots <= 0 or context.market_slice.is_empty():
            return orders

        held_codes = set(context.portfolio.positions.keys())
        candidates = (
            context.market_slice.filter(
                (~pl.col("ts_code").is_in(list(held_codes)))
                & (pl.col("close") >= self.min_price)
                & (pl.col("vol") > 0)
            )
            .sort("ts_code")
        )
        picks = candidates["ts_code"].to_list()[:open_slots]
        if not picks:
            return orders

        price_lookup = dict(zip(context.market_slice["ts_code"].to_list(), context.market_slice["close"].to_list()))
        equity = context.portfolio.total_equity(price_lookup)
        target_value = equity / self.n_positions
        for code in picks:
            orders.append(Order(ts_code=code, side="buy", target_value=target_value, reason=f"等权建仓(1/{self.n_positions})"))
        return orders


__all__ = ["DummyStrategy"]
