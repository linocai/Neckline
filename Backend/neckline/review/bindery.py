"""行情材料装订(V2.5.0 S11,架构 §六 第 2 件事,PROJECT_PLAN §5.9)。

把一周交割单对出的每一笔 FIFO 回合,配齐**四样同期材料 + 一样当时记录**:

| 材料 | 来源 | 缺了怎么说 |
|---|---|---|
| 该票窗口内日 K + **买卖点标注** | `data/market_data.get_multi_stock_history("daily")` | `bars_missing` |
| **同期大盘**(上证综指) | `index_daily`,代码取 `data/panel.SSE_INDEX` | `benchmark_missing` |
| **同期所属申万二级**行业中位数 | `facts/industry.load_series`(事实层,裁定 2/3) | `industry_unmapped` / `industry_series_missing` |
| **当时那几天的报告快照** | `report/store.load_k9_report_index` | `reports_missing` |
| **当时那几天的预案快照** | K9-v3 冻结成绩包候选 | `playbooks_missing` |

🔴 **本层零 LLM**(架构 §六 逐字:「这一层无 LLM 调用」)。装订只做取数与排版,
好坏结论由用户带着材料去聊天框里得出。守门单测断言 `review/**` 零 import
`neckline.llm` / `neckline.search`。

🔴 **它是「我的成绩」那条线的材料层,与另外两条成绩线完全隔离**(架构 §五)。
方向是**单向**的:本模块**读** `k9_*` 的报告 / 预案 / 清单当材料(架构 §六 明文要求
「配上当时那几天的报告与预案快照」),但 ⛔ **一个字都不往 `k9_*` 写**,
`scorecard/**` 也 ⛔ 零 import `neckline.review` —— 交割单里的成交永远不进清单成绩
或覆盖率的分子分母。两条守门单测各锁一个方向。

🛑 **取数必须一次 glob 到底**(§12 坑 1 的那条账):窗口内**全部票**走一次
`get_multi_stock_history`,大盘走一次,行业 / 报告 / 预案 / 清单各走**一次**区间
SQL。⛔ 别在这里按票或按日写循环取数 —— 逐票 glob 15 次 = 上万个 parquet footer,
生产 2 vCPU 箱上就是十几秒(2026-07-29 信息卡端点 18~20s 的同一条链)。

⚠ **两个「看几根 K 线」的数是上下文长度,不是待标定参数**(同 S9 的
`explain/input.py::KLINE_SESSIONS = 60`):把窗口从 20 改成 30 **不会让任何一笔成交
变成另一笔**,它只影响我在聊天框里看到多长一段图。§8 的 22 项待标定参数里没有它们,
K9 §九 也没有。两个都写死在本模块一处,且 `bind_week(pre_sessions=, post_sessions=)`
是**必填关键字** —— 调用方必须显式说自己要多长。⚠ 已登记进 §13 Backlog 供用户复核:
若用户认为它属于「我会想去调的东西」,按架构 §二 判据它就该进参数包。

⚠ **申万归属用的是「今天的」快照,不是成交当日的**(`sw_industry_member` 只给当前
归属,S2 登记 ④)。这与事实包回填的语义差是同一件事(§5.3.5),写在明处:
`RoundTripBinding.industry_source == 'current_snapshot'`,报告文案也照直说。
⛔ 别写「按成交日回改历史归属」的机灵代码。
⚠ K9-v3 候选及其预案随成绩包冻结；交割单只把它们作为事后回看材料，绝不写回策略账。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from neckline.calendar import trading_days_between
from neckline.data.panel import SSE_INDEX
from neckline.review.reconcile import RoundTrip, WeeklyReview

logger = logging.getLogger(__name__)

#: 大盘基准 = 上证综指。⛔ 不在这里另起一个常量:`data/panel.SSE_INDEX` 已经是全仓
#: 「大盘」的单一源(市场状态过滤器与分层报告基准都读它)。`index_daily` 里落着的
#: 另一条是深证成指,本层刻意只装一条 —— 架构 §六 原文是「同期大盘走势」,单数。
BENCHMARK_CODE = SSE_INDEX
BENCHMARK_NAME = "上证综指"

#: 买入日之前 / 卖出日之后各看几个**交易日**。见模块 docstring:上下文长度,不是参数。
PRE_SESSIONS = 20
POST_SESSIONS = 20

#: 一次装订最多铺多少个交易日的窗口。⚠ 这是**工程容量上限**(同
#: `MAX_LOOKBACK_PACKS` 那类),不是策略参数:它挡的是「一份交割单跨了三年」时
#: 把整段行情读进常驻服务(§12 坑 1)。超了就**如实截断并记 gap**,⛔ 不静默照读。
MAX_WINDOW_SESSIONS = 250

#: 申万归属的来源标签。⛔ 闭合两值,不许现编。
INDUSTRY_SOURCE_CURRENT = "current_snapshot"   # `sw_industry_member` 的当前快照
INDUSTRY_SOURCE_NONE = "unmapped"              # 这只票在申万成分表里查不到

_BAR_COLS = ("open", "high", "low", "close", "pre_close", "vol", "amount")


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


# ══════════════════════════════════════════════════════════════════════════
# DTO
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Bar:
    """一根日 K(原始未复权,与事实层同口径)。`pct_chg` 由 `close/pre_close − 1` 现算
    —— ⛔ 不取 `daily.pct_chg` 那一列:两者在除权日不同源,同一份材料里混用会让
    「那天涨了几个点」出现两个答案。"""

    trade_date: str
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    pct_chg: Optional[float]
    vol: Optional[float]
    amount: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tradeDate": self.trade_date, "open": self.open, "high": self.high,
            "low": self.low, "close": self.close, "pctChg": self.pct_chg,
            "vol": self.vol, "amount": self.amount,
        }


@dataclass(frozen=True)
class TradeMark:
    """买卖点标注。`side` ∈ {buy, sell},价与量取交割单原值(⛔ 不拿 K 线收盘价冒充
    成交价 —— 复盘要看的恰恰是「我成交在那天的什么位置」)。"""

    side: str
    trade_date: str
    price: float
    qty: int

    @property
    def amount(self) -> float:
        return round(self.price * self.qty, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {"side": self.side, "tradeDate": self.trade_date,
                "price": self.price, "qty": self.qty, "amount": self.amount}


@dataclass(frozen=True)
class IndustryPoint:
    trade_date: str
    median_ret: float
    member_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {"tradeDate": self.trade_date, "medianRet": self.median_ret,
                "memberCount": self.member_count}


@dataclass(frozen=True)
class DaySnapshot:
    """「当时那几天」的系统记录(架构 §六 第 2 件事的末项)。

    ⚠ 三样各自独立地「有 / 没有」,⛔ 不许一个总开关罩住:
      · `report` 为 None = 那天**没生成过报告**(≠ 那天报告说「今天没有」——
        后者是一份 `state='empty'` 的**存在**的报告);
      · `listing` 为 None = 那天这只票**不在清单上**;
      · `playbook` 为 None = 那天这只票**没有冻结预案**。
    """

    trade_date: str
    report: Optional[Dict[str, Any]]
    listing: Optional[Dict[str, Any]]
    playbook: Optional[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {"tradeDate": self.trade_date, "report": self.report,
                "listing": self.listing, "playbook": self.playbook}


@dataclass(frozen=True)
class RoundTripBinding:
    """一笔 FIFO 回合的装订材料。`gaps` 逐条说明哪一段没取到、为什么
    —— ⛔ 不许用空列表冒充「查过了没有」。"""

    ts_code: str
    name: str
    buy_date: str
    sell_date: Optional[str]
    qty: int
    buy_price: float
    sell_price: Optional[float]
    net_pnl: Optional[float]
    pnl_pct: Optional[float]
    closed: bool
    window_start: str
    window_end: str
    bars: Tuple[Bar, ...]
    marks: Tuple[TradeMark, ...]
    benchmark: Tuple[Bar, ...]
    industry_source: str
    sw_l2_code: Optional[str]
    sw_l2_name: Optional[str]
    industry: Tuple[IndustryPoint, ...]
    snapshots: Tuple[DaySnapshot, ...]
    gaps: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tsCode": self.ts_code, "name": self.name,
            "buyDate": self.buy_date, "sellDate": self.sell_date, "qty": self.qty,
            "buyPrice": self.buy_price, "sellPrice": self.sell_price,
            "netPnl": self.net_pnl, "pnlPct": self.pnl_pct, "closed": self.closed,
            "windowStart": self.window_start, "windowEnd": self.window_end,
            "bars": [b.to_dict() for b in self.bars],
            "marks": [m.to_dict() for m in self.marks],
            "benchmarkCode": BENCHMARK_CODE, "benchmarkName": BENCHMARK_NAME,
            "benchmark": [b.to_dict() for b in self.benchmark],
            "industrySource": self.industry_source,
            "swL2Code": self.sw_l2_code, "swL2Name": self.sw_l2_name,
            "industry": [p.to_dict() for p in self.industry],
            "snapshots": [s.to_dict() for s in self.snapshots],
            "gaps": list(self.gaps),
        }


@dataclass(frozen=True)
class WeekBinding:
    week: str
    window_start: str
    window_end: str
    pre_sessions: int
    post_sessions: int
    round_trips: Tuple[RoundTripBinding, ...]
    gaps: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "week": self.week,
            "windowStart": self.window_start, "windowEnd": self.window_end,
            "preSessions": self.pre_sessions, "postSessions": self.post_sessions,
            "benchmarkCode": BENCHMARK_CODE, "benchmarkName": BENCHMARK_NAME,
            "roundTrips": [rt.to_dict() for rt in self.round_trips],
            "gaps": list(self.gaps),
            "note": (
                "这是**回看材料**,不是判断。系统只负责解析、装订、存档(架构 §六);"
                "好坏结论请带着这份材料到聊天框里得出,再用「结论存档」存回来。"
                "⚠ 申万归属取的是**当前**成分快照,不是成交当日的快照。"
            ),
        }


# ══════════════════════════════════════════════════════════════════════════
# 窗口
# ══════════════════════════════════════════════════════════════════════════

def _sessions_around(
    lo: date, hi: date, pre: int, post: int
) -> Tuple[Optional[date], Optional[date], List[str]]:
    """`[lo, hi]` 向前展 `pre` 个交易日、向后展 `post` 个交易日。

    ⚠ 「向后」受**交易日历本身**限制:卖在最近一个交易日、日历还没排到未来,
    拿到的就少于 `post` 天 —— 那是**正常**的,⛔ 不当成缺口报警(gap 只记
    「日历读不到」这一真异常)。
    """
    notes: List[str] = []
    # 交易日历按自然日查;取足够宽的自然日缓冲再截取交易日(节假日最长约 10 天)。
    back = trading_days_between(lo - timedelta(days=max(pre, 1) * 2 + 30), lo)
    fwd = trading_days_between(hi, hi + timedelta(days=max(post, 1) * 2 + 30))
    if not back or not fwd:
        notes.append("calendar_unavailable:交易日历读不到,窗口退回成交日本身")
        return lo, hi, notes
    start = back[-(pre + 1)] if len(back) > pre else back[0]
    end = fwd[post] if len(fwd) > post else fwd[-1]
    return start, end, notes


# ══════════════════════════════════════════════════════════════════════════
# 装订
# ══════════════════════════════════════════════════════════════════════════

def _bars_from_rows(rows: Sequence[Dict[str, Any]]) -> Tuple[Bar, ...]:
    out: List[Bar] = []
    for r in rows:
        close, pre_close = r.get("close"), r.get("pre_close")
        pct = None
        if close is not None and pre_close not in (None, 0):
            pct = round((float(close) / float(pre_close) - 1) * 100, 4)
        out.append(Bar(
            trade_date=r["trade_date"], open=r.get("open"), high=r.get("high"),
            low=r.get("low"), close=close, pct_chg=pct,
            vol=r.get("vol"), amount=r.get("amount"),
        ))
    return tuple(out)


def _group_bars(frame) -> Dict[str, List[Dict[str, Any]]]:
    """polars DataFrame → `ts_code → [行字典(trade_date 已转 YYYYMMDD 串)]`。"""
    out: Dict[str, List[Dict[str, Any]]] = {}
    if frame is None or frame.is_empty():
        return out
    for row in frame.iter_rows(named=True):
        raw = row.get("trade_date")
        day = raw.strftime("%Y%m%d") if hasattr(raw, "strftime") else str(raw)
        rec = {k: row.get(k) for k in _BAR_COLS}
        rec["trade_date"] = day
        out.setdefault(row["ts_code"], []).append(rec)
    return out


def bind_week(
    review: WeeklyReview,
    *,
    pre_sessions: int = PRE_SESSIONS,
    post_sessions: int = POST_SESSIONS,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> WeekBinding:
    """把一份 `WeeklyReview` 的全部回合装订成材料。**⛔ 零 LLM、⛔ 零写库。**

    `pre_sessions` / `post_sessions` 见模块 docstring(上下文长度,不是策略参数)。
    调用方给什么就用什么,⛔ 本函数不替谁挑一个「合适的」窗口。
    """
    from neckline.data import market_data as md
    from neckline.data import sw_industry
    from neckline.facts import industry as facts_industry
    from neckline.report import store as report_store
    from neckline.scorecard import packages as package_store

    gaps: List[str] = []
    trips: List[RoundTrip] = list(review.round_trips)
    if not trips:
        return WeekBinding(
            week=review.week, window_start="", window_end="",
            pre_sessions=pre_sessions, post_sessions=post_sessions,
            round_trips=(), gaps=("no_round_trips:本周没有可装订的回合",),
        )

    # —— 窗口:全部回合的并集(一次取数覆盖所有票)————————————————————————
    lo = min(rt.buy_date for rt in trips)
    hi = max((rt.sell_date or rt.buy_date) for rt in trips)

    # 🔴 **容量上限只削上下文,⛔ 不许把成交日削掉**(R2-07)。
    # 从前的写法是「窗口超了就 `start = span[-250]`」—— 只动 `start`、`end` 不动。
    # 窗口被向后拉长时,截断后的 `[start, end]` 会**整段落在成交日之后**,
    # 那一周的每一笔回合都拿到 0 根 K 线(材料是空的);同样的形状在正常场景也会
    # 出现:一份跨度较长的交割单(早开仓、本周才平)会让早期回合被从最早处截掉。
    # 现在的判据:先把**成交日区间**留住,余下的额度才分给前后上下文。
    core = trading_days_between(lo, hi)
    budget = MAX_WINDOW_SESSIONS - len(core)
    eff_pre, eff_post = pre_sessions, post_sessions
    if budget <= 0:
        eff_pre = eff_post = 0
        gaps.append(
            f"window_truncated:成交日区间本身就有 {len(core)} 个交易日"
            f"(容量上限 {MAX_WINDOW_SESSIONS}),本次前后各铺 0 个交易日 —— "
            f"⛔ 成交日一天都没截(每一笔回合仍有自己的 K 线)")
    elif pre_sessions + post_sessions > budget:
        total = pre_sessions + post_sessions
        eff_pre = int(budget * pre_sessions / total)
        eff_post = budget - eff_pre
        gaps.append(
            f"window_truncated:前后上下文 {pre_sessions}/{post_sessions} 超出容量上限 "
            f"{MAX_WINDOW_SESSIONS}(成交日已占 {len(core)} 个交易日),"
            f"本次按 {eff_pre}/{eff_post} 铺 —— ⛔ 成交日区间一天都没截")
    start, end, notes = _sessions_around(lo, hi, eff_pre, eff_post)
    gaps.extend(notes)

    codes = sorted({rt.ts_code for rt in trips})

    # —— 一次 glob:全部票的日 K ————————————————————————————————————————
    try:
        panel = md.get_multi_stock_history(
            codes, start, end, table="daily", columns=_BAR_COLS, parquet_dir=parquet_dir)
    except Exception:  # noqa: BLE001  取数炸了不该让整份材料没有
        logger.warning("[bindery] 日 K 取数异常(已降级)", exc_info=True)
        panel = None
        gaps.append("bars_unavailable:日 K 取数异常,详见服务端日志")
    bars_by_code = _group_bars(panel)

    # —— 一次 glob:同期大盘 ————————————————————————————————————————————
    try:
        idx = md.get_index_history(BENCHMARK_CODE, start, end, parquet_dir=parquet_dir)
    except Exception:  # noqa: BLE001
        logger.warning("[bindery] 大盘取数异常(已降级)", exc_info=True)
        idx = None
        gaps.append("benchmark_unavailable:大盘取数异常,详见服务端日志")
    bench_rows = _group_bars(idx).get(BENCHMARK_CODE, [])
    bench = _bars_from_rows(bench_rows)
    if not bench:
        gaps.append(f"benchmark_missing:{BENCHMARK_CODE} 在本窗口没有 index_daily 分区")

    # —— 申万归属(当前快照)+ 一次区间查询取行业中位数 ————————————————————
    try:
        l2_map = sw_industry.load_l2_map(db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[bindery] 申万成分表读取异常(已降级)", exc_info=True)
        l2_map = {}
        gaps.append("industry_map_unavailable:申万成分表读取异常,详见服务端日志")
    l2_of: Dict[str, Tuple[str, str]] = {c: l2_map[c] for c in codes if c in l2_map}
    series = facts_industry.load_series(
        [v[0] for v in l2_of.values()], start, end, db_path=db_path)

    # —— 一次区间查询:报告索引 / K9-v3 不可变包 ————————————————————————
    report_index = report_store.load_k9_report_index(start, end, db_path=db_path)
    membership: Dict[Tuple[str, str], Dict[str, Any]] = {}
    playbooks: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for summary in package_store.list_packages(state="active", db_path=db_path) + package_store.list_packages(state="settled", db_path=db_path):
        selected = str(summary["selection_date"])
        if not (_d(start) <= selected <= _d(end)):
            continue
        package = package_store.load_package(str(summary["batch_id"]), db_path=db_path)
        if package is None:
            continue
        for candidate in package["candidates"]:
            code = candidate["tsCode"]
            if code not in codes:
                continue
            membership[(selected, code)] = {
                "batch_id": summary["batch_id"], "channels": candidate["channels"],
                "channel_ranks": candidate["channelRanks"],
            }
            playbooks[(selected, code)] = candidate["playbook"]
    if not report_index:
        gaps.append("reports_missing:本窗口一份 K9 报告都没有(v2.5.0 上线前的日子属正常)")

    window_days = [_d(d) for d in trading_days_between(start, end)]

    bindings: List[RoundTripBinding] = []
    for rt in trips:
        rt_gaps: List[str] = []
        rows = bars_by_code.get(rt.ts_code, [])
        bars = _bars_from_rows(rows)
        if not bars:
            rt_gaps.append(f"bars_missing:{rt.ts_code} 在本窗口没有 daily 分区")

        marks = [TradeMark("buy", _d(rt.buy_date), rt.buy_price, rt.qty)]
        if rt.closed and rt.sell_date and rt.sell_price is not None:
            marks.append(TradeMark("sell", _d(rt.sell_date), rt.sell_price, rt.qty))

        pair = l2_of.get(rt.ts_code)
        if pair is None:
            source, l2_code, l2_name = INDUSTRY_SOURCE_NONE, None, None
            rt_gaps.append(
                f"industry_unmapped:{rt.ts_code} 在 sw_industry_member 当前快照里查不到 "
                "(退市 / 成分表未刷新 / 北交所等)")
            points: Tuple[IndustryPoint, ...] = ()
        else:
            source, (l2_code, l2_name) = INDUSTRY_SOURCE_CURRENT, pair
            raw = series.get(l2_code, [])
            points = tuple(IndustryPoint(d, m, n) for d, m, n in raw)
            if not points:
                rt_gaps.append(
                    f"industry_series_missing:{l2_code} 在本窗口没有 sw_industry_daily 行"
                    "(那几天没冻结过事实包,⛔ 不是行业当天没涨跌)")

        snaps: List[DaySnapshot] = []
        for day in window_days:
            rep = report_index.get(day)
            lst = membership.get((day, rt.ts_code))
            pb = playbooks.get((day, rt.ts_code))
            if rep is None and lst is None and pb is None:
                continue          # 那天系统什么都没留下,不铺一行空壳
            snaps.append(DaySnapshot(
                trade_date=day, report=rep, listing=lst,
                playbook=pb,
            ))
        if not any(s.playbook for s in snaps):
            rt_gaps.append(
                f"playbooks_missing:{rt.ts_code} 在本窗口没有任何冻结预案"
                "(那几天它没进过清单,或 v2.5.0 还没上线)")

        bindings.append(RoundTripBinding(
            ts_code=rt.ts_code, name=rt.name,
            buy_date=_d(rt.buy_date),
            sell_date=_d(rt.sell_date) if rt.sell_date else None,
            qty=rt.qty, buy_price=rt.buy_price, sell_price=rt.sell_price,
            net_pnl=rt.net_pnl, pnl_pct=rt.pnl_pct, closed=rt.closed,
            window_start=_d(start), window_end=_d(end),
            bars=bars, marks=tuple(marks), benchmark=bench,
            industry_source=source, sw_l2_code=l2_code, sw_l2_name=l2_name,
            industry=points, snapshots=tuple(snaps), gaps=tuple(rt_gaps),
        ))

    return WeekBinding(
        week=review.week, window_start=_d(start), window_end=_d(end),
        # ⚠ 记的是**这次真的铺了几天**(可能被容量上限削过,R2-07),⛔ 不是请求值 ——
        # 材料首行那句「买入前 N 个交易日」得说真话。
        pre_sessions=eff_pre, post_sessions=eff_post,
        round_trips=tuple(bindings), gaps=tuple(gaps),
    )


# ══════════════════════════════════════════════════════════════════════════
# 排版(确定性文案,⛔ 零 LLM)
# ══════════════════════════════════════════════════════════════════════════

def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:+.2f}%"


def render_binding_markdown(binding: WeekBinding) -> str:
    """把装订材料排成一段可**整段复制到聊天框**的 markdown。

    ⛔ 这里不下任何结论、不做任何评价 —— 排版就是排版(架构 §六:系统之外的那一半
    才是对话与总结)。缺口逐条印在材料里,⛔ 不许悄悄略过。
    """
    out: List[str] = [f"# 交割单分析台 · {binding.week}", ""]
    if not binding.round_trips:
        out.append("本周没有可装订的回合(交割单里没有成交,或全部成交都不在本周)。")
        return "\n".join(out)

    out.append(
        f"窗口 {binding.window_start}—{binding.window_end}"
        f"(买入前 {binding.pre_sessions} 个交易日、卖出后 {binding.post_sessions} 个交易日),"
        f"大盘基准 {BENCHMARK_NAME} {BENCHMARK_CODE}。"
    )
    out.append("")
    out.append("> 这是**回看材料**,不是判断。系统只负责解析、装订、存档;"
               "结论请在聊天框里得出,再用「结论存档」存回来。")
    out.append("> ⚠ 申万归属取的是**当前**成分快照,不是成交当日的快照。")
    out.append("")
    if binding.gaps:
        out.append("**整份材料层面的缺口**:")
        out.extend(f"- {g}" for g in binding.gaps)
        out.append("")

    for i, rt in enumerate(binding.round_trips, 1):
        held = f"{rt.buy_date} → {rt.sell_date}" if rt.sell_date else f"{rt.buy_date} → 未平仓"
        out.append(f"## {i}. {rt.name}({rt.ts_code}) · {held}")
        out.append("")
        pnl = "—" if rt.net_pnl is None else f"¥{rt.net_pnl:,.2f}"
        out.append(
            f"- 成交:买 {rt.buy_price} × {rt.qty}"
            + (f",卖 {rt.sell_price}" if rt.sell_price is not None else ",尚未卖出")
            + f";净盈亏 {pnl},价格回报 {_pct(rt.pnl_pct)}"
        )
        if rt.sw_l2_code:
            out.append(f"- 申万二级:{rt.sw_l2_name}({rt.sw_l2_code},来源:当前成分快照)")
        else:
            out.append("- 申万二级:**查不到**(见下方缺口)")
        out.append(f"- 日 K:{len(rt.bars)} 根;同期大盘:{len(rt.benchmark)} 根;"
                   f"同期行业中位数:{len(rt.industry)} 个交易日")

        if rt.snapshots:
            out.append("- 当时那几天的系统记录:")
            for s in rt.snapshots:
                bits: List[str] = []
                if s.report:
                    bits.append(f"报告「{s.report.get('headline', '')}」")
                if s.listing:
                    bits.append(f"清单第 {s.listing.get('rank')} 位"
                                f"({s.listing.get('primary_pattern')}/{s.listing.get('tier')})")
                if s.playbook:
                    lv = s.playbook.get("levels") or {}
                    bits.append(
                        f"预案 v{s.playbook.get('version')}:第一压力位 "
                        f"{lv.get('firstResistance')} / 失效位 {lv.get('invalidation')}")
                out.append(f"  - {s.trade_date}:" + ";".join(bits))
        else:
            out.append("- 当时那几天的系统记录:**一条都没有**(见下方缺口)")

        if rt.gaps:
            out.append("- 缺口:")
            out.extend(f"  - {g}" for g in rt.gaps)
        out.append("")

    return "\n".join(out)


__all__ = [
    "BENCHMARK_CODE", "BENCHMARK_NAME",
    "PRE_SESSIONS", "POST_SESSIONS", "MAX_WINDOW_SESSIONS",
    "INDUSTRY_SOURCE_CURRENT", "INDUSTRY_SOURCE_NONE",
    "Bar", "TradeMark", "IndustryPoint", "DaySnapshot",
    "RoundTripBinding", "WeekBinding",
    "bind_week", "render_binding_markdown",
]
