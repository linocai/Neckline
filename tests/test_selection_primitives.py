"""原语注册表单测(plan §五 V2-③)。覆盖:①特征白名单纯函数;②`Primitive`
构造期校验(kind 合法性 + inputs 白名单,含负例);③注册表防御性复核函数
(含合成坏注册表的负例,不必费力绕过 frozen dataclass 造坏对象);④参数 schema
校验;⑤首包 5 个原语的行为正确性 + **运行期访问锁**(`_SORT_KEY_INPUTS` 体例
平移:用"记录实际访问过哪些 row 键"的字典子类证明 impl 运行时真的不读 LLM
产出字段,不只是静态声明写得漂亮);⑥`neckline/selection/` 下模块级数值字面量
的白名单扫描(§五 V2-③「测试与守门」原文:全仓 grep 不出现可配阈值的模块级
字面量,白名单里显式列出确属引擎常量的项 + 写理由)。
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import pytest

from neckline.selection import primitives as prim

_SELECTION_DIR = Path(__file__).resolve().parent.parent / "neckline" / "selection"


# ══════════════════════════════════════════════════════════════════════════
# ① 特征白名单纯函数
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("feature", [
    "industry_strength_daily.industry_rank",
    "industry_strength_daily.persist_days",
    "corr_matrix_daily.corr",
    "limit_cluster_daily.cluster_size",
    "leader_structure_daily.rs_rank",
    "limit_derived.limit_up",
    "daily.close",
    "daily_basic.turnover_rate",   # `daily*` 无点号,故意同时覆盖 daily_basic
    "moneyflow_dc.net_amount",
    "k4_advisory.sections",
    "k4_advisory.avoid_flag",
    "industry_stage_daily.stage",   # V2-③-K7 新增第 10 个模式
])
def test_is_allowed_feature_accepts_whitelisted_patterns(feature: str):
    assert prim.is_allowed_feature(feature) is True


@pytest.mark.parametrize("feature", [
    "llm_judgments.verdict",          # LLM 产出
    "basket_cards.card_json",         # LLM 影响的冻结卡面
    "reference_plans.script_text",    # 参考件(第〇原则,永不进机械分)
    "tier_history.llm_reason",        # LLM 微调理由
    "baskets.driver",                 # LLM 产出的共同驱动文本
    "stock_basic.industry",           # 身份/分类数据,不在 8 个模式内(设计判断,非"漏了")
    "unknown_table.whatever",
    "",
])
def test_is_allowed_feature_rejects_non_whitelisted(feature: str):
    assert prim.is_allowed_feature(feature) is False


def test_allowed_features_pattern_count_matches_plan():
    """plan §五 V2-③ 原文逐字给了 8 个模式,V2-④ 新增 `ths_daily.*` 第 9 个
    (`surging_concept_seed` 原语需要读概念板块日线,见 `primitives.py` 模块头
    「V2-④ 新增 4 个原语」节),V2-③-K7 新增 `industry_stage_daily.*` 第 10 个
    (④b 产出的行业题材阶段表,见「V2-③-K7 新增」节)。本测试锁死数量,防止有人
    "顺手"多加/少加一个模式而没人注意到(改动这个集合是真正的架构决策,不该
    悄悄发生)。"""
    assert len(prim._ALLOWED_FEATURES) == 10
    assert "ths_daily.*" in prim._ALLOWED_FEATURES
    assert "industry_stage_daily.*" in prim._ALLOWED_FEATURES


# ══════════════════════════════════════════════════════════════════════════
# ② `Primitive` 构造期校验
# ══════════════════════════════════════════════════════════════════════════

def test_primitive_construction_succeeds_with_whitelisted_inputs():
    p = prim.Primitive(
        name="ok_one", kind="filter", inputs=("daily.close",),
        params_schema={}, impl=lambda row, **kw: True,
    )
    assert p.name == "ok_one"


def test_primitive_construction_rejects_bad_kind():
    with pytest.raises(ValueError, match="kind"):
        prim.Primitive(
            name="bad_kind", kind="not_a_kind", inputs=(),
            params_schema={}, impl=lambda row, **kw: True,
        )


@pytest.mark.parametrize("bad_input", [
    "llm_judgments.verdict",
    "basket_cards.card_json",
    "reference_plans.script_text",
    "stock_basic.industry",
])
def test_primitive_construction_rejects_non_whitelisted_inputs(bad_input: str):
    """§2.8-C 第 1 条「不进机械分」的构造期落地:引擎自己的代码都不可能注册出一个
    引用白名单外特征的原语,fail loud 在对象造出来那一刻,不是等到用的时候才发现。"""
    with pytest.raises(ValueError, match="白名单"):
        prim.Primitive(
            name="evil", kind="filter", inputs=(bad_input,),
            params_schema={}, impl=lambda row, **kw: True,
        )


def test_primitive_defaults_and_merge_params():
    p = prim.Primitive(
        name="with_defaults", kind="filter", inputs=(),
        params_schema={"a": {"type": "number", "default": 1.0}, "b": {"type": "string"}},
        impl=lambda row, **kw: kw,
    )
    assert p.defaults() == {"a": 1.0}   # "b" 无 default,不出现
    assert p.merge_params({"b": "x"}) == {"a": 1.0, "b": "x"}
    assert p.merge_params({"a": 9.0, "b": "x"}) == {"a": 9.0, "b": "x"}


def test_primitive_run_dispatches_to_impl_with_merged_params():
    p = prim.Primitive(
        name="echo", kind="filter", inputs=(),
        params_schema={"threshold": {"type": "number", "default": 5.0}},
        impl=lambda row, *, threshold: row["value"] >= threshold,
    )
    assert p.run({"value": 10.0}) is True             # 缺省阈值 5.0
    assert p.run({"value": 1.0}) is False
    assert p.run({"value": 1.0}, {"threshold": 0.5}) is True   # 显式覆盖


# ══════════════════════════════════════════════════════════════════════════
# ③ 注册表 + 防御性复核(含合成坏注册表负例)
# ══════════════════════════════════════════════════════════════════════════

def test_registry_contains_exactly_the_registered_primitives():
    """V2-③ 首包 5 原语 + V2-④ 市场扫描层 4 原语 + V2.2-① 新增 `industry_blacklist`
    (K8 §三 排除白酒的机器载体),共 10 个,不多不少(改动这个集合同样是架构
    决策,见上一测试同款纪律)。"""
    assert set(prim.PRIMITIVES) == {
        "stock_hygiene", "non_new_stock", "k4_advisory_gate",
        "industry_dominance_gate", "intel_rank_priority",
        "hot_industry_seed", "surging_concept_seed",
        "limit_cluster_seed", "anomaly_cluster_seed",
        "industry_blacklist",
    }


def test_validate_all_primitives_whitelisted_passes_on_real_registry():
    assert prim.validate_all_primitives_whitelisted() == []


def test_validate_all_primitives_whitelisted_catches_synthetic_bad_registry():
    """`scripts/activate_pack.py` 闸 2 调用本函数做防御性复核——真实注册表因为
    构造期已经拦过一次,永远测不出"这个函数有没有牙齿"。用鸭子类型的
    `SimpleNamespace` 合成一份坏注册表(不必费力绕过 frozen dataclass 的
    `__post_init__` 去构造一个本不该存在的坏 `Primitive` 对象)。"""
    bad_registry = {
        "evil": SimpleNamespace(
            name="evil", kind="filter", inputs=("llm_judgments.verdict",),
        ),
        "also_bad_kind": SimpleNamespace(
            name="also_bad_kind", kind="not_a_kind", inputs=(),
        ),
        "fine": SimpleNamespace(name="fine", kind="filter", inputs=("daily.close",)),
    }
    errors = prim.validate_all_primitives_whitelisted(bad_registry)
    assert any("evil" in e and "白名单" in e for e in errors)
    assert any("also_bad_kind" in e and "kind" in e for e in errors)
    assert not any("fine" in e for e in errors)


def test_inputs_violations_pure_function():
    assert prim._inputs_violations(("daily.close", "k4_advisory.sections")) == []
    assert prim._inputs_violations(("daily.close", "llm_judgments.verdict")) == ["llm_judgments.verdict"]


# ══════════════════════════════════════════════════════════════════════════
# ④ 参数 schema 校验
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def sample_primitive() -> prim.Primitive:
    return prim.Primitive(
        name="sample", kind="filter", inputs=(),
        params_schema={
            "n": {"type": "number", "default": 1.0},
            "flag": {"type": "boolean", "default": True},
            "mode": {"type": "string", "enum": ["a", "b"], "default": "a"},
            "items": {"type": "array", "items": "string", "default": []},
        },
        impl=lambda row, **kw: True,
    )


def test_validate_params_accepts_valid_and_partial_params(sample_primitive):
    assert prim.validate_params(sample_primitive, {}) == []
    assert prim.validate_params(sample_primitive, {"n": 2.0, "mode": "b"}) == []


def test_validate_params_rejects_unknown_key(sample_primitive):
    errors = prim.validate_params(sample_primitive, {"typo_key": 1})
    assert any("未声明的参数" in e and "typo_key" in e for e in errors)


def test_validate_params_rejects_type_mismatch(sample_primitive):
    errors = prim.validate_params(sample_primitive, {"n": "not-a-number"})
    assert any("期望类型 number" in e for e in errors)


def test_validate_params_rejects_bool_as_number():
    """`isinstance(True, int)` 为 `True`——`bool` 不该被 `number`/`integer` 类型
    悄悄接纳(同 `_TYPE_CHECKERS` 里显式排除 bool 的理由)。"""
    p = prim.Primitive(
        name="numeric", kind="filter", inputs=(),
        params_schema={"n": {"type": "number"}}, impl=lambda row, **kw: True,
    )
    errors = prim.validate_params(p, {"n": True})
    assert any("期望类型 number" in e for e in errors)


def test_validate_params_rejects_enum_violation(sample_primitive):
    errors = prim.validate_params(sample_primitive, {"mode": "c"})
    assert any("不在允许集合" in e for e in errors)


def test_validate_params_rejects_bad_array_item_type(sample_primitive):
    errors = prim.validate_params(sample_primitive, {"items": [1, 2]})
    assert any("数组元素类型应为 string" in e for e in errors)


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 首包 5 个原语:行为正确性 + 运行期访问锁(不读 LLM 产出字段)
# ══════════════════════════════════════════════════════════════════════════

class _KeyTrackingDict(dict):
    """记录 `__getitem__`/`get` 实际访问过哪些键的 dict 子类(`_SORT_KEY_INPUTS`
    体例平移,见 `tests/test_intel_candidates.py::_KeyTrackingDict`)。原语 impl
    里既有 `row.get(...)` 也可能有 `row[...]`,两个方法都要记账。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.accessed: set = set()

    def __getitem__(self, key):
        self.accessed.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.accessed.add(key)
        return super().get(key, default)


