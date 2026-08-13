"""④篮子卡冻结 `neckline/selection/basket_card.py` + 【事务 2】写入口单测
(plan §五 V2-⑦ 验收逐条)。

覆盖(与 plan 验收清单一一对应):
    ① **卡 11 项齐全的 golden 快照**(蓝图 4.6 逐项,缺一即挂)。
    ② **夹逼四态各一**(界内通过 / 越涨停被拦 / low>high 被拦 / 算不出涨跌停被拦),
       被拦时该项为 `null` 且 reason 精确;离场参考**不夹涨跌停**。
    ③ **止损随现役 `stop_pct` 变而变**(改测试库 config → 输出跟着变,禁硬编 0.05)。
    ④ **冻结**:同 `(basket_id, version)` 二次写 → 拒(no-op、不覆盖)+ 差异留痕;
       **追加版本制**:D+1 只能写 version=2,D0 行一字不改。
    ⑤ **结构化阈值确实出现在喂给 LLM 的上下文里**。
    ⑥ **`disclaimer` 单一源**。
另加:LLM 降级四态(结构化半份照出、人话半份缺席如实标)/ 成员白名单闸 /
v1.5.1 标签劫持防护(先剥 JSON)/「有篮子、无卡」合法中间态 / ⑦ 只写一张表。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pytest

from neckline.llm.base import LLMResult
from neckline.llm.budget import LEDGER_REASON, LEDGER_SEARCH, BudgetLedger
from neckline.selection import aggregate as ag
from neckline.selection import basket_card as bc
from neckline.selection import basket_store
from neckline.selection import member_tags as mt
from neckline.strategy import charter_copy as bc_charter_copy
from tests.conftest import seed_active_rule_v1

D0 = date(2024, 4, 8)
D0_S = "20240408"
_REPO = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════════
# 构件
# ══════════════════════════════════════════════════════════════════════════

class _StubProvider:
    """最小假 provider(不经 httpx),同 `test_selection_tier.py::_StubProvider` 体例。"""

    name, model = "stub", "stub-model"

    def __init__(self, reply: Any = None, *, raises: bool = False) -> None:
        self._reply = reply
        self._raises = raises
        self.calls: List[Dict[str, Any]] = []

    def chat(self, messages, *, enable_search=True, search_query=None, transport=None):
        self.calls.append({"messages": list(messages), "enable_search": enable_search})
        if self._raises:
            raise RuntimeError("模拟供应商炸了")
        return self._reply


def _reply(payload: Dict[str, Any], narrative: str = "这个篮子的看法如下。") -> LLMResult:
    body = json.dumps(payload, ensure_ascii=False)
    return LLMResult(ok=True, provider="stub", model="stub-model",
                     content=narrative + "\n\n```json\n" + body + "\n```")


def _member(code: str, **over: Any) -> ag.BasketMemberCandidate:
    kw: Dict[str, Any] = dict(
        ts_code=code, role_llm="leader", role_mech="leader", role_conflict=0,
        reason=f"{code} 是本篮最能代表驱动的一只", name=f"名{code[:3]}",
        industry="电力设备", industry_lift=2.4, rs_rank=1, is_primary=1,
    )
    kw.update(over)
    return ag.BasketMemberCandidate(**kw)


def _basket(key: str = "b1", codes: Sequence[str] = ("600000.SH",), **over: Any) -> ag.BasketCandidate:
    kw: Dict[str, Any] = dict(
        trade_date=D0_S, basket_key=key, name="固态电池中试线", driver="某部委中试线补贴落地",
        driver_kind="policy", why_now="补贴细则昨日发布,今日板块首次放量",
        seed_keys=("s-1",), members=tuple(_member(c) for c in codes),
        evidence=(ag.EvidenceItem(claim="中试线补贴细则发布", source="某部委网站",
                                  date="2024-04-07", url="https://example.gov/x"),),
        evidence_status=ag.EVIDENCE_OK, pack_version="K7-pack-v1",
        engine_api_version=ag.engine_api.ENGINE_API_VERSION, charter_version="v1.3.3",
    )
    kw.update(over)
    return ag.BasketCandidate(**kw)


class _FakeDecision:
    """duck-typed ⑥ 定档结果(⑦ 刻意不 import `tier`;这里也不 import)。"""

    def __init__(self, tier=1, rank=1, score=0.82, reason="龙头更清晰") -> None:
        self.tier, self.rank_in_tier, self.rank_mech = tier, rank, rank
        self.mech_score = score
        self.breakdown = {"dims": {"sector_strength": 0.9, "leader_clarity": 0.8}, "flags": []}
        self.llm_reason = reason


def _mech(code: str = "600000.SH", **over: Any) -> bc.MemberMech:
    kw: Dict[str, Any] = dict(
        ts_code=code, name="名600", close=10.0, ma20=9.2,
        limit_up=11.0, limit_down=9.0, stop_price=9.5,
    )
    kw.update(over)
    return bc.MemberMech(**kw)


def _payload(**over: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "upside_path": "补贴细则落地推动订单预期,先修复缺口再沿 5 日线台阶式抬升,到前高一带算走完",
        "entries": [{"ts_code": "600000.SH", "low": 9.8, "high": 10.2, "max_chase": 10.5,
                     "exit_low": 12.0, "exit_high": 13.5, "why": "回踩昨日实体中枢"}],
        "verification": "次日若多数成员站上今日收盘并守住 MA20,说明驱动被跟随。",
        "invalidation": "若多数成员跌破止损线或 MA20,这条驱动就不成立了。",
        "risks": ["补贴细则落地节奏可能慢于预期", "板块昨日已大涨,追高风险"],
        "tier_note": "T1 第一位合理",
    }
    base.update(over)
    return base


_DEFAULT = object()   # 与"显式传 None"区分开(None = 本次 LLM 没有产出)


def _card(payload: Any = _DEFAULT, *, mechs=None, **over: Any) -> bc.BasketCard:
    kw: Dict[str, Any] = dict(
        tier_decision=_FakeDecision(),
        mechs=mechs if mechs is not None else {"600000.SH": _mech()},
        payload=_payload() if payload is _DEFAULT else payload,
        narrative="这个篮子的看法如下。",
        llm_stage=bc.LLM_OK, stop_pct=0.05, take_profit_retrace=0.08,
    )
    kw.update(over)
    return bc.build_basket_card(_basket(), D0, **kw)


# ══════════════════════════════════════════════════════════════════════════
# ① 卡 11 项齐全(蓝图 4.6 golden 快照)
# ══════════════════════════════════════════════════════════════════════════

def test_card_json_has_all_eleven_blueprint_items():
    j = _card().to_card_json()
    # 1 篮子名称与共同驱动
    assert j["name"] == "固态电池中试线" and j["driver"] and j["driver_kind"] == "policy"
    # 2 驱动证据与信息来源,**每条带日期**
    assert j["evidence"] and all(e["source"] and e["date"] for e in j["evidence"])
    assert j["evidence_status"] == ag.EVIDENCE_OK
    # 3 为什么是现在
    assert j["why_now"]
    # 4 成员、角色与比较结果(含对拍分歧位)
    assert len(j["members"]) == 1
    m = j["members"][0]
    for key in ("ts_code", "role_llm", "role_mech", "role_conflict", "reason",
                "industry", "industry_lift", "rs_rank", "tags", "tags_absent"):
        assert key in m, key
    assert "role_conflicts" in j
    # 5 Tier 及分层理由
    assert j["tier"] == 1 and j["rank_in_tier"] == 1 and j["rank_mech"] == 1
    assert j["mech_score"] == pytest.approx(0.82) and j["tier_breakdown"]["dims"]
    assert j["tier_reason"] == "龙头更清晰" and j["tier_note"] == "T1 第一位合理"
    # 6 预期上涨路径(V2.3.3-①:一段话,不分支)
    assert j["upside_path"] and "台阶式抬升" in j["upside_path"]
    assert j["upside_path_unavailable_reason"] is None
    # 🔴 老两键**已停发**(〇-6:客户端 `decodeIfPresent` → 直接停发,不走两步淘汰)
    assert "scripts" not in j and "scripts_unavailable_reason" not in j
    # 7 建仓观察区间 + 最高追价
    assert m["entry_zone"] == {"low": 9.8, "high": 10.2, "why": "回踩昨日实体中枢"}
    assert m["max_chase"] == 10.5
    # 8 / 9 验证 + 失效,结构化与人话**双份**
    assert j["verification_spec"]["spec_version"] == bc.VERIFY_SPEC_VERSION
    assert j["verification_text"]
    assert j["invalidation_spec"]["spec_version"] == bc.INVALIDATE_SPEC_VERSION
    assert j["invalidation_text"]
    # 10 主要风险
    assert j["risks"] == ["补贴细则落地节奏可能慢于预期", "板块昨日已大涨,追高风险"]
    # 11 disclaimer(固定文案)
    assert j["disclaimer"] == bc.BASKET_CARD_DISCLAIMER
    # 口径指纹七项 + 动态纪律标签
    assert j["fingerprint"] == {
        "stop_pct": 0.05, "take_profit_retrace": 0.08, "charter_version": "v1.3.3",
        # V2.3.2-⑤:对外退出语义两项(K8.md §十九)。这张测试卡是**手搓**的、没喂这两位
        # → `None` = **该章程没有声明过这个语义**,⛔ 不是"读失败"。`stop_pct` 仍是 0.05
        # 且**与它们并列**(只加不删,两步淘汰第一步)。
        "loss_warning_pct": None, "loss_warning_action": None,
        "pack_version": "K7-pack-v1", "engine_api_version": ag.engine_api.ENGINE_API_VERSION,
        # ⑦-b:条件集版本(与跟形状的 spec_version 分开,⑨ 按它分层归因)
        "verification_ruleset_version": bc.VERIFICATION_RULESET_VERSION,
    }
    # ⚠ 「章程止损」→「章程止损线」:**被 V2.4.0 复审 🟡-4 取代**(施工纪律 4)——
    #   线名改由单一源 `charter_copy.stop_line_label(advisory)` 派生,强制条件单口径
    #   下它就是「止损线」。语义一字未变;冻结卡是快照,历史行不受影响。
    assert j["discipline_labels"] == ["章程止损线 −5.0%", "回落止盈 8.0%"]
    assert j["spec_version"] == bc.CARD_SPEC_VERSION and j["version"] == 1
    assert j["degraded"] is False and j["llm_stage"] == bc.LLM_OK


def test_card_json_key_shape_is_stable():
    """冻结快照的键形状 = 给 ⑮ 客户端的契约。**键只增不改**;这条锁住当前全集,
    未来加键会挂在这里 —— 挂了就去确认客户端解码是不是要跟进(CLAUDE.md「落库
    快照两类论」:`card_json` 是"写入当时冻住"的那一类,不会因服务端升级补全新键)。"""
    j = _card().to_card_json()
    assert set(j) == {
        "spec_version", "version", "basket_key", "trade_date", "next_trade_date",
        "name", "driver", "driver_kind", "evidence", "evidence_status", "why_now",
        "members", "role_conflicts", "tier", "rank_in_tier", "rank_mech", "mech_score",
        "tier_breakdown", "tier_reason", "tier_note",
        # V2.3.3-①:`scripts` / `scripts_unavailable_reason` **停发**,换成下面两键。
        "upside_path", "upside_path_unavailable_reason",
        "verification_spec", "verification_text",
        "invalidation_spec", "invalidation_text", "risks", "disclaimer",
        "fingerprint", "discipline_labels", "narrative", "llm_stage", "degraded", "notes",
        # V2.2-③-E(spec v3):引擎归属三键(裁定 #9 单篮子单引擎,成员继承)。
        "engine_code", "engine_version", "skeleton_version",
    }
    assert set(j["members"][0]) == {
        "ts_code", "name", "role_llm", "role_mech", "role_conflict", "reason",
        "is_primary", "industry", "industry_lift", "lift_reason", "primary_reason",
        "rs_rank", "k4_tag",
        # V2.2-③-C(裁定 #11):位置关判定 + 理由 + **当次读数**(卡是冻结件,
        # 存读数 = 事后复核「模型拿什么下的判断」不必回头猜)。
        "position_verdict", "position_reason", "position_metrics",
        # V2.2-③-C2(裁定 #12):核心关同构三键(⛔ 与位置三键分开,两个独立判定)。
        "core_verdict", "core_reason", "core_metrics",
        "mech", "entry_zone", "entry_zone_clamp",
        "entry_zone_unavailable_reason", "max_chase", "max_chase_clamp",
        "max_chase_unavailable_reason", "exit_reference", "exit_reference_clamp",
        "exit_reference_unavailable_reason", "tags", "tags_absent",
    }


def test_card_json_is_json_serialisable_and_deterministic():
    a = json.dumps(_card().to_card_json(), ensure_ascii=False, sort_keys=True)
    b = json.dumps(_card().to_card_json(), ensure_ascii=False, sort_keys=True)
    assert a == b


def test_role_conflict_is_carried_into_card():
    card = bc.build_basket_card(
        _basket(codes=("600000.SH",)), D0, mechs={"600000.SH": _mech()},
        payload=_payload(), llm_stage=bc.LLM_OK, stop_pct=0.05,
    )
    assert card.to_card_json()["role_conflicts"] == []
    b2 = _basket(codes=())
    b2 = ag.BasketCandidate(**{**b2.__dict__, "members": (
        _member("600000.SH", role_llm="leader", role_mech="elastic", role_conflict=1),)})
    j = bc.build_basket_card(b2, D0, mechs={"600000.SH": _mech()}, payload=_payload(),
                             llm_stage=bc.LLM_OK, stop_pct=0.05).to_card_json()
    assert j["role_conflicts"] == ["600000.SH"]
    assert j["members"][0]["role_conflict"] == 1


# ══════════════════════════════════════════════════════════════════════════
# ② 夹逼四态 + 离场参考不夹涨跌停
# ══════════════════════════════════════════════════════════════════════════

def _entry_of(payload_entry: Dict[str, Any], mech: bc.MemberMech) -> Dict[str, Any]:
    j = bc.build_basket_card(
        _basket(), D0, mechs={"600000.SH": mech},
        payload=_payload(entries=[{"ts_code": "600000.SH", **payload_entry}]),
        llm_stage=bc.LLM_OK, stop_pct=0.05,
    ).to_card_json()
    return j["members"][0]


def test_clamp_state_ok_inside_band():
    m = _entry_of({"low": 9.8, "high": 10.2, "max_chase": 10.4}, _mech())
    assert m["entry_zone_clamp"] == bc.CLAMP_OK
    assert m["entry_zone"] == {"low": 9.8, "high": 10.2, "why": ""}
    assert m["entry_zone_unavailable_reason"] is None
    assert m["max_chase"] == 10.4 and m["max_chase_clamp"] == bc.CLAMP_OK


def test_clamp_state_out_of_limit_is_rejected():
    """越涨停 → 该项为 `null` + reason 精确。"""
    m = _entry_of({"low": 10.5, "high": 12.5, "max_chase": 12.9}, _mech())
    assert m["entry_zone"] is None
    assert m["entry_zone_clamp"] == bc.CLAMP_REJECTED_OUT_OF_LIMIT
    assert m["entry_zone_unavailable_reason"] == "生成的数字超出次日涨跌停范围,已拦截"
    assert m["max_chase"] is None and m["max_chase_clamp"] == bc.CLAMP_REJECTED_OUT_OF_LIMIT


def test_clamp_state_malformed_low_gt_high():
    m = _entry_of({"low": 10.4, "high": 9.6, "max_chase": 10.5}, _mech())
    assert m["entry_zone"] is None
    assert m["entry_zone_clamp"] == bc.CLAMP_REJECTED_MALFORMED
    assert m["entry_zone_unavailable_reason"] == "生成的数字格式不合法或自相矛盾,已拦截"
    # **区间被拦不牵连追价**:追价自身合法且界内 → 照常 ok(两项各自落态)
    assert m["max_chase"] == 10.5 and m["max_chase_clamp"] == bc.CLAMP_OK


def test_clamp_state_no_limit_price():
    m = _entry_of({"low": 9.8, "high": 10.2, "max_chase": 10.4},
                  _mech(limit_up=None, limit_down=None,
                        no_limit_reason="查无股票元数据(stock_basic 缺该代码),无法判定板块 / 是否 ST"))
    assert m["entry_zone"] is None
    assert m["entry_zone_clamp"] == bc.CLAMP_REJECTED_NO_LIMIT
    assert m["entry_zone_unavailable_reason"] == "无法算出次日涨跌停价,该项不显示"
    assert m["max_chase_clamp"] == bc.CLAMP_REJECTED_NO_LIMIT
    assert m["mech"]["no_limit_reason"].startswith("查无股票元数据")


def test_clamp_absent_when_llm_gave_nothing():
    """「没给」与「给了被拦」是两件事 —— 即便涨跌停算不出,也不许把 absent 记成
    `rejected_no_limit`(v1.5 `_clamp_buy` 的判定优先级逐条平移)。"""
    m = _entry_of({}, _mech(limit_up=None, limit_down=None))
    assert m["entry_zone_clamp"] == bc.CLAMP_ABSENT
    assert m["entry_zone_unavailable_reason"] == "本次未生成该项"
    assert m["max_chase_clamp"] == bc.CLAMP_ABSENT


def test_max_chase_below_zone_high_is_self_contradictory():
    """最高追价比建仓区间上沿还低 = 自相矛盾(读者无从执行)→ 拦下,区间照留。"""
    m = _entry_of({"low": 9.8, "high": 10.2, "max_chase": 9.9}, _mech())
    assert m["entry_zone_clamp"] == bc.CLAMP_OK
    assert m["max_chase"] is None and m["max_chase_clamp"] == bc.CLAMP_REJECTED_MALFORMED


def test_exit_reference_is_not_clamped_to_limit_band():
    """离场参考**只校验格式、不夹涨跌停**(压力位可能几个交易日后才到)。"""
    m = _entry_of({"low": 9.8, "high": 10.2, "max_chase": 10.4,
                   "exit_low": 13.0, "exit_high": 15.0}, _mech())
    assert m["exit_reference"] == {"low": 13.0, "high": 15.0}   # 远在涨停 11.0 之上
    assert m["exit_reference_clamp"] == bc.EXIT_CLAMP_OK
    bad = _entry_of({"exit_low": 15.0, "exit_high": 13.0}, _mech())
    assert bad["exit_reference"] is None
    assert bad["exit_reference_clamp"] == bc.EXIT_CLAMP_REJECTED_MALFORMED


@pytest.mark.parametrize("raw", [
    {"low": "9.8", "high": 10.2}, {"low": float("nan"), "high": 10.2},
    {"low": 0.0, "high": 10.2}, {"low": -1.0, "high": 10.2}, {"low": 9.8},
])
def test_clamp_entry_zone_rejects_non_numbers(raw):
    low, high, clamp = bc.clamp_entry_zone(raw, 11.0, 9.0)
    assert (low, high) == (None, None) and clamp == bc.CLAMP_REJECTED_MALFORMED


def test_clamp_boundaries_are_inclusive():
    """恰好等于涨停价 / 跌停价的数字**在闭区间内**,放行(plan 原文「闭区间」)。"""
    low, high, clamp = bc.clamp_entry_zone({"low": 9.0, "high": 11.0}, 11.0, 9.0)
    assert clamp == bc.CLAMP_OK and (low, high) == (9.0, 11.0)


def test_llm_cannot_smuggle_a_member_that_is_not_in_the_basket():
    """成员白名单闸(承 ⑤ 的同名闸):`entries` 里出现成员集合外的代码 → 整条丢弃。"""
    j = bc.build_basket_card(
        _basket(codes=("600000.SH",)), D0, mechs={"600000.SH": _mech()},
        payload=_payload(entries=[
            {"ts_code": "999999.SZ", "low": 1.0, "high": 2.0},
            {"ts_code": "600000.SH", "low": 9.8, "high": 10.2},
        ]),
        llm_stage=bc.LLM_OK, stop_pct=0.05,
    ).to_card_json()
    assert [m["ts_code"] for m in j["members"]] == ["600000.SH"]
    assert j["members"][0]["entry_zone"]["low"] == 9.8


# ══════════════════════════════════════════════════════════════════════════
# ③ 止损价系统算,随现役 stop_pct 变而变(禁硬编 0.05)
# ══════════════════════════════════════════════════════════════════════════

def test_stop_price_is_system_computed_from_active_charter(isolated_env):
    seed_active_rule_v1(isolated_env)
    stop_pct, tpr = bc.resolve_charter_pcts(isolated_env.db_path)
    assert (stop_pct, tpr) == (0.05, 0.05)
    mechs = bc.build_member_mech({"600000.SH": 10.0}, D0, stop_pct=stop_pct,
                                 db_path=isolated_env.db_path)
    assert mechs["600000.SH"].stop_price == pytest.approx(9.5)


def test_stop_price_follows_charter_change(isolated_env):
    """改测试库 config → 止损价与纪律标签**跟着变**(单一源 = 现役 `strategy_versions`)。"""
    seed_active_rule_v1(isolated_env, extra_config={"stop_pct": 0.08, "take_profit_retrace": 0.10})
    stop_pct, tpr = bc.resolve_charter_pcts(isolated_env.db_path)
    assert stop_pct == pytest.approx(0.08)
    mechs = bc.build_member_mech({"600000.SH": 10.0}, D0, stop_pct=stop_pct,
                                 db_path=isolated_env.db_path)
    assert mechs["600000.SH"].stop_price == pytest.approx(9.2)
    assert bc.discipline_labels(stop_pct, tpr) == ["章程止损线 −8.0%", "回落止盈 10.0%"]


def test_discipline_labels_degrade_without_numbers_when_charter_missing(isolated_env):
    """无现役章程 → 指纹为空,标签退化成**不带数字**的说法(禁把「−5%」写进模板)。"""
    stop_pct, tpr = bc.resolve_charter_pcts(isolated_env.db_path)
    assert (stop_pct, tpr) == (None, None)
    assert bc.discipline_labels(stop_pct, tpr) == [
        "章程止损线(现役章程未配置比例)", "回落止盈(现役章程未配置比例)"]
    j = _card(stop_pct=None, take_profit_retrace=None,
              mechs={"600000.SH": _mech(stop_price=None)}).to_card_json()
    assert j["members"][0]["mech"]["stop_price"] is None
    assert j["invalidation_spec"]["members"][0][bc.COND_CLOSE_BELOW_STOP_LINE] is None
    assert j["invalidation_spec"]["stop_pct"] is None


def test_discipline_labels_says_no_mechanical_retrace_under_k8(isolated_env):
    """V2.4.0 P3.1:章程**读到了**(`stop_pct` 有值)、只是没配回落止盈
    (`v2.3-k8` 起的常态,K8.md §十三)→ 「本版无机械回落止盈」,⛔ 不是
    「未配置比例」那句(那句是给"整份指纹都没读到"用的,见上一条用例的对照)。"""
    assert bc.discipline_labels(0.05, None) == [
        "章程止损线 −5.0%", bc_charter_copy.RETRACE_DISABLED_COPY]
    # 🔴 复审 🟡-4 正向:`v2.3-k8`(advisory)口径下线名随章程换,⛔ 不再恒印「止损」。
    assert bc.discipline_labels(0.05, None, advisory=True) == [
        "章程亏损警戒线 −5.0%", bc_charter_copy.RETRACE_DISABLED_COPY]


def _docstring_free(path: Path):
    """`(逐行代码文本〔docstring 整段抹成空行〕, 非 docstring 的 ast.Constant 列表)`。

    **为什么要抹 docstring**:本块的禁令本身就写在 docstring 里(「禁硬编 0.05」、
    「不复用 `judge._parse_verdict`」)—— 拿纯文本 grep 会把"写明禁止"当成"违反禁止",
    那种守门单测只会逼人把注释删掉,反而更糟。要抓的是**代码里真的用了它**。"""
    import ast

    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    doc_nodes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                doc_nodes.append(body[0].value)
    blanked = text.splitlines()
    for n in doc_nodes:
        for i in range(n.lineno - 1, (n.end_lineno or n.lineno)):
            blanked[i] = ""
    doc_ids = {id(n) for n in doc_nodes}
    consts = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and id(n) not in doc_ids]
    return "\n".join(blanked), consts


def test_no_hardcoded_discipline_numbers_in_source():
    """源码**代码部分**不许出现写死的纪律数字(plan:「禁把『−5%』『8%』写进
    模板」)—— 止损比例与回落止盈只能来自现役章程 config。f-string 前缀
    (`"章程止损 −"` + 变量)是**合规**的动态模板,只有"前缀后面直接跟数字"才违规。"""
    import re

    _, consts = _docstring_free(_REPO / "neckline" / "selection" / "basket_card.py")
    banned = [re.compile(p) for p in (r"−\s*\d+(\.\d+)?%", r"章程止损线?\s*−?\s*\d",
                                      r"回落止盈\s*\d")]
    for node in consts:
        v = node.value
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            assert float(v) not in (0.05, 0.08), f"第 {node.lineno} 行出现硬编纪律比例 {v}"
        if isinstance(v, str):
            for pat in banned:
                assert not pat.search(v), f"第 {node.lineno} 行的字符串写死了纪律数字:{v!r}"


# ══════════════════════════════════════════════════════════════════════════
# 结构化 spec:⑧ 的唯一判据源
# ══════════════════════════════════════════════════════════════════════════

def test_verification_and_invalidation_specs_are_machine_consumable():
    j = _card().to_card_json()
    v, iv = j["verification_spec"], j["invalidation_spec"]
    assert v["require"] == [bc.COND_CLOSE_AT_OR_ABOVE_REF, bc.COND_HOLDS_MA20]
    assert v["members"][0][bc.COND_CLOSE_AT_OR_ABOVE_REF] == 10.0
    assert v["members"][0][bc.COND_HOLDS_MA20] == 9.2
    assert v["member_count"] == 1 and v["min_members_hit"] == 1
    # ⑦-b-B:失效第 ③ 条由「收盘 < MA20」单条改成复合条件(< D0 收盘 **且** < D0 MA20)
    assert iv["any_of"] == [bc.COND_CLOSE_BELOW_STOP_LINE, bc.COND_LIMIT_DOWN_TOUCH,
                            bc.COND_BELOW_REF_AND_MA20]
    assert iv["members"][0][bc.COND_CLOSE_BELOW_STOP_LINE] == 9.5
    assert iv["members"][0][bc.COND_LIMIT_DOWN_TOUCH] == 9.0
    assert iv["members"][0][bc.COND_BELOW_REF_AND_MA20] == {"ref_close": 10.0, "ma20": 9.2}
    assert iv["stop_pct"] == 0.05
    # 每条件都带人读描述,⑧ 落地时不必反查代码
    assert all(c["desc"] and c["compare"] for c in v["conditions"] + iv["conditions"])


@pytest.mark.parametrize("n,expected", [(1, 1), (2, 1), (3, 2)])
def test_min_members_hit_is_ceil_half(n, expected):
    mechs = [bc.MemberMech(ts_code=f"{i}", close=10.0, ma20=9.0) for i in range(n)]
    spec = bc.build_verification_spec("b1", D0, mechs)
    assert spec["min_members_hit"] == expected


def test_spec_levels_are_null_when_data_missing_not_zero():
    """缺数据的那一条 → `null`(⑧ 见 null 就跳过这条,**不当成"不满足"**),
    绝不写 0 冒充(0 是个真实价位,与"算不出"撞车)。"""
    spec = bc.build_verification_spec("b1", D0, [bc.MemberMech(ts_code="x")])
    assert spec["members"][0][bc.COND_HOLDS_MA20] is None
    assert spec["members"][0][bc.COND_CLOSE_AT_OR_ABOVE_REF] is None
    assert spec["evaluable_members"] == 0


# ── spec ⇄ 人读条款 一致性(A6-①,契约线审计 🔵 B6-①:V1 的
#    `test_candidates.py::test_invalidation_spec_and_text_consistent` 随候选管线
#    陪葬后没重建。V2 的等价对象 = 卡上**双份条款**:结构化半份(`verification_spec` /
#    `invalidation_spec`,⑧ 的唯一判据源)与人读半份。人话半份由 LLM 写、机器验不了,
#    但**喂给它的那份人读阈值块**(`spec_threshold_text`,plan 验收点名的通路,prompt
#    里写死「人话条款必须与机械阈值同频」)是机械生成的 —— 它一旦与 spec 讲不一样的话,
#    LLM 手上的条款和盘中判定用的条款就不是一回事了。这条测试锁的就是这个通路。────

def _spec_pair(mechs, *, stop_pct: Optional[float] = 0.05):
    v = bc.build_verification_spec("b1", D0, list(mechs))
    iv = bc.build_invalidation_spec("b1", D0, list(mechs), stop_pct=stop_pct)
    return v, iv


def test_spec_text_names_every_condition_and_every_threshold():
    """正例:每条条件码的人读描述、每个非空阈值、两侧的聚合门槛,都必须出现在
    人读块里 —— 一条都不许在"翻译"过程中掉队。"""
    mechs = [_mech("600000.SH"), _mech("600001.SH", close=20.0, ma20=18.4,
                                       limit_up=22.0, limit_down=18.0, stop_price=19.0)]
    v, iv = _spec_pair(mechs)
    text = bc.spec_threshold_text(v, iv)

    # ① 条件码 → 人读描述(单一源 `verification_rules.COND_DESC`,不在渲染层另拍文案)
    for code in v["require"] + iv["any_of"]:
        assert bc._COND_DESC[code] in text, f"人读块缺条件描述:{code}"
    # ② 两侧聚合门槛(判「几只成员算数」的那个数)
    assert f"≥ {v['min_members_hit']}" in text and f"≥ {iv['min_members_hit']}" in text
    # ③ 逐成员的每个非空阈值都出现(两位小数,与 spec 里的数值同一个)
    for row in v["members"]:
        assert row["ts_code"] in text
        for key in (bc.COND_CLOSE_AT_OR_ABOVE_REF, bc.COND_HOLDS_MA20):
            assert f"{row[key]:.2f}" in text, f"验证侧阈值没进人读块:{row['ts_code']} {key}"
    for row in iv["members"]:
        assert f"{row[bc.COND_CLOSE_BELOW_STOP_LINE]:.2f}" in text
        assert f"{row[bc.COND_LIMIT_DOWN_TOUCH]:.2f}" in text
        both = row[bc.COND_BELOW_REF_AND_MA20]
        assert f"{both['ref_close']:.2f}" in text and f"{both['ma20']:.2f}" in text
    # ④ 形状版本两侧各自透出(冻结件跨版本回看要认得出是哪套形状)
    assert v["spec_version"] in text and iv["spec_version"] in text


def test_spec_text_says_not_judged_where_the_spec_is_null_and_invents_no_number():
    """负例(本条是这套测试的重点):spec 里是 `null` 的那条,人读块必须说成
    「不判 / 算不出」,**⛔ 不许出现任何编出来的数字**。null 被翻译成一个具体价位 =
    LLM 拿着一个系统根本不会判的阈值去写条款,正是「没有」与「没看」混为一谈。"""
    blind = _mech("600002.SH", close=None, ma20=None,
                  limit_up=None, limit_down=None, stop_price=None)
    v, iv = _spec_pair([blind], stop_pct=None)
    text = bc.spec_threshold_text(v, iv)

    assert v["members"][0][bc.COND_HOLDS_MA20] is None          # 前提:spec 真的是 null
    assert iv["members"][0][bc.COND_LIMIT_DOWN_TOUCH] is None
    assert text.count("不判") >= 5                              # 五处阈值全部如实标
    assert "0.00" not in text                                   # ⛔ 没有把 null 写成 0
    # 该出现的仍要出现:条件描述与门槛不因缺数据而消失(条款还在,只是这次判不了)
    for code in v["require"] + iv["any_of"]:
        assert bc._COND_DESC[code] in text


def test_spec_text_prints_the_stop_line_from_the_spec_not_a_hardcoded_multiplier():
    """止损线那一格必须**原样取 spec 里的数**(它由现役章程 `stop_pct` 算出),⛔ 渲染层
    不许自己乘一个 0.95 —— 那样章程一换,人读条款与盘中判定就各说各话。用一个不可能
    被巧合命中的价位(8.37)证明数字真是从 spec 流过来的。"""
    odd = _mech(stop_price=8.37)
    text = bc.spec_threshold_text(*_spec_pair([odd]))
    assert "止损线 8.37" in text
    assert f"{odd.close * 0.95:.2f}" not in text.split("止损线")[1][:12]


def test_structured_thresholds_reach_the_llm_context():
    """plan 验收:**结构化阈值确实出现在喂给 LLM 的上下文里**(剧本与盘中自动警报
    同频,v1.5-①-A 体例)。"""
    mechs = {"600000.SH": _mech()}
    v = bc.build_verification_spec("b1", D0, list(mechs.values()))
    iv = bc.build_invalidation_spec("b1", D0, list(mechs.values()), stop_pct=0.05)
    ctx = bc.build_card_context(_basket(), D0, mechs, v, iv, tier_decision=_FakeDecision(),
                                discipline=bc.discipline_labels(0.05, 0.08))
    assert "9.50" in ctx          # 止损线
    assert "10.00" in ctx         # 基准收盘 / 验证阈值
    assert "9.20" in ctx          # MA20
    assert "[9.00, 11.00]" in ctx  # 次日涨跌停闭区间(夹逼的锚)
    assert bc.VERIFY_SPEC_VERSION in ctx and bc.INVALIDATE_SPEC_VERSION in ctx
    assert "章程止损线 −5.0%" in ctx
    # 日期锚(`prompt_context` 唯一实现)必须在第一行
    assert ctx.splitlines()[0].startswith("今天是")
    assert "证据" in ctx and "2024-04-07" in ctx


def test_card_context_reports_when_limit_band_is_unavailable():
    mechs = {"600000.SH": _mech(limit_up=None, limit_down=None,
                                no_limit_reason="涨跌停价计算返回空")}
    v = bc.build_verification_spec("b1", D0, list(mechs.values()))
    iv = bc.build_invalidation_spec("b1", D0, list(mechs.values()))
    ctx = bc.build_card_context(_basket(), D0, mechs, v, iv)
    assert "次日涨跌停算不出" in ctx and "涨跌停价计算返回空" in ctx


# ══════════════════════════════════════════════════════════════════════════
# ⑥ disclaimer 单一源
# ══════════════════════════════════════════════════════════════════════════

def test_disclaimer_is_single_sourced():
    src = (_REPO / "neckline" / "selection" / "basket_card.py").read_text(encoding="utf-8")
    assert src.count("参考,非指令 —— 买卖与终选在你") == 1
    assert _card().to_card_json()["disclaimer"] == bc.BASKET_CARD_DISCLAIMER
    # 降级卡也照带(disclaimer 与 LLM 成败无关)
    assert _card(payload=None, llm_stage=bc.LLM_NO_PROVIDER).to_card_json()["disclaimer"] \
        == bc.BASKET_CARD_DISCLAIMER


# ══════════════════════════════════════════════════════════════════════════
# LLM 段:降级、预算、标签劫持防护
# ══════════════════════════════════════════════════════════════════════════

def test_llm_absent_still_yields_structured_half():
    """plan 的降级规格:**结构化半份照出、LLM 半份缺席如实标注**。"""
    j = _card(payload=None, llm_stage=bc.LLM_NO_PROVIDER, narrative="").to_card_json()
    assert j["degraded"] is True and j["llm_stage"] == bc.LLM_NO_PROVIDER
    assert j["upside_path"] is None
    assert j["upside_path_unavailable_reason"] == "本次未生成预期上涨路径(no_provider)"
    assert j["verification_text"] is None and j["invalidation_text"] is None
    assert j["risks"] == []
    # …但机械件一项不少
    assert j["verification_spec"]["members"][0][bc.COND_CLOSE_AT_OR_ABOVE_REF] == 10.0
    assert j["members"][0]["mech"]["stop_price"] == 9.5
    assert j["fingerprint"]["charter_version"] == "v1.3.3"
    assert j["disclaimer"] and j["discipline_labels"]


def test_run_card_llm_no_provider_and_budget_exhausted():
    ledger = BudgetLedger()
    assert bc.run_card_llm("ctx", provider=None, ledger=ledger)[2] == bc.LLM_NO_PROVIDER
    ledger.spend(LEDGER_REASON, ledger.limits[LEDGER_REASON] + 1)
    assert bc.run_card_llm("ctx", provider=_StubProvider(), ledger=ledger)[2] \
        == bc.LLM_BUDGET_EXHAUSTED


def test_run_card_llm_spends_reason_ledger_only():
    """卡的 LLM 段走**推理账**;检索账一分不动(三本账互不透支)。"""
    ledger = BudgetLedger()
    bc.run_card_llm("ctx", provider=_StubProvider(_reply(_payload())), ledger=ledger)
    assert ledger.spent[LEDGER_REASON] >= 0.0
    assert ledger.spent[LEDGER_SEARCH] == 0.0


def test_run_card_llm_does_not_enable_search():
    """⑦ 不联网:证据在 ⑤ 的检索段已取过,这里只做归纳与表达。"""
    p = _StubProvider(_reply(_payload()))
    bc.run_card_llm("ctx", provider=p, ledger=BudgetLedger())
    assert p.calls[0]["enable_search"] is False


def test_run_card_llm_exception_and_parse_failure():
    n, payload, stage = bc.run_card_llm("ctx", provider=_StubProvider(raises=True),
                                        ledger=BudgetLedger())
    assert payload is None and stage.startswith(bc.LLM_CALL_FAILED)

    bad = LLMResult(ok=True, provider="stub", model="m", content="没有围栏也没有 JSON")
    n2, payload2, stage2 = bc.run_card_llm("ctx", provider=_StubProvider(bad),
                                           ledger=BudgetLedger())
    assert payload2 is None and stage2 == bc.LLM_PARSE_FAILED
    assert n2 == "没有围栏也没有 JSON"

    nok = LLMResult(ok=False, provider="stub", model="m", content="", reason="429")
    assert bc.run_card_llm("ctx", provider=_StubProvider(nok),
                           ledger=BudgetLedger())[2].startswith(bc.LLM_CALL_FAILED)


def test_json_is_stripped_before_narrative_is_kept():
    """v1.5.1 标签劫持案的体例:结构化产出走 `json_block` **先剥再用**,用户看到的
    叙述里不残留 JSON(§2.7)。本链路没有结论标签,也不复用 `_parse_verdict`。"""
    p = _StubProvider(_reply(_payload(), narrative="叙述部分。"))
    narrative, payload, stage = bc.run_card_llm("ctx", provider=p, ledger=BudgetLedger())
    assert stage == bc.LLM_OK
    assert "```json" not in narrative and "upside_path" not in narrative
    assert narrative.strip() == "叙述部分。"
    assert payload["upside_path"].startswith("补贴细则落地")


def test_basket_card_module_does_not_reuse_verdict_parser():
    code, _ = _docstring_free(_REPO / "neckline" / "selection" / "basket_card.py")
    assert "_parse_verdict" not in code
    assert "neckline.llm.judge" not in code and "from neckline.llm import judge" not in code
    # 本链路**没有**结论标签,prompt 也不该要一个(有标签又在后面挂 JSON = v1.5.1 案底)
    assert "结论:" not in bc.CARD_SYSTEM_PROMPT
    # 只允许 import 哨兵的元数据查询,不许 import 任何判定模块
    assert "from neckline.sentinel.universe import load_stock_meta" in code
    for banned in ("sentinel.holding", "sentinel.retreat", "sentinel.entry",
                   "sentinel.invalidation", "sentinel.precall", "sentinel.circuit"):
        assert banned not in code, banned
    # ⑥⑦ 不互相 import(plan §五【跨块】D 条)
    assert "selection.tier" not in code and "from neckline.selection import tier" not in code


def test_upside_path_is_one_paragraph_not_branches():
    """V2.3.3-①:卡 #6 只收 `upside_path` 一段话。**老 `scripts` 三格即使还在
    payload 里也一个字都不进新卡** —— 兼容发生在读侧(`_upside_path_present` 的 OR),
    ⛔ 不在写侧回捞老键(那会让新卡里长出一个本版已经不问的东西)。"""
    j = _card(payload=_payload(upside_path="沿缺口上沿反复确认后拾级而上")).to_card_json()
    assert j["upside_path"] == "沿缺口上沿反复确认后拾级而上"
    legacy = dict(_payload()); legacy.pop("upside_path")
    legacy["scripts"] = {"strong": "高开怎么做", "flat": None, "weak": None}
    j2 = _card(payload=legacy).to_card_json()
    assert j2["upside_path"] is None and "scripts" not in j2


def test_blank_upside_path_collapses_to_none():
    j = _card(payload=_payload(upside_path="   ")).to_card_json()
    assert j["upside_path"] is None and j["upside_path_unavailable_reason"]


def test_trade_plan_first_piece_accepts_both_v4_and_v3_card_shapes():
    """🔴 老卡兼容是硬要求(V2.3.3-①):`basket_cards` 是 `INSERT OR IGNORE` 的冻结件,
    新键**永不回填** —— 今天开仓读的可能是昨天冻的那张 v3 卡。只认 `upside_path` 会让
    昨天那批篮子今天全部"缺上涨判断",凭空多一条假警示。"""
    base = _card().to_card_json()
    entry = base["members"][0]

    v4 = dict(base)                                     # 新卡:只有 upside_path
    assert v4["upside_path"]
    assert bc.trade_plan_missing_pieces(v4) == []
    assert bc.member_trade_plan_missing(v4, entry) == []

    v3 = dict(base)                                     # 老卡:只有 scripts 三格
    v3.pop("upside_path"); v3.pop("upside_path_unavailable_reason")
    v3["scripts"] = {"strong": "高开怎么做", "flat": None, "weak": None}
    assert bc.trade_plan_missing_pieces(v3) == []
    assert bc.member_trade_plan_missing(v3, entry) == []

    blank = dict(base)                                  # 两键都空 → 恰缺第 1 件
    blank["upside_path"] = None
    blank["scripts"] = {"strong": "  ", "flat": None, "weak": ""}
    assert bc.trade_plan_missing_pieces(blank) == ["upside_script"]
    assert bc.member_trade_plan_missing(blank, entry) == ["upside_script"]
    # 🔴 判据码字符串一字不改(它已写进历史 plan_json);只有中文标签换了。
    assert "upside_script" in bc.TRADE_PLAN_PIECES
    assert bc.TRADE_PLAN_PIECE_LABELS["upside_script"] == "上涨判断(预期上涨路径)"


# ══════════════════════════════════════════════════════════════════════════
# ④【事务 2】落库:冻结 + 追加版本制 +「有篮子无卡」
# ══════════════════════════════════════════════════════════════════════════

def _count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _save(db_path: Path, card: bc.BasketCard, basket_id: int = 1, version: int = 1):
    return basket_store.save_basket_card(
        basket_id, card.to_card_json(), version=version,
        stop_pct=card.stop_pct, take_profit_retrace=card.take_profit_retrace,
        charter_version=card.charter_version, pack_version=card.pack_version,
        engine_api_version=card.engine_api_version, db_path=db_path,
    )


def test_save_basket_card_writes_only_basket_cards(isolated_env):
    """⑦ **只写 `basket_cards` 一张表**(另三张归 ⑥ 的事务 1)。"""
    stats = _save(isolated_env.db_path, _card())
    assert stats["cards_inserted"] == 1 and stats["frozen_conflicts"] == []
    assert _count(isolated_env.db_path, "basket_cards") == 1
    for other in ("baskets", "basket_members", "tier_history"):
        assert _count(isolated_env.db_path, other) == 0


def test_freeze_second_write_of_same_version_is_refused(isolated_env, caplog):
    """plan 验收:同 `(basket_id, version)` 二次写 → 拒。"""
    _save(isolated_env.db_path, _card())
    with caplog.at_level("WARNING"):
        again = _save(isolated_env.db_path, _card())
    assert again["cards_inserted"] == 0 and again["cards_existing"] == 1
    assert _count(isolated_env.db_path, "basket_cards") == 1
    assert "幂等跳过、不覆盖既有行" in caplog.text


def test_freeze_conflict_is_recorded_never_silently_overwritten(isolated_env, caplog):
    """重跑算出**不一样**的卡 → 库里仍是首跑那份,差异 WARNING + `frozen_conflicts`
    带出给报告层如实披露(「藏起来不是诚实」)。"""
    _save(isolated_env.db_path, _card())
    changed = _card(payload=_payload(risks=["完全不同的风险"]))
    with caplog.at_level("WARNING"):
        stats = _save(isolated_env.db_path, changed)
    assert stats["cards_existing"] == 1 and len(stats["frozen_conflicts"]) == 1
    assert "未采纳" in stats["frozen_conflicts"][0]
    stored = basket_store.load_basket_card(1, db_path=isolated_env.db_path)
    assert stored["card"]["risks"] == ["补贴细则落地节奏可能慢于预期", "板块昨日已大涨,追高风险"]


def test_same_d0_rerun_can_backfill_version_1(isolated_env):
    """「有篮子、无卡」是合法中间态:同一 D0 内重跑**补** `version=1`(仍是 D0 原判)。"""
    assert basket_store.load_basket_card(7, db_path=isolated_env.db_path) is None
    stats = _save(isolated_env.db_path, _card(), basket_id=7)
    assert stats["cards_inserted"] == 1
    assert basket_store.load_basket_card(7, db_path=isolated_env.db_path)["version"] == 1


def test_append_only_versioning_leaves_d0_untouched(isolated_env):
    """D+1 只能追加:写 version=2,**D0 行一字不改**。"""
    _save(isolated_env.db_path, _card())
    assert basket_store.next_card_version(1, db_path=isolated_env.db_path) == 2
    v2 = _card(payload=_payload(risks=["D+1 新增的风险"]), version=2)
    _save(isolated_env.db_path, v2, version=2)
    rows = sqlite3.connect(str(isolated_env.db_path)).execute(
        "SELECT version, card_json FROM basket_cards WHERE basket_id=1 ORDER BY version"
    ).fetchall()
    assert [r[0] for r in rows] == [1, 2]
    assert json.loads(rows[0][1])["risks"][0] == "补贴细则落地节奏可能慢于预期"
    assert json.loads(rows[1][1])["risks"] == ["D+1 新增的风险"]
    # 默认读最新版本
    assert basket_store.load_basket_card(1, db_path=isolated_env.db_path)["version"] == 2
    assert basket_store.load_basket_card(1, version=1, db_path=isolated_env.db_path)["version"] == 1


def test_next_card_version_on_empty_table(isolated_env):
    assert basket_store.next_card_version(42, db_path=isolated_env.db_path) == 1


def test_save_basket_card_rejects_version_zero(isolated_env):
    with pytest.raises(ValueError, match="version 必须 ≥1"):
        basket_store.save_basket_card(1, {"a": 1}, version=0, db_path=isolated_env.db_path)


def test_save_basket_cards_batch(isolated_env):
    cards = {1: _card().to_card_json(), 2: _card().to_card_json()}
    meta = {1: {"stop_pct": 0.05, "charter_version": "v1.3.3"}, 2: {"stop_pct": 0.05}}
    stats = basket_store.save_basket_cards(cards, meta_by_basket_id=meta,
                                           db_path=isolated_env.db_path)
    assert stats["cards_inserted"] == 2
    assert basket_store.load_basket_card(2, db_path=isolated_env.db_path)["stop_pct"] == 0.05


def test_card_json_round_trips_through_sqlite(isolated_env):
    card = _card()
    _save(isolated_env.db_path, card)
    stored = basket_store.load_basket_card(1, db_path=isolated_env.db_path)
    assert stored["card"] == json.loads(json.dumps(card.to_card_json(), ensure_ascii=False))
    assert stored["charter_version"] == "v1.3.3" and stored["pack_version"] == "K7-pack-v1"


# ══════════════════════════════════════════════════════════════════════════
# 编排入口 `build_cards`
# ══════════════════════════════════════════════════════════════════════════

def test_build_cards_is_offline_by_default(isolated_env, monkeypatch):
    """`use_llm` 默认 False —— 传了 provider 也不会调用(同 ⑥ `score_and_tier` 姿势)。"""
    monkeypatch.setattr(mt, "load_tag_panel_rows", lambda *a, **k: {})
    p = _StubProvider(_reply(_payload()))
    cards = bc.build_cards([_basket()], D0, provider=p, db_path=isolated_env.db_path,
                           parquet_dir=isolated_env.parquet_dir,
                           close_of={"600000.SH": 10.0})
    assert p.calls == []
    assert len(cards) == 1 and cards[0].llm_stage == bc.LLM_DISABLED
    assert cards[0].degraded is True


def test_build_cards_with_llm_and_per_basket_fuse(isolated_env, monkeypatch):
    monkeypatch.setattr(mt, "load_tag_panel_rows",
                        lambda codes, *a, **k: {c: {"close": 10.0, "ma20": 9.2} for c in codes})
    seed_active_rule_v1(isolated_env)
    p = _StubProvider(_reply(_payload()))
    cards = bc.build_cards(
        [_basket("b1"), _basket("b2")], D0, provider=p, use_llm=True,
        tier_by_basket_key={"b1": _FakeDecision(), "b2": _FakeDecision(tier=2, rank=1)},
        db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir,
    )
    assert len(cards) == 2 and len(p.calls) == 2
    assert all(c.llm_stage == bc.LLM_OK for c in cards)
    assert cards[0].stop_pct == 0.05 and cards[0].tier == 1 and cards[1].tier == 2
    # 上下文里带了结构化阈值(**先算 spec、再喂 LLM**)
    user_msg = p.calls[0]["messages"][1].content
    assert bc.VERIFY_SPEC_VERSION in user_msg


def test_build_cards_survives_one_bad_basket(isolated_env, monkeypatch, caplog):
    """一张卡炸了不牵连其余(「有篮子无卡」合法,不回删篮子、不抛异常)。"""
    monkeypatch.setattr(mt, "load_tag_panel_rows", lambda *a, **k: {})
    bad = _basket("bad")
    object.__setattr__(bad, "members", "不是可迭代的成员元组而是字符串")
    with caplog.at_level("ERROR"):
        cards = bc.build_cards([bad, _basket("ok")], D0, db_path=isolated_env.db_path,
                               parquet_dir=isolated_env.parquet_dir)
    assert [c.basket_key for c in cards] == ["ok"]


def test_build_basket_card_requires_mechs():
    with pytest.raises(ValueError, match="`mechs` 必填"):
        bc.build_basket_card(_basket(), D0)


def test_build_member_mech_handles_missing_meta(isolated_env):
    m = bc.build_member_mech({"600000.SH": 10.0}, D0, stop_pct=0.05,
                             db_path=isolated_env.db_path)["600000.SH"]
    assert m.limit_up is None and m.limit_down is None
    assert "查无股票元数据" in (m.no_limit_reason or "")
    assert m.stop_price == 9.5      # 止损线不依赖涨跌停,照算


def test_build_member_mech_rejects_non_positive_close(isolated_env):
    m = bc.build_member_mech({"600000.SH": 0.0}, D0, stop_pct=0.05,
                             db_path=isolated_env.db_path)["600000.SH"]
    assert m.close is None and m.stop_price is None
    assert "收盘价缺失或非正" in (m.no_limit_reason or "")
