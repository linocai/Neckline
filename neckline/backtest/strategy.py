"""策略接口(plan 0.7)。同码三跑道(§2.6)的源头契约——`generate_signals`/`on_day`
拿到的 `BacktestContext` 只暴露"当前模拟日 T 及更早"的数据,阶段 1+ 实现母战法
信号时,报告管线(喂今日)与问询台(喂单票)复用同一份策略代码,只是喂不同的
`BacktestContext`。

阶段 0 只定义骨架接口 + `neckline.strategy.dummy.DummyStrategy` 验证跑通,真信号
是阶段 1 的工作。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Callable, List, Optional

import polars as pl

from neckline.backtest.portfolio import Portfolio


@dataclass
class Order:
    """策略产出的订单意图。`shares` 与 `target_value` 二选一:
    - `shares`:精确股数(卖出常用,如"清仓 pos.shares")。
    - `target_value`:目标金额(买入常用,Broker 按 T+1 成交价换算整百股,超出
      可用现金会被裁剪)。
    """

    ts_code: str
    side: str  # "buy" | "sell"
    shares: Optional[int] = None
    target_value: Optional[float] = None
    reason: str = ""


@dataclass
class BacktestContext:
    """喂给策略的"当日快照"。铁律(§3.8 无前视偏差):`market_slice` 只含
    `trade_date` 当天的横截面,`history` 是已绑定 `as_of=trade_date` 的历史查询
    (调用方传更晚的 end 会被 market_data 层截断 + 告警,见 0.6)。"""

    trade_date: date
    market_slice: pl.DataFrame  # 当日全市场 daily 横截面(至少含 ts_code/open/high/low/close/pre_close/pct_chg)
    limit_slice: pl.DataFrame  # 当日 limit_derived 命中行(涨跌停/炸板)
    portfolio: Portfolio  # 只读视角(策略不应直接改 portfolio,应通过返回 Order)
    history: Callable[[str, date, date], pl.DataFrame]  # (code, start, end) -> 历史行情,已做前视截断


class Strategy(ABC):
    """策略基类。`on_day` 每个交易日调用一次,返回当日决策的订单列表(T 日收盘后
    决策,实际成交价由 Broker 按 T+1 规则撮合,见 broker.py)。"""

    @abstractmethod
    def on_day(self, context: BacktestContext) -> List[Order]:
        raise NotImplementedError


__all__ = ["Order", "BacktestContext", "Strategy"]
