"""退潮红色刹车「主线板块跳水」的**机械样本派生**(PROJECT_PLAN §五 V2-⑧-F,
2026-08-03 planner 裁定,V2 review 判定线 🟡-4 收口)。

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

—— 样本定义(定死)——————————————————————————————————————————————————
**当日 ④ 扫描层机械产出的「热点行业 / 暴起概念」种子的原始成分 ∩ 关注池的机械成分。**

1. **不经篮子**:直接调 `scan.seeds.generate_seeds(D0)` 取 `hot_industry` /
   `surging_concept` 两类种子的 `member_codes`(未经二次筛选的原始成分:行业全部
   成员 / 概念全部成分股)。⛔ **不取「篮子所声明种子的原始成分」** —— 哪些种子被
   拿去建篮由 LLM 决定,那只是把塑形**上移一层**。
   种子生成规则读**现役包**、阈值是包参数、成分来自 `stock_basic.industry` /
   `ths_member`,**全链路机械**;这也是与 V1「热门板块 ∩ 关注池」语义最近的机械
   等价物。
2. **⚠「关注池的机械成分」而不是整个关注池(builder 判断,已在 ⑧-F 完工记录登记
   为与 plan 字面的一处偏差,请 planner 复核)**:plan 原文写的是「∩ 关注池」,但
   V2 的关注池**本身**含 T1/T2 篮子成员(⑧-A),而篮子成员 ⊆ 种子成分 —— 若整池
   取交,LLM 换一批成员就换一批样本,**⑧-F 验收里那条核心判据「换掉 LLM 的成员
   选择 → 样本逐位不变」当场不成立**。故本模块只认**机械进池的那两条路**:
   · `prev_limit_up` —— 昨日涨停股(`universe._load_prev_limit_up_codes`,纯机械,
     与 V1「候选=机械生成的强势票」语义最近);
   · `position` —— 用户持仓(用户自己的成交,不是 LLM 的选择;自查判据"换一批 LLM
     成员这个数会不会变"→ 不会)。
   **只靠篮子进池的码不进样本**(它仍在关注池里、仍被别的哨兵盯,只是不进这个
   聚合判据);⚠ 一只票若**同时**从机械路进池(如它本来就是昨日涨停),照进样本
   —— 排除的是"LLM 这条路",不是"LLM 碰过的票"。
3. **样本不足 → 不触发该路 + 如实披露**(`unavailable_reason` 落留痕),⛔ 不拿
   LLM 成员补足。这里的「保守方向 = 不触发」与项目别处「宁可多提醒」**刻意相反**,
   理由三条:① 退潮刹车**另有两路触发**兜底,不是唯一防线;② 误触发的代价是
   「整天不能开新仓」,对短线账户是实打实的机会成本,且会训练用户**忽略刹车**
   (狼来了比不响更危险);③ 与项目一贯「缺数据不判、不猜」一致。
   ⛔ **本模块不新增任何最小样本量阈值** —— "不足"就是**派生不出 / 交集为空**;
   `sector_dive` 阈值与 `hot_sector_sample > 0` 这条既有门槛**一字不动**(⑧-F-B)。
4. **留痕**:`MainlineSample.payload()` 给出 codes + 逐码来源标签 + 样本量 + 种子
   计数 + 包版本,落 `retreat_metrics.hot_sector_sample_json`,让「样本没被 LLM
   塑形」**事后可审计**,而不是靠读一遍代码来相信。

—— 当日冻结(工程决定)——————————————————————————————————————————————
种子集按 `(db_path, D0)` 在**进程内缓存**:①`generate_seeds` 每次要读 `daily_basic`
当日切片 + `ths_daily` + 几张预计算表(实测 0.1~0.2s),盘中 60s 一拍逐拍重算是浪费;
②更要紧的是**语义**:一个纪律触发器的样本不该因为盘中换了策略包而中途改口径 ——
当日首次派生即冻结。进程重启后会重算一次(如实登记的代价,不做跨进程持久化)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# —— 样本来源标签(留痕用;**只有机械路**,登记在这里就是为了让审计一眼看出
#    没有 "basket_member" 这一档)————————————————————————————————————————
SOURCE_PREV_LIMIT_UP = "prev_limit_up"
SOURCE_POSITION = "position"

# —— 派生不出样本时的原因码(如实披露,不静默当成"板块健康")——————————————
REASON_NO_ACTIVE_PACK = "no_active_pack"            # 无现役选股包 → ④ 当日不产出种子
REASON_SEED_FAILED = "seed_generation_failed"       # 种子生成抛异常(读表/读 parquet 失败)
REASON_NO_MAINLINE_SEEDS = "no_mainline_seeds"      # 有包,但当日无热点行业 / 暴起概念种子
REASON_NO_OVERLAP = "no_overlap"                    # 有种子,但与关注池机械成分无交集

_REASON_TEXT: Dict[str, str] = {
    REASON_NO_ACTIVE_PACK: "无现役选股包,当日无机械种子,主线跳水一路不判",
    REASON_SEED_FAILED: "机械种子生成失败,主线跳水一路不判",
    REASON_NO_MAINLINE_SEEDS: "当日无热点行业 / 暴起概念种子,主线跳水一路不判",
    REASON_NO_OVERLAP: "机械种子成分与关注池无交集,主线跳水一路不判",
}


def reason_text(reason: Optional[str]) -> Optional[str]:
    """原因码 → 人读文案(**单一源**,不由看板/推送各自拍文案)。"""
    return None if reason is None else _REASON_TEXT.get(reason, reason)


@dataclass(frozen=True)
class MainlineSample:
    """一次派生的结果。`codes` 为空 = 本拍无样本(`unavailable_reason` 说明为什么)。"""

    codes: Tuple[str, ...] = ()
    sources: Dict[str, str] = field(default_factory=dict)   # ts_code -> 来源标签
    seed_counts: Dict[str, int] = field(default_factory=dict)  # 种子类型 -> 颗数
    seed_member_total: int = 0                               # 两类种子去重后的原始成分总数
    pack_version: Optional[str] = None
    unavailable_reason: Optional[str] = None

    @property
    def size(self) -> int:
        return len(self.codes)

    def payload(self) -> Dict[str, Any]:
        """落 `retreat_metrics.hot_sector_sample_json` 的留痕(⑧-F「样本构成」)。
        `codes` 全量落 —— 关注池上限 200,交集通常几十只,不做截断(截断会让
        「事后可审计」这件事打折)。"""
        return {
            "codes": list(self.codes),
            "sources": dict(self.sources),
            "size": self.size,
            "seed_counts": dict(self.seed_counts),
            "seed_member_total": self.seed_member_total,
            "pack_version": self.pack_version,
            "unavailable_reason": self.unavailable_reason,
            "unavailable_text": reason_text(self.unavailable_reason),
            # 审计锚:样本**只可能**来自这两条机械路;出现别的标签就是有人接了 LLM。
            "allowed_sources": [SOURCE_PREV_LIMIT_UP, SOURCE_POSITION],
        }


# ══════════════════════════════════════════════════════════════════════════
# 种子成分(当日冻结的进程内缓存)
# ══════════════════════════════════════════════════════════════════════════

# key: (str(db_path), 'YYYYMMDD') -> (codes, seed_counts, member_total, pack_version, reason)
_SEED_CACHE: Dict[Tuple[str, str], Tuple[Tuple[str, ...], Dict[str, int], int, Optional[str], Optional[str]]] = {}


def reset_seed_cache() -> None:
    """清空当日冻结缓存(单测与"重跑一次派生"用;生产不调)。"""
    _SEED_CACHE.clear()


def load_mainline_seed_codes(
    report_date: date, *, db_path: Optional[Path] = None, parquet_dir: Optional[Path] = None,
) -> Tuple[Tuple[str, ...], Dict[str, int], int, Optional[str], Optional[str]]:
    """D0 的「热点行业 + 暴起概念」两类种子的**原始成分**(去重、升序,确定性)。

    返回 `(codes, seed_counts, member_total, pack_version, unavailable_reason)`。
    ⛔ 只取这两类 —— `limit_cluster` / `anomaly_cluster` 是「涨停簇 / 异动簇」,不是
    「板块」,拿它们凑样本会把"主线板块跳水"这个词的意思改掉(plan 点名的就是这两类)。
    """
    key = (str(db_path), report_date.strftime("%Y%m%d"))
    hit = _SEED_CACHE.get(key)
    if hit is not None:
        return hit

    from neckline.scan import seeds as seeds_mod

    codes: Tuple[str, ...] = ()
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
        member_set = {c for s in picked for c in s.member_codes if c}
        total = len(member_set)
        codes = tuple(sorted(member_set))   # 确定性:排序落定,不吃上游行序
        if not codes:
            reason = REASON_NO_MAINLINE_SEEDS

    out = (codes, counts, total, pack_version, reason)
    _SEED_CACHE[key] = out
    return out


# ══════════════════════════════════════════════════════════════════════════
# 样本派生(种子成分 ∩ 关注池机械成分)
# ══════════════════════════════════════════════════════════════════════════

def derive_mainline_sample(
    report_date: date,
    *,
    position_codes: Iterable[str] = (),
    prev_limit_up_codes: Iterable[str] = (),
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> MainlineSample:
    """本拍的「主线板块跳水」样本(纯派生,不拉行情、不判触发)。

    `position_codes` / `prev_limit_up_codes` 是关注池里**机械进池**的两条路(见模块头
    第 2 条);⛔ 刻意**不接**篮子成员参数 —— 签名里没有那个口子,后人想接也得先改
    签名、先读这段。来源标签冲突时 `position` 优先(那只票的确是你的持仓)。

    ⚠ 本函数**不做任何阈值判断**,只回答"这一拍该拿哪些票算平均"。触发与否仍由
    `retreat.evaluate_retreat` 按 `sector_dive` 判(阈值一字未动)。
    """
    seed_codes, counts, total, pack_version, reason = load_mainline_seed_codes(
        report_date, db_path=db_path, parquet_dir=parquet_dir)
    if reason is not None and not seed_codes:
        return MainlineSample(seed_counts=counts, seed_member_total=total,
                              pack_version=pack_version, unavailable_reason=reason)

    seed_set = set(seed_codes)
    sources: Dict[str, str] = {}
    for code in prev_limit_up_codes:
        if code in seed_set:
            sources.setdefault(code, SOURCE_PREV_LIMIT_UP)
    for code in position_codes:
        if code in seed_set:
            sources[code] = SOURCE_POSITION      # 持仓标签优先,覆盖上面的默认
    picked: List[str] = sorted(sources)          # 确定性:样本次序只由代码排序决定
    if not picked:
        return MainlineSample(seed_counts=counts, seed_member_total=total,
                              pack_version=pack_version, unavailable_reason=REASON_NO_OVERLAP)
    return MainlineSample(
        codes=tuple(picked),
        sources={c: sources[c] for c in picked},
        seed_counts=counts, seed_member_total=total, pack_version=pack_version,
    )


__all__ = [
    "SOURCE_PREV_LIMIT_UP",
    "SOURCE_POSITION",
    "REASON_NO_ACTIVE_PACK",
    "REASON_SEED_FAILED",
    "REASON_NO_MAINLINE_SEEDS",
    "REASON_NO_OVERLAP",
    "MainlineSample",
    "reason_text",
    "reset_seed_cache",
    "load_mainline_seed_codes",
    "derive_mainline_sample",
]
