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

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import polars as pl

from neckline.calendar import CN_TZ, MARKET_CLOSE_TIME
from neckline.review.parse import RawTrade

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

STOP_BREACHED = "breached"          # 破止损未离场(违纪)
STOP_KEPT = "kept_stop"             # 止损纪律执行到位(容差带内离场)
STOP_NOT_TRIGGERED = "not_triggered"  # 未触及止损区间(正常盈利或浅亏离场,无需判定)
STOP_NOT_APPLICABLE = "not_applicable"  # 现役规则未设止损,不做判定


def classify_stop_discipline(rt: RoundTrip, stop_pct: Optional[float]) -> Tuple[str, str]:
    """返回 (分类, 说明文案)。分类四态见上方常量。"""
    if stop_pct is None:
        return STOP_NOT_APPLICABLE, "现役规则未设固定止损,本回合不做止损纪律判定。"
    pct = rt.pnl_pct
    if pct is None:
        return STOP_NOT_APPLICABLE, "回合未平仓,暂不判定止损纪律。"
    lo = -(stop_pct + STOP_TOLERANCE_PP)   # 更亏的一侧(如 -6%)
    hi = -(stop_pct - STOP_TOLERANCE_PP)   # 更浅的一侧(如 -4%)
    pct_txt = f"{pct:+.1%}"
    if pct <= lo + _EPS:
        return STOP_BREACHED, (
            f"卖出价相对买入价 {pct_txt},跌破止损容差带下沿({lo:.0%}),"
            f"疑似未按 -{stop_pct:.0%} 止损离场(§1.3 第一死因、§2.1 第1条违纪)。"
        )
    if lo - _EPS < pct <= hi + _EPS:
        return STOP_KEPT, f"卖出价相对买入价 {pct_txt},落在止损容差带({lo:.0%}~{hi:.0%})内,止损纪律执行到位。"
    return STOP_NOT_TRIGGERED, f"卖出价相对买入价 {pct_txt},未触及止损容差带,无需判定止损纪律。"


# ======================================================================
#  计划内/计划外 + 持仓台账对账(对账三查①)
# ======================================================================

@dataclass
class PlanLedgerCheck:
    ts_code: str
    name: str
    trade_date: date
    price: float
    qty: int
    plan_status: str
    ledger_status: str

    @property
    def amount(self) -> float:
        return round(self.price * self.qty, 2)


def check_plan_and_ledger(buy_trades: List[RawTrade], db_path: Optional[Path] = None) -> List[PlanLedgerCheck]:
    """实际买入 vs 当日报告候选/问询台海选池(计划内/计划外)+ vs 持仓台账(是否
    录入 `positions`,决定止损提醒是否覆盖到这笔仓位)。"""
    from neckline.api.stores import load_inquiry_pool
    from neckline.report import store as report_store
    from neckline.sentinel.positions import load_all_positions

    positions = load_all_positions(db_path=db_path)
    report_cache: Dict[date, Optional[dict]] = {}
    pool_cache: Dict[date, set] = {}
    out: List[PlanLedgerCheck] = []

    for tx in buy_trades:
        d = tx.trade_date
        if d not in report_cache:
            report_cache[d] = report_store.load_report(d, db_path=db_path)
        rep = report_cache[d]
        if d not in pool_cache:
            pool_cache[d] = {p["ts_code"] for p in load_inquiry_pool(d, db_path=db_path)}
        pool = pool_cache[d]

        if rep is None:
            plan_status = "无报告数据(该日未生成报告,无法核对)"
        else:
            cand_codes = {c.get("ts_code") for c in rep.get("candidates", [])}
            if tx.ts_code in cand_codes:
                plan_status = "计划内(当日报告候选)"
            elif tx.ts_code in pool:
                plan_status = "计划内(问询台海选池)"
            else:
                plan_status = "计划外(未经系统候选/海选池放行的自主买入)"

        ledger_status = "台账缺失(未在系统持仓台账登记,止损提醒未覆盖此仓位)"
        d_str = d.strftime("%Y%m%d")
        for p in positions:
            if p.ts_code != tx.ts_code or p.buy_date != d_str:
                continue
            if p.buy_price and abs(p.buy_price - tx.price) / p.buy_price <= PRICE_MATCH_TOLERANCE:
                ledger_status = "台账已录"
            else:
                ledger_status = f"台账记录价格不符(台账¥{p.buy_price:.2f} vs 交割单¥{tx.price:.2f},请核对)"
            break

        out.append(PlanLedgerCheck(
            ts_code=tx.ts_code, name=tx.name, trade_date=d, price=tx.price, qty=tx.qty,
            plan_status=plan_status, ledger_status=ledger_status,
        ))
    return out


# ======================================================================
#  章程执行(对账三查③,§2.1)
# ======================================================================

def check_single_cap(buy_trades: List[RawTrade], single_cap: float) -> List[str]:
    out = []
    for tx in buy_trades:
        amt = tx.price * tx.qty
        if amt > single_cap + _EPS:
            out.append(
                f"{tx.ts_code}({tx.name})于 {tx.trade_date} 买入金额 ¥{amt:,.0f},"
                f"超过单笔仓位上限 ¥{single_cap:,.0f}(§2.1 第3条)。"
            )
    return out


