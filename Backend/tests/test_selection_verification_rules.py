"""V2-⑦-b 验证 / 失效条件集与聚合规则(plan §五 ⑦-b 验收逐条)。

覆盖:①四态映射(含 n=1/2/3 三种篮子规模的 `min_members_hit` 边界);②**修订后的
失效第 ③ 条**专项(「高于 D0 收盘但跌破 MA20」与「低于 D0 收盘但站上 MA20」都**不判**
`falsified`,两者同时成立才判);③`falsified` 优先级压过一切;④缺数据 / 阈值 null
两侧都不计(`None` ≠ `False`);⑤止损线随现役 `stop_pct` 变而变(禁硬编);
⑥`verification_ruleset_version` 落进 `card_json.fingerprint` + 「改条件即 bump」守门;
⑦引擎常量白名单登记。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tests.conftest import seed_active_rule_v1

from neckline.selection import basket_card as bc
from neckline.selection import verification_rules as vr

D0 = date(2026, 7, 24)


# ══════════════════════════════════════════════════════════════════════════
# 聚合门槛 + 四态映射
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("n,expected", [(0, 1), (1, 1), (2, 1), (3, 2), (4, 2), (5, 3)])
def test_min_members_hit_is_ceil_half_both_sides(n, expected):
    """`ceil(n/2)`,**验证与失效两侧同一个数**(⑦-b 补齐的 Plan 缺口:对称、不发明第二个数)。"""
    assert vr.min_members_hit(n) == expected
    mechs = [bc.MemberMech(ts_code=f"{i}.SH", close=10.0, ma20=9.0, stop_price=9.5,
                           limit_down=9.0) for i in range(n)]
    v = bc.build_verification_spec("b1", D0, mechs)
    iv = bc.build_invalidation_spec("b1", D0, mechs, stop_pct=0.05)
    assert v["min_members_hit"] == iv["min_members_hit"] == expected


@pytest.mark.parametrize("verify,invalid,min_hit,state", [
    # n=1 → min_hit=1
    (1, 0, 1, vr.STATE_VERIFIED),
    (0, 1, 1, vr.STATE_FALSIFIED),
    (0, 0, 1, vr.STATE_UNCLEAR),
    # n=3 → min_hit=2:只对了一半 = partial(四态里唯一承接它的格子)
    (1, 0, 2, vr.STATE_PARTIAL),
    (2, 0, 2, vr.STATE_VERIFIED),
    (0, 2, 2, vr.STATE_FALSIFIED),
    (1, 1, 2, vr.STATE_PARTIAL),   # 两侧都有命中但都没到门槛 → 仍是"只对了一半"
    (0, 1, 2, vr.STATE_UNCLEAR),
])
def test_four_state_mapping(verify, invalid, min_hit, state):
    assert vr.decide_state(verify, invalid, min_hit) == state


def test_falsified_beats_verified_when_both_sides_reach_threshold():
    """**`falsified` 优先级压过一切**(宁可先说坏消息)。"""
    assert vr.decide_state(3, 2, 2) == vr.STATE_FALSIFIED
    assert vr.decide_state(9, 1, 1) == vr.STATE_FALSIFIED


# ══════════════════════════════════════════════════════════════════════════
# 修订后的失效第 ③ 条:两条 AND(⑦-b-B 治的就是「擦边跌破就判证伪」)
# ══════════════════════════════════════════════════════════════════════════

def _below_both(price, *, ref=10.0, ma20=9.5):
    return vr.evaluate_condition(
        vr.CMP_CLOSE_LT_ALL, {vr.LEVEL_REF_CLOSE: ref, vr.LEVEL_MA20: ma20},
        price=price, low=None,
    )


def test_above_ref_but_below_ma20_is_not_falsified():
    """MA20 高于 D0 收盘的形态下,价落在两者之间 → 跌破 MA20 但仍 ≥ D0 收盘:**不判破位**。"""
    assert _below_both(10.2, ref=10.0, ma20=10.5) is False


def test_below_ref_but_above_ma20_is_not_falsified():
    """跌破 D0 收盘但守住 MA20:**不判破位**(中间地带落 `partial`/`unclear`)。"""
    assert _below_both(9.8, ref=10.0, ma20=9.5) is False


def test_below_both_is_falsified():
    assert _below_both(9.4, ref=10.0, ma20=9.5) is True


def test_exactly_at_level_is_not_below():
    """严格小于才算破位(带 `EPS` 容差,不因二进制浮点噪声误判)。"""
    assert _below_both(10.0, ref=10.0, ma20=9.5) is False
    assert _below_both(9.5, ref=10.0, ma20=9.5) is False


def test_compound_condition_needs_both_levels_present():
    """任一子阈值算不出 → 整条**不判**(`None`),⛔ 不许拿半条凑。"""
    assert _below_both(1.0, ref=None, ma20=9.5) is None
    assert _below_both(1.0, ref=10.0, ma20=None) is None
    assert vr.evaluate_condition(vr.CMP_CLOSE_LT_ALL, 9.0, price=1.0, low=None) is None


# ══════════════════════════════════════════════════════════════════════════
# 「没有」与「没看」:null 阈值 / 缺观测 一律 None,绝不当成 False
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("compare", [vr.CMP_CLOSE_GE, vr.CMP_CLOSE_LE, vr.CMP_LOW_LE])
def test_null_level_or_missing_observation_yields_none_not_false(compare):
    assert vr.evaluate_condition(compare, None, price=10.0, low=10.0) is None
    assert vr.evaluate_condition(compare, 10.0, price=None, low=None) is None


def test_unknown_compare_semantics_is_none_not_a_guess():
    """老卡里写了本引擎不认识的比较语义 → 如实"判不了",不瞎猜(冻结件兼容纪律)。"""
    assert vr.evaluate_condition("close~=level", 10.0, price=10.0, low=10.0) is None


def test_limit_down_touch_uses_low_not_close():
    """`触及跌停` 是盘中语义:**触及即算**,不要求收在跌停。"""
    assert vr.evaluate_condition(vr.CMP_LOW_LE, 9.0, price=9.6, low=9.0) is True
    assert vr.evaluate_condition(vr.CMP_LOW_LE, 9.0, price=9.6, low=9.1) is False


# ══════════════════════════════════════════════════════════════════════════
# 止损线随现役章程走(spec 里的数跟着变,禁硬编)
# ══════════════════════════════════════════════════════════════════════════

def test_invalidation_spec_stop_line_follows_active_charter(isolated_env):
    seed_active_rule_v1(isolated_env, extra_config={"stop_pct": 0.08})
    stop_pct, _tpr = bc.resolve_charter_pcts(isolated_env.db_path)
    mechs = bc.build_member_mech({"600000.SH": 10.0}, D0, stop_pct=stop_pct,
                                 db_path=isolated_env.db_path)
    iv = bc.build_invalidation_spec("b1", D0, list(mechs.values()), stop_pct=stop_pct)
    assert iv["stop_pct"] == pytest.approx(0.08)
    assert iv["members"][0][vr.COND_CLOSE_BELOW_STOP_LINE] == pytest.approx(9.2)


# ══════════════════════════════════════════════════════════════════════════
# ruleset 版本:落卡指纹 + 「改条件即 bump」守门
# ══════════════════════════════════════════════════════════════════════════

def test_ruleset_version_is_carried_into_card_fingerprint_and_specs():
    """⑨ 评价引擎按它分层归因;没有它,两套条件集的成绩会被混成一锅。"""
    j = bc.build_verification_spec("b1", D0, [bc.MemberMech(ts_code="600000.SH", close=10.0)])
    assert j["ruleset_version"] == vr.VERIFICATION_RULESET_VERSION
    iv = bc.build_invalidation_spec("b1", D0, [bc.MemberMech(ts_code="600000.SH", close=10.0)])
    assert iv["ruleset_version"] == vr.VERIFICATION_RULESET_VERSION


def test_ruleset_snapshot_forces_version_bump_when_conditions_change():
    """**条件或阈值一改就 bump `VERIFICATION_RULESET_VERSION`**(⑦-b 落地要求)。

    本快照锁住「当前这套条件集长什么样」。改了条件 / 门槛 / 比较语义 → 这条会挂,
    **挂了不是修快照了事**:先 bump 版本串,再同步更新下面的快照(否则 ⑨ 会把两套
    条件集的成绩混成一锅)。
    """
    snapshot = {
        # v2(2026-08-03,判定线审计 🟡-1):条件码与阈值一个没动,变的是**「判不了」
        # 怎么算** —— 由「扔掉不可判的条件、对剩下的子集取 all()/any()」改为 Kleene
        # 三值(见下面 `side_logic` 真值表)。判据松紧变了就是条件集变了,故 bump。
        "ruleset_version": "verify_ruleset_v2",
        "require_all": ("close_at_or_above_ref", "holds_ma20"),
        "any_of": ("close_below_stop_line", "limit_down_touch", "close_below_ref_and_ma20"),
        "compare": {
            "close_at_or_above_ref": "close>=level",
            "holds_ma20": "close>=level",
            "close_below_stop_line": "close<=level",
            "limit_down_touch": "low<=level",
            "close_below_ref_and_ma20": "close<all_levels",
        },
        "min_members_hit_divisor": 2,
        "states": ("verified", "partial", "unclear", "falsified"),
        # 一侧结论的合成读法(**也是条件集的一部分**,🟡-1 起纳入本快照:光锁「什么算
        # 命中」锁不住「缺一条阈值时这一侧算不算命中」,而后者同样决定判据松紧)。
        "side_logic": {
            "and_all_true": True, "and_one_false_one_none": False,
            "and_one_true_one_none": None, "and_empty": None,
            "or_one_true_one_none": True, "or_all_false": False,
            "or_all_false_one_none": None, "or_empty": None,
        },
    }
    actual = {
        "ruleset_version": vr.VERIFICATION_RULESET_VERSION,
        "require_all": tuple(vr.VERIFY_REQUIRE_ALL),
        "any_of": tuple(vr.INVALIDATE_ANY_OF),
        "compare": {c: vr.compare_of(c)
                    for c in tuple(vr.VERIFY_REQUIRE_ALL) + tuple(vr.INVALIDATE_ANY_OF)},
        "min_members_hit_divisor": vr.MIN_MEMBERS_HIT_DIVISOR,
        "states": vr.STATES,
        "side_logic": {
            "and_all_true": vr.combine_side([True, True], require_all=True),
            "and_one_false_one_none": vr.combine_side([False, None], require_all=True),
            "and_one_true_one_none": vr.combine_side([True, None], require_all=True),
            "and_empty": vr.combine_side([], require_all=True),
            "or_one_true_one_none": vr.combine_side([True, None], require_all=False),
            "or_all_false": vr.combine_side([False, False], require_all=False),
            "or_all_false_one_none": vr.combine_side([False, None], require_all=False),
            "or_empty": vr.combine_side([], require_all=False),
        },
    }
    assert actual == snapshot


def test_spec_version_and_ruleset_version_are_different_axes():
    """一个跟**形状**、一个跟**条件集**,⛔ 不许合并成一个串。"""
    assert bc.VERIFY_SPEC_VERSION != vr.VERIFICATION_RULESET_VERSION
    assert bc.INVALIDATE_SPEC_VERSION != vr.VERIFICATION_RULESET_VERSION


# ══════════════════════════════════════════════════════════════════════════
# 归属:引擎常量 + 白名单登记(⛔ 本版不进包)
# ══════════════════════════════════════════════════════════════════════════

_REPO = Path(__file__).resolve().parent.parent


def test_ruleset_is_registered_as_engine_constant_with_no_audit_backing_note():
    from tests.test_selection_primitives import _ENGINE_CONSTANT_WHITELIST

    key = ("verification_rules.py", "MIN_MEMBERS_HIT_DIVISOR")
    assert key in _ENGINE_CONSTANT_WHITELIST
    reason = _ENGINE_CONSTANT_WHITELIST[key]
    assert "临时默认" in reason and "零审计背书" in reason
    assert "插槽边界" in reason      # 要可配必须先扩 §12.2(用户拍板),不是顺手改


def test_ruleset_is_not_a_pack_key():
    """⛔ 本版不进包:包 schema 里不许出现验证 / 失效条件集的任何键。"""
    pack_src = (_REPO / "neckline" / "selection" / "pack.py").read_text(encoding="utf-8")
    for token in ("min_members_hit", "verification_spec", "invalidation",
                  "verify_ruleset", "close_below_ref_and_ma20"):
        assert token not in pack_src, f"pack.py 出现了 {token!r} —— 条件集不许进包(⑦-b-A)"
    for name in ("K4-pack.json", "K7-pack.json"):
        text = (_REPO.parent / "archive" / "packs_retired" / name).read_text(encoding="utf-8")
        assert "min_members_hit" not in text and "ruleset" not in text
