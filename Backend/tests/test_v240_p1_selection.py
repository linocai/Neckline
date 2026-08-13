"""V2.4.0 **P1** 的机器判据(PROJECT_PLAN §五 V2.4.0 P1.1→P1.8+ 与「P1 验收用例」12 条)。

本文件按施工图的 12 条验收逐条编号成类,另加三组守门:

    · `TestP1OldPacksUnchanged` —— 🔴 **必保测试族**:`C1/Z1/Y1` 旧包**逐位行为不变**
      (回滚绳的第三条全靠它:代码 tag + DB 备份 + **旧四包仍可激活**);
    · `TestP1ComparisonDomainRuling` —— 2026-08-12 **裁定 #1**(候选 A)的机器判据:
      顺位只有 driver → industry,**题材域永不产出**,⛔ `ths_member` 一行不许进判定路径;
    · `TestP1SingleImplementation` —— 「唯一实现一处」的 AST / 文本守门。

⚠ **本文件不测 prompt 文案**(那在 `test_selection_aggregate.py` 的两组里),只测**行为**。
"""

from __future__ import annotations

import ast
import json
import sqlite3
from dataclasses import replace as dc_replace
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pytest

from neckline.selection import aggregate as ag
from neckline.selection import basket_store as bs
from neckline.selection import core_metrics as cm
from neckline.selection import gates as gt
from neckline.selection import pack as pack_mod
from neckline.selection import threshold_shadow as tsh
from neckline.selection import tier as ti
from tests.conftest import insert_trade_cal

D0 = date(2024, 4, 8)
D0_S = "20240408"
_ROOT = Path(__file__).resolve().parent.parent
_PACKS = _ROOT / "packs"


def _pack(filename: str) -> pack_mod.Pack:
    doc = json.loads((_PACKS / filename).read_text(encoding="utf-8"))
    m, c = doc["manifest"], doc["config"]
    return pack_mod.Pack(
        pack_version=m["pack_version"], name=m["name"],
        engine_api_version=int(m["engine_api_version"]), manifest=m, config=c,
        evidence_ref=list(m.get("evidence_ref", [])), is_active=True,
        created_at="2024-04-08T00:00:00+00:00", activated_at=None,
        line_code=m.get("line_code", "LEGACY"), status="running",
    )


C1, Z1, Y1 = _pack("C1.json"), _pack("Z1.json"), _pack("Y1.json")
C2, Z2, Y2 = _pack("C2.json"), _pack("Z2.json"), _pack("Y2.json")
SKELETON = _pack("K8-skeleton.json")
OLD_ENGINES = {"C": C1, "Z": Z1, "Y": Y1}
NEW_ENGINES = {"C": C2, "Z": Z2, "Y": Y2}

# 三条 kind 互不相同的证据(C1/C2 `independent_evidence_min=3`)。
_EV3 = (
    ag.EvidenceItem(claim="发布产业扶持政策文件", source="某部委", date="2024-04-07"),
    ag.EvidenceItem(claim="公司公告签订重大合同", source="上市公司A", date="2024-04-06"),
    ag.EvidenceItem(claim="产业链上游开工率回升", source="财联社", date="2024-04-05"),
)
_METRICS = {"close_over_ma20_dev": 0.01, "pct_chg": 1.2}
_CORE_METRICS = {"industry_member_count": 42, "industry_rs_rank_20d": 2}


def _member(code: str, *, role: str = "core", industry: str = "半导体",
            position: Optional[str] = ag.POSITION_OK,
            core: Optional[str] = ag.CORE_OK,
            metrics: Optional[Dict[str, Any]] = None,
            core_metrics: Optional[Dict[str, Any]] = None) -> ag.BasketMemberCandidate:
    return ag.BasketMemberCandidate(
        ts_code=code, role_llm=role, role_mech=None, role_conflict=0,
        reason="理由", industry=industry, name=code,
        position_verdict=position or "", position_reason="位置理由",
        position_metrics=dict(metrics if metrics is not None else _METRICS),
        position_metrics_missing="",
        core_verdict=core or "", core_reason="核心理由",
        core_metrics=dict(core_metrics if core_metrics is not None else _CORE_METRICS),
        core_metrics_missing="",
    )


def _basket(key: str, members, *, name: str = "篮", engine: str = "C",
            market: str = ag.MARKET_OK, sector: str = ag.SECTOR_OK,
            market_counter: Sequence[str] = (), sector_counter: Sequence[str] = (),
            market_missing: Sequence[str] = (),
            evidence=_EV3) -> ag.BasketCandidate:
    return ag.BasketCandidate(
        trade_date=D0_S, basket_key=key, name=name, driver="共同驱动",
        driver_kind="theme", why_now="为什么是现在", seed_keys=("s-1",),
        members=tuple(members), evidence=tuple(evidence),
        evidence_status=ag.EVIDENCE_OK, pack_version="K8-V0.8",
        engine_api_version=ag.engine_api.ENGINE_API_VERSION, charter_version="v2.3-k8",
        engine_code_llm=engine, common_trait="共同特征", persistence="持续性",
        strengthen_and_invalidate="强化与证伪", aux={"seed_pool_size": 8},
        market_verdict=market, market_reason="市场理由",
        sector_verdict=sector, sector_reason="板块理由",
        market_counter_evidence=tuple(market_counter),
        market_missing=tuple(market_missing),
        sector_counter_evidence=tuple(sector_counter),
    )


def _agg(baskets) -> ag.AggregateResult:
    return ag.AggregateResult(trade_date=D0_S, baskets=tuple(baskets),
                              search_stage=ag.STAGE_OK, reason_stage=ag.STAGE_OK,
                              pack_version="K8-V0.8", charter_version="v2.3-k8")