def _sweep_max_in_window(
    intervals: List[Tuple[date, date, float]], window_start: date, window_end: date
) -> Tuple[float, Optional[date]]:
    """扫描线求区间和的峰值(闭区间,按自然日),只在 [window_start,window_end] 内找峰值。
    `intervals` 元素为 (起, 止(含), 权重);止 < 起 的区间会被丢弃。"""
    relevant = [
        (max(s, window_start), min(e, window_end), v)
        for s, e, v in intervals
        if s <= window_end and e >= window_start
    ]
    relevant = [(s, e, v) for s, e, v in relevant if s <= e]
    if not relevant:
        return 0.0, None
    events: List[Tuple[date, float]] = []
    for s, e, v in relevant:
        events.append((s, v))
        events.append((e + timedelta(days=1), -v))
    events.sort(key=lambda t: t[0])

    running = 0.0
    peak = 0.0
    peak_date: Optional[date] = None
    i = 0
    n = len(events)
    while i < n:
        d = events[i][0]
        while i < n and events[i][0] == d:
            running += events[i][1]
            i += 1
        if d > window_end:
            break
        if running > peak + _EPS:
            peak = running
            peak_date = d
    return peak, peak_date


def _collapse_code_intervals(round_trips: List[RoundTrip], asof: date) -> List[Tuple[date, date, str]]:
    """按代码折叠成一段"最早买入~最晚卖出(或数据截止日)"区间(见模块 docstring
    已知简化 2)。"""
    by_code: Dict[str, List[RoundTrip]] = {}
    for rt in round_trips:
        by_code.setdefault(rt.ts_code, []).append(rt)
    out = []
    for code, rts in by_code.items():
        start = min(rt.buy_date for rt in rts)
        end = max((rt.sell_date if rt.closed and rt.sell_date else asof) for rt in rts)
        out.append((start, end, code))
    return out


def check_position_count_and_exposure(
    round_trips: List[RoundTrip], *, week_start: date, week_end: date, asof: date,
    max_positions: int, max_exposure_frac: float, total_capital: float,
    window_label: str = "本周",
) -> List[str]:
    """`window_label`(v1.4-⑥-A):违纪文案里的窗口自称。**缺省「本周」= ⑥-A 之前的原文,
    逐位不变**;只有当该周发生过章程切换、本函数被按日段分多次调用时,调用方才传一个
    带日期与版本号的段标签 —— 否则两段各报一条「本周…」会被误读成重复条目。"""
    out = []
    code_intervals = _collapse_code_intervals(round_trips, asof)
    peak_count, peak_count_date = _sweep_max_in_window(
        [(s, e, 1.0) for s, e, _ in code_intervals], week_start, week_end
    )
    if peak_count > max_positions + _EPS:
        out.append(
            f"{window_label}并发持仓最多达 {int(round(peak_count))} 只(约 {peak_count_date}),"
            f"超过最多持 {max_positions} 只的仓位纪律(§2.1 第3条)。"
        )

    exposure_intervals = [(rt.buy_date, rt.sell_date or asof, rt.buy_amount) for rt in round_trips]
    peak_exposure, peak_exposure_date = _sweep_max_in_window(exposure_intervals, week_start, week_end)
    exposure_cap = max_exposure_frac * total_capital
    if peak_exposure > exposure_cap + _EPS:
        out.append(
            f"{window_label}持仓总敞口最高达 ¥{peak_exposure:,.0f}(约 {peak_exposure_date},"
            f"占总仓 {peak_exposure / total_capital:.1%}),超过敞口上限 "
            f"{max_exposure_frac:.0%}(¥{exposure_cap:,.0f},§2.1 第3条)。"
        )
    return out


