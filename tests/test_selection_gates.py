"""V2.2-③ 六道关口管线 `neckline/selection/gates.py` 单测(plan §五 ③ 测试清单逐条)。

覆盖:
    ① **六关各自 pass / degrade / reject 三态**(机械关只会 pass/reject,证据关只会
       pass/degrade —— ③-A 二分的机器判据;🔴 裁定 #11 后位置关在证据关那一侧);
    ② **机械关 reject → 硬否决**、**证据关 degrade → 只降档且仍在 ③b**(正反双向,
       ⑥ 侧的另一半在 `test_selection_tier.py`);
    ③ **引擎归属**:LLM 主张被机械对拍校验;缺席/给错 → C→Z→Y 机械兜底;
       零运行引擎 → 当日不产任何候选(全部 ③b);
    ④ **缺数 = 不知道,不拦但不给 T1**(六关统一姿势);
    ⑤ 证据独立性:按 `evidence_kind` 归并、技术指标折一份、Z1 消息/政策类来源要求;
    ⑥ `gate_evaluations` 留痕:append-only、成员级 ts_code 语义、engine 列,
       **位置关行 `gate_kind='llm'` 且 `evidence_json` 同时存下读数与 LLM 理由**;
    ⑦ 门槛制正面钉子:**机械分 0.9+ 但机械关(板块/市场)被否 → 不进任何档**;
    ⑧ 🆕 **裁定 #11 的机器判据(正反双向)**:位置关在证据关集合里、`gate_kind='llm'`、
       verdict 只会 pass/degrade、`unfit` 的票 **⛔ 不得从 ③b 消失**、LLM 没给判定
       **⛔ 不静默当 ok**、**LLM 调用增量恒为 0**;
    ⑨ 反向守门:gates.py 零 import `report.score_display` / `sentinel` /
       `selection.tier` / **`scan.landing*`**。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Sequence

import pytest

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


# 一份"什么都取到了"的位置读数(键名 = `scan/landing.py::METRIC_KEYS` 契约)。
_METRICS_OK: Dict[str, object] = {
    "low5_over_low20_ratio": 1.03, "is_new_low_20d": False,
    "close_over_ma20_dev": 0.012, "close_over_platform_floor_dev": 0.034,
    "down_day_amount_ratio_5v20": 0.82, "max_daily_drop_5d": -0.031,
    "close_over_ma5_dev": 0.008, "pct_chg": 2.04, "rs5": 0.014,
    "dist_from_high_60d": -0.10, "cum_return_3d": 0.042, "is_limit_up": False,
    "is_new_high_60d": False, "platform_days": 12,
}


def _member(code: str, *, industry: Optional[str] = None,
            rs_rank: Optional[int] = None,
            position: Optional[str] = ag.POSITION_OK,
            position_reason: str = "回撤到位、量能收敛后转强",
            metrics: Optional[Dict[str, object]] = _METRICS_OK,
            ) -> ag.BasketMemberCandidate:
    """⚠ 裁定 #11 后位置关吃的是**⑤ 随成员带下来的 LLM 判定 + 当次读数**
    (⛔ gates 不再读 `landing_metrics_daily`)。`position=None` = 模型压根没给判定
    (下游必须保守按 weak 处理);`metrics=None` = 当次没有读数可喂。"""
    return ag.BasketMemberCandidate(
        ts_code=code, role_llm="core", role_mech=None, role_conflict=0,
        reason="理由", industry=industry, rs_rank=rs_rank, name=code,
        position_verdict=position or "", position_reason=position_reason,
        position_metrics=dict(metrics) if metrics is not None else None,
        position_metrics_missing="",
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
# ④ 位置关(🔴 裁定 #11:**证据关**,判定吃 LLM 输出,只降级不除名)
# ══════════════════════════════════════════════════════════════════════════

class TestPositionGate:
    def test_ok_passes_and_does_not_bar_t1(self):
        check, unfit = gt._position_member_check(C1, _member("600001.SH"))
        assert check.verdict == gt.VERDICT_PASS and not unfit
        assert check.available is True and check.blocks_t1 is False
        assert check.gate_kind == gt.GATE_KIND_LLM          # 位置关是 LLM 关(裁定 #11)

    def test_weak_degrades_and_never_rejects(self):
        """③-A:证据关**永不 reject**。位置勉强 → degrade(降一档),成员照留。"""
        check, unfit = gt._position_member_check(
            C1, _member("600001.SH", position=ag.POSITION_WEAK, position_reason="支撑刚破又收回"))
        assert check.verdict == gt.VERDICT_DEGRADE and not unfit
        assert check.verdict != gt.VERDICT_REJECT

    def test_unfit_degrades_but_flags_exit_from_formal_candidacy(self):
        """`unfit` 的 verdict 仍是 **degrade**(⛔ 不是硬否决,第 4 锁完好),
        「退出正式候选」由定档层执行(`t2_eligible=False`),票仍进 ③b。"""
        check, unfit = gt._position_member_check(
            C1, _member("600001.SH", position=ag.POSITION_UNFIT, position_reason="已在加速段"))
        assert check.verdict == gt.VERDICT_DEGRADE and unfit is True
        assert "已在加速段" in check.reason

    def test_missing_verdict_falls_back_to_weak_not_ok(self):
        """🔴 LLM 没给判定 → **保守按 weak + 留痕**,⛔ 不静默当 ok
        (「没判」与「判过、没问题」是两件事)。"""
        check, unfit = gt._position_member_check(
            C1, _member("600001.SH", position=None, position_reason=""))
        assert check.verdict == gt.VERDICT_DEGRADE and not unfit
        assert check.evidence["position_verdict"] == ag.POSITION_VERDICT_FALLBACK
        assert check.evidence["verdict_fallback"] is True

    def test_out_of_enum_verdict_also_falls_back_to_weak(self):
        check, _unfit = gt._position_member_check(
            C1, _member("600001.SH", position="excellent"))
        assert check.evidence["position_verdict"] == ag.POSITION_WEAK
        assert check.evidence["position_verdict_raw"] == "excellent"

    def test_ok_without_any_reading_does_not_block_but_bars_t1(self):
        """读数整份缺席 → `available=False` + 挡 T1:让模型在**零读数**下给的 ok
        直接换来 T1,等于拿"没有依据"当依据。⛔ 但不拦(缺数 = 不知道)。"""
        check, unfit = gt._position_member_check(
            C1, _member("600001.SH", metrics=None))
        assert check.verdict == gt.VERDICT_PASS and not unfit
        assert check.available is False and check.blocks_t1 is True
        assert "missing:position_metrics" in check.reason

    def test_evidence_json_carries_both_readings_and_llm_reason(self):
        """🔴 plan ③-C 末段的硬要求:位置关留痕**必须同时**有当次读数与 LLM 理由
        —— 判定不再是可回放的数字而是模型输出,少一样事后就无法复核。"""
        check, _unfit = gt._position_member_check(
            C1, _member("600001.SH", position_reason="回撤到位后放量转强"))
        ev = check.evidence
        assert ev["position_reason"] == "回撤到位后放量转强"
        assert ev["metrics"]["dist_from_high_60d"] == pytest.approx(-0.10)
        assert set(ev["metrics"]) == set(ag.POSITION_METRIC_KEYS)
        assert ev["metrics_available"] is True
        assert ev["position_guidance"]              # 该引擎的定性准则也留痕

    def test_engine_guidance_comes_from_the_pack_and_differs_per_engine(self):
        c = gt._position_member_check(C1, _member("600001.SH"))[0]
        z = gt._position_member_check(Z1, _member("600001.SH"))[0]
        assert c.evidence["position_guidance"] != z.evidence["position_guidance"]
        assert c.evidence["engine_code"] == "C" and z.evidence["engine_code"] == "Z"


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
    def test_llm_engine_claim_is_adopted_after_mech_check(self, isolated_env):
        env = isolated_env
        r = _agg([_basket("k1", [_member("600001.SH")], engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        b = out.result.baskets[0]
        assert (b.engine_code, b.engine_version, b.engine_source) == ("C", "C1", "llm")
        assert b.skeleton_version == "K8-V0.5"
        s = out.summaries["k1"]
        assert not s.excluded and s.engine_code == "C"

    def test_invalid_llm_engine_falls_back_mechanically_in_czy_order(self, isolated_env):
        env = isolated_env
        r = _agg([_basket("k1", [_member("600001.SH")], engine=None)])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        b = out.result.baskets[0]
        assert b.engine_code == "C" and b.engine_source == "mech_fallback"

    def test_position_gate_never_removes_a_member(self, isolated_env):
        """🔴 裁定 11-b 的正面判据:位置关**只降级不除名** —— 哪怕模型判 `unfit`,
        成员照样留在篮子里(⛔ 不出篮),候选也不在关口层被 excluded;
        「退出正式候选」发生在定档层,且票仍在 ③b 列名。"""
        env = isolated_env
        r = _agg([_basket("k1", [_member("600001.SH"),
                                 _member("600002.SH", position=ag.POSITION_UNFIT)],
                          engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        b = out.result.baskets[0]
        assert [m.ts_code for m in b.members] == ["600001.SH", "600002.SH"]
        s = out.summaries["k1"]
        assert s.removed_members == ()          # ⛔ 一个成员都没被摘掉
        assert not s.excluded                    # ⛔ 关口层不除名
        assert s.position_unfit is True and "600002.SH" in s.position_unfit_detail
        assert not s.t1_eligible and not s.t2_eligible   # 退出正式候选(定档层执行)

    def test_all_members_unfit_still_keeps_the_basket_at_gate_level(self, isolated_env):
        env = isolated_env
        r = _agg([_basket("k1", [_member("600001.SH", position=ag.POSITION_UNFIT)],
                          engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        assert len(out.result.baskets) == 1      # 篮子还在(⛔ 不是 members_all_removed)
        assert out.summaries["k1"].excluded is False
        assert out.summaries["k1"].position_unfit is True

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
        r = _agg([_basket("k1", [_member("600001.SH")], evidence=_EV3[:1], engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        s = out.summaries["k1"]
        assert not s.excluded
        assert s.evidence_degrades == 1 and gt.GATE_EVIDENCE in s.degraded_gates
        assert len(out.result.baskets) == 1

    def test_t1_eligibility_needs_all_available_and_position_ok_and_regime(self, isolated_env):
        env = isolated_env
        insert_trade_cal(env, [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3),
                               date(2024, 4, 4), D0])
        _insert_strength_days(env.db_path,
                              [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3),
                               date(2024, 4, 4), D0],
                              {"半导体": 1}, {"半导体": True})
        _insert_regime(env.db_path, "trend_continuation")
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
    def test_high_mech_score_with_sector_gate_rejected_enters_no_tier(self, isolated_env):
        """机械分不低(龙头头名 + 零红牌 + 板块有强度数据)但**板块关名次超限** →
        机械关硬否决,候选退出正式候选,**不进任何档**;③b 说得出名/分/关/原因码
        (没消失)。

        ⚠ **裁定 #11 后位置关不再是这条测试的抓手**(它已是只降级的证据关)——
        plan ③-D 测试清单原文点名改用市场关或板块关构造,照办。"""
        env = isolated_env
        days = [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3), date(2024, 4, 4), D0]
        insert_trade_cal(env, days)
        # 行业强度名次 30 > C1 的 industry_rank_max=10 → 板块关 reject(硬否决)
        _insert_strength_days(env.db_path, days, {"半导体": 30}, {"半导体": True})
        _insert_regime(env.db_path, "trend_continuation")
        r = _agg([_basket("k-hot", [_member("600001.SH", industry="半导体", rs_rank=1)],
                          engine="C", name="高分候选")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        res = ti.score_and_tier(r, D0, db_path=env.db_path, parquet_dir=env.parquet_dir,
                                pack=_pack("K7-pack.json"), gates_outcome=out)
        assert res.decisions == ()                       # 不进任何档
        hit = res.dropped[0]
        assert hit.basket_key == "k-hot" and hit.name == "高分候选"
        assert hit.reason == gt.EXCLUDE_MECH_GATE_REJECTED
        assert hit.gate == gt.GATE_SECTOR and "industry_rank" in (hit.gate_detail or "")
        assert hit.mech_score is not None and hit.mech_score >= 0.4   # 分数不低,仍然出局

    def test_market_gate_rejection_is_also_a_hard_veto_regardless_of_score(self, isolated_env):
        """第二个抓手(市场关):C1 在高位分歧下广度分位不够 → 硬否决。"""
        env = isolated_env
        days = [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3), date(2024, 4, 4), D0]
        insert_trade_cal(env, days)
        _insert_strength_days(env.db_path, days, {"半导体": 1}, {"半导体": True})
        _insert_regime(env.db_path, "high_divergence", breadth_pctile=0.10)
        r = _agg([_basket("k-hot", [_member("600001.SH", industry="半导体", rs_rank=1)],
                          engine="C", name="高分候选")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        res = ti.score_and_tier(r, D0, db_path=env.db_path, parquet_dir=env.parquet_dir,
                                pack=_pack("K7-pack.json"), gates_outcome=out)
        assert res.decisions == ()
        assert res.dropped[0].reason == gt.EXCLUDE_MECH_GATE_REJECTED
        assert res.dropped[0].gate == gt.GATE_MARKET


# ══════════════════════════════════════════════════════════════════════════
# ⑨ 反向守门(静态)
# ══════════════════════════════════════════════════════════════════════════

_GATES_PATH = Path(__file__).resolve().parent.parent / "neckline" / "selection" / "gates.py"


def test_gates_never_imports_score_display_sentinel_tier_or_landing():
    """V2.1-④ 方向性规则(gates 零 import `report.score_display`)+ 第〇原则
    (零 import `sentinel`)+ 防循环(零 import `selection.tier`,方向单一:
    tier → gates)+ 🆕 裁定 #11(零 import `scan.landing*`:位置关的读数由 ⑤ 随成员
    带进来 —— gates 另读一遍会存下「事后那一份」,与模型当时看到的可能不是同一份,
    留痕就白留了)。"""
    import ast

    tree = ast.parse(_GATES_PATH.read_text(encoding="utf-8"))
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    banned = ("neckline.report.score_display", "neckline.sentinel",
              "neckline.selection.tier", "neckline.scan.landing",
              "neckline.scan.landing_store")
    for m in mods:
        assert not any(m == b or m.startswith(b + ".") for b in banned), m


def test_gate_bisection_matches_rulings_six_and_eleven():
    """③-A 裁定 #6 的机器判据,**按裁定 #11 改判**:机械关 = {市场, 板块},
    证据关 = {驱动, 核心, **位置**, 证据};二分互斥且覆盖六关。

    🔴 位置关移入证据关后,**能硬否决的只剩两道读客观预计算量的关** —— 第〇原则
    第 4 锁「LLM 不做闸门」因此不但没被突破、反而更严。⛔ 不得改回。"""
    assert gt.MECH_GATES == {gt.GATE_MARKET, gt.GATE_SECTOR}
    assert gt.EVIDENCE_GATES == {gt.GATE_DRIVER, gt.GATE_CORE,
                                 gt.GATE_POSITION, gt.GATE_EVIDENCE}
    assert gt.GATE_POSITION not in gt.MECH_GATES          # ⛔ 反向:不得改回机械关
    assert gt.MECH_GATES | gt.EVIDENCE_GATES == set(gt.GATE_ORDER)
    assert not (gt.MECH_GATES & gt.EVIDENCE_GATES)
    assert all(gt.GATE_KIND_OF[g] == gt.GATE_KIND_MECH for g in gt.MECH_GATES)
    assert all(gt.GATE_KIND_OF[g] == gt.GATE_KIND_LLM for g in gt.EVIDENCE_GATES)


# ══════════════════════════════════════════════════════════════════════════
# ⑩ 🆕 裁定 #11 的机器判据(正反双向)
# ══════════════════════════════════════════════════════════════════════════

_AGG_PATH = Path(__file__).resolve().parent.parent / "neckline" / "selection" / "aggregate.py"


class TestRulingElevenMachineCriteria:
    def test_unfit_candidate_must_not_disappear_from_section_3b(self, isolated_env):
        """🔴 **本裁定最该有的一条**:位置关判 `unfit` 的票 **⛔ 不得从 ③b 消失**
        —— 只降级不除名(§2.9-C-2「退出正式候选 ≠ 从报告里消失」)。"""
        env = isolated_env
        days = [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3), date(2024, 4, 4), D0]
        insert_trade_cal(env, days)
        _insert_strength_days(env.db_path, days, {"半导体": 1}, {"半导体": True})
        _insert_regime(env.db_path, "trend_continuation")
        r = _agg([_basket("k-unfit",
                          [_member("600001.SH", industry="半导体", rs_rank=1,
                                   position=ag.POSITION_UNFIT,
                                   position_reason="已经拉开的加速段,不是落地起跳")],
                          engine="C", name="位置不合适篮")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        res = ti.score_and_tier(r, D0, db_path=env.db_path, parquet_dir=env.parquet_dir,
                                pack=_pack("K7-pack.json"), gates_outcome=out)
        assert res.decisions == ()                        # 退出正式候选
        assert len(res.dropped) == 1
        hit = res.dropped[0]
        assert hit.basket_key == "k-unfit" and hit.name == "位置不合适篮"   # ⛔ 没消失
        assert hit.reason == ti.DROP_POSITION_UNFIT       # 与"证据关降级超上限"分开
        assert hit.gate == gt.GATE_POSITION
        assert "600001.SH" in (hit.gate_detail or "")
        assert "加速段" in (hit.gate_detail or "")          # 模型那句理由也在 ③b 上

    def test_weak_only_demotes_one_notch_and_stays_a_candidate(self, isolated_env):
        """`weak` = 降一档:T1 拿不到、T2 还在(⛔ 不是出局)。"""
        env = isolated_env
        days = [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3), date(2024, 4, 4), D0]
        insert_trade_cal(env, days)
        _insert_strength_days(env.db_path, days, {"半导体": 1}, {"半导体": True})
        _insert_regime(env.db_path, "trend_continuation")
        r = _agg([_basket("k-weak",
                          [_member("600001.SH", industry="半导体", rs_rank=1,
                                   position=ag.POSITION_WEAK)], engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        s = out.summaries["k-weak"]
        assert not s.t1_eligible and s.t2_eligible
        assert gt.GATE_POSITION in s.degraded_gates and s.evidence_degrades == 1

    def test_position_row_is_llm_kind_and_stores_readings_plus_reason(self, isolated_env):
        """plan ③ 验收原文:位置关行 `gate_kind='llm'` 且 `evidence_json` **同时**
        存下当次读数与 LLM 理由。"""
        env = isolated_env
        r = _agg([_basket("k1", [_member("600001.SH", rs_rank=1,
                                         position_reason="回撤到位后放量转强")], engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        gt.save_gate_evaluations(out, db_path=env.db_path)
        rows = gt.load_gate_evaluations(D0, db_path=env.db_path, candidate_key="k1")
        pos = [r0 for r0 in rows if r0["gate"] == gt.GATE_POSITION]
        assert len(pos) == 1
        row = pos[0]
        assert row["gate_kind"] == gt.GATE_KIND_LLM
        assert row["ts_code"] == "600001.SH"
        assert row["verdict"] in (gt.VERDICT_PASS, gt.VERDICT_DEGRADE)   # ⛔ 永不 reject
        ev = row["evidence"]
        assert ev["position_reason"] == "回撤到位后放量转强"              # LLM 理由
        assert set(ev["metrics"]) == set(ag.POSITION_METRIC_KEYS)         # 当次读数
        assert ev["metrics"]["platform_days"] == 12

    def test_evidence_gates_never_produce_a_reject_verdict(self, isolated_env):
        """③-A 反向守门:四道证据关(含位置关)在**任何**输入下都不会产 reject。"""
        env = isolated_env
        r = _agg([_basket("k1", [_member("600001.SH", position=ag.POSITION_UNFIT),
                                 _member("600002.SH", position=None)],
                          evidence=(), evidence_status=ag.EVIDENCE_SEARCH_UNAVAILABLE,
                          answers=False, engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        for c in out.summaries["k1"].checks:
            if c.gate in gt.EVIDENCE_GATES:
                assert c.verdict != gt.VERDICT_REJECT, (c.gate, c.reason)

    def test_llm_call_count_stays_two_in_the_aggregate_layer(self):
        """🔴 成本铁律(附「成本与超时算术」第 1 条):位置判定**搭 `basket_reason`
        那一次**,⛔ 不新增任何 LLM 调用 —— ⑤ 里 `provider.chat(...)` 的调用点
        恒为 **2 个**(检索段 1 + 推理段 1)。AST 数,⛔ 不数字符串。"""
        import ast

        tree = ast.parse(_AGG_PATH.read_text(encoding="utf-8"))
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "chat"
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "provider"
        ]
        assert len(calls) == 2, [n.lineno for n in calls]
        # gates.py 本身零 LLM 调用(判定复用 ⑤ 的输出)
        gtree = ast.parse(_GATES_PATH.read_text(encoding="utf-8"))
        assert not [n for n in ast.walk(gtree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "chat"]

    def test_metric_key_contract_is_shared_with_the_scan_side(self):
        """键名契约两处对拍(防漂,同 `regime` 阈值键的既有体例):⑤ 读侧的
        `POSITION_METRIC_KEYS` 必须与写侧 `scan/landing.py::METRIC_KEYS` 逐个相等。"""
        from neckline.scan import landing as landing_mod

        assert tuple(ag.POSITION_METRIC_KEYS) == tuple(landing_mod.METRIC_KEYS)
        assert len(set(ag.POSITION_METRIC_KEYS)) == 14
