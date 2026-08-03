"""退潮红色刹车「主线板块跳水」的**机械样本派生**(PROJECT_PLAN §五 V2-⑧-F 立、
**V2-⑧-G 定型**,2026-08-03 planner 两次裁定,V2 review 判定线 🟡-4 收口)。

**这个模块存在的唯一理由**:`hot_sector_avg_chg ≤ sector_dive` 是红色刹车三路触发
之一,红色 = **全天禁开新仓 + 立即级推送 = 纪律层动作**。V1 该样本 =「关注池里命中
今日热门板块标签的**候选**」(候选机械生成,LLM 无权改去留);V2-⑬-1 换成了 **T1/T2
篮子成员**,而篮子成员是 ⑤ 里 LLM 在白名单内**挑出来的** —— 于是一个纪律触发器的
样本组成被 LLM 塑形了。

**为什么"阈值一字未动"救不了这条**(§2.8-C-2(b) 的适用边界,2026-08-03 收紧):
2(b) 的豁免("盯谁可以由篮子决定")能成立,靠的是「**每只票的判定阈值仍只来自章程与
机械 spec**」—— 对**逐票判定**,换样本不改判据。但这里是**聚合量**,**样本组成本身
就是判据的输入**:换一批票平均,结论直接变。自查判据一句话:「**换一批成员,这个数
会不会变?**」会变 = 样本即判据 = 不得沾 LLM。

**偏差是单向的,不是中性噪声**:LLM 挑成员奔着**龙头 / 中军**去,而**领涨股在跳水日
恰恰是最抗跌的一批** → 样本系统性偏向强者 → 均值跌幅被**低估** → **红色刹车更不容易
响**。偏差恰好朝着"保护失效"的方向,故按「从严」办。

—— 样本定义(⑧-G 定死,**取代 ⑧-F 的「∩ 关注池」表述**)——————————————————
**当日 ④ 扫描层机械产出的「热点行业 / 暴起概念」每颗种子,按 `crc32(ts_code)` 升序
取前 `MAINLINE_SAMPLE_PER_SEED`(=4)只,并入关注池,作为本样本的专属来源。**

1. **不经篮子**:直接调 `scan.seeds.generate_seeds(D0)` 取 `hot_industry` /
   `surging_concept` 两类种子的 `member_codes`(未经二次筛选的原始成分:行业全部
   成员 / 概念全部成分股)。⛔ **不取「篮子所声明种子的原始成分」** —— 哪些种子被
   拿去建篮由 LLM 决定,那只是把塑形**上移一层**。
   种子生成规则读**现役包**、阈值是包参数、成分来自 `stock_basic.industry` /
   `ths_member`,**全链路机械**。
2. **⚠ 为什么不再是 ⑧-F 的「种子成分 ∩ 关注池」(⑧-G-A 定性)**:§2.4 里这条叫
   「**主线板块**跳水」,语义主语是**板块本身**,不是"我恰好盯着的那几只票";而关注池
   的存在理由完全是另一回事(**免费实时源限流下只 poll 得起 ~200 只**)。拿一个
   **测量市场状态的量** ∩ 一个**受轮询预算约束的集合**,是实现副产品不是设计 ——
   而且这个副产品**有方向**:池的机械成分几乎全是昨日涨停股 = 全市场动量筛得最狠
   = **最抗跌的一群** → 这个测量在**最该响的日子最不容易响**(⑧-F 四天对拍实测:
   07-24 全市场中位 −2.86%,∩池样本读数 −0.18%,主线板块本身 −2.98%,**差 2.8pp**)。
   `∩ 池` 又不能简单去掉(没报价就算不出收益率)→ **正解是在池里预留一块专门用来测
   主线的、机械且有代表性的样本**,即本模块的配额切片。
3. **⚠ 采样键是 `zlib.crc32(ts_code)` 升序,⛔ 不是 `ts_code` 本身升序**(⑧-G-B,
   这是对施工期候选方案的一处修正,理由要紧):`ts_code` 升序在一颗**行业**种子内部
   会系统性偏向 `000xxx`(深主板)/ `300xxx`(创业板),把 `600xxx`(沪主板)/
   `688xxx`(科创板)排到后面 —— 等于**按板块给样本排序**,而板块与**涨跌停幅度
   (10% vs 20%)、波动率**直接相关 → 样本的波动率画像被系统性扭曲 = 用一把刻度不匀
   的尺,**正是 ⑧-F 花一整块要清除的那类偏差,不许在采样器里请回来**。`crc32` 与
   板块 / 市值 / 上市年份**无关**,且跨进程跨天可复现(项目 CLAUDE.md 明文:要复现
   的分组一律 `crc32`,禁内置 `hash()`)。
4. **K 是精度旋钮,不是策略参数**(⑧-G-B):配合下面第 5 条的 per-seed 估计量,K 只
   影响**每条主线内部**的抽样噪声,**不影响主线之间的权重**。⛔ **不许通过调 K 去凑
   触发频率 —— 那等于偷偷改阈值**;K 只按"样本量够不够稳"来定。
5. **估计量 = 每条主线一票**(⑧-G-C,`estimate()`):先算每颗种子切片内的均值,再对
   种子取均值。⛔ **不是把所有票混在一起平均** —— 名字就叫「主线**板块**跳水」,一颗
   200 成员的种子与一颗 8 成员的种子各代表**一条主线**,不该因成员多就占更大权重。
   pooled(混池)口径仍逐拍落进留痕**只作审计对照**,不进判定。
6. **最小样本量 `MIN_MAINLINE_SAMPLE`**(⑧-G-E):**直接 import 引用**
   `report/industry_strength.py::_MIN_MEMBERS`(=5),⛔ 不抄字面量(照 ⑤-c
   `MIN_LIFT_SAMPLE_SIZE` 同源引用体例)。同一类统计量(横截面均值)、同一个理由
   (样本太小则估计量本身无意义):n=3 时横截面收益率标准误约 2pp,拿它判 −3% 阈值
   **接近抛硬币**,而误触发的代价是**整天禁开新仓**。门槛本身作用在
   `retreat.evaluate_retreat` 的**准入判断**上(阈值比较一字未动)。
7. **样本不足 → 不触发该路 + 如实披露**(`unavailable_reason` 落留痕),⛔ 不拿
   LLM 成员补足。这里的「保守方向 = 不触发」与项目别处「宁可多提醒」**刻意相反**,
   理由三条:① 退潮刹车**另有两路触发**兜底,不是唯一防线;② 误触发的代价是
   「整天不能开新仓」,对短线账户是实打实的机会成本,且会训练用户**忽略刹车**
   (狼来了比不响更危险);③ 与项目一贯「缺数据不判、不猜」一致。
8. **留痕**:`MainlineSample.payload()` 给出 codes + 逐颗种子切片 + 样本量 + 种子
   计数 + 包版本 + 是否被池配额压过,落 `retreat_metrics.hot_sector_sample_json`,
   让「样本没被 LLM 塑形」**事后可审计**,而不是靠读一遍代码来相信。

—— 与关注池的关系(⑧-G-D,配额在 `universe.py`)——————————————————————
本模块只负责**派生**(哪些票该进样本);**池位怎么分**归 `sentinel/universe.py`
(`MAINLINE_SLICE_QUOTA_FLOOR` / `PREV_LIMIT_UP_QUOTA_FLOOR`)。池位不够时调用方用
`MainlineSample.restrict()` 压缩,压缩走**逐颗种子轮转**(每颗种子先各留 1 只,再
各留第 2 只……)—— 这样"挤"掉的是**每条主线的精度**,而不是**整颗种子**;整颗掉队会
直接改掉 per-seed 估计量的权重构成,那是比噪声大得多的失真。

—— 当日冻结(工程决定)——————————————————————————————————————————————
种子集按 `(db_path, D0)` 在**进程内缓存**:①`generate_seeds` 每次要读 `daily_basic`
当日切片 + `ths_daily` + 几张预计算表(实测 0.1~0.2s),盘中 60s 一拍逐拍重算是浪费;
②更要紧的是**语义**:一个纪律触发器的样本不该因为盘中换了策略包而中途改口径 ——
当日**首次成功**派生即冻结。⚠ **失败不缓存**(瞬时故障 / 尚无现役包)—— 下一拍自愈
重试,免得一次读表抖动把这条路径钉死到收盘。进程重启后会重算一次(如实登记的代价,
不做跨进程持久化)。
"""

