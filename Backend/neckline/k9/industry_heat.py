"""行业热度分(K9 §四 第一成分,PROJECT_PLAN §5.4.6)。

> 当日 `sw_industry_daily` 里 **`member_count ≥ params.industry.minMembers`** 且
> **不在 `excludedL2Codes`** 的行业,按 `median_ret` 降序排名 →
> 归一到 `1 − (rank−1)/(N_ranked−1)` ∈ [0, 1]。

**这一层与事实层的分工**(架构 §二 判据:凡是我会想去调的东西都落在策略层):
事实层(`facts/industry.py`)对**每一个**有成员的二级行业都产出中位数,⛔ 无门槛;
「成员数不足的行业不参与热度排名」是**策略主张**,门槛 `minMembers` 是 §8.2 #16
待标定参数,住这里。

🔴 **「查无该行业」的三种处置全部实现,⛔ 没有默认**(§8.3 #18 / §7.6 / 守门 G22):

| `heatAbsentPolicy` | 含义 |
|---|---|
| `renormalize` | 该票按**剩余两项权重重新归一** —— 行业无排名⛔ 不被当成「最差行业」 |
| `zero` | 行业热度分记 **0** —— 等同「最差行业」 |
| `drop` | 该票**直接不参与本日清单** |

取值由标定阶段挑一个填进参数包。本模块用**全映射**分派(`_ABSENT_HANDLERS`,
模块加载时断言三值一个不落),⛔ 没有 `if policy == X: … else: …` 的兜底分支 ——
漏实现一种取值 = **import 就炸**,不是某天悄悄按「最差行业」算了一整个月。

⚠ **代价数字**(§8.3,供标定判断,⛔ 不是建议值):`minMembers` 取 10 → 137 只 /
**3.0%** 的池子拿不到行业热度分;取 15 → 353 只 / **7.7%**。门槛越高,选哪个
`heatAbsentPolicy` 就越要紧。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.facts.industry import IndustryDay
from neckline.k9.params import HeatAbsentPolicy, IndustryParams


@dataclass(frozen=True)
class HeatTable:
    """当日行业热度分表。`scores` 只装**够格参与排名**的行业。"""

    scores: Mapping[str, float]              # l2_code → [0,1],1 = 当日最强行业
    ranked_count: int
    excluded_codes: Tuple[str, ...]          # 被 `excludedL2Codes` 排掉的
    thin_codes: Tuple[str, ...]              # 成员数 < minMembers 的

    def score_of(self, l2_code: Optional[str]) -> Optional[float]:
        """`None` = **查无该行业**(成员数不足 / 被排除 / 这只票压根没有申万归属)。
        ⛔ 调用方不许把它当 0 —— 「不参与排名」与「排在最后」是两回事,
        选哪个由 `heatAbsentPolicy` 决定(见 `apply_absent_policy`)。"""
        if l2_code is None:
            return None
        return self.scores.get(l2_code)


def compute(rows: Sequence[IndustryDay], industry: IndustryParams) -> HeatTable:
    """当日行业事实 + 参数 → 热度分表。

    并列(`median_ret` 相同)取**平均名次** —— 同一个中位数拿到不同分数是纯粹的
    实现噪声,会让「逐字节可复现」变成一句空话。
    """
    excluded = set(industry.excluded_l2_codes)
    kept = [
        r for r in rows
        if r.l2_code not in excluded and r.member_count >= industry.min_members
    ]
    thin = tuple(sorted(
        r.l2_code for r in rows
        if r.l2_code not in excluded and r.member_count < industry.min_members
    ))
    hit_excluded = tuple(sorted(r.l2_code for r in rows if r.l2_code in excluded))

    n = len(kept)
    if n == 0:
        return HeatTable({}, 0, hit_excluded, thin)
    if n == 1:
        # 只有一个行业够格 → 它既是最强也是最弱。归一公式在这里会除零,
        # 给 1.0(「当日最强」)是唯一说得通的读法,⛔ 不给 0(那等于说它最差)。
        return HeatTable({kept[0].l2_code: 1.0}, 1, hit_excluded, thin)

    # 降序排名:median_ret 大的排前面;并列取平均名次。
    ordered = sorted(kept, key=lambda r: (-r.median_ret, r.l2_code))
    ranks: Dict[str, float] = {}
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1].median_ret == ordered[i].median_ret:
            j += 1
        avg_rank = (i + j) / 2 + 1          # 1 起的平均名次
        for k in range(i, j + 1):
            ranks[ordered[k].l2_code] = avg_rank
        i = j + 1

    scores = {code: 1.0 - (rank - 1) / (n - 1) for code, rank in ranks.items()}
    return HeatTable(scores, n, hit_excluded, thin)


# ══════════════════════════════════════════════════════════════════════════
# 「查无该行业」的三种处置 —— **全映射**,⛔ 无默认分支(G22)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class AbsentEffect:
    """一只「查无该行业」的票该被怎么处理。

    三个字段互斥地表达三种取值的后果,⛔ 不许在调用方再写一次 policy 的分支。
    """

    heat_score: Optional[float]     # None = 这一项不参与加权(renormalize)
    renormalize: bool               # 按剩余两项权重重新归一
    drop: bool                      # 该票不参与本日清单


def _absent_renormalize() -> AbsentEffect:
    return AbsentEffect(heat_score=None, renormalize=True, drop=False)


def _absent_zero() -> AbsentEffect:
    return AbsentEffect(heat_score=0.0, renormalize=False, drop=False)


def _absent_drop() -> AbsentEffect:
    return AbsentEffect(heat_score=None, renormalize=False, drop=True)


#: 🔴 三个取值一个不落。⛔ 不许改成 `.get(policy, …)`。
_ABSENT_HANDLERS: Mapping[HeatAbsentPolicy, Callable[[], AbsentEffect]] = {
    HeatAbsentPolicy.RENORMALIZE: _absent_renormalize,
    HeatAbsentPolicy.ZERO: _absent_zero,
    HeatAbsentPolicy.DROP: _absent_drop,
}

# 漏写一种取值 = ImportError,⛔ 不留到某天在报告里悄悄按「最差行业」算。
assert set(_ABSENT_HANDLERS) == set(HeatAbsentPolicy), (
    f"heatAbsentPolicy 有 {set(HeatAbsentPolicy) - set(_ABSENT_HANDLERS)} 没有实现 —— "
    f"三种取值必须全部实现(§7.6),⛔ 不许挑一个当默认")


def apply_absent_policy(policy: HeatAbsentPolicy) -> AbsentEffect:
    """「查无该行业」时按参数包的取值分派。**全映射**,取值不认识 = `KeyError`,
    ⛔ 不是悄悄退回某一种。"""
    return _ABSENT_HANDLERS[policy]()


__all__ = [
    "HeatTable", "compute", "AbsentEffect", "apply_absent_policy",
]
