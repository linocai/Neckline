"""V2.2-③ 门槛制的编排接线(`evening._run_basket_segment`)+ 四件套(③-E)测试。

覆盖:
    ① **端到端(门槛制)**:③ 关口 → ⑥ 定档 → ⑦ 卡(先构建)→ 四件套校验 →
       事务 1 → 事务 2 —— 四件齐的 T1 保住 T1;`baskets` 三列引擎归属落库;
       `gate_evaluations` 有行;卡上引擎三键在。
    ② **四件套缺 → T1 降 T2(⛔ 不是拦截)**:use_llm=False(无剧本/无区间)时
       T1 资格候选降为 T2,篮子照落库、卡照出、降档理由留痕。
    ③ **卡构建整段炸 → 篮子照落库(有篮子无卡仍合法)**,T1 按「无预案」降档。
    ④ `enforce_plan_completeness` 纯函数:幂等 / 重排 / T2 满溢出。
    ⑤ `trade_plan_missing_pieces` / `member_trade_plan_missing`(判定唯一实现)。
    ⑥ ⑩ 开仓继承警示(`plan_incomplete_notice`)+ 周复盘 `planWarnings`。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pytest

from neckline.llm.base import LLMResult
from neckline.report import evening as ev
from neckline.selection import aggregate as ag
from neckline.selection import basket_card as bc
from neckline.selection import gates as gt
from neckline.selection import pack as pack_mod
from neckline.selection import tier as ti
from tests.conftest import insert_stock_basic, insert_trade_cal, write_daily_fixture
from tests.test_selection_gates import (
    _CORE_METRICS_OK, _EV3, _METRICS_OK, _insert_regime, _insert_strength_days,
)

D0 = date(2024, 4, 8)
D0_S = "20240408"
_PACKS_DIR = Path(__file__).resolve().parent.parent / "packs"
CODE = "600001.SH"


def _activate_all_lines(db_path: Path) -> None:
    for fname in ("K8-skeleton.json", "C1.json", "Z1.json", "Y1.json"):
        doc = json.loads((_PACKS_DIR / fname).read_text(encoding="utf-8"))
        pack_mod.activate_pack(doc["manifest"], doc["config"], via="seed", db_path=db_path)


def _member(code: str = CODE, *, position: str = ag.POSITION_OK,
            core: str = ag.CORE_OK) -> ag.BasketMemberCandidate:
    """⚠ 裁定 #11 / #12:位置关与核心关吃的都是 ⑤ 随成员带下来的 **LLM 判定 + 当次
    读数**(⛔ gates 不再读 `landing_metrics_daily`、也不再现算核心读数,夹具因此
    直接给读数)。"""
    return ag.BasketMemberCandidate(
        ts_code=code, role_llm="leader", role_mech=None, role_conflict=0,
        reason="理由", industry="半导体", rs_rank=1, name=code,
        position_verdict=position, position_reason="回撤到位后转强",
        position_metrics=dict(_METRICS_OK), position_metrics_missing="",
        core_verdict=core, core_reason="行业内 20 日第 1、当日领涨",
        core_metrics=dict(_CORE_METRICS_OK), core_metrics_missing="",
    )


def _basket(key: str = "k1", *, name: str = "篮",
            position: str = ag.POSITION_OK,
            core: str = ag.CORE_OK) -> ag.BasketCandidate:
    return ag.BasketCandidate(
        trade_date=D0_S, basket_key=key, name=name, driver="共同驱动",
        driver_kind="theme", why_now="为什么是现在", seed_keys=("s-1",),
        members=(_member(position=position, core=core),), evidence=_EV3,
        evidence_status=ag.EVIDENCE_OK,
        pack_version="K8-V0.5", engine_api_version=ag.engine_api.ENGINE_API_VERSION,
        charter_version="v1.3.3", engine_code_llm="C",
        common_trait="共同特征", persistence="持续性", strengthen_and_invalidate="强化与证伪",
        aux={"seed_pool_size": 8},
    )


def _agg(baskets) -> ag.AggregateResult:
    return ag.AggregateResult(trade_date=D0_S, baskets=tuple(baskets),
                              search_stage=ag.STAGE_OK, reason_stage=ag.STAGE_OK,
                              pack_version="K8-V0.5", charter_version="v1.3.3")


def _seed_t1_world(env) -> None:
    """把 C1 六关全过 + T1 资格所需的机械世界一次喂齐。"""
    _activate_all_lines(env.db_path)
    days = [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3), date(2024, 4, 4), D0]
    insert_trade_cal(env, days)
    _insert_strength_days(env.db_path, days, {"半导体": 1}, {"半导体": True})
    _insert_regime(env.db_path, "trend_continuation")
    # 卡的机械锚(收盘 + 涨跌停)。⚠ **两样缺一不可**:
    #   · `stock_basic` —— `basket_card.build_member_mech` 经 `load_stock_meta` 取
    #     board/is_st 才算得出涨跌停价;缺了 → limit_up/limit_down 皆 None →
    #     `clamp_entry_zone`/`clamp_exit_reference` 一律 `rejected_no_limit` →
    #     四件套永远不齐 → T1 永远拿不到。(这是**夹具**要求,不是产品判据松紧。)
    #   · `daily` 的 `vol`/`change`/`pct_chg` 三列 —— `strategy/features.add_features`
    #     硬要求(⑦-K7 标注件的面板走它),缺列整张面板抛 ColumnNotFoundError。
    insert_stock_basic(env, [{"ts_code": CODE, "name": "测试股", "industry": "半导体",
                              "market": "主板", "list_date": "20100101"}])
    write_daily_fixture(env, "daily", D0, [
        {"ts_code": CODE, "open": 10.0, "high": 10.4, "low": 9.8, "close": 10.0,
         "pre_close": 9.8, "change": 0.2, "pct_chg": 2.04, "vol": 10000.0, "amount": 1e5},
    ])
    write_daily_fixture(env, "limit_derived", D0, [
        {"ts_code": CODE, "board": "MAIN", "status": "none", "limit_pct": 0.1,
         "limit_up_price": 10.78, "limit_down_price": 8.82, "is_limit_up": False,
         "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 0},
    ])


class _CardStub:
    """卡 LLM 桩:返回带四件套的完整 payload。"""

    name, model = "stub", "stub-model"

    def chat(self, messages, *, enable_search=True, search_query=None, transport=None):
        payload = {
            "scripts": {"strong": "高开", "flat": "平开", "weak": "低开"},
            "entries": [{"ts_code": CODE, "low": 9.8, "high": 10.2, "max_chase": 10.5,
                         "exit_low": 12.0, "exit_high": 13.5, "why": "回踩中枢"}],
            "verification": "验证人话", "invalidation": "失效人话",
            "risks": ["风险一"], "tier_note": "档位合理",
        }
        body = json.dumps(payload, ensure_ascii=False)
        return LLMResult(ok=True, provider="stub", model="stub-model",
                         content="叙述。\n\n```json\n" + body + "\n```")


def _run_segment(env, result, monkeypatch, *, use_llm=False, card_provider=None):
    monkeypatch.setattr(ag, "aggregate_baskets", lambda *a, **k: result)
    stats: Dict[str, Any] = {}
    notes: List[str] = []
    dropped = ev._run_basket_segment(
        D0, seed_set=None, db_path=env.db_path, parquet_dir=env.parquet_dir,
        use_llm=use_llm, search_provider=None, reason_provider=None,
        tier_provider=None, card_provider=card_provider,
        transport=None, ledger=None, stats=stats, notes=notes,
    )
    return dropped, stats, notes


def _rows(db_path: Path, sql: str) -> List[tuple]:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# ① 端到端:四件齐 → T1 保住;引擎三列落库;留痕齐
# ══════════════════════════════════════════════════════════════════════════

class TestGatedEveningFlow:
    def test_complete_plan_keeps_t1_and_engine_columns_land(self, isolated_env, monkeypatch):
        env = isolated_env
        _seed_t1_world(env)
        dropped, stats, _notes = _run_segment(
            env, _agg([_basket()]), monkeypatch, use_llm=True, card_provider=_CardStub())
        assert dropped == []
        rows = _rows(env.db_path,
                     "SELECT basket_key, tier, engine_code, engine_version, skeleton_version "
                     "FROM baskets")
        assert rows == [("k1", 1, "C", "C1", "K8-V0.5")]
        assert stats["gates"]["rows_written"] > 0
        assert _rows(env.db_path, "SELECT COUNT(*) FROM gate_evaluations")[0][0] > 0
        card = json.loads(_rows(env.db_path, "SELECT card_json FROM basket_cards")[0][0])
        assert card["tier"] == 1
        assert (card["engine_code"], card["engine_version"], card["skeleton_version"]) == \
            ("C", "C1", "K8-V0.5")
        assert bc.trade_plan_missing_pieces(card) == []

    def test_incomplete_plan_demotes_t1_to_t2_not_blocked(self, isolated_env, monkeypatch):
        """③-E:四件套缺任一 → **不进 T1**(降 T2)+ 留痕;⛔ 不是拦截 ——
        篮子照落库、卡照出。use_llm=False = 无剧本/无区间,四件套天然缺。"""
        env = isolated_env
        _seed_t1_world(env)
        dropped, _stats, notes = _run_segment(env, _agg([_basket()]), monkeypatch,
                                              use_llm=False)
        assert dropped == []
        rows = _rows(env.db_path, "SELECT basket_key, tier, engine_code FROM baskets")
        assert rows == [("k1", 2, "C")]                       # 降 T2,没消失
        hist = json.loads(_rows(env.db_path,
                                "SELECT mech_breakdown_json FROM tier_history")[0][0])
        assert hist["t1_demoted_reason"].startswith(ti.T1_DEMOTED_PLAN_INCOMPLETE)
        card = json.loads(_rows(env.db_path, "SELECT card_json FROM basket_cards")[0][0])
        assert card["tier"] == 2                              # 卡的机械字段对齐最终裁定
        assert any(n.startswith(ti.T1_DEMOTED_PLAN_INCOMPLETE) for n in notes)

    def test_card_build_crash_still_saves_baskets(self, isolated_env, monkeypatch):
        """卡构建整段炸 → 「有篮子无卡」仍合法:篮子照落库(T1 按「无预案」降 T2),
        卡零张。"""
        env = isolated_env
        _seed_t1_world(env)
        monkeypatch.setattr(bc, "build_cards",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("卡炸了")))
        dropped, stats, _notes = _run_segment(env, _agg([_basket()]), monkeypatch,
                                              use_llm=False)
        assert dropped == []
        assert _rows(env.db_path, "SELECT tier FROM baskets") == [(2,)]
        assert _rows(env.db_path, "SELECT COUNT(*) FROM basket_cards")[0][0] == 0
        assert stats["basket"]["cards"] == 0

    def test_gate_excluded_basket_lands_in_dropped_and_handoff(self, isolated_env, monkeypatch):
        """机械关硬否决的候选:不落 `baskets`、进返回的 dropped(③b)与跨进程
        交接表 —— **没消失**。

        ⚠ 裁定 #11 后**位置关不再硬否决**,这条改用市场关构造(C1 在高位分歧下
        广度分位不够 → reject)。"""
        env = isolated_env
        _activate_all_lines(env.db_path)
        _insert_regime(env.db_path, "high_divergence", breadth_pctile=0.10)
        dropped, _stats, _notes = _run_segment(env, _agg([_basket(name="弱广度篮")]),
                                               monkeypatch, use_llm=False)
        assert [d.reason for d in dropped] == [gt.EXCLUDE_MECH_GATE_REJECTED]
        assert dropped[0].gate == gt.GATE_MARKET and dropped[0].name == "弱广度篮"
        assert _rows(env.db_path, "SELECT COUNT(*) FROM baskets")[0][0] == 0
        from neckline.selection.basket_dropped_handoff import load_dropped_handoff

        back = load_dropped_handoff(D0, db_path=env.db_path)
        assert back is not None and back[0].reason == gt.EXCLUDE_MECH_GATE_REJECTED
        assert back[0].gate == gt.GATE_MARKET and back[0].name == "弱广度篮"

    def test_position_unfit_basket_exits_candidacy_but_lands_in_3b(self, isolated_env,
                                                                   monkeypatch):
        """🔴 裁定 #11 在**整条编排链**上的机器判据:位置关判 `unfit` → 不落
        `baskets`,但 ③b 与跨进程交接表里**名 / 分 / 关 / 原因码 / 模型理由**齐全
        —— 票没消失,只是退出正式候选。"""
        env = isolated_env
        _seed_t1_world(env)
        dropped, _stats, _notes = _run_segment(
            env, _agg([_basket(name="位置不合适篮", position=ag.POSITION_UNFIT)]),
            monkeypatch, use_llm=False)
        assert [d.reason for d in dropped] == [ti.DROP_POSITION_UNFIT]
        assert dropped[0].gate == gt.GATE_POSITION and dropped[0].name == "位置不合适篮"
        assert CODE in (dropped[0].gate_detail or "")
        assert _rows(env.db_path, "SELECT COUNT(*) FROM baskets")[0][0] == 0
        from neckline.selection.basket_dropped_handoff import load_dropped_handoff

        back = load_dropped_handoff(D0, db_path=env.db_path)
        assert back is not None and back[0].reason == ti.DROP_POSITION_UNFIT
        # 关口留痕照写:位置关行是 LLM 关、且读数与理由都在
        rows = _rows(env.db_path,
                     "SELECT gate_kind, verdict, evidence_json FROM gate_evaluations "
                     "WHERE gate='position'")
        assert rows and rows[0][0] == "llm" and rows[0][1] == "degrade"
        ev = json.loads(rows[0][2])
        assert ev["position_verdict"] == ag.POSITION_UNFIT and ev["metrics"]


# ══════════════════════════════════════════════════════════════════════════
# ② enforce_plan_completeness 纯函数
# ══════════════════════════════════════════════════════════════════════════

def _decision(key: str, tier: int, rank: int, score: float) -> ti.TierDecision:
    return ti.TierDecision(basket_key=key, tier=tier, mech_score=score,
                           breakdown={}, rank_mech=rank, rank_in_tier=rank)


def _tr(decisions, dropped=()) -> ti.TierResult:
    return ti.TierResult(trade_date=D0_S, decisions=tuple(decisions),
                         dropped=tuple(dropped))


class TestEnforcePlanCompleteness:
    def test_noop_when_all_t1_plans_complete(self):
        r = _tr([_decision("a", 1, 1, 0.9), _decision("b", 2, 1, 0.5)])
        assert ti.enforce_plan_completeness(r, {"a": [], "b": []}) is r

    def test_missing_pieces_demote_to_t2_tail_with_renumbering(self):
        r = _tr([_decision("a", 1, 1, 0.9), _decision("b", 1, 2, 0.8),
                 _decision("c", 2, 1, 0.5)])
        out = ti.enforce_plan_completeness(r, {"a": [], "b": ["entry_zone:600001.SH"],
                                               "c": []})
        by_key = {d.basket_key: d for d in out.decisions}
        assert by_key["a"].tier == 1 and by_key["a"].rank_in_tier == 1
        assert by_key["b"].tier == 2                      # 降档
        assert by_key["c"].tier == 2
        t2 = sorted((d for d in out.decisions if d.tier == 2), key=lambda d: d.rank_in_tier)
        assert [d.basket_key for d in t2] == ["c", "b"]   # 原 T2 在前、降档篮殿后
        assert [d.rank_in_tier for d in t2] == [1, 2]
        assert by_key["b"].breakdown["t1_demoted_reason"].startswith(
            ti.T1_DEMOTED_PLAN_INCOMPLETE)
        assert any(n.startswith(ti.T1_DEMOTED_PLAN_INCOMPLETE) for n in out.notes)

    def test_t1_without_card_counts_as_fully_missing(self):
        out = ti.enforce_plan_completeness(_tr([_decision("a", 1, 1, 0.9)]), {})
        assert [d.tier for d in out.decisions] == [2]

    def test_t2_capacity_is_respected_on_demotion(self):
        t2 = [_decision(f"t2-{i}", 2, i + 1, 0.5 - i * 0.01) for i in range(5)]
        r = _tr([_decision("a", 1, 1, 0.9)] + t2)
        out = ti.enforce_plan_completeness(r, {"a": ["upside_script"]})
        assert all(d.tier == 2 for d in out.decisions)
        assert len([d for d in out.decisions if d.tier == 2]) == ti.TIER_CAPACITY[2]
        overflow = [d for d in out.dropped if d.basket_key == "a"]
        assert overflow and overflow[0].reason == ti.DROP_CAPACITY_OVERFLOW


# ══════════════════════════════════════════════════════════════════════════
# ③ 四件套判定(唯一实现)
# ══════════════════════════════════════════════════════════════════════════

class TestTradePlanPieces:
    _COMPLETE = {
        "scripts": {"strong": "s", "flat": "f", "weak": "w"},
        "invalidation_spec": {"spec_version": "x", "conditions": []},
        "members": [{"ts_code": CODE,
                     "entry_zone": {"low": 9.8, "high": 10.2, "why": ""},
                     "exit_reference": {"low": 12.0, "high": 13.5}}],
    }

    def test_complete_card_has_no_missing_pieces(self):
        assert bc.trade_plan_missing_pieces(self._COMPLETE) == []

    def test_each_piece_is_reported_individually(self):
        card = {**self._COMPLETE, "scripts": None,
                "members": [{"ts_code": CODE, "entry_zone": None,
                             "exit_reference": {"low": 12.0, "high": 13.5}}]}
        missing = bc.trade_plan_missing_pieces(card)
        assert "upside_script" in missing
        assert f"entry_zone:{CODE}" in missing
        assert "exit_reference" not in [m.split(":")[0] for m in missing if ":" not in m]

    def test_no_card_means_all_four_missing(self):
        assert set(m.split(":")[0] for m in bc.trade_plan_missing_pieces(None)) == \
            set(bc.TRADE_PLAN_PIECES)

    def test_member_view_matches_basket_view(self):
        m = self._COMPLETE["members"][0]
        assert bc.member_trade_plan_missing(self._COMPLETE, m) == []
        assert set(bc.member_trade_plan_missing(None, None)) == set(bc.TRADE_PLAN_PIECES)

    def test_label_is_human_readable(self):
        label = bc.trade_plan_missing_label(["upside_script", f"entry_zone:{CODE}"])
        assert "上涨判断" in label and "入场区间" in label


# ══════════════════════════════════════════════════════════════════════════
# ④ ⑩ 开仓继承警示 + 周复盘 planWarnings
# ══════════════════════════════════════════════════════════════════════════

class TestPlanIncompleteNotice:
    def test_inherited_plan_records_completeness_and_notice(self):
        from neckline.positions_entry import SourceBasketMember, build_inherited_plan
        from neckline.positions_entry import plan_incomplete_notice as notice

        card = dict(TestTradePlanPieces._COMPLETE)
        member_entry = card["members"][0]
        src = SourceBasketMember(
            basket_id=1, basket_key="k1", basket_name="篮", driver="驱动", tier=1,
            role_llm="leader", role_mech=None, role_conflict=False,
            card_version=1, card=card, member_entry=member_entry,
        )
        plan, _bid, _ver = build_inherited_plan(src, buy_price=10.0)
        assert plan["trade_plan_complete"] is True and plan["trade_plan_missing"] == []
        assert notice(plan) is None

        incomplete = SourceBasketMember(
            basket_id=1, basket_key="k1", basket_name="篮", driver="驱动", tier=1,
            role_llm="leader", role_mech=None, role_conflict=False,
            card_version=1, card={**card, "scripts": None}, member_entry=member_entry,
        )
        plan2, _b, _v = build_inherited_plan(incomplete, buy_price=10.0)
        assert plan2["trade_plan_complete"] is False
        assert "upside_script" in plan2["trade_plan_missing"]
        assert "上涨判断" in (notice(plan2) or "")

    def test_old_plans_without_the_key_are_not_retroactively_judged(self):
        from neckline.positions_entry import plan_incomplete_notice as notice

        assert notice({"available": True}) is None       # 老计划:没有该键 = 不追认
        assert notice(None) is None
        assert notice({"trade_plan_complete": None}) is None   # 无来源计划可验

    def test_weekly_review_collects_plan_warnings(self, isolated_env):
        from neckline.review.reconcile import _collect_plan_warnings

        env = isolated_env
        # 开仓走**生产写入口**(⛔ 不手搓 INSERT —— `positions` 有 NOT NULL 列,
        # 手搓夹具会跟着 DDL 漂;走 store 就永远与生产同形)。
        from neckline.positions_entry import create_position_plan_v1
        from neckline.sentinel.positions import open_position

        pid = open_position(CODE, 10.0, 100, D0, db_path=env.db_path)
        create_position_plan_v1(
            pid, {"trade_plan_complete": False, "trade_plan_missing": ["upside_script"]},
            source_basket_id=None, source_card_version=None, db_path=env.db_path,
        )
        warnings = _collect_plan_warnings(date(2024, 4, 8), date(2024, 4, 12),
                                          db_path=env.db_path)
        assert len(warnings) == 1
        assert CODE in warnings[0] and "上涨判断" in warnings[0]
        assert "非违纪" in warnings[0]

    def test_weekly_dict_carries_plan_warnings_key(self):
        from neckline.review.reconcile import WeeklyReview, weekly_review_dict

        r = WeeklyReview(week="2024-W15", week_start=date(2024, 4, 8),
                         week_end=date(2024, 4, 14))
        r.plan_warnings = ["警示一"]
        assert weekly_review_dict(r)["planWarnings"] == ["警示一"]