from __future__ import annotations

import logging
import zlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

# ⑧-G-E:**同源 import,不抄字面量**(⑤-c `MIN_LIFT_SAMPLE_SIZE` 同一体例)——同一类
# 统计量、同一个理由,两处漂移会让"多小算太小"在项目里出现两种答案。
from neckline.report.industry_strength import _MIN_MEMBERS as _INDUSTRY_STRENGTH_MIN_MEMBERS

logger = logging.getLogger(__name__)

# —— 每颗种子取几只(⑧-G-B 定 K=4)——————————————————————————————————————
# **引擎常量,不进包**:K 是**精度旋钮不是策略参数** —— 配合 per-seed 估计量,它只
# 影响每条主线内部的抽样噪声,**不影响主线之间的权重**。⛔ **不许通过调 K 去凑触发
# 频率(那等于偷偷改阈值)**,K 只按"样本量够不够稳"来定;要动 `sector_dive` 阈值走
# 用户拍板(§七 P3-37)。实测(2026-08-03,真实 parquet):K=4 时切片 40~100 只/日,
# K=6 时 58~148 只/日,两者都够稳,K=4 给另一路触发器的样本(昨日涨停)留出更多池位。
MAINLINE_SAMPLE_PER_SEED = 4

# —— 最小样本量(⑧-G-E)————————————————————————————————————————————————
# 值 = `industry_strength._MIN_MEMBERS`(5)。⚠ 正因为是**引用**而不是字面量,它天然
# 免疫"两处各写一个 5 然后漂移"这个坑(同 `selection/aggregate.py::MIN_LIFT_SAMPLE_SIZE`
# 的登记原文)。低于此值:`evaluate_retreat` 的主线跳水一路**不判**(不是判"没跌")。
MIN_MAINLINE_SAMPLE = _INDUSTRY_STRENGTH_MIN_MEMBERS