def check_entry_screens(
    buy_trades: List[RawTrade], cfg, *, parquet_dir: Optional[Path] = None,
) -> List[str]:
    """绿盘大阴线/距前高/次新/高弹题材/ST——复用 `neckline.strategy.signals` 同一份
    判定表达式(同码不重写),现役 config 未启用的过滤项天然不产生任何违纪(不硬编)。"""
    from neckline.strategy import signals as S
    from neckline.strategy.features import build_research_panel

    out: List[str] = []
    if not buy_trades:
        return out
    panel_cache: Dict[date, pl.DataFrame] = {}
    for tx in buy_trades:
        d = tx.trade_date
        if d not in panel_cache:
            try:
                panel_cache[d] = build_research_panel(d, d, with_forward=False, parquet_dir=parquet_dir)
            except Exception:  # noqa: BLE001  数据缺口不影响其余检查
                panel_cache[d] = pl.DataFrame()
        panel = panel_cache[d]
        if panel is None or panel.is_empty():
            continue
        sub = panel.filter(pl.col("ts_code") == tx.ts_code)
        if sub.is_empty():
            continue
        row = sub.row(0, named=True)

        if row.get("is_st"):
            out.append(f"{tx.ts_code}({tx.name})于 {tx.trade_date} 买入时为 ST/*ST(选股域常年排除该类票)。")
        if cfg.forbid_high_elasticity and row.get("board") in S.HIGH_ELASTICITY_BOARDS:
            out.append(
                f"{tx.ts_code}({tx.name})于 {tx.trade_date} 买入,所属板块「{row.get('board')}」属高弹题材"
                "(现役规则 forbid_high_elasticity 已启用,§1.3 第二死因)。"
            )
        if cfg.forbid_green_bigdown is not None:
            hit = sub.select(S.forbid_green_bigdown(cfg.forbid_green_bigdown).alias("v")).row(0)[0]
            if hit:
                out.append(
                    f"{tx.ts_code}({tx.name})于 {tx.trade_date} 买入当日为绿盘大阴线"
                    f"(现役规则禁买阈值 {cfg.forbid_green_bigdown:.0%})。"
                )
        if cfg.forbid_far_from_high is not None:
            hit = sub.select(S.forbid_far_from_high(cfg.forbid_far_from_high).alias("v")).row(0)[0]
            if hit:
                out.append(
                    f"{tx.ts_code}({tx.name})于 {tx.trade_date} 买入当日距 20 日高点过远"
                    f"(现役规则禁买阈值 {cfg.forbid_far_from_high:.0%})。"
                )
        if cfg.forbid_new_days is not None:
            hit = sub.select(S.forbid_new_stock(cfg.forbid_new_days).alias("v")).row(0)[0]
            if hit:
                out.append(
                    f"{tx.ts_code}({tx.name})于 {tx.trade_date} 买入时为次新股"
                    f"(现役规则禁买阈值 {cfg.forbid_new_days} 自然日)。"
                )
    return out


def check_cooldown(round_trips: List[RoundTrip], cooldown_days: int) -> List[str]:
    """同票割肉后冷却(§2.1;现役 `cooldown_days=0` 时天然是 no-op,不硬编)。"""
    if not cooldown_days:
        return []
    from neckline.calendar import next_trading_day

    out = []
    by_code: Dict[str, List[RoundTrip]] = {}
    for rt in round_trips:
        by_code.setdefault(rt.ts_code, []).append(rt)
    for code, rts in by_code.items():
        losses = [rt for rt in rts if rt.closed and rt.net_pnl is not None and rt.net_pnl < 0]
        for loss_rt in losses:
            cd = loss_rt.sell_date
            assert cd is not None
            for _ in range(cooldown_days):
                cd = next_trading_day(cd)
            for rt2 in rts:
                if rt2.buy_date > loss_rt.sell_date and rt2.buy_date <= cd:
                    out.append(
                        f"{code} 于 {loss_rt.sell_date} 割肉离场后,在冷却期内"
                        f"(现役规则 {cooldown_days} 交易日,至 {cd})的 {rt2.buy_date} 再次买入,"
                        "违反同票冷却纪律(§2.1)。"
                    )
    return out


_TIME_EXIT_KIND_LABEL = {
    "time_exit_next_day": "时间退出(D5 收盘判非浮盈 → 次日退出)",
    "hard_cap_exit": "浮盈硬上限时间退出(D15 无条件)",
}


def check_time_exit_discipline(
    week_start: date, week_end: date, positions: List, due_map: Dict[int, Dict[str, str]],
) -> List[str]:
    """时间退出违纪审计(§2.1 第 2 条的**周线兜底**;2026-07-27 审计 🔵-9 补)。

    此前 §2.1 第 2 条(止盈/时间退出)在周复盘里**完全没有兜底**——系统当晚推了「按计划
    离场」,用户没走也无人事后记一笔。配合同批的 🔴-1「D5 判一次定格」修复(判向不再逐日
    重判、违纪不被系统事后改口豁免),这里把「说了该走、台账显示没走」如实记成违纪。

    判据(两侧数据都在本系统内,不依赖交割单):
      · **系统说该走**:`due_map[position_id]`(= `holding_store.time_exit_due_map`,取
        `holding_eod_check.time_exit_state` 落 actionable 两态的**最早**一天 L);
      · **应离场日**:L 的**下一个交易日**(「次日退出」的字面含义);
      · **台账显示没走**:该持仓 `sell_date` 晚于应离场日,或到 `week_end` 仍 `open`。
      · **归哪一周**:应离场日所在的 ISO 周(违纪在那天成立)。

    诚实边界(与熔断同款):**只能基于用户已补录进台账的成交判定**,漏录则失灵;
    卖在应离场日**之前**不算违纪(更早离场是更严的自律,不罚)。单档现役 K1 与两档 v1.3
    都覆盖(判据用每日记录的 `time_exit_state`,见 `time_exit_due_map` docstring)。"""
    from neckline.calendar import next_trading_day, trading_days_between

    out: List[str] = []
    for p in positions:
        due = due_map.get(p.id)
        if not due:
            continue
        try:
            decided = datetime.strptime(due["decision_date"], "%Y%m%d").date()
        except (ValueError, TypeError, KeyError):
            continue
        must_exit_by = next_trading_day(decided)
        if not (week_start <= must_exit_by <= week_end):
            continue                      # 违纪(若有)不属于本周
        sell_date = None
        if p.sell_date:
            try:
                sell_date = datetime.strptime(p.sell_date, "%Y%m%d").date()
            except (ValueError, TypeError):
                sell_date = None
        kind = _TIME_EXIT_KIND_LABEL.get(due.get("kind", ""), "时间退出")
        if sell_date is None:
            out.append(
                f"{p.ts_code} {p.buy_date}买入:系统于 {decided.strftime('%Y-%m-%d')} 判"
                f"{kind},应于 {must_exit_by.strftime('%Y-%m-%d')} 离场,台账截至本周末仍"
                f"未平仓——违反 §2.1 第 2 条时间退出纪律(基于已补录台账判定)。"
            )
        elif sell_date > must_exit_by:
            out.append(
                f"{p.ts_code} {p.buy_date}买入:系统于 {decided.strftime('%Y-%m-%d')} 判"
                f"{kind},应于 {must_exit_by.strftime('%Y-%m-%d')} 离场,台账实际卖出日"
                f"{sell_date.strftime('%Y-%m-%d')}(晚 {len(trading_days_between(must_exit_by, sell_date)) - 1} "
                f"个交易日)——违反 §2.1 第 2 条时间退出纪律。"
            )
    return out


