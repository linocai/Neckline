"""原语注册表(plan §五 V2-③)。

**策略包只能引用这里注册的原语,不能带代码**(§12.1)。每个原语 = `name` /
`kind`(filter | feature | sort_key)/ `inputs`(只能引用下面 `_ALLOWED_FEATURES`
白名单里的预计算特征)/ `params_schema`(声明式参数约束,供 `pack.py` 校验包里
的 `config.seeds.<name>` 那一段)/ `impl`(真正的纯函数实现,系统线代码,永不
随包传输)。

**特征白名单(`_ALLOWED_FEATURES`)= 第〇原则四锁「不进机械分」的落地机关**
(plan §2.8-C 第 1 条):只允许来自预计算表与 EOD 面板的列,**LLM 产出的任何
字段一律不在白名单内**——`llm_judgments` / `basket_cards.card_json` /
`reference_plans.*` / `tier_history.llm_reason` 等一律不得出现在任何原语的
`inputs` 里。锁分两道(不是摆设,单测两道都验):
    ① **构造期**:`Primitive.__post_init__` 在原语对象造出来那一刻就核对
       `inputs ⊆ _ALLOWED_FEATURES`,违反直接 `ValueError`——引擎自己的代码
       都不可能注册出一个越界原语(fail loud,不是等到用的时候才发现)。
    ② **运行期**:`validate_all_primitives_whitelisted()` 把整个注册表重新扫一遍
       (`scripts/activate_pack.py` 闸 2 调用它做防御性复核),外加各原语单测里
       用"记录实际访问过哪些 row 键"的字典子类(`_SORT_KEY_INPUTS` 体例平移,
       见 `tests/test_selection_primitives.py`)证明 `impl` **真的**不读 LLM
       产出字段,不只是静态声明写得漂亮。

**`inputs` 命名约定**:`<表名>.<列名>`,与 `_ALLOWED_FEATURES` 的模式一一对应
(如 `industry_strength_daily.industry_rank`)。`daily*`(无点号,故意)同时覆盖
`daily` 与 `daily_basic` 两张表;工程上由 `neckline/strategy/features.py::
build_research_panel` 在 `daily` 基础上滚动计算出的派生列(`ma20` /
`amount_ma20` / `days_since_listing` 等)记作 `daily.<派生列名>`——它们的源头
数据仍是 `daily`,不为此另开一个白名单类目。`k4_advisory` 物理上是
`strategy_versions` K4 研究行 `rule_json["k4_advisory"]` 的嵌套字段(不是独立
SQLite 表),白名单里仍按逻辑表名处理(与 CLAUDE.md「K4 红黄牌文字读 DB
`k4_advisory`」的既有说法一致)。

**身份 / 分类数据不算"特征"、不受白名单约束**:`board`(`data/board.py` 按代码
前缀纯函数分类)、`is_st`(`stock_basic`/`namechange` 静态标记)、`industry`
(`stock_basic.industry`,一票一行业)是全局基础设施式的参考数据,不是会被 LLM
污染或需要 EOD 重算纪律约束的"判断特征"——原语可以在 `impl` 内部使用它们做
过滤,但不必也不应把它们计入 `inputs`(白名单管的是"数值/判断类特征来源",不是
"一切被读过的字段")。这是本文件的设计判断,不是 plan 字面写死的规则,如与规划
意图不符请澄清。

**首包 5 个原语的现值来源**(逐一对应 plan §五 V2-③「首包 K4-pack」清单,
`不发明新参数值,抽的是现值`):
    · `stock_hygiene`           ← `neckline/research/panel.py::base_universe_expr`
      (`close>=2.0` / `amount_ma20>=20000`〔千元,=2000万元〕/ `ma20` 非空)+
      `neckline/report/intel_candidates.py::_ALLOWED_BOARDS`(`MAIN/GEM/STAR`)。
    · `non_new_stock`           ← `intel_candidates.py::NON_NEW_MIN_DAYS = 120`。
    · `k4_advisory_gate`        ← `intel_candidates.py` 的 K4 安检开关语义
      (`hard_cut` 命中拦截出池 / `avoid_flag` 命中打标保留,机器不禁)。
    · `industry_dominance_gate` ← `intel_candidates.py::INDUSTRY_GATE_MIN_LIFT = 2.0`
      (行业闸,lift = 板内占比 ÷ 全市场占比)。
    · `intel_rank_priority`     ← `intel_candidates.py::_sort_key` 的三级键顺序
      (`industry_rank` → `industry_persist_days` → `yellow_card_count`,三者原
      逻辑均为升序;原函数的确定性兜底 `base_score DESC / code ASC` 不做成可配
      参数,原样留在未来消费方的实现里,不属于"三级键"本身的语义)。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

# —— 特征白名单(plan §五 V2-③ 原文逐字照抄的 8 个模式)—————————————————————
# **模块级常量,不是策略参数**:这是"引擎允许原语引用哪些表"的不变量,不随包变化
# ——它由本文件自己列出并在 `tests/test_selection_primitives.py` 的模块级数值
# 字面量白名单扫描测试里显式登记(见该文件 `_ENGINE_CONSTANT_WHITELIST`)。
_ALLOWED_FEATURES: Tuple[str, ...] = (
    "industry_strength_daily.*",
    "corr_matrix_daily.*",
    "limit_cluster_daily.*",
    "leader_structure_daily.*",
    "limit_derived.*",
    "daily*",          # 故意无点号:同时覆盖 daily.* 与 daily_basic.*
    "moneyflow_dc.*",
    "k4_advisory.*",
)

_KINDS: Tuple[str, ...] = ("filter", "feature", "sort_key")


def is_allowed_feature(feature: str) -> bool:
    """`feature` 是否落在 `_ALLOWED_FEATURES` 任一模式内(大小写敏感 —— 项目
    表名/列名全小写 snake_case,不需要不区分大小写的宽容)。"""
    return any(fnmatch.fnmatchcase(feature, pattern) for pattern in _ALLOWED_FEATURES)


def _inputs_violations(inputs: Sequence[str]) -> List[str]:
    """`inputs` 里不在白名单内的项(空列表 = 全部合规)。独立于 `Primitive` 之外
    的纯函数,供构造期校验与 `validate_all_primitives_whitelisted()` 共用同一份
    判据,也方便直接对着一个裸元组写负例单测(不必先构造一个会在 `__post_init__`
    就抛异常的对象)。"""
    return [f for f in inputs if not is_allowed_feature(f)]


# —— 原语声明与注册表 ————————————————————————————————————————————————————

@dataclass(frozen=True)
class Primitive:
    """一个可被包引用的选股原语声明。`impl` 是真正的纯函数:
        · kind="filter"   → `impl(row: Mapping, **params) -> bool`(True=保留);
        · kind="feature"  → `impl(row: Mapping, **params) -> Any`(算一个派生值);
        · kind="sort_key" → `impl(row: Mapping, **params) -> tuple`(排序键元组,
          升序;元组内已按位处理"越小越靠前",消费方直接 `sorted(rows, key=...)`)。

    `row` 是调用方(未来 ④ 市场扫描层 / ⑥ Tier 引擎)拼好的已连接特征字典,本文件
    不关心 `row` 从哪来 —— 原语只是纯函数,不做 I/O、不碰 DB/parquet。"""

    name: str
    kind: str
    inputs: Tuple[str, ...]
    params_schema: Dict[str, Dict[str, Any]]
    impl: Callable[..., Any]

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"原语 {self.name!r} 的 kind={self.kind!r} 不合法(仅允许 {_KINDS})")
        bad = _inputs_violations(self.inputs)
        if bad:
            raise ValueError(
                f"原语 {self.name!r} 引用了特征白名单之外的 inputs:{bad}"
                f"(允许的模式:{list(_ALLOWED_FEATURES)})——拒绝注册,fail loud。"
            )

    def defaults(self) -> Dict[str, Any]:
        """`params_schema` 里声明了 `default` 的键 → 默认值字典(缺省即取此值)。"""
        return {k: v["default"] for k, v in self.params_schema.items() if "default" in v}

    def merge_params(self, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        merged = self.defaults()
        merged.update(params or {})
        return merged

    def run(self, row: Mapping[str, Any], params: Optional[Mapping[str, Any]] = None) -> Any:
        """用 `params`(缺的键取 schema 默认值)对单条 `row` 跑一次 `impl`。
        单测与未来 ④/⑥ 的消费者共用这一条调用路径,不必各自重复"取默认值再调用
        impl"的样板代码。"""
        return self.impl(row, **self.merge_params(params))


