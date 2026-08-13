"""⑫-A 资金流水四分类聚合(plan §五 V2-⑫-A,蓝图 5.3)。把 `parse.py` 新增的跳过行
留痕(`CashFlowEvent`:转入转出/分红/税费/其他)与 `reconcile.py` 已有的 FIFO 回合
净盈亏(交易盈亏)拼成**一份按周分桶、四类互不混同**的资金流水摘要。

**这是新增的聚合层,不是对账引擎骨架的一部分**——`review/parse.py` / `reconcile.py`
/ `material.py` / `store.py` 四个既有模块骨架不改(项目 CLAUDE.md 硬约束);本模块
只读它们已经产出的结果(`ParseResult.cash_flow_events` + `WeeklyReview.stats.
realized_pnl`),不重新解析交割单、不重新做 FIFO 闭合。

**核心纪律(蓝图 5.3 原文)**:「账户金额增加不得直接视为策略收益」——本模块的
`CashFlowSummary` 把四类金额分成四个独立字段,**没有**一个"账户净变动"合计字段,
就是不给调用方一个把四类混着相加、说成"策略收益"的机会。真要看账户余额变动,
去读交割单的「资金余额」列(每行都有,本模块不重算)。

**四类与本模块字段的对应**:
    · 转入转出(`CASH_FLOW_TRANSFER`)→ `transfer_in` / `transfer_out` / `transfer_net`
      (拆成入/出两个非负数 + 净额,比 plan 原文"转入转出"合一的颗粒度更细,
      纯增益、不违反"分开"的要求)。
    · 分红(`CASH_FLOW_DIVIDEND`)→ `dividend`。
    · 税费(`CASH_FLOW_TAX`)→ `tax`(原始符号,通常为负——扣款)。
    · 交易盈亏 → `trading_pnl`,**直接取 `WeeklyReview.stats.realized_pnl`**(已扣
      双边费用的 FIFO 回合净盈亏,`reconcile.compute_weekly_stats` 唯一源),本模块
      不重算。
    · `CASH_FLOW_OTHER`(如利息归本、指定交易零金额登记)→ `other`,如实归为
      "不属前三类"的资金流水,不强并入分红/税费。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence

from neckline.review.parse import (
    CASH_FLOW_DIVIDEND,
    CASH_FLOW_OTHER,
    CASH_FLOW_TAX,
    CASH_FLOW_TRANSFER,
    CashFlowEvent,
)
from neckline.review.reconcile import WeeklyReview, iso_week_key

#: 交易盈亏在本模块摘要里的分类标签(呼应 parse.py 的 `CASH_FLOW_*` 四兄弟,但
#: 它不出自跳过行,单独在此声明——见模块头「为什么不在 parse.py 声明」)。
CASH_FLOW_TRADING_PNL = "trading_pnl"

_EPS = 1e-9


@dataclass(frozen=True)
class CashFlowSummary:
    """一周的资金流水四分类摘要。四个金额字段(`transfer_net`/`dividend`/`tax`/
    `trading_pnl`)**故意不提供一个合计字段**——蓝图 5.3「账户金额增加不得直接
    视为策略收益」的字面落地:没有合计字段,调用方就没有一步把四类混算的近路。"""

    week: str
    transfer_in: float
    transfer_out: float          # 非负数(绝对值),"转出了多少",不是负数
    dividend: float
    tax: float                   # 原始符号(通常 ≤ 0,扣款)
    other: float
    other_event_count: int       # `other` 桶出现过几笔(供人工复核,如利息归本次数)
    trading_pnl: Optional[float]  # None = 本周没有已平仓回合(reconcile 未产出统计,不是 0)
    event_count: int             # 本周资金流水事件总笔数(转入转出+分红+税费+其他)

    @property
    def transfer_net(self) -> float:
        return round(self.transfer_in - self.transfer_out, 2)

    def to_dict(self) -> Dict[str, object]:
        return {
            "week": self.week,
            "transferIn": self.transfer_in,
            "transferOut": self.transfer_out,
            "transferNet": self.transfer_net,
            "dividend": self.dividend,
            "tax": self.tax,
            "other": self.other,
            "otherEventCount": self.other_event_count,
            "tradingPnl": self.trading_pnl,
            "eventCount": self.event_count,
            "note": (
                "四类分开列示,均不计入彼此;账户资金余额的变动是这四类之和再加"
                "已有持仓的浮动盈亏,不能拿本摘要的任一字段单独当作「策略收益」"
                "(蓝图 5.3)。"
            ),
        }


def bucket_events_by_week(events: Sequence[CashFlowEvent]) -> Dict[str, List[CashFlowEvent]]:
    """按 ISO 周(`reconcile.iso_week_key` 同一份实现,不另写日期→周的映射)分桶。"""
    out: Dict[str, List[CashFlowEvent]] = {}
    for e in events:
        out.setdefault(iso_week_key(e.trade_date), []).append(e)
    return out


def build_weekly_cash_flow_summary(
    week: str,
    events: Sequence[CashFlowEvent],
    review: Optional[WeeklyReview] = None,
) -> CashFlowSummary:
    """拼一周的四分类摘要。`events` 应已用 `bucket_events_by_week` 按周过滤好
    (本函数不再按 `week` 二次过滤,调用方传什么就信什么——同批调用方通常是
    `bucket_events_by_week(...)` 的返回值,已经保证了这一点)。

    `review`:该周已算好的 `WeeklyReview`(`run_weekly_review` 产出),取
    `stats.realized_pnl` 作交易盈亏——**不重算**。`None` / `stats is None`(该周
    没有任何已平仓回合)→ `trading_pnl=None`,与"交易盈亏为 0"分开(「没有」≠
    「算出来是 0」)。"""
    transfer_in = transfer_out = dividend = tax = other = 0.0
    other_n = 0
    for e in events:
        if e.kind == CASH_FLOW_TRANSFER:
            if e.amount >= 0:
                transfer_in += e.amount
            else:
                transfer_out += -e.amount
        elif e.kind == CASH_FLOW_DIVIDEND:
            dividend += e.amount
        elif e.kind == CASH_FLOW_TAX:
            tax += e.amount
        elif e.kind == CASH_FLOW_OTHER:
            other += e.amount
            other_n += 1
        else:
            # 未知分类:如实并入 other 而不是静默丢弃(不可能发生——
            # `_CASH_FLOW_KIND_BY_NAME` 兜底已经把陌生业务名称也判成 CASH_FLOW_OTHER
            # ——这里是双重防御,防未来 parse.py 新增分类而本模块忘了同步)。
            other += e.amount
            other_n += 1

    trading_pnl = review.stats.realized_pnl if (review is not None and review.stats is not None) else None

    return CashFlowSummary(
        week=week,
        transfer_in=round(transfer_in, 2), transfer_out=round(transfer_out, 2),
        dividend=round(dividend, 2), tax=round(tax, 2), other=round(other, 2),
        other_event_count=other_n, trading_pnl=trading_pnl, event_count=len(events),
    )


def build_all_weekly_summaries(
    events: Sequence[CashFlowEvent],
    reviews_by_week: Optional[Dict[str, WeeklyReview]] = None,
) -> List[CashFlowSummary]:
    """`events` 里出现过的每一个 ISO 周各出一份摘要(升序)。`reviews_by_week` 缺失
    的周 → `trading_pnl=None`(如实标"没有已平仓回合数据可对照",不是没有资金流水
    ——那一周仍会正常出转入转出/分红/税费三项)。"""
    buckets = bucket_events_by_week(events)
    reviews_by_week = reviews_by_week or {}
    return [
        build_weekly_cash_flow_summary(week, buckets[week], reviews_by_week.get(week))
        for week in sorted(buckets)
    ]


__all__ = [
    "CASH_FLOW_TRADING_PNL",
    "CashFlowSummary",
    "bucket_events_by_week",
    "build_weekly_cash_flow_summary",
    "build_all_weekly_summaries",
]
