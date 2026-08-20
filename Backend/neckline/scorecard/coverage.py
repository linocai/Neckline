"""覆盖率成绩线(K9 §5.2 / 架构 §5.2,PROJECT_PLAN §5.8.1)。

> **定义**:当日真正走强的票,有几只出现在**昨天**的清单里。

🔴 **口径 = 涨停,⛔ 不依赖任何待标定数字。** 涨停是硬事实(`data/limit_derived.py`
自算,含 2020-08-24 创业板改革与 2026-07-06 主板 ST 两个制度分界日),所以这条线
在参数标定完成之前就能跑 —— **它是尺子**。

**两个数,NULL 与 0 语义不同(⛔ 不许互顶)**:

| 指标 | 分母 | 分子 | 读参数吗 |
|---|---|---|---|
| `coverage_all`(**头条**) | 当日**全部**涨停票 | 其中出现在 D−1 清单里的只数 | **否** |
| `coverage_in_pool`(辅助) | D−1 **未被硬边界排除**的涨停票 | 同上 | **是** → 缺参数时写 NULL |

`coverage_all` 的分母刻意**不剔除**科创板 / 北交所 —— K9 第一层排除它们,于是这些票
的涨停结构上永远覆盖不到,而这正是要看见的东西(架构 §5.2:「它衡量的是事实层与
策略层的**联合**漏检」)。想看「在池子里的那部分」就看 `coverage_in_pool`。

**参数没到齐也能跑的那半**(§5.8.1):**涨停普查 + 涨停簇画像 + 当日涨停票的结构性
分布**(分板块 / ST / 申万二级)从第一天就出数;「命中昨日清单」那一项在清单开始
产出的次日自动接上。

⚠ **本模块里没有「被第 N 条边界排除」的判定。**
9 条排除项里有 4 条要参数(次新股天数 / 流动性窗口与分位 / 冲高回落两个门槛 /
白酒的 `excludedL2Codes`),而**判定本身**无论要不要参数都是策略主张。
本模块只做两件事:① 如实报出事实包里的**结构性事实**(板块 / ST / 申万二级);
② 把 D−1 的 `k9_disposition`(S6 产出)里已经写好的 `excluded_by` **原样转述**。
⛔ 不自己判、⛔ 不自己编一条「参数缺失时的简化边界」。

**⛔ 不回填历史覆盖率**(§5.8.1 末):上线前没有清单,编不出来。
`refresh_range` 只会为**有冻结事实包**的日子出「涨停普查」那一半,
`coverage_all` 在没有 D−1 清单时一律 **NULL**,⛔ 不是 0。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import polars as pl

from neckline.facts import limitmap as limitmap_mod
from neckline.facts import store as fact_store

logger = logging.getLogger(__name__)

#: 漏检归因的**闭合枚举**。⛔ 不许现编字符串 —— 归因值是要拿来分类统计的。
REASON_NO_LISTING = "no_listing"                 # 昨天没有清单(上线首日 / 参数未配置)
REASON_NO_DISPOSITION = "no_disposition"         # 有清单但没有 D−1 全市场 disposition
REASON_EXCLUDED_BY_BOUNDARY = "excluded_by_boundary"   # D−1 被硬边界排除(detail = 哪一条)
REASON_NOT_RECALLED = "not_recalled"             # 在池子里,四通道都没召回
REASON_RECALLED_NOT_SEATED = "recalled_not_seated"     # 召回了但没进席(detail = 名次)
REASON_NEWS_EXCLUDED = "news_excluded"           # 解释层消息面剔除

MISS_REASONS: Tuple[str, ...] = (
    REASON_NO_LISTING,
    REASON_NO_DISPOSITION,
    REASON_EXCLUDED_BY_BOUNDARY,
    REASON_NOT_RECALLED,
    REASON_RECALLED_NOT_SEATED,
    REASON_NEWS_EXCLUDED,
)

#: 覆盖率只需要事实包里的这几列(列投影是必填的,§12 坑 1)。
NEEDED_COLUMNS: Tuple[str, ...] = (
    "ts_code", "name", "board", "is_st", "sw_l2_code", "sw_l2_name",
    "is_limit_up", "is_limit_down", "is_limit_open", "consec_limit_up_days",
)


@dataclass(frozen=True)
class ListingSnapshot:
    """D−1 的清单快照。`codes` 是那天清单上的全部 `ts_code`。

    ⚠ **观察分支仍进覆盖率**(K9 §八):覆盖率只看「昨天在不在清单里」,与三分支
    判定无关,⛔ 不许按 `verdict` 过滤这个集合。"""

    trade_date: date
    codes: frozenset


@dataclass(frozen=True)
class DispositionRow:
    """D−1 全市场 disposition 的一行(S6 的 `k9_disposition` parquet,§5.4.8)。

    本模块只**转述**它,⛔ 不重算里面任何一个判定。"""

    ts_code: str
    excluded_by: Optional[str]      # 9 条排除项里的哪一条;None = 没被边界排除
    recalled: bool
    rank: Optional[int]
    seated: bool
    news_excluded: bool


@dataclass(frozen=True)
class Miss:
    """一只**没被覆盖**的涨停票 + 它的归因。"""

    ts_code: str
    name: Optional[str]
    board: Optional[str]
    sw_l2_code: Optional[str]
    sw_l2_name: Optional[str]
    consec_limit_up_days: int
    reason: str
    detail: Optional[str] = None


@dataclass(frozen=True)
class CoverageDay:
    """一天的覆盖率结果。

    🔴 `coverage_all` / `covered_count` 为 `None` = **昨天还没有清单**,
    ⛔ 不是「一只都没覆盖到」;`coverage_in_pool` 为 `None` = 没有 D−1 disposition
    (边界参数缺失),⛔ 不是 0。两处 NULL 都是 §5.8.1 逐字要求的。"""

    trade_date: date
    pack_id: str
    pack_version: str
    limit_up_count: int
    limit_down_count: int
    zaban_count: int
    zaban_rate: Optional[float]
    max_consec_days: Optional[int]
    cluster_count: int
    listing_trade_date: Optional[date]
    listing_size: Optional[int]
    covered_count: Optional[int]
    coverage_all: Optional[float]
    in_pool_denominator: Optional[int]
    covered_in_pool: Optional[int]
    coverage_in_pool: Optional[float]
    census: Dict[str, object]
    misses: Tuple[Miss, ...] = field(default_factory=tuple)


def _ratio(num: Optional[int], den: Optional[int]) -> Optional[float]:
    """分母 0 或缺失 → `None`(⛔ 不是 0:「没覆盖到」与「没得覆盖」是两件事)。"""
    if num is None or not den:
        return None
    return num / den


def _census(limit_ups: pl.DataFrame, lmap: limitmap_mod.LimitMap) -> Dict[str, object]:
    """涨停普查 + 涨停簇画像 + 结构性分布。**全部来自事实包的列,零参数。**"""
    by_board: Dict[str, int] = {}
    by_l2: List[dict] = []
    st_count = 0
    if not limit_ups.is_empty():
        for r in (
            limit_ups.group_by("board").agg(pl.len().alias("n")).sort("board").iter_rows(named=True)
        ):
            by_board[str(r["board"] or "UNKNOWN")] = int(r["n"])
        st_count = int(limit_ups.filter(pl.col("is_st")).height)
        by_l2 = [
            {"l2Code": r["sw_l2_code"], "l2Name": r["sw_l2_name"], "count": int(r["n"])}
            for r in (
                limit_ups.filter(pl.col("sw_l2_code").is_not_null())
                .group_by(["sw_l2_code", "sw_l2_name"])
                .agg(pl.len().alias("n"))
                .sort(["n", "sw_l2_code"], descending=[True, False])
                .iter_rows(named=True)
            )
        ]
    return {
        "byBoard": by_board,
        "stCount": st_count,
        "byL2": by_l2,
        "consecHistogram": {str(k): v for k, v in sorted(lmap.consec_histogram.items())},
        "clusters": [c.to_dict() for c in lmap.clusters],
    }


def _attribute(
    code: str,
    disp: Optional[DispositionRow],
    listing: Optional[ListingSnapshot],
) -> Tuple[str, Optional[str]]:
    """一只没被覆盖的涨停票 → `(reason, detail)`。**只转述,不重判。**"""
    if listing is None:
        return REASON_NO_LISTING, None
    if disp is None:
        return REASON_NO_DISPOSITION, None
    if disp.excluded_by:
        return REASON_EXCLUDED_BY_BOUNDARY, disp.excluded_by
    if disp.news_excluded:
        return REASON_NEWS_EXCLUDED, None
    if not disp.recalled:
        return REASON_NOT_RECALLED, None
    return REASON_RECALLED_NOT_SEATED, (None if disp.rank is None else f"rank={disp.rank}")


def compute_day(
    pack: fact_store.FactPack,
    *,
    listing: Optional[ListingSnapshot] = None,
    dispositions: Optional[Sequence[DispositionRow]] = None,
) -> CoverageDay:
    """**纯函数**(除了从 pack 现读一次 parquet):一天的覆盖率。

    ⛔ 签名里没有参数包,也**收不下**参数包 —— `coverage_all` 的整条计算路径
    结构上读不到任何待标定数字(§5.8.1)。策略侧的信息只能经 `dispositions`
    这条**数据**通道进来。

    ⚠ `pack.rows` 是**每次访问现读 parquet** 的属性(`facts/store.py` 纪律 4),
    所以这里取**一次**存局部变量 —— 上一版一次调用读了 3 遍(`.columns` /
    `.select(...)` / `limitmap.compute(...)` 各一次),5500 行 × 3 在 §12 坑 1
    那台 2 vCPU / 1.6 G 的机器上没必要(复审 L4)。
    """
    frame = pack.rows                     # ← 只读这一次
    rows = frame.select([c for c in NEEDED_COLUMNS if c in frame.columns])
    lmap = limitmap_mod.compute(frame)
    limit_ups = rows.filter(pl.col("is_limit_up")) if not rows.is_empty() else rows

    disp_of = {d.ts_code: d for d in (dispositions or ())}
    codes = limit_ups["ts_code"].to_list() if not limit_ups.is_empty() else []

    covered_count: Optional[int] = None
    coverage_all: Optional[float] = None
    if listing is not None:
        covered_count = sum(1 for c in codes if c in listing.codes)
        coverage_all = _ratio(covered_count, len(codes))

    in_pool_denom: Optional[int] = None
    covered_in_pool: Optional[int] = None
    coverage_in_pool: Optional[float] = None
    if listing is not None and dispositions is not None:
        in_pool = [c for c in codes if (d := disp_of.get(c)) is not None and not d.excluded_by]
        in_pool_denom = len(in_pool)
        covered_in_pool = sum(1 for c in in_pool if c in listing.codes)
        coverage_in_pool = _ratio(covered_in_pool, in_pool_denom)

    misses: List[Miss] = []
    if not limit_ups.is_empty():
        for r in limit_ups.iter_rows(named=True):
            code = r["ts_code"]
            if listing is not None and code in listing.codes:
                continue
            reason, detail = _attribute(code, disp_of.get(code), listing)
            misses.append(Miss(
                ts_code=code,
                name=r.get("name"),
                board=r.get("board"),
                sw_l2_code=r.get("sw_l2_code"),
                sw_l2_name=r.get("sw_l2_name"),
                consec_limit_up_days=int(r.get("consec_limit_up_days") or 0),
                reason=reason,
                detail=detail,
            ))

    return CoverageDay(
        trade_date=pack.trade_date,
        pack_id=pack.pack_id,
        pack_version=pack.pack_version,
        limit_up_count=len(codes),
        limit_down_count=lmap.limit_down_count,
        zaban_count=lmap.zaban_count,
        zaban_rate=lmap.zaban_rate,
        max_consec_days=lmap.max_consec_days,
        cluster_count=len(lmap.clusters),
        listing_trade_date=None if listing is None else listing.trade_date,
        listing_size=None if listing is None else len(listing.codes),
        covered_count=covered_count,
        coverage_all=coverage_all,
        in_pool_denominator=in_pool_denom,
        covered_in_pool=covered_in_pool,
        coverage_in_pool=coverage_in_pool,
        census=_census(limit_ups, lmap),
        misses=tuple(sorted(misses, key=lambda m: m.ts_code)),
    )


def refresh_day(
    trade_date: date,
    *,
    listing: Optional[ListingSnapshot] = None,
    dispositions: Optional[Sequence[DispositionRow]] = None,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Optional[CoverageDay]:
    """算一天并落表。该日**没有冻结事实包** → `None`(「今天没跑成」的日子没有涨停
    普查可言,⛔ 不编一行 0)。

    ⚠ **`listing` / `dispositions` 由编排器递进来,本模块不去找它们。**
    K9 清单(`k9_listing_entries`)与全市场 disposition(`k9_disposition`)是 S6 的
    产物,现在还不存在;两者缺席时这一天只出「涨停普查」那一半,`coverage_all`
    为 NULL —— 这正是 §5.8.1 说的「清单开始产出的次日自动接上」。
    """
    from neckline.scorecard import store as scorecard_store

    try:
        pack = fact_store.load_pack(trade_date, parquet_dir=parquet_dir, db_path=db_path)
    except fact_store.PackNotFrozen:
        logger.info("[coverage] %s 没有冻结事实包,跳过(⛔ 不编一行 0)", trade_date)
        return None
    day = compute_day(pack, listing=listing, dispositions=dispositions)
    scorecard_store.save_coverage_day(day, db_path=db_path)
    logger.info(
        "[coverage] %s 涨停 %d 只 / 炸板 %d 只 / 申万二级涨停簇 %d 个;coverage_all=%s",
        trade_date, day.limit_up_count, day.zaban_count, day.cluster_count,
        "NULL(昨天没有清单)" if day.coverage_all is None else f"{day.coverage_all:.1%}",
    )
    return day


__all__ = [
    "MISS_REASONS",
    "REASON_NO_LISTING",
    "REASON_NO_DISPOSITION",
    "REASON_EXCLUDED_BY_BOUNDARY",
    "REASON_NOT_RECALLED",
    "REASON_RECALLED_NOT_SEATED",
    "REASON_NEWS_EXCLUDED",
    "NEEDED_COLUMNS",
    "ListingSnapshot",
    "DispositionRow",
    "Miss",
    "CoverageDay",
    "compute_day",
    "refresh_day",
]