# LLM 产出字段代表样本(篮子/卡/复盘/Tier 微调的自由文本与判断字段)——首包 5 个
# 原语的 impl 无论如何都不该读到这些键,即便它们恰好出现在同一个 row 结构里。
_POISON_KEYS = frozenset({
    "driver", "llm_reason", "role_llm", "script_text", "veto_reason", "card_json",
    "evidence_status",
})


def _poisoned_row(**legit: Any) -> _KeyTrackingDict:
    row = dict(legit)
    row.update({k: f"POISON:{k}" for k in _POISON_KEYS})
    return _KeyTrackingDict(row)


# —— stock_hygiene ——————————————————————————————————————————————————————

@pytest.mark.parametrize("kwargs,expected", [
    (dict(close=5.0, amount_ma20=30000.0, ma20=4.8, board="MAIN", is_st=False), True),
    (dict(close=1.0, amount_ma20=30000.0, ma20=4.8, board="MAIN", is_st=False), False),   # close 太低
    (dict(close=5.0, amount_ma20=1000.0, ma20=4.8, board="MAIN", is_st=False), False),    # 均额太低
    (dict(close=5.0, amount_ma20=30000.0, ma20=None, board="MAIN", is_st=False), False),  # ma20 缺
    (dict(close=5.0, amount_ma20=30000.0, ma20=4.8, board="BSE", is_st=False), False),    # 板块不在白名单
    (dict(close=5.0, amount_ma20=30000.0, ma20=4.8, board="MAIN", is_st=True), False),    # ST
])
def test_stock_hygiene_behavior(kwargs, expected):
    row = _poisoned_row(**kwargs)
    assert prim.PRIMITIVES["stock_hygiene"].run(row) is expected
    assert row.accessed.isdisjoint(_POISON_KEYS)


