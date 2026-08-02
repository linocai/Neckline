"""驱动种子生成(plan §五 V2-④)。把三张事实表(`corr_matrix_daily` /
`limit_cluster_daily` / `leader_structure_daily`)+ 两张既有预计算表
(`industry_strength_daily` / `ths_daily`)+ `daily_basic` 过滤成**四类驱动
种子**(热点行业 / 暴起概念 / 涨停簇 / 异动簇),交给 ⑤ 驱动聚合层。

**"事实"与"种子"的分野(勿混淆,见 `neckline/scan/__init__.py` 模块头)**:
事实表本身不读包配置;本模块**是**读包配置的地方——四类种子各自的资格判断
一律走 `neckline.selection.pack.get_active_pack().config["seeds"]` 对应的
原语(`hot_industry_seed`/`surging_concept_seed`/`limit_cluster_seed`/
`anomaly_cluster_seed`,见 `neckline/selection/primitives.py`),**本文件里
不出现任何阈值字面量**(§五 V2-④ 原文"禁模块级字面量"——不只是不写模块级
变量,连函数体内的裸字面量阈值也不写,全部从 `Primitive.params_schema` 的
default 或包配置取)。

**无现役包 = 当日不产出任何种子**(`get_active_pack()` docstring 原文,fail
loud 的对立面是"如实披露",不是"报错崩溃"也不是"造一份默认包糊弄过去")—
`generate_seeds()` 返回 `None`,调用方(未来的 ⑤ / `daily_update.py`)按
"今日无种子"处理,不得静默造默认阈值。

**不落表**:种子生成只读**已经是 EOD 预计算**的小表(行业强度 ~30-110 行/日、
`limit_cluster_daily` 通常几十行/日、`ths_daily` ~394 行/日、`daily_basic`
全市场一天一次 SQL/parquet 读),不构成"全市场多年历史扫描"(P0-23 管的是
后者),按需现算足够便宜,不需要额外物化一张种子表。

**成员范围的刻意留白**:每颗种子的 `member_codes` 是**未经二次筛选的原始
成分**(行业全部成员 / 概念全部成分股 / 簇内全部成员 / 异动簇内全部成员)——
"从这批候选里选哪 1-3 只、标什么角色"是 ⑤ 驱动聚合层的职责(LLM + 机械数据
联合判断,含白名单闸),本模块不做这一步筛选,避免抢占 ⑤ 的职责边界。
"""

from __future__ import annotations

import logging
import zlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import polars as pl

from neckline.data.concept_data import load_ths_daily
from neckline.data.market_data import get_market_slice
from neckline.report.board_pool import apply_hygiene, count_members
from neckline.report.industry_strength import load_industry_map
from neckline.report.industry_strength_store import load_industry_strength
from neckline.report.sectors import load_index_names, load_member_map
from neckline.scan import cluster
from neckline.selection.pack import Pack, get_active_pack
from neckline.selection.primitives import PRIMITIVES

logger = logging.getLogger(__name__)

HOT_INDUSTRY = "hot_industry"
SURGING_CONCEPT = "surging_concept"
LIMIT_CLUSTER = "limit_cluster"
ANOMALY_CLUSTER = "anomaly_cluster"

_ANOMALY_ANCHOR_KIND = "anomaly"   # 传给 `cluster.cluster_members_by_anchor` 的 cluster_kind,
                                    # 从不落 `limit_cluster_daily`(该表只收 same_day/consecutive)


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _seed_key(trade_date_s: str, seed_kind: str, anchor: str) -> str:
    """`crc32(trade_date|seed_kind:anchor)` 十六进制(跨进程可复现,§五铁律)。
    与 `cluster.make_cluster_key` 同一手法,不同命名空间(`seed_kind` 而非
    `cluster_kind`),两者不会互相冲突也不需要互相冲突——种子键与簇键是两个
    不同的 ID 空间,种子键只在 `SeedSet` 内部作为去重/展示用途,不落任何表。"""
    raw = f"{trade_date_s}|{seed_kind}:{anchor}".encode("utf-8")
    return format(zlib.crc32(raw), "08x")


