"""涨停分布与涨停簇(V2.5.0 S1:自 `scan/cluster.py` 原样搬入 `facts/`)。

搬家理由(PROJECT_PLAN §3.3):它算的是**当日客观市场结构事实**,不读任何策略参数,
正是架构第一层 · 事实层的东西;`scan/` 的驱动种子体系(主观判断"在哪算是热")随 K8
整块退役。

⚠ **S1 只搬,不改**:内容一字未动,仍读 `report/{board_pool,industry_strength,sectors}.py`
的三个 helper。S3 建事实层时把行业来源换成申万二级,那三个 K8 报告件届时一并退役
(它们在 S1 被**刻意留下**,就是因为本文件还在用 —— 见 PROJECT_PLAN §14 的 S1 登记)。

以下为原模块头,内容未改 ——
涨停共振簇预计算表 `limit_cluster_daily`(plan §五 V2-④,P0-23:EOD 预计算落表、
在线只读)。

**做什么**:把当日(或指定区间每一天)按 (行业 ∪ 概念成分) 对涨停股聚类,产出
两种 `cluster_kind`:
    · `same_day`     —— 当日涨停(不论是否连板)的票,按共享的行业 / 概念分组;
      捕捉"今天谁跟谁一起涨停"的资金共振。
    · `consecutive`  —— 当日涨停**且**已连续 ≥2 天(`limit_derived.
      consec_limit_up_days >= 2`,即今天至少是第 2 个涨停板)的票,按共享的
      行业 / 概念分组;捕捉"这个题材里好几只票都在接力连板"的持续性资金共振
      (`same_day` 的子集,同一票同一天可能**同时**属于两种 cluster_kind,各自
      有独立的 `cluster_key`,composite PK 不冲突)。

**只需要 ≥2 只成员才算"簇"**(`MIN_CLUSTER_SIZE`,工程常量,同
`report/industry_strength.py::_MIN_MEMBERS` 的既有分工——事实表用引擎常量,
不读策略包;策略包管的是"这个簇够不够格当种子",不是"这个簇存不存在")。孤身
涨停不构成"共振",不落一行 `cluster_size=1` 的记录。

**anchor 语义**:每个簇恰好被**一种**维度(`industry` 或 `concept`)锚定——
`anchor_industry` 与 `anchor_concept` 互斥(恰好一个非空)。同一票可能因为
"同行业"和"同概念"两种维度各自成簇而出现在**多个** `cluster_key` 下(结构上
允许,PK 含 `cluster_key`)。

**`consecutive_days` 是每个成员自己的量**(= 该 `ts_code` 当日
`limit_derived.consec_limit_up_days`),**不是簇的聚合值**——PK 是
`(trade_date, cluster_key, ts_code)`,每行天然带着"这一行代表哪只票"的语境,
`cluster_size` 才是簇级聚合(denormalize 到每个成员行,同 `leader_structure_daily`
消费习惯:调用方按 `ts_code` 过滤后仍能拿到簇大小,不必再 join 一次)。

**cluster_key 生成**:`crc32(trade_date|cluster_kind:anchor_type:anchor_value)`
十六进制(承 §五铁律"跨进程/跨天可复现的分组一律 zlib.crc32,不用内置 hash()")。
`cluster_kind` 编进被哈希的字符串,保证 `same_day` 与 `consecutive` 两种簇即使
锚定同一个 (anchor_type, anchor_value) 也不会撞 key。

**继承只读,不重算**:板块分类/涨跌停判定/行业口径全部只读唯一源
(`data/limit_derived.py` 的 `is_limit_up`/`consec_limit_up_days`,
`report/industry_strength.py::load_industry_map` 的 `stock_basic.industry`,
`report/sectors.py::load_member_map` + `report/board_pool.py::apply_hygiene`
的概念成分——**概念板块卫生线复用既有实现,不重抄一份剔除清单**)。
"""

from __future__ import annotations

import logging
import zlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import polars as pl

from neckline.data.market_data import get_market_slice
from neckline.db import connection, init_schema
from neckline.report.board_pool import apply_hygiene, count_members
from neckline.report.industry_strength import load_industry_map
from neckline.report.sectors import load_index_names, load_member_map

logger = logging.getLogger(__name__)

TABLE = "limit_cluster_daily"

# 少于 2 只不构成"共振"(工程不变量,非策略参数——事实表口径,见模块 docstring)。
MIN_CLUSTER_SIZE = 2

SAME_DAY = "same_day"
CONSECUTIVE = "consecutive"

_COLUMNS = (
    "trade_date, cluster_key, ts_code, cluster_kind, cluster_size, "
    "consecutive_days, anchor_industry, anchor_concept, computed_at"
)

_UPSERT_SQL = (
    f"INSERT OR REPLACE INTO {TABLE} ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?)"
)


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_cluster_key(trade_date_s: str, cluster_kind: str, anchor_type: str, anchor_value: str) -> str:
    """`crc32(trade_date|cluster_kind:anchor_type:anchor_value)` 十六进制串——
    跨进程 / 跨天可复现(§五铁律),`seeds.py`/`corr.py`/`leader.py` 与单测共用
    这一个实现,不各自拼字符串。"""
    raw = f"{trade_date_s}|{cluster_kind}:{anchor_type}:{anchor_value}".encode("utf-8")
    return format(zlib.crc32(raw), "08x")


