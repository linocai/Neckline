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

**四类种子的输出顺序必须确定性(2026-08-02 定向快修,⑨ 完工时实证发现的
块外真洞)**:`⑤` 的 `_select_seeds()` 只取 `SeedSet.all_seeds()` 前
`MAX_SEEDS_AGGREGATED`(=20)颗,前提是"每类内部各自有序"(该函数 docstring
原文)。涨停簇 / 异动簇最初实现按 `frame.group_by(["cluster_key"])` 迭代
直接 append,而 **polars `group_by` 官方不保证顺序**,其上游
`load_limit_clusters`/`cluster_members_by_anchor` 的 SQL `SELECT` 也未加
`ORDER BY`——`maintain_order=True` 治标不治本(维持的是一个本就不确定的行序,
"别用行序"这条不只针对 `group_by`,也针对上游查询)。实测同一 D0、同一库
`generate_seeds()` 同进程内连调三次,第 3 颗起 `seed_key` 就不一样,连带
哪 20 颗种子进聚合、聚出哪些篮子全部随机。**修法**:四类种子各自的输出列表在
返回前一律经 `_sort_seeds()` 排定,不依赖任何上游行序——热点行业(SQL `ORDER BY
industry_rank`)与暴起概念(parquet 读回顺序)目前看似已经稳定,但那是"恰好
如此"而非"契约保证",四类**全部**过一遍这一道收口,不只收被点名的两类。

**排序键 = 「语义主键 → `seed_key`」两级序(2026-08-04,判定线审计 🔵-2)**:
2026-08-02 那版一律按 `seed_key`(crc32)单键升序——确定性达成了,但 **截断变得
不可解释**:`⑤` 只取前 20 颗,某类内部超额时进聚合的是"crc32 恰好小"的而不是
"最强的"(热点行业因此丢掉了 `industry_rank` 语义序)。现改为先按该类**自己的
强弱主键**排(热点行业 = `industry_rank` 升序;暴起概念 = 当日涨幅降序;涨停簇 /
异动簇 = `cluster_size` 降序),**再**用 `seed_key` crc32 升序打散并列。
确定性一点没减(第二级键仍是跨进程可复现的 crc32,主键相等时全序仍唯一),
截断从此可解释。⚠ **与 ⑧-G `mainline.crc_rank`(按票 `crc32(ts_code)` 采样)不是
一回事** —— 那是"从池子里等概率抽一批票"的采样键,本函数是"种子谁先谁后"的
展示/截断序,两者同为 crc32 但不同层,别互相"统一"。
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
from neckline.facts import limitmap as cluster
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

# 语义主键算不出时的排位(排在同类最后,由第二级 `seed_key` 内部定序)。
# ⛔ 不拿 0 冒充"最弱"——0 在涨幅/簇大小里都是真实取值,「没有」与「没看」分开。
_PRIMARY_MISSING = float("inf")


def _semantic_primary(seed: DriverSeed) -> float:
    """一颗种子在**它自己那一类**里的强弱主键,**统一成"越小越强"的升序量**
    (模块头「排序键 = 两级序」节)。取值全部来自 `evidence` 里已经算好的机械量,
    本函数**不新算任何判据、也不引入任何阈值**——它只决定"同类里谁先谁后",
    不决定"谁够格当种子"(那是 `PRIMITIVES` 的事)。

        hot_industry     → `industry_rank` 升序(第 1 名最强)
        surging_concept  → `pct_change` **降序**(涨得多的在前)→ 取负号归一成升序
        limit_cluster    → `cluster_size` **降序**(簇越大共振越强)→ 同上
        anomaly_cluster  → 同 `limit_cluster`

    缺主键(evidence 少这一项 / 非数)→ `_PRIMARY_MISSING`,排在同类最后。"""
    ev = seed.evidence or {}

    def _f(v: Any) -> Optional[float]:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        f = float(v)
        return f if f == f else None      # NaN 当算不出

    if seed.seed_kind == HOT_INDUSTRY:
        rank = _f(ev.get("industry_rank"))
        return rank if rank is not None else _PRIMARY_MISSING
    if seed.seed_kind == SURGING_CONCEPT:
        pct = _f(ev.get("pct_change"))
        return -pct if pct is not None else _PRIMARY_MISSING
    # 两类簇:`cluster_size` 是 evidence 里的机械量;真缺了退回成员数(同一个量的
    # 另一种数法,不是新判据),再缺才算算不出。
    size = _f(ev.get("cluster_size"))
    if size is None and seed.member_codes:
        size = float(len(seed.member_codes))
    return -size if size is not None else _PRIMARY_MISSING


def _sort_seeds(items: List[DriverSeed]) -> Tuple[DriverSeed, ...]:
    """四类种子各自落定顺序后再交给 ⑤(模块头「四类种子的输出顺序必须确定性」
    节)。**两级序**:`(语义主键升序, seed_key 升序)`——不用行序,也不假设调用方
    传入的列表已经有序。第二级的 `seed_key` 是 crc32 十六进制串(由行业名 / 概念
    代码 / 簇 anchor 等稳定业务标识派生,`cluster.make_cluster_key` 同手法,跨进程
    无随机盐),故**主键并列时全序仍唯一且可复现**;只有 `seed_key` 也完全相同
    (crc32 碰撞,概率级极小)才会退回 `sorted()` 的稳定序。

    ⚠ 2026-08-02 的旧实现 `_sort_by_seed_key()` 是本函数的单键版本,已被取代
    (确定性等价,截断语义更可解释,判定线审计 🔵-2)。"""
    return tuple(sorted(items, key=lambda s: (_semantic_primary(s), s.seed_key)))


def generate_seeds(
    trade_date: date, *, db_path: Optional[Path] = None, parquet_dir: Optional[Path] = None
) -> Optional[SeedSet]:
    """当日四类驱动种子(**无现役包 → `None`**,如实披露"今日不产出种子",不
    造一份默认包——见 `get_active_pack()` docstring 原文)。四类种子各自经
    `_sort_seeds()` 落定确定性顺序(模块头节:语义主键 → `seed_key` 两级序),
    `⑤` 截断前 `MAX_SEEDS_AGGREGATED` 颗的前提"每类内部各自有序、且序是强弱序"
    由此保证。"""
    pack = get_active_pack(db_path)
    if pack is None:
        logger.warning(
            "[scan.seeds] %s 无现役骨架线包(selection_packs 无 line_code='V' 且 "
            "is_active=1 行)—— 本日不产出任何驱动种子,不使用默认阈值。请先跑 "
            "`python scripts/activate_pack.py --file packs/K8-skeleton.json --confirm`"
            "(V2.2-① 起 get_active_pack() 只认骨架线,LEGACY 老包行不算现役)。",
            _d(trade_date),
        )
        return None
    return SeedSet(
        trade_date=_d(trade_date),
        pack_version=pack.pack_version,
        hot_industry=_sort_seeds(_hot_industry_seeds(trade_date, pack, db_path=db_path)),
        surging_concept=_sort_seeds(_surging_concept_seeds(trade_date, pack, parquet_dir=parquet_dir)),
        limit_cluster=_sort_seeds(_limit_cluster_seeds(trade_date, pack, db_path=db_path)),
        anomaly_cluster=_sort_seeds(
            _anomaly_cluster_seeds(trade_date, pack, db_path=db_path, parquet_dir=parquet_dir)
        ),
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
