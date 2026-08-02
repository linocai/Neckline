"""③Tier 分层引擎 `neckline/selection/tier.py` 单测(plan §五 V2-⑥ 验收逐条)。

覆盖(与 plan 验收清单一一对应):
    ① **定档可复现** —— 同输入两次运行 → tier 与序逐位相同(含分数并列的确定性
       tie-break:`basket_key` 升序,不靠行序)。
    ② **跨档微调被拒** —— LLM 想把篮子挪档 → 整条丢弃 + WARNING;LLM 只改档内序。
    ③ **T1 空态是合法输出**(市场混沌时不许凑数)+ **容量上限不被突破**。
    ④ **`_TIER_SCORE_INPUTS` 白名单 + 运行期访问锁** —— 特征行里同时装着 LLM 产出
       字段,机械分一个都不许读。
    ⑤ **换包 → 序跟着变**,且 `mech_breakdown_json` 能解释变化。
    ⑥ K7 四条:六态打分读包(禁硬编)/ 阶段缺行取中性分且**断言不是 0** /
       `leader_clarity` 随 `rs_rank` 单调衰减 + 三级 tie-break / K4-pack vs K7-pack
       同输入不同序(「插槽不是空架子」在 ⑥ 层面的兑现)。
    ⑦ 落库三条:三表单事务(tier_history 失败 → 一起回滚、库里零行)/ 同日重跑
       no-op 不覆盖 + WARNING / `save_baskets` 搬家后 ⑤ 既有用例仍绿(见
       `test_selection_aggregate.py`,本文件只断言再导出是同一个函数对象)。
    ⑧ 第〇原则守门(静态):不 import 哨兵、不复用 `_parse_verdict`、源码零纪律参数。
"""

from __future__ import annotations

import ast
import json
import logging
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import polars as pl
import pytest

from neckline.llm.base import LLMResult
from neckline.llm.budget import LEDGER_REASON, LEDGER_SEARCH, BudgetLedger
from neckline.report.industry_strength_store import TABLE as STRENGTH_TABLE
from neckline.report.industry_strength import _MIN_MEMBERS, _STRENGTH_QUANTILE
from neckline.scan import leader as leader_mod
from neckline.scan import stage as stage_mod
from neckline.selection import aggregate as ag
from neckline.selection import basket_store
from neckline.selection import pack as pack_mod
from neckline.selection import tier as ti
from tests.conftest import write_daily_fixture

D0 = date(2024, 4, 8)
D0_S = "20240408"
_PACKS_DIR = Path(__file__).resolve().parent.parent / "packs"


# ══════════════════════════════════════════════════════════════════════════
# 构件
# ══════════════════════════════════════════════════════════════════════════

class _StubProvider:
    """最小假 provider(不经 httpx),同 `test_selection_aggregate.py::_StubProvider`
    体例 —— 专注测本模块的编排/解析/降级/闸门,网络细节见 `test_llm.py`。"""

    name = "stub"
    model = "stub-model"

    def __init__(self, replies: Sequence[LLMResult] | LLMResult, *, raises: bool = False) -> None:
        self._replies = list(replies) if isinstance(replies, (list, tuple)) else [replies]
        self._raises = raises
        self.calls: List[Dict[str, Any]] = []

    def chat(self, messages, *, enable_search=True, search_query=None, transport=None):
        self.calls.append({"messages": list(messages), "enable_search": enable_search})
        if self._raises:
            raise RuntimeError("模拟供应商炸了")
        idx = min(len(self.calls) - 1, len(self._replies) - 1)
        return self._replies[idx]


def _adjust_reply(adjustments: Sequence[Dict[str, Any]], narrative: str = "看了一眼档内次序。") -> LLMResult:
    body = json.dumps({"adjustments": list(adjustments)}, ensure_ascii=False)
    return LLMResult(ok=True, provider="stub", model="stub-model",
                     content=narrative + "\n\n```json\n" + body + "\n```")


def _member(
    code: str, *, industry: Optional[str] = None, rs_rank: Optional[int] = None,
    k4_tag: Optional[str] = None, role: str = "core", reason: str = "LLM 写的成员理由",
) -> ag.BasketMemberCandidate:
    return ag.BasketMemberCandidate(
        ts_code=code, role_llm=role, role_mech=None, role_conflict=0, reason=reason,
        industry=industry, rs_rank=rs_rank, k4_tag=k4_tag, name=code,
    )


def _basket(
    key: str, members: Sequence[ag.BasketMemberCandidate], *,
    name: str = "篮子名", driver: str = "LLM 写的共同驱动", why_now: str = "LLM 写的为什么是现在",
    pack_version: str = "K7-pack-v1",
) -> ag.BasketCandidate:
    return ag.BasketCandidate(
        trade_date=D0_S, basket_key=key, name=name, driver=driver, driver_kind="theme",
        why_now=why_now, seed_keys=("s-1",), members=tuple(members),
        evidence=(ag.EvidenceItem(claim="LLM 抄回来的证据", source="某部委", date="2024-04-07"),),
        evidence_status=ag.EVIDENCE_OK, pack_version=pack_version,
        engine_api_version=ag.engine_api.ENGINE_API_VERSION, charter_version="v1.3.3",
    )


def _agg(baskets: Sequence[ag.BasketCandidate], notes: Sequence[str] = ()) -> ag.AggregateResult:
    return ag.AggregateResult(trade_date=D0_S, baskets=tuple(baskets), notes=tuple(notes),
                              pack_version="K7-pack-v1", charter_version="v1.3.3")


def _pack_from_file(filename: str) -> pack_mod.Pack:
    """直接把仓库里的**真实包文件**做成 `Pack` 只读视图(不经 DB)。⑥ 的「换包 →
    序跟着变」必须拿真包比,拿手捏的假权重比等于自己跟自己玩。"""
    doc = json.loads((_PACKS_DIR / filename).read_text(encoding="utf-8"))
    m, c = doc["manifest"], doc["config"]
    return pack_mod.Pack(
        pack_version=m["pack_version"], name=m["name"],
        engine_api_version=int(m["engine_api_version"]), manifest=m, config=c,
        evidence_ref=list(m.get("evidence_ref", [])), is_active=True,
        created_at="2024-04-08T00:00:00+00:00", activated_at=None,
    )


K4_PACK = _pack_from_file("K4-pack.json")
K7_PACK = _pack_from_file("K7-pack.json")


def _pack_with_tier(tier_cfg: Dict[str, Any], *, version: str = "custom-pack-v1") -> pack_mod.Pack:
    return replace(K7_PACK, pack_version=version, config={**K7_PACK.config, "tier": tier_cfg})


def _fctx(**kw) -> ti.TierFeatureContext:
    return ti.TierFeatureContext(trade_date=D0, **kw)


