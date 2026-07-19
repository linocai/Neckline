"""组合状态(plan 0.7):持仓 / 现金 / T+1 锁定。

T+1 铁律:A 股当日买入不可当日卖出,最早次一交易日才能卖(`can_sell` 判据是
`buy_date < as_of_date`,严格早于,不含当日)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List


@dataclass
class Position:
    ts_code: str
    shares: int
    buy_price: float       # 每股成交价(不含费)
    buy_date: date
    buy_fees: float = 0.0  # 建仓费用(佣金+过户费等,不含印花税——印花税只在卖出收)

    @property
    def cost_basis(self) -> float:
        """建仓总成本(含费),用于已实现盈亏计算。"""
        return self.shares * self.buy_price + self.buy_fees


@dataclass
class ClosedTrade:
    """一次完整回合(买→卖)的已实现结果,供 BacktestReport 算胜率/盈亏比/盈利因子。"""

    ts_code: str
    buy_date: date
    sell_date: date
    shares: int
    buy_price: float
    sell_price: float
    buy_fees: float
    sell_fees: float
    reason: str = ""

    @property
    def pnl(self) -> float:
        return self.shares * (self.sell_price - self.buy_price) - self.buy_fees - self.sell_fees

    @property
    def cost_basis(self) -> float:
        return self.shares * self.buy_price + self.buy_fees

    @property
    def pnl_pct(self) -> float:
        cb = self.cost_basis
        return self.pnl / cb if cb else 0.0

    @property
    def hold_calendar_days(self) -> int:
        return (self.sell_date - self.buy_date).days


@dataclass
class TradeRecord:
    """原始成交流水(买/卖各一条,不做回合配对——回合配对结果在 `closed_trades`)。"""

    ts_code: str
    side: str
    trade_date: date
    shares: int
    price: float
    fees: float
    reason: str = ""


class Portfolio:
    def __init__(self, initial_cash: float) -> None:
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: Dict[str, Position] = {}
        self.trade_log: List[TradeRecord] = []
        self.closed_trades: List[ClosedTrade] = []

    # —— T+1 判定 ——————————————————————————————————————————————————

    def can_sell(self, ts_code: str, as_of_date: date) -> bool:
        pos = self.positions.get(ts_code)
        return pos is not None and pos.buy_date < as_of_date

    # —— 成交落账(只由 Broker 调用,Strategy 不直接碰)——————————————————

    def apply_buy(self, ts_code: str, shares: int, price: float, fees: float, trade_date: date, reason: str = "") -> None:
        cost = shares * price + fees
        if cost > self.cash + 1e-6:
            raise ValueError(f"apply_buy: 现金不足(需 {cost:.2f},余 {self.cash:.2f})")
        self.cash -= cost
        if ts_code in self.positions:
            # 加仓:按加权平均成本合并(阶段 0 dummy 策略不触发,预留给阶段 1)
            old = self.positions[ts_code]
            total_shares = old.shares + shares
            avg_price = (old.shares * old.buy_price + shares * price) / total_shares
            self.positions[ts_code] = Position(
                ts_code=ts_code,
                shares=total_shares,
                buy_price=avg_price,
                buy_date=trade_date,  # 加仓后 T+1 锁定按最新一笔重新起算(保守)
                buy_fees=old.buy_fees + fees,
            )
        else:
            self.positions[ts_code] = Position(
                ts_code=ts_code, shares=shares, buy_price=price, buy_date=trade_date, buy_fees=fees
            )
        self.trade_log.append(TradeRecord(ts_code, "buy", trade_date, shares, price, fees, reason))

    def apply_sell(self, ts_code: str, shares: int, price: float, fees: float, trade_date: date, reason: str = "") -> ClosedTrade:
        pos = self.positions.get(ts_code)
        if pos is None:
            raise ValueError(f"apply_sell: 无持仓 {ts_code}")
        if shares > pos.shares:
            raise ValueError(f"apply_sell: 卖出股数 {shares} 超过持仓 {pos.shares}({ts_code})")

        proceeds = shares * price - fees
        self.cash += proceeds

        # 按比例分摊建仓费用到本次卖出的股数(部分卖出场景;dummy 策略只整仓卖,
        # 该分支等价于全额)。
        buy_fees_share = pos.buy_fees * (shares / pos.shares)
        closed = ClosedTrade(
            ts_code=ts_code,
            buy_date=pos.buy_date,
            sell_date=trade_date,
            shares=shares,
            buy_price=pos.buy_price,
            sell_price=price,
            buy_fees=buy_fees_share,
            sell_fees=fees,
            reason=reason,
        )
        self.closed_trades.append(closed)
        self.trade_log.append(TradeRecord(ts_code, "sell", trade_date, shares, price, fees, reason))

        remaining = pos.shares - shares
        if remaining == 0:
            del self.positions[ts_code]
        else:
            self.positions[ts_code] = Position(
                ts_code=ts_code,
                shares=remaining,
                buy_price=pos.buy_price,
                buy_date=pos.buy_date,
                buy_fees=pos.buy_fees - buy_fees_share,
            )
        return closed

    # —— 估值 ——————————————————————————————————————————————————————

    def market_value(self, price_lookup: Dict[str, float]) -> float:
        total = 0.0
        for ts_code, pos in self.positions.items():
            price = price_lookup.get(ts_code)
            if price is None:
                price = pos.buy_price  # 停牌等取不到当日价 → 按最后已知成本价估值(保守占位)
            total += pos.shares * price
        return total

    def total_equity(self, price_lookup: Dict[str, float]) -> float:
        return self.cash + self.market_value(price_lookup)


__all__ = ["Position", "ClosedTrade", "TradeRecord", "Portfolio"]