def _seed_world(env) -> None:
    """一个**四项 audited 硬门全过**的世界(行业名次 1 / 阶段 ignition / 广度 0.75),
    再加行情状态行 —— 六关能全过、T1 够得着。建表姿势照 `test_selection_gates.py`
    的既有 helper(⛔ 别自己少写列:那几张表有 NOT NULL 列)。"""
    from neckline.db import init_schema
    from neckline.report.industry_strength import _MIN_MEMBERS, _STRENGTH_QUANTILE

    days = [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3), date(2024, 4, 4), D0]
    insert_trade_cal(env, days)
    init_schema(db_path=env.db_path)
    conn = sqlite3.connect(str(env.db_path))
    try:
        for d in days:
            conn.execute(
                "INSERT OR REPLACE INTO industry_strength_daily (trade_date, industry, "
                "median_ret, member_count, industry_rank, is_strength_day, persist_days, "
                "quantile, min_members, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (d.strftime("%Y%m%d"), "半导体", 0.02, 20, 1, 1, 3,
                 _STRENGTH_QUANTILE, _MIN_MEMBERS, "now"))
        conn.execute(
            "INSERT OR REPLACE INTO market_regime_daily (trade_date, regime, regime_reason, "
            "inputs_json, strengthening_json, weakening_json, skeleton_version, computed_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (D0_S, "trend_continuation", "test",
             json.dumps({"breadth": {"available": True, "pctile": 0.75}}),
             "[]", "[]", "K8-V0.8", "now"))
        conn.execute(
            "INSERT OR REPLACE INTO industry_stage_daily (trade_date, industry, stage, "
            "is_strength_day, persist_days, limit_up_count, member_count, stage_reason, "
            "spec_fingerprint, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (D0_S, "半导体", "ignition", 1, 3, 5, 20, "test", "fp", "now"))
        conn.commit()
    finally:
        conn.close()


def _run(env, result, engines=NEW_ENGINES) -> gt.GateDayOutcome:
    return gt.evaluate_day(result, D0, db_path=env.db_path, engines=engines,
                           skeleton=SKELETON)


# ══════════════════════════════════════════════════════════════════════════
# P1 验收 1 / 3 / 4 / 5 —— 成员级 OUT(P1.4)
# ══════════════════════════════════════════════════════════════════════════

class TestP1MemberLevelOut:
    """🔴 **planner 点名的头号风险**:成员级 OUT 会改变 `t1/t2_eligible` 的语义面;
    若不同时改 `save_out_candidates`,被移除的成员会**既不在篮里、也不在 OUT 清单里**
    = 凭空消失,而且报告上看不出来。下面第一条就是那条正面守门。"""

    def test_1_leader_ok_elastic_unfit_keeps_the_basket(self, isolated_env):
        """P1 验收 1:`leader=ok + elastic=unfit` → **只移除 elastic,篮子保留**
        (且剩下的成员六关全过时**照样够得着 T1**)。"""
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [
            _member("600001.SH", role="leader"),
            _member("600002.SH", role="elastic", core=ag.CORE_UNFIT),
        ])
        out = _run(env, _agg([b]))
        s = out.summaries["k1"]
        assert s.kept_member_codes == ("600001.SH",)
        assert [r.ts_code for r in s.removed_members] == ["600002.SH"]
        assert not s.excluded and s.t2_eligible and s.t1_eligible
        assert [m.ts_code for m in out.result.baskets[0].members] == ["600001.SH"]
        # 🔴 `basket_key` 不变(共同驱动身份没变,K8 §七)
        assert out.result.baskets[0].basket_key == "k1"

    def test_removed_member_lands_in_out_candidates_not_thin_air(self, isolated_env):
        """🔴🔴 **头号风险的正面守门**:被移除的成员必须出现在**股票级 OUT 清单**里,
        带得出**出局关口**与**理由**;篮子里另两只**一行都不受影响**。"""
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [
            _member("600001.SH", role="leader"),
            _member("600002.SH", role="elastic", position=ag.POSITION_UNFIT),
            _member("600003.SH", role="core"),
        ])
        out = _run(env, _agg([b]))
        n = bs.save_out_candidates(D0, (), {"k1": b}, engine_by_key=out.summaries,
                                   db_path=env.db_path)
        assert n == 1
        rows = bs.load_out_candidates(D0, db_path=env.db_path)
        assert [r["ts_code"] for r in rows] == ["600002.SH"]
        assert rows[0]["out_gate"] == gt.GATE_POSITION
        assert rows[0]["out_reason"] == bs.MEMBER_OUT_REASON_POSITION
        assert "position" in (rows[0]["out_detail"] or "")
        assert rows[0]["engine_code"] == "C" and rows[0]["engine_version"] == "C2"
        # ⛔ 另两只既没被移除、也没被写进 OUT
        assert out.summaries["k1"].kept_member_codes == ("600001.SH", "600003.SH")

    def test_3_elastic_not_a_leader_but_ok_is_kept(self, isolated_env):
        """P1 验收 3:`elastic` 不是龙头,但模型判 `ok`(相关 + 位置合理)→ **保留**。
        (「不是龙头」不再是 `unfit` 的理由 —— 那是 P1.2 角色感知的直接后果。)"""
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [_member("600001.SH", role="elastic", core=ag.CORE_OK)])
        s = _run(env, _agg([b])).summaries["k1"]
        assert s.removed_members == () and s.kept_member_codes == ("600001.SH",)
        assert s.t1_eligible

    def test_4_two_unfit_gates_on_one_member_yield_one_row_with_gate_order(self, isolated_env):
        """P1 验收 4:同一成员核心关与位置关**同时** unfit → **一条**股票级 OUT
        (主原因按固定 `GATE_ORDER`:核心关在位置关之前),**全部理由仍可追溯**。"""
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [
            _member("600001.SH", role="leader"),
            _member("600002.SH", role="elastic", core=ag.CORE_UNFIT,
                    position=ag.POSITION_UNFIT),
        ])
        out = _run(env, _agg([b]))
        s = out.summaries["k1"]
        assert len(s.removed_members) == 1
        assert s.removed_members[0].gate == gt.GATE_CORE
        bs.save_out_candidates(D0, (), {"k1": b}, engine_by_key=out.summaries,
                               db_path=env.db_path)
        rows = bs.load_out_candidates(D0, db_path=env.db_path)
        assert len(rows) == 1 and rows[0]["out_reason"] == bs.MEMBER_OUT_REASON_CORE
        # 全部理由:两关的判定行都在(`gate_evaluations` 的原料)
        gone = {c.gate for c in s.checks if c.ts_code == "600002.SH"}
        assert gone == {gt.GATE_CORE, gt.GATE_POSITION}
        assert s.core_unfit and s.position_unfit

    def test_5_all_members_unfit_takes_the_whole_basket_out(self, isolated_env):
        """P1 验收 5:全部成员 unfit → 整篮 OUT(K8 §六 第 ④ 条)。"""
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [_member("600001.SH", core=ag.CORE_UNFIT),
                           _member("600002.SH", position=ag.POSITION_UNFIT)])
        out = _run(env, _agg([b]))
        s = out.summaries["k1"]
        assert s.excluded and s.exclusion_reason == gt.EXCLUDE_MEMBERS_ALL_REMOVED
        assert out.result.baskets == ()
        assert s.stuck_gate == gt.GATE_CORE       # GATE_ORDER 里最靠前的那一关

    def test_whole_basket_out_lists_every_member_with_its_own_reason(self, isolated_env):
        """整篮 OUT 时,**逐成员的出局原因不许被篮级原因盖掉**:各自被哪一关摘的,
        就写哪一条(⑨ 归因要分得开)。"""
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [_member("600001.SH", core=ag.CORE_UNFIT),
                           _member("600002.SH", position=ag.POSITION_UNFIT)])
        out = _run(env, _agg([b]))
        dropped = [ti.DroppedBasket(basket_key="k1", reason=gt.EXCLUDE_MEMBERS_ALL_REMOVED,
                                    mech_score=0.5, name="篮", gate=gt.GATE_CORE,
                                    gate_detail="全员出篮")]
        bs.save_out_candidates(D0, dropped, {"k1": b}, engine_by_key=out.summaries,
                               db_path=env.db_path)
        rows = {r["ts_code"]: r for r in bs.load_out_candidates(D0, db_path=env.db_path)}
        assert rows["600001.SH"]["out_reason"] == bs.MEMBER_OUT_REASON_CORE
        assert rows["600002.SH"]["out_reason"] == bs.MEMBER_OUT_REASON_POSITION


