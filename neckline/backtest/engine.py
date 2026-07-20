"""事件驱动回测引擎(plan 0.7)。逐日循环:

    for T in trading_days:
        1. 用 T 日收盘价给昨日决策的持仓做 mark-to-market,记入净值曲线
        2. 把【只含 T 日及更早】的 BacktestContext 喂给 Strategy.on_day → 拿到 Order 列表
        3. Order 在 T+1 用 Broker 撮合成交(涨停/跌停/停牌/滑点/手续费)

"喂历史=回测、喂今日=报告、喂单票=问询台"(§2.6 同码三跑道)——本引擎是"喂历史"
跑道;`BacktestContext` 的结构是阶段 1+ 报告管线/问询台复用同一策略代码的契约点。

前复权(plan 0.5 "回测统一用前复权价"):`run()` 开头一次性把 [start,end] 全区间
的 daily 价格列(open/high/low/close/pre_close)前复权,锚点固定在【区间末尾 end】
——同一只票在 T 与 T' 两天的复权价用的是同一个 latest_adj_factor 基准,前后可
直接相减比较。**没有逐日重算锚点**(若按"每天各自的最新因子"逐日复权,同一只
票不同交易日算出来的复权价基准会不一致,买入价与卖出价隔着一次除权除息就对不
上,P&L 会错——这是本模块的关键正确性前提,勿改)。`limit_derived` 的涨跌停判
定仍读原始未复权表(涨跌停是对【真实成交价】的约束,不该用复权价判断)。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import polars as pl

from neckline.backtest.broker import Broker, ExecutionResult
from neckline.backtest.portfolio import Portfolio
from neckline.backtest.report import BacktestReport, build_report
from neckline.backtest.strategy import BacktestContext, Strategy
from neckline.calendar import trading_days_between
from neckline.data.adjust import apply_qfq
from neckline.data.market_data import get_market_slice, scan_table_range
from neckline.data.tushare_client import to_ts_code

logger = logging.getLogger(__name__)

_PRICE_COLS = ("open", "high", "low", "close", "pre_close")
_EMPTY_DAILY = pl.DataFrame()


def load_adjusted_daily(start: date, end: date, parquet_dir: Optional[Path] = None) -> pl.DataFrame:
    """[start,end] 全区间前复权 daily(锚点固定在 end,见模块 docstring)。
    `adj_factor` 缺失(理论不该发生,防御性处理)→ 降级为未复权价 + 告警。
    模块级函数:供 `BacktestEngine` 与外部预热缓存(如阶段 1 研究)同口径调用。
    """
    daily = scan_table_range("daily", start, end, parquet_dir=parquet_dir)
    if daily.is_empty():
        return daily
    adj = scan_table_range("adj_factor", start, end, parquet_dir=parquet_dir)
    if adj.is_empty():
        logger.warning("adj_factor 在 [%s,%s] 区间为空,回测降级为未复权价格", start, end)
        return daily
    merged = daily.join(
        adj.select(["ts_code", "trade_date", "adj_factor"]), on=["ts_code", "trade_date"], how="left"
    )
    adjusted = apply_qfq(merged, price_cols=_PRICE_COLS)
    qfq_cols = [f"{c}_qfq" for c in _PRICE_COLS]
    adjusted = adjusted.drop(list(_PRICE_COLS)).rename(dict(zip(qfq_cols, _PRICE_COLS)))
    return adjusted


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        start: date,
        end: date,
        initial_cash: float = 1_000_000.0,
        broker: Optional[Broker] = None,
        parquet_dir: Optional[Path] = None,
        adjusted_daily: Optional[pl.DataFrame] = None,
    ) -> None:
        self.strategy = strategy
        self.start = start
        self.end = end
        self.initial_cash = initial_cash
        self.broker = broker or Broker()
        self.parquet_dir = parquet_dir  # 测试注入用;None 时用 neckline.config.settings.parquet_dir
        # 预复权缓存注入(可选):[start,end] 全区间前复权 daily 由外部一次性算好传入,
        # 避免每次回测都重算全市场 qfq(阶段 1 参数网格反复回测同一窗口时是主要瓶颈;
        # 报告/问询跑道也可共享同一份)。**必须与本引擎的 [start,end] 同窗**(锚点=区间
        # 末尾,窗口不同则复权基准不同、P&L 会错),由 `load_adjusted_daily` 同口径产出。
        self._adjusted_daily = adjusted_daily

    def _load_adjusted_daily(self, start: date, end: date) -> pl.DataFrame:
        return load_adjusted_daily(start, end, parquet_dir=self.parquet_dir)

    def _make_history_fn(self, adjusted_daily: pl.DataFrame, as_of: date) -> Callable[[str, date, date], pl.DataFrame]:
        """`BacktestContext.history` 的实现:从已复权的整段缓存里切片,而不是重新
        打开 Parquet(避免二次用不同锚点复权造成前后不一致)。仍强制 end<=as_of
        (前视截断,行为对齐 `market_data.get_stock_history`)。"""

        def _history(code: str, h_start: date, h_end: date) -> pl.DataFrame:
            if adjusted_daily.is_empty():
                return adjusted_daily
            eff_end = h_end
            if h_end > as_of:
                logger.warning(
                    "BacktestContext.history(%s): end(%s) > as_of(%s),已截断到 as_of(疑似前视 bug)",
                    code, h_end, as_of,
                )
                eff_end = as_of
            ts_code = to_ts_code(code)
            if h_start > eff_end:
                return adjusted_daily.clear()
            return (
                adjusted_daily.filter(
                    (pl.col("ts_code") == ts_code) & (pl.col("trade_date") >= h_start) & (pl.col("trade_date") <= eff_end)
                )
                .sort("trade_date")
            )

        return _history

    def run(self) -> BacktestReport:
        trading_days = trading_days_between(self.start, self.end)
        if not trading_days:
            raise ValueError(f"[{self.start},{self.end}] 区间无交易日(检查交易日历是否已 backfill)")

        adjusted_daily = (
            self._adjusted_daily
            if self._adjusted_daily is not None
            else self._load_adjusted_daily(trading_days[0], trading_days[-1])
        )
        by_date: Dict[date, pl.DataFrame] = {}
        if not adjusted_daily.is_empty():
            for (d,), sub in adjusted_daily.group_by(["trade_date"]):
                by_date[d] = sub

        portfolio = Portfolio(self.initial_cash)
        equity_curve: List[Tuple[date, float]] = []
        all_results: List[ExecutionResult] = []

        limit_cache: Dict[date, pl.DataFrame] = {}

        def _limit_slice(d: date) -> pl.DataFrame:
            if d not in limit_cache:
                limit_cache[d] = get_market_slice(d, table="limit_derived", parquet_dir=self.parquet_dir)
            return limit_cache[d]

        for i, d in enumerate(trading_days):
            daily_slice = by_date.get(d, _EMPTY_DAILY)
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

            context = BacktestContext(
                trade_date=d,
                market_slice=daily_slice,
                limit_slice=_limit_slice(d),
                portfolio=portfolio,
                history=self._make_history_fn(adjusted_daily, as_of=d),
            )
            orders = self.strategy.on_day(context)

            if orders and i + 1 < len(trading_days):
                next_day = trading_days[i + 1]
                exec_slice = by_date.get(next_day, _EMPTY_DAILY)
                limit_slice_exec = _limit_slice(next_day)
                results = self.broker.execute(orders, exec_slice, limit_slice_exec, portfolio, next_day)
                all_results.extend(results)
                for r in results:
                    if r.status != "filled":
                        logger.debug("订单未成交 %s %s: %s(%s)", r.order.ts_code, r.order.side, r.status, r.detail)

        self.last_execution_results = all_results
        self.last_portfolio = portfolio  # 供调用方/测试查最终持仓明细(报告本身不带)
        return build_report(portfolio, equity_curve, self.initial_cash)


__all__ = ["BacktestEngine", "load_adjusted_daily"]