def test_stock_hygiene_params_change_outcome():
    """换参数 → 结果跟着变(pack `config.seeds.stock_hygiene` 真被消费的最小
    证据):默认板块白名单排除 BSE,自定义板块白名单放行 BSE。"""
    row = dict(close=5.0, amount_ma20=30000.0, ma20=4.8, board="BSE", is_st=False)
    default_result = prim.PRIMITIVES["stock_hygiene"].run(dict(row))
    custom_result = prim.PRIMITIVES["stock_hygiene"].run(dict(row), {"allowed_boards": ["BSE"]})
    assert default_result is False
    assert custom_result is True


# —— industry_blacklist(V2.2-①,K8 §三 排除白酒;plan §五 ① 测试清单原文)————

@pytest.mark.parametrize("industry,industries,expected", [
    ("白酒", ["白酒"], False),        # 命中 → 排除
    ("红黄酒", ["白酒"], True),        # 未命中:同为酒类但 industry 归类值不同,精确归类不殃及
    ("啤酒", ["白酒"], True),
    ("通信", ["白酒"], True),
    ("白酒", [], True),               # 空名单 = 不排除任何行业(schema 缺省)
    (None, ["白酒"], True),           # 🔴 缺失保守判通过:不臆造「反正不是白酒」
])
def test_industry_blacklist_behavior(industry, industries, expected):
    row = {"industry": industry}
    assert prim.PRIMITIVES["industry_blacklist"].run(row, {"industries": industries}) is expected


def test_industry_blacklist_missing_industry_conservative_direction_is_opposite_of_non_new_stock():
    """两个原语的「缺数保守方向」**刻意相反**(docstring 已定死,⛔ 别"统一"):
    黑名单缺数时排除会误杀 → 判通过;白名单(非次新)缺数时放行会漏网 → 判不
    通过。两者代价不对称,各取「宁可少做动作」的那一侧。"""
    assert prim.PRIMITIVES["industry_blacklist"].run({}, {"industries": ["白酒"]}) is True
    assert prim.PRIMITIVES["non_new_stock"].run({}, {"min_days": 120}) is False


def test_industry_blacklist_is_exact_category_match_not_keyword():
    """LinoN 教训:**精确归类,⛔ 非名称关键词** —— 名单串是行业归类值的全等比较,
    不是子串匹配(「白酒概念龙头」这种自造串匹不上真归类值「白酒」才是对的)。"""
    assert prim.PRIMITIVES["industry_blacklist"].run(
        {"industry": "白酒饮料"}, {"industries": ["白酒"]}) is True   # 非全等 → 不排除


# —— P2-47 结案守门:选股域 × 回测域卫生线共享常量逐位对拍 ————————————————————

def test_p247_shared_hygiene_constants_drive_both_domains_bit_for_bit():
    """§七 P2-47(V2.2-① 结案):`base_universe_expr`(回测域)与 `_stock_hygiene`
    (选股域)的三项数值判据自本版起 import 同一组共享常量 —— 本测试在**边界值**
    上逐位对拍两域行为,任何一边再长出私有字面量、边界就会先崩在这里。
    ⚠ 板块口径刻意不对拍(两域不是同一个量:回测排 BSE 含 STAR,选股按包配置),
    见 `primitives.py` 共享常量声明处的理由。"""
    import polars as pl

    from neckline.research.panel import base_universe_expr

    cases = [
        # (close, amount_ma20, 预期)——两常量各自的边界两侧
        (prim.STOCK_HYGIENE_CLOSE_MIN, prim.STOCK_HYGIENE_AMOUNT_MA20_MIN, True),
        (prim.STOCK_HYGIENE_CLOSE_MIN - 0.01, prim.STOCK_HYGIENE_AMOUNT_MA20_MIN, False),
        (prim.STOCK_HYGIENE_CLOSE_MIN, prim.STOCK_HYGIENE_AMOUNT_MA20_MIN - 1.0, False),
    ]
    df = pl.DataFrame({
        "close": [c for c, _a, _e in cases],
        "amount_ma20": [a for _c, a, _e in cases],
        "ma20": [1.0] * len(cases),
        "board": ["MAIN"] * len(cases),
        "is_st": [False] * len(cases),
    })
    research_side = df.select(base_universe_expr().alias("ok"))["ok"].to_list()
    selection_side = [
        prim.PRIMITIVES["stock_hygiene"].run(
            {"close": c, "amount_ma20": a, "ma20": 1.0, "board": "MAIN", "is_st": False})
        for c, a, _e in cases
    ]
    expected = [e for _c, _a, e in cases]
    assert research_side == expected
    assert selection_side == expected
    assert research_side == selection_side   # 逐位对拍:两域同一张嘴


def test_p247_shared_constants_current_values_are_registered_facts():
    """共享常量的现值钉死(改数 = 同时改选股域与回测域的入场资格,必须走 plan)。"""
    assert prim.STOCK_HYGIENE_CLOSE_MIN == 2.0
    assert prim.STOCK_HYGIENE_AMOUNT_MA20_MIN == 20000.0
    assert prim.STOCK_HYGIENE_REQUIRE_MA20 is True


# —— non_new_stock ——————————————————————————————————————————————————————

