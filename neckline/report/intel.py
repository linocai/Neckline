"""复盘情报件(plan §五 v1.3-③-C1)。16:35 报告新增「情报」节的 C1 部分——全 EOD
可算的当日复盘:涨幅/跌幅榜、涨停梯队(连板高度分布)、跌停榜、大盘量能(沪深两市
成交额 + 5 日均)、最强题材及核心一二名、题材持续天数、市值偏好、涨跌停制度偏好。

**证据强度标注(§硬要求①,必须透到客户端字段,不能只写注释)**:涨跌幅榜 / 涨停
梯队 / 跌停榜 / 大盘量能 / 市值偏好 / 涨跌停制度偏好全部直接读 `daily`/
`limit_derived`/`daily_basic`/`index_daily` —— **EOD 硬数据,强证据**。最强题材
(`topThemes`)与题材持续天数依赖同花顺概念板块成分(`ths_member`,**当前快照、
无日期字段**,K2「成分洞」)——**弱证据**,每个 `ThemeItem` 携带
`evidenceStrength="constituent"`(与 `holding_k4_check.K4AdvisoryOut.evidenceStrength`
同一套词表,不新造第二套证据强度枚举),不作强判据、只供参考。

**板块池卫生线(硬要求②)**:`topThemes` 的候选板块universe 在排序前先过
`report.board_pool.apply_hygiene`(同 C2 复用同一份卫生线,不另起一份)。

**不阻断主报告管线(硬要求④)**:`compute_intel` 内部逐项用 `_safe()` 包裹——任一
子项计算异常只降级留空 + 记警告(`IntelReport.warnings`),绝不让调用方连带失败;
`pipeline.py` 侧再包一层 try/except 兜底极端情况(如本模块自身的编排逻辑出 bug)。

**落盘**:本模块不写任何 Parquet(纯读 + 内存聚合),`pipeline.py` 落库走既有
`report/store.py` 的 `reports.intel_json` 列(JSON 快照,同 `watchlist_json` 先例),
不新起 parquet 表、不违反「落盘一律走 write_table_day」铁律(因为压根没有新落盘)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

import polars as pl

from neckline.data.market_data import get_index_history, get_market_slice
from neckline.report.board_pool import apply_hygiene, count_members, invert_member_map
from neckline.report.candidates import _load_stock_names
from neckline.report.sectors import DEFAULT_TOP_N, compute_sector_strength, load_index_names, load_member_map
from neckline.strategy.features import SSE_INDEX

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 深证成指(与 features.SSE_INDEX 组合得「沪深两市合计成交额」,大盘量能标准口径;
# 同 scripts/backfill.py::INDEX_CODES 命名,该常量属于 scripts/ 不供包内 import,
# 故本处独立小常量,不新建"指数代码注册表"模块)。
SZ_INDEX = "399001.SZ"

_RANK_LIST_N = 20          # 涨幅/跌幅榜展示条数
_LIMIT_DOWN_DISPLAY_N = 100  # 跌停榜展示上限(超出只截断展示,limitDownTotalCount 给真实总数)
_TOP_THEME_N = DEFAULT_TOP_N  # 最强题材展示条数,复用 sectors.py 既有常量(=10)
_THEME_LEADER_N = 2         # 每个题材展示的核心龙头数
_ALL_BOARDS_TOP_N = 1000    # 远超真实概念板块总数(394,2026-07-24 快照),用于先拿到
                            # 卫生线过滤前的【全量】排序结果,再过滤 + 截断展示
_MARKET_VOLUME_LOOKBACK_DAYS = 20  # 日历天回看窗口,留够 buffer 凑满 5 个交易日(含节假日)

# 市值分桶边界(单位:万元,与 daily_basic.total_mv 同单位;边界为常见散户口径分档)。
_MV_BUCKETS: List[tuple] = [
    (0.0, 500_000.0, "<50亿"),
    (500_000.0, 1_000_000.0, "50-100亿"),
    (1_000_000.0, 3_000_000.0, "100-300亿"),
    (3_000_000.0, 10_000_000.0, "300-1000亿"),
    (10_000_000.0, float("inf"), "≥1000亿"),
]

# 涨跌停幅度分桶标签(limit_pct×100 取整数百分比 → 标签;未识别值原样展示"Ncm"防吞)。
_LIMIT_REGIME_LABELS: Dict[int, str] = {5: "5cm", 10: "10cm", 20: "20cm", 30: "30cm"}

EVIDENCE_NOTE = (
    "涨跌幅榜/涨停梯队/跌停榜/大盘量能/市值偏好/涨跌停制度偏好 = EOD 硬数据"
    "(daily/limit_derived/daily_basic/index_daily 直接读,强证据);"
    "最强题材与题材持续天数依赖同花顺概念板块成分(ths_member 当前快照,K2「成分洞」)"
    "= 弱证据,仅供参考,不作强判据(见各题材项 evidenceStrength 字段)。"
)


def _safe(warnings: List[str], label: str, fn: Callable[[], T], default: T) -> T:
    """情报节子项计算的统一降级包裹(§硬要求④:任一项失败只留空 + 记警告,不
    阻断主报告)。"""
    try:
        return fn()
    except Exception:  # noqa: BLE001 —— 情报节任何子项异常都不能连带主报告失败
        logger.warning("情报节(C1) [%s] 计算异常,已降级留空", label, exc_info=True)
        warnings.append(f"{label}:计算异常,已降级留空(详见服务端日志)。")
        return default


@dataclass
class RankedMover:
    ts_code: str
    name: str
    pct_chg: float
    close: float

    def to_public_dict(self) -> Dict[str, Any]:
        return {"code": self.ts_code, "name": self.name, "pctChg": round(self.pct_chg, 2), "close": round(self.close, 2)}


@dataclass
class LimitLadderRung:
    consec_days: int
    count: int

    def to_public_dict(self) -> Dict[str, Any]:
        return {"consecDays": self.consec_days, "count": self.count}


@dataclass
class MarketVolume:
    sh_amount_yi: float
    sz_amount_yi: float
    total_amount_yi: float
    ma5_amount_yi: float
    sample_days: int   # 参与 5 日均计算的实际交易日数(<5 时诚实标注样本不足)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "shAmountYi": round(self.sh_amount_yi, 2),
            "szAmountYi": round(self.sz_amount_yi, 2),
            "totalAmountYi": round(self.total_amount_yi, 2),
            "ma5AmountYi": round(self.ma5_amount_yi, 2),
            "sampleDays": self.sample_days,
        }


@dataclass
class ThemeLeader:
    ts_code: str
    name: str
    pct_chg: float
    is_limit_up: bool

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "code": self.ts_code, "name": self.name,
            "pctChg": round(self.pct_chg, 2), "isLimitUp": self.is_limit_up,
        }


@dataclass
class ThemeItem:
    index_code: str
    name: str
    board_age: int
    ret_20d: float
    persistence_label: str
    leaders: List[ThemeLeader] = field(default_factory=list)
    evidence_strength: str = "constituent"   # 恒 constituent(成分依赖,弱证据),同 K4AdvisoryOut 词表

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "code": self.index_code, "name": self.name, "boardAge": self.board_age,
            "ret20d": round(self.ret_20d, 4), "persistenceLabel": self.persistence_label,
            "evidenceStrength": self.evidence_strength,
            "leaders": [l.to_public_dict() for l in self.leaders],
        }


@dataclass
class BucketCount:
    label: str
    count: int
    pct_of_total: float

    def to_public_dict(self) -> Dict[str, Any]:
        return {"label": self.label, "count": self.count, "pctOfTotal": round(self.pct_of_total, 4)}


@dataclass
class IntelReport:
    trade_date: date
    evidence_note: str = EVIDENCE_NOTE
    gainers: List[RankedMover] = field(default_factory=list)
    losers: List[RankedMover] = field(default_factory=list)
    limit_up_ladder: List[LimitLadderRung] = field(default_factory=list)
    limit_down: List[RankedMover] = field(default_factory=list)
    limit_down_total_count: int = 0
    market_volume: Optional[MarketVolume] = None
    top_themes: List[ThemeItem] = field(default_factory=list)
    theme_persistence_distribution: Dict[str, int] = field(default_factory=dict)
    mv_preference: List[BucketCount] = field(default_factory=list)
    limit_regime_preference: List[BucketCount] = field(default_factory=list)
    excluded_boards_note: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "tradeDate": self.trade_date.isoformat(),
            "evidenceNote": self.evidence_note,
            "gainers": [m.to_public_dict() for m in self.gainers],
            "losers": [m.to_public_dict() for m in self.losers],
            "limitUpLadder": [r.to_public_dict() for r in self.limit_up_ladder],
            "limitDown": [m.to_public_dict() for m in self.limit_down],
            "limitDownTotalCount": self.limit_down_total_count,
            "marketVolume": self.market_volume.to_public_dict() if self.market_volume else None,
            "topThemes": [t.to_public_dict() for t in self.top_themes],
            "themePersistenceDistribution": dict(self.theme_persistence_distribution),
            "mvPreference": [b.to_public_dict() for b in self.mv_preference],
            "limitRegimePreference": [b.to_public_dict() for b in self.limit_regime_preference],
            "excludedBoardsNote": self.excluded_boards_note,
            "warnings": list(self.warnings),
        }


def empty_intel_report(trade_date: date, reason: str) -> IntelReport:
    """`pipeline.py` 兜底工厂(硬要求④ 的外层保险丝——`compute_intel` 本身内部已
    逐项降级,这里只应对本模块编排逻辑自身出乎意料的异常)。"""
    return IntelReport(trade_date=trade_date, warnings=[reason])


# —— 涨幅/跌幅榜 ——————————————————————————————————————————————————————————

def _rank_movers(daily: pl.DataFrame, names: Dict[str, str], *, descending: bool, n: int) -> List[RankedMover]:
    d = (
        daily.filter(pl.col("pct_chg").is_not_null())
        .sort(["pct_chg", "ts_code"], descending=[descending, False])
    )
    out: List[RankedMover] = []
    for r in d.head(n).iter_rows(named=True):
        code = r["ts_code"]
        out.append(RankedMover(
            ts_code=code, name=names.get(code, code),
            pct_chg=float(r["pct_chg"]), close=float(r["close"] or 0.0),
        ))
    return out


# —— 涨停梯队 / 跌停榜 ————————————————————————————————————————————————————

def _limit_up_ladder(limit_up: pl.DataFrame) -> List[LimitLadderRung]:
    if limit_up.is_empty():
        return []
    agg = (
        limit_up.group_by("consec_limit_up_days")
        .agg(pl.len().alias("n"))
        .sort("consec_limit_up_days", descending=True)   # 最高连板在前(梯队"塔尖"优先)
    )
    return [
        LimitLadderRung(consec_days=int(r["consec_limit_up_days"]), count=int(r["n"]))
        for r in agg.iter_rows(named=True)
    ]


def _limit_down_list(limit_down: pl.DataFrame, daily: pl.DataFrame, names: Dict[str, str]) -> List[RankedMover]:
    if limit_down.is_empty():
        return []
    pct_by_code = dict(zip(daily["ts_code"].to_list(), daily["pct_chg"].to_list())) if not daily.is_empty() else {}
    close_by_code = dict(zip(daily["ts_code"].to_list(), daily["close"].to_list())) if not daily.is_empty() else {}
    d = limit_down.sort("ts_code").head(_LIMIT_DOWN_DISPLAY_N)
    out: List[RankedMover] = []
    for r in d.iter_rows(named=True):
        code = r["ts_code"]
        pct = pct_by_code.get(code)
        close = close_by_code.get(code)
        out.append(RankedMover(
            ts_code=code, name=names.get(code, code),
            pct_chg=float(pct) if pct is not None else 0.0,
            close=float(close) if close is not None else float(r.get("limit_down_price") or 0.0),
        ))
    return out


# —— 大盘量能 ——————————————————————————————————————————————————————————

def _compute_market_volume(trade_date: date, parquet_dir: Optional[Path], warnings: List[str]) -> Optional[MarketVolume]:
    start = trade_date - timedelta(days=_MARKET_VOLUME_LOOKBACK_DAYS)
    sh = get_index_history(SSE_INDEX, start, trade_date, as_of=trade_date, parquet_dir=parquet_dir)
    sz = get_index_history(SZ_INDEX, start, trade_date, as_of=trade_date, parquet_dir=parquet_dir)
    if sh.is_empty() or sz.is_empty():
        warnings.append("大盘量能:index_daily 当日或历史数据缺失,已留空。")
        return None
    combined = (
        sh.select(["trade_date", pl.col("amount").alias("sh_amount")])
        .join(sz.select(["trade_date", pl.col("amount").alias("sz_amount")]), on="trade_date", how="inner")
        .sort("trade_date")
    )
    today_row = combined.filter(pl.col("trade_date") == trade_date)
    if today_row.is_empty():
        warnings.append("大盘量能:当日沪深两市 index_daily 数据缺失或无法对齐,已留空。")
        return None
    combined = combined.with_columns((pl.col("sh_amount") + pl.col("sz_amount")).alias("total_amount"))
    tail = combined.tail(5)
    sample = tail.height
    if sample < 5:
        warnings.append(f"大盘量能:5 日均样本仅 {sample} 个交易日(历史数据不足 5 日),已诚实标注。")
    ma5 = float(tail["total_amount"].mean())
    row = today_row.row(0, named=True)
    # TuShare amount 单位千元(见 tushare_client.py 惯例注释);/100000 换算为亿元。
    to_yi = 1.0 / 100000.0
    return MarketVolume(
        sh_amount_yi=row["sh_amount"] * to_yi,
        sz_amount_yi=row["sz_amount"] * to_yi,
        total_amount_yi=(row["sh_amount"] + row["sz_amount"]) * to_yi,
        ma5_amount_yi=ma5 * to_yi,
        sample_days=sample,
    )


# —— 最强题材 + 核心龙头 + 题材持续天数 ————————————————————————————————————

def _persistence_label(board_age: int) -> str:
    if board_age <= 0:
        return "未站上MA20(非持续)"
    if board_age == 1:
        return "新起(1日)"
    if board_age <= 3:
        return "持续中(2-3日)"
    return "已延续(≥4日,警惕退潮)"


def _theme_leaders(
    codes: List[str], daily_by_code: Dict[str, Dict[str, float]], limit_up_codes: set, names: Dict[str, str],
) -> List[ThemeLeader]:
    rows = [(c, daily_by_code[c]["pct_chg"]) for c in codes if c in daily_by_code]
    rows.sort(key=lambda x: x[1], reverse=True)
    out: List[ThemeLeader] = []
    for code, pct in rows[:_THEME_LEADER_N]:
        out.append(ThemeLeader(
            ts_code=code, name=names.get(code, code), pct_chg=float(pct), is_limit_up=code in limit_up_codes,
        ))
    return out


def _top_themes(
    trade_date: date,
    daily: pl.DataFrame,
    limit_up_codes: set,
    member_map: Optional[Dict[str, List[str]]],
    index_names: Optional[Dict[str, str]],
    parquet_dir: Optional[Path],
    db_path: Optional[Path],
    warnings: List[str],
) -> tuple:
    """返回 (top_themes, persistence_distribution, excluded_boards_note)。板块池
    先过卫生线(硬要求②,C1/C2 复用同一份),再截断展示前 `_TOP_THEME_N`。"""
    all_scores = compute_sector_strength(trade_date, parquet_dir=parquet_dir, top_n=_ALL_BOARDS_TOP_N)
    if not all_scores:
        return [], {}, ""

    member_map = member_map if member_map is not None else load_member_map(parquet_dir=parquet_dir)
    index_names = index_names if index_names is not None else load_index_names(parquet_dir=parquet_dir)
    counts = count_members(member_map)
    hygiene = apply_hygiene(index_names, counts)
    audit = hygiene.audit_lines()
    excluded_note = ("板块池卫生线已剔除:" + "；".join(audit)) if audit else ""
    if audit:
        logger.info("情报节(C1) 板块池卫生线剔除审计: %s", "；".join(audit))

    kept_scores = [s for s in all_scores if s.index_code in hygiene.kept][:_TOP_THEME_N]
    if not kept_scores:
        return [], {}, excluded_note

    inv = invert_member_map(member_map)
    daily_by_code: Dict[str, Dict[str, float]] = {
        r["ts_code"]: r for r in daily.select(["ts_code", "pct_chg"]).iter_rows(named=True)
    } if not daily.is_empty() else {}

    leader_codes: List[str] = []
    for s in kept_scores:
        leader_codes.extend(inv.get(s.index_code, []))
    names = _load_stock_names(list(dict.fromkeys(leader_codes)), db_path)

    themes: List[ThemeItem] = []
    dist: Dict[str, int] = {}
    for s in kept_scores:
        label = _persistence_label(s.board_age)
        dist[label] = dist.get(label, 0) + 1
        leaders = _theme_leaders(inv.get(s.index_code, []), daily_by_code, limit_up_codes, names)
        themes.append(ThemeItem(
            index_code=s.index_code, name=s.name, board_age=s.board_age,
            ret_20d=s.ret_20d, persistence_label=label, leaders=leaders,
        ))
    return themes, dist, excluded_note


# —— 市值偏好 / 涨跌停制度偏好 ————————————————————————————————————————————
#
# 两者刻意采用不同的分桶展示策略,不是疏漏:市值偏好固定展示 `_MV_BUCKETS` 全部
# 5 档(哪怕当日某档 0 只)——散户市值分档是稳定 taxonomy,跨日对比需要桶位固定;
# 涨跌停制度偏好只展示当日**实际出现过**的幅度值(见 `_limit_regime_preference`
# 的 `for p in sorted(counts)`)——可能出现的幅度值理论上不封闭(万一未来新增
# 板块类型),没有一个"标准 5 档"可固定枚举,展示当日真实出现的值更诚实。

def _mv_preference(limit_up: pl.DataFrame, daily_basic: pl.DataFrame) -> List[BucketCount]:
    if limit_up.is_empty() or daily_basic.is_empty():
        return []
    mv_by_code = dict(zip(daily_basic["ts_code"].to_list(), daily_basic["total_mv"].to_list()))
    bucket_counts = [0] * len(_MV_BUCKETS)
    classified = 0
    for code in limit_up["ts_code"].to_list():
        mv = mv_by_code.get(code)
        if mv is None:
            continue
        classified += 1
        for i, (lo, hi, _label) in enumerate(_MV_BUCKETS):
            if lo <= mv < hi:
                bucket_counts[i] += 1
                break
    if classified == 0:
        return []
    return [
        BucketCount(label=label, count=cnt, pct_of_total=cnt / classified)
        for (_lo, _hi, label), cnt in zip(_MV_BUCKETS, bucket_counts)
    ]


def _limit_regime_preference(limit_up: pl.DataFrame) -> List[BucketCount]:
    if limit_up.is_empty():
        return []
    pct_ints = [round(float(p) * 100) for p in limit_up["limit_pct"].to_list()]
    total = len(pct_ints)
    counts: Dict[int, int] = {}
    for p in pct_ints:
        counts[p] = counts.get(p, 0) + 1
    out: List[BucketCount] = []
    for p in sorted(counts):
        label = _LIMIT_REGIME_LABELS.get(p, f"{p}cm")
        out.append(BucketCount(label=label, count=counts[p], pct_of_total=counts[p] / total))
    return out


# —— 主入口 ——————————————————————————————————————————————————————————————

def compute_intel(
    trade_date: date,
    *,
    member_map: Optional[Dict[str, List[str]]] = None,
    index_names: Optional[Dict[str, str]] = None,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> IntelReport:
    """复盘情报件 I/O 入口(角色对应 `sentiment.compute_sentiment`/`sectors.
    compute_sector_strength`)。纯读 + 内存聚合,不写任何库/文件。任一子项异常
    只降级留空 + 记警告(见 `_safe`),不向上抛出——`pipeline.py` 仍需再包一层
    try/except 兜底本函数编排逻辑自身的意外(硬要求④双保险)。"""
    warnings: List[str] = []
    daily = get_market_slice(trade_date, table="daily", parquet_dir=parquet_dir)
    limit = get_market_slice(trade_date, table="limit_derived", parquet_dir=parquet_dir)
    limit_up = limit.filter(pl.col("is_limit_up")) if not limit.is_empty() else limit
    limit_down = limit.filter(pl.col("is_limit_down")) if not limit.is_empty() else limit
    limit_up_codes = set(limit_up["ts_code"].to_list()) if not limit_up.is_empty() else set()

    if daily.is_empty():
        warnings.append("涨跌幅榜:当日 daily 无数据,已留空。")
    if limit.is_empty():
        warnings.append("涨停梯队/跌停榜/涨跌停制度偏好:当日 limit_derived 无数据,已留空。")

    mover_names: Dict[str, str] = {}
    if not daily.is_empty():
        top_codes = set(
            daily.filter(pl.col("pct_chg").is_not_null()).sort("pct_chg", descending=True).head(_RANK_LIST_N)["ts_code"].to_list()
        ) | set(
            daily.filter(pl.col("pct_chg").is_not_null()).sort("pct_chg").head(_RANK_LIST_N)["ts_code"].to_list()
        )
        if not limit_down.is_empty():
            top_codes |= set(limit_down.sort("ts_code").head(_LIMIT_DOWN_DISPLAY_N)["ts_code"].to_list())
        mover_names = _safe(warnings, "涨跌幅/跌停名称解析", lambda: _load_stock_names(list(top_codes), db_path), {})

    gainers = _safe(warnings, "涨幅榜", lambda: _rank_movers(daily, mover_names, descending=True, n=_RANK_LIST_N), []) if not daily.is_empty() else []
    losers = _safe(warnings, "跌幅榜", lambda: _rank_movers(daily, mover_names, descending=False, n=_RANK_LIST_N), []) if not daily.is_empty() else []
    ladder = _safe(warnings, "涨停梯队", lambda: _limit_up_ladder(limit_up), [])
    limit_down_list = _safe(warnings, "跌停榜", lambda: _limit_down_list(limit_down, daily, mover_names), [])
    limit_down_total = int(limit_down.height) if not limit_down.is_empty() else 0

    market_volume = _safe(warnings, "大盘量能", lambda: _compute_market_volume(trade_date, parquet_dir, warnings), None)

    top_themes, persistence_dist, excluded_note = _safe(
        warnings, "最强题材/题材持续天数",
        lambda: _top_themes(trade_date, daily, limit_up_codes, member_map, index_names, parquet_dir, db_path, warnings),
        ([], {}, ""),
    )

    daily_basic = get_market_slice(trade_date, table="daily_basic", parquet_dir=parquet_dir)
    mv_pref = _safe(warnings, "市值偏好", lambda: _mv_preference(limit_up, daily_basic), [])
    regime_pref = _safe(warnings, "涨跌停制度偏好", lambda: _limit_regime_preference(limit_up), [])

    return IntelReport(
        trade_date=trade_date,
        gainers=gainers, losers=losers,
        limit_up_ladder=ladder, limit_down=limit_down_list, limit_down_total_count=limit_down_total,
        market_volume=market_volume,
        top_themes=top_themes, theme_persistence_distribution=persistence_dist,
        mv_preference=mv_pref, limit_regime_preference=regime_pref,
        excluded_boards_note=excluded_note,
        warnings=warnings,
    )


__all__ = [
    "RankedMover",
    "LimitLadderRung",
    "MarketVolume",
    "ThemeLeader",
    "ThemeItem",
    "BucketCount",
    "IntelReport",
    "EVIDENCE_NOTE",
    "SZ_INDEX",
    "compute_intel",
    "empty_intel_report",
]