# ======================================================================
#  章程分段(v1.4-⑥-A:该周发生过切换时,周报注明切换时刻并分段计数)
# ======================================================================

@dataclass
class CharterSegment:
    """周内一段「同一版章程治下」的区间。`start=None` = 自周初起的那一段。
    `trade_count` = 本周落在该段内的**成交笔数**(买 + 卖各算一笔,与"逐笔判"同口径)。"""
    version: str
    start: Optional[datetime] = None      # 北京时间 aware;None = 周初
    trade_count: int = 0


@dataclass
class CharterSwitch:
    """周内发生的一次章程切换(= `strategy_versions` 的一次激活落在本周窗口内)。
    `at` 一律已换算成**北京时间**(展示与序列化口径;时间轴事实源是激活事件流,见
    `brain._activation_events`)。"""
    at: datetime
    from_version: str
    to_version: str
    note: str = ""


def _week_window(w_start: date, w_end: date) -> Tuple[datetime, datetime]:
    """周窗口 `[周一 00:00, 下周一 00:00)`(北京时间,半开)——相邻周不会把同一次激活
    各算一遍。"""
    lo = datetime.combine(w_start, time(0, 0), tzinfo=CN_TZ)
    hi = datetime.combine(w_end + timedelta(days=1), time(0, 0), tzinfo=CN_TZ)
    return lo, hi


def build_charter_timeline(
    w_start: date, w_end: date, week_trades: Sequence[RawTrade], base_version: str,
    *, db_path: Optional[Path] = None,
) -> Tuple[List[CharterSegment], List[CharterSwitch]]:
    """本周的章程分段 + 切换清单(v1.4-⑥-A 周报呈现)。

    `base_version` = 本周**周初**挂的章程版本(= `WeeklyReview.strategy_version`,由
    `brain.config_governing_for_week` 给,**与逐笔判据用的解析器不同但相容**:周初标签
    看「激活日 < week_start」,而周内的切换从 `brain.activations_between` 取实际激活时刻;
    两者在所有现实时点上给出一致的分段——见模块头「逐笔取章程」节)。

    **同版本再激活(回滚 / 重激活同一版)不算切换**(阈值没变,报出来只会制造噪音),
    但它仍在时间轴上,故只是不新起一段,不是被丢掉。**切回上一版**(如 v1.3.3 → v1.3)
    阈值真的变了,照常报一次切换(v1.4 review 🟡-1 起事件流表达得了这种形状)。
    """
    from neckline.strategy import brain

    lo, hi = _week_window(w_start, w_end)
    segments: List[CharterSegment] = [CharterSegment(version=base_version or "", start=None)]
    switches: List[CharterSwitch] = []
    for inst, ver in brain.activations_between(lo, hi, db_path=db_path):
        prev = segments[-1].version
        if ver.version == prev:
            continue
        at_cn = inst.astimezone(CN_TZ)
        segments.append(CharterSegment(version=ver.version, start=at_cn))
        switches.append(CharterSwitch(at=at_cn, from_version=prev, to_version=ver.version))

    for t in week_trades:
        inst = trade_instant(t.trade_date, t.trade_time)
        idx = 0
        for i, seg in enumerate(segments):
            if seg.start is not None and seg.start <= inst:
                idx = i
        segments[idx].trade_count += 1

    for i, sw in enumerate(switches):
        before = sum(s.trade_count for s in segments[: i + 1])
        after = sum(s.trade_count for s in segments[i + 1:])
        sw.note = (
            f"本周 {sw.at.strftime('%Y-%m-%d %H:%M')} 发生章程切换 "
            f"{sw.from_version or '(未知)'}→{sw.to_version}"
            f"(切换前 {before} 笔按 {sw.from_version or '(未知)'} 判、"
            f"切换后 {after} 笔按 {sw.to_version} 判)。"
        )
    return segments, switches


