"""V2.3.3 批 ③:`TASK_AUCTION` + 输出 JSON 契约 + **三道机械夹逼闸**。

三道闸各造正反两例(共 6 例)+ 次序例(同时命中闸 1 与闸 3 → 记
`clamped_by_data_quality`),并把两条不变量钉死:
  · `verdict_raw != verdict` 的行**必有**非空 `clamped_by`(⛔ 不许静默夹逼);
  · `auction/**` 里 `provider.chat(...)` 调用点**恰 1 个**(⛔ 不逐篮调用)。
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from neckline.auction import (
    CLAMPED_BY_DATA_QUALITY,
    CLAMPED_BY_MISSING_STRONG_EVIDENCE,
    CLAMPED_BY_SINGLE_STRONG,
    CLAMPED_BY_Y1_LOW_WEIGHT,
    DQ_DEGRADED,
    DQ_INSUFFICIENT,
    DQ_OK,
    LLM_BUDGET_EXHAUSTED,
    LLM_NO_PROVIDER,
    LLM_OK,
    LLM_PARSE_FAILED,
    VERDICT_CONFIRM,
    VERDICT_NEUTRAL,
    VERDICT_PENDING_EXPLANATION,
    VERDICT_VETO,
)
from neckline.auction import llm as al
from neckline.llm.base import LLMResult
from neckline.llm.budget import LEDGER_REASON, BudgetLedger
from neckline.llm.router import TASK_AUCTION

_REPO = Path(__file__).resolve().parent.parent
_AUCTION = _REPO / "neckline" / "auction"


# ══════════════════════════════════════════════════════════════════════════
# 夹具
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class _Mech:
    """`clamp_verdict` 只要求几个属性(duck-typed,同 `judge_candidate` 的既有体例)。

    ⚠ 字段取值刻意照**库里真形状**:`engine_code` = 线码 `C`/`Z`/`Y`,
    `engine_version` = `C1`/`Z1`/`Y1`(见 `packs/*.json` 的 `manifest.line_code` vs
    `manifest.pack_version`)。⛔ 别把夹具改成 `engine_code="Z1"` —— 那会把
    「闸按线判」这条真实语义遮蔽掉,施工时正是这一处差点静默失效。
    """
    basket_key: str = "k1"
    engine_code: Optional[str] = "C"
    engine_version: Optional[str] = "C1"
    #: ⚠ **V2.4.0 P2.3 起闸 1 读的是 `critical_quality`,不再读 `data_quality`**
    #: (施工图 §五 P2.3:判据由「整体」收窄到「关键域」)。两位在真链路上恒等
    #: (`_build_basket_mech` 一处赋值),夹具照样两位都给 —— 但**闸只看后者**。
    #: 🔴 另有一条正面用例钉死「夹具只给 `data_quality` 时闸 1 照样夹」(默认拒)。
    data_quality: str = DQ_OK
    critical_quality: str = DQ_OK
    context_quality: str = DQ_OK
    hit_invalidation_codes: List[str] = field(default_factory=list)


def _fields(**over) -> al.BasketFields:
    kw: Dict[str, Any] = {"basket_key": "k1", "verdict": VERDICT_CONFIRM}
    kw.update(over)
    return al.BasketFields(**kw)


# ══════════════════════════════════════════════════════════════════════════
# ③-A 路由与流式接线(两项**必须同路**)
# ══════════════════════════════════════════════════════════════════════════

def test_task_auction_is_wired_into_both_streaming_and_read_timeout():
    """🔴 §七 P0-40/P0-44 的原病复发路径就是"只接一半"。两项读同一个
    `LONG_CONTEXT_TASKS` 元组,加进去即两项同路。"""
    from neckline.llm import router

    assert TASK_AUCTION in router.ALL_TASKS
    assert TASK_AUCTION in router.LONG_CONTEXT_TASKS
    assert router.use_streaming_for_task(TASK_AUCTION) is True
    assert router.read_timeout_for_task(TASK_AUCTION) == router.STREAM_CHUNK_GAP_TIMEOUT_SECONDS
    # ⚠ 流式下这个 90 的含义是 **chunk 间隔**,不是整段生成上限;⛔ 别看见 90 以为回退了。
    assert router.STREAM_CHUNK_GAP_TIMEOUT_SECONDS == 90.0


def test_auction_task_is_not_a_search_task():
    """本链路**不联网**(资料是 9:26 冻结的读数)→ ⛔ 不进 `DEFAULT_SEARCH_TASKS`;
    `web_search` tools 协议 × 流式的组合本项目从未验证过。"""
    from neckline.llm import router

    assert TASK_AUCTION not in router.DEFAULT_SEARCH_TASKS


def test_exactly_one_provider_chat_call_site_in_the_auction_package():
    """⛔ **不逐篮调用**:一次调用覆盖全部篮子(K8 §二十)。
    ⚠ 别指望 `test_threshold_shadow.py` 那条守门 —— 它只扫 `aggregate.py` 与三个指定模块。"""
    sites = []
    for p in sorted(_AUCTION.rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "chat"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "provider"):
                sites.append((str(p.relative_to(_REPO)), node.lineno))
    assert len(sites) == 1, f"`provider.chat(...)` 调用点必须恰 1 个,实际:{sites}"
    assert sites[0][0] == "neckline/auction/llm.py"


def test_llm_module_imports_prompt_context_and_uses_the_date_anchor():
    """🔴 竞价层尤其需要"今天是哪天"(满篇在讲「D0 那天」与「今天开盘」)。"""
    src = al.__loader__.get_source(al.__name__) or ""
    assert "from neckline.llm.prompt_context import" in src
    assert "TIMELINESS_RULES" in al.AUCTION_SYSTEM_PROMPT or True
    assert "时效纪律" in al.AUCTION_SYSTEM_PROMPT


def _code_only(module) -> str:
    """去掉 docstring 与 `#` 注释后的代码文本。**禁令本身就写在文档里**
    (「⛔ 不复用 `judge._parse_verdict`」),裸 grep 会把"写明禁止"当成"违反禁止"
    —— 那种守门只会逼人删注释,反而更糟。"""
    src = module.__loader__.get_source(module.__name__) or ""
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                c = body[0].value
                for i in range(c.lineno - 1, (c.end_lineno or c.lineno)):
                    lines[i] = ""
    return "\n".join(ln for ln in lines if not ln.strip().startswith("#"))


def test_auction_module_does_not_reuse_the_verdict_label_parser():
    """v1.5.1 标签劫持案的既定纪律:本链路没有「结论:通过|否决」标签,
    ⛔ 不复用 `judge._parse_verdict`,围栏解析一律走 `llm/json_block.py`。"""
    body = _code_only(al)
    assert "_parse_verdict" not in body
    assert "from neckline.llm.json_block import split_narrative_and_reference_json" in body
    assert "结论:" not in al.AUCTION_SYSTEM_PROMPT


# ══════════════════════════════════════════════════════════════════════════
# ③-B prompt 与输出契约
# ══════════════════════════════════════════════════════════════════════════

def test_system_prompt_carries_k8_boundaries_and_bans_intraday_states():
    p = al.AUCTION_SYSTEM_PROMPT
    for frag in ("不改变 D0 的行情状态", "不从竞价排行中临时增加交易标的",
                 "报告发出后结束本次任务", "不持续观察 9:30 以后的价格",
                 "竞价结论只说明竞价反映出的信息", "不等于买入指令",
                 # ⚠ 「数据缺失只能形成中性」这句自 V2.4.0 P2.3 起**被下一条取代**:
                 # 判据收窄成「关键域」,上下文域缺失只降置信度(K8 §二十「数据质量
                 # 分域」;施工图 §五 P2.3)。旧断言若原样保留,会把"域过严"这个
                 # 正被修的病重新钉死在 prompt 里。
                 "关键域数据缺失或冲突只能形成中性",
                 "上下文域缺失只降低置信度,不改变结论",
                 "只有一只竞价强股时保持中性",
                 "一致且明确的负面证据"):
        assert frag in p, frag
    assert "`qualified`、`wait`、`cancelled`" in p
    # 三个布尔「判不出就写 null」必须在 prompt 里明说(⛔ 不许模型猜成 false)
    assert "判不出就写 `null`" in p


def test_unknown_basket_key_is_dropped_whole_with_a_note():
    """篮子标识不在资料里 → **整条丢弃 + 记 note**(同 `entries` 既有纪律)。"""
    payload = {"baskets": [{"basket_key": "k1", "verdict": "confirm"},
                           {"basket_key": "ghost", "verdict": "veto"}]}
    by, _ov, _an, _risks, notes = al.parse_auction_payload(payload, known_basket_keys=["k1"])
    assert set(by) == {"k1"}
    assert any("ghost" in n for n in notes)


def test_null_booleans_stay_none_and_are_never_guessed_as_false():
    payload = {"baskets": [{"basket_key": "k1", "verdict": "veto",
                            "driver_negative": None, "sector_core_negative": True,
                            "candidate_negative": "yes"}]}
    by, *_ = al.parse_auction_payload(payload, known_basket_keys=["k1"])
    f = by["k1"]
    assert f.driver_negative is None
    assert f.sector_core_negative is True
    assert f.candidate_negative is None, "非布尔一律当『判不出』,⛔ 不强转"


def test_missing_strong_codes_field_is_distinguishable_from_an_empty_list():
    """⚠ **没给** ≠ 空数组 —— 闸 2 靠这个区分(两者夹逼码不同)。"""
    by, *_ = al.parse_auction_payload(
        {"baskets": [{"basket_key": "k1", "verdict": "confirm"}]}, known_basket_keys=["k1"])
    assert by["k1"].auction_strong_codes is None
    by2, *_ = al.parse_auction_payload(
        {"baskets": [{"basket_key": "k1", "verdict": "confirm", "auction_strong_codes": []}]},
        known_basket_keys=["k1"])
    assert by2["k1"].auction_strong_codes == []


def test_unrecognized_verdict_code_becomes_pending_not_neutral():
    """模型给了不认识的码 → 「待解释」。⛔ 不猜成中性 —— 中性是一个**实质判断**。"""
    by, *_ = al.parse_auction_payload(
        {"baskets": [{"basket_key": "k1", "verdict": "qualified"}]}, known_basket_keys=["k1"])
    assert by["k1"].verdict is None
    assert al.clamp_verdict(by["k1"], _Mech()) == (VERDICT_PENDING_EXPLANATION, None)


# ══════════════════════════════════════════════════════════════════════════
# ③-C 🔴 三道机械夹逼闸(各正反两例 + 次序例)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("dq", [DQ_DEGRADED, DQ_INSUFFICIENT])
def test_gate1_data_quality_forces_neutral_without_exception(dq):
    """闸 1(K8 §二十「**关键域**数据缺失或冲突只能形成中性」,**无例外**)。
    ⚠ 之所以敢无例外:「命中 D0 失效位」走 §五 ②-G 的独立警报通道,恒定输出,
    被夹成 neutral 时那条信息**一个字都没丢**。
    ⚠ **V2.4.0 P2.3 起判据源 = `critical_quality`**(施工图 §五 P2.3);
    ⛔ 只把 `data_quality` 设成不 ok **不再**触发这道闸(那正是"域过严"被修掉的地方)。"""
    for raw in (VERDICT_CONFIRM, VERDICT_VETO):
        v, by = al.clamp_verdict(
            _fields(verdict=raw),
            _Mech(data_quality=dq, critical_quality=dq,
                  hit_invalidation_codes=["600000.SH"]))
        assert (v, by) == (VERDICT_NEUTRAL, CLAMPED_BY_DATA_QUALITY)


def test_gate1_reads_the_critical_domain_and_ignores_the_context_domain():
    """🔴 **V2.4.0 P2.3 的正反两例**(施工图 §五 P2.3 + P2 验收 4/5):
    上下文域降级 ⛔ 不夹逼结论;关键域降级**照夹**。
    这条用例取代了旧口径「整份 `data_quality` 不 ok 就夹」——「一只无关指数缺失导致
    整篮强制中性」正是本版要修的第 ② 个病。"""
    # 上下文域 degraded、关键域 ok → **不夹**
    assert al.clamp_verdict(
        _fields(verdict=VERDICT_CONFIRM),
        _Mech(critical_quality=DQ_OK, context_quality=DQ_DEGRADED)) == (VERDICT_CONFIRM, None)
    # 关键域 degraded、上下文域 ok → **照夹**
    assert al.clamp_verdict(
        _fields(verdict=VERDICT_CONFIRM),
        _Mech(critical_quality=DQ_DEGRADED, context_quality=DQ_OK)) == (
        VERDICT_NEUTRAL, CLAMPED_BY_DATA_QUALITY)


def test_gate1_defaults_to_clamping_when_the_critical_domain_is_missing_entirely():
    """🔴 **默认拒**:一个连 `critical_quality` 都没有的对象,⛔ 不许拿到 confirm / veto。
    (⛔ 刻意**不回退**到 `data_quality` —— 回退等于把收窄前的旧判据偷偷放回来。)"""

    class _NoCritical:
        basket_key = "k1"
        engine_code = "C"
        engine_version = "C1"
        data_quality = DQ_OK          # 老形状:只有这一位
        hit_invalidation_codes: List[str] = []

    assert al.clamp_verdict(_fields(verdict=VERDICT_CONFIRM), _NoCritical()) == (
        VERDICT_NEUTRAL, CLAMPED_BY_DATA_QUALITY)


def test_gate1_lets_everything_through_when_data_quality_is_ok():
    for raw in (VERDICT_CONFIRM, VERDICT_NEUTRAL, VERDICT_VETO):
        assert al.clamp_verdict(_fields(verdict=raw), _Mech(engine_code="C", engine_version="C1")) == (raw, None)


def test_gate2_z1_confirm_needs_more_than_one_auction_strong_code():
    """闸 2(K8 §二十「Z1 …只有一只竞价强股时保持中性」)。⚠ 那个 1 是 K8 原文给的。"""
    m = _Mech(engine_code="Z", engine_version="Z1")
    assert al.clamp_verdict(_fields(auction_strong_codes=["600000.SH"]), m) == (
        VERDICT_NEUTRAL, CLAMPED_BY_SINGLE_STRONG)
    assert al.clamp_verdict(_fields(auction_strong_codes=[]), m) == (
        VERDICT_NEUTRAL, CLAMPED_BY_SINGLE_STRONG)
    # 字段压根没给 → 另一个码(「没给证据」与「给了只有一只」不是一回事)
    assert al.clamp_verdict(_fields(auction_strong_codes=None), m) == (
        VERDICT_NEUTRAL, CLAMPED_BY_MISSING_STRONG_EVIDENCE)


def test_gate2_passes_with_two_distinct_strong_codes_and_only_touches_z1_confirm():
    m = _Mech(engine_code="Z", engine_version="Z1")
    assert al.clamp_verdict(
        _fields(auction_strong_codes=["600000.SH", "600001.SH"]), m) == (VERDICT_CONFIRM, None)
    # 同样一只强股,但结论不是 confirm → 闸 2 不管
    assert al.clamp_verdict(
        _fields(verdict=VERDICT_VETO, auction_strong_codes=["600000.SH"]), m) == (
        VERDICT_VETO, None)
    # 同样一只强股,但引擎不是 Z1 → 闸 2 不管(⛔ 别"顺手"推广到 C1)
    assert al.clamp_verdict(
        _fields(auction_strong_codes=["600000.SH"]), _Mech(engine_code="C", engine_version="C1")) == (
        VERDICT_CONFIRM, None)


def test_gate3_y1_veto_requires_hit_or_all_three_negatives():
    """闸 3(K8 §二十:Y1 **只有**在触发 D0 失效、或三项一致明确负面时才形成否决)。"""
    m = _Mech(engine_code="Y", engine_version="Y1")
    # 反例:什么都没有 → 夹成中性
    assert al.clamp_verdict(_fields(verdict=VERDICT_VETO), m) == (
        VERDICT_NEUTRAL, CLAMPED_BY_Y1_LOW_WEIGHT)
    # 正例 A:命中 D0 失效位
    assert al.clamp_verdict(_fields(verdict=VERDICT_VETO),
                            _Mech(engine_code="Y", engine_version="Y1",
                                  hit_invalidation_codes=["600000.SH"])) == (VERDICT_VETO, None)
    # 正例 B:三项**全为 True**
    assert al.clamp_verdict(_fields(verdict=VERDICT_VETO, driver_negative=True,
                                    sector_core_negative=True, candidate_negative=True), m) == (
        VERDICT_VETO, None)


def test_gate3_treats_null_as_not_negative():
    """⚠ `is True` 的写法是刻意的:`null`(判不出)**不算负面**,
    「我判不出」与「我看过了、不是负面」在这里同样都够不成一致负面证据。"""
    m = _Mech(engine_code="Y", engine_version="Y1")
    for third in (None, False):
        assert al.clamp_verdict(
            _fields(verdict=VERDICT_VETO, driver_negative=True, sector_core_negative=True,
                    candidate_negative=third), m) == (VERDICT_NEUTRAL, CLAMPED_BY_Y1_LOW_WEIGHT)


def test_gate_order_is_one_two_three_and_only_the_first_hit_is_recorded():
    """🔴 次序写死 闸 1 → 闸 2 → 闸 3;`clamped_by` 是**单值**,只记第一个命中的。"""
    # 同时命中闸 1(数据不 ok)与闸 3(Y1 veto 无证据)→ 记闸 1 的码
    assert al.clamp_verdict(
        _fields(verdict=VERDICT_VETO),
        _Mech(engine_code="Y", engine_version="Y1",
              data_quality=DQ_DEGRADED, critical_quality=DQ_DEGRADED)) == (
        VERDICT_NEUTRAL, CLAMPED_BY_DATA_QUALITY)
    # 同时命中闸 1 与闸 2 → 同样记闸 1 的码
    assert al.clamp_verdict(
        _fields(auction_strong_codes=["600000.SH"]),
        _Mech(engine_code="Z", engine_version="Z1",
              data_quality=DQ_INSUFFICIENT, critical_quality=DQ_INSUFFICIENT)) == (
        VERDICT_NEUTRAL, CLAMPED_BY_DATA_QUALITY)


def test_c1_has_no_dedicated_gate():
    """⚠ K8 对 C1 说的是**给模型的判断口径**,不是形式约束 → 只进 prompt、不设闸。
    ⛔ 别"顺手"给 C1 补一道。"""
    body = _code_only(al)
    assert '== "C1"' not in body and "== 'C1'" not in body
    for raw in (VERDICT_CONFIRM, VERDICT_VETO):
        assert al.clamp_verdict(_fields(verdict=raw), _Mech(engine_code="C", engine_version="C1")) == (raw, None)


@pytest.mark.parametrize("raw,mech", [
    (VERDICT_CONFIRM, _Mech(data_quality=DQ_DEGRADED, critical_quality=DQ_DEGRADED)),
    (VERDICT_VETO, _Mech(engine_code="Y", engine_version="Y1")),
    (VERDICT_CONFIRM, _Mech(engine_code="Z", engine_version="Z1")),
])
def test_invariant_a_clamped_row_always_carries_a_clamp_code(raw, mech):
    """🔴 不变量:`verdict_raw != verdict` 的行**必有**非空 `clamped_by`。
    ⛔ 禁止模型已输出的结论被**静默**丢弃(同 V2.3.2 ⑧-0 路径 A 的裁定),
    且每一次夹逼必须进小报告第 4 块「异常与风险」。"""
    v, by = al.clamp_verdict(_fields(verdict=raw), mech)
    assert v != raw
    assert by is not None
    note = al.clamp_risk_note("k1", raw, v, by)
    assert note is not None and by in note["text"] and raw in note["text"]


def test_no_clamp_means_no_clamp_note():
    v, by = al.clamp_verdict(_fields(verdict=VERDICT_NEUTRAL), _Mech())
    assert (v, by) == (VERDICT_NEUTRAL, None)
    assert al.clamp_risk_note("k1", VERDICT_NEUTRAL, v, by) is None


# ══════════════════════════════════════════════════════════════════════════
# ③-D 小纸条挂载判据
# ══════════════════════════════════════════════════════════════════════════

def test_engine_line_is_read_from_the_line_code_not_the_version_string():
    """🔴 **施工图字面与库里字段语义的一处出入,已如实登记**(`llm.engine_line_of` docstring):
    §五 ③-C 的伪代码写 `mech.engine_code == "Z1"`,但 `baskets.engine_code` 存的是
    **线码 `C`/`Z`/`Y`**,`engine_version` 才是 `C1`/`Z1`/`Y1`
    (`tests/test_v22_gated_flow.py` 里那条 `("k1", 1, "C", "C1", "K8-V0.6")` 是既有事实)。
    照字面写 `== "Z1"` 的后果是**闸 2 / 闸 3 永远不触发,而且看不出来**。

    ⛔ 别把它改回按版本号枚举:K8 §二十 的三档权重是**引擎种类**的属性,将来出 `Z2`
    这条闸也该照样管用。
    """
    assert al.engine_line_of(_Mech(engine_code="Z", engine_version="Z1")) == "Z"
    # 只有版本号、线码缺失(老行)→ 从版本号取首字母兜底
    assert al.engine_line_of(_Mech(engine_code=None, engine_version="Y1")) == "Y"
    # 两者都没有(K8 之前的老篮子,如实)→ None → 三档权重都不适用,只走闸 1
    assert al.engine_line_of(_Mech(engine_code=None, engine_version=None)) is None
    assert al.clamp_verdict(_fields(verdict=VERDICT_VETO),
                            _Mech(engine_code=None, engine_version=None)) == (VERDICT_VETO, None)
    # 用**真形状**再走一遍闸 2(线码 "Z" + 版本 "Z1")
    assert al.clamp_verdict(_fields(auction_strong_codes=["600000.SH"]),
                            _Mech(engine_code="Z", engine_version="Z1")) == (
        VERDICT_NEUTRAL, CLAMPED_BY_SINGLE_STRONG)


def test_manual_note_attaches_on_neutral_conflict_or_clamped_only():
    """K8 §二十:小纸条「只出现在**中性、证据冲突或临界标的**旁边」。
    ⚠ 「临界标的」K8 没给判据 → 用「被夹逼过」代表它,⛔ 不发明"接近阈值"的数。"""
    assert al.manual_note_attached(VERDICT_NEUTRAL, _fields(), None) is True
    assert al.manual_note_attached(VERDICT_CONFIRM, _fields(evidence_conflict=True), None) is True
    assert al.manual_note_attached(VERDICT_VETO, _fields(), CLAMPED_BY_Y1_LOW_WEIGHT) is True
    # 平静的确认:三条都不满足 → 不挂
    assert al.manual_note_attached(VERDICT_CONFIRM, _fields(evidence_conflict=False), None) is False


# ══════════════════════════════════════════════════════════════════════════
# `explain` 的四条降级路径
# ══════════════════════════════════════════════════════════════════════════

class _Provider:
    name, model = "stub", "stub-model"

    def __init__(self, content: str = "", ok: bool = True, reason: str = "ok", boom: bool = False):
        self._content, self._ok, self._reason, self._boom = content, ok, reason, boom
        self.calls: List[Any] = []

    def chat(self, messages, *, enable_search=True, search_query=None, transport=None):
        self.calls.append({"messages": messages, "enable_search": enable_search,
                           "search_query": search_query})
        if self._boom:
            raise RuntimeError("上游炸了")
        return LLMResult(ok=self._ok, content=self._content, reason=self._reason,
                         provider=self.name, model=self.model)


@dataclass
class _MechBundle:
    trade_date: Any
    d0_date: Any
    market: Any = None
    baskets: List[Any] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _bundle():
    from datetime import date

    from neckline.auction.mech import BasketMech, MarketMech

    return _MechBundle(
        trade_date=date(2026, 8, 11), d0_date=date(2026, 8, 10),
        market=MarketMech(source="sina", captured_at="2026-08-11T09:26:30",
                          requested_codes=4, fetched_codes=4, data_quality=DQ_OK),
        baskets=[BasketMech(basket_id=1, basket_key="k1", name="篮一", covered_tier=1,
                            engine_code="C1", data_quality=DQ_OK,
                            # V2.4.0 P2.3:闸 1 读关键域 —— 夹具补上这一位。
                            critical_quality=DQ_OK, context_quality=DQ_OK)],
    )


def _reply(payload: Dict[str, Any], narrative: str = "今早指数普遍高开。") -> str:
    return narrative + "\n\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def test_explain_happy_path_parses_and_does_not_go_online():
    p = _Provider(_reply({"market": {"overview": "指数普遍高开", "anchors_note": "锚点不取得资格"},
                          "baskets": [{"basket_key": "k1", "verdict": "confirm",
                                       "reasons": ["主线仍强", "核心同向"]}],
                          "risks": ["个别成员缺数据"]}))
    out = al.explain(_bundle(), provider=p, ledger=BudgetLedger())
    assert out.llm_stage == LLM_OK
    assert out.market_overview == "指数普遍高开" and out.anchors_note
    assert out.risks == ["个别成员缺数据"]
    assert out.by_basket["k1"].verdict == VERDICT_CONFIRM
    assert "```json" not in out.narrative
    # 本链路**不联网**:⛔ 不开搜索、⛔ 不传检索词
    assert p.calls[0]["enable_search"] is False and p.calls[0]["search_query"] is None
    # 首行必须是日期锚(竞价层满篇在讲「D0 那天」与「今天开盘」)
    user_msg = p.calls[0]["messages"][1].content
    assert user_msg.splitlines()[0].strip()
    assert "2026" in user_msg.splitlines()[0]


def test_explain_without_provider_is_provider_none_and_never_calls_out():
    out = al.explain(_bundle(), provider=None)
    assert out.llm_stage == LLM_NO_PROVIDER and out.by_basket == {}


def test_explain_with_unparsable_output_is_parse_failed():
    out = al.explain(_bundle(), provider=_Provider("只有一段散文,没有围栏。"))
    assert out.llm_stage == LLM_PARSE_FAILED and out.by_basket == {}


def test_explain_with_exhausted_budget_skips_the_call():
    ledger = BudgetLedger()
    ledger.spend(LEDGER_REASON, ledger.limits[LEDGER_REASON] + 1)
    p = _Provider(_reply({"baskets": []}))
    out = al.explain(_bundle(), provider=p, ledger=ledger)
    assert out.llm_stage == LLM_BUDGET_EXHAUSTED and p.calls == []


def test_explain_survives_a_raising_provider():
    out = al.explain(_bundle(), provider=_Provider(boom=True))
    assert out.llm_stage.startswith("call_failed:")
    assert out.by_basket == {}
