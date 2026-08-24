"""K9 第三层 · 排序(K9 §四,PROJECT_PLAN §5.4.6)。

> `score = w_ih × 行业热度分 + w_ps × 形态内强度分 + w_relay × 跨日接力分`

三项权重待标定(§8.1 #11),三项各自都归一到 **[0, 1]**,所以总分也在 [0, 1]。

| 成分 | 口径 | 可比性 |
|---|---|---|
| **行业热度分** | 见 `k9/industry_heat.py`(读 `minMembers` / `excludedL2Codes` / `heatAbsentPolicy`) | **跨形态可比** |
| **形态内强度分** | 该形态每个强度项在**本形态候选集内**取百分位(并列取平均名次)→ 按 `patternSubWeights[pattern]` 加权 | 形态内可比 |
| **跨日接力分** | 过去 `relayLookbackDays` 天内被**其它**形态选中过 | **跨形态可比** |

🔴 **跨日接力分的四种组合全部实现,⛔ 无默认**(§8.3 #19/#20 / 守门 G22):
`relaySource ∈ {recalled, shortlisted}` × `relayScoring ∈ {binary, count}`,
用**全映射**分派(模块加载时断言两个枚举一个取值不落)。

- **当日方向 ⛔ 不参与排序**(K9 §四 末段)——它只作报告背景。本模块因此连方向都
  拿不到:`rank()` 的签名里没有它。
- **一只票命中多个形态**:形态内强度分取各命中形态的 **max**、`primary_pattern` =
  argmax、`patterns` 列全部。这是 K9 §五-4「命中多个形态**不加分**」的最保守读法
  —— max 不会超过任何单形态得分。
- **决定性排序键** `(score desc, 行业热度分 desc, ts_code asc)`:保证同包同参跑两遍
  逐字节相同(守门 G10)。

⚠ **强度项缺读数时按剩余项重新归一**(已登记 §14):某个强度性读数为 `None`
(如历史不足算不出上方机械空间)时,该项**退出**本票的加权并把权重摊给剩余项,
⛔ 不按 0 分算 —— 0 分等于宣称「这项它最差」,而真相是「这项没读到」。
整组都读不到 → 形态内强度分 0.0(此时确实无从比较)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from neckline.k9 import industry_heat as heat_mod
from neckline.k9 import ranks as ranks_mod
from neckline.k9.contract import (
    PATTERN_ORDER,
    ChannelHit,
    Pattern,
    SeatKind,
    Tier,
)
from neckline.k9.params import K9Params, RelayScoring, RelaySource


@dataclass(frozen=True)
class RelayRecord:
    """历史上「这只票在那天被那个形态选中过」的一条记录。

    由 `k9/run.py` 从 `k9_channel_hits`(`recalled`)或 `k9_listing_entries`
    (`shortlisted`)取,取哪张由 `params.ranking.relaySource` 决定 —— 本模块只负责
    **算分**,⛔ 不去决定读哪张表(那是取数,不是排序)。
    """

    trade_date: str            # 'YYYYMMDD'
    ts_code: str
    pattern: Pattern


@dataclass(frozen=True)
class ScoredCandidate:
    """一只候选票的三项分与总分。"""

    ts_code: str
    patterns: Tuple[Pattern, ...]
    primary_pattern: Pattern
    tier: Tier
    industry_heat_score: Optional[float]     # None = 查无该行业且 policy=renormalize
    pattern_strength_score: float
    relay_score: float
    score: float
    evidence: Mapping[str, Optional[bool]] = field(default_factory=dict)
    risks: Tuple[str, ...] = ()

    @property
    def sort_key(self) -> Tuple[float, float, str]:
        """决定性排序键。⚠ 行业热度分为 `None` 时排在同分有热度分的票**之后**
        —— 这只是一个**排序**哨兵(-1.0),⛔ 不是把它的热度分算成 -1。"""
        heat = -1.0 if self.industry_heat_score is None else self.industry_heat_score
        return (-self.score, -heat, self.ts_code)


# ══════════════════════════════════════════════════════════════════════════
# 形态内强度分
# ══════════════════════════════════════════════════════════════════════════

def pattern_strength_scores(
    hits: Sequence[ChannelHit], sub_weights: Mapping[str, Mapping[str, float]]
) -> Dict[Tuple[str, Pattern], float]:
    """`(ts_code, pattern) → 形态内强度分 ∈ [0,1]`。

    百分位在**本形态的候选集内**取(K9 §四:形态内可比),⛔ 不跨形态取 ——
    形态 1 的「放量 3 倍」与形态 2 的「跑输行业 5%」没有共同尺子。

    强度项的键必须逐字对上 `patternSubWeights[pattern]`;对不上**当场抛** ——
    ⛔ 不静默按 0 分算,那会让一个打错的键悄悄变成「这项它最差」。
    """
    out: Dict[Tuple[str, Pattern], float] = {}
    for pattern in PATTERN_ORDER:
        group = [h for h in hits if h.pattern is pattern]
        if not group:
            continue
        weights = sub_weights.get(pattern.value)
        if weights is None:
            raise KeyError(
                f"参数包的 ranking.patternSubWeights 缺 {pattern.value} —— "
                f"该形态今天有候选却没有合成权重,⛔ 不许按 0 分兜底")
        keys = tuple(weights)
        for h in group:
            unknown = sorted(set(h.strength) - set(keys))
            if unknown:
                raise KeyError(
                    f"{pattern.value} 的强度项 {unknown} 不在 patternSubWeights"
                    f"{sorted(keys)} 里 —— 键对不上就没有权重可用,"
                    f"⛔ 不许静默丢弃")
        percentiles = {
            key: ranks_mod.pct_rank({h.ts_code: h.strength.get(key) for h in group})
            for key in keys
        }
        for h in group:
            num = 0.0
            den = 0.0
            for key in keys:
                p = percentiles[key].get(h.ts_code)
                if p is None:            # 该项没读到 → 退出加权(见模块 docstring)
                    continue
                w = float(weights[key])
                num += w * p
                den += w
            out[(h.ts_code, pattern)] = (num / den) if den > 0 else 0.0
    return out


# ══════════════════════════════════════════════════════════════════════════
# 跨日接力分 —— 四种组合全部实现,**全映射**,⛔ 无默认(G22)
# ══════════════════════════════════════════════════════════════════════════

def _relay_binary(counts: Mapping[str, int]) -> Dict[str, float]:
    """有 / 无,二值。"""
    return {code: (1.0 if n > 0 else 0.0) for code, n in counts.items()}


def _relay_count(counts: Mapping[str, int]) -> Dict[str, float]:
    """计次(被几个不同形态在几天里选过)。

    ⚠ 归一到 [0,1] 用的是**当日候选里的最大计次**(已登记 §14):三项分必须同量纲
    才能加权,而「最多被接力几次」没有先验上限。当日最大为 0 → 全体 0。
    """
    top = max(counts.values(), default=0)
    if top <= 0:
        return {code: 0.0 for code in counts}
    return {code: n / top for code, n in counts.items()}


#: 🔴 两个取值一个不落。⛔ 不许改成 `.get(scoring, …)`。
_RELAY_SCORERS: Mapping[RelayScoring, Callable[[Mapping[str, int]], Dict[str, float]]] = {
    RelayScoring.BINARY: _relay_binary,
    RelayScoring.COUNT: _relay_count,
}
assert set(_RELAY_SCORERS) == set(RelayScoring), (
    f"relayScoring 有 {set(RelayScoring) - set(_RELAY_SCORERS)} 没有实现 —— "
    f"两种打分形状必须全部实现(§7.6),⛔ 不许挑一个当默认")

#: `relaySource` 的两个取值 → 该去读哪张表。**本模块不读表**,这张映射是给
#: `k9/run.py` 用的 —— 把「读哪张」也做成全映射,漏一个取值同样 import 就炸。
RELAY_TABLE_OF: Mapping[RelaySource, str] = {
    RelaySource.RECALLED: "k9_channel_hits",
    RelaySource.SHORTLISTED: "k9_listing_entries",
}
assert set(RELAY_TABLE_OF) == set(RelaySource), (
    f"relaySource 有 {set(RelaySource) - set(RELAY_TABLE_OF)} 没有对应的取数表")


def relay_counts(
    records: Iterable[RelayRecord], today_patterns: Mapping[str, Tuple[Pattern, ...]]
) -> Dict[str, int]:
    """`ts_code → 被**其它**形态选中过的次数`(K9 §四:「被**其他**形态选中过」)。

    「其它」= 不在这只票**今天**命中的形态集合里。同一天同一个形态重复出现只算一次
    (一天一个形态就是一份证据,⛔ 不因为两档都记了一条就算两份)。
    """
    seen: Dict[str, set] = {code: set() for code in today_patterns}
    for r in records:
        mine = today_patterns.get(r.ts_code)
        if mine is None or r.pattern in mine:
            continue
        seen[r.ts_code].add((r.trade_date, r.pattern))
    return {code: len(v) for code, v in seen.items()}


# ══════════════════════════════════════════════════════════════════════════
# 总分
# ══════════════════════════════════════════════════════════════════════════

def rank(
    hits: Sequence[ChannelHit],
    *,
    params: K9Params,
    heat: heat_mod.HeatTable,
    l2_of: Mapping[str, Optional[str]],
    relay_records: Sequence[RelayRecord],
) -> Tuple[List[ScoredCandidate], List[str]]:
    """把召回集合排成一份**确定性**的名次表。

    返回 `(按名次升序的候选, 被 heatAbsentPolicy='drop' 丢掉的票)`。
    ⛔ 本函数不分席位(那是 `k9/quota.py`),也不知道当日方向(K9 §四 末段)。
    """
    if not hits:
        return [], []

    today_patterns: Dict[str, Tuple[Pattern, ...]] = {}
    tier_of: Dict[str, Tier] = {}
    for h in hits:
        got = today_patterns.get(h.ts_code, ())
        if h.pattern not in got:
            today_patterns[h.ts_code] = tuple(
                p for p in PATTERN_ORDER if p in got or p is h.pattern)
        # 成色取更好的那个:两档都中 → 记 strict(K9 §五-7)
        if tier_of.get(h.ts_code) is not Tier.STRICT:
            tier_of[h.ts_code] = h.tier

    strength = pattern_strength_scores(hits, params.ranking.pattern_sub_weights)
    counts = relay_counts(relay_records, today_patterns)
    relay_scores = _RELAY_SCORERS[params.ranking.relay_scoring](counts)

    w = params.ranking.weights
    out: List[ScoredCandidate] = []
    dropped: List[str] = []
    for code in sorted(today_patterns):
        patterns = today_patterns[code]
        # 命中多个形态:取 max,并列时按 PATTERN_ORDER 定序(⛔ 不加分,K9 §五-4)
        best = max(
            patterns,
            key=lambda p: (strength.get((code, p), 0.0), -PATTERN_ORDER.index(p)),
        )
        base_ps = strength.get((code, best), 0.0)
        bonus = max(
            (float(hit.bonus_score) for hit in hits
             if hit.ts_code == code and hit.pattern is best),
            default=0.0,
        )
        ps = min(1.0, base_ps + bonus)
        relay = relay_scores.get(code, 0.0)

        heat_score = heat.score_of(l2_of.get(code))
        if heat_score is None:
            effect = heat_mod.apply_absent_policy(params.industry.heat_absent_policy)
            if effect.drop:
                dropped.append(code)
                continue
            if effect.renormalize:
                den = w.pattern_strength + w.relay
                score = ((w.pattern_strength * ps + w.relay * relay) / den) if den > 0 else 0.0
            else:
                heat_score = effect.heat_score
                score = (w.industry_heat * float(heat_score)
                         + w.pattern_strength * ps + w.relay * relay)
        else:
            score = (w.industry_heat * heat_score
                     + w.pattern_strength * ps + w.relay * relay)

        code_hits = [h for h in hits if h.ts_code == code]
        evidence: Dict[str, Optional[bool]] = {}
        for hit in code_hits:
            for key, value in hit.evidence.items():
                previous = evidence.get(key)
                # True 胜过 False；数据可用的 False 胜过不可用的 None。
                evidence[key] = (True if previous is True or value is True
                                 else False if previous is False or value is False
                                 else None)
        risks = tuple(sorted({risk for hit in code_hits for risk in hit.risks}))
        out.append(ScoredCandidate(
            ts_code=code, patterns=patterns, primary_pattern=best,
            tier=tier_of[code], industry_heat_score=heat_score,
            pattern_strength_score=ps, relay_score=relay,
            # 浮点尾数在不同机器上可能差 1 ulp → 落库前统一收到 12 位,
            # 「逐字节相等」这条验收才是可达的(⛔ 不是为了好看)。
            score=round(score, 12),
            evidence=dict(sorted(evidence.items())), risks=risks,
        ))
    out.sort(key=lambda c: c.sort_key)
    return out, sorted(dropped)


__all__ = [
    "RelayRecord", "ScoredCandidate",
    "pattern_strength_scores", "relay_counts", "RELAY_TABLE_OF", "rank",
]
