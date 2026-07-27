"""对账引擎(plan 4D.2)。输入已解析的 `RawTrade` 流 → FIFO 闭合回合 → 「对账三查」
+ 单周统计 + 强制复盘判定,按 ISO 周(`YYYY-Www`)分桶输出 `WeeklyReview`。

**同码不重写铁律(§2.6/§3.8)**:
    · 止损/仓位纪律(止损/回落止盈/hold/单笔上限/持仓数/敞口)读大脑现役
      `MomentumConfig`,不硬编字面量(项目 CLAUDE.md「钉死的领域常量单一源」)。
      **v1.2-A 起按周取「当时现役」**:每 ISO 周以 `week_start` 调
      `brain.config_governing_for_week()` 解析该周 governing 版本(判据「激活日 <
      week_start」= 激活当周仍按旧章程判,2026-07-27 审计 🟡-3 修复;章程升级后
      重跑历史周不洗白旧违纪),不再一次性 `get_active()` 应用到所有周。
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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import polars as pl

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
    __slots__ = ("price", "date", "name", "qty_original", "qty_remaining", "fee_total")

    def __init__(self, price: float, date_: date, name: str, qty: int, fee_total: float) -> None:
        self.price = price
        self.date = date_
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
                buy_queue.append(_Lot(tx.price, tx.trade_date, tx.name, tx.qty, tx.fee))
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
                closed=False,
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
) -> List[str]:
    out = []
    code_intervals = _collapse_code_intervals(round_trips, asof)
    peak_count, peak_count_date = _sweep_max_in_window(
        [(s, e, 1.0) for s, e, _ in code_intervals], week_start, week_end
    )
    if peak_count > max_positions + _EPS:
        out.append(
            f"本周并发持仓最多达 {int(round(peak_count))} 只(约 {peak_count_date}),"
            f"超过最多持 {max_positions} 只的仓位纪律(§2.1 第3条)。"
        )

    exposure_intervals = [(rt.buy_date, rt.sell_date or asof, rt.buy_amount) for rt in round_trips]
    peak_exposure, peak_exposure_date = _sweep_max_in_window(exposure_intervals, week_start, week_end)
    exposure_cap = max_exposure_frac * total_capital
    if peak_exposure > exposure_cap + _EPS:
        out.append(
            f"本周持仓总敞口最高达 ¥{peak_exposure:,.0f}(约 {peak_exposure_date},"
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
    strategy_version: Optional[str] = None   # 本周 governing 大脑版本号(v1.2-A:按 week_end
                                             # 解析「当时现役」,落 reviews.strategy_version)


def run_weekly_review(
    trades: List[RawTrade], *, db_path: Optional[Path] = None, parquet_dir: Optional[Path] = None,
    total_capital: Optional[float] = None,
) -> Tuple[List[WeeklyReview], List[str]]:
    """顶层入口:FIFO 闭合 → 按 ISO 周分桶 → 每周跑「对账三查」+ 统计 + 强制复盘。

    **按周取「当时现役」config(v1.2-A 历史洗白修复 + 2026-07-27 审计 🟡-3)**:每个 ISO 周
    以 `week_start` 为 ref 调 `brain.config_governing_for_week(week_start)`(判据**激活日 <
    week_start**,即**激活当周仍按旧章程判**)解析该周 governing 的大脑版本,用它的
    `MomentumConfig` 判止损/仓位/禁买——**不再一次性 `get_active()` 应用到所有周**。
    否则章程升级(如 single_cap 2 万→4 万)后重跑历史周,当初超限的违纪会被今天的
    上限凭空洗白掉;旧判据(激活日 ≤ week_end)还会在周末/北京周一凌晨激活时,把**刚
    结束那一周**整周交给新章程判(审计实测:该周违纪 1 条 → 0 条)。无现役版本(纯 legacy
    库经兜底退回 `get_active`,仍为 None)时,
    止损纪律/禁买过滤两类检查诚实跳过(不臆造规则)。governing 版本号落
    `review.strategy_version`(→ `reviews.strategy_version` 审计"这周用哪版判的")。

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

    def _cfg_for_week(week_start: date) -> Tuple[Optional[MomentumConfig], Optional[str]]:
        """解析某 ISO 周(以 `week_start` 标识)governing 版本的 (MomentumConfig, 版本号)。
        无 / 非法 config → (None, 版本号或 None):止损/章程检查据此诚实跳过。

        **判据「激活日 < week_start」= 激活当周仍按旧章程判**(2026-07-27 审计 🟡-3 修复,
        用户拍板方案 (a))——旧写法用 `config_active_at(week_end)`(激活日 ≤ week_end),
        周末/北京周一凌晨跑切换器会把**刚结束那一周**整周交给新章程判、洗白该周的违纪。
        判据与理由的唯一源在 `brain.config_governing_for_week` docstring,此处不重述。"""
        gov = brain.config_governing_for_week(week_start, db_path=db_path)
        if gov is None:
            return None, None
        try:
            return MomentumConfig(**gov.rule["config"]), gov.version
        except (KeyError, TypeError):
            return None, gov.version

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

    # cooldown 违纪与具体周次无关(整批算一次,下面按"再次买入"落在哪周分发)——用
    # 数据截止日 asof **所在周** governing 的 cooldown_days(现役恒为 0 时 `check_cooldown`
    # 提前返回空列表,循环体不需要重算)。同走周口径,与逐周判据一致。
    asof_cfg, _ = _cfg_for_week(week_range(iso_week_key(asof))[0])
    cooldown_violations = check_cooldown(round_trips, asof_cfg.cooldown_days) if asof_cfg is not None else []

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

        span_lo = min(t.trade_date for t in trades)
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
        # 按周取「当时现役」config(v1.2-A + 2026-07-27 审计 🟡-3):以 **week_start** 解析该周
        # governing 版本(判据「激活日 < week_start」——激活当周仍按旧章程判,不洗白刚结束的一周)。
        cfg, gov_version = _cfg_for_week(w_start)
        buy_trades_week = [t for t in trades if t.side == "buy" and w_start <= t.trade_date <= w_end]
        closed_week = [rt for rt in round_trips if rt.closed and rt.sell_date and w_start <= rt.sell_date <= w_end]

        review = WeeklyReview(week=week, week_start=w_start, week_end=w_end, strategy_version=gov_version)
        review.round_trips = round_trips
        review.closed_round_trips = closed_week

        review.plan_checks = check_plan_and_ledger(buy_trades_week, db_path=db_path)

        if cfg is not None and cfg.stop_pct is not None:
            for rt in closed_week:
                kind, note = classify_stop_discipline(rt, cfg.stop_pct)
                review.stop_discipline.append((rt, kind, note))
                if kind == STOP_BREACHED:
                    review.discipline_violations.append(
                        f"{rt.ts_code}({rt.name}) {rt.buy_date}买入→{rt.sell_date}卖出:{note}"
                    )
        else:
            for rt in closed_week:
                review.stop_discipline.append((rt, STOP_NOT_APPLICABLE, "现役规则未设固定止损,本回合不做止损纪律判定。"))

        if cfg is not None:
            review.discipline_violations += check_single_cap(buy_trades_week, cfg.single_cap)
            review.discipline_violations += check_position_count_and_exposure(
                round_trips, week_start=w_start, week_end=w_end, asof=asof,
                max_positions=cfg.max_positions, max_exposure_frac=cfg.max_exposure_frac,
                total_capital=total_capital,
            )
            review.discipline_violations += check_entry_screens(buy_trades_week, cfg, parquet_dir=parquet_dir)
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
        "strategyVersion": review.strategy_version or "",
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