@dataclass(frozen=True)
class DriverSeed:
    """一颗驱动种子:一个"够格当共同驱动"的事实(行业 / 概念 / 簇),附带候选
    成员与证据(供 ⑤ 的 LLM 输入 + 人工审计)。"""

    seed_key: str
    seed_kind: str          # hot_industry | surging_concept | limit_cluster | anomaly_cluster
    label: str               # 人读标签(行业名 / 概念名 / 簇 anchor)
    member_codes: Tuple[str, ...]
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SeedSet:
    """当日全部种子(四类分开存放,同 `baskets.driver_kind` 枚举不强行合并)。"""

    trade_date: str          # 'YYYYMMDD'
    pack_version: str
    hot_industry: Tuple[DriverSeed, ...] = ()
    surging_concept: Tuple[DriverSeed, ...] = ()
    limit_cluster: Tuple[DriverSeed, ...] = ()
    anomaly_cluster: Tuple[DriverSeed, ...] = ()

    def all_seeds(self) -> Tuple[DriverSeed, ...]:
        return self.hot_industry + self.surging_concept + self.limit_cluster + self.anomaly_cluster

    def counts(self) -> Dict[str, int]:
        return {
            HOT_INDUSTRY: len(self.hot_industry),
            SURGING_CONCEPT: len(self.surging_concept),
            LIMIT_CLUSTER: len(self.limit_cluster),
            ANOMALY_CLUSTER: len(self.anomaly_cluster),
        }


# ══════════════════════════════════════════════════════════════════════════
# 四类种子各自的生成(阈值全部来自 `pack.seeds_config(<primitive_name>)`)
# ══════════════════════════════════════════════════════════════════════════

def _hot_industry_seeds(trade_date: date, pack: Pack, *, db_path: Optional[Path] = None) -> List[DriverSeed]:
    primitive = PRIMITIVES["hot_industry_seed"]
    params = pack.seeds_config("hot_industry_seed")
    scores = load_industry_strength(trade_date, db_path=db_path)
    if not scores:
        return []
    industry_of = load_industry_map(db_path)
    members_by_industry: Dict[str, List[str]] = {}
    for code, ind in industry_of.items():
        members_by_industry.setdefault(ind, []).append(code)

    day_s = _d(trade_date)
    out: List[DriverSeed] = []
    for s in scores:
        row = {"industry_rank": s.industry_rank, "is_strength_day": s.is_strength_day}
        if not primitive.run(row, params):
            continue
        out.append(DriverSeed(
            seed_key=_seed_key(day_s, HOT_INDUSTRY, s.industry),
            seed_kind=HOT_INDUSTRY,
            label=s.industry,
            member_codes=tuple(sorted(members_by_industry.get(s.industry, []))),
            evidence={
                "industry_rank": s.industry_rank,
                "median_ret": s.median_ret,
                "member_count": s.member_count,
                "persist_days": s.persist_days,
            },
        ))
    return out


def _surging_concept_seeds(trade_date: date, pack: Pack, *, parquet_dir: Optional[Path] = None) -> List[DriverSeed]:
    primitive = PRIMITIVES["surging_concept_seed"]
    params = pack.seeds_config("surging_concept_seed")
    ths = load_ths_daily(parquet_dir)
    if ths.is_empty():
        return []
    today = ths.filter(pl.col("trade_date") == trade_date)
    if today.is_empty():
        return []

    index_names = load_index_names(parquet_dir)
    member_map = load_member_map(parquet_dir)     # con_code -> [index_code,...]
    hygiene = apply_hygiene(index_names, count_members(member_map))
    inv: Dict[str, List[str]] = {}
    for con_code, idx_codes in member_map.items():
        for idx in idx_codes:
            if idx in hygiene.kept:
                inv.setdefault(idx, []).append(con_code)

    day_s = _d(trade_date)
    out: List[DriverSeed] = []
    for r in today.iter_rows(named=True):
        idx_code = r["ts_code"]
        if idx_code not in hygiene.kept:
            continue
        row = {"pct_change": r.get("pct_change")}
        if not primitive.run(row, params):
            continue
        out.append(DriverSeed(
            seed_key=_seed_key(day_s, SURGING_CONCEPT, idx_code),
            seed_kind=SURGING_CONCEPT,
            label=index_names.get(idx_code, idx_code),
            member_codes=tuple(sorted(set(inv.get(idx_code, [])))),
            evidence={"index_code": idx_code, "pct_change": r.get("pct_change"), "close": r.get("close")},
        ))
    return out


