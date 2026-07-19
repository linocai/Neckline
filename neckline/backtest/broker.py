"""撮合层(plan 0.7)。把 Strategy 产出的 `Order` 撮合成实际成交,落实四条约束:

    · 涨停买不进(T+1 执行日 `limit_derived.status == 'limit_up'` → 拒买)
    · 跌停卖不出(T+1 执行日 `limit_derived.status == 'limit_down'` → 拒卖)
    · 停牌跳过(T+1 执行日 daily 无该 ts_code 行 → 拒单)
    · 滑点 + 手续费(佣金双边、印花税单边卖出、过户费双边;成交价 = T+1 开盘价
      按滑点方向调整)

成交价模型(阶段 0 简化,无分钟线数据,§3.2):T 日策略决策 → T+1 开盘价成交
(daily-bar 回测的标准简化,买入价上浮滑点、卖出价下浮滑点,不对称不利成交)。
A 股买入按整百股(一手)取整,卖出按订单给定股数(通常是清仓,已是合法股数)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

import polars as pl

from neckline.backtest.portfolio import Portfolio
from neckline.backtest.strategy import Order

logger = logging.getLogger(__name__)

LOT_SIZE = 100  # A 股一手 = 100 股


@dataclass
class ExecutionResult:
    order: Order
    status: str  # filled | blocked_limit_up | blocked_limit_down | suspended | insufficient_cash | no_position | invalid
    fill_price: Optional[float] = None
    shares: Optional[int] = None
    fees: Optional[float] = None
    detail: str = ""


class Broker:
    def __init__(
        self,
        commission_rate: float = 0.00025,
        min_commission: float = 5.0,
        stamp_duty_rate: float = 0.0005,  # 卖出单边(2023 减半后现行税率)
        transfer_fee_rate: float = 0.00001,  # 过户费双边(沪市为主,简化统一施加)
        slippage_bp: float = 10.0,  # 万分之十 = 0.1%
    ) -> None:
        self.commission_rate = commission_rate
        self.min_commission = min_commission
        self.stamp_duty_rate = stamp_duty_rate
        self.transfer_fee_rate = transfer_fee_rate
        self.slippage_bp = slippage_bp

    def _buy_fees(self, value: float) -> float:
        return max(value * self.commission_rate, self.min_commission) + value * self.transfer_fee_rate

    def _sell_fees(self, value: float) -> float:
        return (
            max(value * self.commission_rate, self.min_commission)
            + value * self.transfer_fee_rate
            + value * self.stamp_duty_rate
        )

    def execute(
        self,
        orders: List[Order],
        exec_slice: pl.DataFrame,
        limit_slice: pl.DataFrame,
        portfolio: Portfolio,
        trade_date: date,
    ) -> List[ExecutionResult]:
        """`exec_slice`/`limit_slice` 是【执行日】(T+1,非策略决策的 T 日)的数据。"""
        open_lookup: Dict[str, float] = {}
        if not exec_slice.is_empty() and "open" in exec_slice.columns:
            open_lookup = dict(zip(exec_slice["ts_code"].to_list(), exec_slice["open"].to_list()))

        limit_up_codes = set()
        limit_down_codes = set()
        if not limit_slice.is_empty():
            limit_up_codes = set(limit_slice.filter(pl.col("status") == "limit_up")["ts_code"].to_list())
            limit_down_codes = set(limit_slice.filter(pl.col("status") == "limit_down")["ts_code"].to_list())

        results: List[ExecutionResult] = []
        for order in orders:
            results.append(self._execute_one(order, open_lookup, limit_up_codes, limit_down_codes, portfolio, trade_date))
        return results

    def _execute_one(
        self,
        order: Order,
        open_lookup: Dict[str, float],
        limit_up_codes: set,
        limit_down_codes: set,
        portfolio: Portfolio,
        trade_date: date,
    ) -> ExecutionResult:
        if order.side not in ("buy", "sell"):
            return ExecutionResult(order, "invalid", detail=f"未知 side={order.side!r}")

        open_price = open_lookup.get(order.ts_code)
        if open_price is None or open_price <= 0:
            return ExecutionResult(order, "suspended", detail="执行日无成交数据(停牌/未上市/已退市)")

        if order.side == "buy":
            if order.ts_code in limit_up_codes:
                return ExecutionResult(order, "blocked_limit_up", detail="执行日涨停,买不进")
            fill_price = round(open_price * (1 + self.slippage_bp / 10000), 2)
            shares = order.shares
            if shares is None:
                if not order.target_value or order.target_value <= 0:
                    return ExecutionResult(order, "invalid", detail="buy 订单缺 shares 与 target_value")
                shares = int(order.target_value // fill_price // LOT_SIZE) * LOT_SIZE
            else:
                shares = (shares // LOT_SIZE) * LOT_SIZE
            if shares < LOT_SIZE:
                return ExecutionResult(order, "invalid", detail="换算后不足一手(100股),订单作废")

            # 现金不够时逐手(100股)下调直到成本(含费)不超现金,而不是用近似费率
            # 一次性反推再回填——回填费率若忽略最低佣金 5 元下限,小额订单会算出
            # "刚好够"但真实 fees(含下限)一算又超一点,炸在 Portfolio.apply_buy
            # 的现金校验上(施工时 code review 发现,已用逐手下调法根治)。
            value = shares * fill_price
            fees = self._buy_fees(value)
            while shares >= LOT_SIZE and value + fees > portfolio.cash + 1e-6:
                shares -= LOT_SIZE
                value = shares * fill_price
                fees = self._buy_fees(value)
            if shares < LOT_SIZE:
                return ExecutionResult(order, "insufficient_cash", detail=f"现金不足(余{portfolio.cash:.2f})")
            portfolio.apply_buy(order.ts_code, shares, fill_price, fees, trade_date, order.reason)
            return ExecutionResult(order, "filled", fill_price=fill_price, shares=shares, fees=fees)

        # side == "sell"
        if order.ts_code in limit_down_codes:
            return ExecutionResult(order, "blocked_limit_down", detail="执行日跌停,卖不出")
        if not portfolio.can_sell(order.ts_code, trade_date):
            return ExecutionResult(order, "no_position", detail="无持仓或未满 T+1(买入当日不可卖)")
        pos = portfolio.positions[order.ts_code]
        shares = order.shares if order.shares is not None else pos.shares
        shares = min(shares, pos.shares)
        if shares <= 0:
            return ExecutionResult(order, "invalid", detail="卖出股数非法")
        fill_price = round(open_price * (1 - self.slippage_bp / 10000), 2)
        value = shares * fill_price
        fees = self._sell_fees(value)
        closed = portfolio.apply_sell(order.ts_code, shares, fill_price, fees, trade_date, order.reason)
        return ExecutionResult(order, "filled", fill_price=fill_price, shares=shares, fees=fees, detail=f"pnl={closed.pnl:.2f}")


__all__ = ["Broker", "ExecutionResult", "LOT_SIZE"]
