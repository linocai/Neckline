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
`daily` 与 `daily_basic` 两张表;工程上由 `neckline/data/panel.py::
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

**V2-④ 新增 4 个原语(市场扫描层「驱动种子」资格判断,plan §五 V2-④)**:
`neckline/scan/seeds.py` 消费,回答"这个事实(行业/概念/簇/异动股)够不够格
当一颗驱动种子",**不是**"这个事实存不存在"——三张事实表(`corr_matrix_daily`/
`limit_cluster_daily`/`leader_structure_daily`)与 `industry_strength_daily`
本身的计算不读包配置(工程常量,同 `industry_strength.py::_MIN_MEMBERS` 的既有
分工),只有"够不够格上报为种子"这道阈值才读包。**这四个原语的参数值是本次
新拟定的启发式起点(非回测拟合值,如实登记)**,与前 5 个"从现有代码抽现值"
性质不同,理由见各自 docstring:
    · `hot_industry_seed`      —— row=`industry_strength_daily` 单日单行业。
    · `surging_concept_seed`   —— row=`ths_daily` 单日单概念指数。**新增白名单
      模式 `ths_daily.*`**(`_ALLOWED_FEATURES` 从 8 个扩到 9 个——概念板块原始
      日线是继续被读取的既有数据源,`ths_daily.pct_change` 与 `daily.close`/
      `moneyflow_dc.net_amount` 同属"预计算表/EOD 原始数据"这一类,不是 LLM
      产出,符合白名单"只放行预计算表与 EOD 面板列"的既定标准,不是破例)。
    · `limit_cluster_seed`     —— row=`limit_cluster_daily` 单日单簇(聚合到
      簇一级,不是簇成员一级)。
    · `anomaly_cluster_seed`   —— row=**单票**当日 `daily_basic.volume_ratio`
      (量比异动)。命名与其它三个"种子类型名"对齐(对应"异动簇"),但 `impl`
      判断的是"这只票算不算异动"(逐票),**聚类**(按行业分组、凑够
      `min_cluster_members` 只)是 `seeds.py` 的编排逻辑,不在本原语内——
      `min_cluster_members` 因此是"声明了但 `impl` 不使用"的参数(`seeds.py`
      直接读 `Pack.seeds_config()` 取用,`Primitive.run()` 调用路径仍会把它
      传给 `impl` 但被忽略,同 `params_schema` 声明可以承载"给编排层看"的参数
      这一既有设计弹性,不违反"参数必须都被 impl 消费"这类并不存在的规则)。

**V2-③-K7 新增(K7 需求 4:原语/白名单/schema 跟进,plan §五 V2-③-K7)**:只加
不改,③ 已完工的 5 个原语与 ④ 已完工的 4 个原语语义**一字不动**:
    · 白名单新增第 10 个模式 **`industry_stage_daily.*`**(④b 产出的行业题材
      阶段表),供 `intel_rank_priority` 新排序维度 `industry_stage_score` 与
      未来 ⑥ 的 `driver_freshness` 维度引用。
    · `intel_rank_priority` 的 `dims` 取值扩容到 5 个合法维度(K4-pack-v1 仍只用
      前 3 个,行为逐位不变):既有 `industry_rank`/`industry_persist_days`/
      `yellow_card_count` 三个 + 新增 `industry_stage_score`(K7 需求 1b,读五态
      打分映射)/`leader_rs_rank`(K7 需求 1a,读 `leader_structure_daily.rs_rank`,
      同时补齐"该列可被 sort_key 类原语引用"这条通路——白名单本身早已放行
      `leader_structure_daily.*`,缺的是一个真的把它列进 `inputs`/当作 `dims`
      取值使用的 sort_key 原语)。**每个维度的排序方向显式登记在
      `_RANK_DIM_DIRECTIONS`,不靠"一律当同向"猜**:`industry_rank`/
      `leader_rs_rank` 是名次(升序为优),`industry_stage_score` 是打分(降序
      为优,分越高越优先),`yellow_card_count` 升序为优(既有语义不变)。引用了
      未登记方向的维度名 → `_intel_rank_priority` 直接 `ValueError`(fail loud,
      不猜方向)。缺值(任一维度算不出)→ 该维恒排 `+inf`(排最后),与方向
      无关——"缺数"永远是"最差",不因维度改成降序而意外变成"最优"。
    · 本次扩容对 K4-pack-v1 是**纯增量**:白名单只加不减、`intel_rank_priority`
      默认 `dims`(`_DEFAULT_RANK_DIMS`)未变、既有 3 个维度的方向全部是 `asc`
      (与扩容前 `row.get(d)` 直接取值再字典序比较的既有行为数值等价,只是显式
      包了一层方向声明)——`ENGINE_API_VERSION` 因此**不变**(判定依据与
      `config.tier.stage_scores` schema 扩展的判定合并写在
      `neckline/selection/pack.py::_validate_stage_scores` 与 PROJECT_PLAN.md
      V2-③-K7 完工记录,不在此重复)。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

# —— 特征白名单(plan §五 V2-③ 原文逐字照抄的 8 个模式 + V2-④ 新增 1 个 +
# V2-③-K7 新增 1 个,共 10 个)——————————————————————————————————————————
# **模块级常量,不是策略参数**:这是"引擎允许原语引用哪些表"的不变量,不随包变化
# ——它由本文件自己列出并在 `tests/test_selection_primitives.py` 的模块级数值
# 字面量白名单扫描测试里显式登记(见该文件 `_ENGINE_CONSTANT_WHITELIST`)。
#
# `ths_daily.*`(V2-④ 新增,原 8 个之外):概念板块原始日线(`data/concept_data.py`
# 日更自愈),供 `surging_concept_seed` 原语判断"哪个概念今天暴起"。它与
# `daily`/`moneyflow_dc` 同属"预计算表 / EOD 原始数据"这一类,不是 LLM 产出,
# 符合白名单既定标准(见模块头「V2-④ 新增 4 个原语」节)。
#
# `industry_stage_daily.*`(V2-③-K7 新增,第 10 个):④b 产出的行业题材阶段
# 六态状态机表(EOD 预计算,在线只读),供 `intel_rank_priority` 新排序维度
# `industry_stage_score` 与未来 ⑥ 的 `driver_freshness` 维度引用,同样是
# "预计算表列",不是 LLM 产出(见模块头「V2-③-K7 新增」节)。
_ALLOWED_FEATURES: Tuple[str, ...] = (
    "industry_strength_daily.*",
    "corr_matrix_daily.*",
    "limit_cluster_daily.*",
    "leader_structure_daily.*",
    "limit_derived.*",
    "daily*",          # 故意无点号:同时覆盖 daily.* 与 daily_basic.*
    "moneyflow_dc.*",
    "k4_advisory.*",
    "ths_daily.*",
    "industry_stage_daily.*",
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
# V2.2-①(§七 P2-47 结案):选股域 × 回测域**卫生线共享常量,唯一源在此**。
# 此前 `_stock_hygiene` 的默认值与 `neckline/research/panel.py::base_universe_expr`
# 的字面量只靠 docstring 人工声称一致(无 import、无守门),任何一边改数另一边
# **静默漂移** —— 选股与回测对"什么票配入场"分叉且无人报警。修法 = 抽共享常量
# (plan §五 ① 首选修法):`base_universe_expr` 改 import 这三个名字,两域同源。
# ⛔ **板块口径刻意不在共享之列**:回测域排的是 `BSE`(`board != "BSE"`,含 STAR,
# 是 K2–K7 全部战役可比性的地基,一个字不许动),选股域 V2.2 起按包配置排
# `STAR + BSE`(`allowed_boards=["MAIN","GEM"]`,K8 §三)—— **两者不是同一个量**,
# 强行"统一"会悄悄改掉回测域的选股域清洗。同理 `exclude_st` 两侧实现机制不同
# (研究域 `S.forbid_st()` 表达式 vs 原语读 `row["is_st"]`),不抽。
# 数值登记:`tests/test_selection_primitives.py::_ENGINE_CONSTANT_WHITELIST`。
STOCK_HYGIENE_CLOSE_MIN: float = 2.0          # qfq 收盘下限(元)
STOCK_HYGIENE_AMOUNT_MA20_MIN: float = 20000.0  # 20 日均额下限(千元,=2000 万元)
STOCK_HYGIENE_REQUIRE_MA20: bool = True       # 至少 20 交易日历史(ma20 非空)


# ══════════════════════════════════════════════════════════════════════════
# 首包 K4-pack 用到的 5 个原语实现(现值来源见模块头)
# ══════════════════════════════════════════════════════════════════════════

def _stock_hygiene(
    row: Mapping[str, Any],
    *,
    close_min: float = STOCK_HYGIENE_CLOSE_MIN,
    amount_ma20_min: float = STOCK_HYGIENE_AMOUNT_MA20_MIN,
    require_ma20: bool = STOCK_HYGIENE_REQUIRE_MA20,
    allowed_boards: Sequence[str] = ("MAIN", "GEM", "STAR"),
    exclude_st: bool = True,
) -> bool:
    """个股卫生线(三项数值默认 ← 模块级共享常量,P2-47 结案后与
    `neckline/research/panel.py::base_universe_expr` 同源)。`board`/`is_st` 是
    分类/身份数据,不计入白名单 `inputs`(见模块头「身份/分类数据」节)。
    ⚠ `allowed_boards` **默认值不动**(仍含 STAR):K8 §三「排除科创板」是**纯包
    配置**(`packs/K8-skeleton.json` 写 `["MAIN","GEM"]`),零代码改动(plan §五
    ① 原文);默认值是「原语的中性起点」不是「现役策略」,改默认值 = 悄悄改掉
    所有不显式给这个键的历史包语义。"""
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


def _industry_blacklist(row: Mapping[str, Any], *, industries: Sequence[str] = ()) -> bool:
    """行业黑名单(V2.2-① 新原语,K8 §三「排除白酒股」的机器载体)。按
    `stock_basic.industry` **精确归类**排除(继承 LinoN 教训:⛔ 非名称关键词匹配
    ——按板块/行业整段归类,不枚举精确子串,见项目 CLAUDE.md「钉死的领域常量·
    板块分类」条)。`industry` 属「身份 / 分类数据」,**不进 `_ALLOWED_FEATURES`**
    (既有约定,同 `board`/`is_st`,见模块头「身份/分类数据」节)。

    **`industry` 缺失 → 保守判通过(True,不排除)** —— 与 `_non_new_stock`
    「缺失判不通过」的保守方向**刻意相反**,⛔ 别"统一":黑名单缺数时排除会
    **误杀**(把查无行业的好票当白酒扔掉),白名单缺数时放行会**漏网**(把没证据
    的票放进来)——两者代价不对称,保守方向各自取「宁可少做动作」的那一侧。
    不臆造"反正不是白酒"这种正面结论,只是"没有排除它的依据"(plan §五 ①
    测试与守门原文)。

    实测口径(2026-08-08 对本地 `stock_basic` 核实):白酒股的 `industry` 取值
    = **「白酒」**(19 只);同库另有「红黄酒」(9)/「啤酒」(8)/「酒店餐饮」(9)
    等**不同的**归类值,K8 只排白酒,故 `packs/K8-skeleton.json` 写 `["白酒"]`
    恰好只命中那 19 只,不殃及其余酒类。"""
    industry = row.get("industry")
    if industry is None:
        return True
    return industry not in set(industries)


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

# —— K7 需求 4 / ③-K7-B:每个合法排序维度的显式方向声明(定死,不许靠"一律
# 当同向"猜)。**登记的是 K4-pack + K7-pack 两包共用的合法维度全集**——
# K4-pack-v1 只用前三个(全部 `asc`,与扩容前"直接取值参与字典序比较"的既有
# 行为数值等价);K7-pack 额外引用后两个:`industry_stage_score` 是打分(K7
# 需求 1b,分越高越优先 = `desc`),`leader_rs_rank` 是簇内 RS 名次(K7 需求
# 1a,名次越小越优先 = `asc`,同 `industry_rank` 语义)。新增排序维度前必须先
# 在这里登记方向,`_intel_rank_priority` 拒绝处理未登记的维度名(fail loud,
# 不猜)。值是字符串(`"asc"`/`"desc"`),不是数值权重,故不落
# `_ENGINE_CONSTANT_WHITELIST` 的数值字面量扫描范围。
_RANK_DIM_DIRECTIONS: Dict[str, str] = {
    "industry_rank": "asc",
    "industry_persist_days": "asc",
    "yellow_card_count": "asc",
    "industry_stage_score": "desc",
    "leader_rs_rank": "asc",
}


def _intel_rank_priority(
    row: Mapping[str, Any], *, dims: Sequence[str] = _DEFAULT_RANK_DIMS
) -> Tuple[float, ...]:
    """情报排序键(现值 ← `intel_candidates.py::_sort_key` 的三级键顺序;
    K4-pack-v1 仍是三者均升序。K7 需求 4 扩容后 `dims` 可以是
    `_RANK_DIM_DIRECTIONS` 五个合法维度的任意子集/顺序)。`dims` 就是"权重
    表示"——**顺序即权重**:元组字典序比较下,第一维的区分力天然压过后面所有
    维度之和,这是对原函数「三级优先级」最忠实的转译(不发明一组会改变比较
    语义的数值权重)。

    **每个维度的方向必须在 `_RANK_DIM_DIRECTIONS` 登记,不按"一律升序"猜**——
    `industry_stage_score` 是打分,分越高越优先;若仍按升序原样参与字典序
    比较,分数低的反而会排到前面,方向恰好反了。做法:`asc` 维度原样取值参与
    比较,`desc` 维度取负号再参与比较(字典序比较仍然是"越小越靠前"这一个
    约定,不改变 tuple 内其余维度的比较语义,也不改变最终排序结果的可读性
    ——只是取值前多做一步符号翻转)。引用了未登记方向的维度名 →
    `ValueError`(fail loud,不猜方向)。

    缺值(如无 industry/成员<5 未参与排名/K7 五态阶段表当日缺行)→ `+inf` 排
    最后,**与方向无关**——不静默当 0(原函数同款纪律,0 会把无行业票错误顶到
    榜首;对 `desc` 维度而言"当 0"更是双重错误:0 分是"过热"态的真实取值,
    `None` 是"没数据",两者绝不可互相顶替,见 ④b-C 保险丝纪律)。**不含**原
    函数的确定性兜底(`base_score DESC`/`code ASC`)——那是"同名次时如何保证
    可复现"的实现细节,不是"排序键"本身要表达的排序意图,留给未来消费方的
    实现自行补上确定性 tie-break。"""
    out: List[float] = []
    for d in dims:
        direction = _RANK_DIM_DIRECTIONS.get(d)
        if direction is None:
            raise ValueError(
                f"intel_rank_priority: 排序维度 {d!r} 未在 _RANK_DIM_DIRECTIONS "
                f"登记排序方向(仅支持 {sorted(_RANK_DIM_DIRECTIONS)}),拒绝猜测"
                "方向,fail loud。"
            )
        value = row.get(d)
        if value is None:
            out.append(float("inf"))
            continue
        out.append(float(value) if direction == "asc" else -float(value))
    return tuple(out)


_register(Primitive(
    name="stock_hygiene",
    kind="filter",
    inputs=("daily.close", "daily.amount_ma20", "daily.ma20"),
    params_schema={
        "close_min": {"type": "number", "default": STOCK_HYGIENE_CLOSE_MIN},
        "amount_ma20_min": {"type": "number", "default": STOCK_HYGIENE_AMOUNT_MA20_MIN},
        "require_ma20": {"type": "boolean", "default": STOCK_HYGIENE_REQUIRE_MA20},
        "allowed_boards": {"type": "array", "items": "string", "default": ["MAIN", "GEM", "STAR"]},
        "exclude_st": {"type": "boolean", "default": True},
    },
    impl=_stock_hygiene,
))

_register(Primitive(
    name="industry_blacklist",
    kind="filter",
    # 空:`industry` 是身份/分类数据,不受特征白名单约束(同 industry_dominance_gate
    # 的 inputs=() 先例,见模块头「身份/分类数据」节)。
    inputs=(),
    params_schema={
        "industries": {"type": "array", "items": "string", "default": []},
    },
    impl=_industry_blacklist,
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
        "industry_stage_daily.stage",        # K7 需求 4:industry_stage_score 维度来源列
        "leader_structure_daily.rs_rank",    # K7 需求 4:leader_rs_rank 维度来源列
    ),
    params_schema={
        "dims": {"type": "array", "items": "string", "default": list(_DEFAULT_RANK_DIMS)},
    },
    impl=_intel_rank_priority,
))


# ══════════════════════════════════════════════════════════════════════════
# V2-④ 市场扫描层「驱动种子」资格判断(4 个新原语,现值来源见模块头对应节)
# ══════════════════════════════════════════════════════════════════════════

def _hot_industry_seed(
    row: Mapping[str, Any], *, max_rank: int = 10, require_strength_day: bool = True
) -> bool:
    """热点行业种子资格(row=`industry_strength_daily` 单日单行业)。`industry_rank`
    缺失(成员数不足 `_MIN_MEMBERS`,当日未评级)→ 不通过,保守(同
    `industry_dominance_gate` 缺值即不通过的既有纪律)。"""
    rank = row.get("industry_rank")
    if rank is None:
        return False
    if require_strength_day and not row.get("is_strength_day"):
        return False
    return rank <= max_rank


def _surging_concept_seed(row: Mapping[str, Any], *, min_pct_change: float = 5.0) -> bool:
    """暴起概念种子资格(row=`ths_daily` 单日单概念指数)。用**当日单日涨幅**
    (`pct_change`)而非板块年龄(`board_age`)或 20 日动量——"暴起"要捕捉的是
    "今天突然冲出来",不是"已经持续強势一段时间"这个不同的量(与
    `report/sectors.py::board_age` 的既有分工一致:后者只做板块展示,不再是
    任何判据的数据源,见项目 CLAUDE.md「双会话架构」节;本原语不读它)。"""
    pct = row.get("pct_change")
    return pct is not None and pct >= min_pct_change - _LIFT_EPS


def _limit_cluster_seed(
    row: Mapping[str, Any], *, min_cluster_size: int = 2, min_consecutive_days: int = 2
) -> bool:
    """涨停簇种子资格(row=`limit_cluster_daily` **簇一级**聚合行,不是簇成员)。
    `cluster_size`/`consecutive_days` 满足任一门槛即通过——同日多只共振,或
    已连续接力多天,都算"够格当种子"(两者不是同一件事:前者是"广度",后者是
    "持续性")。事实表本身已保证 `cluster_size>=2`(`cluster.MIN_CLUSTER_SIZE`
    工程常量,见 `neckline/facts/limitmap.py`);本原语的 `min_cluster_size` 让
    包可以把这道门槛抬得更高(如只要 >=3 只共振才算种子)。"""
    size = row.get("cluster_size")
    days = row.get("consecutive_days")
    size_ok = size is not None and size >= min_cluster_size
    days_ok = days is not None and days >= min_consecutive_days
    return size_ok or days_ok


def _anomaly_cluster_seed(
    row: Mapping[str, Any], *, min_volume_ratio: float = 3.0, min_cluster_members: int = 2
) -> bool:
    """异动簇种子的**逐票**资格判断(row=单票当日 `daily_basic` 行)——量比
    (`volume_ratio`,今日成交量/近 5 日均量)达到门槛即算"当日异动"。**聚类**
    (按行业分组、凑够 `min_cluster_members` 只才算一簇)是 `neckline/scan/
    seeds.py` 的编排逻辑,不在本原语内——`min_cluster_members` 是声明给编排层
    读的参数,`impl` 本身不消费它(见模块头「V2-④ 新增 4 个原语」节说明)。"""
    vr = row.get("volume_ratio")
    return vr is not None and vr >= min_volume_ratio - _LIFT_EPS


_register(Primitive(
    name="hot_industry_seed",
    kind="filter",
    inputs=("industry_strength_daily.industry_rank", "industry_strength_daily.is_strength_day"),
    params_schema={
        "max_rank": {"type": "integer", "default": 10},
        "require_strength_day": {"type": "boolean", "default": True},
    },
    impl=_hot_industry_seed,
))

_register(Primitive(
    name="surging_concept_seed",
    kind="filter",
    inputs=("ths_daily.pct_change",),
    params_schema={
        "min_pct_change": {"type": "number", "default": 5.0},
    },
    impl=_surging_concept_seed,
))

_register(Primitive(
    name="limit_cluster_seed",
    kind="filter",
    inputs=("limit_cluster_daily.cluster_size", "limit_cluster_daily.consecutive_days"),
    params_schema={
        "min_cluster_size": {"type": "integer", "default": 2},
        "min_consecutive_days": {"type": "integer", "default": 2},
    },
    impl=_limit_cluster_seed,
))

_register(Primitive(
    name="anomaly_cluster_seed",
    kind="filter",
    inputs=("daily_basic.volume_ratio",),
    params_schema={
        "min_volume_ratio": {"type": "number", "default": 3.0},
        "min_cluster_members": {"type": "integer", "default": 2},
    },
    impl=_anomaly_cluster_seed,
))


__all__ = [
    "is_allowed_feature",
    "Primitive",
    "PRIMITIVES",
    "validate_all_primitives_whitelisted",
    "validate_params",
]