@pytest.mark.parametrize("days,min_days,expected", [
    (200, 120, True),
    (119, 120, False),
    (120, 120, True),   # 边界:>= 判定
    (None, 120, False),
])
def test_non_new_stock_behavior(days, min_days, expected):
    row = _poisoned_row(days_since_listing=days)
    assert prim.PRIMITIVES["non_new_stock"].run(row, {"min_days": min_days}) is expected
    assert row.accessed.isdisjoint(_POISON_KEYS)


# —— k4_advisory_gate ———————————————————————————————————————————————————

@pytest.mark.parametrize("section,hard_cut_action,avoid_flag_action,expected", [
    (None, "exclude", "tag", True),
    ("hard_cut", "exclude", "tag", False),     # 现值:hard_cut 拦截出池
    ("hard_cut", "tag", "tag", True),          # 换开关 → 不拦(证明开关真被消费)
    ("avoid_flag", "exclude", "tag", True),    # 现值:avoid_flag 只打标不拦
    ("avoid_flag", "exclude", "exclude", False),
])
def test_k4_advisory_gate_behavior(section, hard_cut_action, avoid_flag_action, expected):
    row = _poisoned_row(k4_section=section)
    result = prim.PRIMITIVES["k4_advisory_gate"].run(
        row, {"hard_cut_action": hard_cut_action, "avoid_flag_action": avoid_flag_action}
    )
    assert result is expected
    assert row.accessed.isdisjoint(_POISON_KEYS)


# —— industry_dominance_gate ————————————————————————————————————————————

@pytest.mark.parametrize("lift,min_lift,expected", [
    (3.0, 2.0, True),
    (1.0, 2.0, False),
    (2.0, 2.0, True),          # 边界:EPS 容差下 >= 通过
    (2.0 - 1e-10, 2.0, True),  # 浮点噪声容差内仍判通过
    (None, 2.0, False),
])
def test_industry_dominance_gate_behavior(lift, min_lift, expected):
    row = _poisoned_row(industry_lift=lift)
    assert prim.PRIMITIVES["industry_dominance_gate"].run(row, {"min_lift": min_lift}) is expected
    assert row.accessed.isdisjoint(_POISON_KEYS)


# —— intel_rank_priority ————————————————————————————————————————————————

def test_intel_rank_priority_tuple_shape_and_none_sorts_last():
    row = _poisoned_row(industry_rank=3, industry_persist_days=1, yellow_card_count=0)
    key = prim.PRIMITIVES["intel_rank_priority"].run(row)
    assert key == (3, 1, 0)
    assert row.accessed.isdisjoint(_POISON_KEYS)

    row_no_rank = _poisoned_row(industry_rank=None, industry_persist_days=1, yellow_card_count=0)
    key_no_rank = prim.PRIMITIVES["intel_rank_priority"].run(row_no_rank)
    assert key_no_rank[0] == float("inf")


def test_intel_rank_priority_ordering_matches_v1_three_level_priority():
    """顺序即权重:第一维(industry_rank)天然压过后面所有维度,与
    `intel_candidates.py::_sort_key` 的三级优先级逐位一致。"""
    entries = [
        {"code": "A", "industry_rank": 2, "industry_persist_days": 0, "yellow_card_count": 5},
        {"code": "B", "industry_rank": 1, "industry_persist_days": 3, "yellow_card_count": 5},
        {"code": "C", "industry_rank": 1, "industry_persist_days": 0, "yellow_card_count": 9},
        {"code": "D", "industry_rank": 1, "industry_persist_days": 0, "yellow_card_count": 0},
    ]
    ordered = sorted(entries, key=lambda e: prim.PRIMITIVES["intel_rank_priority"].run(e))
    assert [e["code"] for e in ordered] == ["D", "C", "B", "A"]


def test_intel_rank_priority_dims_param_changes_outcome():
    """换 `dims` 顺序 → 排序结果跟着变(pack 消费证据)。"""
    entries = [
        {"code": "A", "industry_rank": 2, "yellow_card_count": 0},
        {"code": "B", "industry_rank": 1, "yellow_card_count": 9},
    ]
    by_rank_first = sorted(
        entries, key=lambda e: prim.PRIMITIVES["intel_rank_priority"].run(e, {"dims": ["industry_rank", "yellow_card_count"]})
    )
    by_card_first = sorted(
        entries, key=lambda e: prim.PRIMITIVES["intel_rank_priority"].run(e, {"dims": ["yellow_card_count", "industry_rank"]})
    )
    assert [e["code"] for e in by_rank_first] == ["B", "A"]
    assert [e["code"] for e in by_card_first] == ["A", "B"]


# ══════════════════════════════════════════════════════════════════════════
# V2-③-K7:`intel_rank_priority` 排序维度扩容(K7 需求 4)——方向声明 +
# 新增两个维度(`industry_stage_score`/`leader_rs_rank`)+ fail loud
# ══════════════════════════════════════════════════════════════════════════

def test_intel_rank_priority_leader_rs_rank_is_ascending():
    """`leader_rs_rank`(K7 需求 1a,簇内 RS 名次)升序为优:名次 1 排在名次 3
    前面——同 `industry_rank` 既有语义,不因为是"新增维度"就换个方向。同时
    确认 `leader_structure_daily.rs_rank` 真的能被 sort_key 类原语引用(③-K7-A
    要求的"确认"落地成这条行为单测,不只是白名单字符串匹配)。"""
    row_best = _poisoned_row(leader_rs_rank=1)
    row_worst = _poisoned_row(leader_rs_rank=3)
    key_best = prim.PRIMITIVES["intel_rank_priority"].run(row_best, {"dims": ["leader_rs_rank"]})
    key_worst = prim.PRIMITIVES["intel_rank_priority"].run(row_worst, {"dims": ["leader_rs_rank"]})
    assert key_best < key_worst
    assert row_best.accessed.isdisjoint(_POISON_KEYS)