PRIMITIVES: Dict[str, Primitive] = {}


def _register(primitive: Primitive) -> Primitive:
    if primitive.name in PRIMITIVES:
        raise ValueError(f"原语 {primitive.name!r} 重复注册")
    PRIMITIVES[primitive.name] = primitive
    return primitive


def validate_all_primitives_whitelisted(
    registry: Optional[Mapping[str, Primitive]] = None,
) -> List[str]:
    """把注册表里每个原语的 `inputs`/`kind` 重新核对一遍(防御性复核 ——
    `Primitive.__post_init__` 已经在构造期拦过一次,这是"万一注册表被绕过构造
    校验直接篡改"的第二道,`scripts/activate_pack.py` 闸 2 调用它)。`registry`
    缺省用全局 `PRIMITIVES`;单测可传一份合成的坏注册表(如
    `{"evil": SimpleNamespace(name=..., inputs=(...), kind=...)}`)验证本函数
    真的有牙齿,不必费力绕过 frozen dataclass 的构造期校验去偷造一个坏对象。"""
    reg = PRIMITIVES if registry is None else registry
    errors: List[str] = []
    for name, primitive in reg.items():
        if primitive.kind not in _KINDS:
            errors.append(f"原语 {name!r} 的 kind={primitive.kind!r} 不合法(仅允许 {_KINDS})")
        bad = _inputs_violations(primitive.inputs)
        if bad:
            errors.append(f"原语 {name!r} 引用了特征白名单之外的 inputs:{bad}")
    return errors