# —— 样本来源标签(留痕用;⑧-G 起**只有配额切片这一条机械路**,登记在这里就是为了
#    让审计一眼看出没有 "basket_member" 这一档)——————————————————————————
SOURCE_MAINLINE_SLICE = "mainline_slice"

# —— 派生不出样本时的原因码(如实披露,不静默当成"板块健康")——————————————
REASON_NO_ACTIVE_PACK = "no_active_pack"            # 无现役选股包 → ④ 当日不产出种子
REASON_SEED_FAILED = "seed_generation_failed"       # 种子生成抛异常(读表/读 parquet 失败)
REASON_NO_MAINLINE_SEEDS = "no_mainline_seeds"      # 有包,但当日无热点行业 / 暴起概念种子
REASON_NO_POOL_QUOTA = "no_pool_quota"              # 有切片,但关注池一个位都腾不出来

_REASON_TEXT: Dict[str, str] = {
    REASON_NO_ACTIVE_PACK: "无现役选股包,当日无机械种子,主线跳水一路不判",
    REASON_SEED_FAILED: "机械种子生成失败,主线跳水一路不判",
    REASON_NO_MAINLINE_SEEDS: "当日无热点行业 / 暴起概念种子,主线跳水一路不判",
    REASON_NO_POOL_QUOTA: "关注池无剩余池位容纳主线切片,主线跳水一路不判",
}


def reason_text(reason: Optional[str]) -> Optional[str]:
    """原因码 → 人读文案(**单一源**,不由看板/推送各自拍文案)。"""
    return None if reason is None else _REASON_TEXT.get(reason, reason)


def crc_rank(ts_code: str) -> Tuple[int, str]:
    """采样键(⑧-G-B):`crc32(ts_code)` 升序,同值再按代码升序兜底(crc32 碰撞极少,
    但排序必须**全序**才逐位可复现)。⛔ 不用 `ts_code` 本身升序 —— 那是按板块排序。"""
    return (zlib.crc32(ts_code.encode("utf-8")), ts_code)


def seed_slice_codes(member_codes: Tuple[str, ...], k: int = MAINLINE_SAMPLE_PER_SEED) -> Tuple[str, ...]:
    """一颗种子的切片:去重后按 `crc_rank` 升序取前 `k` 只(**纯函数,确定性**)。"""
    return tuple(sorted({c for c in member_codes if c}, key=crc_rank)[:k])