# ══════════════════════════════════════════════════════════════════════════
# P1 验收 2 —— 角色感知(P1.2)
# ══════════════════════════════════════════════════════════════════════════

class TestP1RoleAwareCore:
    def test_2_core_role_capacity_anchor_is_not_out_for_not_being_a_leader(self, isolated_env):
        """P1 验收 2:`core` 是容量中军、不是龙头 → 可判 `ok`,**不因非龙头 OUT**。

        ⚠ 判定本身在 LLM 侧(机械层只消费结论)—— 本条守的是**机械层不许再加一条
        「非龙头就出局」的暗门**:同一份读数下 `core_verdict=ok` 就是过。"""
        env = isolated_env
        _seed_world(env)
        # 读数刻意难看:行业内 20 日第 30 名(不是龙头),但模型按 `core` 角色判 ok
        b = _basket("k1", [_member("600001.SH", role="core", core=ag.CORE_OK,
                                   core_metrics={"industry_member_count": 42,
                                                 "industry_rs_rank_20d": 30})])
        s = _run(env, _agg([b])).summaries["k1"]
        assert s.removed_members == () and s.t1_eligible
        core = [c for c in s.checks if c.gate == gt.GATE_CORE][0]
        assert core.verdict == gt.VERDICT_PASS
        # 读数照旧原样留痕(⛔ 机械层不因"名次难看"改判)
        assert core.evidence["metrics"]["industry_rs_rank_20d"] == 30

    def test_prompt_criteria_are_role_split_not_one_ruler(self):
        """K8 §五-4 的三把尺必须逐字进 prompt;⛔ 「所有角色的 ok 都等于同行业龙头」
        那句(现役 prompt 的老写法)不许再出现。"""
        text = ag.K8_CORE_CRITERIA
        assert "leader" in text and "core" in text and "elastic" in text
        assert "⛔ 不要求最高弹性" in text and "⛔ 不要求它证明自己是龙头" in text
        assert "它就是它那一群(同行业)里的**龙头**" not in ag.BASKET_REASON_SYSTEM_PROMPT


# ══════════════════════════════════════════════════════════════════════════
# P1 验收 6 —— 缺失 ≠ 负面证据(P1.1)
# ══════════════════════════════════════════════════════════════════════════

