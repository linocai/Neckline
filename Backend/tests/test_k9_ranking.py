"""K9 第三层 · 排序的**纯函数**夹具(PROJECT_PLAN §5.4.6 / K9 §四 / §五-4)。

🔴 **为什么单开一份**(2026-08-21 复审 M5):`k9/ranking.py` 是第三层的核心,272 行,
在这之前**没有一条直接单测**。全链夹具(`test_k9_layer.py`)证明的是「跑得通」,
守门 G10 的「跑两遍逐字节相等」证明的是**可复现** —— 都不证明**算对**。
以下四件事在这之前一次都没被断言过:

| 未被覆盖 | 代码位置 | K9 出处 |
|---|---|---|
| 三成分公式 `score = w_ih×heat + w_ps×ps + w_relay×relay` | `ranking.py` `rank()` | K9 §四 |
| 「命中多个形态取 max、⛔ 不加分」 | `rank()` 的 `best = max(...)` | K9 §五-4 |
| `renormalize` 的分母 `(w_ps + w_relay)` | 同上 | §8.3 #18 |
| 决定性排序键 `(score desc, heat desc, ts_code asc)` | `ScoredCandidate.sort_key` | §5.4.6 |

⚠ 「命中多个形态」这一支尤其空:`tests/k9_env.py` 的合成市场里**每只票只命中一个
形态**,所以 `max` 那一支在全链里从没被执行过 —— 而 K9 §五-4 的「不加分」正是它。

⚠ **本文件里的权重与分数全是夹具**,不是标定值、不是建议值(§8 的 22 项待标定)。
它们的唯一职责是让公式**手算得出来**:凡是断言里出现的数,都能在注释里对上一步算式。
"""

from __future__ import annotations

import pytest

from neckline.k9 import industry_heat as heat_mod
from neckline.k9 import params as P
from neckline.k9 import ranking as ranking_mod
from neckline.k9.contract import ChannelHit, Pattern, Tier

_L2 = "801080.SI"


def _params(**over) -> P.K9Params:
    """一份**手算友好**的参数:三项主权重 0.5 / 0.3 / 0.2,每个形态单强度项权重 1.0。"""
    weights = over.pop("weights", (0.5, 0.3, 0.2))
    sub = {
        "p1": {"volMultiple": 1.0},
        "p2": {"relStrengthShortfall": 1.0},
        "p3": {"shortWindowImprovement": 1.0},
        "p4": {"inflowRank": 1.0},
    }
    sub.update(over.pop("sub_weights", {}))
    return P.K9Params(
        package_version="fixture", fact_pack_version="fp-2",
        calibrated_by="unit", calibrated_at="t", approved_by="unit", approved_at="t",
        boundary=P.BoundaryParams(new_listing_days=30, liquidity_window_days=20,
                                  liquidity_bottom_pct=0.2, spike_fade_ret_pct=5.0,
                                  spike_fade_gap_pct=3.0),
        industry=P.IndustryParams(
            min_members=3, excluded_l2_codes=("801125.SI",),
            heat_absent_policy=over.pop("policy", P.HeatAbsentPolicy.RENORMALIZE)),
        volume=P.VolumeParams(ma_days=20, eruption_multiple=2.0),
        channels=P.ChannelParams(
            p1=P.ChannelTiers(strict=None, relaxed=None),
            p2=P.ChannelTiers(strict=None, relaxed=None),
            p3=P.ChannelTiers(strict=None, relaxed=None),
            p4=P.ChannelTiers(strict=None, relaxed=None)),
        ranking=P.RankingParams(
            weights=P.RankingWeights(industry_heat=weights[0],
                                     pattern_strength=weights[1], relay=weights[2]),
            pattern_sub_weights=sub,
            relay_lookback_days=10,
            relay_source=P.RelaySource.RECALLED,
            relay_scoring=over.pop("scoring", P.RelayScoring.BINARY),
            upside_room_mech_days=20),
        quota=P.QuotaParams(min=1, max=20, floor_per_channel=1,
                            over_strict_consecutive_days=3),
        explain=P.ExplainParams(max_backfill_rounds=1),
        source_path="fixture",
    )


