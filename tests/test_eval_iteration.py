"""V2.2-④-D 修改建议四分类(`neckline/eval/iteration.py`)。

🔴 **本文件最要紧的一组断言不是"分类算得对",是"分界线不在代码里"**
(CLAUDE.md「定性需求不许自行定量」,2026-08-09 立规):K8 §十七 只给了四句定性描述
(保留 / 观察 / 降权 / 淘汰),**一个数字都没有**。所以:

  · 骨架包没配 `config.iteration` 时,`classify_factors()` **一行都不分类**
    (`klass=None` + `klass_status='thresholds_undecided'`),⛔ 不猜、⛔ 不用默认值、
    ⛔ 不静默降级成 `observe`;
  · 边界用例(19/20/39/40)是**喂进去的**那两个数在起作用 —— 测的是判据逻辑,
    **不是**给这两个数背书;
  · 全仓静态守门:模块里没有任何形如 `min_n = <数字>` 的默认值。

另覆盖 plan ④-D 点名的「零写 `selection_packs`」(AST)。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from neckline.eval import iteration as it
from neckline.eval.metrics import LEGACY_ENGINE

from .conftest import source_code_only

_ROOT = Path(__file__).resolve().parent.parent
_MODULE = _ROOT / "neckline" / "eval" / "iteration.py"

_TH = it.IterationThresholds(min_n=20, retire_min_n=40)      # ← **测试自己喂的数**


# ══════════════════════════════════════════════════════════════════════════
# 造数
# ══════════════════════════════════════════════════════════════════════════

def _closure(basket_id, *, tier=1, state="verified", regime="trend_continuation",
             engine=("C", "C1"), skeleton="K8-V0.5", ruleset="verify_ruleset_v2",
             untriggered=None, position_verdict=None, d0="20260805", pack="K8-V0.5"):
    return {
        "basket_id": basket_id, "d0_date": d0, "d1_date": "20260806",
        "covered_tier": tier, "regime_at_d0": regime, "tier_accuracy": state,
        "untriggered_reason": untriggered, "closed_at": "t",
        "skeleton_version": skeleton, "verification_ruleset_version": ruleset,
        "engine_breakdown": {"engine_code": engine[0], "engine_version": engine[1]},
        "mech": {
            "meta": {"basket_key": f"k{basket_id}", "pack_version": pack},
            "driver_persistence": {"state": state},
            "core_strength": {"led": True},
            "untriggered_reason": {"triggered": untriggered is None},
            "liftoff_signal": {"available": True, "d0_verdict": (
                {"A.SZ": {"position_verdict": position_verdict}} if position_verdict else None)},
        },
    }


def _stat(n, delta, edge=it.EDGE_BETTER, *, dimension=it.FACTOR_TIER, value="T1"):
    return it.FactorStat(
        stratum=("K8-V0.5", "C", "C1", "verify_ruleset_v2"),
        dimension=dimension, value=value, n=n, scored=n,
        accuracy=(None if delta is None else 0.5 + delta), baseline_accuracy=0.5,
        delta=delta, distribution={}, placebo_edge=edge)


# ══════════════════════════════════════════════════════════════════════════
# 🔴 分界线不在代码里
# ══════════════════════════════════════════════════════════════════════════

class TestThresholdsAreNotOurs:
    def test_no_threshold_defaults_anywhere_in_the_module(self):
        """🔴 **头号红线的机器判据**:模块里不许出现 `min_n=<数字>` 这类默认值。

        `IterationThresholds` 的两个字段**没有默认值**(只能由
        `from_pack_config` 从包里读出来),故整份源码里不该存在把它们赋成常量的写法。"""
        code = source_code_only(_MODULE)
        for field in ("min_n", "retire_min_n"):
            hits = re.findall(rf"{field}\s*[:=]\s*(-?\d+)", code)
            assert not hits, (
                f"`{field}` 在 iteration.py 里被赋了字面量 {hits} —— K8 §十七 只给定性"
                f"描述,这个数必须由用户拍板后经四道闸进包(CLAUDE.md「定性需求不许自行定量」)")

    def test_dataclass_fields_have_no_defaults(self):
        with pytest.raises(TypeError):
            it.IterationThresholds()            # type: ignore[call-arg]

    def test_absent_section_means_no_thresholds_not_an_error(self):
        th, problems = it.IterationThresholds.from_pack_config({"seeds": {}, "tier": {}})
        assert th is None and problems == []    # 「还没拍板」不是配置错误

    def test_malformed_section_fails_loud_instead_of_degrading_to_absent(self):
        """「配错了」与「没配」必须分得开 —— 后者会自愈(等用户拍板),前者要人改。"""
        th, problems = it.IterationThresholds.from_pack_config(
            {"iteration": {"min_n": {"value": "二十"}, "retire_min_n": {"value": 40}}})
        assert th is None and problems and "min_n" in problems[0]

    def test_retire_line_below_min_line_is_rejected(self):
        th, problems = it.IterationThresholds.from_pack_config(
            {"iteration": {"min_n": {"value": 40}, "retire_min_n": {"value": 20}}})
        assert th is None and problems

    def test_well_formed_section_is_read_with_its_provenance(self):
        th, problems = it.IterationThresholds.from_pack_config({"iteration": {
            "min_n": {"value": 20, "provenance": {"source": "engineering_v1"}},
            "retire_min_n": {"value": 40, "provenance": {"source": "engineering_v1"}},
        }})
        assert problems == [] and th is not None
        assert (th.min_n, th.retire_min_n) == (20, 40)
        assert th.provenance["min_n"]["source"] == "engineering_v1"

    def test_the_shipped_skeleton_pack_has_no_iteration_section_yet(self):
        """**现状的机器判据**:`packs/K8-skeleton.json` 里**没有** `config.iteration`
        —— 那两个数还没有人拍板。⛔ 谁想"临时补一个"就会撞红这条。"""
        import json

        doc = json.loads((_ROOT / "packs" / "K8-skeleton.json").read_text(encoding="utf-8"))
        assert it.CONFIG_SECTION not in doc["config"]


class TestPendingIsNotAClass:
    def test_every_row_is_unclassified_when_the_lines_are_undecided(self):
        rows = it.classify_factors([_stat(5, 0.1), _stat(500, -0.3)], None)
        assert [r["klass"] for r in rows] == [None, None]
        assert {r["klassStatus"] for r in rows} == {it.THRESHOLDS_UNDECIDED}

    def test_pending_never_masquerades_as_observe(self):
        """🔴 「还没决定」与「样本不足」是两件事 —— 混成一句就是撒谎。"""
        rows = it.classify_factors([_stat(1, 0.1)], None)
        assert rows[0]["klass"] != it.KLASS_OBSERVE and rows[0]["klass"] is None

    def test_pending_row_says_exactly_which_numbers_are_missing(self):
        row = it.classify_factors([_stat(5, 0.1)], None)[0]
        assert "min_n" in row["suggestion"] and "retire_min_n" in row["suggestion"]
        assert "config.iteration" in row["suggestion"] or "骨架包" in row["suggestion"]
        assert "⛔ 系统不会替你选这两个数" in row["suggestion"]

    def test_statistics_are_still_there_while_pending(self):
        """分类给不了,**统计量照给** —— 那正是本块该交付的东西。"""
        row = it.classify_factors([_stat(37, 0.12)], None)[0]
        assert row["n"] == 37 and row["delta"] == pytest.approx(0.12)
        assert row["accuracy"] is not None and row["baselineAccuracy"] == 0.5


# ══════════════════════════════════════════════════════════════════════════
# 四分类边界(19/20/39/40)—— **喂进去的**那两个数在起作用
# ══════════════════════════════════════════════════════════════════════════

class TestClassificationBoundaries:
    def test_the_four_classes_are_exactly_k8s_four_words(self):
        assert it.KLASS_ORDER == ("keep", "observe", "downweight", "retire")

    @pytest.mark.parametrize("n,expected", [
        (19, it.KLASS_OBSERVE),      # < min_n(20)      → 样本不足
        (20, it.KLASS_KEEP),         # ≥ min_n 且优于基线 + 安慰剂 → 持续有效
    ])
    def test_min_n_boundary(self, n, expected):
        assert it.classify_factors([_stat(n, 0.1, it.EDGE_BETTER)], _TH)[0]["klass"] == expected

    @pytest.mark.parametrize("n,expected", [
        (39, it.KLASS_DOWNWEIGHT),   # < retire_min_n(40) → 只降权,⛔ 不淘汰
        (40, it.KLASS_RETIRE),       # ≥ retire_min_n     → 持续失效
    ])
    def test_retire_n_boundary(self, n, expected):
        assert it.classify_factors(
            [_stat(n, -0.1, it.EDGE_WORSE)], _TH)[0]["klass"] == expected

    def test_positive_delta_without_placebo_support_is_only_downweight(self):
        """「优于本层基线」但安慰剂对照没判赢 → **降权**,⛔ 不给 keep。"""
        for edge in (it.EDGE_WORSE, it.EDGE_INCONCLUSIVE, it.EDGE_UNAVAILABLE):
            row = it.classify_factors([_stat(50, 0.1, edge)], _TH)[0]
            assert row["klass"] == it.KLASS_DOWNWEIGHT

    def test_negative_delta_but_placebo_says_better_is_not_retired(self):
        row = it.classify_factors([_stat(50, -0.1, it.EDGE_BETTER)], _TH)[0]
        assert row["klass"] == it.KLASS_DOWNWEIGHT

    def test_unavailable_accuracy_is_observe_not_retire(self):
        """🔴 「算不出」≠「无效」(§3.8 老规矩)。"""
        row = it.classify_factors([_stat(500, None, it.EDGE_WORSE)], _TH)[0]
        assert row["klass"] == it.KLASS_OBSERVE
        assert row["klassStatus"] == it.STAT_UNAVAILABLE

    def test_every_classified_row_repeats_the_manual_loop(self):
        row = it.classify_factors([_stat(50, 0.1)], _TH)[0]
        assert "四道闸" in row["suggestion"] and "策略台" in row["suggestion"]

    def test_row_shape_matches_the_plan(self):
        row = it.classify_factors([_stat(50, 0.1)], _TH)[0]
        for key in ("factor", "klass", "n", "evidence", "suggestion"):
            assert key in row


# ══════════════════════════════════════════════════════════════════════════
# 分层与统计量
# ══════════════════════════════════════════════════════════════════════════

class TestStratumAndStats:
    def test_four_key_stratum(self):
        assert it.stratum_of(_closure(1)) == ("K8-V0.5", "C", "C1", "verify_ruleset_v2")

    def test_legacy_closure_keeps_its_own_layer(self):
        legacy = _closure(1, engine=(None, None), skeleton="K7-pack-v1")
        assert it.stratum_of(legacy) == ("K7-pack-v1", LEGACY_ENGINE, LEGACY_ENGINE,
                                         "verify_ruleset_v2")

    def test_accuracy_uses_the_registered_conversion_and_drops_unclear(self):
        rows = [_closure(1, state="verified"), _closure(2, state="partial"),
                _closure(3, state="falsified"), _closure(4, state="unclear")]
        acc, denom = it.accuracy_of(rows)
        assert denom == 3 and acc == pytest.approx(0.5)     # (1 + .5 + 0) / 3

    def test_accuracy_with_no_scorable_sample_is_none_not_zero(self):
        acc, denom = it.accuracy_of([_closure(1, state="unclear")])
        assert acc is None and denom == 0

    def test_factor_stats_cover_every_declared_dimension(self, isolated_env):
        rows = [_closure(1, position_verdict="ok", untriggered=None),
                _closure(2, tier=2, position_verdict="unfit",
                         untriggered="zone_not_reached", state="falsified")]
        stats = it.collect_factor_stats(rows, db_path=isolated_env.db_path)
        dims = {s.dimension for s in stats}
        assert it.FACTOR_REGIME in dims and it.FACTOR_TIER in dims
        assert it.FACTOR_ENGINE in dims and it.FACTOR_POSITION_VERDICT in dims
        assert it.FACTOR_UNTRIGGERED in dims

    def test_position_verdict_bucket_takes_the_worst_member(self):
        c = _closure(1)
        c["mech"]["liftoff_signal"]["d0_verdict"] = {
            "A.SZ": {"position_verdict": "ok"}, "B.SZ": {"position_verdict": "unfit"}}
        assert it._dominant_position_verdict(c) == "unfit"

    def test_stat_rows_are_deterministic(self, isolated_env):
        rows = [_closure(i) for i in range(1, 6)]
        a = [s.factor for s in it.collect_factor_stats(rows, db_path=isolated_env.db_path)]
        b = [s.factor for s in it.collect_factor_stats(list(reversed(rows)),
                                                       db_path=isolated_env.db_path)]
        assert a == b

    def test_placebo_edges_reuse_the_existing_verdict_never_reinvent(self):
        class _Rep:
            def __init__(self, pack, conclusive, real, rnd):
                self.pack_version = pack
                self.vs_random = {"conclusive": conclusive, "detail": {"real": real,
                                                                       "random": rnd}}
        edges = it.placebo_edges([_Rep("A", True, 0.05, 0.01), _Rep("B", True, 0.01, 0.05),
                                  _Rep("C", False, 0.05, 0.01)])
        assert edges == {"A": it.EDGE_BETTER, "B": it.EDGE_WORSE, "C": it.EDGE_INCONCLUSIVE}

    def test_inconclusive_is_never_upgraded_by_a_nicer_median(self):
        """样本没到既有结论线就是 `inconclusive` —— ⛔ 不因为"看起来更高"就说它更好。"""
        class _Rep:
            pack_version = "A"
            vs_random = {"conclusive": False, "detail": {"real": 9.9, "random": 0.0}}
        assert it.placebo_edges([_Rep()]) == {"A": it.EDGE_INCONCLUSIVE}


# ══════════════════════════════════════════════════════════════════════════
# 成绩单(K8 §十六 八项 / 六项)
# ══════════════════════════════════════════════════════════════════════════

class TestScoreboards:
    def test_selection_scoreboard_has_all_eight_items(self):
        board = it.selection_scoreboard([_closure(1), _closure(2, tier=2)])
        for key in it.SELECTION_ITEMS:
            assert key in board, key
        assert len(it.SELECTION_ITEMS) == 8
        assert board["tier_signal_accuracy"]["T1"]["n"] == 1

    def test_trade_scoreboard_has_all_six_items(self):
        board = it.trade_scoreboard([])
        for key in it.TRADE_ITEMS:
            assert key in board, key
        assert len(it.TRADE_ITEMS) == 6

    def test_efficiency_item_never_concludes(self):
        board = it.trade_scoreboard([])
        assert "⛔ 无阈值" in board["exit_quality_on_decay"]["note"]

    def test_position_verdict_bucket_is_the_p3_49_evidence_face(self):
        board = it.selection_scoreboard([_closure(1, position_verdict="ok"),
                                         _closure(2, position_verdict="unfit")])
        assert set(board["support_and_liftoff"]["by_position_verdict"]) == {"ok", "unfit"}
        assert "P3-49" in board["support_and_liftoff"]["note"]


# ══════════════════════════════════════════════════════════════════════════
# ⛔ 零写回
# ══════════════════════════════════════════════════════════════════════════

def test_module_never_writes_to_selection_packs():
    """🔴 V2.1 裁定 #3 一字不变 / K8 §十七「用户确认后才生效」:⛔ 零自动回写。"""
    code = source_code_only(_MODULE).upper()
    for banned in ("INSERT INTO SELECTION_PACKS", "UPDATE SELECTION_PACKS",
                   "DELETE FROM SELECTION_PACKS", "ACTIVATE_PACK", "SAVE_PACK"):
        assert banned not in code, f"iteration.py 出现写包路径:{banned}"


def test_module_has_no_write_sql_at_all():
    """更严一档:本模块**一句写 SQL 都没有**(它是纯读侧装配)。"""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in {"execute", "executemany", "executescript"}:
            continue
        arg = node.args[0] if node.args else None
        sql = arg.value.upper() if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else ""
        assert not any(v in sql for v in ("INSERT", "UPDATE", "DELETE", "REPLACE")), sql


def test_report_declares_the_thresholds_are_undecided(isolated_env):
    rep = it.build_iteration_report([_closure(1)], db_path=isolated_env.db_path)
    assert rep["thresholds"]["available"] is False
    assert "拍板" in rep["thresholds"]["unavailableReason"]
    assert rep["strataKey"] == ["skeletonVersion", "engineCode", "engineVersion",
                                "rulesetVersion"]
    assert "⛔ 本模块零写回选股包" in rep["disclaimer"]
    assert all(r["klass"] is None for r in rep["suggestions"])


def test_resolve_thresholds_with_no_active_skeleton_is_none(isolated_env):
    th, problems = it.resolve_thresholds(db_path=isolated_env.db_path)
    assert th is None and problems == []
