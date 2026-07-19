"""事件驱动回测引擎(plan 0.7)。逐日循环:

    for T in trading_days:
        1. 用 T 日收盘价给昨日决策的持仓做 mark-to-market,记入净值曲线
        2. 把【只含 T 日及更早】的 BacktestContext 喂给 Strategy.on_day → 拿到 Order 列表
        3. Order 在 T+1 用 Broker 撮合成交(涨停/跌停/停牌/滑点/手续费)

"喂历史=回测、喂今日=报告、喂单票=问询台"(§2.6 同码三跑道)——本引擎是"喂历史"
跑道;`BacktestContext` 的结构是阶段 1+ 报告管线/问询台复用同一策略代码的契约点。
"""

from __future__ import annotations

import logging
from datetime import date
from functools import partial
from pathlib import Path
from typing import List, Optional, Tuple

import polars as pl

from neckline.backtest.broker import Broker, ExecutionResult
from neckline.backtest.portfolio import Portfolio
from neckline.backtest.report import BacktestReport, build_report
from neckline.backtest.strategy import BacktestContext, Strategy
from neckline.calendar import trading_days_between
from neckline.data.market_data import get_market_slice, get_stock_history

logger = logging.getLogger(__name__)


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        start: date,
        end: date,
        initial_cash: float = 1_000_000.0,
        broker: Optional[Broker] = None,
        parquet_dir: Optional[Path] = None,
    ) -> None:
        self.strategy = strategy
        self.start = start
        self.end = end
        self.initial_cash = initial_cash
        self.broker = broker or Broker()
        self.parquet_dir = parquet_dir  # 测试注入用;None 时用 neckline.config.settings.parquet_dir

    def run(self) -> BacktestReport:
        trading_days = trading_days_between(self.start, self.end)
        if not trading_days:
            raise ValueError(f"[{self.start},{self.end}] 区间无交易日(检查交易日历是否已 backfill)")

        portfolio = Portfolio(self.initial_cash)
        equity_curve: List[Tuple[date, float]] = []
        all_results: List[ExecutionResult] = []

        market_cache: dict = {}

        def _get_slice(d: date, table: str) -> pl.DataFrame:
            key = (d, table)
            if key not in market_cache:
                market_cache[key] = get_market_slice(d, table=table, parquet_dir=self.parquet_dir)
            return market_cache[key]

        for i, d in enumerate(trading_days):
            daily_slice = _get_slice(d, "daily")
            if daily_slice.is_empty():
                logger.warning("交易日 %s 无 daily 数据(数据缺口),按无成交处理,净值沿用上一日", d)
                if equity_curve:
                    equity_curve.append((d, equity_curve[-1][1]))
                else:
                    equity_curve.append((d, self.initial_cash))
                continue

            price_lookup = dict(zip(daily_slice["ts_code"].to_list(), daily_slice["close"].to_list()))
            equity_today = portfolio.total_equity(price_lookup)
            equity_curve.append((d, equity_today))

            limit_slice_today = _get_slice(d, "limit_derived")
            history_fn = partial(get_stock_history, as_of=d, parquet_dir=self.parquet_dir)
            context = BacktestContext(
                trade_date=d,
                market_slice=daily_slice,
                limit_slice=limit_slice_today,
                portfolio=portfolio,
                history=history_fn,
            )
            orders = self.strategy.on_day(context)

            if orders and i + 1 < len(trading_days):
                next_day = trading_days[i + 1]
                exec_slice = _get_slice(next_day, "daily")
                limit_slice_exec = _get_slice(next_day, "limit_derived")
                results = self.broker.execute(orders, exec_slice, limit_slice_exec, portfolio, next_day)
                all_results.extend(results)
                for r in results:
                    if r.status != "filled":
                        logger.debug("订单未成交 %s %s: %s(%s)", r.order.ts_code, r.order.side, r.status, r.detail)

        self.last_execution_results = all_results
        return build_report(portfolio, equity_curve, self.initial_cash)


__all__ = ["BacktestEngine"]