def test_intel_rank_priority_industry_stage_score_is_descending():
    """`industry_stage_score`(K7 需求 1b,五态打分)降序为优:分高的排在分低的
    前面。**若现实现把 dims 一律当同向(升序)处理,这条会失败**——分高的会被
    错误排到后面,这正是 ③-K7-B 原文点名要防的坑。"""
    row_high = _poisoned_row(industry_stage_score=1.0)
    row_low = _poisoned_row(industry_stage_score=0.2)
    key_high = prim.PRIMITIVES["intel_rank_priority"].run(row_high, {"dims": ["industry_stage_score"]})
    key_low = prim.PRIMITIVES["intel_rank_priority"].run(row_low, {"dims": ["industry_stage_score"]})
    assert key_high < key_low   # 排序键更小 → sorted() 后排前面 → 分高的真的排前面
    assert row_high.accessed.isdisjoint(_POISON_KEYS)


def test_intel_rank_priority_descending_dim_missing_value_still_sorts_last():
    """缺值恒 `+inf` 排最后,**与方向无关**——即便是 `desc` 维度,"算不出"也绝
    不能被误判成"最优"(0 分是 `overheat` 态的真实取值,`None` 是"没数据",
    两者不可互相顶替,见 ④b-C 保险丝纪律)。"""
    row_missing = _poisoned_row(industry_stage_score=None)
    row_present = _poisoned_row(industry_stage_score=0.0)   # 真实的"过热"最低分
    key_missing = prim.PRIMITIVES["intel_rank_priority"].run(row_missing, {"dims": ["industry_stage_score"]})
    key_present = prim.PRIMITIVES["intel_rank_priority"].run(row_present, {"dims": ["industry_stage_score"]})
    assert key_missing[0] == float("inf")
    assert key_present < key_missing   # 哪怕是最低分 0.0,也排在"缺数"前面


def test_intel_rank_priority_k7_pack_dims_ordering_end_to_end():
    """用 K7-pack 排序键的真实四维顺序(`industry_rank`→`industry_stage_score`→
    `leader_rs_rank`→`yellow_card_count`,`archive/packs_retired/K7-pack.json` 原样)跑一次端到
    端排序,证明混合 asc/desc 时整体排序仍正确(不是只有单维度测试才对)。"""
    k7_dims = ["industry_rank", "industry_stage_score", "leader_rs_rank", "yellow_card_count"]
    entries = [
        # A/B 同 industry_rank=1(第一维打平),靠 industry_stage_score 分高低
        # 分胜负:分高的 B 应排 A 前面,尽管 B 的 leader_rs_rank 名次更差。
        {"code": "A", "industry_rank": 1, "industry_stage_score": 0.2, "leader_rs_rank": 1, "yellow_card_count": 0},
        {"code": "B", "industry_rank": 1, "industry_stage_score": 1.0, "leader_rs_rank": 5, "yellow_card_count": 0},
        # C 的 industry_rank=2,天然排在 A/B 后面(第一维已分出胜负)。
        {"code": "C", "industry_rank": 2, "industry_stage_score": 1.0, "leader_rs_rank": 1, "yellow_card_count": 0},
    ]
    ordered = sorted(entries, key=lambda e: prim.PRIMITIVES["intel_rank_priority"].run(e, {"dims": k7_dims}))
    assert [e["code"] for e in ordered] == ["B", "A", "C"]


def test_intel_rank_priority_unregistered_dim_raises_fail_loud():
    """未在 `_RANK_DIM_DIRECTIONS` 登记方向的维度名 → 拒绝猜测,`ValueError`
    (不静默当升序处理)。"""
    with pytest.raises(ValueError, match="未在 _RANK_DIM_DIRECTIONS 登记"):
        prim.PRIMITIVES["intel_rank_priority"].run(
            {"totally_unknown_dim": 1}, {"dims": ["totally_unknown_dim"]}
        )


def test_intel_rank_priority_k4_pack_v1_dims_unaffected_by_k7_extension():
    """回滚锚(V2-③-K7 验收硬判据):K4-pack-v1 默认三维度全部是 `asc`,行为与
    扩容前"直接取值参与字典序比较"数值等价——`(3, 1, 0)` 这种整数元组与新实现
    返回的浮点元组逐元素相等(Python `3 == 3.0`),排序结果也逐位不变。"""
    row = _poisoned_row(industry_rank=3, industry_persist_days=1, yellow_card_count=0)
    key = prim.PRIMITIVES["intel_rank_priority"].run(row, {"dims": list(prim._DEFAULT_RANK_DIMS)})
    assert key == (3, 1, 0)
    assert all(isinstance(v, float) for v in key)   # 内部确已转 float,只是数值相等


# ══════════════════════════════════════════════════════════════════════════
# V2-④ 新增 4 个原语:行为正确性 + 运行期访问锁(不读 LLM 产出字段)
# ══════════════════════════════════════════════════════════════════════════

# —— hot_industry_seed ——————————————————————————————————————————————————

@pytest.mark.parametrize("rank,is_strength,max_rank,require_strength,expected", [
    (5, True, 10, True, True),
    (15, True, 10, True, False),      # 超出 max_rank
    (10, True, 10, True, True),       # 边界:<= 判定
    (5, False, 10, True, False),      # 不是强度日且要求强度日
    (5, False, 10, False, True),      # 换参数放宽 require_strength_day → 通过
    (None, True, 10, True, False),    # 未评级(成员不足)保守不通过
])
def test_hot_industry_seed_behavior(rank, is_strength, max_rank, require_strength, expected):
    row = _poisoned_row(industry_rank=rank, is_strength_day=is_strength)
    result = prim.PRIMITIVES["hot_industry_seed"].run(
        row, {"max_rank": max_rank, "require_strength_day": require_strength}
    )
    assert result is expected
    assert row.accessed.isdisjoint(_POISON_KEYS)


# —— surging_concept_seed ———————————————————————————————————————————————

@pytest.mark.parametrize("pct_change,min_pct_change,expected", [
    (8.0, 5.0, True),
    (2.0, 5.0, False),
    (5.0, 5.0, True),               # 边界:EPS 容差下 >= 通过
    (5.0 - 1e-10, 5.0, True),       # 浮点噪声容差内仍判通过
    (None, 5.0, False),
])
def test_surging_concept_seed_behavior(pct_change, min_pct_change, expected):
    row = _poisoned_row(pct_change=pct_change)
    assert prim.PRIMITIVES["surging_concept_seed"].run(row, {"min_pct_change": min_pct_change}) is expected
    assert row.accessed.isdisjoint(_POISON_KEYS)