@dataclass(frozen=True)
class MainlineSeedSlice:
    """一颗主线种子在样本里的那一片(per-seed 估计量的**一票**)。"""

    seed_key: str
    seed_kind: str
    label: str
    member_total: int               # 该颗种子的原始成分数(切片前)
    codes: Tuple[str, ...] = ()     # crc32 升序取前 K(可能被池配额压短)

    def payload(self) -> Dict[str, Any]:
        return {
            "seed_key": self.seed_key,
            "seed_kind": self.seed_kind,
            "label": self.label,
            "member_total": self.member_total,
            "codes": list(self.codes),
        }


@dataclass(frozen=True)
class MainlineSample:
    """一次派生的结果。`codes` 为空 = 本拍无样本(`unavailable_reason` 说明为什么)。"""

    slices: Tuple[MainlineSeedSlice, ...] = ()
    seed_counts: Dict[str, int] = field(default_factory=dict)   # 种子类型 -> 颗数
    seed_member_total: int = 0                                  # 两类种子去重后的原始成分总数
    pack_version: Optional[str] = None
    unavailable_reason: Optional[str] = None
    restricted_from: Optional[int] = None                       # 被池配额压过 → 压缩前的样本量

    @property
    def codes(self) -> Tuple[str, ...]:
        """全部切片码去重升序(**次序只由代码排序决定**,不吃种子行序)。"""
        return tuple(sorted({c for s in self.slices for c in s.codes}))

    @property
    def size(self) -> int:
        return len(self.codes)

    @property
    def seed_count(self) -> int:
        return len(self.slices)

    def restrict(self, allowance: int) -> "MainlineSample":
        """把样本压缩到最多 `allowance` 只(⑧-G-D 池配额)。

        ⚠ **只看额度,不看"谁已经在池里了"** —— 一旦让"已在池里的码不占额度",LLM 多
        挑一个恰好也在切片里的成员就能让样本多出一只(⑧-F 登记的残留耦合 ②b 换个
        面目回来)。样本因此是**纯机械输入的函数**。⚠ 这与「排除的是 LLM 这条路、
        不是 LLM 碰过的票」(⑧-G-G 第 1 条)不矛盾:一只票被 `crc32` 选中就进样本,
        **与它是不是篮子成员无关**。

        压缩规则 = **逐颗种子轮转**:先给每颗种子留第 1 只,再第 2 只……直到额度用完。
        ⛔ 不按种子顺序整颗整颗地砍 —— 那会直接改掉 per-seed 估计量的权重构成
        (少一条主线 = 少一票),比"每条主线少抽几只"失真大得多。
        """
        if allowance >= self.size:
            return self
        allowance = max(0, allowance)
        kept: set = set()
        budget = allowance
        depth = max((len(s.codes) for s in self.slices), default=0)
        for rank in range(depth):                       # 轮转:第 rank 只,逐颗种子过一遍
            for s in self.slices:
                if rank >= len(s.codes):
                    continue
                code = s.codes[rank]
                if code in kept:
                    continue
                if budget <= 0:
                    break
                kept.add(code)
                budget -= 1
            if budget <= 0:
                break
        slices = tuple(
            MainlineSeedSlice(
                seed_key=s.seed_key, seed_kind=s.seed_kind, label=s.label,
                member_total=s.member_total,
                codes=tuple(c for c in s.codes if c in kept),
            )
            for s in self.slices
        )
        slices = tuple(s for s in slices if s.codes)
        reason = self.unavailable_reason
        if not slices:
            reason = reason or REASON_NO_POOL_QUOTA
        return MainlineSample(
            slices=slices, seed_counts=dict(self.seed_counts),
            seed_member_total=self.seed_member_total, pack_version=self.pack_version,
            unavailable_reason=reason,
            restricted_from=self.size,
        )

    def payload(self) -> Dict[str, Any]:
        """落 `retreat_metrics.hot_sector_sample_json` 的留痕(⑧-F 立、⑧-G 扩)。
        `codes` 全量落 —— 关注池上限 200,切片通常几十只,不做截断(截断会让
        「事后可审计」这件事打折)。"""
        codes = self.codes
        return {
            "codes": list(codes),
            # 逐码来源标签:⑧-G 起只可能是配额切片这一条机械路(形状与 ⑧-F 兼容)。
            "sources": {c: SOURCE_MAINLINE_SLICE for c in codes},
            "size": len(codes),
            "seed_counts": dict(self.seed_counts),
            "seed_member_total": self.seed_member_total,
            "seed_slices": [s.payload() for s in self.slices],
            "per_seed_k": MAINLINE_SAMPLE_PER_SEED,
            "min_sample": MIN_MAINLINE_SAMPLE,
            "restricted_from": self.restricted_from,
            "pack_version": self.pack_version,
            "unavailable_reason": self.unavailable_reason,
            "unavailable_text": reason_text(self.unavailable_reason),
            # 审计锚:样本**只可能**来自这一条机械路;出现别的标签就是有人接了 LLM。
            "allowed_sources": [SOURCE_MAINLINE_SLICE],
        }