class TestP1MissingIsNotEvidence:
    def test_6_missing_core_and_position_block_t1_keep_t2_and_never_out(self, isolated_env):
        """P1 验收 6:core/position 字段缺失 → **T1 被阻断,T2 保留,不 OUT**,
        且 🔴 **不计入 `evidence_degrades`**(K8 §五:缺失不构成负面证据)。"""
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [_member("600001.SH", core=None, position=None)])
        s = _run(env, _agg([b])).summaries["k1"]
        assert s.t2_eligible and not s.t1_eligible
        assert not s.excluded and s.removed_members == ()
        assert s.has_unavailable is True
        assert s.evidence_degrades == 0            # ⛔ 漏答不是降级
        assert s.degraded_gates == ()
        for gate in (gt.GATE_CORE, gt.GATE_POSITION):
            c = [x for x in s.checks if x.gate == gate][0]
            assert c.verdict == gt.VERDICT_PASS and c.available is False
            assert c.blocks_t1 is True

    def test_model_saying_unknown_is_the_same_state_as_omitting_it(self):
        """模型**明说** `unknown` 与**压根没答**必须落到同一个状态(K8 §五 五态表)。
        ⛔ 但落库的枚举没有被扩成四值:verdict 仍是空串,`available=False` 表达它。"""
        omit, *_ = ag._parse_core_check({}, code="600001.SH", name="篮")
        said, reason, *_ = ag._parse_core_check(
            {"core_check": {"verdict": "unknown", "reason": "读数不足"}},
            code="600001.SH", name="篮")
        assert omit == said == ""
        assert "读数不足" in reason
        assert set(ag.CORE_VERDICTS) == {"ok", "weak", "unfit"}     # ⛔ 枚举没被扩

    def test_weak_still_counts_as_a_degrade(self, isolated_env):
        """反向:`weak` **是**看过之后的疑点 → 照旧计入降级数。
        (若这条也变成 0,就说明把「有疑点」一起吞了。)"""
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [_member("600001.SH", core=ag.CORE_WEAK)])
        s = _run(env, _agg([b])).summaries["k1"]
        assert s.evidence_degrades == 1 and s.degraded_gates == (gt.GATE_CORE,)


# ══════════════════════════════════════════════════════════════════════════
# P1 验收 7 —— T2 新规则 + 影子台账(P1.6)
# ══════════════════════════════════════════════════════════════════════════

class TestP1TierPolicy:
    def _two_weak(self) -> ag.BasketCandidate:
        """两处 weak:核心关(成员级)+ 板块关(篮子级)。"""
        return _basket("k1", [_member("600001.SH", core=ag.CORE_WEAK)],
                       sector=ag.SECTOR_WEAK)

    def test_7_two_weaks_still_reach_t2_under_the_new_engines(self, isolated_env):
        """P1 验收 7:两个以上 weak → C2/Z2/Y2 下**仍可 T2**
        (K8 §八:未经校准的「最多一个降级」不得把篮子送入 OUT)。"""
        env = isolated_env
        _seed_world(env)
        s = _run(env, _agg([self._two_weak()])).summaries["k1"]
        assert s.evidence_degrades >= 2
        assert s.t2_formal_policy == gt.T2_POLICY_NO_HARD_FAIL
        assert s.t2_eligible is True and s.t1_eligible is False

    def test_7_same_input_would_have_failed_under_the_old_packs(self, isolated_env):
        """同一份输入喂旧包 → **旧规则会把它送进 OUT**。
        这条与上一条成对:证明"放宽"是真的发生了,不是测试自己造的假象。"""
        env = isolated_env
        _seed_world(env)
        s = _run(env, _agg([self._two_weak()]), engines=OLD_ENGINES).summaries["k1"]
        assert s.t2_formal_policy == ""
        assert s.t2_eligible is False

    def test_old_pack_drop_reason_is_the_real_one_not_the_removal_marker(self, isolated_env):
        """🔴 **归因不许撒谎**:旧包下「2 处降级 + 摘过一名成员」的篮子出局,真实原因是
        **证据关降级超上限**,⛔ 不是 `core_unfit` —— `core_unfit` 自 P1.4 起只是
        「本篮摘过人」的留痕位。老代码按它归因会把两件事讲成一件。"""
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [_member("600001.SH", core=ag.CORE_WEAK),
                           _member("600002.SH", core=ag.CORE_UNFIT)],
                    sector=ag.SECTOR_WEAK)
        out = _run(env, _agg([b]), engines=OLD_ENGINES)
        s = out.summaries["k1"]
        assert s.core_unfit is True and s.kept_member_codes == ("600001.SH",)
        res = ti.score_and_tier(_agg([b]), D0, db_path=env.db_path,
                                parquet_dir=env.parquet_dir, pack=SKELETON,
                                gates_outcome=out)
        assert [d.reason for d in res.dropped] == [ti.DROP_EVIDENCE_DEGRADED_OUT]
        assert ti.DROP_CORE_UNFIT not in {d.reason for d in res.dropped}
        # ⚠ 老码本身**仍然导出**(历史快照与 ⑨ 归因按码读回),只是新运行不再产生
        assert ti.DROP_CORE_UNFIT == "core_unfit"
        assert ti.DROP_POSITION_UNFIT == "position_unfit"

    def test_7_shadow_ledger_records_that_the_old_rule_would_have_failed(self, isolated_env):
        """P1 验收 7 后半:**影子台账记录旧规则会失败**
        (`threshold_shadow_evals` 的 `tier_evidence.t2.max_evidence_degrades` 行)。"""
        env = isolated_env
        _seed_world(env)
        out = _run(env, _agg([self._two_weak()]))
        tsh.save_threshold_shadow(out, tier_by_candidate={"k1": 2}, regime="trend_continuation",
                                  db_path=env.db_path)
        rows = [r for r in tsh.load_threshold_shadow(D0, D0, db_path=env.db_path)
                if r["threshold_key"] == gt.T2_SHADOW_THRESHOLD_KEY]
        assert len(rows) == 1
        assert rows[0]["would_pass"] == 0          # 旧规则本会把它 OUT 掉
        assert rows[0]["reading"] >= 2 and rows[0]["threshold_value"] == 1
        assert rows[0]["final_tier"] == 2          # 新规则下它实际拿到 T2

    def test_old_packs_write_no_shadow_row_for_a_live_rule(self, isolated_env):
        """旧包上 `max_evidence_degrades` **仍是活判据** → ⛔ 不写影子行
        (给一条活规则记"影子"= 同一件事记两遍,还看起来像它不生效了)。"""
        env = isolated_env
        _seed_world(env)
        out = _run(env, _agg([self._two_weak()]), engines=OLD_ENGINES)
        keys = {r.threshold_key for r in out.summaries["k1"].threshold_readings}
        assert gt.T2_SHADOW_THRESHOLD_KEY not in keys

    def test_t1_no_longer_depends_on_a_number(self, isolated_env):
        """P1.6:T1 由**结构条件**确定(六关全过 + 无 unavailable),
        ⛔ 不再看 `t1.max_evidence_degrades`。"""
        env = isolated_env
        _seed_world(env)
        s = _run(env, _agg([_basket("k1", [_member("600001.SH")])])).summaries["k1"]
        assert s.t1_eligible and s.evidence_degrades == 0
        src = (Path(gt.__file__)).read_text(encoding="utf-8")
        body = src.split("def t1_eligible", 1)[1].split("@property", 1)[0]
        assert "t1_max_evidence_degrades" not in body, (
            "T1 判据里又出现了数字型上限 —— P1.6 明写 T1 由结构条件确定")