def test_surging_concept_seed_does_not_read_retired_board_age():
    """"暴起"读的是当日单日涨幅,不是 `board_age`(概念板块年龄——已退役为
    展示专用,不再是任何判据的数据源,见项目 CLAUDE.md)。即便 row 里混进一个
    `board_age` 键,原语也不该访问它。"""
    row = _KeyTrackingDict(pct_change=9.0, board_age=999)
    assert prim.PRIMITIVES["surging_concept_seed"].run(row) is True
    assert "board_age" not in row.accessed


# —— limit_cluster_seed —————————————————————————————————————————————————

@pytest.mark.parametrize("size,days,min_size,min_days,expected", [
    (3, 1, 2, 2, True),        # 广度达标(size),持续性不够也通过(任一满足)
    (1, 3, 2, 2, True),        # 持续性达标(days),广度不够也通过
    (1, 1, 2, 2, False),       # 两者都不达标
    (None, None, 2, 2, False),  # 都缺失
])
def test_limit_cluster_seed_behavior(size, days, min_size, min_days, expected):
    row = _poisoned_row(cluster_size=size, consecutive_days=days)
    result = prim.PRIMITIVES["limit_cluster_seed"].run(
        row, {"min_cluster_size": min_size, "min_consecutive_days": min_days}
    )
    assert result is expected
    assert row.accessed.isdisjoint(_POISON_KEYS)


# —— anomaly_cluster_seed ———————————————————————————————————————————————

@pytest.mark.parametrize("volume_ratio,min_volume_ratio,expected", [
    (4.0, 3.0, True),
    (1.2, 3.0, False),
    (3.0, 3.0, True),              # 边界
    (3.0 - 1e-10, 3.0, True),      # 浮点噪声容差
    (None, 3.0, False),
])
def test_anomaly_cluster_seed_behavior(volume_ratio, min_volume_ratio, expected):
    row = _poisoned_row(volume_ratio=volume_ratio)
    assert prim.PRIMITIVES["anomaly_cluster_seed"].run(row, {"min_volume_ratio": min_volume_ratio}) is expected
    assert row.accessed.isdisjoint(_POISON_KEYS)