def _heat(**scores) -> heat_mod.HeatTable:
    return heat_mod.HeatTable(scores=dict(scores), ranked_count=len(scores),
                              excluded_codes=(), thin_codes=())


def _hit(code: str, pattern: Pattern, key: str, value: float) -> ChannelHit:
    return ChannelHit(ts_code=code, pattern=pattern, tier=Tier.STRICT,
                      strength={key: value})


# ══════════════════════════════════════════════════════════════════════════
# ① 三成分公式(K9 §四)
# ══════════════════════════════════════════════════════════════════════════

class TestTheThreeComponentFormula:
    def test_the_total_is_exactly_the_weighted_sum_of_the_three(self):
        """`score = w_ih×heat + w_ps×ps + w_relay×relay`,⛔ 没有第四项、没有系数。

        两只票的 `volMultiple` 是 3.0 / 1.0 → 形态内百分位 = **1.0 / 0.0**
        (`pct_rank` 归一到 [0,1]:`avg_idx / (n−1)`,最强 1、最弱 0)。
        AAA:0.5×0.8 + 0.3×1.0 + 0.2×0(没有接力证据) = 0.70
        BBB:0.5×0.8 + 0.3×0.0 + 0.2×0                  = 0.40
        """
        hits = [_hit("AAA", Pattern.P1, "volMultiple", 3.0),
                _hit("BBB", Pattern.P1, "volMultiple", 1.0)]
        out, dropped = ranking_mod.rank(
            hits, params=_params(), heat=_heat(**{_L2: 0.8}),
            l2_of={"AAA": _L2, "BBB": _L2}, relay_records=[])
        got = {c.ts_code: c for c in out}
        assert dropped == []
        assert got["AAA"].pattern_strength_score == pytest.approx(1.0)
        assert got["BBB"].pattern_strength_score == pytest.approx(0.0)
        assert got["AAA"].score == pytest.approx(0.70)
        assert got["BBB"].score == pytest.approx(0.40)

    def test_the_relay_component_really_enters_the_total(self):
        """接力分不是装饰:同样的热度与强度,有接力证据的那只必须高出 `w_relay`。

        AAA 在 D−1 被**另一个**形态(p4)选中过 → binary 计分 1.0。
        0.5×0.8 + 0.3×1.0 + 0.2×1.0 = 0.90(比上一条多的正好是 0.2)。
        """
        hits = [_hit("AAA", Pattern.P1, "volMultiple", 3.0),
                _hit("BBB", Pattern.P1, "volMultiple", 1.0)]
        records = [ranking_mod.RelayRecord("20240401", "AAA", Pattern.P4)]
        out, _ = ranking_mod.rank(
            hits, params=_params(), heat=_heat(**{_L2: 0.8}),
            l2_of={"AAA": _L2, "BBB": _L2}, relay_records=records)
        got = {c.ts_code: c for c in out}
        assert got["AAA"].relay_score == pytest.approx(1.0)
        assert got["BBB"].relay_score == pytest.approx(0.0)
        assert got["AAA"].score == pytest.approx(0.90)

    def test_the_score_is_rounded_to_twelve_places_before_it_leaves(self):
        """§5.4.6:浮点尾数在不同机器上可能差 1 ulp,「逐字节相等」才可达。"""
        hits = [_hit("AAA", Pattern.P1, "volMultiple", 3.0)]
        out, _ = ranking_mod.rank(
            hits, params=_params(weights=(1 / 3, 1 / 3, 1 / 3)),
            heat=_heat(**{_L2: 1 / 3}), l2_of={"AAA": _L2}, relay_records=[])
        assert out[0].score == round(out[0].score, 12)


# ══════════════════════════════════════════════════════════════════════════
# ② 🔴 命中多个形态:取 max,⛔ 不加分(K9 §五-4)
# ══════════════════════════════════════════════════════════════════════════