# —— 参数 schema 校验(轻量版"JSON Schema";§3.1 钉死依赖清单没有 `jsonschema`,
#    不为这一处新增第三方库,手写几个类型判据足够覆盖当前 5 个原语的需要)——————

_TYPE_CHECKERS: Dict[str, Callable[[Any], bool]] = {
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def validate_params(primitive: Primitive, params: Mapping[str, Any]) -> List[str]:
    """按 `primitive.params_schema` 校验 `params`(即包 `config.seeds.<name>` 那一
    段)。只做结构性检查:未声明的键 / 类型不匹配 / 枚举越界 / 数组元素类型不对,
    每条记一行错误(不抛异常,收集完整错误列表交调用方一次性打印)。"""
    schema = primitive.params_schema
    errors: List[str] = []
    unknown = sorted(set(params) - set(schema))
    if unknown:
        errors.append(f"{primitive.name}: 未声明的参数 {unknown}")
    for key, spec in schema.items():
        if key not in params:
            continue  # 缺省 = 取 schema 默认值(Primitive.defaults()),不算错误
        value = params[key]
        type_name = spec["type"]
        if not _TYPE_CHECKERS[type_name](value):
            errors.append(f"{primitive.name}.{key}: 期望类型 {type_name},实得 {type(value).__name__}")
            continue
        enum = spec.get("enum")
        if enum is not None and value not in enum:
            errors.append(f"{primitive.name}.{key}: 值 {value!r} 不在允许集合 {enum}")
        if type_name == "array":
            item_type = spec.get("items")
            if item_type is not None:
                bad_items = [v for v in value if not _TYPE_CHECKERS[item_type](v)]
                if bad_items:
                    errors.append(
                        f"{primitive.name}.{key}: 数组元素类型应为 {item_type},实得 {bad_items!r}"
                    )
    return errors


# ══════════════════════════════════════════════════════════════════════════
# 首包 K4-pack 用到的 5 个原语实现(现值来源见模块头)
# ══════════════════════════════════════════════════════════════════════════

def _stock_hygiene(
    row: Mapping[str, Any],
    *,
    close_min: float = 2.0,
    amount_ma20_min: float = 20000.0,
    require_ma20: bool = True,
    allowed_boards: Sequence[str] = ("MAIN", "GEM", "STAR"),
    exclude_st: bool = True,
) -> bool:
    """个股卫生线(现值 ← `research/panel.py::base_universe_expr` +
    `intel_candidates.py::_ALLOWED_BOARDS`)。`board`/`is_st` 是分类/身份数据,
    不计入白名单 `inputs`(见模块头「身份/分类数据」节)。"""
    if exclude_st and row.get("is_st"):
        return False
    if row.get("board") not in allowed_boards:
        return False
    close = row.get("close")
    if close is None or close < close_min:
        return False
    if require_ma20 and row.get("ma20") is None:
        return False
    amount_ma20 = row.get("amount_ma20")
    if amount_ma20 is None or amount_ma20 < amount_ma20_min:
        return False
    return True


def _non_new_stock(row: Mapping[str, Any], *, min_days: int = 120) -> bool:
    """非次新(现值 ← `intel_candidates.py::NON_NEW_MIN_DAYS`,同
    `strategy/signals.py::forbid_new_stock` 口径:`days_since_listing < min_days`
    即次新,本原语返回"是否通过"故取反)。`days_since_listing` 缺失(面板未覆盖
    的极端情形)保守判非通过,不臆造"反正是老股"。"""
    days = row.get("days_since_listing")
    return days is not None and days >= min_days


_K4_ACTIONS: Tuple[str, ...] = ("exclude", "tag", "ignore")


def _k4_advisory_gate(
    row: Mapping[str, Any],
    *,
    hard_cut_action: str = "exclude",
    avoid_flag_action: str = "tag",
) -> bool:
    """K4 安检开关(现值 ← `intel_candidates.py`:`hard_cut` 命中拦截出池 =
    `exclude`,`avoid_flag` 命中打标保留 = `tag`,机器不禁)。`row["k4_section"]`
    取值 `"hard_cut" | "avoid_flag" | None`——`tag`/`ignore` 两种取值下本原语都
    返回 `True`(不拦),两者的差异留给调用方展示层(是否把标注透出到卡面),
    本原语只管"拦不拦"这一件事。"""
    section = row.get("k4_section")
    if section == "hard_cut" and hard_cut_action == "exclude":
        return False
    if section == "avoid_flag" and avoid_flag_action == "exclude":
        return False
    return True


# 浮点比较容差(工程不变量,非策略参数——同 `sentinel/holding.py`/
# `intel_candidates.py::_INDUSTRY_GATE_EPS` 先例:裸 >=/<= 比较除法产生的浮点
# 噪声是本项目反复踩过的通用坑,见项目 CLAUDE.md「盘中哨兵」节)。
_LIFT_EPS = 1e-9


def _industry_dominance_gate(row: Mapping[str, Any], *, min_lift: float = 2.0) -> bool:
    """行业闸(现值 ← `intel_candidates.py::INDUSTRY_GATE_MIN_LIFT`)。
    `row["industry_lift"]` 由调用方预先算好(lift = 板内该行业占比 ÷ 全市场该
    行业占比,分母/分子的计算逻辑属"扫描层引擎本体",不进包);本原语只管
    "lift 是否达到主导行业阈值"这一道阈值判断。`industry_lift` 缺失(算不出,
    如全市场查无该行业占比)保守判不通过。"""
    lift = row.get("industry_lift")
    return lift is not None and lift >= min_lift - _LIFT_EPS


_DEFAULT_RANK_DIMS: Tuple[str, ...] = ("industry_rank", "industry_persist_days", "yellow_card_count")


def _intel_rank_priority(
    row: Mapping[str, Any], *, dims: Sequence[str] = _DEFAULT_RANK_DIMS
) -> Tuple[float, ...]:
    """情报排序三级键(现值 ← `intel_candidates.py::_sort_key` 的键顺序,三者
    均升序)。`dims` 就是"权重表示"——**顺序即权重**:元组字典序比较下,第一维
    的区分力天然压过后面所有维度之和,这是对原函数「三级优先级」最忠实的转译
    (不发明一组会改变比较语义的数值权重)。缺值(如无 industry/成员<5 未参与
    排名)→ `+inf` 排最后,不静默当 0(原函数同款纪律,0 会把无行业票错误顶到
    榜首)。**不含**原函数的确定性兜底(`base_score DESC`/`code ASC`)——那是
    "同名次时如何保证可复现"的实现细节,不是"三级键"本身要表达的排序意图,留给
    未来消费方的实现自行补上确定性 tie-break。"""
    return tuple(float("inf") if row.get(d) is None else row.get(d) for d in dims)


_register(Primitive(
    name="stock_hygiene",
    kind="filter",
    inputs=("daily.close", "daily.amount_ma20", "daily.ma20"),
    params_schema={
        "close_min": {"type": "number", "default": 2.0},
        "amount_ma20_min": {"type": "number", "default": 20000.0},
        "require_ma20": {"type": "boolean", "default": True},
        "allowed_boards": {"type": "array", "items": "string", "default": ["MAIN", "GEM", "STAR"]},
        "exclude_st": {"type": "boolean", "default": True},
    },
    impl=_stock_hygiene,
))

_register(Primitive(
    name="non_new_stock",
    kind="filter",
    inputs=("daily.days_since_listing",),
    params_schema={
        "min_days": {"type": "integer", "default": 120},
    },
    impl=_non_new_stock,
))

_register(Primitive(
    name="k4_advisory_gate",
    kind="filter",
    inputs=("k4_advisory.sections",),
    params_schema={
        "hard_cut_action": {"type": "string", "enum": list(_K4_ACTIONS), "default": "exclude"},
        "avoid_flag_action": {"type": "string", "enum": list(_K4_ACTIONS), "default": "tag"},
    },
    impl=_k4_advisory_gate,
))

_register(Primitive(
    name="industry_dominance_gate",
    kind="filter",
    # 空:lift 由扫描层引擎本体从 `stock_basic.industry`(身份/分类数据,不受
    # 白名单约束,见模块头)算好后塞进 row,本原语不直接引用任何预计算表的列。
    inputs=(),
    params_schema={
        "min_lift": {"type": "number", "default": 2.0},
    },
    impl=_industry_dominance_gate,
))

_register(Primitive(
    name="intel_rank_priority",
    kind="sort_key",
    inputs=(
        "industry_strength_daily.industry_rank",
        "industry_strength_daily.persist_days",
        "k4_advisory.avoid_flag",
    ),
    params_schema={
        "dims": {"type": "array", "items": "string", "default": list(_DEFAULT_RANK_DIMS)},
    },
    impl=_intel_rank_priority,
))


__all__ = [
    "is_allowed_feature",
    "Primitive",
    "PRIMITIVES",
    "validate_all_primitives_whitelisted",
    "validate_params",
]