def test_anomaly_cluster_seed_declares_but_does_not_consume_cluster_param():
    """`min_cluster_members` 是声明给 `seeds.py` 编排层读的参数,`impl` 本身
    不消费它——换这个参数不改变单票判断结果(证明 impl 确实没用它)。"""
    row = dict(volume_ratio=5.0)
    r1 = prim.PRIMITIVES["anomaly_cluster_seed"].run(row, {"min_cluster_members": 2})
    r2 = prim.PRIMITIVES["anomaly_cluster_seed"].run(row, {"min_cluster_members": 99})
    assert r1 is r2 is True


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 模块级数值字面量白名单扫描(全仓 grep 要求,AST 精确匹配同
#    `test_db_isolation_guardrail.py` 体例,不用纯文本 grep)
# ══════════════════════════════════════════════════════════════════════════

# {(文件名, 常量名): 理由}——加一条新常量前先问自己:这真的是"引擎不变量"
# (版本号/浮点容差/白名单本身这类不随包变化的东西),还是一个悄悄溜进来的
# "可配阈值"?后者必须挪进某个原语的 `params_schema` 默认值或函数参数默认值。
_ENGINE_CONSTANT_WHITELIST: Dict[Tuple[str, str], str] = {
    ("engine_api.py", "ENGINE_API_VERSION"): (
        "引擎兼容版本号,单一源判据——包声明的 engine_api_version 是拿来核对"
        "这个数的,不是来源自它,版本号本身不是策略参数。"
    ),
    ("primitives.py", "_LIFT_EPS"): (
        "浮点比较容差,工程不变量(同 sentinel/holding.py `_EPS` / "
        "intel_candidates.py `_INDUSTRY_GATE_EPS` 先例:裸 >=/<= 比较除法产生的"
        "浮点噪声是本项目通用坑),非策略参数。"
    ),
    # —— V2.2-①(§七 P2-47 结案):选股域 × 回测域卫生线**共享常量**,唯一源。
    #    它们不是"裸模块级阈值全局"那类病(那病的判据是「只有这里一份、包够不着」),
    #    而是刻意的双域同源点:选股域包可覆盖(params_schema default 引用它),回测域
    #    `neckline/research/panel.py::base_universe_expr` import 它 —— 改数 = 同时改
    #    两域入场资格,必须走 plan;逐位对拍守门见本文件 P2-47 段。
    ("primitives.py", "STOCK_HYGIENE_CLOSE_MIN"): (
        "P2-47 共享常量:qfq 收盘下限,选股域 stock_hygiene 默认值与回测域 "
        "base_universe_expr 的唯一同源点(值仍可被包覆盖)。"
    ),
    ("primitives.py", "STOCK_HYGIENE_AMOUNT_MA20_MIN"): (
        "P2-47 共享常量:20 日均额下限(千元),同上——两域同源,改数走 plan。"
    ),
    # —— V2-⑤ 驱动聚合层(plan §五「插槽边界」原文:「②驱动聚合的两道机械闸 =
    #    **引擎本体,不进包**」——本节四项因此**刻意**不是包参数)——————————
    ("aggregate.py", "MIN_MEMBERS"): (
        "篮子成员数下限,蓝图 4.2「每个篮子允许 1—3 只股票」的产品硬规则,"
        "不是可调阈值;插槽边界明文把驱动聚合归为引擎本体、不进包。"
    ),
    ("aggregate.py", "MAX_MEMBERS"): (
        "篮子成员数上限,同上(蓝图 4.2 的 3 只)。改它等于改产品定义,"
        "要走 plan 而不是换包。"
    ),
    ("aggregate.py", "MAX_SEEDS_AGGREGATED"): (
        "一次聚合最多喂几颗种子的工程护栏(真正的 governor 是 `BudgetLedger`)。"
        "它约束的是上下文规模与调用次数,不参与任何选股判据,故非策略参数。"
    ),
    ("aggregate.py", "MAX_MEMBERS_IN_CONTEXT"): (
        "每颗种子在 LLM 上下文里最多列几只成员的工程护栏(同时定义了白名单闸的"
        "白名单范围)。同样只约束上下文规模,不是「够不够格」这类阈值判据。"
    ),
    # —— V2-⑥ Tier 分层引擎(plan §五「插槽边界」同一条:定档引擎是**引擎本体**,
    #    进包的只有 `tier.weights` / `tier.stage_scores` 两项)——————————————
    ("tier.py", "TIER_CAPACITY"): (
        "T1≤2 / T2≤5(V2.1-② T3 退役前还有 T3≤10),plan §五 原文写死的产品规则"
        "(同 aggregate 的 MIN_MEMBERS/MAX_MEMBERS 性质:改它等于改产品定义,要走 "
        "plan 而不是换包)。"
    ),
    # —— V2-⑦-K7 成员标注件(判据阈值一律走**函数关键字默认值**〔见
    #    `member_tags.evaluate_member_tags` 签名〕,故这里只剩一个浮点容差)————
    ("member_tags.py", "_EPS"): (
        "浮点比较容差,工程不变量(同 primitives.py `_LIFT_EPS` / sentinel/holding.py "
        "`_EPS` 先例:裸 >=/<= 比较除法产生的浮点噪声是本项目通用坑),非策略参数。"
        "⑦-K7 的三组真判据阈值(强势资格 / 回调带 / 企稳日 / 追入带)全部落在 "
        "`evaluate_member_tags()` 的关键字默认值上——本测试消息自己列出的两种合规"
        "形态之一。"
    ),
    ("tier.py", "TIERS"): (
        "现役档位的枚举本身(V2.1-② 起 1/2),是**写侧**取值域,不是阈值。"
        "⚠ `baskets.tier` 的 DDL 取值域仍含 3(历史行),读侧一律按数据实际出现的"
        "档位构造,别拿这个常量去收窄读侧。"
    ),
    ("tier.py", "TIER1_MIN_SCORE"): (
        "T1 质量线的**缺省回退值**(V2-⑥-b planner 裁定后降级,不再是权威——"
        "质量线的权威现在住包 `config.tier.quality_lines.tier1_min`,只有包没给"
        "这个键时才用这个数,见 `tier.resolve_quality_lines()`;归属改判「进包」的"
        "理由是它与五维权重同标度,换权重会静默改变 T1 选择性)。数值本身仍是 ⑥ "
        "施工期定的临时默认(同 ⑤-c MIN_LIFT_SAMPLE_SIZE=5 的处置姿势,未获证据"
        "支持具体取值),校准前置到 ⑨ 评价引擎周报 + 进化门禁(= 换包),§七 "
        "P3-33 挂账,⛔ 不许在代码里顺手改数、也不许顺手改包里的数。"
    ),
    ("tier.py", "TIER2_MIN_SCORE"): "T2 质量线的缺省回退值,同 TIER1_MIN_SCORE 那一条。",
    # —— V2.2-③ 六道关口(gates.py):真判据阈值全部住引擎包 `gates.*`(键名契约
    #    `pack._ENGINE_GATE_SCHEMA`,gates.py 零硬编阈值数字);这里剩下的四个是
    #    引擎不变量 / 缺键回退值,不是可调策略参数。————————————————————————
    ("gates.py", "T1_MAX_EVIDENCE_DEGRADES_DEFAULT"): (
        "K8 §八 T1「零降级」的**缺键回退值**(权威住引擎包 tier_evidence.t1."
        "max_evidence_degrades,三个首版包都显式给了 0;这里只兜历史行/测试替身"
        "缺键的情况,同 tier.TIER1_MIN_SCORE 的回退值定位)。"
    ),
    ("gates.py", "T2_MAX_EVIDENCE_DEGRADES_DEFAULT"): (
        "K8 §八 T2「至多一处降级」的缺键回退值,同上(权威在包 tier_evidence.t2)。"
    ),
    ("gates.py", "STRENGTH_DAYS_WINDOW"): (
        "C1 板块关「近 5 日强度日」的窗口 5 —— 它是包键 `strength_days_min_5d` "
        "键名语义的一部分(名字里就写着 5d),不是独立可调参数;真阈值(≥几天)"
        "在包里。改窗口 = 改键名语义,要走 plan + 扩 _ENGINE_GATE_SCHEMA。"
    ),
    ("gates.py", "_EPS"): (
        "浮点比较容差,工程不变量(同 primitives.py `_LIFT_EPS` / member_tags.py "
        "`_EPS` 先例),非策略参数。"
    ),
    # ⚠ `TIER3_MIN_SCORE` 于 V2.1-② 随 T3 全链退役而**删除**(不留影子档);
    # 反向 hasattr 守门在 `tests/test_selection_tier.py::test_tier3_min_score_is_retired`。
    # ⛔ 别在这份白名单里给它留一行"以防万一"——白名单是"允许存在"的登记,
    # 给一个已删常量留登记等于给复活开绿灯。
    ("tier.py", "NEUTRAL_DIM_SCORE"): (
        "某一维算不出时的中性分。它是「[0,1] 归一化维度的中点」这一**度量约定**,"
        "不是可调偏好——真正要防的是拿 0 冒充「没数据」(0 是 stage_scores 里 overheat "
        "的真实取值,「没有」与「没看」必须分得开)。"
    ),
    ("tier.py", "ONE_WORD_PENALTY"): (
        "一字板 = 当日根本买不进 → 机会可实现性折价 100%。这是「买不进的涨停不算机会」"
        "(蓝图 4.9)这句产品定义的算术表达,不是可调阈值。"
    ),
    ("tier.py", "LIMIT_UP_PENALTY"): (
        "涨停但开过板 = 盘中有过成交机会 → 折价 50%。同上,是「能不能买到」的语义刻度,"
        "不是对涨停本身的偏好。"
    ),
    ("tier.py", "_EPS"): (
        "浮点比较容差,工程不变量(同 primitives._LIFT_EPS / sentinel/holding.py `_EPS` "
        "先例:裸 >=/<= 比较除法产生的浮点噪声是本项目通用坑),非策略参数。"
    ),
    ("tier.py", "MAX_BASKETS_IN_RANK_CONTEXT"): (
        "喂给 LLM 微调段的每档篮子上限,上下文规模护栏(同 aggregate."
        "MAX_MEMBERS_IN_CONTEXT 性质),不参与任何判据。"
    ),
    # —— V2-⑦-b 验证 / 失效条件集(2026-08-02 planner 裁定:**引擎默认,⛔ 本版
    #    不进包** —— §12.2 插槽边界明文把「④篮子卡冻结体例」列在「引擎本体,不进包」
    #    一侧,要包化必须走「扩插槽边界〔用户拍板〕→ 扩 schema → 发包」三步)————
    ("verification_rules.py", "MIN_MEMBERS_HIT_DIVISOR"): (
        "篮子级聚合门槛 `ceil(n / MIN_MEMBERS_HIT_DIVISOR)` 的除数(= 2,即「过半、"
        "向上取整」),验证与失效两侧同一个数。⚠ **临时默认、零审计背书** —— 没有"
        "任何回测或事件研究支持它,是为了让四态状态机能跑起来而拟的占位值;"
        "**要可配须先扩 §12.2 插槽边界(用户拍板)**,⛔ builder 不许自行改数、"
        "也不许自行加包键。校准前置到 ⑨ 评价引擎攒够样本,§七 P3-34 挂账。"
    ),
    ("verification_rules.py", "EPS"): (
        "浮点比较容差,工程不变量(同 primitives._LIFT_EPS / sentinel/holding.py "
        "`_EPS` 先例:裸 >=/<= 比较价位是本项目通用坑),非策略参数。"
    ),
    # —— V2.2-③-C2 核心关读数层(core_metrics.py,🔴 用户裁定 #12)——————————
    # ⚠ 这三个是**「这个量是什么」的定义**,不是「够不够格」的阈值 —— 核心关自此
    # **零阈值、零及格线**(含「行业内前 X%」这类,用户明确否决),整个模块里根本
    # 没有一条判据可言,只有读数。定义常量的既有分工体例见 `scan/landing.py` 的
    # 窗口常量段(⛔ 不进包:进包意味着"可调",而可调的前提是它参与判定)。
    ("core_metrics.py", "RS_WINDOW_DAYS"): (
        "「20 日收益」里的那个 20 —— 读数名字(`industry_rs_rank_20d`)自己写着它,"
        "是量的定义而不是及格线。改它 = 换一个量,要走 plan + 改键名。"
    ),
    ("core_metrics.py", "CONSEC_LIMIT_LOOKBACK_DAYS"): (
        "连板高度的回看上限(右截尾,同 `scan/landing.py::PLATFORM_DAYS_CAP` 体例):"
        "饱和值读作「≥该值」。它是取数窗口 / 内存上界,不参与任何判定。"
    ),
    ("core_metrics.py", "_MIN_RANKABLE"): (
        "分位需要至少两个可比成员 —— 数学定义(分母 n−1 不能为 0),不是门槛。"
    ),
}


def _is_all_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0 and all(_is_all_numeric(v) for v in value)
    if isinstance(value, dict):
        return len(value) > 0 and all(_is_all_numeric(v) for v in value.values())
    return False


def _module_level_numeric_assignments(path: Path) -> List[Tuple[int, str, Any]]:
    """只看**模块顶层**语句(`tree.body`,不 `ast.walk` 递归进函数/类内部)——
    函数参数默认值(如 `def _stock_hygiene(row, *, close_min=2.0)`)与
    `params_schema` 字典里挂在 `Primitive(...)` 构造调用参数上的默认值,均不是
    "裸模块级全局",结构上天然不会被本扫描命中,不需要单独排除。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: List[Tuple[int, str, Any]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value_node = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value_node = [node.target], node.value
        else:
            continue
        try:
            value = ast.literal_eval(value_node)
        except (ValueError, SyntaxError, TypeError):
            continue
        if not _is_all_numeric(value):
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                hits.append((node.lineno, t.id, value))
    return hits


_SELECTION_FILES = sorted(_SELECTION_DIR.glob("*.py"))


@pytest.mark.parametrize("path", _SELECTION_FILES, ids=lambda p: p.name)
def test_no_unwhitelisted_module_level_numeric_thresholds(path: Path):
    hits = _module_level_numeric_assignments(path)
    for lineno, name, value in hits:
        key = (path.name, name)
        assert key in _ENGINE_CONSTANT_WHITELIST, (
            f"{path.name}:{lineno} 模块级数值字面量 {name}={value!r} 未登记在白名单——"
            f"neckline/selection/ 下的可配阈值必须走包 config(原语 params_schema 的 "
            f"default,或纯函数参数默认值),不许是裸模块级全局。若这确属引擎不变量"
            f"(版本号/浮点容差一类),把 (\"{path.name}\", \"{name}\") 连同理由一起"
            f"加进 test_selection_primitives.py::_ENGINE_CONSTANT_WHITELIST。"
        )


def test_engine_constant_whitelist_entries_are_still_present():
    """反向校验:白名单里登记的每一项都必须真的能在扫描结果里找到——防止未来
    重构删掉/改名该常量后,白名单条目变成没人会注意到的死记录。"""
    all_hits = {
        (path.name, name)
        for path in _SELECTION_FILES
        for _, name, _ in _module_level_numeric_assignments(path)
    }
    for key in _ENGINE_CONSTANT_WHITELIST:
        assert key in all_hits, (
            f"白名单条目 {key} 未在任何 neckline/selection/*.py 文件里找到对应的"
            f"模块级数值常量,是不是已被删除/改名?请清理这条白名单登记。"
        )