def concept_membership_map(parquet_dir: Optional[Path] = None) -> Dict[str, List[str]]:
    """`ts_code -> [index_code, ...]`,已过板块池卫生线(`board_pool.apply_hygiene`)
    剔除资格 / 宽基成分类标签(融资融券 / 沪深股通等——见该模块 docstring,成分动辄
    上千只,当聚类锚点用等于"全市场都在同一簇")。**单一实现**,`cluster.py` /
    `corr.py` / `seeds.py` 三处概念聚类候选一律调用本函数,不各自重复
    `load_member_map` + `apply_hygiene` 的组合。"""
    member_map = load_member_map(parquet_dir=parquet_dir)   # con_code -> [index_code,...]
    if not member_map:
        return {}
    index_names = load_index_names(parquet_dir=parquet_dir)
    hygiene = apply_hygiene(index_names, count_members(member_map))
    out: Dict[str, List[str]] = {}
    for code, concepts in member_map.items():
        kept = [c for c in concepts if c in hygiene.kept]
        if kept:
            out[code] = kept
    return out


_MEMBER_SCHEMA: Dict[str, pl.DataType] = {
    "cluster_key": pl.String,
    "ts_code": pl.String,
    "cluster_kind": pl.String,
    "cluster_size": pl.Int64,
    "consecutive_days": pl.Int64,
    "anchor_industry": pl.String,
    "anchor_concept": pl.String,
}

_ROW_SCHEMA: Dict[str, pl.DataType] = {
    "trade_date": pl.String,
    **_MEMBER_SCHEMA,
    "computed_at": pl.String,
}


def _empty_cluster_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=_MEMBER_SCHEMA)


def cluster_members_by_anchor(
    codes_with_days: Sequence[Tuple[str, int]],
    industry_of: Dict[str, str],
    concept_of: Dict[str, List[str]],
    trade_date_s: str,
    cluster_kind: str,
    *,
    min_size: int = MIN_CLUSTER_SIZE,
) -> pl.DataFrame:
    """通用聚类原语:给一批 `(ts_code, consecutive_days)`,按其行业(至多 1 个)与
    概念成分(可多个,已过卫生线)分组,组内成员数 `>= min_size` 才产出簇。

    **不仅供本模块的涨停簇使用**——`seeds.py` 的异动簇(按 `daily_basic.
    volume_ratio` 异动的股票分组)复用同一个函数,聚类逻辑单一源,`cluster_kind`
    由调用方传入区分语义(不在本函数内做涨停特定假设)。**本函数不限制
    `cluster_kind` 取值必须是 `same_day`/`consecutive` 之一**(那是
    `limit_cluster_daily` 这张**持久化表**自己的语义约束,由
    `compute_limit_clusters_for_day` 只用这两个常量调用来保证;`seeds.py` 传入
    的 `"anomaly"` 等值只用于当天的种子生成,从不落这张表,不受此约束)——只做
    最基本的"非空字符串"检查,防止调用方传空值导致 cluster_key 里出现无意义的
    `"|:industry:"` 片段。

    返回列:`cluster_key/ts_code/cluster_kind/cluster_size/consecutive_days/
    anchor_industry/anchor_concept`(不含 `trade_date`/`computed_at`,由调用方
    落表前再贴上——纯函数,不做 I/O、不猜 trade_date 格式)。
    """
    if not cluster_kind:
        raise ValueError("cluster_kind 不能为空")
    rows: List[Tuple[str, str, str, int]] = []   # (ts_code, anchor_type, anchor_value, days)
    for code, days in codes_with_days:
        ind = industry_of.get(code)
        if ind:
            rows.append((code, "industry", ind, days))
        for concept in concept_of.get(code, []):
            rows.append((code, "concept", concept, days))
    if not rows:
        return _empty_cluster_frame()

    membership = pl.DataFrame(
        rows, schema=["ts_code", "anchor_type", "anchor_value", "consecutive_days"], orient="row"
    )
    sizes = (
        membership.group_by(["anchor_type", "anchor_value"])
        .agg(pl.col("ts_code").n_unique().alias("cluster_size"))
        .filter(pl.col("cluster_size") >= min_size)
    )
    if sizes.is_empty():
        return _empty_cluster_frame()

    joined = membership.join(sizes, on=["anchor_type", "anchor_value"], how="inner")
    # cluster_key 逐组算(组数通常几十个量级,不值得为此发明 polars UDF 哈希)。
    combos = joined.select(["anchor_type", "anchor_value"]).unique().iter_rows()
    key_of = {
        (t, v): make_cluster_key(trade_date_s, cluster_kind, t, v) for t, v in combos
    }
    cluster_keys = [key_of[(t, v)] for t, v in joined.select(["anchor_type", "anchor_value"]).iter_rows()]
    joined = joined.with_columns(pl.Series("cluster_key", cluster_keys))
    joined = joined.with_columns(
        pl.when(pl.col("anchor_type") == "industry").then(pl.col("anchor_value")).otherwise(None).alias("anchor_industry"),
        pl.when(pl.col("anchor_type") == "concept").then(pl.col("anchor_value")).otherwise(None).alias("anchor_concept"),
        pl.lit(cluster_kind).alias("cluster_kind"),
    )
    return joined.select(
        ["cluster_key", "ts_code", "cluster_kind", "cluster_size", "consecutive_days", "anchor_industry", "anchor_concept"]
    )