def _insert_strength(db_path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    """`industry_strength_daily` 行(口径指纹用模块常量,不抄字面量 —— 抄了就会在
    口径变更时静默失效)。"""
    from neckline.db import init_schema

    init_schema(db_path=db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        for r in rows:
            conn.execute(
                f"INSERT OR REPLACE INTO {STRENGTH_TABLE} (trade_date, industry, median_ret, "
                "member_count, industry_rank, is_strength_day, persist_days, quantile, "
                "min_members, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (D0_S, r["industry"], r.get("median_ret", 0.01), r.get("member_count", 20),
                 r["rank"], 1, 1, _STRENGTH_QUANTILE, _MIN_MEMBERS, "now"),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_stage(db_path: Path, stage_by_industry: Dict[str, str]) -> None:
    from neckline.db import init_schema

    init_schema(db_path=db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        for industry, st in stage_by_industry.items():
            conn.execute(
                f"INSERT OR REPLACE INTO {stage_mod.TABLE} ({stage_mod._COLUMNS}) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (D0_S, industry, st, 1, 2, 3, 20, "测试行", stage_mod.SPEC_FINGERPRINT, "now"),
            )
        conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# ① 五维机械分:每一维算得出给分,算不出给中性分 + flag,**永不给 0 冒充**
# ══════════════════════════════════════════════════════════════════════════

class TestSectorStrength:
    def test_industry_rank_drives_the_score(self):
        fctx = _fctx(industry_rank={"半导体": 1, "煤炭": 100}, industry_total=100)
        top, f1 = ti._dim_sector_strength(["半导体"], ["600001.SH"], fctx)
        bottom, f2 = ti._dim_sector_strength(["煤炭"], ["600002.SH"], fctx)
        assert top == pytest.approx(1.0) and f1 == []
        assert bottom < top and f2 == []

    def test_concept_hot_board_only_adds_never_subtracts(self):
        """概念热榜只做**加成**:同一个弱行业,成员在热榜第 1 的概念里 → 分更高;
        不在任何热榜概念里 → 只是没有这一路证据,**不因此被扣分**。"""
        fctx = _fctx(industry_rank={"煤炭": 100}, industry_total=100,
                     concept_rank_by_code={"600001.SH": 1}, concept_total=10)
        with_concept, _ = ti._dim_sector_strength(["煤炭"], ["600001.SH"], fctx)
        without, _ = ti._dim_sector_strength(["煤炭"], ["600002.SH"], fctx)
        assert with_concept == pytest.approx(1.0)
        assert without < with_concept
        # 「不在热榜」没有把行业侧那一路拉低 —— 仍等于纯行业分
        assert without == pytest.approx(ti._rank_to_score(100, 100))

    def test_both_sides_absent_is_neutral_not_zero(self):
        score, flags = ti._dim_sector_strength(["某行业"], ["600001.SH"], _fctx())
        assert score == ti.NEUTRAL_DIM_SCORE != 0.0
        assert flags == [ti.FLAG_SECTOR_MISSING]


class TestDriverFreshness:
    @pytest.mark.parametrize("stage_code", list(stage_mod.STAGE_ORDER))
    def test_each_of_six_stages_equals_the_pack_value(self, stage_code):
        """K7 验收 ①:六态各喂一条 → 分值**等于包里 `stage_scores` 对应值**。
        期望值从包里读,**禁硬编** —— 硬编就等于代码里藏了第二份权重。"""
        expected = K7_PACK.tier_stage_scores()[stage_code]
        fctx = _fctx(stage_of={"半导体": stage_code}, stage_available=True)
        score, flags = ti._dim_driver_freshness(["半导体"], fctx, K7_PACK.tier_stage_scores())
        assert score == pytest.approx(expected)
        assert flags == []

    def test_overheat_is_really_zero_in_the_pack(self):
        """`overheat` 的**真实取值就是 0** —— 这正是"缺行不许写 0"那条纪律的由来
        (写 0 就与它撞车)。这条断言给下一条测试当参照物。"""
        assert K7_PACK.tier_stage_scores()[stage_mod.OVERHEAT] == 0.0

    def test_missing_stage_row_is_neutral_and_flagged_not_zero(self):
        """K7 验收 ②:阶段表当日无该行业行 → 中性分 + `stage_missing`,**断言不是 0**。"""
        fctx = _fctx(stage_of={"别的行业": stage_mod.FERMENTATION}, stage_available=True)
        score, flags = ti._dim_driver_freshness(["半导体"], fctx, K7_PACK.tier_stage_scores())
        assert score == ti.NEUTRAL_DIM_SCORE
        assert score != 0.0
        assert ti.FLAG_STAGE_MISSING in flags

    def test_pack_without_stage_scores_degrades_to_neutral(self):
        """K4-pack-v1 没有 `stage_scores` 这一段(回滚锚,需求 2 明文不为此重发版)
        → 该维取中性分 + 如实标,不崩、也不臆造一份默认映射。"""
        assert K4_PACK.tier_stage_scores() == {}
        fctx = _fctx(stage_of={"半导体": stage_mod.FERMENTATION}, stage_available=True)
        score, flags = ti._dim_driver_freshness(["半导体"], fctx, K4_PACK.tier_stage_scores())
        assert score == ti.NEUTRAL_DIM_SCORE
        assert flags == [ti.FLAG_STAGE_SCORES_ABSENT]

    def test_stage_code_not_in_pack_map_is_flagged(self):
        partial = {stage_mod.FERMENTATION: 1.0}
        fctx = _fctx(stage_of={"半导体": stage_mod.EBB}, stage_available=True)
        score, flags = ti._dim_driver_freshness(["半导体"], fctx, partial)
        assert score == ti.NEUTRAL_DIM_SCORE
        assert ti.FLAG_STAGE_UNMAPPED in flags and ti.FLAG_STAGE_MISSING in flags


class TestLeaderClarity:
    def test_decays_monotonically_with_rs_rank(self):
        """K7 验收 ③ 前半:头名度随 `rs_rank` **单调衰减**。"""
        scores = [ti._dim_leader_clarity([r])[0] for r in (1, 2, 3, 4, 5)]
        assert scores[0] == pytest.approx(1.0)
        assert all(a > b for a, b in zip(scores, scores[1:]))

    def test_best_ranked_member_defines_the_basket(self):
        assert ti._dim_leader_clarity([5, 1, 3])[0] == pytest.approx(1.0)

    def test_no_rs_rank_is_neutral_not_zero(self):
        score, flags = ti._dim_leader_clarity([None, None])
        assert score == ti.NEUTRAL_DIM_SCORE != 0.0
        assert flags == [ti.FLAG_LEADER_MISSING]

    def test_three_level_tie_break_flows_through_to_leader_clarity(self):
        """K7 验收 ③ 后半:**RS 降序 → 成交额降序 → `ts_code` 升序** 三级 tie-break。
        造两组并列(RS 三票全并列;其中两票连成交额也并列)跑 ④ 的
        `compute_leader_structure_for_day`,再把产出的 `rs_rank` 喂进本维 ——
        断言"头名"确实是 tie-break 定死的那一只,而不是行序随机挑的。"""
        codes = ["600003.SH", "600001.SH", "600002.SH"]
        window = pl.DataFrame({
            "ts_code": [c for c in codes for _ in range(20)],
            "ret_1d": [0.01] * 60,     # RS20 完全并列 → 全靠后两级 tie-break
        })
        clusters = pl.DataFrame({
            "cluster_key": ["k1"] * 3, "ts_code": codes, "consecutive_days": [1, 3, 2],
        })
        # 600001/600003 成交额并列最高 → 第三级 `ts_code` 升序 → 600001 是头名。
        amounts = pl.DataFrame({"ts_code": codes, "amount": [9e5, 9e5, 1e5]})
        out = leader_mod.compute_leader_structure_for_day(D0, window, clusters, amounts)
        rank_of = dict(zip(out["ts_code"].to_list(), out["rs_rank"].to_list()))
        assert rank_of["600001.SH"] == 1 and rank_of["600003.SH"] == 2
        assert rank_of["600002.SH"] == 3
        # 连板高度最高的是 600001(3 连板)—— 但那是巧合以外的东西:把它调低仍不影响
        # 名次,因为本维只读 `rs_rank`(⛔ 连板高度不进头名主定义)。
        assert ti._dim_leader_clarity([rank_of["600001.SH"]])[0] > \
               ti._dim_leader_clarity([rank_of["600003.SH"]])[0] > \
               ti._dim_leader_clarity([rank_of["600002.SH"]])[0]


class TestTradability:
    def test_one_word_penalized_more_than_reopened_limit(self):
        fctx = _fctx(tradability_available=True, limit_up={"A", "B"}, one_word={"A"})
        clean, _ = ti._dim_tradability(["C", "D"], fctx)
        reopened, _ = ti._dim_tradability(["B", "D"], fctx)
        sealed, _ = ti._dim_tradability(["A", "D"], fctx)
        assert clean == pytest.approx(1.0)
        assert clean > reopened > sealed

    def test_all_one_word_scores_zero_because_nothing_is_buyable(self):
        fctx = _fctx(tradability_available=True, limit_up={"A"}, one_word={"A"})
        assert ti._dim_tradability(["A"], fctx)[0] == pytest.approx(0.0)

    def test_unavailable_is_neutral_and_flagged(self):
        score, flags = ti._dim_tradability(["A"], _fctx())
        assert score == ti.NEUTRAL_DIM_SCORE
        assert flags == [ti.FLAG_TRADABILITY_MISSING]


class TestCardDensity:
    def test_more_k4_tags_lower_score(self):
        clean, f = ti._dim_card_density([None, None], k4_unavailable=False)
        one, _ = ti._dim_card_density(["A2", None], k4_unavailable=False)
        both, _ = ti._dim_card_density(["A2", "B3"], k4_unavailable=False)
        assert (clean, f) == (1.0, [])
        assert clean > one > both == 0.0

    def test_k4_unavailable_is_neutral_not_a_clean_bill(self):
        """⑤-b 报了 `k4_unavailable` 时**不能**拿"零命中"冒充"干净"。"""
        score, flags = ti._dim_card_density([None, None], k4_unavailable=True)
        assert score == ti.NEUTRAL_DIM_SCORE
        assert flags == [ti.FLAG_CARD_DENSITY_MISSING]


# ══════════════════════════════════════════════════════════════════════════
# ② 权重只住包里 + 运行期访问锁
# ══════════════════════════════════════════════════════════════════════════

class _RecordingRow(dict):
    """记录 `__getitem__`/`get` 实际访问过哪些键的 dict 子类
    (`_SORT_KEY_INPUTS` / ③ 原语访问锁的体例平移)。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.accessed: set = set()

    def __getitem__(self, key):
        self.accessed.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.accessed.add(key)
        return super().get(key, default)


class TestWeightsAndAccessLock:
    def test_weights_come_from_the_active_pack(self):
        w = ti.resolve_weights(K7_PACK)
        raw = K7_PACK.tier_weights()
        assert set(w) == ti._TIER_SCORE_INPUTS
        # K7-pack 权重和本就为 1,归一化是恒等变换 → 逐位等于包里的数
        for k, v in raw.items():
            assert w[k] == pytest.approx(v)

    def test_missing_dim_fails_loud(self):
        bad = _pack_with_tier({"weights": {"sector_strength": 1.0}, "dims": ["sector_strength"]})
        with pytest.raises(ValueError, match="缺"):
            ti.resolve_weights(bad)

    def test_unknown_dim_fails_loud(self):
        w = dict(K7_PACK.tier_weights())
        w["llm_conviction"] = 0.1
        with pytest.raises(ValueError, match="引擎不认识"):
            ti.resolve_weights(_pack_with_tier({"weights": w, "dims": list(w)}))

    def test_no_active_pack_fails_loud(self):
        with pytest.raises(ValueError, match="现役策略包"):
            ti.resolve_weights(None)

    def test_mech_score_reads_only_whitelisted_keys(self):
        """**运行期访问锁**:特征行里同时装着 LLM 产出字段,机械分一个都不许碰。"""
        b = _basket("k1", [_member("600001.SH", industry="半导体", rs_rank=1)])
        row, _flags = ti.build_tier_row(b, _fctx(), stage_scores=K7_PACK.tier_stage_scores())
        rec = _RecordingRow(row)
        ti.mech_score(rec, ti.resolve_weights(K7_PACK))
        assert rec.accessed <= ti._TIER_SCORE_INPUTS      # 不超出白名单
        assert rec.accessed == ti._TIER_SCORE_INPUTS      # 五键确实全用到(非摆设)
        assert rec.accessed.isdisjoint(ti._LLM_PROVENANCE_KEYS)

    def test_the_lock_is_falsifiable_llm_fields_really_are_in_the_row(self):
        """访问锁要有意义,行里就必须**真的**有 LLM 产出字段可读 —— 否则"没读到"
        是因为压根没有,证明不了任何事。"""
        b = _basket("k1", [_member("600001.SH", reason="LLM 说它是龙头")],
                    name="固态电池", driver="工信部发文")
        row, _ = ti.build_tier_row(b, _fctx(), stage_scores={})
        assert set(row) == ti._TIER_ROW_KEYS
        assert row["llm_driver"] == "工信部发文" and row["llm_name"] == "固态电池"
        assert row["llm_member_reasons"] == ("LLM 说它是龙头",)

    def test_whitelist_and_llm_keys_are_disjoint(self):
        assert ti._TIER_SCORE_INPUTS.isdisjoint(ti._LLM_PROVENANCE_KEYS)


# ══════════════════════════════════════════════════════════════════════════
# ③ 定档:容量上限非配额、T1 可空、可复现
# ══════════════════════════════════════════════════════════════════════════

class TestAssignTiers:
    def test_capacity_caps_are_never_exceeded(self):
        scored = [(f"k{i:02d}", 0.99 - i * 0.001) for i in range(30)]
        placement, dropped = ti.assign_tiers(scored)
        counts = {t: sum(1 for v in placement.values() if v[0] == t) for t in ti.TIERS}
        assert counts == ti.TIER_CAPACITY
        assert len(dropped) == 30 - sum(ti.TIER_CAPACITY.values())
        assert {d.reason for d in dropped} == {ti.DROP_CAPACITY_OVERFLOW}

    def test_t1_is_empty_when_nothing_clears_the_bar(self):
        """「上限非配额,允许 T1 为空」—— 市场混沌(全场平庸)时不许凑数。"""
        placement, dropped = ti.assign_tiers([("a", 0.55), ("b", 0.50), ("c", 0.30)])
        assert [placement[k][0] for k in ("a", "b", "c")] == [2, 2, 3]
        assert not [k for k, v in placement.items() if v[0] == 1]
        assert dropped == []

    def test_clearing_t1_bar_but_t1_full_cascades_down_not_squeezed_in(self):
        scored = [("a", 0.95), ("b", 0.90), ("c", 0.85), ("d", 0.80)]
        placement, _ = ti.assign_tiers(scored)
        assert placement["a"][0] == 1 and placement["b"][0] == 1
        assert placement["c"][0] == 2 and placement["d"][0] == 2

    def test_rank_mech_is_one_based_within_tier(self):
        placement, _ = ti.assign_tiers([("a", 0.95), ("b", 0.90), ("c", 0.50)])
        assert placement["a"] == (1, 1) and placement["b"] == (1, 2)
        assert placement["c"] == (2, 1)

    def test_score_ties_broken_by_basket_key_not_row_order(self):
        """CLAUDE.md 铁律:并列由行序打散 = 不确定性。同分必须靠 `basket_key` 升序
        定死 —— 两种输入行序给出**同一个**结果。"""
        a = ti.assign_tiers([("zzz", 0.7), ("aaa", 0.7)])[0]
        b = ti.assign_tiers([("aaa", 0.7), ("zzz", 0.7)])[0]
        assert a == b
        assert a["aaa"][1] == 1 and a["zzz"][1] == 2


# ══════════════════════════════════════════════════════════════════════════
# ④ 编排:score_and_tier 端到端(纯机械,零 LLM)
# ══════════════════════════════════════════════════════════════════════════

def _three_baskets() -> ag.AggregateResult:
    return _agg([
        _basket("k-strong", [_member("600001.SH", industry="半导体", rs_rank=1)], name="强"),
        _basket("k-mid", [_member("600002.SH", industry="煤炭", rs_rank=3)], name="中"),
        _basket("k-weak", [_member("600003.SH", industry="纺织", k4_tag="A2")], name="弱"),
    ])


class TestScoreAndTier:
    def test_deterministic_across_two_identical_runs(self, isolated_env):
        env = isolated_env
        _insert_strength(env.db_path, [{"industry": "半导体", "rank": 1},
                                       {"industry": "煤炭", "rank": 50},
                                       {"industry": "纺织", "rank": 99}])
        _insert_stage(env.db_path, {"半导体": stage_mod.FERMENTATION,
                                    "煤炭": stage_mod.EBB, "纺织": stage_mod.OVERHEAT})
        r = _three_baskets()
        a = ti.score_and_tier(r, D0, db_path=env.db_path, parquet_dir=env.parquet_dir, pack=K7_PACK)
        b = ti.score_and_tier(r, D0, db_path=env.db_path, parquet_dir=env.parquet_dir, pack=K7_PACK)
        assert [(d.basket_key, d.tier, d.rank_in_tier, d.mech_score) for d in a.decisions] == \
               [(d.basket_key, d.tier, d.rank_in_tier, d.mech_score) for d in b.decisions]
        assert a.decisions[0].breakdown == b.decisions[0].breakdown

    def test_stronger_basket_gets_the_better_tier_or_rank(self, isolated_env):
        env = isolated_env
        _insert_strength(env.db_path, [{"industry": "半导体", "rank": 1},
                                       {"industry": "煤炭", "rank": 50},
                                       {"industry": "纺织", "rank": 99}])
        _insert_stage(env.db_path, {"半导体": stage_mod.FERMENTATION,
                                    "煤炭": stage_mod.EBB, "纺织": stage_mod.OVERHEAT})
        res = ti.score_and_tier(_three_baskets(), D0, db_path=env.db_path,
                                parquet_dir=env.parquet_dir, pack=K7_PACK)
        order = [d.basket_key for d in res.decisions]
        assert order == ["k-strong", "k-mid", "k-weak"]
        assert res.decisions[0].tier <= res.decisions[-1].tier

    def test_breakdown_explains_the_score(self, isolated_env):
        env = isolated_env
        _insert_strength(env.db_path, [{"industry": "半导体", "rank": 1}])
        res = ti.score_and_tier(
            _agg([_basket("k1", [_member("600001.SH", industry="半导体", rs_rank=2)])]),
            D0, db_path=env.db_path, parquet_dir=env.parquet_dir, pack=K7_PACK,
        )
        bd = res.decisions[0].breakdown
        assert set(bd["dims"]) == ti._TIER_SCORE_INPUTS
        assert set(bd["weights"]) == ti._TIER_SCORE_INPUTS
        assert bd["score"] == pytest.approx(sum(bd["contrib"].values()), abs=1e-5)
        assert bd["pack_version"] == "K7-pack-v1"
        assert ti.FLAG_STAGE_MISSING in bd["flags"]     # 没喂阶段表 → 如实标

    def test_no_llm_text_leaks_into_the_breakdown(self, isolated_env):
        """`mech_breakdown_json` 是**机械分的解释**,不是 LLM 叙述的转存处。"""
        env = isolated_env
        res = ti.score_and_tier(
            _agg([_basket("k1", [_member("600001.SH", reason="LLM 说它是龙头")],
                          name="固态电池", driver="工信部发文")]),
            D0, db_path=env.db_path, parquet_dir=env.parquet_dir, pack=K7_PACK,
        )
        blob = json.dumps(res.decisions[0].breakdown, ensure_ascii=False)
        for llm_text in ("固态电池", "工信部发文", "LLM 说它是龙头"):
            assert llm_text not in blob

    def test_empty_input_is_a_legal_output(self, isolated_env):
        env = isolated_env
        res = ti.score_and_tier(_agg([]), D0, db_path=env.db_path,
                                parquet_dir=env.parquet_dir, pack=K7_PACK)
        assert res.decisions == () and "no_baskets" in res.notes

    def test_cross_day_scoring_is_refused(self, isolated_env):
        env = isolated_env
        with pytest.raises(ValueError, match="跨日定档"):
            ti.score_and_tier(_three_baskets(), date(2024, 4, 9), db_path=env.db_path,
                              parquet_dir=env.parquet_dir, pack=K7_PACK)

    def test_k4_unavailable_note_propagates_to_card_density(self, isolated_env):
        env = isolated_env
        res = ti.score_and_tier(
            _agg([_basket("k1", [_member("600001.SH")])], notes=("k4_unavailable",)),
            D0, db_path=env.db_path, parquet_dir=env.parquet_dir, pack=K7_PACK,
        )
        assert ti.FLAG_CARD_DENSITY_MISSING in res.decisions[0].breakdown["flags"]

    def test_active_pack_is_read_from_db_when_not_passed(self, isolated_env):
        env = isolated_env
        doc = json.loads((_PACKS_DIR / "K7-pack.json").read_text(encoding="utf-8"))
        pack_mod.activate_pack(doc["manifest"], doc["config"], via="test", db_path=env.db_path)
        res = ti.score_and_tier(_three_baskets(), D0, db_path=env.db_path,
                                parquet_dir=env.parquet_dir)
        assert res.pack_version == "K7-pack-v1"

    def test_overflowing_baskets_are_dropped_with_trace(self, isolated_env):
        """20 个**零数据**篮子(每一维都降级取中性分)→ 分数全是同一个平庸值,
        **一个都够不到 T1 线**:T1 空、T2 满 5、T3 满 10、余下 5 个溢出留痕。
        这一条同时兑现「上限非配额」与「市场混沌时不许凑数」两句话。"""
        env = isolated_env
        many = _agg([_basket(f"k{i:02d}", [_member(f"6000{i:02d}.SH")]) for i in range(20)])
        res = ti.score_and_tier(many, D0, db_path=env.db_path,
                                parquet_dir=env.parquet_dir, pack=K7_PACK)
        by_tier = res.by_tier()
        assert by_tier[1] == []                                    # T1 空,没被凑数
        assert len(by_tier[2]) == ti.TIER_CAPACITY[2]
        assert len(by_tier[3]) == ti.TIER_CAPACITY[3]
        assert len(res.dropped) == 20 - ti.TIER_CAPACITY[2] - ti.TIER_CAPACITY[3]
        assert {d.reason for d in res.dropped} == {ti.DROP_CAPACITY_OVERFLOW}
        assert any(n.startswith("capacity_overflow") for n in res.notes)

    def test_total_capacity_is_seventeen_when_everything_clears_t1(self, isolated_env):
        """全场都够 T1 线时:T1 收 2、其余向下顺延填满 T2/T3,合计 17 个,
        第 18 个起溢出 —— **容量上限一个都不许突破**。"""
        env = isolated_env
        _insert_strength(env.db_path, [{"industry": "半导体", "rank": 1}])
        _insert_stage(env.db_path, {"半导体": stage_mod.FERMENTATION})
        many = _agg([
            _basket(f"k{i:02d}", [_member(f"6000{i:02d}.SH", industry="半导体", rs_rank=1)])
            for i in range(20)
        ])
        res = ti.score_and_tier(many, D0, db_path=env.db_path,
                                parquet_dir=env.parquet_dir, pack=K7_PACK)
        assert all(d.mech_score >= ti.TIER1_MIN_SCORE for d in res.decisions)
        counts = {t: len(v) for t, v in res.by_tier().items()}
        assert counts == ti.TIER_CAPACITY
        assert len(res.decisions) == sum(ti.TIER_CAPACITY.values()) == 17
        assert len(res.dropped) == 3


class TestPackSwapChangesTierOrder:
    """K7 验收 ④ + ③ 完工记录里登记的跨块欠账:**换包 → Tier 序跟着变**。
    用**同一批篮子**分别跑 K4-pack(占位权重,回滚锚)与 K7-pack(证据化初值),
    断言权重差异真的改变了定档结果 —— 「插槽不是空架子」在 ⑥ 层面的兑现。"""

    def _input(self) -> ag.AggregateResult:
        # A:板块弱、龙头极清晰;B:板块极强、龙头模糊。K7 把 leader_clarity 抬到
        # 0.30、把 driver_freshness 压到 0.10,两者的相对位置因此会翻转。
        return _agg([
            _basket("k-leader", [_member("600001.SH", industry="纺织", rs_rank=1)], name="龙头清晰"),
            _basket("k-sector", [_member("600002.SH", industry="半导体", rs_rank=8)], name="板块最强"),
        ])

    def _run(self, env, pack) -> ti.TierResult:
        return ti.score_and_tier(self._input(), D0, db_path=env.db_path,
                                 parquet_dir=env.parquet_dir, pack=pack)

    def _seed(self, env):
        _insert_strength(env.db_path, [{"industry": "半导体", "rank": 1},
                                       {"industry": "纺织", "rank": 100}])
        _insert_stage(env.db_path, {"半导体": stage_mod.FERMENTATION,
                                    "纺织": stage_mod.EBB})

    def test_same_input_two_packs_different_order(self, isolated_env):
        env = isolated_env
        self._seed(env)
        k4 = self._run(env, K4_PACK)
        k7 = self._run(env, K7_PACK)
        order_k4 = [(d.basket_key, d.tier, d.rank_in_tier) for d in k4.decisions]
        order_k7 = [(d.basket_key, d.tier, d.rank_in_tier) for d in k7.decisions]
        assert order_k4 != order_k7, (order_k4, order_k7)

    def test_breakdown_explains_why_the_order_changed(self, isolated_env):
        """序变了要**解释得出来**。四个「纯机械事实」维度两包逐位相同(换包不改
        事实),变的是 `weights`/`contrib`;`driver_freshness` 是唯一会跟着换包变
        **分值**的维度 —— 因为六态打分映射本身就是包参数(K7 需求 1b)。"""
        env = isolated_env
        self._seed(env)
        k4 = {d.basket_key: d.breakdown for d in self._run(env, K4_PACK).decisions}
        k7 = {d.basket_key: d.breakdown for d in self._run(env, K7_PACK).decisions}
        facts = ti._TIER_SCORE_INPUTS - {ti.DIM_DRIVER_FRESHNESS}
        for key in k4:
            assert {d: k4[key]["dims"][d] for d in facts} == \
                   {d: k7[key]["dims"][d] for d in facts}
            assert k4[key]["weights"] != k7[key]["weights"]
            assert k4[key]["contrib"] != k7[key]["contrib"]
            assert k4[key]["pack_version"] != k7[key]["pack_version"]

    def test_stage_scores_only_live_in_k7(self, isolated_env):
        """K4-pack 的 `driver_freshness` 恒取中性分(它没有 `stage_scores` 段),
        K7-pack 才真的按六态打分 —— 这也是两包分数不同的来源之一。"""
        env = isolated_env
        self._seed(env)
        k4 = {d.basket_key: d.breakdown for d in self._run(env, K4_PACK).decisions}
        k7 = {d.basket_key: d.breakdown for d in self._run(env, K7_PACK).decisions}
        assert all(b["dims"]["driver_freshness"] == ti.NEUTRAL_DIM_SCORE for b in k4.values())
        assert any(b["dims"]["driver_freshness"] != ti.NEUTRAL_DIM_SCORE for b in k7.values())


# ══════════════════════════════════════════════════════════════════════════
# ⑤ LLM 同档微调:只能改档内序、留痕、**不得跨档**
# ══════════════════════════════════════════════════════════════════════════

def _two_in_one_tier() -> ag.AggregateResult:
    return _agg([
        _basket("k-a", [_member("600001.SH")], name="甲"),
        _basket("k-b", [_member("600002.SH")], name="乙"),
    ])


def _mech(env, r=None, **kw) -> ti.TierResult:
    return ti.score_and_tier(r or _two_in_one_tier(), D0, db_path=env.db_path,
                             parquet_dir=env.parquet_dir, pack=K7_PACK, **kw)


class TestLLMTuning:
    def test_reorders_within_tier_and_leaves_a_trace(self, isolated_env):
        env = isolated_env
        base = _mech(env)
        first, second = [d.basket_key for d in base.decisions]
        prov = _StubProvider(_adjust_reply([
            {"basket_key": second, "tier": base.decisions[1].tier, "rank_in_tier": 1,
             "reason": "乙的驱动更贴近明早竞价"},
        ]))
        res = _mech(env, use_llm=True, provider=prov)
        assert [d.basket_key for d in res.decisions] == [second, first]
        moved = {d.basket_key: d for d in res.decisions}[second]
        assert moved.rank_mech == 2 and moved.rank_in_tier == 1
        assert moved.llm_rank_delta == 1                       # 正 = 被往前提
        assert moved.llm_reason == "乙的驱动更贴近明早竞价"
        # 被挤下去的那个:机械序仍原样留着(两个都存,可归因)
        other = {d.basket_key: d for d in res.decisions}[first]
        assert other.rank_mech == 1 and other.rank_in_tier == 2 and other.llm_rank_delta == -1
        assert res.llm_stage == ti.LLM_OK

    def test_cross_tier_proposal_is_rejected_with_warning(self, isolated_env, caplog):
        """**跨档一律拒收**(§2.8-C 第 1 条 Tier 变体)。"""
        env = isolated_env
        base = _mech(env)
        key = base.decisions[0].basket_key
        other_tier = 1 if base.decisions[0].tier != 1 else 3
        prov = _StubProvider(_adjust_reply([
            {"basket_key": key, "tier": other_tier, "rank_in_tier": 1, "reason": "我想升档"},
        ]))
        with caplog.at_level(logging.WARNING):
            res = _mech(env, use_llm=True, provider=prov)
        assert {d.basket_key: d.tier for d in res.decisions} == \
               {d.basket_key: d.tier for d in base.decisions}
        assert [d.llm_rank_delta for d in res.decisions] == [0, 0]
        assert [r.reason for r in res.rejected_adjustments] == [ti.REJECT_CROSS_TIER]
        assert any("拒收跨档提案" in rec.message for rec in caplog.records)

    def test_llm_can_never_change_the_tier_map(self, isolated_env):
        """性质断言:无论 LLM 提什么,**档位映射逐位不变**,只有档内序可能变。"""
        env = isolated_env
        base = _mech(env)
        keys = [d.basket_key for d in base.decisions]
        prov = _StubProvider(_adjust_reply([
            {"basket_key": keys[0], "tier": 1, "rank_in_tier": 1},
            {"basket_key": keys[1], "tier": 1, "rank_in_tier": 2},
            {"basket_key": "不存在的篮子", "tier": 1, "rank_in_tier": 1},
        ]))
        res = _mech(env, use_llm=True, provider=prov)
        assert res.tier_by_basket_key() == base.tier_by_basket_key()

    @pytest.mark.parametrize("bad,expect", [
        ({"basket_key": "查无此篮", "rank_in_tier": 1}, ti.REJECT_UNKNOWN_BASKET),
        ({"basket_key": "k-a", "rank_in_tier": 99}, ti.REJECT_BAD_RANK),
        ({"basket_key": "k-a", "rank_in_tier": "第一"}, ti.REJECT_BAD_RANK),
        ("我不是对象", ti.REJECT_MALFORMED),
    ])
    def test_malformed_proposals_rejected_by_kind(self, isolated_env, bad, expect):
        env = isolated_env
        prov = _StubProvider(_adjust_reply([bad]))
        res = _mech(env, use_llm=True, provider=prov)
        assert [r.reason for r in res.rejected_adjustments] == [expect]
        assert [d.llm_rank_delta for d in res.decisions] == [0, 0]

    def test_two_proposals_fighting_for_one_slot_first_wins(self, isolated_env):
        env = isolated_env
        base = _mech(env)
        keys = [d.basket_key for d in base.decisions]
        prov = _StubProvider(_adjust_reply([
            {"basket_key": keys[1], "rank_in_tier": 1},
            {"basket_key": keys[0], "rank_in_tier": 1},
        ]))
        res = _mech(env, use_llm=True, provider=prov)
        assert [r.reason for r in res.rejected_adjustments] == [ti.REJECT_SLOT_TAKEN]
        assert [d.basket_key for d in res.decisions] == [keys[1], keys[0]]

    def test_same_basket_proposed_twice_second_is_dropped(self, isolated_env):
        """同一个篮子被提两次会让它同时占两个坑,剩余篮子填空位时直接崩 ——
        必须在闸上拦掉(第二条丢弃),而不是靠"模型不会这么干"。"""
        env = isolated_env
        base = _mech(env)
        keys = [d.basket_key for d in base.decisions]
        prov = _StubProvider(_adjust_reply([
            {"basket_key": keys[1], "rank_in_tier": 1},
            {"basket_key": keys[1], "rank_in_tier": 2},
        ]))
        res = _mech(env, use_llm=True, provider=prov)
        assert [r.reason for r in res.rejected_adjustments] == [ti.REJECT_DUPLICATE_KEY]
        assert [d.basket_key for d in res.decisions] == [keys[1], keys[0]]

    def test_empty_adjustments_is_a_normal_output_not_a_degrade(self, isolated_env):
        env = isolated_env
        prov = _StubProvider(_adjust_reply([]))
        res = _mech(env, use_llm=True, provider=prov)
        assert res.llm_stage == ti.LLM_OK and not res.llm_adjusted
        assert not any(n.startswith("tier_rank_unadjusted") for n in res.notes)

    @pytest.mark.parametrize("provider,ledger,expect", [
        (None, None, ti.LLM_NO_PROVIDER),
        ("boom", None, ti.LLM_CALL_FAILED),
        ("garbage", None, ti.LLM_PARSE_FAILED),
        (None, "spent", ti.LLM_BUDGET_EXHAUSTED),
    ])
    def test_llm_failure_falls_back_to_mech_order_and_says_so(
        self, isolated_env, provider, ledger, expect,
    ):
        """LLM 失败 / 预算尽 → **机械序原样用**,诚实标注未微调(不静默、不崩)。"""
        env = isolated_env
        base = _mech(env)
        prov = None
        if provider == "boom":
            prov = _StubProvider([], raises=True)
        elif provider == "garbage":
            prov = _StubProvider(LLMResult(ok=True, provider="s", model="m",
                                           content="我就说点大白话,没有围栏。"))
        led = None
        if ledger == "spent":
            led = BudgetLedger()
            led.spend(LEDGER_REASON, led.limits[LEDGER_REASON] + 1)
            prov = _StubProvider(_adjust_reply([]))
        res = _mech(env, use_llm=True, provider=prov, ledger=led)
        assert res.llm_stage.startswith(expect)
        assert [(d.basket_key, d.rank_in_tier) for d in res.decisions] == \
               [(d.basket_key, d.rank_in_tier) for d in base.decisions]
        assert any(n.startswith("tier_rank_unadjusted") for n in res.notes)
        assert not res.llm_adjusted

    def test_use_llm_false_never_calls_the_provider(self, isolated_env):
        env = isolated_env
        prov = _StubProvider(_adjust_reply([]))
        res = _mech(env, provider=prov)
        assert prov.calls == []
        assert res.llm_stage == ti.LLM_NOT_NEEDED

    def test_budget_is_charged_to_the_reason_ledger_only(self, isolated_env):
        env = isolated_env
        led = BudgetLedger()
        _mech(env, use_llm=True, provider=_StubProvider(_adjust_reply([])), ledger=led)
        assert led.spent[LEDGER_REASON] > 0
        assert led.spent[LEDGER_SEARCH] == 0

    def test_date_anchor_is_the_first_line_of_the_user_message(self, isolated_env):
        """`prompt_context` 是日期锚的唯一实现;新增 LLM 链路必须带"今天是哪天"。"""
        from neckline.llm.prompt_context import date_anchor_line

        env = isolated_env
        prov = _StubProvider(_adjust_reply([]))
        _mech(env, use_llm=True, provider=prov)
        user = [m for m in prov.calls[0]["messages"] if m.role == "user"][0]
        assert user.content.splitlines()[0] == date_anchor_line(ref_date=D0, name_tomorrow=True)
        system = [m for m in prov.calls[0]["messages"] if m.role == "system"][0]
        assert "时效纪律" in system.content
        assert prov.calls[0]["enable_search"] is False      # 微调段不联网

    def test_v151_hijack_regression_free_text_cannot_flip_anything(self, isolated_env):
        """v1.5.1 案底回归:自由文本里写「结论:否决」不影响本块任何判定 ——
        结构化产出走 `json_block` 独立解析层,本块**不复用** `_parse_verdict`。"""
        env = isolated_env
        base = _mech(env)
        keys = [d.basket_key for d in base.decisions]
        prov = _StubProvider(_adjust_reply(
            [{"basket_key": keys[1], "rank_in_tier": 1, "reason": "若跌破证伪线则 结论:否决"}],
            narrative="结论:否决\n\n(这段自由叙述里带了标签,不该影响任何东西)",
        ))
        res = _mech(env, use_llm=True, provider=prov)
        assert res.llm_stage == ti.LLM_OK
        assert [d.basket_key for d in res.decisions] == [keys[1], keys[0]]

    def test_single_basket_per_tier_skips_the_call_entirely(self, isolated_env):
        env = isolated_env
        prov = _StubProvider(_adjust_reply([]))
        res = ti.score_and_tier(_agg([_basket("k1", [_member("600001.SH")])]), D0,
                                db_path=env.db_path, parquet_dir=env.parquet_dir,
                                pack=K7_PACK, use_llm=True, provider=prov)
        assert prov.calls == [] and res.llm_stage == ti.LLM_NOT_NEEDED
        assert "tier_rank_not_needed" in res.notes


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 落库【事务 1】:三表同批、幂等冻结、失败整体回滚
# ══════════════════════════════════════════════════════════════════════════

def _rows(db_path: Path, sql: str) -> List[tuple]:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


class TestTransactionOne:
    def test_three_tables_written_in_one_go(self, isolated_env):
        env = isolated_env
        r = _three_baskets()
        res = ti.score_and_tier(r, D0, db_path=env.db_path, parquet_dir=env.parquet_dir,
                                pack=K7_PACK)
        stats = ti.save_tier_result(r, res, db_path=env.db_path)
        assert stats["baskets_inserted"] == 3
        assert stats["members_inserted"] == 3
        assert stats["tier_history_inserted"] == 3
        assert stats["frozen_conflicts"] == []
        hist = _rows(env.db_path, "SELECT trade_date, basket_id, tier, rank_mech, rank_in_tier, "
                                  "llm_rank_delta, pack_version FROM tier_history ORDER BY basket_id")
        assert len(hist) == 3
        assert {h[0] for h in hist} == {D0_S}
        assert {h[6] for h in hist} == {"K7-pack-v1"}
        # `tier_history.basket_id` 真的指向 `baskets.id`(不是臆造的序号)
        ids = {row[0] for row in _rows(env.db_path, "SELECT id FROM baskets")}
        assert {h[1] for h in hist} == ids
        blob = json.loads(_rows(env.db_path,
                                "SELECT mech_breakdown_json FROM tier_history LIMIT 1")[0][0])
        assert set(blob["dims"]) == ti._TIER_SCORE_INPUTS

    def test_tier_history_failure_rolls_back_all_three_tables(self, isolated_env, monkeypatch):
        """故意让 `tier_history` 插入失败 → `baskets`/`basket_members` **一起回滚、
        库里零行**(三张一起成功或一起回滚,不留半截)。"""
        env = isolated_env
        r = _three_baskets()
        res = ti.score_and_tier(r, D0, db_path=env.db_path, parquet_dir=env.parquet_dir,
                                pack=K7_PACK)

        def _boom(conn, rows):
            raise sqlite3.OperationalError("模拟 tier_history 写失败")

        monkeypatch.setattr(basket_store, "_save_tier_history_on_conn", _boom)
        with pytest.raises(sqlite3.OperationalError):
            ti.save_tier_result(r, res, db_path=env.db_path)
        assert _rows(env.db_path, "SELECT COUNT(*) FROM baskets")[0][0] == 0
        assert _rows(env.db_path, "SELECT COUNT(*) FROM basket_members")[0][0] == 0
        assert _rows(env.db_path, "SELECT COUNT(*) FROM tier_history")[0][0] == 0

    def test_replay_is_noop_does_not_overwrite_and_warns(self, isolated_env, caplog):
        """同日重跑 = no-op 且**不覆盖**既有行(D0 冻结件);算出来不一致要
        **留痕 + WARNING**,不静默。"""
        env = isolated_env
        r = _three_baskets()
        res = ti.score_and_tier(r, D0, db_path=env.db_path, parquet_dir=env.parquet_dir,
                                pack=K7_PACK)
        ti.save_tier_result(r, res, db_path=env.db_path)
        frozen = _rows(env.db_path, "SELECT basket_key, tier FROM baskets ORDER BY basket_key")

        # 第二次:硬把每个篮子的 tier 掰成 1(模拟"重算结果不一样")
        bumped = replace(res, decisions=tuple(
            replace(d, tier=1, rank_in_tier=1, rank_mech=1) for d in res.decisions
        ))
        with caplog.at_level(logging.WARNING):
            stats = ti.save_tier_result(r, bumped, db_path=env.db_path)
        assert stats["baskets_inserted"] == 0 and stats["baskets_existing"] == 3
        assert stats["tier_history_inserted"] == 0 and stats["tier_history_existing"] == 3
        assert _rows(env.db_path,
                     "SELECT basket_key, tier FROM baskets ORDER BY basket_key") == frozen
        assert stats["frozen_conflicts"]        # 差异如实带出来
        assert any("未采纳" in rec.message for rec in caplog.records)
        assert any("幂等跳过" in rec.message for rec in caplog.records)

    def test_overflowed_baskets_are_not_written(self, isolated_env):
        env = isolated_env
        many = _agg([_basket(f"k{i:02d}", [_member(f"6000{i:02d}.SH")]) for i in range(20)])
        res = ti.score_and_tier(many, D0, db_path=env.db_path, parquet_dir=env.parquet_dir,
                                pack=K7_PACK)
        ti.save_tier_result(many, res, db_path=env.db_path)
        assert _rows(env.db_path, "SELECT COUNT(*) FROM baskets")[0][0] == len(res.decisions)
        written = {row[0] for row in _rows(env.db_path, "SELECT basket_key FROM baskets")}
        assert written.isdisjoint({d.basket_key for d in res.dropped})

    def test_tier_history_without_baskets_fails_loud(self, isolated_env):
        env = isolated_env
        with pytest.raises(ValueError, match="找不到对应 baskets.id"):
            basket_store.save_tier_history(
                [{"basket_key": "查无此篮", "tier": 1, "mech_score": 0.9, "mech_breakdown": {},
                  "rank_in_tier": 1, "rank_mech": 1, "pack_version": "K7-pack-v1"}],
                trade_date=D0_S, basket_id_by_key={}, db_path=env.db_path,
            )

    def test_tier_history_missing_required_key_fails_loud(self, isolated_env):
        env = isolated_env
        with pytest.raises(ValueError, match="缺必填键"):
            basket_store.save_tier_history(
                [{"basket_key": "k1", "tier": 1, "mech_score": 0.9, "mech_breakdown": {},
                  "rank_in_tier": 1, "pack_version": "K7-pack-v1"}],   # 缺 rank_mech
                trade_date=D0_S, basket_id_by_key={"k1": 1}, db_path=env.db_path,
            )

    def test_save_tier_decision_refuses_baskets_without_history(self, isolated_env):
        env = isolated_env
        r = _three_baskets()
        with pytest.raises(ValueError, match="缺 tier_history 留痕"):
            basket_store.save_tier_decision(
                r, tier_by_basket_key={b.basket_key: 1 for b in r.baskets},
                tier_history_by_basket_key={}, db_path=env.db_path,
            )


class TestSaveBasketsMigration:
    def test_aggregate_reexports_the_very_same_function(self):
        """搬家体例(照 `llm/json_block.py`):`aggregate.save_baskets` 与
        `basket_store.save_baskets` 是**同一个对象**,行为逐字节不变,⑤ 的既有
        用例一字不改仍绿(见 `test_selection_aggregate.py`)。"""
        assert ag.save_baskets is basket_store.save_baskets

    def test_conn_parameter_joins_the_callers_transaction(self, isolated_env):
        """`conn=` 时**不自己 commit** —— 调用方回滚,写入随之消失。"""
        from neckline.db import get_connection, init_schema

        env = isolated_env
        r = _three_baskets()
        init_schema(db_path=env.db_path)
        conn = get_connection(env.db_path)
        try:
            basket_store.save_baskets(r, tier_by_basket_key={b.basket_key: 2 for b in r.baskets},
                                      conn=conn)
            assert conn.execute("SELECT COUNT(*) FROM baskets").fetchone()[0] == 3
            conn.rollback()
        finally:
            conn.close()
        assert _rows(env.db_path, "SELECT COUNT(*) FROM baskets")[0][0] == 0


# ══════════════════════════════════════════════════════════════════════════
# ⑦ 特征装配的 I/O 路径(只读表 + 当日分区,缺一路只降一维)
# ══════════════════════════════════════════════════════════════════════════

class TestFeatureContextIO:
    def test_reads_industry_strength_and_stage_tables(self, isolated_env):
        env = isolated_env
        _insert_strength(env.db_path, [{"industry": "半导体", "rank": 1},
                                       {"industry": "煤炭", "rank": 2}])
        _insert_stage(env.db_path, {"半导体": stage_mod.FERMENTATION})
        fctx = ti.build_feature_context(D0, ["600001.SH"], db_path=env.db_path,
                                        parquet_dir=env.parquet_dir)
        assert fctx.industry_rank == {"半导体": 1, "煤炭": 2} and fctx.industry_total == 2
        assert fctx.stage_of == {"半导体": stage_mod.FERMENTATION} and fctx.stage_available

    def test_one_word_board_detected_from_limit_derived_plus_daily(self, isolated_env):
        env = isolated_env
        write_daily_fixture(env, "daily", D0, [
            {"ts_code": "600001.SH", "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0,
             "pre_close": 10.0, "amount": 1e5},
            {"ts_code": "600002.SH", "open": 10.2, "high": 11.0, "low": 10.0, "close": 11.0,
             "pre_close": 10.0, "amount": 2e5},
        ])
        write_daily_fixture(env, "limit_derived", D0, [
            {"ts_code": "600001.SH", "board": "MAIN", "status": "limit_up", "limit_pct": 0.1,
             "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
             "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 1},
            {"ts_code": "600002.SH", "board": "MAIN", "status": "limit_up", "limit_pct": 0.1,
             "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
             "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 1},
        ])
        fctx = ti.build_feature_context(D0, ["600001.SH", "600002.SH"],
                                        db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert fctx.tradability_available
        assert fctx.limit_up == {"600001.SH", "600002.SH"}
        assert fctx.one_word == {"600001.SH"}      # 全天最低价没低于涨停价 = 买不进
        sealed, _ = ti._dim_tradability(["600001.SH"], fctx)
        reopened, _ = ti._dim_tradability(["600002.SH"], fctx)
        assert sealed < reopened

    def test_missing_everything_degrades_each_dim_independently(self, isolated_env):
        env = isolated_env
        fctx = ti.build_feature_context(D0, ["600001.SH"], db_path=env.db_path,
                                        parquet_dir=env.parquet_dir)
        assert fctx.industry_total == 0 and not fctx.stage_available
        assert not fctx.tradability_available and fctx.concept_total == 0


# ══════════════════════════════════════════════════════════════════════════
# V2-⑥-b 追加子项(2026-08-02 planner 裁定):档位质量线进包 + T3 下限 +
# 溢出摘要原因码拆分 + neutral_filled_weight 审计字段。见 PROJECT_PLAN.md
# §五 V2-⑥-b。不改动以上任何一条既有测试。
# ══════════════════════════════════════════════════════════════════════════

class TestQualityLineResolution:
    """`resolve_quality_lines()`:逐键独立回退引擎默认,姿势刻意与
    `resolve_weights()` 的 fail-loud 不同(⑥-b-A)。"""

    def test_resolves_to_engine_defaults_when_pack_has_no_quality_lines(self):
        """K4-pack-v1 没有 `quality_lines` 键——回滚锚必须逐位落到引擎默认。"""
        assert ti.resolve_quality_lines(K4_PACK) == {
            "tier1_min": ti.TIER1_MIN_SCORE,
            "tier2_min": ti.TIER2_MIN_SCORE,
            "tier3_min": ti.TIER3_MIN_SCORE,
        }

    def test_resolves_to_pack_values_when_present(self):
        assert ti.resolve_quality_lines(K7_PACK) == {
            "tier1_min": 0.60, "tier2_min": 0.40, "tier3_min": 0.25,
        }

    def test_partial_override_falls_back_key_by_key(self):
        """三键各自独立回退——同 `stage_scores` "不要求六态全部出现"同一纪律,
        不是"quality_lines 要么整段给要么整段不给"的全有全无开关。"""
        custom = _pack_with_tier({**K7_PACK.config["tier"],
                                  "quality_lines": {"tier3_min": 0.10}})
        assert ti.resolve_quality_lines(custom) == {
            "tier1_min": ti.TIER1_MIN_SCORE, "tier2_min": ti.TIER2_MIN_SCORE, "tier3_min": 0.10,
        }

    def test_no_active_pack_fails_loud(self):
        with pytest.raises(ValueError, match="现役策略包"):
            ti.resolve_quality_lines(None)


class TestAssignTiersQualityLineFloor:
    """⑥-b-B:T3 也要有下限,「上限非配额」这句话是对三档同时说的。"""

    def test_all_baskets_below_t3_line_leave_every_tier_empty(self):
        """一批分数 0.10~0.20 的篮子(全部低于默认 tier3_min=0.25)→ T3 为空,
        而不是被塞满(⑥-b 验收原文)。"""
        scored = [(f"k{i:02d}", round(0.10 + i * 0.01, 2)) for i in range(11)]   # 0.10..0.20
        placement, dropped = ti.assign_tiers(scored)
        assert placement == {}
        assert all(sum(1 for v in placement.values() if v[0] == t) == 0 for t in ti.TIERS)
        assert len(dropped) == 11
        assert {d.reason for d in dropped} == {ti.DROP_BELOW_QUALITY_LINE}

    def test_below_quality_line_and_capacity_overflow_are_distinct_reason_codes(self):
        """溢出摘要两种原因码必须分得开(⑥-b-C):同一批里**同时**制造「分数够、
        位置满」与「分数不够」两种"没进来",断言两个原因码都出现且互不覆盖
        对方的篮子。"""
        # 18 个够 T1 线的篮子:T1(2)+T2(5)+T3(10)=17 个坑,第 18 个分数也够但
        # 位置满 → capacity_overflow;另外三个连 T3 线(0.25)都没过 → below_quality_line。
        plenty = [(f"ok{i:02d}", round(0.90 - i * 0.001, 6)) for i in range(18)]
        starved = [(f"bad{i:02d}", 0.05 + i * 0.01) for i in range(3)]
        placement, dropped = ti.assign_tiers(plenty + starved)
        by_reason = {d.basket_key: d.reason for d in dropped}
        assert set(by_reason.values()) == {ti.DROP_CAPACITY_OVERFLOW, ti.DROP_BELOW_QUALITY_LINE}
        overflow_keys = {k for k, r in by_reason.items() if r == ti.DROP_CAPACITY_OVERFLOW}
        starved_keys = {k for k, r in by_reason.items() if r == ti.DROP_BELOW_QUALITY_LINE}
        assert overflow_keys == {"ok17"}                       # 唯一一个"分数够、位置满"
        assert starved_keys == {f"bad{i:02d}" for i in range(3)}
        assert overflow_keys.isdisjoint(starved_keys)

    def test_custom_quality_lines_change_eligibility(self):
        """`assign_tiers` 吃自定义 `quality_lines`(不是只认模块默认)——验证
        换包后三线真的跟着变,不是摆设参数。"""
        scored = [("a", 0.30)]
        placement_default, dropped_default = ti.assign_tiers(scored)
        assert placement_default == {"a": (3, 1)} and dropped_default == []   # 默认 tier3_min=0.25,达标

        strict = {"tier1_min": 0.60, "tier2_min": 0.40, "tier3_min": 0.35}
        placement_strict, dropped_strict = ti.assign_tiers(scored, quality_lines=strict)
        assert placement_strict == {}
        assert {d.reason for d in dropped_strict} == {ti.DROP_BELOW_QUALITY_LINE}


class TestScoreAndTierAllTiersEmpty:
    def test_all_tiers_empty_is_a_legal_output(self, isolated_env):
        """三档皆空是合法输出(⑥-b-B):篮子确实存在,只是一个都够不到(严格自定义
        质量线拉满)——`score_and_tier` 应当如实产出「今日无篮子定档」,不抛异常、
        不是 `no_baskets`(那是"根本没有篮子"的另一种情况)。"""
        env = isolated_env
        strict_pack = _pack_with_tier({
            **K7_PACK.config["tier"],
            "quality_lines": {"tier1_min": 0.99, "tier2_min": 0.98, "tier3_min": 0.97},
        })
        many = _agg([_basket(f"k{i:02d}", [_member(f"6000{i:02d}.SH")]) for i in range(5)])
        res = ti.score_and_tier(many, D0, db_path=env.db_path,
                                parquet_dir=env.parquet_dir, pack=strict_pack)
        assert res.decisions == ()
        assert res.by_tier() == {1: [], 2: [], 3: []}
        assert len(res.dropped) == 5
        assert {d.reason for d in res.dropped} == {ti.DROP_BELOW_QUALITY_LINE}
        assert "no_baskets" not in res.notes
        assert "below_quality_line:5" in res.notes
        assert res.quality_lines == {"tier1_min": 0.99, "tier2_min": 0.98, "tier3_min": 0.97}


class TestScoreAndTierMixedDropReasons:
    def test_notes_report_both_reason_codes_separately_when_both_occur(self, isolated_env):
        """`score_and_tier()` 的 `notes` 摘要也不许把两种"没进来"揉成一句话——
        不只是 `assign_tiers` 的返回值要分得开(⑥-b-C)。"""
        env = isolated_env
        _insert_strength(env.db_path, [{"industry": "半导体", "rank": 1},
                                       {"industry": "纺织", "rank": 99}])
        _insert_stage(env.db_path, {"半导体": stage_mod.FERMENTATION,
                                    "纺织": stage_mod.OVERHEAT})
        write_daily_fixture(env, "daily", D0, [
            {"ts_code": "600999.SH", "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0,
             "pre_close": 10.0, "amount": 1e5},
        ])
        write_daily_fixture(env, "limit_derived", D0, [
            {"ts_code": "600999.SH", "board": "MAIN", "status": "limit_up", "limit_pct": 0.1,
             "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
             "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 1},
        ])
        # 20 个板块最强 + 龙头头名的篮子(全部够 T1 线)→ 17 个坑,3 个 capacity_overflow。
        strong = [
            _basket(f"strong{i:02d}", [_member(f"6001{i:02d}.SH", industry="半导体", rs_rank=1)])
            for i in range(20)
        ]
        # 板块最弱 + 一字板 + 红牌的篮子 → 连 T3 线都够不到,below_quality_line。
        weak = _basket("k-weak", [_member("600999.SH", industry="纺织", k4_tag="A2")], name="弱")
        res = ti.score_and_tier(_agg(strong + [weak]), D0, db_path=env.db_path,
                                parquet_dir=env.parquet_dir, pack=K7_PACK)
        by_reason = {d.basket_key: d.reason for d in res.dropped}
        assert len(res.dropped) == 4
        assert by_reason.get("k-weak") == ti.DROP_BELOW_QUALITY_LINE
        assert sum(1 for r in by_reason.values() if r == ti.DROP_CAPACITY_OVERFLOW) == 3
        assert "below_quality_line:1" in res.notes
        assert "capacity_overflow:3" in res.notes


class TestNeutralFilledWeight:
    """`ti.neutral_filled_weight()` 纯函数(⑥-b-D):不依赖 DB/parquet,直接喂
    flags + weights。"""

    def test_zero_when_no_flags(self):
        weights = ti.resolve_weights(K7_PACK)
        assert ti.neutral_filled_weight([], weights) == 0.0

    def test_sums_weights_of_missing_dims_only(self):
        weights = ti.resolve_weights(K7_PACK)
        flags = [ti.FLAG_STAGE_MISSING, ti.FLAG_LEADER_MISSING, ti.FLAG_TRADABILITY_MISSING]
        expected = (weights[ti.DIM_DRIVER_FRESHNESS] + weights[ti.DIM_LEADER_CLARITY]
                    + weights[ti.DIM_TRADABILITY])
        assert ti.neutral_filled_weight(flags, weights) == pytest.approx(expected)

    def test_stage_scores_absent_counts_the_same_as_stage_missing(self):
        """`driver_freshness` 有两个不同的"缺数据"flag(整段缺 stage_scores /
        当日无该行业阶段行),两个都必须计入——不是只认其中一个。"""
        weights = ti.resolve_weights(K7_PACK)
        a = ti.neutral_filled_weight([ti.FLAG_STAGE_MISSING], weights)
        b = ti.neutral_filled_weight([ti.FLAG_STAGE_SCORES_ABSENT], weights)
        assert a == b == pytest.approx(weights[ti.DIM_DRIVER_FRESHNESS])

    def test_unrelated_flag_stage_unmapped_alone_does_not_count(self):
        """`FLAG_STAGE_UNMAPPED` 单独出现(没有伴随 `FLAG_STAGE_MISSING`)不代表
        这一维被中性填充——它只是说"某个行业的阶段码没打上分",该维仍可能是
        **别的**行业算出来的真实值(见 `_dim_driver_freshness` docstring)。"""
        weights = ti.resolve_weights(K7_PACK)
        assert ti.neutral_filled_weight([ti.FLAG_STAGE_UNMAPPED], weights) == 0.0


class TestNeutralFilledWeightEndToEnd:
    """跑通 `score_and_tier` 全链路,确认字段真的落进 `breakdown`(不只是独立
    纯函数正确,接线也要对)。"""

    def test_equals_sum_of_weights_for_three_missing_dims(self, isolated_env):
        """⑥-b-D 验收原文:三维缺数据 → 该值 = 那三维权重之和。"""
        env = isolated_env
        _insert_strength(env.db_path, [{"industry": "半导体", "rank": 1}])
        res = ti.score_and_tier(
            _agg([_basket("k1", [_member("600001.SH", industry="半导体")])]),
            D0, db_path=env.db_path, parquet_dir=env.parquet_dir, pack=K7_PACK,
        )
        bd = res.decisions[0].breakdown
        # driver_freshness(没喂阶段表)/ leader_clarity(没给 rs_rank)/
        # tradability(没喂行情切片)三维缺数据;sector_strength(插了强度表)与
        # card_density(k4_tag=None→零命中的真实值)两维不缺。
        assert ti.FLAG_STAGE_MISSING in bd["flags"]
        assert ti.FLAG_LEADER_MISSING in bd["flags"]
        assert ti.FLAG_TRADABILITY_MISSING in bd["flags"]
        assert ti.FLAG_SECTOR_MISSING not in bd["flags"]
        expected = (bd["weights"][ti.DIM_DRIVER_FRESHNESS] + bd["weights"][ti.DIM_LEADER_CLARITY]
                    + bd["weights"][ti.DIM_TRADABILITY])
        assert bd["neutral_filled_weight"] == pytest.approx(expected)

    def test_rank_two_is_not_mistaken_for_missing_in_the_full_pipeline(self, isolated_env):
        """⑥-b-D 点名的撞车:`rank=2` 真实算出 0.5,和"没数据"的中性填充 0.5
        数值相同——`neutral_filled_weight` 必须靠 flags 分辨,不能靠数值。"""
        env = isolated_env
        _insert_strength(env.db_path, [{"industry": "半导体", "rank": 1}])
        res = ti.score_and_tier(
            _agg([
                _basket("k-rank2", [_member("600001.SH", industry="半导体", rs_rank=2)]),
                _basket("k-missing", [_member("600002.SH", industry="半导体", rs_rank=None)]),
            ]),
            D0, db_path=env.db_path, parquet_dir=env.parquet_dir, pack=K7_PACK,
        )
        by_key = {d.basket_key: d.breakdown for d in res.decisions}
        rank2, missing = by_key["k-rank2"], by_key["k-missing"]
        # 两者 leader_clarity 数值上撞车(1/2 == 中性分 0.5),但语义相反。
        assert rank2["dims"][ti.DIM_LEADER_CLARITY] == pytest.approx(0.5)
        assert missing["dims"][ti.DIM_LEADER_CLARITY] == pytest.approx(0.5)
        assert ti.FLAG_LEADER_MISSING not in rank2["flags"]
        assert ti.FLAG_LEADER_MISSING in missing["flags"]
        # 除 rs_rank 外两个篮子其余条件全部相同,neutral_filled_weight 的差异必须
        # 恰好等于 leader_clarity 这一份权重——真实第二名不计入,真缺数据要计入。
        w = rank2["weights"][ti.DIM_LEADER_CLARITY]
        assert missing["neutral_filled_weight"] == pytest.approx(rank2["neutral_filled_weight"] + w)


# ══════════════════════════════════════════════════════════════════════════
# ⑧ 第〇原则 / 架构守门(静态)
# ══════════════════════════════════════════════════════════════════════════

_TIER_PATH = Path(__file__).resolve().parent.parent / "neckline" / "selection" / "tier.py"
_STORE_PATH = Path(__file__).resolve().parent.parent / "neckline" / "selection" / "basket_store.py"


def _imported_modules(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
        elif isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
    return mods


@pytest.mark.parametrize("path", [_TIER_PATH, _STORE_PATH], ids=lambda p: p.name)
def test_never_imports_sentinel(path):
    """§2.8-C 第 2 条:LLM 产出的自由文本与数字一律不进哨兵判据。"""
    assert not [m for m in _imported_modules(path) if m.startswith("neckline.sentinel")]


def test_tier_does_not_reuse_the_verdict_parser():
    mods = _imported_modules(_TIER_PATH)
    assert "neckline.llm.judge" not in mods
    assert "neckline.llm.json_block" in mods
    tree = ast.parse(_TIER_PATH.read_text(encoding="utf-8"), filename=str(_TIER_PATH))
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)} | \
             {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "_parse_verdict" not in called


def test_reads_no_discipline_thresholds():
    """纪律参数(止损/回落止盈/仓位)不在**定档层**出现 —— 定档只读策略包与预计算表,
    章程版本号只当口径指纹(§2.0:纪律只住章程)。"""
    src = _TIER_PATH.read_text(encoding="utf-8")
    for banned in ("stop_pct", "take_profit_retrace", "active_config(", "0.05", "0.08"):
        assert banned not in src, f"Tier 层出现了纪律参数痕迹:{banned}"


def test_store_persists_discipline_fingerprint_but_never_reads_it():
    """`basket_store.py` 的判据 V2-⑦ 起与 `tier.py` **刻意不同**,不是放松。

    原先这条把 store 和 tier 一起扫「`stop_pct` 不许出现」。⑦ 落地后 store 多了
    `basket_cards` 的写入口,而那张表**本来就有** `stop_pct` / `take_profit_retrace`
    两列(① 建表时定的口径指纹列,同 `reference_plans.stop_pct` 既有惯例)——列名
    必然出现在 INSERT 语句里。真正要守的是「本层**不去读**纪律参数」:值由调用方
    (⑦ 的 `basket_card.resolve_charter_pcts`,唯一源 = 现役 `strategy_versions`)
    算好后原样落行,store 既不查章程、也不含任何比例字面量。"""
    src = _STORE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_STORE_PATH))

    # ① 本层不向章程要数(不 import brain、不调 active_config/get_active)
    assert "strategy.brain" not in src and "strategy import brain" not in src
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)} | \
             {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert called.isdisjoint({"active_config", "get_active", "resolve_charter_pcts"})

    # ② 没有任何纪律比例字面量(禁硬编 0.05 / 0.08)
    assert not [c for c in ast.walk(tree) if isinstance(c, ast.Constant)
                and isinstance(c.value, float) and c.value in (0.05, 0.08)]

    # ③ 两个指纹只以「调用方传进来的参数」形式进入本层,本层不派生
    owners = {
        f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
        for a in list(f.args.args) + list(f.args.kwonlyargs)
        if a.arg in ("stop_pct", "take_profit_retrace")
    }
    assert owners == {"save_basket_card"}, owners


def test_limit_height_never_feeds_leader_clarity():
    """⛔ 连板高度不进头名主定义(十二格审计:双尾放大器,涨停触达 1.5× 的同时
    次日跌停 3×;它的用途在 ⑦-K7 的双尾标注)。判据要机器可查:源码里出现
    `limit_height` 的每一行都必须是注释/文档,不能是取值。"""
    for line in _TIER_PATH.read_text(encoding="utf-8").splitlines():
        if "limit_height" in line:
            assert line.lstrip().startswith("#") or "⛔" in line or "`" in line, line


def test_tier_score_inputs_contains_exactly_the_five_dims():
    assert ti._TIER_SCORE_INPUTS == {
        "sector_strength", "driver_freshness", "leader_clarity", "tradability", "card_density",
    }


def test_capacity_matches_the_plan():
    assert ti.TIER_CAPACITY == {1: 2, 2: 5, 3: 10}