# ══════════════════════════════════════════════════════════════════════════
# P1 验收 8 / 9 —— 篮子级 OUT 边界(P1.5)
# ══════════════════════════════════════════════════════════════════════════

class TestP1BasketLevelOutBoundary:
    def test_8_market_unfit_with_counter_evidence_takes_the_basket_out(self, isolated_env):
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [_member("600001.SH")], market=ag.MARKET_UNFIT,
                    market_counter=["主线指数连续三日缩量下行"])
        s = _run(env, _agg([b])).summaries["k1"]
        assert s.market_unfit and s.basket_level_unfit
        assert not s.t2_eligible and not s.t1_eligible

    def test_9_unfit_without_counter_evidence_is_clamped_to_weak(self, isolated_env):
        """P1 验收 9(其一):反证为空 → 夹成 `weak`,**不 OUT**。
        ⚠ 老结构(扁平 `*_verdict` + `*_reason`)进来时必然走这条 —— **刻意的保守方向**。"""
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [_member("600001.SH")], market=ag.MARKET_UNFIT)
        s = _run(env, _agg([b])).summaries["k1"]
        assert s.market_unfit is False and s.t2_eligible is True
        c = [x for x in s.checks if x.gate == gt.GATE_MARKET][0]
        assert c.evidence["unfit_clamped_to_weak"] == ["no_counter_evidence"]
        assert c.evidence["market_verdict_before_clamp"] == ag.MARKET_UNFIT

    def test_9_model_reported_missing_also_clamps(self, isolated_env):
        """P1 验收 9(其二):模型自己说「缺了什么」→ 那是保守输出,同样夹成 weak。"""
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [_member("600001.SH")], market=ag.MARKET_UNFIT,
                    market_counter=["主线缩量"], market_missing=["拿不到北向资金数据"])
        s = _run(env, _agg([b])).summaries["k1"]
        assert s.market_unfit is False
        c = [x for x in s.checks if x.gate == gt.GATE_MARKET][0]
        assert "model_reported_missing" in c.evidence["unfit_clamped_to_weak"]

    def test_member_gates_are_not_subject_to_the_clamp(self, isolated_env):
        """🔴 **边界**:四条件只管**市场关 / 板块关**(K8 §五 逐字)——
        ⛔ 不许推广到成员的核心关 / 位置关(K8 §六 没那么要求)。"""
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [_member("600001.SH"),
                           _member("600002.SH", core=ag.CORE_UNFIT)])   # 无 counter
        s = _run(env, _agg([b])).summaries["k1"]
        assert [r.ts_code for r in s.removed_members] == ["600002.SH"]


# ══════════════════════════════════════════════════════════════════════════
# P1 验收 10 —— 🔴 必保测试族:旧包逐位行为不变
# ══════════════════════════════════════════════════════════════════════════

