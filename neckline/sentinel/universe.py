"""关注池组装(plan 阶段3 §2.4 工程要求「批量拉取」的前置步骤)。哨兵每拍只对一个
有限、可解释的「关注池」批量拉价,不对全市场 ~5900 只票逐分钟轮询——理由见
`retreat.py` 模块头注释(免费源持续高频全市场轮询的限流/稳定性代价 vs 关注池
已覆盖真正需要盯的票)。

关注池 = 昨晚报告的候选(全部 20 只,不只 LLM 审判过的前10只——买点/证伪哨兵
覆盖的是"候选池"整体,§2.4 原文未把两个哨兵限定到"前10只"这个 LLM 子集)
∪ 当前持仓 ∪ 昨日涨停股(退潮哨兵的市场宽度代理样本,见 `retreat.py`)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import polars as pl

from neckline.calendar import prev_trading_day, trading_days_between
from neckline.data.board import Board, classify
from neckline.data.limit_derived import resolve_exempt_days
from neckline.data.market_data import get_market_slice, load_stock_basic, scan_table_range
from neckline.report import store
from neckline.report.candidates import Candidate
from neckline.sentinel.positions import Position, load_open_positions

# 退潮哨兵市场宽度代理样本的容量上限(见模块头注释;避免极端全员涨停日把批量
# 请求撑到不合理大小——保守分块防线,不是精细调过的数字)。
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
    breadth_extra_codes: List[str]   # 候选+持仓之外,为退潮哨兵补充的昨日涨停股代码
    codes: List[str] = field(default_factory=list)  # 去重后全部关注代码(拉价用)


def load_watch_universe(
    trade_date: date,
    *,
    breadth_cap: int = DEFAULT_BREADTH_CAP,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> WatchUniverse:
    """组装 `trade_date` 这一天的关注池。`db_path`/`parquet_dir` 均可覆盖,供单测
    注入隔离环境。无报告 / 无持仓都不报错——空关注池是合法状态(如刚部署当天
    尚未跑过 `scripts/report.py`),调用方(engine.py)据此优雅跳过对应哨兵。
    """
    report_date = prev_trading_day(trade_date)
    report = store.load_report(report_date, db_path=db_path)
    candidates: List[Candidate] = []
    if report is not None:
        for d in report["candidates"]:
            candidates.append(_dict_to_candidate(d))

    positions = load_open_positions(db_path=db_path)

    breadth_extra = _load_prev_limit_up_codes(report_date, breadth_cap, parquet_dir=parquet_dir)

    codes: List[str] = []
    seen = set()
    for c in [c.ts_code for c in candidates] + [p.ts_code for p in positions] + breadth_extra:
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
        codes=codes,
    )


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
    """
    if meta.list_date is None:
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
