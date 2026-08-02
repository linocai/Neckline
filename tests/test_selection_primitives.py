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
    「V2-④ 新增 4 个原语」节)。本测试锁死数量,防止有人"顺手"多加/少加一个
    模式而没人注意到(改动这个集合是真正的架构决策,不该悄悄发生)。"""
    assert len(prim._ALLOWED_FEATURES) == 9
    assert "ths_daily.*" in prim._ALLOWED_FEATURES


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

def test_registry_contains_exactly_the_five_first_pack_primitives():
    """V2-③ 首包 5 原语 + V2-④ 市场扫描层新增 4 原语(见 `primitives.py` 模块头
    「V2-④ 新增 4 个原语」节),共 9 个,不多不少(改动这个集合同样是架构决策,
    见上一测试同款纪律)。"""
    assert set(prim.PRIMITIVES) == {
        "stock_hygiene", "non_new_stock", "k4_advisory_gate",
        "industry_dominance_gate", "intel_rank_priority",
        "hot_industry_seed", "surging_concept_seed",
        "limit_cluster_seed", "anomaly_cluster_seed",
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