def compute_limit_clusters_for_day(
    trade_date: date,
    limit_today: pl.DataFrame,
    industry_of: Dict[str, str],
    concept_of: Dict[str, List[str]],
) -> pl.DataFrame:
    """纯函数(无 I/O):当日 `limit_derived` 命中行 → 两种 `cluster_kind` 的簇成员表
    (已贴 `trade_date`/`computed_at`,可直接落表)。`limit_today` 需含
    `ts_code`/`is_limit_up`/`consec_limit_up_days`。"""
    if limit_today.is_empty():
        return _empty_cluster_frame().with_columns(
            pl.lit(_d(trade_date)).alias("trade_date"), pl.lit(_now()).alias("computed_at")
        )
    up = limit_today.filter(pl.col("is_limit_up"))
    if up.is_empty():
        return _empty_cluster_frame().with_columns(
            pl.lit(_d(trade_date)).alias("trade_date"), pl.lit(_now()).alias("computed_at")
        )
    day_s = _d(trade_date)
    same_day_pool = list(zip(up["ts_code"].to_list(), up["consec_limit_up_days"].to_list()))
    consecutive_pool = [
        (c, d) for c, d in same_day_pool if d is not None and d >= 2
    ]

    same_day = cluster_members_by_anchor(same_day_pool, industry_of, concept_of, day_s, SAME_DAY)
    consecutive = cluster_members_by_anchor(consecutive_pool, industry_of, concept_of, day_s, CONSECUTIVE)
    out = pl.concat([same_day, consecutive], how="diagonal_relaxed") if not (same_day.is_empty() and consecutive.is_empty()) else _empty_cluster_frame()
    return out.with_columns(pl.lit(day_s).alias("trade_date"), pl.lit(_now()).alias("computed_at"))


def refresh_limit_clusters(
    days: Iterable[date],
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """批算 + upsert 落表(日更增量与历史 bootstrap 共用同一入口——两张事实表都不
    存在跨日递推状态,"批量处理 N 天"与"逐天调用 N 次"是同一份代码路径,天然满足
    「三路等价」里的"全量算 ≡ 逐日递推"这一半;第三路"落表读回"由 `load_limit_clusters`
    验证。**每天只读当日一个 `limit_derived` 分区**(P0-23:不整表 scan)。"""
    init_schema(db_path)
    industry_of = load_industry_map(db_path)
    concept_of = concept_membership_map(parquet_dir)
    stats = {"days": 0, "rows": 0, "same_day_clusters": 0, "consecutive_clusters": 0}
    for d in sorted(set(days)):
        limit_today = get_market_slice(d, table="limit_derived", parquet_dir=parquet_dir)
        frame = compute_limit_clusters_for_day(d, limit_today, industry_of, concept_of)
        stats["days"] += 1
        if frame.is_empty():
            continue
        payload = [
            (
                r["trade_date"], r["cluster_key"], r["ts_code"], r["cluster_kind"],
                int(r["cluster_size"]), int(r["consecutive_days"]),
                r["anchor_industry"], r["anchor_concept"], r["computed_at"],
            )
            for r in frame.iter_rows(named=True)
        ]
        with connection(db_path) as conn:
            conn.executemany(_UPSERT_SQL, payload)
        stats["rows"] += len(payload)
        stats["same_day_clusters"] += frame.filter(pl.col("cluster_kind") == SAME_DAY)["cluster_key"].n_unique()
        stats["consecutive_clusters"] += frame.filter(pl.col("cluster_kind") == CONSECUTIVE)["cluster_key"].n_unique()
    return stats


def load_limit_clusters(trade_date: date, *, db_path: Optional[Path] = None) -> pl.DataFrame:
    """在线唯一读入口:给定交易日的全部簇成员行(空 = 当日无簇,合法结果,不现算)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM {TABLE} WHERE trade_date=?", (_d(trade_date),)
        ).fetchall()
    if not rows:
        return pl.DataFrame(schema=_ROW_SCHEMA)
    return pl.DataFrame(rows, schema=_ROW_SCHEMA, orient="row")


__all__ = [
    "TABLE",
    "MIN_CLUSTER_SIZE",
    "SAME_DAY",
    "CONSECUTIVE",
    "make_cluster_key",
    "concept_membership_map",
    "cluster_members_by_anchor",
    "compute_limit_clusters_for_day",
    "refresh_limit_clusters",
    "load_limit_clusters",
]