@dataclass(frozen=True)
class MainlineEstimate:
    """一拍的读数。**判定用 `per_seed_avg`**(⑧-G-C);`pooled_avg` 只作审计对照。"""

    per_seed_avg: Optional[float] = None
    pooled_avg: Optional[float] = None
    quoted: int = 0                                     # 有报价、真正参与均值的只数
    seeds_with_data: int = 0                            # 至少有一只有报价的种子数
    per_seed_avgs: Dict[str, float] = field(default_factory=dict)   # seed_key -> 该主线均值

    def payload(self) -> Dict[str, Any]:
        return {
            "quoted": self.quoted,
            "seeds_with_data": self.seeds_with_data,
            "per_seed_avg": self.per_seed_avg,
            # ⚠ 审计对照量,**不进判定**:两个口径讲不同话时(⑧-F/⑧-G 对拍实测最大
            # 差 1.06pp)要能一眼看出来,而不是事后重算。
            "pooled_avg": self.pooled_avg,
            "per_seed_avgs": dict(self.per_seed_avgs),
        }


def estimate(sample: MainlineSample, returns: Mapping[str, float]) -> MainlineEstimate:
    """按 ⑧-G-C「每条主线一票」算读数:先种子内均值,再种子间均值。

    `returns` = 已经拉到的行情算出的 `ts_code -> 盘中涨跌幅`(缺报价的码不出现)。
    ⛔ 本函数不做任何阈值判断,只回答"这一拍读数是多少"。
    """
    per_seed: Dict[str, float] = {}
    quoted: set = set()
    for s in sample.slices:
        vals = [returns[c] for c in s.codes if c in returns]
        if not vals:
            continue
        per_seed[s.seed_key] = sum(vals) / len(vals)
        quoted.update(c for c in s.codes if c in returns)
    if not per_seed:
        return MainlineEstimate()
    pooled_vals = [returns[c] for c in sorted(quoted)]
    return MainlineEstimate(
        per_seed_avg=sum(per_seed.values()) / len(per_seed),
        pooled_avg=sum(pooled_vals) / len(pooled_vals),
        quoted=len(quoted),
        seeds_with_data=len(per_seed),
        per_seed_avgs=per_seed,
    )


# ══════════════════════════════════════════════════════════════════════════
# 种子切片(当日冻结的进程内缓存)
# ══════════════════════════════════════════════════════════════════════════

# key: (str(db_path), 'YYYYMMDD') -> (slices, seed_counts, member_total, pack_version, reason)
_SEED_CACHE: Dict[
    Tuple[str, str],
    Tuple[Tuple[MainlineSeedSlice, ...], Dict[str, int], int, Optional[str], Optional[str]],
] = {}


def reset_seed_cache() -> None:
    """清空当日冻结缓存(单测与"重跑一次派生"用;生产不调)。"""
    _SEED_CACHE.clear()