def _charter_day_runs(
    lo: date, hi: date, resolve_version: Callable[[datetime], Optional[str]],
) -> List[Tuple[date, date, str]]:
    """把 `[lo, hi]` 按「每个自然日**收盘时刻**现役的章程版本」切成连续日段,返回
    `(段起日, 段止日, 版本号)`。

    用于**日粒度**判据(并发持仓数 / 敞口 / 冷却):这类量不是"一笔成交",而是"某一天
    的持仓水平",故按天归属章程、每段各自比该段自己的上限——等价于"每一天比该天自己
    的上限",与逐笔判同精神。段内无切换时只有一段 → 与旧的整周一把抓**逐位等价**。"""
    runs: List[Tuple[date, date, str]] = []
    d = lo
    while d <= hi:
        ver = resolve_version(day_close_instant(d)) or ""
        if runs and runs[-1][2] == ver:
            runs[-1] = (runs[-1][0], d, ver)
        else:
            runs.append((d, d, ver))
        d += timedelta(days=1)
    return runs


def _group_by_charter(
    items: Sequence, instant_of: Callable, resolve: Callable,
) -> List[Tuple[object, Optional[str], List]]:
    """把一串成交按「其成交时刻 governing 的章程」分组(保持原顺序,连续同版本合并)。
    返回 `[(cfg, version, [items…])]`;`cfg=None` = 该段无可用 config(判据诚实跳过)。"""
    out: List[Tuple[object, Optional[str], List]] = []
    for it in items:
        cfg, ver = resolve(instant_of(it))
        if out and out[-1][1] == ver:
            out[-1][2].append(it)
        else:
            out.append((cfg, ver, [it]))
    return out


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
    week: str
    week_start: date
    week_end: date
    round_trips: List[RoundTrip] = field(default_factory=list)          # 本周相关回合(用于并发/敞口扫描)
    closed_round_trips: List[RoundTrip] = field(default_factory=list)   # 本周内平仓(止损纪律 + 周统计用)
    plan_checks: List[PlanLedgerCheck] = field(default_factory=list)
    discipline_violations: List[str] = field(default_factory=list)
    stop_discipline: List[Tuple[RoundTrip, str, str]] = field(default_factory=list)  # (回合, 分类, 说明)
    stats: Optional[WeeklyStats] = None
    forced_review: bool = False
    forced_review_reason: str = ""
    strategy_version: Optional[str] = None   # 本周**周初**挂的大脑版本号(v1.2-A 起判据
                                             # 「激活日 < week_start」,落 reviews.strategy_version)。
                                             # v1.4-⑥-A 后它是**周标签**,不再是逐笔判据——
                                             # 该周真正用过哪几版见 charter_segments。
    # v1.4-⑥-A:本周章程分段 + 切换清单(周内无切换 → segments 只有一段、switches 为空)。
    charter_segments: List[CharterSegment] = field(default_factory=list)
    charter_switches: List[CharterSwitch] = field(default_factory=list)