class TestMultiplePatternsTakeTheMaxNotTheSum:
    def _two_pattern_case(self, p1_strength: float, p4_strength: float):
        """AAA 同时命中 p1 与 p4;各自形态内都只有它一只 → 两边的百分位都是 1.0。

        ⚠ 因此陪跑票必不可少:没有第二只,`pct_rank` 给不出区分度。
        """
        hits = [
            _hit("AAA", Pattern.P1, "volMultiple", p1_strength),
            _hit("ZZZ", Pattern.P1, "volMultiple", 0.0),
            _hit("AAA", Pattern.P4, "inflowRank", p4_strength),
            _hit("YYY", Pattern.P4, "inflowRank", 0.0),
        ]
        l2_of = {c: _L2 for c in ("AAA", "ZZZ", "YYY")}
        out, _ = ranking_mod.rank(
            hits, params=_params(), heat=_heat(**{_L2: 0.0}),
            l2_of=l2_of, relay_records=[])
        return {c.ts_code: c for c in out}

    def test_hitting_two_patterns_does_not_add_the_two_strength_scores(self):
        """🔴 K9 §五-4 逐字:「命中多个形态**取 max**,⛔ 不加分」。

        两边的形态内强度分都是 1.0(各自形态里它最强)。
        取 max → 1.0 → score = 0.5×0 + 0.3×1.0 + 0.2×0 = **0.30**;
        若实现改成相加,这里会是 0.3×2.0 = 0.60。
        """
        got = self._two_pattern_case(3.0, 3.0)
        assert got["AAA"].patterns == (Pattern.P1, Pattern.P4)
        assert got["AAA"].pattern_strength_score == pytest.approx(1.0)
        assert got["AAA"].score == pytest.approx(0.30), "⛔ 两个形态的强度分不许相加"

    def test_the_primary_pattern_is_the_stronger_one(self):
        """主形态 = 强度分更高的那个(⛔ 不是先召回的那个)。

        p1 里 AAA 拿 1.0(它比陪跑的 ZZZ 强),p4 里 AAA 与 YYY 的 `inflowRank`
        都是 0.0 → **并列取平均名次** → 两只都是 0.5 → p1 更强 → 主形态 p1。
        """
        got = self._two_pattern_case(3.0, 0.0)
        assert got["AAA"].primary_pattern is Pattern.P1
        assert got["AAA"].pattern_strength_score == pytest.approx(1.0)

    def test_a_tie_between_patterns_breaks_by_pattern_order_not_by_luck(self):
        """两个形态的强度分并列 → 按 `PATTERN_ORDER`(p1<p2<p3<p4)定序。

        ⛔ 不许靠 dict / set 的迭代顺序 —— 那会让「逐字节可复现」变成一句空话。
        """
        got = self._two_pattern_case(3.0, 3.0)
        assert got["AAA"].primary_pattern is Pattern.P1

    def test_patterns_are_listed_in_the_canonical_order(self):
        """`patterns` 是**全部**命中的形态,按 `PATTERN_ORDER` 排(K9 §五-4「全部列出」)。"""
        hits = [
            _hit("AAA", Pattern.P4, "inflowRank", 1.0),      # 先给 p4,证明不是「按到达顺序」
            _hit("AAA", Pattern.P1, "volMultiple", 1.0),
        ]
        out, _ = ranking_mod.rank(
            hits, params=_params(), heat=_heat(**{_L2: 0.0}),
            l2_of={"AAA": _L2}, relay_records=[])
        assert out[0].patterns == (Pattern.P1, Pattern.P4)


# ══════════════════════════════════════════════════════════════════════════
# ③ `heatAbsentPolicy` 三种处置的**算式**(§8.3 #18)
# ══════════════════════════════════════════════════════════════════════════