def load_mainline_seed_slices(
    report_date: date, *, db_path: Optional[Path] = None, parquet_dir: Optional[Path] = None,
) -> Tuple[Tuple[MainlineSeedSlice, ...], Dict[str, int], int, Optional[str], Optional[str]]:
    """D0 的「热点行业 + 暴起概念」两类种子,每颗切出前 K 只(确定性)。

    返回 `(slices, seed_counts, member_total, pack_version, unavailable_reason)`。
    ⛔ 只取这两类 —— `limit_cluster` / `anomaly_cluster` 是「涨停簇 / 异动簇」,不是
    「板块」,拿它们凑样本会把"主线板块跳水"这个词的意思改掉(plan 点名的就是这两类)。
    """
    key = (str(db_path), report_date.strftime("%Y%m%d"))
    hit = _SEED_CACHE.get(key)
    if hit is not None:
        return hit

    from neckline.scan import seeds as seeds_mod

    slices: Tuple[MainlineSeedSlice, ...] = ()
    counts: Dict[str, int] = {}
    total = 0
    pack_version: Optional[str] = None
    reason: Optional[str] = None
    try:
        seed_set = seeds_mod.generate_seeds(report_date, db_path=db_path, parquet_dir=parquet_dir)
    except Exception:  # noqa: BLE001 —— 种子算不出不该掀翻整拍哨兵,如实降级为"不判该路"
        logger.warning("[mainline] %s 机械种子生成失败,主线跳水一路本日不判",
                       report_date, exc_info=True)
        seed_set = None
        reason = REASON_SEED_FAILED

    if seed_set is None:
        reason = reason or REASON_NO_ACTIVE_PACK
    else:
        pack_version = seed_set.pack_version
        picked = tuple(seed_set.hot_industry) + tuple(seed_set.surging_concept)
        counts = {
            seeds_mod.HOT_INDUSTRY: len(seed_set.hot_industry),
            seeds_mod.SURGING_CONCEPT: len(seed_set.surging_concept),
        }
        total = len({c for s in picked for c in s.member_codes if c})
        slices = tuple(
            MainlineSeedSlice(
                seed_key=s.seed_key, seed_kind=s.seed_kind, label=s.label,
                member_total=len({c for c in s.member_codes if c}),
                codes=seed_slice_codes(s.member_codes),
            )
            for s in picked
        )
        slices = tuple(s for s in slices if s.codes)    # 空成分的种子不占一票
        if not slices:
            reason = REASON_NO_MAINLINE_SEEDS

    out = (slices, counts, total, pack_version, reason)
    # ⚠ **只缓存成功的派生**:一次瞬时读表 / 读 parquet 失败不该让一条纪律路径
    # 整天失效(那是"冻结"的反面 —— 冻的应该是一份算出来的样本,不是一次故障);
    # 失败下一拍自愈重试,代价是一次 0.1~0.2s 的重算。「无现役包」同理:包是盘中
    # 可能被激活的配置态,缓存它等于把"今天没包"钉死到收盘。
    if reason is None:
        _SEED_CACHE[key] = out
    return out


def derive_mainline_sample(
    report_date: date,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> MainlineSample:
    """本拍的「主线板块跳水」样本(纯派生,不拉行情、不判触发、**不看关注池**)。

    ⛔ 刻意**不接**篮子成员参数 —— 签名里没有那个口子,后人想接也得先改签名、先读
    模块头。⚠ ⑧-G 起也**不接**持仓 / 昨日涨停:样本 = 机械配额切片本身,与"我恰好
    盯着谁"解耦(⑧-G-A);池位分配是 `universe.py` 的事,不够时调用方 `restrict()`。

    ⚠ 本函数**不做任何阈值判断**。触发与否仍由 `retreat.evaluate_retreat` 按
    `sector_dive` 判(阈值一字未动)。
    """
    slices, counts, total, pack_version, reason = load_mainline_seed_slices(
        report_date, db_path=db_path, parquet_dir=parquet_dir)
    return MainlineSample(
        slices=slices, seed_counts=counts, seed_member_total=total,
        pack_version=pack_version, unavailable_reason=reason,
    )


__all__ = [
    "MAINLINE_SAMPLE_PER_SEED",
    "MIN_MAINLINE_SAMPLE",
    "SOURCE_MAINLINE_SLICE",
    "REASON_NO_ACTIVE_PACK",
    "REASON_SEED_FAILED",
    "REASON_NO_MAINLINE_SEEDS",
    "REASON_NO_POOL_QUOTA",
    "MainlineSeedSlice",
    "MainlineSample",
    "MainlineEstimate",
    "crc_rank",
    "seed_slice_codes",
    "estimate",
    "reason_text",
    "reset_seed_cache",
    "load_mainline_seed_slices",
    "derive_mainline_sample",
]