def _limit_cluster_seeds(trade_date: date, pack: Pack, *, db_path: Optional[Path] = None) -> List[DriverSeed]:
    primitive = PRIMITIVES["limit_cluster_seed"]
    params = pack.seeds_config("limit_cluster_seed")
    clusters = cluster.load_limit_clusters(trade_date, db_path=db_path)
    if clusters.is_empty():
        return []

    out: List[DriverSeed] = []
    for (key,), sub in clusters.group_by(["cluster_key"]):
        cluster_size = int(sub["cluster_size"][0])
        # 资格判断用簇内最长连板天数(是否至少有一只成员已进入多日接力)——
        # `consecutive_days` 是成员各自的量,聚成"够不够格当种子"取 max 是本
        # 模块的编排决定,不是 `limit_cluster_daily` 表本身的语义(如实登记)。
        consecutive_days_max = int(sub["consecutive_days"].max())
        row = {"cluster_size": cluster_size, "consecutive_days": consecutive_days_max}
        if not primitive.run(row, params):
            continue
        anchor_industry = sub["anchor_industry"][0]
        anchor_concept = sub["anchor_concept"][0]
        out.append(DriverSeed(
            seed_key=key,   # 直接复用 cluster_key(当日已是稳定键,不必再套一层 crc32)
            seed_kind=LIMIT_CLUSTER,
            label=anchor_industry or anchor_concept or key,
            member_codes=tuple(sorted(sub["ts_code"].unique().to_list())),
            evidence={
                "cluster_size": cluster_size,
                "consecutive_days_max": consecutive_days_max,
                "cluster_kind": sub["cluster_kind"][0],
                "anchor_industry": anchor_industry,
                "anchor_concept": anchor_concept,
            },
        ))
    return out


def _anomaly_cluster_seeds(
    trade_date: date, pack: Pack, *, db_path: Optional[Path] = None, parquet_dir: Optional[Path] = None
) -> List[DriverSeed]:
    primitive = PRIMITIVES["anomaly_cluster_seed"]
    params = pack.seeds_config("anomaly_cluster_seed")
    min_cluster_members = int(primitive.merge_params(params)["min_cluster_members"])

    basic = get_market_slice(trade_date, table="daily_basic", parquet_dir=parquet_dir)
    if basic.is_empty():
        return []
    qualifying: List[Tuple[str, int]] = []
    for r in basic.select(["ts_code", "volume_ratio"]).iter_rows(named=True):
        if primitive.run({"volume_ratio": r["volume_ratio"]}, params):
            qualifying.append((r["ts_code"], 0))   # 第二位是聚类原语要的占位"天数",异动簇不用连板语义
    if not qualifying:
        return []

    industry_of = load_industry_map(db_path)
    concept_of = cluster.concept_membership_map(parquet_dir)
    day_s = _d(trade_date)
    frame = cluster.cluster_members_by_anchor(
        qualifying, industry_of, concept_of, day_s, _ANOMALY_ANCHOR_KIND, min_size=min_cluster_members,
    )
    if frame.is_empty():
        return []

    out: List[DriverSeed] = []
    for (key,), sub in frame.group_by(["cluster_key"]):
        anchor_industry = sub["anchor_industry"][0]
        anchor_concept = sub["anchor_concept"][0]
        out.append(DriverSeed(
            seed_key=key,
            seed_kind=ANOMALY_CLUSTER,
            label=anchor_industry or anchor_concept or key,
            member_codes=tuple(sorted(sub["ts_code"].unique().to_list())),
            evidence={
                "cluster_size": int(sub["cluster_size"][0]),
                "anchor_industry": anchor_industry,
                "anchor_concept": anchor_concept,
            },
        ))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 编排入口
# ══════════════════════════════════════════════════════════════════════════

def generate_seeds(
    trade_date: date, *, db_path: Optional[Path] = None, parquet_dir: Optional[Path] = None
) -> Optional[SeedSet]:
    """当日四类驱动种子(**无现役包 → `None`**,如实披露"今日不产出种子",不
    造一份默认包——见 `get_active_pack()` docstring 原文)。"""
    pack = get_active_pack(db_path)
    if pack is None:
        logger.warning(
            "[scan.seeds] %s 无现役策略包(selection_packs 无 is_active=1 行)—— "
            "本日不产出任何驱动种子,不使用默认阈值。请先跑 "
            "`python scripts/activate_pack.py --file packs/K4-pack.json --confirm`。",
            _d(trade_date),
        )
        return None
    return SeedSet(
        trade_date=_d(trade_date),
        pack_version=pack.pack_version,
        hot_industry=tuple(_hot_industry_seeds(trade_date, pack, db_path=db_path)),
        surging_concept=tuple(_surging_concept_seeds(trade_date, pack, parquet_dir=parquet_dir)),
        limit_cluster=tuple(_limit_cluster_seeds(trade_date, pack, db_path=db_path)),
        anomaly_cluster=tuple(_anomaly_cluster_seeds(trade_date, pack, db_path=db_path, parquet_dir=parquet_dir)),
    )


__all__ = [
    "HOT_INDUSTRY",
    "SURGING_CONCEPT",
    "LIMIT_CLUSTER",
    "ANOMALY_CLUSTER",
    "DriverSeed",
    "SeedSet",
    "generate_seeds",
]
