"""V2.2-③ 六道关口管线 `neckline/selection/gates.py` 单测(plan §五 ③ 测试清单逐条)。

覆盖:
    ① **六关各自 pass / degrade / reject 三态**(机械关只会 pass/reject,证据关只会
       pass/degrade —— ③-A 二分的机器判据);
    ② **机械关 reject → 硬否决**、**证据关 degrade → 只降档且仍在 ③b**(正反双向,
       ⑥ 侧的另一半在 `test_selection_tier.py`);
    ③ **引擎归属**:LLM 主张被机械对拍校验;缺席/给错 → C→Z→Y 机械兜底;成员不满足
       该引擎机械关阈值 → **直接出篮**;零运行引擎 → 当日不产任何候选(全部 ③b);
    ④ **缺数 = 不知道,不拦但不给 T1**(六关统一姿势);
    ⑤ 证据独立性:按 `evidence_kind` 归并、技术指标折一份、Z1 消息/政策类来源要求;
    ⑥ `gate_evaluations` 留痕:append-only、成员级 ts_code 语义、engine 列;
    ⑦ 门槛制正面钉子:**机械分 0.9+ 但位置关 falling → 不进任何档**(plan 点名);
    ⑧ 反向守门:gates.py 零 import `report.score_display` / `sentinel` / `selection.tier`。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Sequence

import pytest

from neckline.scan import landing as landing_mod
from neckline.scan import stage as stage_mod
from neckline.selection import aggregate as ag
from neckline.selection import gates as gt
from neckline.selection import pack as pack_mod
from neckline.selection import tier as ti
from tests.conftest import insert_trade_cal

D0 = date(2024, 4, 8)
D0_S = "20240408"
_PACKS_DIR = Path(__file__).resolve().parent.parent / "packs"


def _pack(filename: str) -> pack_mod.Pack:
    doc = json.loads((_PACKS_DIR / filename).read_text(encoding="utf-8"))
    m, c = doc["manifest"], doc["config"]
    return pack_mod.Pack(
        pack_version=m["pack_version"], name=m["name"],
        engine_api_version=int(m["engine_api_version"]), manifest=m, config=c,
        evidence_ref=list(m.get("evidence_ref", [])), is_active=True,
        created_at="2024-04-08T00:00:00+00:00", activated_at=None,
        line_code=m.get("line_code", "LEGACY"), status="running",
    )


C1 = _pack("C1.json")
Z1 = _pack("Z1.json")
Y1 = _pack("Y1.json")
ENGINES = {"C": C1, "Z": Z1, "Y": Y1}
SKELETON = _pack("K8-skeleton.json")


# —— 三条来源类别刻意不同的证据(独立 kind ×3;C1 `independent_evidence_min=3`)——
_EV3 = (
    ag.EvidenceItem(claim="发布产业扶持政策文件", source="某部委", date="2024-04-07"),
    ag.EvidenceItem(claim="公司公告签订重大合同", source="上市公司A", date="2024-04-06"),
    ag.EvidenceItem(claim="产业链上游开工率回升", source="财联社", date="2024-04-05"),
)


def _member(code: str, *, industry: Optional[str] = None,
            rs_rank: Optional[int] = None) -> ag.BasketMemberCandidate:
    return ag.BasketMemberCandidate(
        ts_code=code, role_llm="core", role_mech=None, role_conflict=0,
        reason="理由", industry=industry, rs_rank=rs_rank, name=code,
    )


def _basket(key: str, members, *, name: str = "篮", engine: Optional[str] = None,
            evidence=_EV3, evidence_status: str = ag.EVIDENCE_OK,
            answers: bool = True, pool: Optional[int] = 8) -> ag.BasketCandidate:
    return ag.BasketCandidate(
        trade_date=D0_S, basket_key=key, name=name, driver="共同驱动",
        driver_kind="theme", why_now="为什么是现在" if answers else "",
        seed_keys=("s-1",), members=tuple(members), evidence=tuple(evidence),
        evidence_status=evidence_status, pack_version="K8-V0.5",
        engine_api_version=ag.engine_api.ENGINE_API_VERSION, charter_version="v1.3.3",
        engine_code_llm=engine,
        common_trait="共同特征" if answers else "",
        persistence="逻辑持续性" if answers else "",
        strengthen_and_invalidate="强化与证伪" if answers else "",
        aux={"seed_pool_size": pool} if pool is not None else {},
    )


def _agg(baskets) -> ag.AggregateResult:
    return ag.AggregateResult(trade_date=D0_S, baskets=tuple(baskets),
                              pack_version="K8-V0.5", charter_version="v1.3.3")


def _insert_regime(db_path: Path, regime: str, *, breadth_pctile=None) -> None:
    from neckline.db import init_schema

    init_schema(db_path=db_path)
    inputs = {"breadth": {"available": breadth_pctile is not None, "pctile": breadth_pctile}}
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO market_regime_daily (trade_date, regime, regime_reason, "
            "inputs_json, strengthening_json, weakening_json, skeleton_version, computed_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (D0_S, regime, "test", json.dumps(inputs), "[]", "[]", "K8-V0.5", "now"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_landing(db_path: Path, states: Dict[str, str],
                    metrics: Optional[Dict[str, dict]] = None) -> None:
    from neckline.db import init_schema

    init_schema(db_path=db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        for code, state in states.items():
            m = (metrics or {}).get(code, {})
            conn.execute(
                "INSERT OR REPLACE INTO landing_state_daily (trade_date, ts_code, state, "
                "state_reason, metrics_json, skeleton_version, computed_at) VALUES (?,?,?,?,?,?,?)",
                (D0_S, code, state, "test", json.dumps(m), "K8-V0.5", "now"),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_strength_days(db_path: Path, days: Sequence[date],
                          rank_by_industry: Dict[str, int],
                          strength_by_industry: Dict[str, bool]) -> None:
    from neckline.db import init_schema
    from neckline.report.industry_strength import _MIN_MEMBERS, _STRENGTH_QUANTILE
    from neckline.report.industry_strength_store import TABLE

    init_schema(db_path=db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        for d in days:
            for ind, rank in rank_by_industry.items():
                conn.execute(
                    f"INSERT OR REPLACE INTO {TABLE} (trade_date, industry, median_ret, "
                    "member_count, industry_rank, is_strength_day, persist_days, quantile, "
                    "min_members, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (d.strftime("%Y%m%d"), ind, 0.01, 20, rank,
                     1 if strength_by_industry.get(ind) else 0, 1,
                     _STRENGTH_QUANTILE, _MIN_MEMBERS, "now"),
                )
        conn.commit()
    finally:
        conn.close()


def _ctx(env, codes: Sequence[str], **kw) -> gt.GateContext:
    ctx = gt.build_gate_context(D0, codes, db_path=env.db_path,
                                parquet_dir=env.parquet_dir, skeleton=SKELETON)
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


# ══════════════════════════════════════════════════════════════════════════
# ① 证据 kind 归并(机械口径)
# ══════════════════════════════════════════════════════════════════════════

class TestEvidenceKinds:
    def test_three_distinct_categories_are_three_independent_kinds(self):
        kinds, has_np = gt.independent_evidence_kinds(_EV3)
        assert len(kinds) == 3 and has_np

    def test_technical_indicators_fold_into_one_kind(self):
        """K8 原文「高度相关的技术指标按一份计」→ 技术类不论来源全折成一份。"""
        tech = (
            ag.EvidenceItem(claim="MACD 金叉且放量突破平台", source="来源甲", date="2024-04-07"),
            ag.EvidenceItem(claim="站上 20 日均线", source="来源乙", date="2024-04-06"),
        )
        kinds, has_np = gt.independent_evidence_kinds(tech)
        assert kinds == (gt.EVIDENCE_KIND_TECHNICAL,)
        assert has_np is False       # 纯技术 → 不算消息/政策类来源

    def test_same_source_same_category_counts_once(self):
        dup = (
            ag.EvidenceItem(claim="报道 A", source="财联社", date="2024-04-07"),
            ag.EvidenceItem(claim="报道 B", source="财联社", date="2024-04-06"),
        )
        kinds, _ = gt.independent_evidence_kinds(dup)
        assert len(kinds) == 1


# ══════════════════════════════════════════════════════════════════════════
# ② 市场关(机械):三态分支 + 缺行不拦不给 T1
# ══════════════════════════════════════════════════════════════════════════

class TestMarketGate:
    def test_primary_regime_passes(self, isolated_env):
        _insert_regime(isolated_env.db_path, "trend_continuation")
        c = gt._market_gate(C1, _ctx(isolated_env, []), [])
        assert c.verdict == gt.VERDICT_PASS and not c.blocks_t1 and c.available

    def test_missing_regime_row_does_not_block_but_bars_t1(self, isolated_env):
        """② 已定:缺行 = 不拦,但该票不得进 T1(⛔ 别写反)。"""
        c = gt._market_gate(C1, _ctx(isolated_env, []), [])
        assert c.verdict == gt.VERDICT_PASS
        assert c.available is False and c.blocks_t1 is True
        assert c.reason == "missing:market_regime"

    def test_c1_high_divergence_breadth_below_threshold_rejects(self, isolated_env):
        _insert_regime(isolated_env.db_path, "high_divergence", breadth_pctile=0.40)
        c = gt._market_gate(C1, _ctx(isolated_env, []), [])
        assert c.verdict == gt.VERDICT_REJECT
        assert c.score == pytest.approx(0.40) and c.threshold == pytest.approx(0.60)
        assert "breadth_pctile" in c.reason      # ③b「差多少」数值内嵌

    def test_c1_high_divergence_breadth_above_threshold_passes(self, isolated_env):
        _insert_regime(isolated_env.db_path, "high_divergence", breadth_pctile=0.75)
        c = gt._market_gate(C1, _ctx(isolated_env, []), [])
        assert c.verdict == gt.VERDICT_PASS and not c.blocks_t1

    def test_c1_rotation_confirmed_passes_but_blocks_t1(self, isolated_env):
        """C1 `rotation_confirmed_blocks_t1`:切换确认下不产 T1 —— 不是 reject。"""
        _insert_regime(isolated_env.db_path, "rotation_confirmed")
        c = gt._market_gate(C1, _ctx(isolated_env, []), [])
        assert c.verdict == gt.VERDICT_PASS and c.blocks_t1

    def test_z1_trend_continuation_requires_stage(self, isolated_env):
        env = isolated_env
        _insert_regime(env.db_path, "trend_continuation")
        ctx = _ctx(env, [], stage_of={"半导体": stage_mod.FERMENTATION,
                                      "纺织": stage_mod.EBB}, stage_available=True)
        ok = gt._market_gate(Z1, ctx, ["半导体"])
        bad = gt._market_gate(Z1, ctx, ["纺织"])
        assert ok.verdict == gt.VERDICT_PASS
        assert bad.verdict == gt.VERDICT_REJECT

    def test_z1_trend_continuation_without_stage_data_bars_t1_not_reject(self, isolated_env):
        _insert_regime(isolated_env.db_path, "trend_continuation")
        c = gt._market_gate(Z1, _ctx(isolated_env, []), ["半导体"])
        assert c.verdict == gt.VERDICT_PASS and c.available is False and c.blocks_t1


# ══════════════════════════════════════════════════════════════════════════
# ③ 板块关(机械)
# ══════════════════════════════════════════════════════════════════════════

class TestSectorGate:
    def _seed_strength(self, env, *, rank: int, strength: bool):
        days = [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3),
                date(2024, 4, 4), D0]
        insert_trade_cal(env, days)
        _insert_strength_days(env.db_path, days, {"半导体": rank}, {"半导体": strength})

    def test_c1_rank_and_strength_days_pass(self, isolated_env):
        self._seed_strength(isolated_env, rank=3, strength=True)
        c = gt._sector_gate(C1, _ctx(isolated_env, []), ["半导体"], pool_size=8)
        assert c.verdict == gt.VERDICT_PASS and c.available

    def test_c1_rank_too_low_rejects_with_gap(self, isolated_env):
        self._seed_strength(isolated_env, rank=30, strength=True)
        c = gt._sector_gate(C1, _ctx(isolated_env, []), ["半导体"], pool_size=8)
        assert c.verdict == gt.VERDICT_REJECT
        assert c.score == 30.0 and c.threshold == 10.0

    def test_c1_not_enough_strength_days_rejects(self, isolated_env):
        self._seed_strength(isolated_env, rank=3, strength=False)
        c = gt._sector_gate(C1, _ctx(isolated_env, []), ["半导体"], pool_size=8)
        assert c.verdict == gt.VERDICT_REJECT
        assert "strength_days" in c.reason

    def test_no_strength_table_bars_t1_not_reject(self, isolated_env):
        c = gt._sector_gate(C1, _ctx(isolated_env, []), ["半导体"], pool_size=8)
        assert c.verdict == gt.VERDICT_PASS and c.available is False and c.blocks_t1

    def test_z1_cluster_too_small_rejects(self, isolated_env):
        ctx = _ctx(isolated_env, [], stage_of={"半导体": stage_mod.IGNITION},
                   stage_available=True)
        c = gt._sector_gate(Z1, ctx, ["半导体"], pool_size=2)
        assert c.verdict == gt.VERDICT_REJECT
        assert c.score == 2.0 and c.threshold == 3.0


# ══════════════════════════════════════════════════════════════════════════
# ④ 位置关(机械,成员级):四态 + 引擎分支阈值 + 出篮
# ══════════════════════════════════════════════════════════════════════════

class TestPositionGate:
    def test_falling_member_is_removed(self, isolated_env):
        _insert_landing(isolated_env.db_path, {"600001.SH": landing_mod.FALLING})
        check, removed, t1 = gt._position_member_check(
            C1, _ctx(isolated_env, ["600001.SH"]), "600001.SH")
        assert check.verdict == gt.VERDICT_REJECT and removed and not t1

    def test_liftoff_with_depth_in_band_is_t1_capable(self, isolated_env):
        _insert_landing(isolated_env.db_path, {"600001.SH": landing_mod.LIFTOFF_CONFIRMED},
                        {"600001.SH": {"dist_from_high_60d": -0.10}})
        check, removed, t1 = gt._position_member_check(
            C1, _ctx(isolated_env, ["600001.SH"]), "600001.SH")
        assert check.verdict == gt.VERDICT_PASS and not removed and t1

    def test_c1_pullback_too_shallow_removes_member(self, isolated_env):
        """C1 回撤深度带 [-0.20, -0.05]:回撤太浅 = 不是「健康回调」→ 出篮。"""
        _insert_landing(isolated_env.db_path, {"600001.SH": landing_mod.LIFTOFF_CONFIRMED},
                        {"600001.SH": {"dist_from_high_60d": -0.02}})
        check, removed, _t1 = gt._position_member_check(
            C1, _ctx(isolated_env, ["600001.SH"]), "600001.SH")
        assert check.verdict == gt.VERDICT_REJECT and removed
        assert "pullback_depth" in check.reason

    def test_c1_landing_pending_stays_but_not_t1(self, isolated_env):
        _insert_landing(isolated_env.db_path, {"600001.SH": landing_mod.LANDING_PENDING},
                        {"600001.SH": {"dist_from_high_60d": -0.10}})
        check, removed, t1 = gt._position_member_check(
            C1, _ctx(isolated_env, ["600001.SH"]), "600001.SH")
        assert check.verdict == gt.VERDICT_PASS and not removed and not t1

    def test_z1_landing_pending_is_removed(self, isolated_env):
        """Z1 只认 `liftoff_confirmed`(右侧启动):落地进行中不满足该引擎阈值 → 出篮。"""
        _insert_landing(isolated_env.db_path, {"600001.SH": landing_mod.LANDING_PENDING})
        check, removed, _t1 = gt._position_member_check(
            Z1, _ctx(isolated_env, ["600001.SH"]), "600001.SH")
        assert check.verdict == gt.VERDICT_REJECT and removed

    def test_y1_platform_too_short_removes_member(self, isolated_env):
        _insert_landing(isolated_env.db_path, {"600001.SH": landing_mod.LIFTOFF_CONFIRMED},
                        {"600001.SH": {"platform_days": 12, "platform_amplitude": 0.10}})
        check, removed, _t1 = gt._position_member_check(
            Y1, _ctx(isolated_env, ["600001.SH"]), "600001.SH")
        assert check.verdict == gt.VERDICT_REJECT and removed
        assert check.score == 12.0 and check.threshold == 40.0

    def test_missing_landing_row_keeps_member_but_bars_t1(self, isolated_env):
        _insert_landing(isolated_env.db_path, {"600009.SH": landing_mod.LIFTOFF_CONFIRMED})
        check, removed, t1 = gt._position_member_check(
            C1, _ctx(isolated_env, ["600001.SH", "600009.SH"]), "600001.SH")
        assert check.verdict == gt.VERDICT_PASS and not removed and not t1
        assert check.available is False and check.reason == "missing:landing_state"

    def test_none_state_keeps_member_but_bars_t1(self, isolated_env):
        _insert_landing(isolated_env.db_path, {"600001.SH": landing_mod.NONE_STATE})
        check, removed, t1 = gt._position_member_check(
            C1, _ctx(isolated_env, ["600001.SH"]), "600001.SH")
        assert check.verdict == gt.VERDICT_PASS and not removed and not t1
        assert check.reason == "landing.none"


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 核心关 / 驱动关 / 证据关(证据关类:只 pass/degrade,⛔ 永不 reject)
# ══════════════════════════════════════════════════════════════════════════

class TestEvidenceClassGates:
    def test_core_rank_over_threshold_degrades_not_rejects(self):
        c = gt._core_member_check(C1, _member("600001.SH", rs_rank=7))
        assert c.verdict == gt.VERDICT_DEGRADE
        assert c.score == 7.0 and c.threshold == 3.0

    def test_core_rank_within_threshold_passes(self):
        assert gt._core_member_check(C1, _member("600001.SH", rs_rank=2)).verdict == gt.VERDICT_PASS

    def test_core_missing_rank_bars_t1_not_degrade(self):
        c = gt._core_member_check(C1, _member("600001.SH", rs_rank=None))
        assert c.verdict == gt.VERDICT_PASS and c.available is False and c.blocks_t1

    def test_driver_all_four_answers_pass(self):
        assert gt._driver_gate(_basket("k", [_member("600001.SH")])).verdict == gt.VERDICT_PASS

    def test_driver_missing_answers_degrade(self):
        c = gt._driver_gate(_basket("k", [_member("600001.SH")], answers=False))
        assert c.verdict == gt.VERDICT_DEGRADE
        assert "missing_answers" in c.reason

    def test_driver_search_unavailable_bars_t1_not_degrade(self):
        """「压根没搜」什么都不能说明(⑤ 的两态纪律)→ 不降级,但不给 T1。"""
        c = gt._driver_gate(_basket("k", [_member("600001.SH")],
                                    evidence=(), evidence_status=ag.EVIDENCE_SEARCH_UNAVAILABLE))
        assert c.verdict == gt.VERDICT_PASS and c.available is False and c.blocks_t1

    def test_evidence_three_kinds_pass_c1(self):
        assert gt._evidence_gate(C1, _basket("k", [_member("600001.SH")])).verdict == gt.VERDICT_PASS

    def test_evidence_too_few_kinds_degrade_with_gap(self):
        c = gt._evidence_gate(C1, _basket("k", [_member("600001.SH")], evidence=_EV3[:1]))
        assert c.verdict == gt.VERDICT_DEGRADE
        assert c.score == 1.0 and c.threshold == 3.0

    def test_z1_requires_a_news_policy_source(self):
        tech3 = (
            ag.EvidenceItem(claim="MACD 金叉", source="甲", date="2024-04-07"),
            ag.EvidenceItem(claim="研报首次覆盖给出评级", source="某证券研究所", date="2024-04-06"),
            ag.EvidenceItem(claim="研究报告上调目标价", source="另一研究所", date="2024-04-05"),
        )
        c = gt._evidence_gate(Z1, _basket("k", [_member("600001.SH")], evidence=tech3))
        assert c.verdict == gt.VERDICT_DEGRADE
        assert "no_news_policy_source" in c.reason


# ══════════════════════════════════════════════════════════════════════════
# ⑥ evaluate_day:引擎归属 + 出篮 + 除名 + 留痕
# ══════════════════════════════════════════════════════════════════════════

class TestEvaluateDay:
    def _liftoff(self, env, *codes):
        _insert_landing(env.db_path, {c: landing_mod.LIFTOFF_CONFIRMED for c in codes},
                        {c: {"dist_from_high_60d": -0.10} for c in codes})

    def test_llm_engine_claim_is_adopted_after_mech_check(self, isolated_env):
        env = isolated_env
        self._liftoff(env, "600001.SH")
        r = _agg([_basket("k1", [_member("600001.SH")], engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        b = out.result.baskets[0]
        assert (b.engine_code, b.engine_version, b.engine_source) == ("C", "C1", "llm")
        assert b.skeleton_version == "K8-V0.5"
        s = out.summaries["k1"]
        assert not s.excluded and s.engine_code == "C"

    def test_invalid_llm_engine_falls_back_mechanically_in_czy_order(self, isolated_env):
        env = isolated_env
        self._liftoff(env, "600001.SH")
        r = _agg([_basket("k1", [_member("600001.SH")], engine=None)])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        b = out.result.baskets[0]
        assert b.engine_code == "C" and b.engine_source == "mech_fallback"

    def test_member_failing_engine_mech_threshold_is_removed(self, isolated_env):
        """§2.9-C-4 对拍闸:LLM 说 C,篮内一名成员 falling → **那一只直接出篮**,
        篮子照留(⚠ 与 role_conflict「两说并存」刻意不同)。"""
        env = isolated_env
        _insert_landing(env.db_path,
                        {"600001.SH": landing_mod.LIFTOFF_CONFIRMED,
                         "600002.SH": landing_mod.FALLING},
                        {"600001.SH": {"dist_from_high_60d": -0.10}})
        r = _agg([_basket("k1", [_member("600001.SH"), _member("600002.SH")], engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        b = out.result.baskets[0]
        assert [m.ts_code for m in b.members] == ["600001.SH"]
        s = out.summaries["k1"]
        assert [rm.ts_code for rm in s.removed_members] == ["600002.SH"]
        assert not s.excluded

    def test_all_members_removed_exits_formal_candidacy(self, isolated_env):
        env = isolated_env
        _insert_landing(env.db_path, {"600001.SH": landing_mod.FALLING})
        r = _agg([_basket("k1", [_member("600001.SH")], engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        assert out.result.baskets == ()
        s = out.summaries["k1"]
        assert s.excluded and s.exclusion_reason == gt.EXCLUDE_MEMBERS_ALL_REMOVED
        assert s.stuck_gate == gt.GATE_POSITION and "falling" in (s.stuck_detail or "")

    def test_no_active_engines_means_no_candidates_today(self, isolated_env):
        """零运行引擎 = 当日不产任何候选(pack.get_active_line 既定语义);候选
        **不消失**,全部按 `no_active_engine` 留痕(③b 由 ⑥ 转出)。"""
        r = _agg([_basket("k1", [_member("600001.SH")], engine="C")])
        out = gt.evaluate_day(r, D0, db_path=isolated_env.db_path, engines={}, skeleton=SKELETON)
        assert out.result.baskets == ()
        assert out.summaries["k1"].excluded
        assert out.summaries["k1"].exclusion_reason == gt.EXCLUDE_NO_ACTIVE_ENGINE

    def test_mech_gate_reject_is_a_hard_veto(self, isolated_env):
        """机械关 reject → 硬否决(③-A 正向);证据关同场景只降级(反向在
        TestEvidenceClassGates 与 tier 测里锁)。"""
        env = isolated_env
        _insert_regime(env.db_path, "high_divergence", breadth_pctile=0.10)   # C1 → reject
        self._liftoff(env, "600001.SH")
        r = _agg([_basket("k1", [_member("600001.SH")], engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        s = out.summaries["k1"]
        assert s.excluded and s.exclusion_reason == gt.EXCLUDE_MECH_GATE_REJECTED
        assert s.stuck_gate == gt.GATE_MARKET
        assert "breadth_pctile" in (s.stuck_detail or "")
        assert out.result.baskets == ()

    def test_evidence_degrade_does_not_exclude_at_gate_level(self, isolated_env):
        """证据关 degrade **只降级**:gates 层照样保留候选(除不除名归 ⑥ 按
        `tier_evidence.t2` 上限判)。"""
        env = isolated_env
        self._liftoff(env, "600001.SH")
        r = _agg([_basket("k1", [_member("600001.SH")], evidence=_EV3[:1], engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        s = out.summaries["k1"]
        assert not s.excluded
        assert s.evidence_degrades == 1 and gt.GATE_EVIDENCE in s.degraded_gates
        assert len(out.result.baskets) == 1

    def test_t1_eligibility_needs_all_available_and_liftoff_and_regime(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3),
                               date(2024, 4, 4), D0])
        _insert_strength_days(env.db_path,
                              [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3),
                               date(2024, 4, 4), D0],
                              {"半导体": 1}, {"半导体": True})
        _insert_regime(env.db_path, "trend_continuation")
        self._liftoff(env, "600001.SH")
        r = _agg([_basket("k1", [_member("600001.SH", industry="半导体", rs_rank=1)],
                          engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        s = out.summaries["k1"]
        assert s.t1_eligible, (s.blocks_t1_reasons, s.degraded_gates)

        # 反向:抽掉 regime 行 → 同一篮不再 T1(不拦,只挡 T1)。
        conn = sqlite3.connect(str(env.db_path))
        conn.execute("DELETE FROM market_regime_daily")
        conn.commit()
        conn.close()
        out2 = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        s2 = out2.summaries["k1"]
        assert not s2.excluded and not s2.t1_eligible and s2.t2_eligible


# ══════════════════════════════════════════════════════════════════════════
# ⑦ gate_evaluations 留痕
# ══════════════════════════════════════════════════════════════════════════

class TestGateEvaluationsTable:
    def test_rows_written_with_member_level_ts_code_semantics(self, isolated_env):
        env = isolated_env
        _insert_landing(env.db_path, {"600001.SH": landing_mod.LIFTOFF_CONFIRMED},
                        {"600001.SH": {"dist_from_high_60d": -0.10}})
        r = _agg([_basket("k1", [_member("600001.SH", rs_rank=1)], engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        n = gt.save_gate_evaluations(out, db_path=env.db_path)
        rows = gt.load_gate_evaluations(D0, db_path=env.db_path)
        assert n == len(rows) == 6      # 四个篮子级 + 核心/位置各一成员行
        by_gate = {r0["gate"]: r0 for r0 in rows}
        assert set(by_gate) == set(gt.GATE_ORDER)
        for g in (gt.GATE_MARKET, gt.GATE_DRIVER, gt.GATE_SECTOR, gt.GATE_EVIDENCE):
            assert by_gate[g]["ts_code"] is None
        for g in (gt.GATE_CORE, gt.GATE_POSITION):
            assert by_gate[g]["ts_code"] == "600001.SH"
        assert {r0["engine_code"] for r0 in rows} == {"C"}
        assert {r0["engine_version"] for r0 in rows} == {"C1"}
        assert all(r0["gate_kind"] in ("mech", "llm") for r0 in rows)

    def test_rerun_appends_instead_of_overwriting(self, isolated_env):
        """append-only:同日重跑 = 追加新批次(审计表,「上一次怎么判的」本身是
        审计对象),⛔ 不覆盖。"""
        env = isolated_env
        r = _agg([_basket("k1", [_member("600001.SH")], engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        gt.save_gate_evaluations(out, db_path=env.db_path)
        gt.save_gate_evaluations(out, db_path=env.db_path)
        rows = gt.load_gate_evaluations(D0, db_path=env.db_path)
        assert len(rows) == 12

    def test_rejected_candidates_leave_rows_too(self, isolated_env):
        """硬否决的候选**恰恰最需要留痕**(③b 与 ④ 归因的原料)。"""
        env = isolated_env
        _insert_regime(env.db_path, "high_divergence", breadth_pctile=0.10)
        r = _agg([_basket("k1", [_member("600001.SH")], engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        assert out.result.baskets == ()
        gt.save_gate_evaluations(out, db_path=env.db_path)
        rows = gt.load_gate_evaluations(D0, db_path=env.db_path, candidate_key="k1")
        market = [r0 for r0 in rows if r0["gate"] == gt.GATE_MARKET]
        assert market and market[0]["verdict"] == gt.VERDICT_REJECT
        assert market[0]["score"] == pytest.approx(0.10)
        assert market[0]["threshold"] == pytest.approx(0.60)


# ══════════════════════════════════════════════════════════════════════════
# ⑧ 门槛制正面钉子(plan 测试清单点名的那一条,**真关口**版)
# ══════════════════════════════════════════════════════════════════════════

class TestHighScoreCannotBeatTheGates:
    def test_high_mech_score_with_falling_landing_enters_no_tier(self, isolated_env):
        """机械分拉满(板块第 1 + 龙头头名 + 零红牌)但位置关 `falling` → 成员出篮、
        候选退出正式候选,**不进任何档**;③b 说得出名/分/关/原因码(没消失)。"""
        env = isolated_env
        days = [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3), date(2024, 4, 4), D0]
        insert_trade_cal(env, days)
        _insert_strength_days(env.db_path, days, {"半导体": 1}, {"半导体": True})
        _insert_regime(env.db_path, "trend_continuation")
        _insert_landing(env.db_path, {"600001.SH": landing_mod.FALLING})
        r = _agg([_basket("k-hot", [_member("600001.SH", industry="半导体", rs_rank=1)],
                          engine="C", name="高分候选")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        res = ti.score_and_tier(r, D0, db_path=env.db_path, parquet_dir=env.parquet_dir,
                                pack=_pack("K7-pack.json"), gates_outcome=out)
        assert res.decisions == ()                       # 不进任何档
        hit = res.dropped[0]
        assert hit.basket_key == "k-hot" and hit.name == "高分候选"
        assert hit.reason == gt.EXCLUDE_MEMBERS_ALL_REMOVED
        assert hit.gate == gt.GATE_POSITION and "falling" in (hit.gate_detail or "")
        assert hit.mech_score is not None and hit.mech_score >= 0.6   # 分数真的高,仍然出局


# ══════════════════════════════════════════════════════════════════════════
# ⑨ 反向守门(静态)
# ══════════════════════════════════════════════════════════════════════════

_GATES_PATH = Path(__file__).resolve().parent.parent / "neckline" / "selection" / "gates.py"


def test_gates_never_imports_score_display_sentinel_or_tier():
    """V2.1-④ 方向性规则(gates 零 import `report.score_display`)+ 第〇原则
    (零 import `sentinel`)+ 防循环(零 import `selection.tier`,方向单一:
    tier → gates)。"""
    import ast

    tree = ast.parse(_GATES_PATH.read_text(encoding="utf-8"))
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    banned = ("neckline.report.score_display", "neckline.sentinel", "neckline.selection.tier")
    for m in mods:
        assert not any(m == b or m.startswith(b + ".") for b in banned), m


def test_gate_bisection_matches_ruling_six():
    """③-A 裁定 #6 的机器判据:机械关 = {市场, 板块, 位置},证据关 = {驱动, 核心,
    证据},二分互斥且覆盖六关。⛔ 不重开。"""
    assert gt.MECH_GATES == {gt.GATE_MARKET, gt.GATE_SECTOR, gt.GATE_POSITION}
    assert gt.EVIDENCE_GATES == {gt.GATE_DRIVER, gt.GATE_CORE, gt.GATE_EVIDENCE}
    assert gt.MECH_GATES | gt.EVIDENCE_GATES == set(gt.GATE_ORDER)
    assert not (gt.MECH_GATES & gt.EVIDENCE_GATES)
    assert all(gt.GATE_KIND_OF[g] == gt.GATE_KIND_MECH for g in gt.MECH_GATES)
    assert all(gt.GATE_KIND_OF[g] == gt.GATE_KIND_LLM for g in gt.EVIDENCE_GATES)
