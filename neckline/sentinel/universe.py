"""关注池组装(plan 阶段3 §2.4 工程要求「批量拉取」的前置步骤)。哨兵每拍只对一个
有限、可解释的「关注池」批量拉价,不对全市场 ~5900 只票逐分钟轮询——理由见
`retreat.py` 模块头注释(免费源持续高频全市场轮询的限流/稳定性代价 vs 关注池
已覆盖真正需要盯的票)。

关注池 = 昨晚报告的候选(全部 20 只,不只 LLM 审判过的前10只——买点/证伪哨兵
覆盖的是"候选池"整体,§2.4 原文未把两个哨兵限定到"前10只"这个 LLM 子集)
∪ 当前持仓 ∪ **自选池(v1.1-C 并入,`neckline.watchlist`)** ∪ 昨日涨停股
(退潮哨兵的市场宽度代理样本,见 `retreat.py`)。

**v1.1-C.2 自选并入 + ≤200 上限**:自选池「优先级与候选/持仓同级」——去重后
`持仓 ∪ 自选 ∪ 候选` 全部保留(正常 5+30+20=55 远低于 200,不会触发下面的兜底
裁剪),`_load_prev_limit_up_codes` 只填**剩余额度**到 `breadth_cap`(默认
200)——自选并入挤占的是"昨日涨停代理样本"的尾部,不放大总拉价量。若这三类
去重后本身已经 ≥ `breadth_cap`(现实中不会发生,55 << 200),按**持仓 > 自选 >
候选**的优先序截断(此时代理样本挤占额度为 0)——这个顺序是产品拍板的裁剪
优先级,不是随手定的。

自选票的「候选同级待遇」不仅限于拉价:昨晚自选体检(`report.watchlist_check`)
若判定某只自选票**已触发母战法买点**(与"这只票今天算不算候选"用的是同一把
尺,见该模块 `build_entry_mask` 复用),其 `entry_spec`/`invalidation_spec` 会
随报告落库(`reports.watchlist_json`);本模块据此把这些「已触发」的自选票也
转成 `Candidate` 形状并入 `watchlist_candidates`,供买点 / 证伪哨兵
(`engine.py`)与盘前校准(`precall.py`)同码消费——它们只读昨晚**已经算好、
写死**的 entry_spec/invalidation_spec,不在盘中重新计算任何判定,不违反
§2.4「盘中不产生任何新决策」的铁律。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl

from neckline import watchlist as watchlist_store
from neckline.calendar import prev_trading_day, trading_days_between
from neckline.data.board import Board, classify
from neckline.data.limit_derived import resolve_exempt_days
from neckline.data.market_data import get_market_slice, load_stock_basic, scan_table_range
from neckline.report import store
from neckline.report.candidates import Candidate
from neckline.sentinel.positions import Position, load_open_positions

# 退潮哨兵市场宽度代理样本的容量上限(见模块头注释;避免极端全员涨停日把批量
# 请求撑到不合理大小——保守分块防线,不是精细调过的数字)。v1.1-C 起同时也是
# 「持仓∪自选∪候选∪昨日涨停代理」合并后的关注池总上限(见模块头注释)。
DEFAULT_BREADTH_CAP = 200
# 计算前5日均量时的自然日回溯窗口(足够覆盖5个交易日,含长假缓冲)。
_VOLUME_LOOKBACK_DAYS = 15


@dataclass
class WatchUniverse:
    trade_date: date            # 哨兵运行的这一天(今天)
    report_date: date           # prev_trading_day(trade_date)——candidates 理应来自这天的报告
    report_found: bool          # 该日报告是否真的生成过(找不到→candidates 为空,不是报告本身为空)
    candidates: List[Candidate]
    positions: List[Position]
    breadth_extra_codes: List[str]   # 候选+持仓+自选之外,为退潮哨兵补充的昨日涨停股代码
    watchlist_codes: List[str] = field(default_factory=list)        # 自选池全部代码(≤30,不论是否触发买点)
    # 自选池里「昨晚体检已判定触发母战法买点」的票,转成 Candidate 形状供买点/
    # 证伪哨兵与盘前校准同码消费(entry_spec/invalidation_spec 昨晚已写死)——
    # 已在 `candidates` 里的代码不重复出现在这里(见 `load_watch_universe`)。
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
    注入隔离环境。无报告 / 无持仓 / 无自选都不报错——空关注池是合法状态(如刚
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
    watchlist_codes = watchlist_store.list_watchlist_codes(db_path=db_path)

    # —— v1.1-C.2:持仓 ∪ 自选 ∪ 候选 全部保留(同级优先级),兜底按
    #    「持仓>自选>候选」截断(见模块头注释,正常 5+30+20=55 不会触发)——————
    priority_codes: List[str] = []
    seen = set()
    for c in [p.ts_code for p in positions] + watchlist_codes + [c.ts_code for c in candidates]:
        if c not in seen:
            seen.add(c)
            priority_codes.append(c)
    if len(priority_codes) > breadth_cap:
        priority_codes = priority_codes[:breadth_cap]
        seen = set(priority_codes)

    remaining = breadth_cap - len(priority_codes)
    breadth_extra = _load_prev_limit_up_codes(report_date, remaining, parquet_dir=parquet_dir) if remaining > 0 else []

    codes: List[str] = list(priority_codes)
    for c in breadth_extra:
        if c not in seen:
            seen.add(c)
            codes.append(c)

    watchlist_candidates = _build_watchlist_candidates(report, watchlist_codes, candidates)

    return WatchUniverse(
        trade_date=trade_date,
        report_date=report_date,
        report_found=report is not None,
        candidates=candidates,
        positions=positions,
        breadth_extra_codes=breadth_extra,
        watchlist_codes=watchlist_codes,
        watchlist_candidates=watchlist_candidates,
        codes=codes,
    )


def _dict_to_candidate(d: Dict) -> Candidate:
    """`store.load_report` 的 `candidates` 是 `Candidate.public_dict()` 的 JSON
    往返(不含 `raw`),`Candidate.raw`/`entry_spec`/`invalidation_spec` 均有缺省值
    或本就在 dict 里,`Candidate(**d)` 可直接重建。"""
    return Candidate(**d)


def _build_watchlist_candidates(
    report: Optional[Dict[str, Any]], watchlist_codes: List[str], candidates: List[Candidate],
) -> List[Candidate]:
    """自选池里「昨晚体检已触发买点」的票 → `Candidate` 形状(§v1.1-C.2「自选票
    享候选同级待遇」)。三重过滤:①报告存在且体检节非空;②该票**现在仍在**
    自选池里(用户可能已删除,盘中立刻停止把它当候选处理,不必等明天报告才
    生效);③不与 `candidates`(报告本身的候选)重复——已经是候选的票走
    `candidates` 那条路径就够,不重复构造。"""
    if report is None:
        return []
    watchlist_set = set(watchlist_codes)
    existing = {c.ts_code for c in candidates}
    out: List[Candidate] = []
    for wd in report.get("watchlist") or []:
        if not isinstance(wd, dict):
            continue
        code = wd.get("ts_code")
        if not code or code not in watchlist_set or code in existing:
            continue
        if not wd.get("buy_point_triggered"):
            continue
        out.append(_watchlist_dict_to_candidate(wd))
    return out


def _watchlist_dict_to_candidate(d: Dict[str, Any]) -> Candidate:
    """把 v1.1-C 自选体检快照(`WatchlistCheckItem.public_dict()` 的 JSON 往返)
    里"已触发买点"的一条转成 `Candidate`,供哨兵复用同一套 `entry_spec`/
    `invalidation_spec` 判定。`rank=0`/`raw={}` 是哨兵不消费的字段,留缺省值——
    `entry.py`/`invalidation.py`/`precall.py` 只读 `.ts_code`/`.name`/
    `.entry_spec`/`.invalidation_spec` 这几个字段,duck typing 已经够用。"""
    return Candidate(
        ts_code=d["ts_code"], name=d.get("name") or d["ts_code"],
        close=d.get("close") or 0.0, score=d.get("score") or 0.0, rank=0,
        board=d.get("board", "MAIN"),
        pattern_tags=d.get("pattern_tags") or [],
        hot_sectors=d.get("hot_sectors") or [], sector_names=d.get("sector_names") or [],
        entry_plan=d.get("entry_plan", ""), stop_loss=d.get("stop_loss", ""),
        target=d.get("target", ""), invalidation_text=d.get("invalidation_text", ""),
        invalidation_spec=d.get("invalidation_spec") or {}, entry_spec=d.get("entry_spec") or {},
    )


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
]