class TestHeatAbsentPolicyArithmetic:
    def _one(self, policy: P.HeatAbsentPolicy):
        """AAA 的行业查不到热度分(`l2_of` 给 None)。强度分 1.0、接力 0。"""
        hits = [_hit("AAA", Pattern.P1, "volMultiple", 3.0)]
        return ranking_mod.rank(
            hits, params=_params(policy=policy), heat=_heat(**{_L2: 0.8}),
            l2_of={"AAA": None}, relay_records=[])

    def test_renormalize_divides_by_the_two_remaining_weights(self):
        """🔴 §8.3 #18:分母是 `(w_ps + w_relay)`,⛔ 不是 1、也⛔ 不是三项之和。

        (0.3×1.0 + 0.2×0) ÷ (0.3 + 0.2) = **0.6**。
        若分母写成 1.0 会得 0.3;若把缺席热度当 0 算(那是 `zero` 的语义)也会得 0.3
        —— 两种错都会让这条红。
        """
        out, dropped = self._one(P.HeatAbsentPolicy.RENORMALIZE)
        assert dropped == []
        assert out[0].industry_heat_score is None, "⛔ 缺席不是 0,是**没有读数**"
        assert out[0].score == pytest.approx(0.6)

    def test_zero_treats_the_absent_industry_as_the_worst_one(self):
        """`zero`:热度分记 0 并照常参与三项加权 → 0.5×0 + 0.3×1.0 = 0.30。"""
        out, _ = self._one(P.HeatAbsentPolicy.ZERO)
        assert out[0].industry_heat_score == 0.0
        assert out[0].score == pytest.approx(0.30)

    def test_drop_removes_the_candidate_and_names_it(self):
        """`drop`:这只票不进候选,且**说出是谁**(⛔ 不静默少一只)。"""
        out, dropped = self._one(P.HeatAbsentPolicy.DROP)
        assert out == [] and dropped == ["AAA"]

    def test_a_stock_with_a_known_industry_is_untouched_by_the_policy(self):
        """三种处置只作用于**查无该行业**的票,⛔ 不许殃及有热度分的。"""
        hits = [_hit("AAA", Pattern.P1, "volMultiple", 3.0)]
        for policy in P.HeatAbsentPolicy:
            out, dropped = ranking_mod.rank(
                hits, params=_params(policy=policy), heat=_heat(**{_L2: 0.8}),
                l2_of={"AAA": _L2}, relay_records=[])
            assert dropped == []
            assert out[0].score == pytest.approx(0.5 * 0.8 + 0.3 * 1.0), policy


# ══════════════════════════════════════════════════════════════════════════
# ④ 决定性排序键 `(score desc, heat desc, ts_code asc)`(§5.4.6)
# ══════════════════════════════════════════════════════════════════════════

