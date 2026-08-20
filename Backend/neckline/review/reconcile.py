"""对账引擎(V2.5.0 S11 收口)。输入已解析的 `RawTrade` 流 → **FIFO 闭合回合** →
单周统计 + 强制复盘线判定,按 ISO 周(`YYYY-Www`)分桶输出 `WeeklyReview`。

🔴 **本模块只出事实,不出判据**(架构 §六 / PROJECT_PLAN §5.9)。
K8 时代的「对账三查」——单笔仓位上限 / 并发持仓数与敞口 / 四条禁买过滤 / 同票割肉
冷却 / 时间退出 / 止损纪律 / 计划台账核对 / 章程分段——**已在 S1 整块删除**:
它们全部绑在持仓台账与「大脑章程」(`strategy/brain.py`)上,两者随 K8 一起下线。
K9 §六 给这一层的职责只有**解析 / 装订 / 存档**,好坏结论由用户带着材料去聊天框得出。

⛔ **不许把那些判据以任何形式请回来,也不许留恒空的壳**:空的「本周违纪」会被读成
「这周很干净」,而真相是「这一项已经不判了」。要看装订材料走 `review/bindery.py`。

**留下的三样,以及它们各自的口径**:

  · **FIFO 闭合回合**(`build_round_trips`)—— 按 (代码, 日期序) 先进先出配对。
    卖出量超过样本内已知买入量的差额(上传窗口看不到更早的建仓)→ 计入 warnings,
    ⛔ 不拼凑虚假买入行。

  · **单周统计**(`WeeklyStats`)—— 胜率 / 盈利因子 / 盈亏比 / 费用 / 毛净盈亏 /
    `realized_loss`。⚠ `realized_loss` 只累加**本周内平仓的负 pnl**(`min(pnl,0)`,
    盈利不冲抵亏损),⛔ 不是「周净盈亏」。

  · **强制复盘线**(`is_forced_review`)—— 单周实现亏损 ≥ 总仓
    `FORCED_REVIEW_LOSS_FRAC`(2%)。这是**纯统计**,不读任何章程,S1 明令别连坐删。

**成交时刻(`trade_instant`)为什么还在**:它是 `RoundTrip` 的时刻口径(交割单只有
日期时按**该日收盘时刻** `MARKET_CLOSE_TIME` 兜底),`bindery.py` 标买卖点、
`cashflow.py` 分周都靠它给出一个**唯一**的「这笔算哪一天」。⛔ 别改成 00:00 / 09:30:
收盘时刻是当天最晚可能的成交时刻,换成别的会把当日成交推到切换之前。
⚠ 两家已知券商格式的交割单都**只有日期**(`RawTrade.trade_time` 恒 None),
故这条兜底就是生产上的常态路径,不是边角分支。

**已知简化(诚实标注,不回避)**:FIFO 回合把同代码的多段买卖折叠成
「该代码的最早买入日 ~ 最晚卖出日(或数据截止日)」;若同代码期间有「卖光又买回」
的间隙,把间隙也算作持仓中(保守偏严,只会多提示、不会漏)。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import polars as pl

from neckline.calendar import CN_TZ, MARKET_CLOSE_TIME
from neckline.review.parse import RawTrade

logger = logging.getLogger(__name__)

_EPS = 1e-9

# 🔴 V2.5.0 S11:`STOP_TOLERANCE_PP` 与 `PRICE_MATCH_TOLERANCE` 两个常量**已删除** ——
# 它们只服务于 S1 删掉的止损纪律判定与计划台账核对,留着一个没人读的容差带,
# 下一个人只会以为「这里还在判止损」。⛔ 不留死常量当纪念碑。

# §2.1 第4条:单周实现亏损 ≥ 总仓 2% → 当晚强制复盘(章程已拍板的固定政策值,
# 不在 §6 P1-P10 回测参数清单内,故不进 strategy_versions,是本模块的字面常量,
# 与 `momentum.py::week_halving_threshold`(5%,已否决的次周减半线)同口径不同阈值)。
FORCED_REVIEW_LOSS_FRAC = 0.02


# ======================================================================
#  成交时刻(「这笔算哪一天」的唯一口径)
# ======================================================================

def trade_instant(trade_date: date, trade_time: Optional[time] = None) -> datetime:
    """把「成交日期(+ 可选时刻)」造成 **tz-aware 北京时间**时刻。全包唯一的时刻口径
    —— `RoundTrip.buy_instant` / `sell_instant`、`bindery.py` 标买卖点、`cashflow.py`
    分周都读它,⛔ 不许谁再自己 `datetime.combine` 一遍。

    **交割单只有日期没有时刻 → 一律按「该日收盘时刻」**(`MARKET_CLOSE_TIME`,15:00
    北京时间)。**⛔ 不许改成 00:00 或 09:30**:收盘时刻 = 当天**最晚可能的成交时刻**,
    取更早的时刻会把当天的成交系统性地推到当天任何一次口径切换之前。
    ⚠ 两家已知券商格式的交割单都**只有日期**(`RawTrade.trade_time` 恒 None),
    故这条兜底就是生产上的常态路径,不是边角分支;`trade_time` 非空时直接用真时刻。
    """
    return datetime.combine(trade_date, trade_time or MARKET_CLOSE_TIME, tzinfo=CN_TZ)


# 🔴 V2.5.0 S11:`day_close_instant()` **已删除**。它只是 `trade_instant(d, None)` 的
# 别名,唯一用途是给 S1 删掉的日粒度章程判据(并发持仓数 / 敞口 / 冷却)当锚。
# ⛔ 不留没有调用方的别名 —— 它的 docstring 里那句「归属哪版章程」会让下一个人以为
# 这里还在判章程。


# ======================================================================
#  FIFO 闭合回合
# ======================================================================

@dataclass
class RoundTrip:
    ts_code: str
    name: str
    buy_date: date
    buy_price: float
    qty: int
    fees: float                      # 归属这笔回合的买卖手续费合计(按股数比例分摊)
    sell_date: Optional[date] = None
    sell_price: Optional[float] = None
    closed: bool = False
    # v1.4-⑥-A:成交时刻(北京时间,交割单不带时刻时为 None → 按该日收盘时刻兜底)。
    # 逐笔判章程要用,故从 `RawTrade` 原样带过来,不在回合层重新猜。
    buy_time: Optional[time] = None
    sell_time: Optional[time] = None

    @property
    def buy_instant(self) -> datetime:
        return trade_instant(self.buy_date, self.buy_time)

    @property
    def sell_instant(self) -> Optional[datetime]:
        """卖出时刻;未平仓 → None(止损纪律本就不判未平仓回合)。"""
        return trade_instant(self.sell_date, self.sell_time) if self.sell_date else None

    @property
    def buy_amount(self) -> float:
        return round(self.buy_price * self.qty, 2)

    @property
    def net_pnl(self) -> Optional[float]:
        if not self.closed or self.sell_price is None:
            return None
        return round((self.sell_price - self.buy_price) * self.qty - self.fees, 2)

    @property
    def pnl_pct(self) -> Optional[float]:
        """未计费用的价格回报率(止损纪律判定用"卖出价相对买入价",不掺费用)。"""
        if not self.closed or self.sell_price is None or self.buy_price <= 0:
            return None
        return (self.sell_price - self.buy_price) / self.buy_price


class _Lot:
    __slots__ = ("price", "date", "time", "name", "qty_original", "qty_remaining", "fee_total")

    def __init__(
        self, price: float, date_: date, name: str, qty: int, fee_total: float,
        time_: Optional[time] = None,
    ) -> None:
        self.price = price
        self.date = date_
        self.time = time_          # v1.4-⑥-A:买入时刻(可空),回合层原样带出
        self.name = name
        self.qty_original = qty
        self.qty_remaining = qty
        self.fee_total = fee_total


def build_round_trips(trades: List[RawTrade]) -> Tuple[List[RoundTrip], List[str]]:
    """按 (代码,日期序) FIFO 闭合回合(plan 4D 通用清洗要求)。输入需已是「成交行」
    (银证转账/分红等非交易行应在 `parse.py` 阶段已被过滤)。

    卖出量超过样本内已知买入量的差额(样本外结转持仓,上传窗口看不到更早的
    建仓)→ 计入 warnings,不强行拼凑虚假买入行。收尾仍有余量的买入 lot →
    产出 `closed=False` 的回合(仍持仓,供仓位纪律核对用)。
    """
    by_code: Dict[str, List[RawTrade]] = {}
    for t in trades:
        by_code.setdefault(t.ts_code, []).append(t)

    round_trips: List[RoundTrip] = []
    warnings: List[str] = []

    for code, txs in by_code.items():
        ordered = sorted(txs, key=lambda t: t.trade_date)  # 稳定排序,保留同日原始相对顺序
        buy_queue: List[_Lot] = []
        for tx in ordered:
            if tx.side == "buy":
                buy_queue.append(
                    _Lot(tx.price, tx.trade_date, tx.name, tx.qty, tx.fee, time_=tx.trade_time)
                )
                continue
            # side == "sell"
            remaining = tx.qty
            sell_fee_per_share = (tx.fee / tx.qty) if tx.qty else 0.0
            while remaining > 0 and buy_queue:
                lot = buy_queue[0]
                matched = min(remaining, lot.qty_remaining)
                buy_fee_per_share = (lot.fee_total / lot.qty_original) if lot.qty_original else 0.0
                round_trips.append(RoundTrip(
                    ts_code=code, name=tx.name or lot.name,
                    buy_date=lot.date, buy_price=lot.price, qty=matched,
                    fees=round((buy_fee_per_share + sell_fee_per_share) * matched, 2),
                    sell_date=tx.trade_date, sell_price=tx.price, closed=True,
                    buy_time=lot.time, sell_time=tx.trade_time,
                ))
                lot.qty_remaining -= matched
                remaining -= matched
                if lot.qty_remaining <= 0:
                    buy_queue.pop(0)
            if remaining > 0:
                warnings.append(
                    f"{code} 于 {tx.trade_date} 卖出 {tx.qty} 股,样本内找不到对应买入(差 {remaining} 股)"
                    "——可能是本次上传窗口之前已建仓的持仓,该部分差额已忽略,不纳入回合统计。"
                )
        for lot in buy_queue:
            round_trips.append(RoundTrip(
                ts_code=code, name=lot.name, buy_date=lot.date, buy_price=lot.price,
                qty=lot.qty_remaining,
                fees=round((lot.fee_total / lot.qty_original) * lot.qty_remaining, 2) if lot.qty_original else 0.0,
                closed=False, buy_time=lot.time,
            ))

    return round_trips, warnings


# ======================================================================
#  止损纪律(对账三查②,§1.3/§2.1 第1条)
# ======================================================================


# ======================================================================
#  计划内/计划外 + 持仓台账对账(对账三查①)
# ======================================================================


# ======================================================================
#  章程执行(对账三查③,§2.1)
# ======================================================================


# ======================================================================
#  章程分段(v1.4-⑥-A:该周发生过切换时,周报注明切换时刻并分段计数)
# ======================================================================


# ======================================================================
#  周统计 + 强制复盘
# ======================================================================

@dataclass
class WeeklyStats:
    closed_count: int
    open_count: int
    win_rate: float
    profit_factor: float
    profit_loss_ratio: float
    total_fees: float
    gross_pnl: float          # 计费用前
    realized_pnl: float       # 计费用后(净)
    realized_loss: float      # 只累加负 pnl(§2.1 第4条口径,同 momentum.py week_loss)


def compute_weekly_stats(closed_round_trips: List[RoundTrip], open_count: int) -> WeeklyStats:
    pnls = [rt.net_pnl for rt in closed_round_trips if rt.net_pnl is not None]
    gross = [
        (rt.sell_price - rt.buy_price) * rt.qty
        for rt in closed_round_trips
        if rt.sell_price is not None
    ]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)
    win_rate = len(wins) / n if n else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    avg_win = (gross_profit / len(wins)) if wins else 0.0
    avg_loss = (gross_loss / len(losses)) if losses else 0.0
    profit_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else (float("inf") if avg_win > 0 else 0.0)
    total_fees = sum(rt.fees for rt in closed_round_trips)
    realized_loss = sum(min(p, 0.0) for p in pnls)   # 同 momentum.py _consume_closed_trades 的 week_loss 口径
    return WeeklyStats(
        closed_count=n, open_count=open_count, win_rate=win_rate,
        profit_factor=profit_factor, profit_loss_ratio=profit_loss_ratio,
        total_fees=round(total_fees, 2), gross_pnl=round(sum(gross), 2),
        realized_pnl=round(sum(pnls), 2), realized_loss=round(realized_loss, 2),
    )


def is_forced_review(stats: WeeklyStats, total_capital: float) -> bool:
    return abs(stats.realized_loss) >= FORCED_REVIEW_LOSS_FRAC * total_capital - _EPS


# ======================================================================
#  ISO 周分桶 + 顶层入口
# ======================================================================

def iso_week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_range(week_key: str) -> Tuple[date, date]:
    y, w = week_key.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1)
    sunday = date.fromisocalendar(int(y), int(w), 7)
    return monday, sunday


@dataclass
class WeeklyReview:
    """一周的对账结果(V2.5.0 S1 起**只装事实,不装判据**)。

    🔴 K8 章程判据整块退役(PROJECT_PLAN §5.9):`plan_checks` / `discipline_violations` /
    `stop_discipline` / `charter_segments` / `charter_switches` / `charter_notes` /
    `plan_warnings` 七个字段**已删除** —— 它们全部绑在持仓台账与「大脑章程」上,两者
    都已随 K8 下线。K9 §六 只要**解析 / 装订 / 存档**三件事,不要纪律判定。
    ⛔ 不许把这些字段留成恒空的壳:恒空会被读成"本周没问题",而真相是"这项已经不判了"。
    """

    week: str
    week_start: date
    week_end: date
    round_trips: List[RoundTrip] = field(default_factory=list)          # 本周相关回合
    closed_round_trips: List[RoundTrip] = field(default_factory=list)   # 本周内平仓(周统计用)
    stats: Optional[WeeklyStats] = None
    forced_review: bool = False
    forced_review_reason: str = ""


def run_weekly_review(
    trades: List[RawTrade], *, db_path: Optional[Path] = None, parquet_dir: Optional[Path] = None,
    total_capital: Optional[float] = None,
) -> Tuple[List[WeeklyReview], List[str]]:
    """顶层入口:FIFO 闭合 → 按 ISO 周分桶 → 每周出 `WeeklyStats` 与强制复盘线判定。

    🔴 **V2.5.0 S1:K8 的「对账三查」整块退役**(PROJECT_PLAN §5.9)——单笔上限 / 并发
    与敞口 / 禁买过滤 / 冷却 / 时间退出 / 止损纪律 / 计划台账核对 / 章程分段,连同它们
    依赖的 `strategy.brain`(大脑章程)与持仓台账一起下线。K9 §六 给这一层的职责只有
    **解析、装订、存档**三件,判断由用户在聊天框里做。

    ⛔ 不要在这里"留一个恒空的违纪清单":空清单会被读成「本周没问题」,而事实是
    「这一项已经不判了」——两者必须分得开(§五 〇c 诚实披露体例)。

    `total_capital` 显式注入(默认 None → `neckline.config.settings.total_capital`),
    只用于 §2.1 第 4 条「单周实现亏损 ≥ 总仓 2%」这条**纯统计**的强制复盘线,
    它不读任何章程。

    `parquet_dir` 目前不再被任何判据使用,签名保留是为了不惊动调用方
    (`review/store.py` / `api/app.py::review_upload`);S11 收口这一层时一并处理。

    返回 `(周报列表, FIFO 数据完整性警告)` —— 后者(如"卖出找不到匹配买入")是整批
    数据层面的问题,不属于任何单一周,由调用方与解析警告一并展示。
    """
    if total_capital is None:
        from neckline.config import settings
        total_capital = settings.total_capital

    round_trips, rt_warnings = build_round_trips(trades)
    if not round_trips:
        return [], rt_warnings

    all_weeks = set()
    for t in trades:
        if t.side == "buy":
            all_weeks.add(iso_week_key(t.trade_date))
    for rt in round_trips:
        if rt.closed and rt.sell_date:
            all_weeks.add(iso_week_key(rt.sell_date))
        else:
            all_weeks.add(iso_week_key(rt.buy_date))

    open_count = sum(1 for rt in round_trips if not rt.closed)
    reviews: List[WeeklyReview] = []
    for week in sorted(all_weeks):
        w_start, w_end = week_range(week)
        closed_week = [rt for rt in round_trips
                       if rt.closed and rt.sell_date and w_start <= rt.sell_date <= w_end]
        review = WeeklyReview(week=week, week_start=w_start, week_end=w_end)
        review.round_trips = round_trips
        review.closed_round_trips = closed_week
        review.stats = compute_weekly_stats(closed_week, open_count=open_count)
        review.forced_review = is_forced_review(review.stats, total_capital)
        if review.forced_review:
            review.forced_review_reason = (
                f"本周实现亏损合计 ¥{abs(review.stats.realized_loss):,.0f},"
                f"达总仓(¥{total_capital:,.0f})的 {abs(review.stats.realized_loss) / total_capital:.1%},"
                f"触及 §2.1 第4条「单周实现亏损 ≥ 总仓 {FORCED_REVIEW_LOSS_FRAC:.0%}」强制复盘线。"
            )
        reviews.append(review)

    return reviews, rt_warnings


# ======================================================================
#  JSON 序列化(供 API 响应与 `reviews` 表 result_json 落库共用同一份形状)
# ======================================================================

def _finite(x: float) -> Optional[float]:
    """`profit_factor`/`profit_loss_ratio` 无亏损样本时是 `float('inf')`——标准 JSON
    无 Infinity/NaN 字面量,Swift `JSONDecoder` 默认策略会直接解码失败。落 None
    (客户端展示"—"/"无亏损样本"),不裸传 inf。"""
    import math

    if x is None or math.isinf(x) or math.isnan(x):
        return None
    return x


def round_trip_dict(rt: RoundTrip) -> dict:
    return {
        "tsCode": rt.ts_code,
        "name": rt.name,
        "buyDate": rt.buy_date.strftime("%Y%m%d"),
        "buyPrice": rt.buy_price,
        "qty": rt.qty,
        "buyAmount": rt.buy_amount,
        "fees": rt.fees,
        "sellDate": rt.sell_date.strftime("%Y%m%d") if rt.sell_date else None,
        "sellPrice": rt.sell_price,
        "closed": rt.closed,
        "netPnl": rt.net_pnl,
        "pnlPct": rt.pnl_pct,
    }


def weekly_stats_dict(s: WeeklyStats) -> dict:
    return {
        "closedCount": s.closed_count,
        "openCount": s.open_count,
        "winRate": s.win_rate,
        "profitFactor": _finite(s.profit_factor),
        "profitLossRatio": _finite(s.profit_loss_ratio),
        "totalFees": s.total_fees,
        "grossPnl": s.gross_pnl,
        "realizedPnl": s.realized_pnl,
        "realizedLoss": s.realized_loss,
    }


def weekly_review_dict(review: WeeklyReview) -> dict:
    """`WeeklyReview` → JSON 安全字典(camelCase,直接就是 API 响应的形状,也是
    `reviews.result_json` 落库的形状——两处共用同一份,不重复定义契约)。

    🔴 **V2.5.0 S1**:`strategyVersion` / `charterSegments` / `charterSwitches` /
    `planWarnings` / `charterNotes` / `planChecks` / `disciplineViolations` /
    `stopDiscipline` 八个键**已删除** —— 产出它们的 K8 章程判据整块退役
    (PROJECT_PLAN §5.9)。⛔ 不留恒空键:空的「本周违纪」会被读成"这周很干净",
    而事实是"这一项系统已经不判了"。
    ⚠ `reviews` 表里 V2.4.x 及更早的历史行仍带着这些键(B 类冻结快照,写入当时冻住),
    读回时按 `decodeIfPresent` 处理即可,⛔ 不回填、不改写历史行(裁定 6)。
    """
    return {
        "week": review.week,
        "weekStart": review.week_start.strftime("%Y%m%d"),
        "weekEnd": review.week_end.strftime("%Y%m%d"),
        "roundTrips": [round_trip_dict(rt) for rt in review.round_trips],
        "closedRoundTrips": [round_trip_dict(rt) for rt in review.closed_round_trips],
        "stats": weekly_stats_dict(review.stats) if review.stats is not None else None,
        "forcedReview": review.forced_review,
        "forcedReviewReason": review.forced_review_reason,
    }


__all__ = [
    "FORCED_REVIEW_LOSS_FRAC",
    "trade_instant",
    "RoundTrip",
    "build_round_trips",
    "WeeklyStats",
    "compute_weekly_stats",
    "is_forced_review",
    "iso_week_key",
    "week_range",
    "WeeklyReview",
    "run_weekly_review",
    "round_trip_dict",
    "weekly_stats_dict",
    "weekly_review_dict",
]
