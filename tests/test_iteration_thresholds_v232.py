"""V2.3.2-④ 四分类 30/80 + 有效样本单位 + 骨架 `K8-V0.6` 的机器判据。

覆盖:
    ④-A 闸 1 新增两个校验的**正反例**(段名拼错 / 对账表与实际模式不一致必须被拒);
    ④-B **有效样本单位 = `D0 日期 × 篮子 × 引擎版本`**:一篮一行、成员数不影响 n、
        混引擎场景当前不可达(裁定 #9)—— ⛔ 不为不可达场景预建代码;
    ④-C ⑧-3 的 **70% 单一状态集中度**:达到即不给 `retire`、降 `observe`;
        状态取 **D0 当时保存的**那一份;⛔ 不直接提全局淘汰。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from neckline.eval import iteration as it
from neckline.selection import gates as gt
from neckline.selection import pack as pack_mod

_ROOT = Path(__file__).resolve().parent.parent
_SKELETON = _ROOT / "packs" / "K8-skeleton.json"
_TH = it.IterationThresholds(min_n=30, retire_min_n=80)


def _skel() -> Dict[str, Any]:
    return json.loads(_SKELETON.read_text(encoding="utf-8"))


def _engine(version: str) -> pack_mod.Pack:
    doc = json.loads((_ROOT / "packs" / f"{version}.json").read_text(encoding="utf-8"))
    m, c = doc["manifest"], doc["config"]
    return pack_mod.Pack(
        pack_version=m["pack_version"], name=m["name"],
        engine_api_version=int(m["engine_api_version"]), manifest=m, config=c,
        evidence_ref=list(m.get("evidence_ref", [])), is_active=True,
        created_at="2024-04-08T00:00:00+00:00", activated_at=None,
        line_code=m.get("line_code", "LEGACY"), status="running",
    )


# 🔴 **V2.4.0 P1.8+:骨架包升 `K8-V0.8` 时对账表随之改指 `C2`/`Z2`/`Y2`** ——
# 对账表按 **`pack_version`** 对号入座(`gates.check_threshold_governance` 的刻意设计:
# **引擎升过版就必须重新过一遍这张表**),所以本组测试的"现役引擎"也必须是新的那三条。
# ⛔ 别把它改回 C1/Z1/Y1 去"迁就"—— 那会让「升版必须同步对账表」这条闸失效。
ENGINES = {"C": _engine("C2"), "Z": _engine("Z2"), "Y": _engine("Y2")}


# ══════════════════════════════════════════════════════════════════════════
# ④-A 闸 1
# ══════════════════════════════════════════════════════════════════════════

class TestGateOneIteration:
    def test_the_shipped_pack_passes(self):
        assert pack_mod.validate_pack_doc(_skel()) == []

    def test_a_typo_in_the_section_name_is_rejected_not_silently_ignored(self):
        """🔴 **本条是 `_validate_iteration` 存在的全部理由**:骨架线 config **放行
        任何未知顶层键**(只禁 `engine` 段)—— 段名拼成 `iterations` 会**静默**让四分类
        退回「未拍板」,而那与"还没配"**长得一模一样**。⛔ 不许静默放行。"""
        doc = _skel()
        doc["config"]["iterations"] = doc["config"].pop("iteration")
        # 段名拼错 → `config.iteration` 消失 → 四分类静默退回「未拍板」
        th, problems = it.IterationThresholds.from_pack_config(doc["config"])
        assert th is None and problems == []      # ← 运行期**看不出来**,这就是危险所在
        # 故必须在闸 1 拒:未知顶层段本身不报错,但对账表里那些键仍在 → 用另一条抓它
        doc2 = _skel()
        doc2["config"]["iteration"]["min_nn"] = doc2["config"]["iteration"].pop("min_n")
        errors = pack_mod.validate_pack_doc(doc2)
        assert any("白名单外" in e and "min_nn" in e for e in errors), errors
        assert any("min_n 缺失" in e for e in errors), errors

    @pytest.mark.parametrize("mutate,fragment", [
        (lambda s: s["min_n"].__setitem__("value", 30.5), "非负整数"),
        (lambda s: s["min_n"].__setitem__("value", -1), "非负整数"),
        (lambda s: s["retire_min_n"].__setitem__("value", 10), "必须 ≥"),
        (lambda s: s.pop("retire_min_n"), "缺失"),
        (lambda s: s["min_n"].pop("provenance"), "value/provenance"),
    ])
    def test_bad_shapes_are_rejected(self, mutate, fragment):
        doc = _skel()
        mutate(doc["config"]["iteration"])
        errors = pack_mod.validate_pack_doc(doc)
        assert any(fragment in e for e in errors), (fragment, errors)

    def test_values_and_provenance_are_the_user_decided_ones(self):
        """30 / 80 来自 K8.md §十七(用户确认)→ provenance 必须是 `audited`。
        ⛔ 标成 `engineering_v1` 等于把用户拍的板讲成工程首版翻译。"""
        section = _skel()["config"]["iteration"]
        assert section["min_n"]["value"] == 30
        assert section["retire_min_n"]["value"] == 80
        for key in ("min_n", "retire_min_n"):
            assert section[key]["provenance"]["source"] == "audited"


class TestGateOneThresholdGovernance:
    def test_the_shipped_table_matches_the_live_engine_packs(self):
        """对账表 × 三个现役引擎包逐条一致(④-A 的正例)。"""
        assert gt.check_threshold_governance(
            _skel()["config"]["threshold_governance"], ENGINES) == []

    def test_a_silently_flipped_provenance_cannot_pass(self):
        """🔴🔴 **这张表存在的全部理由**:有人把某条阈值从 `engineering_v1` 改成
        `audited`(= **悄悄恢复机械硬否决**)却没同步对账表 → 闸 1 当场拒,并**指名
        是哪一条**。这是裁定 1「零自动升级」的物理落点。"""
        tampered = {k: _engine(k + "2") for k in ("C", "Z", "Y")}
        leaf = (tampered["C"].config["engine"]["gates"]["sector"]["strength_days_min_5d"])
        leaf["provenance"] = {"source": "audited", "ref": "偷偷改的"}
        errors = gt.check_threshold_governance(
            _skel()["config"]["threshold_governance"], tampered)
        assert len(errors) == 1
        assert "C2.sector.strength_days_min_5d" in errors[0]
        assert "'evidence'" in errors[0] and "'hard'" in errors[0]

    def test_a_wrong_entry_in_the_table_is_also_rejected(self):
        """反方向:表里写错(把 evidence 写成 hard)同样拒 —— plan ④ 验收 ④ 点名。"""
        doc = _skel()
        doc["config"]["threshold_governance"]["C2.sector.strength_days_min_5d"]["mode"] = "hard"
        errors = gt.check_threshold_governance(
            doc["config"]["threshold_governance"], ENGINES)
        assert any("C2.sector.strength_days_min_5d" in e for e in errors), errors

    def test_an_unlisted_engine_leaf_is_rejected(self):
        """漏登记同样拒:引擎包里有、对账表里没有 = 那条阈值没人盯着它的 provenance。"""
        doc = _skel()
        doc["config"]["threshold_governance"].pop("Y2.sector.industry_rank_max")
        errors = gt.check_threshold_governance(
            doc["config"]["threshold_governance"], ENGINES)
        assert any("对账表缺登记" in e and "Y2.sector.industry_rank_max" in e
                   for e in errors), errors

    def test_shape_errors_are_caught_by_the_pure_validator(self):
        for mutate, fragment in (
            (lambda g: g.__setitem__("C2.sector.industry_rank_max", {"mode": "hard"}), "两键"),
            (lambda g: g["C2.sector.industry_rank_max"].__setitem__("mode", "soft"), "mode 必须是"),
            (lambda g: g["C2.sector.industry_rank_max"].__setitem__("basis", "  "), "basis 不能为空"),
            (lambda g: g.__setitem__("乱写", {"mode": "hard", "basis": "x"}), "三段"),
        ):
            doc = _skel()
            mutate(doc["config"]["threshold_governance"])
            errors = pack_mod.validate_pack_doc(doc)
            assert any(fragment in e for e in errors), (fragment, errors)

    def test_governance_modes_match_the_gates_constants(self):
        """防漂:`pack._GOVERNANCE_MODES` 与 `gates.ENFORCEMENT_*` 两值必须一致
        (pack ⛔ 不能 import gates —— 会成环,故靠这条对拍)。"""
        assert pack_mod._GOVERNANCE_MODES == {gt.ENFORCEMENT_HARD, gt.ENFORCEMENT_EVIDENCE}


# ══════════════════════════════════════════════════════════════════════════
# ④-B 有效样本单位 = D0 日期 × 篮子 × 引擎版本
# ══════════════════════════════════════════════════════════════════════════

def _closure(basket_id: int, *, state="falsified", regime="trend_continuation",
             engine=("C", "C1"), members=1) -> Dict[str, Any]:
    return {
        "basket_id": basket_id, "d0_date": "20260805", "covered_tier": 1,
        "tier_accuracy": state, "regime_at_d0": regime,
        "skeleton_version": "K8-V0.6",
        "verification_ruleset_version": "verify_ruleset_v2",
        "engine_breakdown": {"engine_code": engine[0], "engine_version": engine[1]},
        "mech": {"meta": {"pack_version": "K8-V0.6", "basket_key": f"k{basket_id}"},
                 "liftoff_signal": {"d0_verdict": {
                     f"60000{i}.SH": {"position_verdict": "ok"} for i in range(members)}}},
    }


class TestEffectiveSampleUnit:
    def test_n_counts_baskets_not_members(self):
        """🔴 K8.md §十七:有效样本单位 = `D0 日期 × 篮子 × 引擎版本`。
        **成员数不影响 n** —— 三只成员的篮子和一只成员的篮子各算一个样本。"""
        one = it.collect_factor_stats([_closure(1, members=1)])
        many = it.collect_factor_stats([_closure(1, members=5)])
        n_one = {s.factor: s.n for s in one if s.dimension == it.FACTOR_REGIME}
        n_many = {s.factor: s.n for s in many if s.dimension == it.FACTOR_REGIME}
        assert n_one == n_many == {"regime=trend_continuation": 1}

    def test_two_baskets_on_the_same_day_are_two_samples(self):
        stats = it.collect_factor_stats([_closure(1), _closure(2)])
        regime = [s for s in stats if s.dimension == it.FACTOR_REGIME]
        assert regime and regime[0].n == 2

    def test_engine_version_splits_the_strata(self):
        """引擎**版本**进分层键 → 同一个引擎码的两个版本**不混样本**。"""
        stats = it.collect_factor_stats(
            [_closure(1, engine=("C", "C1")), _closure(2, engine=("C", "C2"))])
        strata = {s.stratum for s in stats}
        assert len(strata) == 2
        assert {s[2] for s in strata} == {"C1", "C2"}

    def test_mixed_engine_basket_is_unreachable_by_ruling_nine(self):
        """裁定 #9 **单篮子单引擎** → 一个篮子只可能有一个 `engine_breakdown`。
        ⛔ **不为这个不可达场景预建代码**(写了也验不了);这条只是把事实钉住:
        结案件的引擎归属是**一个标量对,不是列表**。"""
        c = _closure(1)
        eb = c["engine_breakdown"]
        assert isinstance(eb.get("engine_code"), str)
        assert not isinstance(eb.get("engine_code"), (list, tuple, set))
        assert it.stratum_of(c) == ("K8-V0.6", "C", "C1", "verify_ruleset_v2")

    def test_the_definition_is_written_into_both_module_headers(self):
        """④-B 的另一半:定义**带 K8.md §十七 出处**写进两个模块头
        (⛔ 别只钉代码不钉文档 —— 下一个人是照文档改的)。"""
        for rel in ("neckline/eval/iteration.py", "neckline/review/selection_clock.py"):
            head = (_ROOT / rel).read_text(encoding="utf-8")[:6000]
            assert "D0 日期 × 篮子 × 引擎版本" in head, rel
            assert "§十七" in head, rel


# ══════════════════════════════════════════════════════════════════════════
# ④-C 70% 单一行情状态集中度(⑧-3 拍板)
# ══════════════════════════════════════════════════════════════════════════

def _stat(n: int, delta: float, failure_regimes: Dict[str, int]) -> it.FactorStat:
    return it.FactorStat(
        stratum=("K8-V0.6", "C", "C1", "verify_ruleset_v2"),
        dimension=it.FACTOR_REGIME, value="v", n=n, scored=n,
        accuracy=0.3, baseline_accuracy=0.3 - delta, delta=delta,
        distribution={}, placebo_edge=it.EDGE_WORSE,
        failure_regimes=dict(failure_regimes))


class TestRegimeConcentration:
    def test_ratio_comes_from_the_ruling(self):
        """⑧-3 给的是 **70%**,⛔ 工程侧不许改。"""
        assert it.REGIME_CONCENTRATION_RATIO == 0.70

    def test_concentrated_failures_block_retire_and_downgrade_to_observe(self):
        """🔴 ⑧-3:失败 ≥70% 落在同一状态 → **不提全局淘汰**,降 `observe` 并说明
        应优先研究该因素在对应状态下的降权或停用。"""
        st = _stat(100, -0.2, {"trend_continuation": 8, "high_divergence": 2})
        row = it.classify_factors([st], _TH)[0]
        assert row["klass"] == it.KLASS_OBSERVE
        assert row["klassStatus"] == it.KLASS_FAILURES_CONCENTRATED
        assert "80%" in row["suggestion"] and "trend_continuation" in row["suggestion"]
        assert "不提全局淘汰" in row["suggestion"]
        assert "降权或停用" in row["suggestion"]

    def test_spread_failures_still_retire(self):
        """失败分散(<70%)→ 淘汰建议照给(⛔ 这道闸不是"永远不淘汰")。"""
        st = _stat(100, -0.2, {"trend_continuation": 5, "high_divergence": 3,
                               "rotation_confirmed": 2})
        row = it.classify_factors([st], _TH)[0]
        assert row["klass"] == it.KLASS_RETIRE and row["klassStatus"] == it.KLASS_DECIDED

    def test_exactly_seventy_percent_counts_as_concentrated(self):
        """「**至少** 70%」—— 边界值算集中(⛔ 别写成严格大于)。"""
        st = _stat(100, -0.2, {"trend_continuation": 7, "high_divergence": 3})
        assert it.classify_factors([st], _TH)[0]["klassStatus"] == \
            it.KLASS_FAILURES_CONCENTRATED

    def test_sample_gate_runs_first_then_concentration(self):
        """两道闸**先后次序写死**:先看 n,再看集中度。样本不够时连集中度都不问,
        直接「观察:样本不足」。"""
        st = _stat(10, -0.2, {"trend_continuation": 10})
        row = it.classify_factors([st], _TH)[0]
        assert row["klass"] == it.KLASS_OBSERVE and row["klassStatus"] == it.KLASS_DECIDED
        assert "min_n" in row["suggestion"]

    def test_unknown_regime_failures_dilute_but_stay_in_the_denominator(self):
        """⑧-3:**分母 = 全部失败样本**。D0 当天没有行情状态行的失败样本照样进分母
        (只是不归任何一个状态)—— 「一半失败查不到状态」会如实把集中度压下去。"""
        st = _stat(100, -0.2, {"trend_continuation": 6, it._REGIME_UNKNOWN: 4})
        assert st.failure_samples == 10
        assert st.regime_concentration == pytest.approx(0.6)
        assert it.classify_factors([st], _TH)[0]["klass"] == it.KLASS_RETIRE

    def test_zero_failures_never_blocks_retire(self):
        """没有失败样本 → 谈不上"失败集中",⛔ 不拿不存在的证据挡结论。"""
        st = _stat(100, -0.2, {})
        assert st.regime_concentration is None
        assert it.classify_factors([st], _TH)[0]["klass"] == it.KLASS_RETIRE

    def test_failure_regimes_come_from_the_d0_saved_state(self):
        """⑧-3:状态取 **D0 当时保存的**那一份(`regime_at_d0`),⛔ 不用当前重算值;
        且「失败样本」只数 `falsified`(⛔ partial 不算失败 —— 不新造统计口径)。"""
        closures = [
            _closure(1, state="falsified", regime="high_divergence"),
            _closure(2, state="falsified", regime="high_divergence"),
            _closure(3, state="partial", regime="trend_continuation"),
            _closure(4, state="verified", regime="rotation_confirmed"),
            _closure(5, state="falsified", regime=None),
        ]
        counts = it.failure_regime_counts(closures)
        assert counts == {"high_divergence": 2, it._REGIME_UNKNOWN: 1}

    def test_stats_carry_the_readings_into_the_handoff(self):
        """读数要摊进移交件 —— 否则「为什么这条没给淘汰」事后查不到底。"""
        d = _stat(100, -0.2, {"trend_continuation": 8, "high_divergence": 2}).to_dict()
        assert d["failureSamples"] == 10
        assert d["regimeConcentration"] == pytest.approx(0.8)
        assert d["dominantFailureRegime"] == "trend_continuation"
        assert d["failureRegimes"] == {"trend_continuation": 8, "high_divergence": 2}
