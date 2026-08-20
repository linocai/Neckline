"""对账引擎(plan 4D.2)。输入已解析的 `RawTrade` 流 → FIFO 闭合回合 → 「对账三查」
+ 单周统计 + 强制复盘判定,按 ISO 周(`YYYY-Www`)分桶输出 `WeeklyReview`。

**同码不重写铁律(§2.6/§3.8)**:
    · 止损/仓位纪律(止损/回落止盈/hold/单笔上限/持仓数/敞口)读大脑现役
      `MomentumConfig`,不硬编字面量(项目 CLAUDE.md「钉死的领域常量单一源」)。
      **v1.4-⑥-A 起按「成交时刻」逐笔取「当时现役」**(§七 P1-4),见下方专节;
      v1.2-A~v1.3 的「按周一把抓」判据(`brain.config_governing_for_week`)只剩
      `WeeklyReview.strategy_version` 这个**周标签**still 在用,不再是判据入口。
    · 绿盘大阴线/距前高/次新/高弹题材四条禁买过滤,直接复用
      `neckline.strategy.signals` 的同名判定表达式(与回测/报告候选管线同一份
      信号定义),本模块不重新推一遍阈值比较。
    · 板块分类复用 `neckline.data.board`(经 `signals.HIGH_ELASTICITY_BOARDS`/
      面板 `board` 列间接复用,未另写正则)。

**单周实现亏损口径(强制复盘线,§2.1 第4条)**:与 `neckline.strategy.momentum.
MomentumStrategy._consume_closed_trades` 的 `week_loss` 完全同口径——只累加
「本周内平仓的负 pnl」(`min(pnl,0)`,盈利不冲抵亏损),不是"周净盈亏"。该函数
docstring 明确写了"5%=挂起项『次周单笔减半』;区别于§2.1已采纳的2%强制复盘线"
——两个阈值(2%/5%)在语义上是同一个"单周实现亏损"公式的不同触发线,本模块的
`FORCED_REVIEW_LOSS_FRAC=0.02` 与该 docstring 明确对应,非另起口径。

**逐笔取章程(v1.4-⑥-A,§七 P1-4;🔴 碰纪律判定,改前读全本节)**:

    旧病:按周一把抓 → 章程激活当周的成交被**旧章程**判(2026-07-27 激活 v1.3.3 后,
    用户当周按「三仓 4 万」打,周复盘仍按 K1「五仓 2 万 + 禁创业板」判 → 单笔 >2 万、
    买创业板、敞口超 60% 全被**误标违纪**)。修法 = 取 config 的入口从「周」下沉到「笔」。

    **时间轴唯一源 = append-only 的 `strategy_activation_log`**(解析器
    `brain.config_governing_at`;v1.4 review 🟡-1 之前是 `strategy_versions.activated_at`
    单戳,回滚重激活会把历史整段改判 —— 现已改成事件流,老库仍按单戳兜底,两者在单次激活
    下逐位等价)。激活戳落库是 **UTC**,交割单成交时刻是**北京时间** —— 归一由
    `trade_instant()`(造 aware 北京时刻)+ `brain._parse_instant`(按 UTC 读)两处
    合力完成,**本模块不自己 strip/加减时区**。边界语义:**成交时刻恰好等于激活时刻算
    「新章程」**(判据 `激活时刻 <= 成交时刻`,理由见 `config_governing_at` docstring)。

    **每条判据锚在「它审计的那笔成交」的时刻**(⚠ 不是统一锚在买入,也不是统一锚在周初):
      · 单笔仓位上限 / 禁买过滤(绿盘大阴线·距前高·次新·高弹题材·ST)→ 锚**买入时刻**
        (违纪成立于"你按当时的章程不该这么买"的那一刻);
      · 止损纪律(破 -5% 未离场)→ 锚**卖出时刻**。理由:它审计的是**离场决策**,而盘中
        哨兵每一拍读的都是**当时现役**的 `stop_pct`(`get_active()`)——按卖出时刻的章程
        判,才与系统当时真的在提醒用户什么一致;按买入时刻判会拿一条用户当时根本没被
        提醒过的线去罚人;
      · 并发持仓数 / 敞口上限 → **日粒度时点量**:每个自然日归属「该日**收盘时刻**现役的
        章程」,按此把周切成连续日段,每段各自求峰值、各自比该段的 cap(等价于"每一天
        的持仓水平比该天自己的上限",不是拿一周的峰值比某一版的上限);
      · 同票割肉冷却 → 锚**再次买入时刻**(违纪成立于再买那一刻),沿用既有"整批算一次、
        按再买日分发到周"的姿势,只是把 `cooldown_days` 换成该日段的值。
      · 时间退出违纪(§2.1 第 2 条周线兜底)**不读 config**,判据是历史事实,不受本次改动
        影响(见 `check_time_exit_discipline`)。

    **交割单只有日期没有时刻时**(两家已知券商格式都是这样):一律按**该日收盘时刻**
    (`MARKET_CLOSE_TIME` 15:00 北京时间)取 config —— 定死,见 `trade_instant()`。

    **周内没发生章程切换时,逐笔判据与旧的按周判据逐位等价**(回归护栏,有单测);
    发生切换时,周报显式注明切换时刻并**分段计数**(`WeeklyReview.charter_segments` /
    `charter_switches`,文案进 `review/material.py`)。

**已知简化(诚实标注,不回避)**:
    1. 「最多持 N 只」/「敞口 ≤60%」的核算范围限于**本次上传数据可见的持仓区间**
       (按回合的 buy_date~sell_date 或"数据截止日"扫描线求峰值)——若某票是在
       未上传的更早期间开仓、本次只看到中途卖出,该笔的"占用仓位"在开仓阶段不可
       追溯(不在本次上传范围内),这是"只审计当周成交"的固有边界,非漏判 bug。
    2. FIFO 回合把同代码的多段买卖折叠成"该代码的最早买入日~最晚卖出日(或数据
       截止日)"一段区间用于「持仓只数」并发计数(见 `_collapse_code_intervals`)
       ——若同代码期间有"卖光又买回"的间隙,并发计数会把间隙也算作"持仓中"
       (保守偏严,不会漏判,只可能对边界情形多报警示,供人工复核)。
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

# 容差带(§2.1 第1条「破-5%止损未离场」的判定容差,思路沿 LinoN [-6%,-4%])——
# 联动现役 `stop_pct`(不写死绝对 -6%/-4%,大脑升级止损线后容差带跟着平移)。
STOP_TOLERANCE_PP = 0.01

# §2.1 第4条:单周实现亏损 ≥ 总仓 2% → 当晚强制复盘(章程已拍板的固定政策值,
# 不在 §6 P1-P10 回测参数清单内,故不进 strategy_versions,是本模块的字面常量,
# 与 `momentum.py::week_halving_threshold`(5%,已否决的次周减半线)同口径不同阈值)。
FORCED_REVIEW_LOSS_FRAC = 0.02

# 单笔仓位/台账价格比对容差(1%,浮点/四舍五入噪音带)。
PRICE_MATCH_TOLERANCE = 0.01


# ======================================================================
#  成交时刻(v1.4-⑥-A 逐笔判章程的时间轴入口)
# ======================================================================

def trade_instant(trade_date: date, trade_time: Optional[time] = None) -> datetime:
    """把「成交日期(+ 可选时刻)」造成 **tz-aware 北京时间**时刻,供
    `brain.config_governing_at` 逐笔取章程(v1.4-⑥-A)。

    **交割单只有日期没有时刻 → 一律按「该日收盘时刻」**(`MARKET_CLOSE_TIME`,15:00
    北京时间;plan §五-⑥-A 定死的口径)。**不许改成 00:00 或 09:30**,理由:
      · 收盘时刻 = 当天**最晚可能的成交时刻**。取 00:00 / 09:30 会把当天所有成交推到
        切换之前 → 章程激活当日的成交仍被旧章程判,P1-4 的病(激活当日按新章程打、
        复盘按旧章程判 = 假警报)没修干净;
      · **诚实边界(实测,勿照抄 plan 原文的理由)**:plan 给的理由是"章程激活都在盘后
        跑,不会出现同日切换后又开仓"——但 2026-07-27 那次真实激活的时刻是**北京 14:36
        (盘中)**,该前提并不总成立。故本口径真正的依据是上一条(方向与 P1-4 要修的假
        警报一致、与用户当日实际按新章程操作的事实一致),而不是"同日不可能有切换后成交"。
        代价写明:激活当日**激活时刻之前**的成交会被判给新章程(偏宽一天);要消除它只能
        靠交割单带上真实成交时刻(`RawTrade.trade_time` 非空时本函数直接用真时刻,兜底不生效)。
      · 两家已知券商格式的交割单都**只有日期**(`trade_time` 恒 None),故这条兜底就是
        生产上的常态路径,不是边角分支。
    """
    return datetime.combine(trade_date, trade_time or MARKET_CLOSE_TIME, tzinfo=CN_TZ)


def day_close_instant(d: date) -> datetime:
    """某自然日的**收盘时刻**(北京时间 aware)。日粒度判据(并发持仓数 / 敞口 / 冷却)
    归属哪版章程时用它当锚 —— 与 `trade_instant` 的日期兜底同一个时刻口径,不另立一套。"""
    return trade_instant(d, None)


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
    "STOP_TOLERANCE_PP",
    "FORCED_REVIEW_LOSS_FRAC",
    "trade_instant",
    "day_close_instant",
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
