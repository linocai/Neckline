"""K9 第五节 · 名额分配(K9 §五,PROJECT_PLAN §5.4.7)。

八条规则逐条实现:

| # | 规则 | 落点 |
|---|---|---|
| 1 | **容量** 最少 `quota.min`(10)、最多 `quota.max`(20) | `allocate` |
| 2 | **保底席位** 当日有候选的形态各先占 `floorPerChannel`(1) 席 | `allocate` 第一轮 |
| 3 | **自由竞争** 剩余席位按总分统一分配,不限形态 | `allocate` 第二轮 |
| 4 | **单一席位** 一只票只占一个席位;命中的形态全部列出;命中多形态⛔ 不加分 | `ranking` 取 max + 本模块去重 |
| 5 | **诚实缺席** 某形态当日无候选 → 标「今日无此形态」,⛔ 不放宽标准去凑 | `absent_patterns` |
| 6 | **分档放宽** 严格档不足 `quota.min` 时自动切换放宽档 | `choose_tier` |
| 7 | **成色标注** 报告标明严格档几只、放宽档几只 | 每只票自带 `tier` |
| 8 | **过严提示** 连续多日靠放宽档凑足 → 判据过严 | `over_strict`(读 `k9_runs` 历史) |

🔴 **保底席位的分配次序 = 各形态「最佳候选分数」降序**(§5.4.7 第 3 步),
并列时按 `p1 < p2 < p3 < p4` 定序。⛔ 不是固定按 p1..p4 轮 —— 那会给 p1 系统性优势
(它总能先挑走那只两边都中的票)。这样既避免了偏袒,又完全确定性。

⚠ **容量不足时如实出这么多**(§5.4.7 第 6 步):放宽档后仍 < `quota.min` →
报告显式披露 `capacity_short`,⛔ **不制造候选**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from neckline.k9.contract import PATTERN_ORDER, ChannelHit, Pattern, SeatKind, Tier
from neckline.k9.params import QuotaParams
from neckline.k9.ranking import ScoredCandidate


@dataclass(frozen=True)
class TierDecision:
    """§5.4.7 第 2 步:今天用严格档还是「严格 ∪ 放宽」。"""

    tier_used: Tier
    hits: Tuple[ChannelHit, ...]              # 参与后续排序的召回集合
    strict_candidates: int                    # 严格档去重后的票数
    relaxed_candidates: int                   # 并集去重后的票数
    per_pattern: Mapping[str, Mapping[str, int]]   # 每形态 strict / relaxed 数


def choose_tier(hits: Sequence[ChannelHit], quota: QuotaParams) -> TierDecision:
    """严格档去重后 ≥ `quota.min` → **只从 strict 抽**;否则用 `strict ∪ relaxed`。

    ⚠ 档位必须在**排序之前**决定:形态内强度分是在**本形态候选集内**取百分位的,
    候选集变了分数就变了。⛔ 不许先按并集排完再砍掉放宽档的票。
    """
    strict_hits = [h for h in hits if h.tier is Tier.STRICT]
    strict_codes = {h.ts_code for h in strict_hits}
    all_codes = {h.ts_code for h in hits}

    per_pattern = {
        p.value: {
            "strict": len({h.ts_code for h in strict_hits if h.pattern is p}),
            "relaxed": len({h.ts_code for h in hits if h.pattern is p}),
        }
        for p in PATTERN_ORDER
    }
    if len(strict_codes) >= quota.min:
        return TierDecision(Tier.STRICT, tuple(strict_hits), len(strict_codes),
                            len(all_codes), per_pattern)
    return TierDecision(Tier.RELAXED, tuple(hits), len(strict_codes),
                        len(all_codes), per_pattern)


@dataclass(frozen=True)
class Seat:
    candidate: ScoredCandidate
    rank: int
    seat_kind: Optional[SeatKind]             # None = 后备(reserve)


@dataclass(frozen=True)
class Allocation:
    seated: Tuple[Seat, ...]
    reserve: Tuple[Seat, ...]
    absent_patterns: Tuple[Pattern, ...]
    capacity_short: bool

    @property
    def seated_per_pattern(self) -> Dict[str, int]:
        out = {p.value: 0 for p in PATTERN_ORDER}
        for s in self.seated:
            out[s.candidate.primary_pattern.value] += 1
        return out


def allocate(
    candidates: Sequence[ScoredCandidate],
    quota: QuotaParams,
    *,
    recalled_patterns: Iterable[Pattern],
) -> Allocation:
    """保底 → 自由竞争 → 后备。`candidates` 必须已按名次升序(`ranking.rank` 的输出)。

    🔴 `recalled_patterns` **必填**(2026-08-21 复审 L1):K9 §五-5 的「诚实缺席」
    说的是「某形态当日**无候选**」,而 `candidates` 是 `ranking.rank` 的输出 ——
    已经过了 `heatAbsentPolicy='drop'` 那一刀。拿它算缺席,会把「有候选、但候选
    因为查不到行业热度被丢了」误报成「今日无此形态」,而同一份报告里的
    `channel_counts`(取自 drop **之前**的 `decision.per_pattern`)会说那个形态
    今天有几只 —— 两个数当场打架。
    被 drop 的票单独在 `Shortlist.dropped_by_heat_absent` 里说,⛔ 不混进缺席。
    ⛔ 不给默认值:空默认会让这条口径在调用方忘了传时安静退回旧行为。
    """
    if quota.min > quota.max:
        raise ValueError(f"quota.min({quota.min}) > quota.max({quota.max})")

    present = set(recalled_patterns)
    absent = tuple(p for p in PATTERN_ORDER if p not in present)

    seat_of: Dict[str, SeatKind] = {}
    by_code = {c.ts_code: c for c in candidates}

    # —— 第一轮:保底席位 ————————————————————————————————————————————————
    # 每个形态的候选按分数降序排好;分配次序 = 各形态最佳候选分数降序。
    queues: Dict[Pattern, List[ScoredCandidate]] = {
        p: [c for c in candidates if p in c.patterns] for p in PATTERN_ORDER
    }
    order = sorted(
        (p for p in PATTERN_ORDER if queues[p]),
        key=lambda p: (-queues[p][0].score, PATTERN_ORDER.index(p)),
    )
    for p in order:
        taken = 0
        for c in queues[p]:
            if taken >= quota.floor_per_channel or len(seat_of) >= quota.max:
                break
            if c.ts_code in seat_of:
                continue                  # 一票一席:被别的形态占了就取本形态次优
            seat_of[c.ts_code] = SeatKind.FLOOR
            taken += 1

    # —— 第二轮:自由竞争 ————————————————————————————————————————————————
    for c in candidates:
        if len(seat_of) >= quota.max:
            break
        if c.ts_code not in seat_of:
            seat_of[c.ts_code] = SeatKind.FREE

    seated: List[Seat] = []
    reserve: List[Seat] = []
    for i, c in enumerate(candidates, start=1):
        kind = seat_of.get(c.ts_code)
        (seated if kind is not None else reserve).append(Seat(c, i, kind))

    return Allocation(
        seated=tuple(seated),
        reserve=tuple(reserve),
        absent_patterns=absent,
        # 放宽档后仍不足 → **如实出这么多**,⛔ 不制造候选(§5.4.7 第 6 步)
        capacity_short=len(seated) < quota.min,
    )


def over_strict(relaxed_days_streak: int, quota: QuotaParams) -> bool:
    """K9 §五-8:连续 `overStrictConsecutiveDays` 天靠放宽档凑足 → 「判据过严,建议重标」。

    连续天数由 `k9/run.py` 从 `k9_runs` 历史数出来 —— 本模块只判门槛,⛔ 不查库。
    """
    return relaxed_days_streak >= quota.over_strict_consecutive_days


__all__ = ["TierDecision", "choose_tier", "Seat", "Allocation", "allocate", "over_strict"]
