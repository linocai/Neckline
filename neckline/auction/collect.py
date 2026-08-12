"""竞价层的**冻结抓取**(V2.3.3-②,K8.md §二十「输入范围」/「数据来源与边界」)。

职责只有一件:**组装标的清单 → 拉一次价 → 冻结成 `AuctionSnapshot`**,外加数据质量
五项(来源 / 抓取时刻 / 覆盖率 / 缺失 / 冲突)。

⛔ **不干什么**:不判定(那是 `mech.py`)、不落库(那是 `store.py`)、不写 parquet
(存拍归 `sentinel/capture.py`,那是另一条独立旁路)。

🔴 **自己拉一次价,⛔ 不搭 `precall` / `capture` 的便车**(同 `capture.run_auction_capture`
docstring 里的既有理由:让存拍与纪律外壳彻底解耦)。代价 = **每早多一次批量请求**
(9:25:30 precall + 9:25 capture + 9:26 auction = 3 次 / 早晨,相对既有 ~240 次 / 天
可忽略),已如实登记进 §五 ⑨-B-5 —— **部署次日必须查 journal 有没有拉价失败或限流**。

**抓取清单 = 五组去重并集**(K8 §二十「输入范围」逐条):
    1. D0 全部 T1/T2 篮子成员 ← `sentinel/universe.load_watch_universe(...).baskets`;
    2. 各篮子的主线核心 / 容量核心 ← 卡上 `members[].role`(与第 1 组**同集合**,
       只是角色标注,不额外扩码);
    3. 🔴 上证 + 深证 + 创业板三支指数,**显式并入**。⛔ 不许改
       `universe.py::_related_index_codes` —— 那个函数只按"关注池里出现过的板块"
       加指数(当天没有创业板票 → 创业板指就不在池里),改它会同时改掉哨兵与存拍的
       关注池;**竞价层自己多要这三个码**,零副作用;
    4. 候选所属板块的基准指数 ← `BOARD_BENCHMARK_INDEX`(同 `universe.py` 唯一源);
    5. 竞价强势股(**代理样本**)← 关注池里 `gap_pct > 0` 且**不属于任何 T1/T2 篮**的
       标的 —— ⚠ **不截断**(§五 ⑨-A 第 5 行:截断需要一个 K8 没给的数;且关注池本身
       就是代理样本,不是全市场明细)。⚠ 第 5 组不是"再拉一批码":`gap_pct` 只有拉完价
       才知道,所以它是**同一次抓取结果里的一个子集**,不多打一次请求。

🔴 **「板块对照股」已由用户拍板落地(裁定 P3-70,2026-08-12)**:板块基准走
「≥3 只有效**板块对照股**竞价涨跌幅的中位数」,取数域 = 关注池里与该票**同行业**
(`stock_basic.industry`,本仓钉死的行业口径)且**不属于本篮**的票 —— 本模块负责把
`industry_of`(个股 → 行业)冻进快照,判定与中位数在 `mech.py`。
⚠ 原「降级为板块基准指数、逐股对照本版不做 → §七 P3-65」那半条**已销案**;
P3-65 的另一半(周度竞价聚合不下发客户端)不受影响、仍挂着。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from neckline.auction import (
    DQ_DEGRADED,
    DQ_INSUFFICIENT,
    DQ_OK,
    NOTE_INDUSTRY_MAP_UNAVAILABLE,
    REL_UNDET_BOARD_EXCLUDED,
    REL_UNDET_NO_BOARD_META,
)
from neckline.calendar import prev_trading_day
from neckline.data.board import Board
from neckline.sentinel.quotes import Quote, get_quotes
from neckline.sentinel.universe import (
    BOARD_BENCHMARK_INDEX,
    DEFAULT_BREADTH_CAP,
    MAIN_BOARD_INDEX_SH,
    MAIN_BOARD_INDEX_SZ,
    StockMeta,
    WatchUniverse,
    load_prev5_avg_volume,
    load_stock_meta,
    load_watch_universe,
)
from neckline.selection.basket_store import BasketRef

logger = logging.getLogger(__name__)

#: 三支市场对照指数(K8 §二十「上证、深证、创业板等市场对照指数」)。**显式并入抓取
#: 清单**,不靠 `universe._related_index_codes` 的"池里出现过才加"逻辑。
MARKET_INDEX_CODES: Tuple[str, ...] = (
    MAIN_BOARD_INDEX_SH,                 # 000001.SH 上证综指
    MAIN_BOARD_INDEX_SZ,                 # 399001.SZ 深证成指
    BOARD_BENCHMARK_INDEX[Board.GEM],    # 399006.SZ 创业板指
)

#: 冻结窗口(K8 §二十 原文给的,⛔ 不是本项目发明的数)。`data_quality=ok` 要求
#: `captured_at` 落在这个左闭右开区间内 —— 越窗 = 这份快照不是"9:26 那一刻"的。
AUCTION_WINDOW_START = time(9, 26)
AUCTION_WINDOW_END = time(9, 29)

#: 拉价前复判发现窗口已关时的 `fetch_skipped_reason`(编排层据此**零落库**)。
SKIP_WINDOW_CLOSED = "window_closed_before_fetch"


def market_index_of(board: Optional[Board], ts_code: str) -> Tuple[Optional[str], Optional[str]]:
    """个股 → **市场指数**(`rel_to_index` 的独立路径),返回 `(指数码, 取不到的原因码)`。

    🔴 **用户裁定 P3-70(2026-08-12)逐条照抄,⛔ 不许加减**:
        沪市主板 → 上证指数 · 深市主板 → 深证成指 · 创业板 → 创业板指 · 北交所 → 北证50 ·
        **科创板按 K8 §三「排除科创板股票」的规则排除**。

    🔴 **科创板返回 `None` + `board_excluded`,⛔ 绝不 fallback 到别的指数** ——
    拿上证综指顶替一只科创板票的市场基准,是把「这个数按规则不该有」讲成「这个数是这么多」。
    ⚠ 码的**唯一源仍是 `sentinel/universe.py`**(`BOARD_BENCHMARK_INDEX` /
    `MAIN_BOARD_INDEX_SH` / `MAIN_BOARD_INDEX_SZ`),⛔ 本包不抄第二份。
    ⚠ 这条路径与 `rel_to_sector` 的板块基准**刻意分开**(裁定:禁止同源同值);
    `benchmark_of`(板块协同段展示用的板块基准指数)是**另一件事**,⛔ 别把两者合并。
    """
    if board is None:
        return None, REL_UNDET_NO_BOARD_META
    if board == Board.STAR:
        return None, REL_UNDET_BOARD_EXCLUDED
    if board == Board.MAIN:
        return (MAIN_BOARD_INDEX_SH if ts_code.upper().endswith(".SH")
                else MAIN_BOARD_INDEX_SZ), None
    code = BOARD_BENCHMARK_INDEX.get(board)
    return (code, None) if code else (None, REL_UNDET_NO_BOARD_META)


def gap_pct_of(price: Optional[float], pre_close: Optional[float]) -> Optional[float]:
    """竞价涨跌幅 = `竞价价 / 昨收 − 1`。**公式的唯一源在这里。**

    昨收缺失或 ≤0 → `None`(⛔ 不拿 0 冒充"平开" —— 那会让"算不出"看起来像"没涨没跌")。

    ⚠ **与 `sentinel/capture.py::record_auction_snapshot` 里那一处是刻意的小重复**:
    依赖方向不许 `sentinel` 反向 import `auction`,所以那边一字不动;两处一致由守门
    单测在**同一张输入表**上逐位对拍(含 `None` / `0` / 负 `pre_close` 分支),
    这是登记过的取舍,**不是遗漏**。
    """
    try:
        p = float(price) if price is not None else None
        pc = float(pre_close) if pre_close is not None else None
    except (TypeError, ValueError):
        return None
    if not p or pc is None or pc <= 0:
        return None
    return p / pc - 1.0


@dataclass(frozen=True)
class AuctionSnapshot:
    """9:26—9:29 冻结的那一份竞价结果。**冻结件:构造完就不再变**。

    ⚠ 它只装「抓到了什么」,**不装任何判定** —— 一个 `verdict` / `hit_invalidation`
    都不在这里(那是 `mech.py` 的活)。
    """

    trade_date: date                       # D1 = 竞价发生这天
    d0_date: date                          # 被验证的 D0 = prev_trading_day(D1)
    #: 🔴 **真正拉完价的那一刻**(不是轮询那一拍的时刻)。V2.3.3 复审 🟡-2:原先存的是
    #: `_sentinel_loop` 循环顶部取的 `now` —— 而 precall + capture + 本层的批量拉价
    #: (含主源失败降备源的重试)可能吃掉几分钟。慢的早晨会写下一份**用开盘后价格**、
    #: 却自称 9:26 冻结的报告,`captured_in_window` 还是 `True` → 闸 1 夹不住。
    captured_at: datetime                  # 冻结时刻(北京时间,CN_TZ 唯一源)
    #: 拉价**开始**的时刻。与 `captured_at` 一起,拉价耗时是可查的(⛔ 别只留一个点)。
    fetch_started_at: Optional[datetime] = None
    #: 非空 = **拉价前复判窗口已关**,本次一条价都没拉、⛔ 零落库(〇b-4 同一条纪律)。
    fetch_skipped_reason: str = ""
    requested: Tuple[str, ...] = ()        # 抓取清单(去重后,确定性顺序)
    quotes: Mapping[str, Quote] = field(default_factory=dict)
    missing: Tuple[str, ...] = ()          # 清单里但拉不到的
    conflicts: Tuple[str, ...] = ()        # 跨源冲突;⚠ 结构性恒空,见模块 DDL 注释与 §七 P4-66
    baskets: Tuple[BasketRef, ...] = ()    # D0 的 T1/T2 篮子(含成员与引擎归属四键)
    index_codes: Tuple[str, ...] = ()      # 三支市场指数 + 各板块基准指数(去重)
    benchmark_of: Mapping[str, str] = field(default_factory=dict)   # 个股 → 其板块基准指数
    #: 🔴 个股 → **市场指数**(`rel_to_index` 的独立路径,裁定 P3-70)。**科创板不在这里**
    #: (K8 §三 排除)—— 缺席的原因去 `market_index_undetermined` 查,⛔ 别当成"没算"。
    market_index_of: Mapping[str, str] = field(default_factory=dict)
    #: 个股 → 「拿不到市场指数」的原因码(`board_excluded` / `no_board_meta`)。
    market_index_undetermined: Mapping[str, str] = field(default_factory=dict)
    #: 🔴 个股 → 行业(`stock_basic.industry`,本仓钉死的行业口径)= `rel_to_sector`
    #: **板块对照股**的取数域(裁定 P3-70 ②)。⚠ 只含**关注池里的个股**,指数不在里面
    #: —— 这就是「⛔ 禁止用市场指数代替板块基准」的**结构性保证**。
    industry_of: Mapping[str, str] = field(default_factory=dict)
    meta: Mapping[str, StockMeta] = field(default_factory=dict)
    prev5_avg_volume: Mapping[str, float] = field(default_factory=dict)
    #: 关注池里 `gap_pct > 0` 且不属于任何 T1/T2 篮的标的(市场锚点,**代理样本、不截断**)。
    strong_anchor_codes: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()

    @property
    def source(self) -> str:
        """逐票 `Quote.source` 汇总:`sina` | `tencent` | `mixed` | `unknown`。

        ⚠ **一条都没抓到 → `unknown`**,⛔ 不拿主源名冒充(「没抓到」不是「用的新浪」)。
        """
        srcs = {str(getattr(q, "source", "") or "unknown") for q in self.quotes.values()}
        if not srcs:
            return "unknown"
        if len(srcs) == 1:
            return next(iter(srcs))
        return "mixed"

    @property
    def captured_in_window(self) -> bool:
        """**真正拉完价的那一刻**落在 `[09:26, 09:29)` 内。越窗 = 这份快照不是"那一刻"
        的 → `degraded` → 闸 1「数据缺失只能形成中性」把结论夹成中性。

        🔴 这就是 🟡-2 的第二层:拉价**前**复判窗口(零落库)拦住"整段都在窗外"的情形,
        这个属性拦住"拉价跨过了 9:29"的情形 —— 两层缺一不可。"""
        return AUCTION_WINDOW_START <= self.captured_at.time() < AUCTION_WINDOW_END

    @property
    def fetch_elapsed_sec(self) -> Optional[float]:
        """拉价耗时(秒)。`fetch_started_at` 缺 → `None`(⛔ 不拿 0 冒充"瞬间完成")。"""
        if self.fetch_started_at is None:
            return None
        return (self.captured_at - self.fetch_started_at).total_seconds()

    def gap_of(self, code: str) -> Optional[float]:
        q = self.quotes.get(code)
        if q is None:
            return None
        return gap_pct_of(getattr(q, "price", None), getattr(q, "pre_close", None))

    def quality_of(self, codes: "Tuple[str, ...] | List[str]") -> str:
        """某个样本域的数据质量三态(**结构性判据,⛔ 不是百分比阈值**)。

            insufficient = 样本域里一条都没抓到(fetched == 0)
            ok           = fetched == requested 且 跨源冲突为空 且 captured_at 在窗内
            degraded     = 其余(有缺失 / 有冲突 / 抓取时刻越窗)

        ⚠ 样本域**为空**(例如 D0 一个篮子都没有)也判 `insufficient` —— 「没有可判的
        东西」与「判过了都好」必须分得开(§七 P0-39 同款纪律)。
        """
        want = [c for c in dict.fromkeys(codes)]
        if not want:
            return DQ_INSUFFICIENT
        got = [c for c in want if c in self.quotes]
        if not got:
            return DQ_INSUFFICIENT
        conflicted = set(self.conflicts) & set(want)
        if len(got) == len(want) and not conflicted and self.captured_in_window:
            return DQ_OK
        return DQ_DEGRADED


def build_watchlist(
    wu: WatchUniverse, *, meta: Optional[Mapping[str, StockMeta]] = None,
) -> Tuple[List[str], Dict[str, str], List[str], Dict[str, str], Dict[str, str]]:
    """抓取清单(去重、**确定性顺序**)+ 个股→板块基准指数 + 指数码清单
    + 🔴 个股→**市场指数**(独立路径,裁定 P3-70)+ 拿不到市场指数的原因码。

    顺序 = 「篮子成员(按篮子顺序)→ 三支市场指数 → 各板块基准指数 → 关注池其余」。
    ⚠ 关注池其余那一段是第 5 组「竞价强势股」的**取样域**:它们本来就已经在
    `wu.codes` 里(持仓 / 昨日涨停 / 主线切片),并入清单不额外多打一次请求。
    ⚠ **市场指数映射不新增任何抓取码**:它落在的那几支(上证 / 深证 / 创业板 / 北证50)
    与板块基准指数是同一批码,清单逐位不变 —— 分开的是**语义与算法**,不是请求量。
    """
    codes: List[str] = []
    seen: set = set()

    def _add(code: str) -> None:
        if code and code not in seen:
            seen.add(code)
            codes.append(code)

    for b in wu.baskets:
        for c in b.member_codes:
            _add(c)
    index_codes: List[str] = []
    for c in MARKET_INDEX_CODES:
        if c not in index_codes:
            index_codes.append(c)
    # 候选所属板块的基准指数(同 `universe.py` 唯一源;个股 → 指数的映射一并留下,
    # `mech.rel_to_sector` 直接吃它,⛔ 不在 mech 里再判一次板块)。
    benchmark_of: Dict[str, str] = {}
    mkt_index_of: Dict[str, str] = {}
    mkt_index_undetermined: Dict[str, str] = {}
    m = meta or {}
    for code, sm in m.items():
        if sm.board == Board.MAIN:
            bench = MAIN_BOARD_INDEX_SH if code.upper().endswith(".SH") else MAIN_BOARD_INDEX_SZ
        else:
            bench = BOARD_BENCHMARK_INDEX.get(sm.board, "")
        if bench:
            benchmark_of[code] = bench
            if bench not in index_codes:
                index_codes.append(bench)
        # 🔴 **市场指数走自己那条路**(裁定 P3-70):科创板在这里是 `None + board_excluded`,
        # 而 `benchmark_of` 那边照旧给 000688.SH —— 两条映射**刻意不同**,⛔ 别"统一"。
        mi_code, mi_reason = market_index_of(sm.board, code)
        if mi_code:
            mkt_index_of[code] = mi_code
        elif mi_reason:
            mkt_index_undetermined[code] = mi_reason
    for c in index_codes:
        _add(c)
    for c in wu.codes:
        _add(c)
    return codes, benchmark_of, index_codes, mkt_index_of, mkt_index_undetermined


def collect_auction_snapshot(
    trade_date: date,
    now: datetime,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    breadth_cap: int = DEFAULT_BREADTH_CAP,
    quotes_fn: Optional[Callable[[List[str]], Dict[str, Quote]]] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
) -> AuctionSnapshot:
    """拉一次价并冻结。`quotes_fn` 可覆盖(默认 `sentinel.quotes.get_quotes`)——
    合成竞价快照冒烟(`scripts/smoke_auction.py`)据此注入,不改一行编排,同
    `precall.run_precall_tick` 的既有体例。

    🔴 **`now_fn` 是"真实时钟",`now` 是"那一拍的名义时刻"**(V2.3.3 复审 🟡-2):
    `captured_at` 一律取 **`fetch()` 返回之后**的 `now_fn()`,`fetch_started_at` 取
    调用之前的那一次 —— 因为 `_sentinel_loop` 循环顶部的 `now` 与真正拉到价之间隔着
    precall、capture 与本层的批量请求(含降备源重试),慢的早晨能差好几分钟。
    ⚠ 拉价**前**还会用 `now_fn()` **复判一次窗口**:已越窗就一条价都不拉、
    `fetch_skipped_reason` 非空,编排层据此**零落库**(〇b-4「事后不许补跑」同一条纪律)。
    ⚠ `now_fn` 缺省 = `datetime.now`(真实时钟);回放 / 单测显式注入
    (同 `precall.run_precall_tick(now=…)` 的既有体例)。

    **拉价失败不掀翻本层**:异常只 WARNING + 落一条 note,快照照常冻结(`quotes` 为空
    → 数据质量 `insufficient`)—— 「一条都没抓到」是要被如实报出来的事实,不是崩溃。
    """
    clock: Callable[[], datetime] = now_fn or datetime.now
    notes: List[str] = []
    wu: WatchUniverse = load_watch_universe(
        trade_date, breadth_cap=breadth_cap, db_path=db_path, parquet_dir=parquet_dir
    )
    basket_codes = [c for b in wu.baskets for c in b.member_codes]
    # 板块判定唯一源 `load_stock_meta`(→ `data/board.classify`),⛔ 不自己写前缀正则。
    try:
        meta = load_stock_meta(list(dict.fromkeys(basket_codes + list(wu.codes))),
                              db_path=db_path) if wu.codes or basket_codes else {}
    except Exception:  # noqa: BLE001 —— 可选情报的保险丝(§铁律)
        logger.warning("[auction] 查股票元数据失败,本次无板块基准指数对照", exc_info=True)
        meta = {}
        notes.append("stock_meta_unavailable")

    requested, benchmark_of, index_codes, mkt_index_of, mkt_index_undet = build_watchlist(
        wu, meta=meta)

    # 🔴 **板块对照股的取数域**(裁定 P3-70 ②):`stock_basic.industry` —— 本仓钉死的
    # 行业口径(唯一读取实现 `report/industry_strength.py::load_industry_map`,
    # 与 `stock_persist_days` 同一口径),⛔ 不在本包另造一套板块分类。
    # ⚠ 只留**抓取清单里的个股**:指数不在 `stock_basic` 里,天然进不来 ——
    #    这正是「⛔ 禁止用市场指数代替板块基准」的结构性保证。
    try:
        from neckline.report.industry_strength import load_industry_map

        _all_industry = load_industry_map(db_path)
        wanted = set(requested)
        industry_of = {c: ind for c, ind in _all_industry.items() if c in wanted}
    except Exception:  # noqa: BLE001 —— 可选情报的保险丝(§铁律)
        logger.warning("[auction] 查行业口径失败,本次无板块对照股基准", exc_info=True)
        industry_of = {}
        # 🔴 这条 note 是**逐票原因码分岔的判据**(复审 🔵-7):整张表读不到 = 系统缺席,
        # 逐票落 `industry_map_unavailable`,⛔ 不与「这一只票真没登记行业」(`no_industry`)
        # 讲成同一句话。字面量单一源在 `auction/__init__.py`。
        notes.append(NOTE_INDUSTRY_MAP_UNAVAILABLE)

    # 🔴 **拉价前复判窗口**(复审 🟡-2 第一层):组清单本身要读关注池 / 元数据,
    # 加上同一拍里排在前面的 precall 与 capture,到这里可能已经 9:30 了。
    # 越窗就**一条价都不拉、零落库** —— 拉了就是拿开盘后的价格冒充 9:26 那一刻(〇b-4)。
    fetch_started_at = clock()
    if not (AUCTION_WINDOW_START <= fetch_started_at.time() < AUCTION_WINDOW_END):
        logger.warning("[auction] 拉价前复判:窗口已关(%s),本次一条价都不拉、零落库",
                       fetch_started_at.isoformat(timespec="seconds"))
        return AuctionSnapshot(
            trade_date=trade_date, d0_date=prev_trading_day(trade_date),
            captured_at=fetch_started_at, fetch_started_at=fetch_started_at,
            fetch_skipped_reason=SKIP_WINDOW_CLOSED,
            requested=tuple(requested), quotes={}, missing=tuple(requested), conflicts=(),
            baskets=tuple(wu.baskets), index_codes=tuple(index_codes),
            benchmark_of=benchmark_of,
            market_index_of=mkt_index_of, market_index_undetermined=mkt_index_undet,
            industry_of=industry_of,
            meta=meta, prev5_avg_volume={},
            strong_anchor_codes=(),
            notes=tuple(notes + [SKIP_WINDOW_CLOSED]),
        )

    fetch = quotes_fn or (lambda codes: get_quotes(codes))
    quotes: Dict[str, Quote] = {}
    if requested:
        try:
            quotes = dict(fetch(requested) or {})
        except Exception:  # noqa: BLE001
            logger.warning("[auction] 竞价批量拉价失败,本次快照为空(如实标 insufficient)",
                           exc_info=True)
            notes.append("quotes_fetch_failed")
    # 🔴 **冻结时刻 = 真正拉完价的这一刻**(⛔ 不是轮询那一拍的 `now`)。
    captured_at = clock()
    if not (AUCTION_WINDOW_START <= captured_at.time() < AUCTION_WINDOW_END):
        # 拉价**跨过了**窗口右端:报告照常落库(机械事实与失效警报不能丢),但
        # `captured_in_window` 为假 → `data_quality` 降级 → 闸 1 把结论夹成中性。
        logger.warning("[auction] 拉价跨过了 9:29(开始 %s / 完成 %s),本次数据质量降级",
                       fetch_started_at.isoformat(timespec="seconds"),
                       captured_at.isoformat(timespec="seconds"))
        notes.append("captured_out_of_window")

    try:
        prev5 = load_prev5_avg_volume(list(requested), trade_date,
                                      parquet_dir=parquet_dir) if requested else {}
    except Exception:  # noqa: BLE001
        logger.warning("[auction] 前 5 日均量读取失败,竞价量能附注本次缺席", exc_info=True)
        prev5 = {}
        notes.append("prev5_volume_unavailable")

    missing = tuple(c for c in requested if c not in quotes)
    basket_member_set = set(basket_codes)
    index_set = set(index_codes)
    # 第 5 组:竞价强势股(代理样本)= 抓到了、gap>0、不属于任何 T1/T2 篮、不是指数。
    # ⚠ **不截断**(§五 ⑨-A 第 5 行);排序按 gap 降序 + 代码升序做**确定性 tie-break**
    # (CLAUDE.md:进判据 / 排序的名次必须先排定确定性 tie-break)。
    anchors: List[Tuple[float, str]] = []
    for code in requested:
        if code in basket_member_set or code in index_set:
            continue
        g = gap_pct_of(getattr(quotes.get(code), "price", None),
                       getattr(quotes.get(code), "pre_close", None))
        if g is not None and g > 0:
            anchors.append((g, code))
    anchors.sort(key=lambda t: (-t[0], t[1]))

    return AuctionSnapshot(
        trade_date=trade_date,
        d0_date=prev_trading_day(trade_date),
        captured_at=captured_at,
        fetch_started_at=fetch_started_at,
        requested=tuple(requested),
        quotes=quotes,
        missing=missing,
        # ⚠ 跨源冲突**结构性不可达**:`sentinel/quotes.py` 是「主源失败才降备源」、
        # **不同时拉两源** → 没有第二个读数可以跟第一个打架。字段留着是因为 K8 §二十
        # 要求报"冲突状态";⛔ 不为它加第二次网络请求(§七 P4-66)。
        conflicts=(),
        baskets=tuple(wu.baskets),
        index_codes=tuple(index_codes),
        benchmark_of=benchmark_of,
        market_index_of=mkt_index_of,
        market_index_undetermined=mkt_index_undet,
        industry_of=industry_of,
        meta=meta,
        prev5_avg_volume=prev5,
        strong_anchor_codes=tuple(code for _g, code in anchors),
        notes=tuple(notes),
    )


__all__ = [
    "MARKET_INDEX_CODES",
    "AUCTION_WINDOW_START",
    "AUCTION_WINDOW_END",
    "SKIP_WINDOW_CLOSED",
    "market_index_of",
    "gap_pct_of",
    "AuctionSnapshot",
    "build_watchlist",
    "collect_auction_snapshot",
]