class TestTheSortIsDeterministic:
    def test_higher_score_comes_first(self):
        hits = [_hit("AAA", Pattern.P1, "volMultiple", 1.0),
                _hit("BBB", Pattern.P1, "volMultiple", 3.0)]
        out, _ = ranking_mod.rank(
            hits, params=_params(), heat=_heat(**{_L2: 0.5}),
            l2_of={"AAA": _L2, "BBB": _L2}, relay_records=[])
        assert [c.ts_code for c in out] == ["BBB", "AAA"]

    def test_same_score_breaks_by_industry_heat_then_by_code(self):
        """同分 → 热度分高的在前;热度也同 → `ts_code` **升序**(⛔ 不是随机)。"""
        a = ranking_mod.ScoredCandidate(
            ts_code="BBB", patterns=(Pattern.P1,), primary_pattern=Pattern.P1,
            tier=Tier.STRICT, industry_heat_score=0.9,
            pattern_strength_score=0.1, relay_score=0.0, score=0.5)
        b = ranking_mod.ScoredCandidate(
            ts_code="AAA", patterns=(Pattern.P1,), primary_pattern=Pattern.P1,
            tier=Tier.STRICT, industry_heat_score=0.1,
            pattern_strength_score=0.9, relay_score=0.0, score=0.5)
        c = ranking_mod.ScoredCandidate(
            ts_code="CCC", patterns=(Pattern.P1,), primary_pattern=Pattern.P1,
            tier=Tier.STRICT, industry_heat_score=0.9,
            pattern_strength_score=0.1, relay_score=0.0, score=0.5)
        assert [x.ts_code for x in sorted((a, b, c), key=lambda x: x.sort_key)] == [
            "BBB", "CCC", "AAA"]

    def test_an_absent_heat_score_sorts_after_a_real_one_but_is_not_minus_one(self):
        """`None` 只是**排序哨兵**(-1.0),⛔ 不是把它的热度分算成 -1。"""
        absent = ranking_mod.ScoredCandidate(
            ts_code="AAA", patterns=(Pattern.P1,), primary_pattern=Pattern.P1,
            tier=Tier.STRICT, industry_heat_score=None,
            pattern_strength_score=0.5, relay_score=0.0, score=0.5)
        real = ranking_mod.ScoredCandidate(
            ts_code="BBB", patterns=(Pattern.P1,), primary_pattern=Pattern.P1,
            tier=Tier.STRICT, industry_heat_score=0.0,
            pattern_strength_score=0.5, relay_score=0.0, score=0.5)
        assert [x.ts_code for x in sorted((absent, real), key=lambda x: x.sort_key)] == [
            "BBB", "AAA"]
        assert absent.industry_heat_score is None, "⛔ 哨兵不许回写进读数"

    def test_the_same_input_ranks_the_same_way_twice(self):
        """确定性:同样的输入跑两遍,名次逐位相同(G10 在纯函数这一层的形态)。"""
        hits = [_hit(c, Pattern.P1, "volMultiple", v)
                for c, v in (("AAA", 1.0), ("BBB", 1.0), ("CCC", 2.0))]
        l2_of = {c: _L2 for c in ("AAA", "BBB", "CCC")}
        first, _ = ranking_mod.rank(hits, params=_params(), heat=_heat(**{_L2: 0.5}),
                                    l2_of=l2_of, relay_records=[])
        second, _ = ranking_mod.rank(hits, params=_params(), heat=_heat(**{_L2: 0.5}),
                                     l2_of=l2_of, relay_records=[])
        assert [c.ts_code for c in first] == [c.ts_code for c in second]
        assert [c.score for c in first] == [c.score for c in second]


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 形态内强度分:键对不上**当场抛**,⛔ 不静默按 0 分
# ══════════════════════════════════════════════════════════════════════════

class TestPatternStrengthKeysMustMatch:
    def test_an_unknown_strength_key_raises_instead_of_scoring_zero(self):
        hits = [_hit("AAA", Pattern.P1, "typoedKey", 1.0)]
        with pytest.raises(KeyError, match="typoedKey"):
            ranking_mod.pattern_strength_scores(
                hits, {"p1": {"volMultiple": 1.0}})

    def test_a_missing_sub_weight_group_raises(self):
        hits = [_hit("AAA", Pattern.P1, "volMultiple", 1.0)]
        with pytest.raises(KeyError, match="patternSubWeights"):
            ranking_mod.pattern_strength_scores(hits, {"p2": {"x": 1.0}})

    def test_a_missing_reading_renormalises_over_the_remaining_weights(self):
        """§14 S6 登记:某一项没读到 → **退出加权**并重新归一,⛔ 不按 0 分算。

        AAA 缺 `upsideRoomFar`,只剩 `volMultiple`(权重 0.4)→ 分母也只剩 0.4,
        于是它的强度分 = 它在 `volMultiple` 上的百分位本身(1.0)。
        """
        hits = [
            ChannelHit(ts_code="AAA", pattern=Pattern.P1, tier=Tier.STRICT,
                       strength={"volMultiple": 3.0}),
            ChannelHit(ts_code="BBB", pattern=Pattern.P1, tier=Tier.STRICT,
                       strength={"volMultiple": 1.0, "upsideRoomFar": 1.0}),
        ]
        got = ranking_mod.pattern_strength_scores(
            hits, {"p1": {"volMultiple": 0.4, "upsideRoomFar": 0.6}})
        assert got[("AAA", Pattern.P1)] == pytest.approx(1.0)