def run_weekly_review(
    trades: List[RawTrade], *, db_path: Optional[Path] = None, parquet_dir: Optional[Path] = None,
    total_capital: Optional[float] = None,
) -> Tuple[List[WeeklyReview], List[str]]:
    """顶层入口:FIFO 闭合 → 按 ISO 周分桶 → 每周跑「对账三查」+ 统计 + 强制复盘。

    **按「成交时刻」逐笔取 config(v1.4-⑥-A,§七 P1-4;判据入口的唯一版本)**:每笔成交
    以 `trade_instant()` 造出的北京时刻调 `brain.config_governing_at(ts)` 解析**当时**
    governing 的大脑版本,用它的 `MomentumConfig` 判该笔——**不再按周一把抓**(章程激活
    当周的成交被旧章程判 = 已知假警报)。哪条判据锚哪个时刻(买入/卖出/该日收盘)见模块头
    「逐笔取章程」节。历史洗白防线**依然成立且更严**:已经结束的那一周里的成交,其成交
    时刻必然早于之后才发生的激活 → 恒按当时的旧章程判,今天的新上限洗不白它。

    `WeeklyReview.strategy_version` 仍是**周标签**(`brain.config_governing_for_week`,判据
    「激活日 < week_start」,v1.2-A/🟡-3 语义不变,落 `reviews.strategy_version`);该周真正
    用过哪几版、几点切的,见 `charter_segments` / `charter_switches`。无现役版本(纯 legacy
    库经兜底退回 `get_active`,仍为 None)时,止损纪律/禁买过滤两类检查诚实跳过(不臆造规则)。

    `total_capital` 显式注入(默认 None → 落 `neckline.config.settings.total_capital`)
    ——与 `db_path`/`parquet_dir` 同款风格,单测可直接传值,不必монkeypatch 全局
    `settings`(本模块新增,尚未纳入 conftest 的 `isolated_env`/`api_env` monkeypatch
    覆盖范围,显式参数是更直接的可测试写法)。

    返回 `(周报列表, FIFO 数据完整性警告)`——后者(如"卖出找不到匹配买入")是
    整批数据层面的问题,不属于任何单一周的"违纪",故不塞进某一周的
    `discipline_violations`,由调用方(API 层)与解析警告一并展示。
    """
    from neckline.strategy import brain
    from neckline.strategy.momentum import MomentumConfig

    if total_capital is None:
        from neckline.config import settings
        total_capital = settings.total_capital

    def _to_cfg(gov) -> Tuple[Optional[MomentumConfig], Optional[str]]:
        """`StrategyVersion` → (MomentumConfig, 版本号)。无 / 非法 config → (None, 版本号或
        None):止损/章程检查据此诚实跳过(不臆造规则)。"""
        if gov is None:
            return None, None
        try:
            return MomentumConfig(**gov.rule["config"]), gov.version
        except (KeyError, TypeError):
            return None, gov.version

    def _cfg_for_week(week_start: date) -> Tuple[Optional[MomentumConfig], Optional[str]]:
        """本周**周标签**版本(判据「激活日 < week_start」,v1.2-A/审计 🟡-3 语义原样保留)。
        ⚠ v1.4-⑥-A 起它**不再是判据入口**(判据走 `_cfg_at`),只做 `strategy_version` 标签
        与章程分段的周初基线。判据与理由的唯一源在 `brain.config_governing_for_week`
        docstring,此处不重述。"""
        return _to_cfg(brain.config_governing_for_week(week_start, db_path=db_path))

    # 逐笔/逐日解析「该时刻 governing 的 config」(v1.4-⑥-A 判据入口)。按时刻记忆化:
    # 同一天的多笔成交、同一段里的多天,只查一次库(`config_governing_at` 每次都读全表)。
    _cfg_at_cache: Dict[datetime, Tuple[Optional[MomentumConfig], Optional[str]]] = {}

    def _cfg_at(ts: datetime) -> Tuple[Optional[MomentumConfig], Optional[str]]:
        if ts not in _cfg_at_cache:
            _cfg_at_cache[ts] = _to_cfg(brain.config_governing_at(ts, db_path=db_path))
        return _cfg_at_cache[ts]

    def _version_at(ts: datetime) -> Optional[str]:
        return _cfg_at(ts)[1]

    round_trips, rt_warnings = build_round_trips(trades)
    if not round_trips:
        return [], rt_warnings

    asof = max((t.trade_date for t in trades), default=date.today())

    all_dates = set()
    for t in trades:
        if t.side == "buy":
            all_dates.add(iso_week_key(t.trade_date))
    for rt in round_trips:
        if rt.closed and rt.sell_date:
            all_dates.add(iso_week_key(rt.sell_date))
        else:
            all_dates.add(iso_week_key(rt.buy_date))

    # cooldown 违纪与具体周次无关(整批算一次,下面按"再次买入"落在哪周分发)。
    # **v1.4-⑥-A**:`cooldown_days` 改按「**再次买入那一天**现役的章程」取——把交割单覆盖
    # 区间按日段切开(`_charter_day_runs`),每段用该段的 `cooldown_days` 整批算一次、只保留
    # "再次买入日落在本段内"的那些违纪。区间内无章程切换时只有一段 = 与旧的一把抓逐位等价。
    # (现役各版 `cooldown_days` 恒为 0 → `check_cooldown` 提前返回空列表,是真实的 no-op 路径。)
    span_lo_date = min(t.trade_date for t in trades)
    cooldown_violations: List[str] = []
    for run_lo, run_hi, _run_ver in _charter_day_runs(span_lo_date, asof, _version_at):
        run_cfg, _ = _cfg_at(day_close_instant(run_lo))
        if run_cfg is None or not run_cfg.cooldown_days:
            continue
        cooldown_violations += [
            msg for msg in check_cooldown(round_trips, run_cfg.cooldown_days)
            if _cooldown_violation_in_week(msg, run_lo, run_hi)
        ]

    # 时间退出违纪审计的两侧数据(审计 🔵-9):台账全量持仓 + 系统「判该走」的最早日。
    # 整批读一次(与 cooldown 同姿势),逐周按「应离场日落在哪周」分发。库读失败不掀翻
    # 周复盘主流程(该项诚实跳过 = 与「无现役 config 时止损检查跳过」同款诚实降级)。
    try:
        from neckline.report.holding_store import time_exit_due_map
        from neckline.sentinel.positions import load_all_positions

        ledger_positions = load_all_positions(db_path=db_path)
        time_exit_due = time_exit_due_map(db_path=db_path)
    except Exception:  # noqa: BLE001
        ledger_positions, time_exit_due = [], {}

    # 「应离场日」所在周若**当周没有任何成交**,原来根本不会生成该周的 WeeklyReview →
    # 违纪静默丢失(而「拿着没卖、整周没动」恰恰是时间退出违纪最典型的样子)。故把这些周
    # 也纳入 all_dates —— 但**只纳入交割单实际覆盖区间内的周**(`[最早成交日所在周,
    # asof 所在周]`):区间外我们没有该周的成交数据,沉默是诚实的,不能拿半份数据判违纪。
    if time_exit_due:
        from neckline.calendar import next_trading_day

        span_lo = span_lo_date
        for due in time_exit_due.values():
            try:
                decided = datetime.strptime(due["decision_date"], "%Y%m%d").date()
            except (ValueError, TypeError, KeyError):
                continue
            must_exit_by = next_trading_day(decided)
            if span_lo <= must_exit_by <= asof:
                all_dates.add(iso_week_key(must_exit_by))

    reviews: List[WeeklyReview] = []
    for week in sorted(all_dates):
        w_start, w_end = week_range(week)
        # 周标签(v1.2-A + 审计 🟡-3 判据「激活日 < week_start」,语义不变);⑥-A 之后它只是
        # 标签与章程分段的周初基线,**不再是任何判据的取数入口**。
        gov_version = _cfg_for_week(w_start)[1]
        buy_trades_week = [t for t in trades if t.side == "buy" and w_start <= t.trade_date <= w_end]
        closed_week = [rt for rt in round_trips if rt.closed and rt.sell_date and w_start <= rt.sell_date <= w_end]
        trades_week = [t for t in trades if w_start <= t.trade_date <= w_end]   # 买+卖,分段计数用

        review = WeeklyReview(week=week, week_start=w_start, week_end=w_end, strategy_version=gov_version)
        review.round_trips = round_trips
        review.closed_round_trips = closed_week
        review.charter_segments, review.charter_switches = build_charter_timeline(
            w_start, w_end, trades_week, gov_version or "", db_path=db_path,
        )

        review.plan_checks = check_plan_and_ledger(buy_trades_week, db_path=db_path)

        # ① 止损纪律:锚**卖出时刻**(审计的是离场决策,与哨兵当时按现役 stop_pct 提醒同源)。
        for rt in closed_week:
            sell_at = rt.sell_instant
            rt_cfg, _ = _cfg_at(sell_at) if sell_at is not None else (None, None)
            if rt_cfg is not None and rt_cfg.stop_pct is not None:
                kind, note = classify_stop_discipline(rt, rt_cfg.stop_pct)
                review.stop_discipline.append((rt, kind, note))
                if kind == STOP_BREACHED:
                    review.discipline_violations.append(
                        f"{rt.ts_code}({rt.name}) {rt.buy_date}买入→{rt.sell_date}卖出:{note}"
                    )
            else:
                review.stop_discipline.append(
                    (rt, STOP_NOT_APPLICABLE, "现役规则未设固定止损,本回合不做止损纪律判定。")
                )

        # ② 单笔仓位上限 + ④ 禁买过滤:锚**买入时刻**(按买入时刻的章程分组,组内一次调用)。
        #    顺序刻意保持「单笔上限 → 并发/敞口 → 禁买过滤 → 冷却」与 ⑥-A 之前逐位一致
        #    (周内无切换时只有一组/一段,违纪清单逐位等价 = 回归护栏)。
        buy_groups = _group_by_charter(
            buy_trades_week, lambda t: trade_instant(t.trade_date, t.trade_time), _cfg_at,
        )
        for grp_cfg, _grp_ver, grp in buy_groups:
            if grp_cfg is not None:
                review.discipline_violations += check_single_cap(grp, grp_cfg.single_cap)
        # ③ 并发持仓数 / 敞口:**日粒度**,按「该日收盘时刻现役的章程」把本周切成日段,
        #    每段各自求峰值、各自比该段的 cap(见模块头「逐笔取章程」节)。只有一段时
        #    窗口自称仍是「本周」= 与 ⑥-A 之前逐位一致;多段时各自带段标签,免得两条
        #    「本周…」看起来像重复条目。
        day_runs = _charter_day_runs(w_start, w_end, _version_at)
        for run_lo, run_hi, run_ver in day_runs:
            run_cfg, _ = _cfg_at(day_close_instant(run_lo))
            if run_cfg is None:
                continue
            label = "本周" if len(day_runs) == 1 else (
                f"本周 {run_lo.strftime('%m-%d')}~{run_hi.strftime('%m-%d')}"
                f"({run_ver or '未知版本'} 治下)"
            )
            review.discipline_violations += check_position_count_and_exposure(
                round_trips, week_start=run_lo, week_end=run_hi, asof=asof,
                max_positions=run_cfg.max_positions, max_exposure_frac=run_cfg.max_exposure_frac,
                total_capital=total_capital, window_label=label,
            )
        for grp_cfg, _grp_ver, grp in buy_groups:
            if grp_cfg is not None:
                review.discipline_violations += check_entry_screens(grp, grp_cfg, parquet_dir=parquet_dir)
        review.discipline_violations += [
            msg for msg in cooldown_violations if _cooldown_violation_in_week(msg, w_start, w_end)
        ]
        # 时间退出违纪(§2.1 第 2 条周线兜底,审计 🔵-9)。与上面几条不同,本项**不读 cfg**
        # ——判据是「系统当时在 `holding_eod_check` 里记了该走」这一历史事实,不是拿今天的
        # 参数重算(同「不用今天的章程重判历史周」精神)。无现役 config 的库照样能审。
        review.discipline_violations += check_time_exit_discipline(
            w_start, w_end, ledger_positions, time_exit_due
        )

        review.stats = compute_weekly_stats(closed_week, open_count=sum(1 for rt in round_trips if not rt.closed))
        review.forced_review = is_forced_review(review.stats, total_capital)
        if review.forced_review:
            review.forced_review_reason = (
                f"本周实现亏损合计 ¥{abs(review.stats.realized_loss):,.0f},"
                f"达总仓(¥{total_capital:,.0f})的 {abs(review.stats.realized_loss) / total_capital:.1%},"
                f"触及 §2.1 第4条「单周实现亏损 ≥ 总仓 {FORCED_REVIEW_LOSS_FRAC:.0%}」强制复盘线。"
            )
        reviews.append(review)

    return reviews, rt_warnings


