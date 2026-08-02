"""关注池组装(plan 阶段3 §2.4 的前置步骤;**V2-⑧-A 改组**)。哨兵每拍只对一个
有限、可解释的「关注池」批量拉价,不对全市场 ~5900 只票逐分钟轮询——理由见
`retreat.py` 模块头注释(免费源持续高频全市场轮询的限流/稳定性代价 vs 关注池
已覆盖真正需要盯的票)。

**V2-⑧-A 新组成(plan §五 V2-⑧-A)**::

    持仓 ∪ **T1/T2 篮子成员**(D0 冻结的前两档,`baskets`/`basket_members`)
         ∪ 候选(V1 残留,⑬-1 删)∪ **相关板块指数** ∪ 昨日涨停股(宽度代理样本)

三处**刻意**的取舍,别当成疏漏:

    · **自选池不再是来源**(⑧-A 原文,⑬-11 复述「⑧-A 已改」):`neckline.watchlist`
      本模块**一行都不读**,`watchlist_codes`/`watchlist_candidates` 恒为空——字段
      保留只为让 `engine.py`/`precall.py` 的 `wu.candidates + wu.watchlist_candidates`
      一行不动(⑧-D:纪律外壳不改)。表与模块本体的删除归 ⑬-11(先建后拆)。
    · **候选仍在**:⑬-1 还没执行,买点 / 证伪哨兵此刻仍以 `Candidate` 为对象;⑧ 只
      「加篮子、去自选」,**不顺手拆 V1**(先建后拆纪律)。
    · **「相关板块 ETF/指数」本版落地为板块基准指数**(见 `BOARD_BENCHMARK_INDEX`):
      本项目**没有** ETF 成分/映射数据源(TuShare 600 元档未含,`ths_index` 是同花顺
      板块指数、免费实时源不认它的代码),硬编一份 ETF 清单等于凭空发明。指数是
      **可得且可核对**的那一半,已如实登记(⑧ 完工记录)。

**容量与优先序(≤ `breadth_cap`,默认 200,与改组前同量级)**:
`持仓 > T1/T2 篮子成员 > 候选 > 板块指数`,`_load_prev_limit_up_codes` 只填**剩余
额度**。指数排在候选之后是因为它**没有任何纪律消费方**(只进存拍与语境),真要挤,
先挤它、不挤哨兵要判的票。

⚠ **指数 / ETF 代码不会污染退潮哨兵的宽度样本**:`retreat.compute_breadth_snapshot`
按 `stock_basic` 元数据逐票判涨跌停,查无元数据的代码**结构上就被跳过**(它本来就
不可能"涨停/跌停")——这不是巧合,是那个函数早就写死的口径,新增指数代码因此
零影响于退潮判定(单测锁死)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import polars as pl

from neckline.calendar import prev_trading_day, trading_days_between
from neckline.data.board import Board, classify
from neckline.data.limit_derived import resolve_exempt_days
from neckline.data.market_data import get_market_slice, load_stock_basic, scan_table_range
from neckline.report import store
from neckline.report.candidates import Candidate
from neckline.selection.basket_store import BasketRef, load_baskets_for_date
from neckline.sentinel.positions import Position, load_open_positions

logger = logging.getLogger(__name__)

# 退潮哨兵市场宽度代理样本的容量上限(见模块头注释;避免极端全员涨停日把批量
# 请求撑到不合理大小——保守分块防线,不是精细调过的数字)。v1.1-C 起同时也是
# 关注池总上限;V2-⑧-A 改组后口径不变(仍 200,「与现状同量级」)。
DEFAULT_BREADTH_CAP = 200
# 计算前5日均量时的自然日回溯窗口(足够覆盖5个交易日,含长假缓冲)。
_VOLUME_LOOKBACK_DAYS = 15

# 盘中会被盯的篮子档位(T1/T2)。T3 不进盘中池是**容量取舍**,不是"T3 不重要"——
# 它在 EOD 那一拍照样被判(`basket_verify.run_eod_verification` 判全部档位)。
INTRADAY_BASKET_TIERS: Tuple[int, ...] = (1, 2)

# 「相关板块 ETF/指数」的可得落地 = **板块基准指数**(见模块头注释:没有 ETF 映射
# 数据源,不硬编清单)。代码取自 `scripts/backfill.py::INDEX_CODES` 同一批(本项目
# 追踪的指数),主板按交易所分沪深两支。
BOARD_BENCHMARK_INDEX: Dict[Board, str] = {
    Board.GEM: "399006.SZ",     # 创业板指
    Board.STAR: "000688.SH",    # 科创50
    Board.BSE: "899050.BJ",     # 北证50
}
MAIN_BOARD_INDEX_SH = "000001.SH"   # 上证综指
MAIN_BOARD_INDEX_SZ = "399001.SZ"   # 深证成指


@dataclass
class WatchUniverse:
    trade_date: date            # 哨兵运行的这一天(今天)
    report_date: date           # prev_trading_day(trade_date)——candidates / 篮子理应来自这天
    report_found: bool          # 该日报告是否真的生成过(找不到→candidates 为空,不是报告本身为空)
    candidates: List[Candidate]
    positions: List[Position]
    breadth_extra_codes: List[str]   # 上面几类之外,为退潮哨兵补充的昨日涨停股代码
    # —— V2-⑧-A 新增两类来源 ————————————————————————————————————————————
    baskets: List[BasketRef] = field(default_factory=list)      # D0 的 T1/T2 篮子(含成员)
    basket_codes: List[str] = field(default_factory=list)       # 上面那些篮子的成员代码(去重)
    index_codes: List[str] = field(default_factory=list)        # 相关板块基准指数(只进存拍/语境)
    # —— 自选池:V2-⑧-A 起**不再是关注池来源**,两字段恒为空 ————————————————
    # 保留字段而不是删掉,是为了让 `engine.py`/`precall.py` 里
    # `wu.candidates + wu.watchlist_candidates` 那一行**一个字都不用改**(⑧-D:纪律
    # 外壳不动)。`neckline/watchlist.py` 与 `watchlist` 表的删除归 ⑬-11(先建后拆)。
    watchlist_codes: List[str] = field(default_factory=list)
    watchlist_candidates: List[Candidate] = field(default_factory=list)
    codes: List[str] = field(default_factory=list)  # 去重后全部关注代码(拉价用)


def load_watch_universe(
    trade_date: date,
    *,
    breadth_cap: int = DEFAULT_BREADTH_CAP,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> WatchUniverse:
    """组装 `trade_date` 这一天的关注池。`db_path`/`parquet_dir` 均可覆盖,供单测
    注入隔离环境。无报告 / 无持仓 / 无篮子都不报错——空关注池是合法状态(如刚
    部署当天尚未跑过 `scripts/report.py`),调用方(engine.py/precall.py)据此
    优雅跳过对应哨兵。
    """
    report_date = prev_trading_day(trade_date)
    report = store.load_report(report_date, db_path=db_path)
    candidates: List[Candidate] = []
    if report is not None:
        for d in report["candidates"]:
            candidates.append(_dict_to_candidate(d))

    positions = load_open_positions(db_path=db_path)

    # —— V2-⑧-A:D0(= report_date)冻结的 T1/T2 篮子成员 ——————————————————
    baskets = _load_intraday_baskets(report_date, db_path=db_path)
    basket_codes: List[str] = []
    for b in baskets:
        for code in b.member_codes:
            if code not in basket_codes:
                basket_codes.append(code)

    # —— 优先序:持仓 > T1/T2 成员 > 候选 > 板块指数(理由见模块头)——————————
    index_codes = _related_index_codes(
        list(dict.fromkeys(basket_codes + [p.ts_code for p in positions])), db_path=db_path
    )
    priority_codes: List[str] = []
    seen = set()
    for c in ([p.ts_code for p in positions] + basket_codes
              + [c.ts_code for c in candidates] + index_codes):
        if c not in seen:
            seen.add(c)
            priority_codes.append(c)
    if len(priority_codes) > breadth_cap:
        priority_codes = priority_codes[:breadth_cap]
        seen = set(priority_codes)
        index_codes = [c for c in index_codes if c in seen]

    remaining = breadth_cap - len(priority_codes)
    breadth_extra = _load_prev_limit_up_codes(report_date, remaining, parquet_dir=parquet_dir) if remaining > 0 else []

    codes: List[str] = list(priority_codes)
    for c in breadth_extra:
        if c not in seen:
            seen.add(c)
            codes.append(c)

    return WatchUniverse(
        trade_date=trade_date,
        report_date=report_date,
        report_found=report is not None,
        candidates=candidates,
        positions=positions,
        breadth_extra_codes=breadth_extra,
        baskets=baskets,
        basket_codes=basket_codes,
        index_codes=index_codes,
        codes=codes,
    )


def _load_intraday_baskets(report_date: date, *, db_path: Optional[Path]) -> List[BasketRef]:
    """D0 的 T1/T2 篮子。读库失败 / 无篮子 → 空列表(**合法状态**:V2 引擎还没跑过、
    或今日无篮子达到定档标准);⛔ 绝不因此掀翻整个关注池 —— 哨兵拿不到篮子也还要
    盯持仓。"""
    try:
        return load_baskets_for_date(report_date, tiers=INTRADAY_BASKET_TIERS, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[universe] 读 %s 的 T1/T2 篮子失败,本拍关注池不含篮子成员",
                       report_date, exc_info=True)
        return []


def _related_index_codes(codes: List[str], *, db_path: Optional[Path]) -> List[str]:
    """这批票**所在板块**的基准指数(去重、排序,**确定性**)。板块判定唯一源
    `load_stock_meta`(→ `data/board.classify`),⛔ 不自己写前缀正则。查无元数据的
    代码跳过(既判不了板块,也就给不出"相关指数",不猜)。"""
    if not codes:
        return []
    try:
        meta = load_stock_meta(codes, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[universe] 查板块元数据失败,本拍关注池不含板块指数", exc_info=True)
        return []
    out: set = set()
    for code, m in meta.items():
        if m.board == Board.MAIN:
            out.add(MAIN_BOARD_INDEX_SH if code.upper().endswith(".SH") else MAIN_BOARD_INDEX_SZ)
        elif m.board in BOARD_BENCHMARK_INDEX:
            out.add(BOARD_BENCHMARK_INDEX[m.board])
    return sorted(out)


def _dict_to_candidate(d: Dict) -> Candidate:
    """`store.load_report` 的 `candidates` 是 `Candidate.public_dict()` 的 JSON
    往返(不含 `raw`),`Candidate.raw`/`entry_spec`/`invalidation_spec` 均有缺省值
    或本就在 dict 里,`Candidate(**d)` 可直接重建。"""
    return Candidate(**d)


def _load_prev_limit_up_codes(report_date: date, cap: int, parquet_dir: Optional[Path]) -> List[str]:
    prev_limit = get_market_slice(report_date, table="limit_derived", parquet_dir=parquet_dir)
    if prev_limit.is_empty():
        return []
    up = prev_limit.filter(pl.col("is_limit_up")).sort("consec_limit_up_days", descending=True)
    return up["ts_code"].to_list()[:cap]


def load_prev5_avg_volume(
    codes: List[str], as_of: date, parquet_dir: Optional[Path] = None
) -> Dict[str, float]:
    """`codes` 各自的前5个交易日平均成交量(手,EOD `daily.vol` 口径),供
    `intraday.intraday_vol_ratio` 的基准输入。`as_of` 通常是哨兵运行的今天——
    基准取"今天之前"的5个交易日,不含今天(今天尚未收盘,数据本就还没有)。

    一次批量 scan(不逐票查询),codes 数量在数百量级时开销可忽略。数据不足5天
    (如新股)→ 用实际可用天数的均值;完全无历史 → 该票不出现在返回 dict 里
    (调用方按 `.get(code)` 处理缺失,自然走 `intraday_vol_ratio` 的 no_base 分支)。
    """
    if not codes:
        return {}
    end = prev_trading_day(as_of)
    start = end - timedelta(days=_VOLUME_LOOKBACK_DAYS)
    df = scan_table_range("daily", start, end, parquet_dir=parquet_dir)
    if df.is_empty():
        return {}
    df = df.filter(pl.col("ts_code").is_in(codes)).select(["ts_code", "trade_date", "vol"])
    out: Dict[str, float] = {}
    for (code,), sub in df.group_by(["ts_code"]):
        vols = sub.sort("trade_date")["vol"].to_list()[-5:]
        if vols:
            out[code] = sum(vols) / len(vols)
    return out


@dataclass
class StockMeta:
    ts_code: str
    name: str
    board: Board
    is_st: bool
    list_date: Optional[date]


def load_stock_meta(codes: List[str], db_path: Optional[Path] = None) -> Dict[str, StockMeta]:
    """`ts_code -> StockMeta`,供退潮/持仓哨兵算涨跌停价(`limit_derived.
    compute_intraday_limit_prices` 需要 board/is_st/trade_date)与展示名称用。

    `is_st` 直接读 `stock_basic.name`(当前名称)前缀——不做历史 as-of 判定
    (那是回测/报告的关切,盘中哨兵只关心"现在"),但要求 `stock_basic` 是最新的
    (`scripts/daily_update.py` 每交易日盘后刷新 `bootstrap_metadata()`,见该脚本)。
    """
    if not codes:
        return {}
    sb = load_stock_basic(db_path)
    if sb.is_empty():
        return {}
    sb = sb.filter(pl.col("ts_code").is_in(codes))
    out: Dict[str, StockMeta] = {}
    for row in sb.iter_rows(named=True):
        name = row["name"] or row["ts_code"]
        is_st = name.strip().strip("*").upper().startswith("ST")
        out[row["ts_code"]] = StockMeta(
            ts_code=row["ts_code"],
            name=name,
            board=classify(row["market"], row["ts_code"]),
            is_st=is_st,
            list_date=row["list_date"],
        )
    return out


def is_new_stock_exempt(meta: StockMeta, trade_date: date) -> bool:
    """该票在 `trade_date` 是否仍处于新股涨跌幅豁免窗口(见
    `neckline.data.limit_derived.resolve_exempt_days`)。`list_date` 未知 → 保守按
    "已过豁免期"处理(不放过涨跌停判定;宁可误判老股为受限,不放过真正该防的新股)。

    **性能/日志噪音坑(施工中实测踩到,已修)**:A 股大量主板老股 `list_date` 在
    2015 年之前(`neckline.calendar` 的 trade_cal DB 覆盖默认从 2015-01-01 起,
    见 `scripts/init_calendar.py`)——若直接对每只票调用
    `trading_days_between(list_date, trade_date)` 算精确交易日数,老股会命中
    "查询早于DB覆盖范围"分支,退化为**逐自然日** `is_trading_day` 判断 + 每天
    一条 warning 日志,几十年区间循环下来极慢且刷屏。**先用自然日差做廉价预筛**
    ——超过30自然日(远大于任何板块的5交易日豁免窗口上限)必然已过豁免期,直接
    返回 False,不进入昂贵的精确计算;只有"看起来像最近一个月内上市"的票才会
    走精确的 `trading_days_between`(此时区间必然很小,即使意外落在DB覆盖范围
    外,回退成本也可忽略)。
    """
    if meta.list_date is None or meta.list_date > trade_date:
        return False
    if (trade_date - meta.list_date).days > 30:
        return False
    exempt_days = resolve_exempt_days(meta.board, meta.list_date)
    days_since_listing = len(trading_days_between(meta.list_date, trade_date))
    return 0 < days_since_listing <= exempt_days


__all__ = [
    "WatchUniverse",
    "load_watch_universe",
    "load_prev5_avg_volume",
    "StockMeta",
    "load_stock_meta",
    "is_new_stock_exempt",
    "DEFAULT_BREADTH_CAP",
    "INTRADAY_BASKET_TIERS",
    "BOARD_BENCHMARK_INDEX",
    "MAIN_BOARD_INDEX_SH",
    "MAIN_BOARD_INDEX_SZ",
]