class TestP1OldPacksUnchanged:
    """🔴 **回滚绳的第三条**(代码 tag + DB 备份 + **旧四包仍可激活**)全靠这一族。
    ⛔ 这里的断言一条都不许"为了让新行为好看"而放宽。

    🔴 **⚠ 类名会骗人 —— 它锁的只有「那一条 pack 开关」**(2026-08-12 独立复审 🟡-3
    指出,用户裁定 B 采纳):按包分叉的**只有** `tier_evidence.t2.formal_policy`;
    **P1.1 / P1.4 / P1.5 是代码级语义、对所有包生效**,C1/Z1/Y1 同样吃到 ——
    因为那三条修的是 **bug**(成员凭空消失 / 缺失当负证据),⛔ 把 bug 修复挂在
    版本号上等于留一个已知的错误档位。
    **实际后果**:「只回滚策略包」**回不到 v2.3.3 的选股行为**,要真回去必须走
    回滚绳 ②(`git checkout v2.3.3`)—— 详见 `PROJECT_PLAN.md` §五 前提 #8 收窄段
    与 P4.7-实测 ① 行。⚠ 同一件事的正面演示就在本文件的
    `test_old_pack_drop_reason_is_the_real_one_not_the_removal_marker`。"""

    def test_10_old_packs_carry_no_formal_policy_key(self):
        for pk in (C1, Z1, Y1):
            t2 = pk.config["engine"]["tier_evidence"]["t2"]
            assert gt.TIER_EVIDENCE_POLICY_KEY not in t2, pk.pack_version

    def test_10_new_packs_differ_from_old_ones_by_exactly_that_one_key(self):
        """🔴 **⛔ 不许借升版偷偷调参**:C2/Z2/Y2 的 `config` 去掉 `formal_policy`
        之后必须与 C1/Z1/Y1 **逐位相同**(阈值、provenance 一个字都不许动)。"""
        for old, new in ((C1, C2), (Z1, Z2), (Y1, Y2)):
            stripped = json.loads(json.dumps(new.config))
            popped = stripped["engine"]["tier_evidence"]["t2"].pop(
                gt.TIER_EVIDENCE_POLICY_KEY, None)
            assert popped == gt.T2_POLICY_NO_HARD_FAIL, new.pack_version
            assert stripped == old.config, f"{old.pack_version} → {new.pack_version} 有阈值被改动"
        # 线码仍是线码(闸 1 交叉校验:`engine_code` 必须与 `line_code` 逐位相等)
        for pk, code in ((C2, "C"), (Z2, "Z"), (Y2, "Y")):
            assert pk.config["engine"]["engine_code"] == code == pk.line_code

    def test_10_old_pack_t2_still_obeys_max_evidence_degrades(self, isolated_env):
        """旧包上「最多一处降级」**仍然是硬判据**(v2.3.3 行为逐位不变)。"""
        env = isolated_env
        _seed_world(env)
        one = _basket("k1", [_member("600001.SH", core=ag.CORE_WEAK)])
        two = _basket("k2", [_member("600002.SH", core=ag.CORE_WEAK)],
                      sector=ag.SECTOR_WEAK)
        out = _run(env, _agg([one, two]), engines=OLD_ENGINES)
        assert out.summaries["k1"].t2_eligible is True      # 1 处降级 ≤ 1
        assert out.summaries["k2"].t2_eligible is False     # 2 处降级 > 1

    def test_10_engine_api_version_stays_two(self):
        """⛔ 不许"顺手"升代际:升了会当场废掉「旧四包仍可激活」这条回滚绳
        (PROJECT_PLAN §3.14-G)。"""
        from neckline.selection import engine_api

        assert engine_api.ENGINE_API_VERSION == 2
        for pk in (C1, Z1, Y1, C2, Z2, Y2, SKELETON):
            assert pk.engine_api_version == 2, pk.pack_version

    def test_10_all_four_new_packs_pass_the_activation_schema_gate(self):
        """四个新包过闸 1 的 schema/兼容校验(激活演练的第一道)。"""
        for name in ("K8-skeleton.json", "C2.json", "Z2.json", "Y2.json"):
            doc = json.loads((_PACKS / name).read_text(encoding="utf-8"))
            assert pack_mod.validate_pack_doc(doc) == [], name

    def test_10_governance_table_follows_the_engine_version_bump(self):
        """🔴 对账表按 `pack_version` 对号入座 —— 骨架升版必须同步改指 C2/Z2/Y2,
        否则闸 1 当场拒(这正是那张表存在的理由)。"""
        gov = SKELETON.config["threshold_governance"]
        assert all(k.split(".")[0] in {"C2", "Z2", "Y2"} for k in gov), sorted(gov)
        assert gt.check_threshold_governance(gov, NEW_ENGINES) == []
        # 反向:拿旧引擎对拍必须报错(版本对不上)
        assert gt.check_threshold_governance(gov, OLD_ENGINES) != []


# ══════════════════════════════════════════════════════════════════════════
# P1 验收 11 / 12 —— 容量溢出不是 OUT · 历史只读
# ══════════════════════════════════════════════════════════════════════════

class TestP1OutLedgerBoundaries:
    def test_11_capacity_overflow_never_writes_out_candidates(self, isolated_env):
        """P1 验收 11(= 现役行为,本版**只是不许改坏**):K8 §八 末段。"""
        env = isolated_env
        b = _basket("k1", [_member("600001.SH")])
        dropped = [ti.DroppedBasket(basket_key="k1", reason=ti.DROP_CAPACITY_OVERFLOW,
                                    mech_score=0.7, name="篮")]
        n = bs.save_out_candidates(D0, dropped, {"k1": b}, db_path=env.db_path)
        assert n == 0 and bs.load_out_candidates(D0, db_path=env.db_path) == []
        assert ti.DROP_CAPACITY_OVERFLOW in bs.NON_OUT_REASONS

    def test_12_replay_never_updates_or_deletes_history(self, isolated_env):
        """P1 验收 12:历史 `gate_evaluations` / `out_candidates` **不被更新或删除**。
        ⚠ 两张表的纪律**刻意不同**:`gate_evaluations` 是**追加新批次**(同日重跑 =
        多一批,`created_at` 区分);`out_candidates` 是 `INSERT OR IGNORE` **幂等**
        (主键去重)。两者都**零 UPDATE / 零 DELETE**。"""
        env = isolated_env
        _seed_world(env)
        b = _basket("k1", [_member("600001.SH"),
                           _member("600002.SH", core=ag.CORE_UNFIT)])
        out = _run(env, _agg([b]))
        assert gt.save_gate_evaluations(out, db_path=env.db_path) > 0
        first_gate = gt.load_gate_evaluations(D0, db_path=env.db_path)
        assert bs.save_out_candidates(D0, (), {"k1": b}, engine_by_key=out.summaries,
                                      db_path=env.db_path) == 1
        first_out = bs.load_out_candidates(D0, db_path=env.db_path)

        gt.save_gate_evaluations(out, db_path=env.db_path)          # 重跑
        assert bs.save_out_candidates(D0, (), {"k1": b}, engine_by_key=out.summaries,
                                      db_path=env.db_path) == 0     # 幂等
        again_gate = gt.load_gate_evaluations(D0, db_path=env.db_path)
        again_out = bs.load_out_candidates(D0, db_path=env.db_path)
        # 老行**逐位仍在**(前缀相等),OUT 清单一行没多
        assert again_gate[:len(first_gate)] == first_gate
        assert len(again_gate) == 2 * len(first_gate)               # 追加新批次
        assert again_out == first_out


