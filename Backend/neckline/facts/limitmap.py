"""涨停分布与涨停簇(架构第一层 · 事实层,PROJECT_PLAN §5.3.1「市场级读数」)。

**V2.5.0 S3 重写**。S1 只是把 `scan/cluster.py` 原样搬进 `facts/`(一字未动),
本片按裁定 3 把它切到**申万二级**并砍掉两件不该在事实层的东西:

| 项 | S1 搬进来时 | S3 现在 | 为什么 |
|---|---|---|---|
| 行业口径 | `stock_basic.industry`(旧 110 行业) | **申万二级 `sw_l2_code`** | 裁定 3。S4 覆盖率线以涨停为口径,归因落在旧分类上会让「漏掉的是哪一类票」整条结论都是错的 |
| 概念板块锚点 | 与行业并列的第二种锚 | **删除** | K9 §3.0 / 架构 §3.1 明令「概念板块不进入任何机械计算」 |
| 落表 | upsert 进 `limit_cluster_daily` | **不落表,纯函数** | §5.3.1 定死:涨停簇**摘要**进 `fact_packs.market_json`。`limit_cluster_daily` 装的是 K8 口径的旧行,按裁定 6 **只读留档**;把新口径的行掺进同一列 = 静默口径漂移 |

于是本模块变成**零 I/O 的纯计算件**:输入是已装配好的当日事实包大表,输出是可直接
序列化进 `market_json` 的市场级读数。⛔ 不读库、不读 parquet、不认参数包。

**簇的定义**:当日涨停票里,共享同一个申万二级行业且成员数 `>= MIN_CLUSTER_SIZE`
的一组。两种 `kind`:
    · `same_day`    —— 当日涨停(不论是否连板)的票,按二级行业分组;
      捕捉「今天谁跟谁一起涨停」的资金共振。
    · `consecutive` —— 当日涨停**且**已连续 ≥2 天(`consec_limit_up_days >= 2`)的票;
      捕捉「这个题材里好几只票都在接力连板」(`same_day` 的子集,同一票同一天可能
      同时出现在两种 kind 下)。

⚠ `MIN_CLUSTER_SIZE = 2` **是工程常量,不是策略参数** —— 它回答的是「这个簇**存不
存在**」(孤身一只涨停按字面就不构成「共振」),不是「这个簇够不够格当种子」。
🔴 与之相对,`facts/industry.py` 刻意**没有**最小成员数门槛:那一个决定的是「哪些票
拿不到行业强度、进不了形态召回」,是**策略主张**,必须走参数包(§8.2 第 16 项)。
两者分工不同,⛔ 别把这条注释当成「事实层可以自带门槛」的通行证。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import polars as pl

logger = logging.getLogger(__name__)

#: 少于 2 只不构成「共振」(工程不变量,见模块 docstring 的 ⚠)。
MIN_CLUSTER_SIZE = 2

SAME_DAY = "same_day"
CONSECUTIVE = "consecutive"

#: 本模块要求事实包大表提供的列(其余列一概不看)。
REQUIRED_COLUMNS: Tuple[str, ...] = (
    "ts_code", "board", "sw_l2_code", "sw_l2_name",
    "is_limit_up", "is_limit_down", "is_limit_open", "consec_limit_up_days",
)


@dataclass(frozen=True)
class LimitCluster:
    """一个涨停簇。`members` 按 `ts_code` 升序(可复现,⛔ 不依赖行序)。"""

    kind: str
    l2_code: str
    l2_name: str
    size: int
    members: Tuple[str, ...]
    max_consec_days: int

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "l2Code": self.l2_code,
            "l2Name": self.l2_name,
            "size": self.size,
            "members": list(self.members),
            "maxConsecDays": self.max_consec_days,
        }


@dataclass(frozen=True)
class LimitMap:
    """当日涨停地图。全部字段都是**当日**读数,⛔ 无任何窗口量(§5.3)。"""

    limit_up_codes: Tuple[str, ...]
    limit_up_count: int
    limit_down_count: int
    zaban_count: int
    #: 炸板率 = 炸板 ÷ (涨停 + 炸板)。分母 0(当日既无涨停也无炸板)→ `None`,
    #: ⛔ 不是 0 ——「没炸」与「没得炸」是两件事。
    zaban_rate: Optional[float]
    #: 连板高度 = 当日涨停票里最大的 `consec_limit_up_days`。无涨停 → `None`。
    max_consec_days: Optional[int]
    #: 连板高度分布 `{天数: 只数}`,只统计当日涨停票。
    consec_histogram: Dict[int, int]
    #: 分板块的涨停 / 跌停 / 炸板家数 `{board: {...}}`。
    by_board: Dict[str, Dict[str, int]]
    clusters: Tuple[LimitCluster, ...]

    def to_dict(self) -> dict:
        return {
            "limitUpCount": self.limit_up_count,
            "limitDownCount": self.limit_down_count,
            "zabanCount": self.zaban_count,
            "zabanRate": self.zaban_rate,
            "maxConsecDays": self.max_consec_days,
            "consecHistogram": {str(k): v for k, v in sorted(self.consec_histogram.items())},
            "byBoard": {k: self.by_board[k] for k in sorted(self.by_board)},
            "clusters": [c.to_dict() for c in self.clusters],
        }


def _empty() -> "LimitMap":
    return LimitMap(
        limit_up_codes=(), limit_up_count=0, limit_down_count=0, zaban_count=0,
        zaban_rate=None, max_consec_days=None, consec_histogram={}, by_board={}, clusters=(),
    )


def _clusters_for(rows: pl.DataFrame, kind: str, min_size: int) -> List[LimitCluster]:
    """按 `sw_l2_code` 分组产簇。无申万归属(`sw_l2_code` 为空)的票**不参与聚类**
    —— 「查无行业」不是一个行业,⛔ 不许把它们凑成一个「其它」簇。"""
    if rows.is_empty():
        return []
    named = rows.filter(pl.col("sw_l2_code").is_not_null() & (pl.col("sw_l2_code") != ""))
    if named.is_empty():
        return []
    grouped = (
        named.group_by(["sw_l2_code", "sw_l2_name"])
        .agg(
            pl.col("ts_code").sort().alias("members"),
            pl.len().alias("size"),
            pl.col("consec_limit_up_days").max().alias("max_consec"),
        )
        .filter(pl.col("size") >= min_size)
        .sort(["sw_l2_code"])
    )
    return [
        LimitCluster(
            kind=kind,
            l2_code=r["sw_l2_code"],
            l2_name=r["sw_l2_name"] or "",
            size=int(r["size"]),
            members=tuple(r["members"]),
            max_consec_days=int(r["max_consec"] or 0),
        )
        for r in grouped.iter_rows(named=True)
    ]


def compute(pack_rows: pl.DataFrame, *, min_cluster_size: int = MIN_CLUSTER_SIZE) -> LimitMap:
    """**纯函数,无 I/O**:事实包大表 → 当日涨停地图。

    `pack_rows` 需含 `REQUIRED_COLUMNS`;缺列直接抛(⛔ 不静默降级成「今天没涨停」
    —— 那会让一次装配 bug 伪装成一个平静的市场)。
    """
    if pack_rows.is_empty():
        return _empty()
    missing = [c for c in REQUIRED_COLUMNS if c not in pack_rows.columns]
    if missing:
        raise ValueError(f"事实包大表缺列 {missing},涨停地图算不出(⛔ 不降级成「今天没涨停」)")

    df = pack_rows.select(list(REQUIRED_COLUMNS)).with_columns(
        pl.col("is_limit_up").fill_null(False),
        pl.col("is_limit_down").fill_null(False),
        pl.col("is_limit_open").fill_null(False),
        pl.col("consec_limit_up_days").fill_null(0),
    )
    up = df.filter(pl.col("is_limit_up"))
    down_count = int(df.filter(pl.col("is_limit_down")).height)
    zaban_count = int(df.filter(pl.col("is_limit_open")).height)
    up_count = int(up.height)

    denom = up_count + zaban_count
    zaban_rate = (zaban_count / denom) if denom else None

    by_board: Dict[str, Dict[str, int]] = {}
    for r in (
        df.group_by("board")
        .agg(
            pl.col("is_limit_up").sum().alias("limit_up"),
            pl.col("is_limit_down").sum().alias("limit_down"),
            pl.col("is_limit_open").sum().alias("zaban"),
        )
        .iter_rows(named=True)
    ):
        by_board[str(r["board"] or "UNKNOWN")] = {
            "limitUp": int(r["limit_up"]),
            "limitDown": int(r["limit_down"]),
            "zaban": int(r["zaban"]),
        }

    hist: Dict[int, int] = {}
    max_consec: Optional[int] = None
    if up_count:
        for r in up.group_by("consec_limit_up_days").agg(pl.len().alias("n")).iter_rows(named=True):
            hist[int(r["consec_limit_up_days"])] = int(r["n"])
        max_consec = int(up["consec_limit_up_days"].max() or 0)

    consecutive = up.filter(pl.col("consec_limit_up_days") >= 2)
    clusters = tuple(
        _clusters_for(up, SAME_DAY, min_cluster_size)
        + _clusters_for(consecutive, CONSECUTIVE, min_cluster_size)
    )

    return LimitMap(
        limit_up_codes=tuple(sorted(up["ts_code"].to_list())),
        limit_up_count=up_count,
        limit_down_count=down_count,
        zaban_count=zaban_count,
        zaban_rate=zaban_rate,
        max_consec_days=max_consec,
        consec_histogram=hist,
        by_board=by_board,
        clusters=clusters,
    )


__all__ = [
    "MIN_CLUSTER_SIZE",
    "SAME_DAY",
    "CONSECUTIVE",
    "REQUIRED_COLUMNS",
    "LimitCluster",
    "LimitMap",
    "compute",
]