def _cooldown_violation_in_week(msg: str, w_start: date, w_end: date) -> bool:
    """`check_cooldown` 的违纪落到"再次买入"的那一周(消息文案里最后一个日期)。
    用简单字符串扫描避免改 `check_cooldown` 的返回结构,cooldown 现役恒为0天时
    本函数不会被调用到任何非空列表(no-op 路径)。"""
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", msg)
    if not dates:
        return True
    try:
        d = date.fromisoformat(dates[-1])
    except ValueError:
        return True
    return w_start <= d <= w_end


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


def plan_check_dict(c: PlanLedgerCheck) -> dict:
    return {
        "tsCode": c.ts_code,
        "name": c.name,
        "tradeDate": c.trade_date.strftime("%Y%m%d"),
        "price": c.price,
        "qty": c.qty,
        "amount": c.amount,
        "planStatus": c.plan_status,
        "ledgerStatus": c.ledger_status,
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
    `reviews.result_json` 落库的形状——两处共用同一份,不重复定义契约)。"""
    return {
        "week": review.week,
        "weekStart": review.week_start.strftime("%Y%m%d"),
        "weekEnd": review.week_end.strftime("%Y%m%d"),
        # 审计 🔵-9:该周 governing 章程版本号(列早已落 `reviews.strategy_version`,但 API
        # 响应/客户端此前看不到「这周用哪版章程判的」)。无版本(纯 legacy 库)→ 空串,
        # 客户端按「未知」展示,不臆造版本名。
        # v1.4-⑥-A:本字段是**周初标签**;该周若发生过章程切换,逐笔实际按哪版判见
        # `charterSegments` / `charterSwitches`(客户端展示「本周发生过章程切换」时读它们,
        # 不要拿 strategyVersion 一个标量去说"整周都按这版判")。
        "strategyVersion": review.strategy_version or "",
        "charterSegments": [
            {
                "version": s.version,
                # 段起始时刻(北京时间 'YYYY-MM-DD HH:MM');null = 自周初起的那一段。
                "start": s.start.strftime("%Y-%m-%d %H:%M") if s.start else None,
                "tradeCount": s.trade_count,
            }
            for s in review.charter_segments
        ],
        "charterSwitches": [
            {
                "at": sw.at.strftime("%Y-%m-%d %H:%M"),
                "fromVersion": sw.from_version,
                "toVersion": sw.to_version,
                "note": sw.note,
            }
            for sw in review.charter_switches
        ],
        "roundTrips": [round_trip_dict(rt) for rt in review.round_trips],
        "closedRoundTrips": [round_trip_dict(rt) for rt in review.closed_round_trips],
        "planChecks": [plan_check_dict(c) for c in review.plan_checks],
        "disciplineViolations": review.discipline_violations,
        "stopDiscipline": [
            {"roundTrip": round_trip_dict(rt), "classification": kind, "note": note}
            for rt, kind, note in review.stop_discipline
        ],
        "stats": weekly_stats_dict(review.stats) if review.stats is not None else None,
        "forcedReview": review.forced_review,
        "forcedReviewReason": review.forced_review_reason,
    }


__all__ = [
    "STOP_TOLERANCE_PP",
    "FORCED_REVIEW_LOSS_FRAC",
    "trade_instant",
    "day_close_instant",
    "CharterSegment",
    "CharterSwitch",
    "build_charter_timeline",
    "RoundTrip",
    "build_round_trips",
    "STOP_BREACHED",
    "STOP_KEPT",
    "STOP_NOT_TRIGGERED",
    "STOP_NOT_APPLICABLE",
    "classify_stop_discipline",
    "PlanLedgerCheck",
    "check_plan_and_ledger",
    "check_single_cap",
    "check_position_count_and_exposure",
    "check_entry_screens",
    "check_cooldown",
    "check_time_exit_discipline",
    "WeeklyStats",
    "compute_weekly_stats",
    "is_forced_review",
    "iso_week_key",
    "week_range",
    "WeeklyReview",
    "run_weekly_review",
    "round_trip_dict",
    "plan_check_dict",
    "weekly_stats_dict",
    "weekly_review_dict",
]
