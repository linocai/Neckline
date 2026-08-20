"""次日核对的**冻结抓取**(V2.5.0 S8;自 `git show eac2823:.../auction/collect.py`
(616 行)取回再改,⛔ 未凭空重写)。

职责只有一件:**组装标的清单 → 拉一次价 → 冻结成 `Snapshot`**,外加数据质量
(来源 / 抓取时刻 / 覆盖率 / 缺失 / 冲突)。

⛔ **不干什么**:不判定(那是 `checklist.py` / `settle.py`)、不落库(那是 `store.py`)。

🔴 **取回时保住的原有纪律**(逐条):
    · **自己拉一次价**,⛔ 不搭别人的便车;
    · 🔴 **拉价前用真实时钟复判窗口** —— 已越窗就**一条价都不拉、零落库**
      (组清单本身要读库,慢的早晨到这里可能已经过点了);
    · 🔴 `captured_at` = **真正拉完价的那一刻**,⛔ 不是轮询那一拍的名义 `now`;
    · 拉价**跨过**窗口右端 → 报告照落、`captured_in_window` 为假 → 样本域降级;
    · **拉价失败不掀翻本层**:异常只 WARNING + 一条 note,快照照常冻结
      (`quotes` 为空 → `insufficient`)——「一条都没抓到」是要如实报出来的事实,不是崩溃;
    · 「两源都没拉到」(`missing`)与「拉到了但不合格」(`invalid`)**分两栏**,
      ⛔ 别合并:前者是网络 / 限流,后者是数据本身有问题,排障方向完全相反。

🔴 **改掉的 K8 语义**(⛔ 不许找回来):
    · **抓取清单 = D0 清单上那几只票**(≤ `quota.max` = 20 只,天然有界),
      ⛔ 不再有 T1/T2 篮子成员、盘中关注池、独立观察池、竞价强势股、板块对照股;
    · **市场对照指数整块删除**:K9 的核对表是「代入 D0 冻结预案逐条比对」的
      **纯条件求值**(架构 §四),它不需要市场环境 —— 那是 K8 的 LLM 解释才需要的;
    · `prev_close` / `prev_low` / `prev_high` 一律取 **D0 冻结事实包**,
      ⛔ 不取实时源自带的 `pre_close`(⚠ 源的 `pre_close` 仍参与七项校验,
      那是「这条读数自洽吗」;而预案条件里的「昨日」必须是**冻结件**,
      否则同一份条件在两拍之间可能踩到两个不同的昨收)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.auction import (
    DQ_DEGRADED,
    DQ_INSUFFICIENT,
    DQ_OK,
    QF_FRESH,
    SKIP_WINDOW_CLOSED,
)
from neckline.auction.quality import QuoteQuality, resolve_dual
from neckline.data.realtime import DualQuote, Quote, get_quotes_dual
from neckline.playbook.model import gap_percent_points

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrevBar:
    """D0 的收 / 低 / 高(**取自冻结事实包**,⛔ 不取实时源)。

    预案条件里的 `prev_close` / `prev_low` / `prev_high` 全从这里来 ——
    它是 D0 那一刻就冻住的事实,两拍读到的是同一个数。"""

    ts_code: str
    close: Optional[float] = None
    low: Optional[float] = None
    high: Optional[float] = None


@dataclass(frozen=True)
class Snapshot:
    """一次冻结抓取的产物。**冻结件:构造完就不再变。**

    ⚠ 它只装「抓到了什么」,**不装任何判定** —— 一个 verdict 都不在这里
    (那是 `checklist.py` / `settle.py` 的活)。
    """

    trade_date: date                       # D1 = 核对发生这天
    d0_date: date                          # 被验证的 D0
    window: Tuple[time, time]              # 本次的窗口(左闭右开)
    #: 🔴 **真正拉完价的那一刻**(不是轮询那一拍的时刻)。
    captured_at: datetime
    #: 拉价**开始**的时刻。与 `captured_at` 一起,拉价耗时是可查的(⛔ 别只留一个点)。
    fetch_started_at: Optional[datetime] = None
    #: 非空 = **拉价前复判窗口已关**,本次一条价都没拉、⛔ 零落库。
    fetch_skipped_reason: str = ""
    requested: Tuple[str, ...] = ()
    #: **选用的**那一份读数(双源核验后的胜出者,只装通过校验的)。
    quotes: Mapping[str, Quote] = field(default_factory=dict)
    missing: Tuple[str, ...] = ()          # 清单里、**两源都没拉到**的
    invalid: Tuple[str, ...] = ()          # 抓到了、但七项校验没过的
    quote_quality: Mapping[str, QuoteQuality] = field(default_factory=dict)
    #: 两源的**原始 `Quote` 对象**(只在内存里传,⛔ 不落库 —— 落库的是上面那份账)。
    dual_quotes: Mapping[str, DualQuote] = field(default_factory=dict)
    conflicts: Tuple[str, ...] = ()
    #: D0 冻结事实包给的昨收 / 昨低 / 昨高。
    prev_bars: Mapping[str, PrevBar] = field(default_factory=dict)
    notes: Tuple[str, ...] = ()

    @property
    def source(self) -> str:
        """逐票 `Quote.source` 汇总:`sina` | `tencent` | `mixed` | `unknown`。
        ⚠ **一条都没抓到 → `unknown`**,⛔ 不拿主源名冒充。"""
        srcs = {str(getattr(q, "source", "") or "unknown") for q in self.quotes.values()}
        if not srcs:
            return "unknown"
        if len(srcs) == 1:
            return next(iter(srcs))
        return "mixed"

    @property
    def captured_in_window(self) -> bool:
        """**真正拉完价的那一刻**落在本次窗口内。越窗 = 这份快照不是「那一刻」的
        → 样本域降级。🔴 这是第二层:拉价**前**复判(零落库)拦住「整段都在窗外」,
        这个属性拦住「拉价跨过了窗口右端」—— 两层缺一不可。"""
        start, end = self.window
        return start <= self.captured_at.time() < end

    @property
    def fetch_elapsed_sec(self) -> Optional[float]:
        if self.fetch_started_at is None:
            return None
        return (self.captured_at - self.fetch_started_at).total_seconds()

    def is_usable(self, code: str) -> bool:
        """这一格**有没有可用读数**。🔴 判据是「双源核验后 `freshness != insufficient`」,
        **⛔ 不是「`code in quotes`」** —— 上一交易日的缓存行情**也在 `quotes` 里**,
        长得跟正常读数一模一样。"""
        qq = (self.quote_quality or {}).get(code)
        if qq is None:
            return code in self.quotes
        return bool(qq.usable) and code in self.quotes

    def price_of(self, code: str) -> Optional[float]:
        """现价。🔴 **不可用的读数一律 `None`** —— 拿昨天的收盘价算出「今天涨了 7%」
        再据此把一只票判成「放弃」,是本系统明令要掐掉的那件事。"""
        if not self.is_usable(code):
            return None
        q = self.quotes.get(code)
        v = getattr(q, "price", None) if q is not None else None
        return float(v) if v else None

    def _positive(self, code: str, attr: str) -> Optional[float]:
        if not self.is_usable(code):
            return None
        q = self.quotes.get(code)
        v = getattr(q, attr, None) if q is not None else None
        try:
            f = float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
        # 0 = 「源还没发这一位」,⛔ 不是「今天的最低价是 0 元」。
        return f if (f is not None and f > 0) else None

    def open_of(self, code: str) -> Optional[float]:
        return self._positive(code, "open")

    def low_of(self, code: str) -> Optional[float]:
        return self._positive(code, "low")

    def high_of(self, code: str) -> Optional[float]:
        return self._positive(code, "high")

    def gap_of(self, code: str) -> Optional[float]:
        """竞价涨跌幅(**百分点**)。分母取 **D0 冻结事实包**的收盘价,
        ⛔ 不取源自带的 `pre_close`(见模块头)。"""
        prev = self.prev_bars.get(code)
        return gap_percent_points(self.price_of(code), prev.close if prev else None)

    def quality_of(self, codes: Sequence[str]) -> str:
        """某个样本域的数据质量三态(**结构性判据,⛔ 不是百分比阈值**)。

            insufficient = 样本域里一条**可用读数**都没有
            ok           = 每一格都可用 且 跨源冲突为空 且 captured_at 在窗内
            degraded     = 其余

        ⚠ 样本域**为空**也判 `insufficient` ——「没有可判的东西」与「判过了都好」
        必须分得开。"""
        want = list(dict.fromkeys(codes))
        if not want:
            return DQ_INSUFFICIENT
        got = [c for c in want if self.is_usable(c)]
        if not got:
            return DQ_INSUFFICIENT
        conflicted = set(self.conflicts) & set(want)
        qq = self.quote_quality or {}
        all_fresh = all((c not in qq) or qq[c].freshness == QF_FRESH for c in want)
        if len(got) == len(want) and all_fresh and not conflicted and self.captured_in_window:
            return DQ_OK
        return DQ_DEGRADED


def load_prev_bars(
    d0_date: date, codes: Sequence[str], *,
    parquet_dir: Optional[Path] = None, db_path: Optional[Path] = None,
) -> Dict[str, PrevBar]:
    """从 **D0 冻结事实包**读昨收 / 昨低 / 昨高。

    包没冻结 / 那只票不在包里 → 该票**缺席**(⛔ 不补一个近似值):
    缺了它,依赖 `prev_*` 的条件求值就是 `UNKNOWN`,那只票落「观察」——
    这正是我们要的诚实结果。"""
    from neckline.facts import store as facts_store

    try:
        pack = facts_store.load_pack(d0_date, parquet_dir=parquet_dir, db_path=db_path)
    except Exception:  # noqa: BLE001 —— 包没冻结是可读出来的事实,不是崩溃
        logger.warning("[auction] D0 %s 的事实包读不到,本次无 prev_* 读数", d0_date,
                       exc_info=True)
        return {}
    want = set(codes)
    out: Dict[str, PrevBar] = {}
    rows = pack.rows.select(["ts_code", "close", "low", "high"])
    for r in rows.iter_rows(named=True):
        if r["ts_code"] in want:
            out[r["ts_code"]] = PrevBar(
                ts_code=r["ts_code"],
                close=None if r["close"] is None else float(r["close"]),
                low=None if r["low"] is None else float(r["low"]),
                high=None if r["high"] is None else float(r["high"]),
            )
    return out


def collect_snapshot(
    trade_date: date,
    now: datetime,
    *,
    codes: Sequence[str],
    window: Tuple[time, time],
    d0_date: date,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    dual_quotes_fn: Optional[Callable[[List[str]], Dict[str, DualQuote]]] = None,
    quotes_fn: Optional[Callable[[List[str]], Dict[str, Quote]]] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
    rejection_of: Optional[Callable[[str, Quote], Optional[bool]]] = None,
    prev_bars: Optional[Mapping[str, PrevBar]] = None,
) -> Snapshot:
    """拉一次价并冻结。

    🔴 **走双源**(`data.realtime.get_quotes_dual`):核验需要两个可以互相打架的读数。
    ⚠ **只给 `quotes_fn` = 单源替身**:那时备源恒缺席、跨源冲突结构性为空 ——
    这是替身的局限,**不是「已核对无冲突」**(逐票账里 `checks` 只有一条,一眼看得出)。

    🔴 **`now_fn` 是「真实时钟」,`now` 是「那一拍的名义时刻」**:`captured_at` 一律取
    `fetch()` 返回**之后**的 `now_fn()`,`fetch_started_at` 取调用之前的那一次。
    ⚠ 拉价**前**还会用 `now_fn()` **复判一次窗口**:已越窗就一条价都不拉、
    `fetch_skipped_reason` 非空,编排层据此**零落库**。

    `rejection_of(code, quote) -> True/False/None` 是**放弃分支**的三态钩子
    (由 `checklist.py` 提供,它才拿得到 D0 冻结预案)。⚠ 不给 = 本次不做那一类
    跨源比较,⛔ 不是「没冲突」。
    """
    clock: Callable[[], datetime] = now_fn or datetime.now
    start, end = window
    notes: List[str] = []
    requested = list(dict.fromkeys(c for c in codes if c))
    # ⚠ 调用方多半已经读过一次 D0 事实包(它要拿 `prev_*` 建跨源冲突钩子)——
    # 传进来就**不再读第二遍**(读的是同一份冻结件,重复读只是白花一次 parquet IO)。
    bars: Mapping[str, PrevBar] = (
        prev_bars if prev_bars is not None
        else load_prev_bars(d0_date, requested, parquet_dir=parquet_dir, db_path=db_path))
    if requested and not bars:
        notes.append("prev_bars_unavailable")

    # 🔴 **拉价前复判窗口**(第一层):组清单要读库,到这里可能已经过点了。
    # 越窗就**一条价都不拉、零落库** —— 拉了就是拿窗口之后的价格冒充那一刻。
    fetch_started_at = clock()
    if not (start <= fetch_started_at.time() < end):
        logger.warning("[auction] 拉价前复判:窗口已关(%s),本次一条价都不拉、零落库",
                       fetch_started_at.isoformat(timespec="seconds"))
        return Snapshot(
            trade_date=trade_date, d0_date=d0_date, window=window,
            captured_at=fetch_started_at, fetch_started_at=fetch_started_at,
            fetch_skipped_reason=SKIP_WINDOW_CLOSED,
            requested=tuple(requested), quotes={}, missing=tuple(requested),
            prev_bars=bars, notes=tuple(notes + [SKIP_WINDOW_CLOSED]),
        )

    if dual_quotes_fn is not None:
        fetch_dual = dual_quotes_fn
    elif quotes_fn is not None:
        def fetch_dual(cs: List[str]) -> Dict[str, DualQuote]:
            single = dict(quotes_fn(cs) or {})
            return {c: DualQuote(code=c, primary=single.get(c)) for c in cs}
    else:
        fetch_dual = get_quotes_dual

    duals: Dict[str, DualQuote] = {}
    if requested:
        try:
            duals = dict(fetch_dual(requested) or {})
        except Exception:  # noqa: BLE001 —— 拉价失败不掀翻本层
            logger.warning("[auction] 批量拉价失败,本次快照为空(如实标 insufficient)",
                           exc_info=True)
            notes.append("quotes_fetch_failed")
    # 🔴 **冻结时刻 = 真正拉完价的这一刻**(⛔ 不是轮询那一拍的 `now`)。
    captured_at = clock()

    quotes: Dict[str, Quote] = {}
    quote_quality: Dict[str, QuoteQuality] = {}
    dual_by_code: Dict[str, DualQuote] = {}
    for code in requested:
        d = duals.get(code) or DualQuote(code=code)
        dual_by_code[code] = d
        hook = None
        if rejection_of is not None:
            hook = (lambda q, _c=code: rejection_of(_c, q))  # noqa: E731
        chosen, qq = resolve_dual(code, d, trade_date=trade_date, captured_at=captured_at,
                                  rejection_of=hook)
        quote_quality[code] = qq
        if chosen is not None and qq.usable:
            quotes[code] = chosen
    degraded_sources = [c for c, qq in quote_quality.items() if qq.source_degraded and qq.usable]
    if degraded_sources:
        logger.info("[auction] %d 只改用备源(主源不可用 / 未通过校验):%s",
                    len(degraded_sources), "、".join(sorted(degraded_sources)[:20]))
        notes.append(f"source_degraded:{len(degraded_sources)}")
    if not (start <= captured_at.time() < end):
        # 拉价**跨过了**窗口右端:快照照常返回(读数不能丢),但 `captured_in_window`
        # 为假 → 数据质量降级。
        logger.warning("[auction] 拉价跨过了窗口右端(开始 %s / 完成 %s),本次数据质量降级",
                       fetch_started_at.isoformat(timespec="seconds"),
                       captured_at.isoformat(timespec="seconds"))
        notes.append("captured_out_of_window")

    # 🔴 「两源都没拉到」与「拉到了但不合格」**分成两栏**(⛔ 别合并)。
    missing = tuple(c for c in requested
                    if not quote_quality.get(c) or not quote_quality[c].checks)
    invalid = tuple(c for c in requested
                    if c not in missing and not quote_quality[c].usable)
    conflicts = tuple(c for c in requested
                      if quote_quality.get(c) is not None and quote_quality[c].conflict)

    return Snapshot(
        trade_date=trade_date, d0_date=d0_date, window=window,
        captured_at=captured_at, fetch_started_at=fetch_started_at,
        requested=tuple(requested), quotes=quotes,
        missing=missing, invalid=invalid, quote_quality=quote_quality,
        dual_quotes=dual_by_code, conflicts=conflicts,
        prev_bars=bars, notes=tuple(notes),
    )


__all__ = [
    "PrevBar", "Snapshot", "load_prev_bars", "collect_snapshot",
]