# ══════════════════════════════════════════════════════════════════════════
# 裁定 #1(2026-08-12):比较域只做 driver → industry
# ══════════════════════════════════════════════════════════════════════════

class TestP1ComparisonDomainRuling:
    """🔴 用户裁定 #1 逐字:「本版只实现『共同驱动域 → 行业域』。题材域明确记录
    `theme_domain_not_implemented`,⛔ 不得使用 `ths_member` 参与判定。」"""

    def test_driver_domain_wins_when_it_has_at_least_one_peer(self):
        d = cm.resolve_comparison_domain(
            "600001.SH", driver_peer_codes=("600001.SH", "600002.SH", "600003.SH"),
            driver_domain_key="s-1+s-2", industry="半导体",
            industry_peer_codes=("600001.SH", "600009.SH"))
        assert d["comparison_domain"] == cm.COMPARISON_DOMAIN_DRIVER
        assert d["comparison_domain_key"] == "s-1+s-2"
        assert d["peer_codes"] == ["600002.SH", "600003.SH"]     # ⛔ 不含自己
        assert d["peer_count"] == 2
        assert d["domain_fallback_reason"] == cm.DOMAIN_FALLBACK_NONE

    def test_zero_driver_peers_falls_through_theme_straight_to_industry(self):
        """回退触发条件 = 「除自己以外的同域成员数 = 0」(**定义,不是阈值**);
        ② 层被跳过 → `domain_fallback_reason='theme_domain_not_implemented'`。"""
        d = cm.resolve_comparison_domain(
            "600001.SH", driver_peer_codes=("600001.SH",), driver_domain_key="s-1",
            industry="半导体", industry_peer_codes=("600001.SH", "600009.SH"))
        assert d["comparison_domain"] == cm.COMPARISON_DOMAIN_INDUSTRY
        assert d["comparison_domain_key"] == "半导体"
        assert d["peer_codes"] == ["600009.SH"] and d["peer_count"] == 1
        assert d["domain_fallback_reason"] == cm.DOMAIN_FALLBACK_THEME_NOT_IMPLEMENTED

    def test_theme_domain_is_never_produced(self):
        """🔴 ② 层**永不产出**:穷举各种入参都不会出现 `theme`。"""
        for kwargs in (
            dict(driver_peer_codes=(), industry=None, industry_peer_codes=()),
            dict(driver_peer_codes=("600001.SH",), industry="半导体",
                 industry_peer_codes=("600001.SH",)),
            dict(driver_peer_codes=("600002.SH",), industry="半导体",
                 industry_peer_codes=("600009.SH",)),
        ):
            d = cm.resolve_comparison_domain("600001.SH", **kwargs)
            assert d["comparison_domain"] != cm.COMPARISON_DOMAIN_THEME

    def test_no_comparable_domain_is_a_third_state_not_a_lie(self):
        """①③ 都没有同域成员 → `comparison_domain=None`(**第三态**)+ 原因码,
        ⛔ 不许谎称"按行业比过了"。"""
        d = cm.resolve_comparison_domain("600001.SH", driver_peer_codes=("600001.SH",),
                                         industry=None, industry_peer_codes=())
        assert d["comparison_domain"] is None and d["peer_count"] == 0
        assert d["domain_fallback_reason"] == cm.DOMAIN_FALLBACK_NO_COMPARABLE_DOMAIN
        assert cm.DOMAIN_FALLBACK_THEME_NOT_IMPLEMENTED in d["domain_fallback_reason"]

    def test_five_audit_fields_are_always_present(self):
        for kwargs in (dict(driver_peer_codes=("600002.SH",)),
                       dict(industry="半导体", industry_peer_codes=("600009.SH",)),
                       dict()):
            d = cm.resolve_comparison_domain("600001.SH", **kwargs)
            assert set(cm.DOMAIN_AUDIT_KEYS) <= set(d)

    def test_domain_fields_ride_into_gate_evidence_json(self, isolated_env):
        """五个字段**恒落库**:随成员 `core_metrics` 进 `gate_evaluations.evidence_json`。"""
        env = isolated_env
        _seed_world(env)
        metrics = dict(_CORE_METRICS)
        metrics.update(cm.resolve_comparison_domain(
            "600001.SH", driver_peer_codes=("600002.SH", "600003.SH"),
            driver_domain_key="s-1"))
        b = _basket("k1", [_member("600001.SH", core_metrics=metrics)])
        out = _run(env, _agg([b]))
        gt.save_gate_evaluations(out, db_path=env.db_path)
        rows = [r for r in gt.load_gate_evaluations(D0, db_path=env.db_path)
                if r["gate"] == gt.GATE_CORE]
        stored = rows[0]["evidence"]["metrics"]
        assert stored["comparison_domain"] == "driver"
        assert stored["peer_count"] == 2 and stored["peer_codes"] == ["600002.SH", "600003.SH"]

    def test_ths_member_never_enters_the_selection_judgement_path(self):
        """🔴 裁定 #1 的硬判据:`ths_member`(概念板块成分)在**选股判定链**上零引用
        —— 本仓「概念板块只做展示、不再是任何判据的数据源」那条纪律因此完好无损。

        ⚠ **判据按 AST 取"真代码",⛔ 不扫注释与 docstring**:这几个模块的模块头
        **自己就写着**「⛔ 不得使用 `ths_member` 参与判定」—— 按文本扫会把那句禁令
        当成违规,而「一个对自己的注释报警的闸门等于没有闸门」(`CLAUDE.md` 登记过)。
        ⚠ 与 P0.7 #2 那类「**文案**类判据连注释一起扫」刻意相反 —— 两种口径别搞混。"""
        for module in (cm, ag, gt, ti):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    body = getattr(node, "body", [])
                    if (body and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        docstrings.add(id(body[0].value))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) in docstrings:
                        continue
                    assert "ths_member" not in node.value, (
                        f"{module.__name__}:{node.lineno} 出现了 ths_member 字面量")
                if isinstance(node, ast.Attribute):
                    assert "ths_member" not in node.attr, module.__name__
                if isinstance(node, ast.Name):
                    assert "ths_member" not in node.id, module.__name__

    def test_readings_stay_industry_scoped_and_the_legend_says_so(self):
        """⚠ **读数六项恒按行业算**(P1.3「现有读数原样保留」)—— 比较域是 driver 时
        它们**不是**驱动域内的名次;口径说明必须把这句话讲出来,否则模型会读错。"""
        assert "一律按该票所属行业算" in cm.CORE_METRIC_LEGEND
        assert "两者**可能不是同一群**" in cm.CORE_METRIC_LEGEND
        assert all(k.startswith("industry_") or k.startswith("cluster_")
                   or k == "consec_limit_up_days" for k in cm.CORE_METRIC_KEYS)


# ══════════════════════════════════════════════════════════════════════════
# 唯一实现守门
# ══════════════════════════════════════════════════════════════════════════

class TestP1SingleImplementation:
    def test_formal_policy_is_read_in_exactly_one_module(self):
        """P1.6:「`gates.py` 读它决定 T2 判据走哪条,**唯一实现一处**」。
        ⛔ 别在 tier.py / basket_card.py 再读一遍包 —— 那是第二个事实源。"""
        offenders: List[str] = []
        for path in sorted((_ROOT / "neckline").rglob("*.py")):
            if path.name in {"gates.py", "pack.py"}:
                continue                       # 判定实现 + schema 白名单,各一处
            src = path.read_text(encoding="utf-8")
            body = "\n".join(ln for ln in src.splitlines()
                             if not ln.strip().startswith("#"))
            if '"formal_policy"' in body or "'formal_policy'" in body:
                offenders.append(str(path.relative_to(_ROOT)))
        assert offenders == [], offenders

    def test_comparison_domain_has_a_single_resolver(self):
        """比较域顺位的判定只此一处(`core_metrics.resolve_comparison_domain`)。"""
        hits: List[str] = []
        for path in sorted((_ROOT / "neckline").rglob("*.py")):
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "resolve_comparison_domain":
                    hits.append(str(path.relative_to(_ROOT)))
        assert hits == ["neckline/selection/core_metrics.py"], hits

    def test_member_out_reason_codes_are_labelled_on_both_sides(self):
        """新原因码必须两侧都有中文标签(⛔ 别让界面印英文码)。"""
        from neckline.report.basket_daily import DROPPED_REASON_LABEL

        from tests.client_sources import models_text
        client = models_text()   # V2.4.0 P3.7:DTO 已拆六份,统一入口读
        for code in (bs.MEMBER_OUT_REASON_CORE, bs.MEMBER_OUT_REASON_POSITION):
            assert code in DROPPED_REASON_LABEL, code
            assert f'case "{code}"' in client, code

    def test_card_spec_version_bumped_with_the_shape(self):
        """P1.5+:形状真的变了才 bump —— 三处新键必须都在 `_gate_breakdown` 的产出里。"""
        from neckline.selection.basket_card import CARD_SPEC_VERSION

        assert CARD_SPEC_VERSION == "basket_card_v5"
        s = gt.BasketGateSummary(
            basket_key="k1",
            checks=(gt.GateCheck(gt.GATE_MARKET, gt.VERDICT_PASS, available=False,
                                 evidence={"support": ["a"], "counter_evidence": [],
                                           "missing": ["b"]}),),
            t2_formal_policy=gt.T2_POLICY_NO_HARD_FAIL, has_unavailable=True)
        node = ti._gate_breakdown(s)
        assert node["gate_available"] == {gt.GATE_MARKET: False}
        assert node["gate_support"] == {gt.GATE_MARKET: ["a"]}
        assert node["gate_missing"] == {gt.GATE_MARKET: ["b"]}
        assert node["t2_formal_policy"] == gt.T2_POLICY_NO_HARD_FAIL
        assert node["has_unavailable"] is True

    def test_old_v4_cards_still_read_back(self):
        """⛔ 老卡兼容:v4 快照没有这些新键 → 消费方按老形状读,**不许变成"缺件"**。"""
        old = {"gates": {"available": True, "verdicts": {"market": "pass"},
                         "evidence_degrades": 0, "degraded_gates": [], "blocks_t1": False}}
        node = old["gates"]
        assert "gate_available" not in node          # 老形状本来就没有
        # 客户端侧:缺键 → `gateAvailable` 空表 → 该关只按 verdict 画(⛔ 不猜成判不出)
        from tests.client_sources import models_text
        client = models_text()   # V2.4.0 P3.7:DTO 已拆六份,统一入口读
        block = client.split("struct BasketGates", 1)[1].split("\n}", 1)[0]
        assert 'obj["gate_available"]?.objectValue ?? [:]' in block
        assert "var gateAvailable: [String: Bool] = [:]" in block
