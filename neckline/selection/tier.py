"""③Tier 分层引擎(plan §五 V2-⑥)。把 ⑤ 产出的**篮子候选**定档 T1/T2/T3。

**目标一句话**:定档**全机械、可完整复现**;LLM 只能在**同档内**微调次序并留痕,
**不得跨档**(§2.8-C 第 1 条 —— 「LLM 不进排序」精确化 = 不进**机械分**)。

**机械分五维**(维度选择与权重**读现役包**,代码里不许有第二份数字):

===================  ===========================================================
`sector_strength`    板块强度。行业侧读 `industry_strength_daily`(**只读表**,
                     P0-23 纪律);概念侧读今日概念板块热榜(`report/sectors.py`
                     既有在线路径)。二者取**较大者** —— 概念热榜只做加成、不做
                     减分(不在热榜上 ≠ 弱,只是没有这一路证据)。
`driver_freshness`   驱动新鲜度 = **行业五态打分**(K7 需求 1b)。读 ④b 的
                     `industry_stage_daily.stage`(六态英文码),经**现役包**的
                     `config.tier.stage_scores` 映射成分值。~~persist_days 单调
                     反用~~ 已被 H11 审计取代(非单调:2-3 天发酵态才是最优注意
                     力段)。**映射必须是包参数**(regime 敏感),校准走 ⑨ + 换包。
`leader_clarity`     龙头结构清晰度 = **簇内 RS20 头名度**(K7 需求 1a)。数据源
                     `leader_structure_daily.rs_rank`(经 ⑤ 解析进
                     `BasketMemberCandidate.rs_rank`),**依名次衰减**。
                     ⛔ 连板高度**不进**头名主定义(十二格审计判定它是双尾放大器,
                     用途在 ⑦-K7 的双尾标注);⛔ 成交额头名审计否决,只在
                     `rs_rank` 的 tie-break 里当次级键(落点在 ④,本块只消费)。
`tradability`        可交易性。一字比例与涨停占比 —— **买不进的涨停不算机会**
                     (蓝图 4.9;可交易性审计:龙头组可买率显著更低)。
`card_density`       红黄牌密度(`k4_advisory` 命中数,由 ⑤-b 卫生线的
                     `avoid_flag→tag` 那一档带过来)。**降格为风险信息与模型特征,
                     不等同禁买**(蓝图 7.2 / §2.0 第 3 条「机器不禁、人可复核」)。
===================  ===========================================================

**白名单锁(`_TIER_SCORE_INPUTS`,`intel_candidates._SORT_KEY_INPUTS` 体例平移)**
`mech_score()` 的入参是一个**同时装着机械维度与 LLM 产出字段**的特征行,而它只许
读白名单里那五个键 —— 单测用"记录实际访问过哪些键"的字典子类在**运行期**证明
「LLM 产出的任何字段不得进机械分」,不是靠注释自觉。

**容量**:T1 ≤ 2 / T2 ≤ 5 / T3 ≤ 10,**全部是上限非配额**(市场混沌时不许凑数,
允许**任何一档**为空——V2-⑥-b-B 纠正:「T3 无下限」曾让 `T3≤10` 变成事实上的
配额,这正是 V1「每天硬凑 20 只候选」的病)。落地 = 每档一道**质量线**(机械分
下限):够不到某档线就进不了那一档,哪怕那一档空着;连 T3 线都够不到 →
**当天不进任何档**(`DROP_BELOW_QUALITY_LINE`,与容量溢出的
`DROP_CAPACITY_OVERFLOW` 是两种不同的"没进来",不许合并,见 ⑥-b-C)。

**V2-⑥-b 追加(2026-08-02 planner 裁定)**:三档质量线的**权威**从"引擎常量"
移到"现役包" `config.tier.quality_lines`(与 `weights` 同标度,换权重会静默
改变 T1 选择性,两个数必须住在一起才谈得上一起校准)。`TIER1_MIN_SCORE` /
`TIER2_MIN_SCORE` / `TIER3_MIN_SCORE` 三个模块常量**降级为「包未给
`quality_lines` 时的缺省回退值」**,不再是权威;读取一律经
`resolve_quality_lines()`,**不直接读模块常量**(K4-pack-v1 没有这个键,回退
到这三个数正是它作为回滚锚必须保持的行为)。

**保险丝(承 P0-23「降级方向 = 不拦 + 显式披露」)**:任何一维算不出 → 取**中性分**
并在 `mech_breakdown_json` 的 `flags` 里如实标(如 `stage_missing`),**绝不写 0**
—— 0 是 `overheat` 的真实取值,与「没数据」撞车,「没有」与「没看」必须分得开。

**落库**:三张表(`baskets` / `basket_members` / `tier_history`)**同一事务**,
写入口在 `neckline/selection/basket_store.py`(见该模块头「运行期次序」);本模块
只产出内存结果 + 一个薄封装 `save_tier_result()`,**不自己写 INSERT**。

**编排归属**:全链路(⑤→⑥→⑦)编排在 ⑭-A 的 `report/pipeline.py`;本模块只提供
纯函数 + 落库封装,**不 import ⑦、不自己开编排**。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import polars as pl

from neckline.data.market_data import get_market_slice
from neckline.llm.base import ChatMessage, LLMProvider
from neckline.llm.budget import LEDGER_REASON, BudgetLedger
from neckline.llm.factory import get_provider
from neckline.llm.json_block import split_narrative_and_reference_json
from neckline.llm.prompt_context import TIMELINESS_RULES, date_anchor_line
from neckline.llm.router import TASK_TIER_RANK
from neckline.report.industry_strength_store import load_industry_strength
from neckline.report.sectors import DEFAULT_TOP_N, compute_sector_strength
from neckline.scan import cluster as cluster_mod
from neckline.scan import stage as stage_mod
from neckline.selection import basket_store, engine_api
from neckline.selection.pack import Pack, get_active_pack

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# 维度白名单与运行期访问锁
# ══════════════════════════════════════════════════════════════════════════

DIM_SECTOR_STRENGTH = "sector_strength"
DIM_DRIVER_FRESHNESS = "driver_freshness"
DIM_LEADER_CLARITY = "leader_clarity"
DIM_TRADABILITY = "tradability"
DIM_CARD_DENSITY = "card_density"

# **机械分唯一允许读的五个键**(plan §五 V2-⑥「白名单锁」:`_SORT_KEY_INPUTS`
# 体例平移)。`mech_score()` 拿到的特征行里**故意还装着 LLM 产出字段**
# (`_LLM_PROVENANCE_KEYS`),单测用记录访问键的字典子类在运行期证明它一个都没碰。
_TIER_SCORE_INPUTS = frozenset({
    DIM_SECTOR_STRENGTH, DIM_DRIVER_FRESHNESS, DIM_LEADER_CLARITY,
    DIM_TRADABILITY, DIM_CARD_DENSITY,
})

# 特征行里**刻意混入**的 LLM 产出字段。它们存在的唯一目的就是给访问锁一个可证伪的
# 对象 —— 白名单单测断言 `accessed == _TIER_SCORE_INPUTS`,也就同时断言了这些键
# **一个都没被读过**。⛔ 往这里加键很安全,往 `_TIER_SCORE_INPUTS` 加键要先过
# §2.8-C(LLM 产出不进机械分)。
_LLM_PROVENANCE_KEYS = frozenset({
    "llm_name", "llm_driver", "llm_why_now", "llm_member_reasons",
    "llm_roles", "llm_evidence_claims",
})

_TIER_ROW_KEYS = _TIER_SCORE_INPUTS | _LLM_PROVENANCE_KEYS


# ══════════════════════════════════════════════════════════════════════════
# 引擎常量(**引擎本体,不进包**;数值字面量在
# `tests/test_selection_primitives.py::_ENGINE_CONSTANT_WHITELIST` 逐条登记)
# ══════════════════════════════════════════════════════════════════════════

# 容量上限(plan §五 V2-⑥ 原文写死的产品规则,同 `aggregate.MIN_MEMBERS/MAX_MEMBERS`
# 性质:改它等于改产品定义,要走 plan 而不是换包)。**上限非配额** —— 见
# `assign_tiers()`。
TIER_CAPACITY: Dict[int, int] = {1: 2, 2: 5, 3: 10}
TIERS: Tuple[int, ...] = (1, 2, 3)

# 每档的**质量线**(机械分下限)。V2-⑥-b-B 纠正:T3 也有下限——没有下限时
# `T3≤10` 是事实上的配额(只要有 ≥10 个候选就永远填满),质量线的职责是
# **防止把平庸篮子抬进任何一档**("市场混沌时不许凑数",不是只对 T1/T2 说的)。
#
# ⚠ **以下三个数是「包未给 `config.tier.quality_lines` 时的缺省回退值」,
# 不再是权威**(V2-⑥-b-A planner 裁定:质量线归属改判"进包",理由是它与五维
# 权重同标度、换权重会静默改变 T1 选择性,两者必须住在一起才能一起校准)。
# 读取一律经 `resolve_quality_lines()`,业务代码不直接读这三个模块常量。
# **数值本身仍是临时工程默认**(同 ⑤-c `MIN_LIFT_SAMPLE_SIZE=5` 的处置姿势,
# 未获证据支持):五维各自归一到 [0,1]、权重归一后加权和亦在 [0,1],`0.5` 是
# "整体中性"的自然基准 —— T1 线取 `0.60`(明显好于中性)、T2 线取 `0.40`
# (不明显差于中性)、T3 线取 `0.25`(planner 拟定:明显低于 T2 但不为零,不为
# 零是因为零就退回"无下限=配额"的老病)。前向校准走 ⑨ 评价引擎周报 + 进化
# 门禁(= 换包),⛔ 不许顺手改这里的数、也不许顺手改包里的数(§七 P3-33 挂账)。
TIER1_MIN_SCORE = 0.60
TIER2_MIN_SCORE = 0.40
TIER3_MIN_SCORE = 0.25

# `assign_tiers()` 的 `quality_lines` 形参默认值——保证既有调用方(测试与任何
# 未来的直接调用)不传这个新参数时行为不变。三键只读不改,共享同一个字典
# 对象是安全的。
_DEFAULT_QUALITY_LINES: Dict[str, float] = {
    "tier1_min": TIER1_MIN_SCORE, "tier2_min": TIER2_MIN_SCORE, "tier3_min": TIER3_MIN_SCORE,
}

# 某一维算不出时的**中性分**(⛔ 不是 0 —— 0 在 `stage_scores` 里是 `overheat`
# 的真实取值,拿它冒充"没数据"就把「没有」和「没看」混成一件事)。取 [0,1] 的
# 中点 = "这一维不提供任何倾向",配合 `flags` 里的 `*_missing` 如实披露。
NEUTRAL_DIM_SCORE = 0.5

# `tradability` 的两档扣分:一字板 = 当日**根本买不进**(全扣);涨停但开过板 =
# 盘中有过成交机会(半扣)。两者都是"机会的可实现性"折价,不是对涨停本身的贬低。
ONE_WORD_PENALTY = 1.0
LIMIT_UP_PENALTY = 0.5

# 浮点比较容差(同 `sentinel/holding.py::_EPS` / `primitives._LIFT_EPS` 先例:
# 裸 >=/<= 比较除法产生的浮点噪声是本项目通用坑)。
_EPS = 1e-9

# 喂给 LLM 微调段的每档篮子上限(上下文护栏,不参与任何判据 —— 档容量本身已经
# 更小,这条只是"万一未来容量放宽也不至于撑爆上下文"的兜底)。
MAX_BASKETS_IN_RANK_CONTEXT = 20


# —— `mech_breakdown_json.flags` 的取值(语义不合并,每种"没算出来"分开查)——
FLAG_SECTOR_MISSING = "sector_strength_missing"        # 行业强度表与概念热榜都没有
FLAG_STAGE_MISSING = "stage_missing"                   # 阶段表当日无该行业行(plan 点名)
FLAG_STAGE_SCORES_ABSENT = "stage_scores_absent"       # 现役包没写 stage_scores 这一段
FLAG_STAGE_UNMAPPED = "stage_unmapped"                 # 有阶段码,但包里没给它打分
FLAG_LEADER_MISSING = "leader_clarity_missing"         # 成员全无 rs_rank(簇内排不出)
FLAG_TRADABILITY_MISSING = "tradability_missing"       # 涨跌停/行情切片读不到
FLAG_CARD_DENSITY_MISSING = "card_density_missing"     # ⑤-b 报了 k4_unavailable

# 每一维「取了中性分」对应哪个 flag(V2-⑥-b-D 新增,`neutral_filled_weight()`
# 的唯一依据)。⚠ **只认这些 flag,不认数值** —— `leader_clarity` 的 `1/rank`
# 在 `rank=2` 时恰好也等于 `NEUTRAL_DIM_SCORE`(真实第二名与"没数据"数值撞车,
# 见 `_dim_leader_clarity` 与本模块设计判断②),拿数值反推"是不是中性填充"在
# 这里会判错,只有 flag 靠得住。`driver_freshness` 有两个互斥的缺数据 flag
# (整段缺 `stage_scores` / 当日无该行业阶段行)都算,`FLAG_STAGE_UNMAPPED`
# **不算**——它单独出现时该维仍可能是"别的"行业算出来的真实值(见
# `_dim_driver_freshness` docstring),不代表这一维被中性填充。
_DIM_MISSING_FLAGS: Dict[str, frozenset] = {
    DIM_SECTOR_STRENGTH: frozenset({FLAG_SECTOR_MISSING}),
    DIM_DRIVER_FRESHNESS: frozenset({FLAG_STAGE_MISSING, FLAG_STAGE_SCORES_ABSENT}),
    DIM_LEADER_CLARITY: frozenset({FLAG_LEADER_MISSING}),
    DIM_TRADABILITY: frozenset({FLAG_TRADABILITY_MISSING}),
    DIM_CARD_DENSITY: frozenset({FLAG_CARD_DENSITY_MISSING}),
}

# —— LLM 微调段状态(与 ⑤ 的 `STAGE_*` 同一套语义纪律:不合并)————————————
LLM_OK = "ok"
LLM_NO_PROVIDER = "no_provider"
LLM_CALL_FAILED = "call_failed"
LLM_BUDGET_EXHAUSTED = "budget_exhausted"
LLM_PARSE_FAILED = "parse_failed"
LLM_NOT_NEEDED = "not_needed"          # 没有篮子 / 每档都只有 1 个,没什么可微调的

# —— LLM 提案的拒收码(**跨档一律拒收**,守门单测)——————————————————————
REJECT_CROSS_TIER = "cross_tier"           # 想把篮子挪到别的档 → 直接丢弃
REJECT_UNKNOWN_BASKET = "unknown_basket"   # 提案里的 basket_key 今天不存在
REJECT_BAD_RANK = "bad_rank"               # 名次不是 1..n 的整数
REJECT_SLOT_TAKEN = "slot_taken"           # 两条提案抢同一个名次(先到先得,后到丢弃)
REJECT_DUPLICATE_KEY = "duplicate_key"     # 同一个篮子被提了两次(第二条丢弃)
REJECT_MALFORMED = "malformed"             # 形状不对

DROP_CAPACITY_OVERFLOW = "capacity_overflow"       # 分数够、位置满 → 机会多到装不下
DROP_BELOW_QUALITY_LINE = "below_quality_line"     # 连 tier3_min 都没过 → 今天没什么好货
# ⚠ 两者是相反的市场结论(⑥-b-C),⛔ 不许合并成一个"未入选"。

TIER_RANK_SYSTEM_PROMPT = f"""你是 A 股短线交易系统里的「同档次序参谋」。

系统已经用**机械分**把今天的篮子定好了档位(T1/T2/T3)。你的权限**只有一个**:
在**同一个档位内部**微调篮子的先后次序,并说明理由。

硬边界(违反者会被系统机械丢弃,不会生效):
1. **绝对不许跨档**:不许把任何篮子从一个档挪到另一个档,也不许新增或删除篮子。
2. 只能给出**已经存在的** basket_key;名次必须是该档内 1..N 的整数,不许重复。
3. 你的次序是**注意力优先级**,不是收益预测,更不是买入建议。
4. 没有理由调整就交空数组 —— **不调整是完全正常的输出**,不要为了显得有用而乱动。

{TIMELINESS_RULES}

输出格式(先写一段自由说明,最后附一个 ```json 围栏块):

```json
{{"adjustments": [{{"basket_key": "…", "tier": 1, "rank_in_tier": 2, "reason": "…"}}]}}
```
"""


# ══════════════════════════════════════════════════════════════════════════
# 数据形状
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class TierFeatureContext:
    """一天的**机械特征**快照(只读,本模块不改任何一张表)。每一路独立包保险丝
    —— 少一路只是那一维取中性分并如实标,绝不让当日无定档。"""

    trade_date: date
    industry_rank: Dict[str, int] = field(default_factory=dict)
    industry_total: int = 0
    concept_rank_by_code: Dict[str, int] = field(default_factory=dict)
    concept_total: int = 0
    stage_of: Dict[str, str] = field(default_factory=dict)
    stage_available: bool = False
    limit_up: Set[str] = field(default_factory=set)
    one_word: Set[str] = field(default_factory=set)
    tradability_available: bool = False


@dataclass(frozen=True)
class TierDecision:
    """一个篮子的定档结果(= `tier_history` 一行减 `basket_id`,`basket_id` 落库
    时才有)。`rank_mech` 与 `rank_in_tier` **两个都存**:前者是机械原始序、后者是
    LLM 微调后的最终序,§2.8-C 第 1 条要求可复现可归因,不许只存最终结果。"""

    basket_key: str
    tier: int
    mech_score: float
    breakdown: Dict[str, Any]
    rank_mech: int          # 档内机械序(1-based)
    rank_in_tier: int       # 档内最终序(1-based)
    llm_rank_delta: int = 0     # = rank_mech − rank_in_tier(正 = 被 LLM 往前提)
    llm_reason: Optional[str] = None


@dataclass(frozen=True)
class DroppedBasket:
    basket_key: str
    reason: str
    mech_score: float


@dataclass(frozen=True)
class RejectedAdjustment:
    """被机械校验拦下的 LLM 微调提案(**留痕**:拒了什么、为什么、原样是什么)。"""

    reason: str
    detail: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TierResult:
    trade_date: str
    decisions: Tuple[TierDecision, ...] = ()
    dropped: Tuple[DroppedBasket, ...] = ()
    rejected_adjustments: Tuple[RejectedAdjustment, ...] = ()
    llm_stage: str = LLM_NOT_NEEDED
    llm_narrative: str = ""
    pack_version: str = ""
    weights: Dict[str, float] = field(default_factory=dict)
    # 本次定档实际用的三档质量线(`resolve_quality_lines()` 解出来的那份,已经
    # 是"包给了就用包的、缺了就回退引擎默认"之后的最终值)——同 `weights`,
    # 是审计快照,不是可写配置。
    quality_lines: Dict[str, float] = field(default_factory=dict)
    notes: Tuple[str, ...] = ()

    @property
    def llm_adjusted(self) -> bool:
        return any(d.llm_rank_delta != 0 for d in self.decisions)

    def by_tier(self) -> Dict[int, List[TierDecision]]:
        out: Dict[int, List[TierDecision]] = {t: [] for t in TIERS}
        for d in sorted(self.decisions, key=lambda x: (x.tier, x.rank_in_tier)):
            out[d.tier].append(d)
        return out

    def tier_by_basket_key(self) -> Dict[str, int]:
        return {d.basket_key: d.tier for d in self.decisions}


# ══════════════════════════════════════════════════════════════════════════
# 特征装配(每天一次,全篮复用)
# ══════════════════════════════════════════════════════════════════════════

def build_feature_context(
    trade_date: date,
    codes: Sequence[str],
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> TierFeatureContext:
    """装配五维所需的机械数据。**四路各自独立 try/except**(§五铁律:核心管线对
    可选情报输入的调用必须包保险丝),任一路塌了只让对应维度取中性分。"""
    ctx = TierFeatureContext(trade_date=trade_date)
    wanted = sorted({c for c in codes if c})

    # —— 行业强度(P0-23:`industry_strength_daily` 只读表,不现算自愈)——
    try:
        rows = load_industry_strength(trade_date, db_path=db_path)
        ctx.industry_total = len(rows)
        ctx.industry_rank = {r.industry: int(r.industry_rank) for r in rows}
    except Exception:  # noqa: BLE001
        logger.warning("[tier] 行业强度表读取失败,sector_strength 缺行业侧", exc_info=True)

    # —— 概念板块热榜(既有在线路径,同 `report/intel.py`)——
    try:
        scores = compute_sector_strength(trade_date, parquet_dir=parquet_dir, top_n=DEFAULT_TOP_N)
        if scores:
            rank_by_index = {s.index_code: s.rank for s in scores}
            ctx.concept_total = len(scores)
            membership = cluster_mod.concept_membership_map(parquet_dir)
            for code in wanted:
                ranks = [rank_by_index[i] for i in membership.get(code, ()) if i in rank_by_index]
                if ranks:
                    ctx.concept_rank_by_code[code] = min(ranks)
    except Exception:  # noqa: BLE001
        logger.warning("[tier] 概念板块热榜读取失败,sector_strength 缺概念侧", exc_info=True)

    # —— ④b 行业题材六态(只读表)——
    try:
        rows_stage = stage_mod.load_industry_stage(trade_date, db_path=db_path)
        ctx.stage_of = stage_mod.stage_lookup(rows_stage)
        ctx.stage_available = bool(ctx.stage_of)
    except Exception:  # noqa: BLE001
        logger.warning("[tier] 行业阶段表读取失败,driver_freshness 走中性分", exc_info=True)

    # —— 一字 / 涨停(当日两个分区切片,不扫历史)——
    try:
        limit = get_market_slice(trade_date, table="limit_derived", parquet_dir=parquet_dir)
        daily = get_market_slice(trade_date, table="daily", parquet_dir=parquet_dir)
        if not limit.is_empty():
            hit = limit.filter(pl.col("ts_code").is_in(wanted) & pl.col("is_limit_up"))
            ctx.limit_up = set(hit["ts_code"].to_list())
            ctx.tradability_available = True
            if ctx.limit_up and not daily.is_empty():
                lows = {
                    r["ts_code"]: r["low"]
                    for r in daily.filter(pl.col("ts_code").is_in(list(ctx.limit_up)))
                    .select(["ts_code", "low"]).iter_rows(named=True)
                }
                for r in hit.select(["ts_code", "limit_up_price"]).iter_rows(named=True):
                    low = lows.get(r["ts_code"])
                    price = r["limit_up_price"]
                    # 一字板 = 全天最低价都没低于涨停价 → 根本没有买进的机会。
                    if low is not None and price is not None and float(low) >= float(price) - _EPS:
                        ctx.one_word.add(r["ts_code"])
        elif not daily.is_empty():
            # `limit_derived` 是**稀疏表**(只落有信号的行):当日 `daily` 有数据而
            # 涨跌停表零命中 = 真的一只涨停都没有,不是"读不到"。
            ctx.tradability_available = True
    except Exception:  # noqa: BLE001
        logger.warning("[tier] 涨跌停/行情切片读取失败,tradability 走中性分", exc_info=True)

    return ctx


# ══════════════════════════════════════════════════════════════════════════
# 五维打分(每一维:算得出给分,算不出给中性分 + flag,**永不给 0 冒充**)
# ══════════════════════════════════════════════════════════════════════════

def _rank_to_score(rank: int, total: int) -> float:
    """名次 → [0,1] 分值(第 1 名 1.0,末名 1/total)。`total<=0` 视为算不出。"""
    if total <= 0 or rank <= 0:
        return NEUTRAL_DIM_SCORE
    return max(0.0, min(1.0, 1.0 - (rank - 1) / total))


def _dim_sector_strength(
    industries: Sequence[str], codes: Sequence[str], fctx: TierFeatureContext,
) -> Tuple[float, List[str]]:
    """行业强度与概念热榜**取较大者**。概念侧只做加成不做减分 —— 一只票不在今日
    概念热榜上,不等于它弱,只等于这一路没有证据(「没有」与「没看」)。"""
    parts: List[float] = []
    if fctx.industry_total > 0:
        ranks = [fctx.industry_rank[i] for i in industries if i in fctx.industry_rank]
        if ranks:
            parts.append(_rank_to_score(min(ranks), fctx.industry_total))
    if fctx.concept_total > 0:
        cranks = [fctx.concept_rank_by_code[c] for c in codes if c in fctx.concept_rank_by_code]
        if cranks:
            parts.append(_rank_to_score(min(cranks), fctx.concept_total))
    if not parts:
        return NEUTRAL_DIM_SCORE, [FLAG_SECTOR_MISSING]
    return max(parts), []


def _dim_driver_freshness(
    industries: Sequence[str], fctx: TierFeatureContext, stage_scores: Mapping[str, float],
) -> Tuple[float, List[str]]:
    """六态 → 现役包 `stage_scores` 映射(K7 需求 1b)。**缺行取中性分 + 标
    `stage_missing`,绝不写 0**(0 是 `overheat` 的真实取值)。"""
    if not stage_scores:
        return NEUTRAL_DIM_SCORE, [FLAG_STAGE_SCORES_ABSENT]
    flags: List[str] = []
    vals: List[float] = []
    seen_stage = False
    for ind in industries:
        st = fctx.stage_of.get(ind)
        if st is None:
            continue
        seen_stage = True
        if st in stage_scores:
            vals.append(float(stage_scores[st]))
        elif FLAG_STAGE_UNMAPPED not in flags:
            flags.append(FLAG_STAGE_UNMAPPED)
    if not vals:
        flags.append(FLAG_STAGE_MISSING)
        return NEUTRAL_DIM_SCORE, flags
    if not seen_stage:  # pragma: no cover - 逻辑上 vals 非空即 seen_stage
        flags.append(FLAG_STAGE_MISSING)
    return max(vals), flags


def _dim_leader_clarity(rs_ranks: Sequence[Optional[int]]) -> Tuple[float, List[str]]:
    """簇内 RS20 头名度,**依名次衰减**:`1 / rank`(第 1 名 1.0、第 2 名 0.5、
    第 3 名 0.333…)。取本篮成员里最靠前的那个名次 —— 这一维问的是「这个篮子里
    有没有一只是簇内的头」,不是「平均排第几」。

    衰减函数是**引擎常量**(plan §五 V2-⑥ 原文:"衰减函数是引擎常量,不进包;
    若日后要可配再走加原语流程"):倒数衰减无额外参数、单调、上界 1.0,是 MRR
    的标准形态。⛔ 连板高度不进这里(双尾放大器,归 ⑦-K7 标注)。
    """
    ranks = [int(r) for r in rs_ranks if r is not None and int(r) > 0]
    if not ranks:
        return NEUTRAL_DIM_SCORE, [FLAG_LEADER_MISSING]
    return 1.0 / min(ranks), []


def _dim_tradability(codes: Sequence[str], fctx: TierFeatureContext) -> Tuple[float, List[str]]:
    """一字比例与涨停占比 —— **买不进的涨停不算机会**(蓝图 4.9)。"""
    if not codes:
        return NEUTRAL_DIM_SCORE, [FLAG_TRADABILITY_MISSING]
    if not fctx.tradability_available:
        return NEUTRAL_DIM_SCORE, [FLAG_TRADABILITY_MISSING]
    n = len(codes)
    one_word = sum(1 for c in codes if c in fctx.one_word)
    limit_open = sum(1 for c in codes if c in fctx.limit_up and c not in fctx.one_word)
    penalty = (one_word * ONE_WORD_PENALTY + limit_open * LIMIT_UP_PENALTY) / n
    return max(0.0, min(1.0, 1.0 - penalty)), []


def _dim_card_density(
    k4_tags: Sequence[Optional[str]], *, k4_unavailable: bool,
) -> Tuple[float, List[str]]:
    """红黄牌密度。⑤-b 报了 `k4_unavailable` 时**不能**拿"零命中"冒充"干净"
    —— 那正是「没有」与「没看」混为一谈,取中性分 + 如实标。"""
    if k4_unavailable:
        return NEUTRAL_DIM_SCORE, [FLAG_CARD_DENSITY_MISSING]
    if not k4_tags:
        return NEUTRAL_DIM_SCORE, [FLAG_CARD_DENSITY_MISSING]
    hits = sum(1 for t in k4_tags if t)
    return max(0.0, min(1.0, 1.0 - hits / len(k4_tags))), []


def build_tier_row(
    basket: Any, fctx: TierFeatureContext, *,
    stage_scores: Mapping[str, float], k4_unavailable: bool = False,
) -> Tuple[Dict[str, Any], List[str]]:
    """把一个 `BasketCandidate` 摊成**特征行**,返回 `(row, flags)`。

    ⚠ 行里**同时装着** `_TIER_SCORE_INPUTS`(五维分值)与 `_LLM_PROVENANCE_KEYS`
    (LLM 产出的名称/驱动/理由/角色/证据)。后者存在的唯一目的是让白名单单测能在
    运行期证明 `mech_score()` 一个都没读 —— 这是 plan 要求的"运行期访问锁",不是
    冗余数据。
    """
    codes = [m.ts_code for m in basket.members]
    industries = sorted({m.industry for m in basket.members if getattr(m, "industry", None)})
    flags: List[str] = []

    sector, f1 = _dim_sector_strength(industries, codes, fctx)
    fresh, f2 = _dim_driver_freshness(industries, fctx, stage_scores)
    leader, f3 = _dim_leader_clarity([getattr(m, "rs_rank", None) for m in basket.members])
    trad, f4 = _dim_tradability(codes, fctx)
    card, f5 = _dim_card_density(
        [getattr(m, "k4_tag", None) for m in basket.members], k4_unavailable=k4_unavailable,
    )
    for f in (f1, f2, f3, f4, f5):
        flags.extend(f)

    row: Dict[str, Any] = {
        DIM_SECTOR_STRENGTH: sector,
        DIM_DRIVER_FRESHNESS: fresh,
        DIM_LEADER_CLARITY: leader,
        DIM_TRADABILITY: trad,
        DIM_CARD_DENSITY: card,
        # —— 以下全是 LLM 产出,**机械分一个都不许读**(访问锁的证伪对象)——
        "llm_name": basket.name,
        "llm_driver": basket.driver,
        "llm_why_now": basket.why_now,
        "llm_member_reasons": tuple(m.reason for m in basket.members),
        "llm_roles": tuple(m.role_llm for m in basket.members),
        "llm_evidence_claims": tuple(e.claim for e in basket.evidence),
    }
    return row, flags


# ══════════════════════════════════════════════════════════════════════════
# 机械分与定档
# ══════════════════════════════════════════════════════════════════════════

def resolve_weights(pack: Optional[Pack]) -> Dict[str, float]:
    """从**现役包**读五维权重。**缺维度 → fail loud,不静默补默认值**(plan §五
    V2-⑥:「权重是包里的数,代码里不许有第二份」)。多出引擎不认识的维度同样
    fail loud —— 静默忽略等于让包以为自己配了个生效的旋钮。

    返回**归一化**后的权重(除以权重和):机械分的尺度因此恒在 [0,1],才谈得上和
    `TIER1_MIN_SCORE`/`TIER2_MIN_SCORE` 这两条档位线比较。K4-pack / K7-pack 的
    权重本来就和为 1,归一化对它们是恒等变换。
    """
    if pack is None:
        raise ValueError("Tier 定档需要现役策略包(权重只住包里),当前无现役包")
    raw = pack.tier_weights()
    missing = sorted(_TIER_SCORE_INPUTS - set(raw))
    unknown = sorted(set(raw) - _TIER_SCORE_INPUTS)
    if missing or unknown:
        raise ValueError(
            f"策略包 {pack.pack_version} 的 config.tier.weights 与引擎五维不符:"
            f"缺 {missing};引擎不认识 {unknown}(⑥ 不臆造默认权重)"
        )
    total = sum(float(v) for v in raw.values())
    if total <= 0:
        raise ValueError(f"策略包 {pack.pack_version} 的 tier.weights 权重和 <= 0,无法定档")
    return {k: float(raw[k]) / total for k in sorted(_TIER_SCORE_INPUTS)}


def resolve_quality_lines(pack: Optional[Pack]) -> Dict[str, float]:
    """从**现役包**读三档质量线(V2-⑥-b-A 裁定)。**逐键独立回退引擎默认**——
    与 `resolve_weights()` 的 fail-loud 姿势刻意不同,两条理由各自写死、别去
    "统一"成同一种:`weights` 每个包 schema 都必须给全(缺了就是包坏了),
    `quality_lines` 缺(整段缺,或只缺其中一两个子键)一律回退
    `TIER1_MIN_SCORE`/`TIER2_MIN_SCORE`/`TIER3_MIN_SCORE` —— 因为 `K4-pack-v1`
    不重发版、是 ⑯-E 的回滚锚,不给回退路径就等于把回滚锚作废。

    无现役包 → 抛(同 `resolve_weights`:定档必须有包,哪怕这次只是为了取
    质量线的回退默认,也不该在没有包的情况下悄悄定档)。"""
    if pack is None:
        raise ValueError("Tier 定档需要现役策略包(质量线只住包里或回退引擎默认),当前无现役包")
    raw = pack.tier_quality_lines()
    return {
        "tier1_min": float(raw.get("tier1_min", TIER1_MIN_SCORE)),
        "tier2_min": float(raw.get("tier2_min", TIER2_MIN_SCORE)),
        "tier3_min": float(raw.get("tier3_min", TIER3_MIN_SCORE)),
    }


def mech_score(row: Mapping[str, Any], weights: Mapping[str, float]) -> float:
    """加权机械分。**只读 `_TIER_SCORE_INPUTS` 白名单五键**(运行期访问锁单测锁死)
    —— `row` 里同时存在的 LLM 产出字段一个都不许碰(§2.8-C 第 1 条)。"""
    return sum(float(weights[dim]) * float(row[dim]) for dim in sorted(weights))


def neutral_filled_weight(flags: Sequence[str], weights: Mapping[str, float]) -> float:
    """V2-⑥-b-D 新增审计字段:本篮加权和中,由**中性填充**贡献的权重合计。

    **为什么要它**:一个三维缺数据的篮子,靠三个 `NEUTRAL_DIM_SCORE=0.5` 的
    填充也可能压过 `tier1_min` —— 那就成了「因为不知道所以进 T1」。这个数字
    让"这份机械分里有多少是猜的"变得可审计。

    **只认 flags,不认数值**——`leader_clarity` 的 `1/rank` 在 `rank=2` 时恰好
    也等于 `NEUTRAL_DIM_SCORE`(真实第二名与"没数据"数值撞车,见模块头「V2-⑥-b
    追加」旁的设计判断②),拿数值反推"是不是中性填充"在这里会判错。每个维度
    是否被中性填充,只看它自己专属的 flag 是否出现在 `flags` 里
    (`_DIM_MISSING_FLAGS`,与 `build_tier_row` 里各 `_dim_*` 函数的返回路径
    一一对应——每个 `_dim_*` 只有走"缺数据"分支才会同时"返回 `NEUTRAL_DIM_SCORE`
    并挂上这个 flag",两者绑在一起,不会有 flag 挂了但值不是中性分的情况)。

    **本子项只记录 + 披露,⛔ 先不设闸**——不凭空造一个"缺数据超过多少就降档"
    的阈值,承裁定「先观测再立规」;要不要据此降档进 §七 P3-33,等 ⑨ 评价引擎
    攒够样本再说。
    """
    flag_set = set(flags)
    return sum(
        float(weights[dim]) for dim, missing_flags in _DIM_MISSING_FLAGS.items()
        if missing_flags & flag_set
    )


def _eligible_tier(score: float, quality_lines: Mapping[str, float]) -> Optional[int]:
    """score 够哪一档的线,就"想要"哪一档(是否真能进去还要看容量,见
    `assign_tiers`)。**连 T3 线都够不到 → `None`**(V2-⑥-b-B:T3 也有下限,
    不是"只要不够 T1/T2 就自动落进 T3")。"""
    if score >= quality_lines["tier1_min"] - _EPS:
        return 1
    if score >= quality_lines["tier2_min"] - _EPS:
        return 2
    if score >= quality_lines["tier3_min"] - _EPS:
        return 3
    return None


def assign_tiers(
    scored: Sequence[Tuple[str, float]],
    quality_lines: Mapping[str, float] = _DEFAULT_QUALITY_LINES,
) -> Tuple[Dict[str, Tuple[int, int]], List[DroppedBasket]]:
    """机械定档。输入 `[(basket_key, mech_score), ...]`,输出
    `({basket_key: (tier, rank_mech)}, dropped)`。

    `quality_lines`:`{"tier1_min", "tier2_min", "tier3_min"}` 三键(默认取
    `_DEFAULT_QUALITY_LINES` = 引擎缺省值;`score_and_tier()` 会传现役包
    `resolve_quality_lines()` 解出来的那份)。

    **确定性铁律**:先按 `(机械分降序, basket_key 升序)` 排定 —— 分数并列时靠
    `basket_key`(crc32 十六进制,跨进程可复现)打破,**不靠行序**(CLAUDE.md:
    `rank(method="ordinal")` 的并列由行序打散 = 不确定性)。

    **上限非配额**:每档先看质量线(`_eligible_tier`),够格才进那一档;够格但该档
    已满 → **向下顺延**到还有位子的档(不是把它挤进去,也不是丢掉);三档都满 →
    今日不定档(`DROP_CAPACITY_OVERFLOW`,「分数够、位置满」)。**允许任何一档
    为空**(V2-⑥-b-B):没有篮子够某档线时,那一档就是空的,不许拿别档凑数。
    **连 T3 线都够不到 → 直接丢弃**(`DROP_BELOW_QUALITY_LINE`,「分数不够」),
    连容量判断都不参与 —— 这与容量溢出是两种相反的市场结论,⛔ 不许合并
    (V2-⑥-b-C)。
    """
    order = sorted(scored, key=lambda kv: (-kv[1], kv[0]))
    used = {t: 0 for t in TIERS}
    counts = {t: 0 for t in TIERS}
    out: Dict[str, Tuple[int, int]] = {}
    dropped: List[DroppedBasket] = []
    for key, score in order:
        want = _eligible_tier(score, quality_lines)
        if want is None:
            dropped.append(DroppedBasket(basket_key=key, reason=DROP_BELOW_QUALITY_LINE,
                                         mech_score=score))
            continue
        placed = False
        for t in TIERS:
            if t < want:
                continue
            if used[t] < TIER_CAPACITY[t]:
                used[t] += 1
                counts[t] += 1
                out[key] = (t, counts[t])
                placed = True
                break
        if not placed:
            dropped.append(DroppedBasket(basket_key=key, reason=DROP_CAPACITY_OVERFLOW,
                                         mech_score=score))
    return out, dropped


# ══════════════════════════════════════════════════════════════════════════
# LLM 同档微调(**只能改档内序、留痕、不得跨档**)
# ══════════════════════════════════════════════════════════════════════════

def build_tier_rank_context(
    by_tier: Mapping[int, Sequence[TierDecision]], names: Mapping[str, str], ref_date: date,
) -> str:
    """微调段的 user 消息 = 日期锚(`prompt_context` 唯一实现)+ 逐档的机械序与
    五维分项。**不给"可以换档"的任何暗示**,并且把机械分项摊开 —— 让模型知道机械
    侧凭什么这么排,它才谈得上"补充机械看不见的东西"。"""
    lines = [date_anchor_line(ref_date=ref_date, name_tomorrow=True), ""]
    for t in TIERS:
        items = list(by_tier.get(t, ()))[:MAX_BASKETS_IN_RANK_CONTEXT]
        if not items:
            lines.append(f"── T{t}(容量上限 {TIER_CAPACITY[t]}):**今日为空**")
            lines.append("")
            continue
        lines.append(f"── T{t}(容量上限 {TIER_CAPACITY[t]},共 {len(items)} 个;"
                     f"**只能在本档内部调序**)")
        for d in items:
            dims = d.breakdown.get("dims", {})
            dim_txt = "、".join(f"{k} {float(v):.2f}" for k, v in sorted(dims.items()))
            lines.append(
                f"   {d.rank_mech}. {names.get(d.basket_key, d.basket_key)}"
                f"|basket_key {d.basket_key}|机械分 {d.mech_score:.3f}"
            )
            lines.append(f"      五维:{dim_txt}")
            flags = d.breakdown.get("flags", [])
            if flags:
                lines.append(f"      数据缺口(该维已取中性分):{'、'.join(flags)}")
        lines.append("")
    lines.append("需要调整就给 adjustments 数组,不需要就交空数组。**跨档提案会被系统丢弃。**")
    return "\n".join(lines)


def run_tier_rank(
    by_tier: Mapping[int, Sequence[TierDecision]], names: Mapping[str, str], ref_date: date,
    *, provider: Optional[LLMProvider], ledger: BudgetLedger, transport: Optional[Any] = None,
) -> Tuple[str, Optional[List[Dict[str, Any]]], str]:
    """微调段一次调用,返回 `(叙述, 提案列表 or None, 段状态)`。

    **`None` 与 `[]` 语义不同**:`None` = 微调段缺席(机械序原样用,如实披露);
    `[]` = 模型跑了、明确说"不用调" —— 那是**完全正常的输出**,不是降级。

    预算走**推理账**(`LEDGER_REASON`,② 的三本账之一;本段不联网 —— 档内次序是
    对已有机械依据的再判断,不需要新证据)。
    """
    if provider is None:
        return "", None, LLM_NO_PROVIDER
    if ledger.exhausted(LEDGER_REASON):
        return "", None, LLM_BUDGET_EXHAUSTED

    messages = [
        ChatMessage(role="system", content=TIER_RANK_SYSTEM_PROMPT),
        ChatMessage(role="user", content=build_tier_rank_context(by_tier, names, ref_date)),
    ]
    started = time.monotonic()
    try:
        result = provider.chat(messages, enable_search=False, transport=transport)
    except Exception as exc:  # noqa: BLE001
        ledger.spend(LEDGER_REASON, time.monotonic() - started)
        logger.warning("[tier] 同档微调调用抛异常,机械序原样用", exc_info=True)
        return "", None, f"{LLM_CALL_FAILED}:{type(exc).__name__}"
    ledger.spend(LEDGER_REASON, time.monotonic() - started)

    if not getattr(result, "ok", False):
        return "", None, f"{LLM_CALL_FAILED}:{getattr(result, 'reason', '')}"

    # **先剥 JSON 再谈解析**(v1.5.1 标签劫持案:机器可读标签后面还挂内容会架空
    # last-match 锚点)。本块**不复用** `judge._parse_verdict` —— 输出里根本没有
    # "结论:"标签,硬套那套锚点就是给自己埋雷。
    narrative, payload = split_narrative_and_reference_json(result.content or "")
    if not isinstance(payload, dict):
        logger.warning("[tier] 同档微调输出解不出 JSON 块,机械序原样用")
        return narrative, None, LLM_PARSE_FAILED
    raw = payload.get("adjustments")
    if not isinstance(raw, list):
        logger.warning("[tier] 同档微调 JSON 缺 adjustments 数组,机械序原样用")
        return narrative, None, LLM_PARSE_FAILED
    return narrative, raw, LLM_OK


def apply_llm_adjustments(
    by_tier: Mapping[int, Sequence[TierDecision]], proposals: Sequence[Any],
) -> Tuple[List[TierDecision], List[RejectedAdjustment]]:
    """把 LLM 提案**机械校验**后落到档内次序上。

    **跨档一律拒收**(plan §五 V2-⑥ 定死 + 守门单测):提案里的 `tier` 与机械定档
    不符 → 整条丢弃 + WARNING,**绝不执行**。其余四类拒收(未知篮子 / 名次非法 /
    抢位 / 形状不对)同样逐条留痕,语义不合并。

    未被任何有效提案指定名次的篮子,**按机械序**依次填进剩下的空位 —— 于是"LLM
    只动了一个篮子"时其余篮子的相对次序保持不变,可复现。
    """
    tier_of = {d.basket_key: d.tier for ds in by_tier.values() for d in ds}
    rejected: List[RejectedAdjustment] = []
    wanted: Dict[int, Dict[int, str]] = {t: {} for t in TIERS}
    reason_of: Dict[str, str] = {}

    for p in proposals:
        if not isinstance(p, dict):
            rejected.append(RejectedAdjustment(REJECT_MALFORMED, f"提案不是对象:{p!r}"))
            continue
        key = p.get("basket_key")
        if not isinstance(key, str) or key not in tier_of:
            rejected.append(RejectedAdjustment(REJECT_UNKNOWN_BASKET,
                                               f"今日没有这个篮子:{key!r}", dict(p)))
            continue
        mech_tier = tier_of[key]
        raw_tier = p.get("tier")
        if raw_tier is not None:
            try:
                proposed_tier = int(raw_tier)
            except (TypeError, ValueError):
                rejected.append(RejectedAdjustment(REJECT_MALFORMED,
                                                   f"{key} 的 tier 不是整数:{raw_tier!r}", dict(p)))
                continue
            if proposed_tier != mech_tier:
                logger.warning(
                    "[tier] 拒收跨档提案:%s 机械定档 T%d,LLM 想挪到 T%d —— "
                    "LLM 只能在同档内微调(§2.8-C 第 1 条)。",
                    key, mech_tier, proposed_tier,
                )
                rejected.append(RejectedAdjustment(
                    REJECT_CROSS_TIER, f"{key}:T{mech_tier} → T{proposed_tier}", dict(p)))
                continue
        n = len(by_tier.get(mech_tier, ()))
        raw_rank = p.get("rank_in_tier")
        if isinstance(raw_rank, bool) or not isinstance(raw_rank, int) or not (1 <= raw_rank <= n):
            rejected.append(RejectedAdjustment(
                REJECT_BAD_RANK, f"{key}:名次 {raw_rank!r} 不在 1..{n}", dict(p)))
            continue
        if key in wanted[mech_tier].values():
            # 同一个篮子被提了两次(第二条要另一个名次)。放行会让它同时占两个坑,
            # 剩余篮子填空位时直接 `StopIteration` —— 语义上也说不通,**第二条丢弃**。
            rejected.append(RejectedAdjustment(
                REJECT_DUPLICATE_KEY, f"{key}:同一篮子被提了两次,后一条丢弃", dict(p)))
            continue
        if raw_rank in wanted[mech_tier]:
            rejected.append(RejectedAdjustment(
                REJECT_SLOT_TAKEN,
                f"{key}:T{mech_tier} 第 {raw_rank} 位已被 {wanted[mech_tier][raw_rank]} 占用",
                dict(p)))
            continue
        wanted[mech_tier][raw_rank] = key
        reason = p.get("reason")
        if isinstance(reason, str) and reason.strip():
            reason_of[key] = reason.strip()

    out: List[TierDecision] = []
    for t in TIERS:
        items = sorted(by_tier.get(t, ()), key=lambda d: d.rank_mech)
        slots = wanted[t]
        pinned = set(slots.values())
        rest = [d for d in items if d.basket_key not in pinned]
        final: List[str] = []
        it = iter(rest)
        for slot in range(1, len(items) + 1):
            if slot in slots:
                final.append(slots[slot])
            else:
                final.append(next(it).basket_key)
        by_key = {d.basket_key: d for d in items}
        for idx, key in enumerate(final, start=1):
            d = by_key[key]
            out.append(TierDecision(
                basket_key=d.basket_key, tier=d.tier, mech_score=d.mech_score,
                breakdown=d.breakdown, rank_mech=d.rank_mech, rank_in_tier=idx,
                llm_rank_delta=d.rank_mech - idx, llm_reason=reason_of.get(key),
            ))
    return out, rejected


# ══════════════════════════════════════════════════════════════════════════
# 编排入口
# ══════════════════════════════════════════════════════════════════════════

def score_and_tier(
    result: Any,
    trade_date: date,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    pack: Optional[Pack] = None,
    provider: Any = None,
    use_llm: bool = False,
    ledger: Optional[BudgetLedger] = None,
    transport: Optional[Any] = None,
) -> TierResult:
    """⑥ 唯一编排入口:`AggregateResult` → 机械分 → 定档 → (可选)LLM 同档微调。

    `pack`:默认取现役包(权重与 `stage_scores` 都只住包里)。**无现役包 → 抛**
    —— 定档必须有权重,臆造一份默认权重等于在代码里藏第二套策略。

    `use_llm`:默认 **False**(纯机械,零 LLM 调用)。⑭ 的管线会显式打开它并传
    `provider`/`ledger`;单测与本地冒烟保持默认即可完全离线。传了 `provider` 但
    `use_llm=False` 时**不会**调用 —— 两个开关刻意分开,免得"注入个桩"就意外走上
    LLM 路径。

    **本函数不落库**(落库走 `save_tier_result()`,plan 的事务 1)。
    """
    trade_date_s = trade_date.strftime("%Y%m%d")
    if result.trade_date and result.trade_date != trade_date_s:
        raise ValueError(
            f"score_and_tier:聚合结果是 {result.trade_date} 的,却拿 {trade_date_s} 来定档 —— "
            f"跨日定档会把历史篮子按今天的面板重打分,拒绝执行"
        )

    notes: List[str] = []
    pack = pack if pack is not None else get_active_pack(db_path)
    weights = resolve_weights(pack)
    quality_lines = resolve_quality_lines(pack)
    stage_scores = pack.tier_stage_scores()
    if not stage_scores:
        notes.append(FLAG_STAGE_SCORES_ABSENT)
    pack_version = pack.pack_version

    if not result.baskets:
        return TierResult(trade_date=trade_date_s, llm_stage=LLM_NOT_NEEDED,
                          pack_version=pack_version, weights=weights,
                          quality_lines=quality_lines,
                          notes=tuple(notes + ["no_baskets"]))

    k4_unavailable = "k4_unavailable" in tuple(getattr(result, "notes", ()) or ())
    codes = sorted({m.ts_code for b in result.baskets for m in b.members})
    fctx = build_feature_context(trade_date, codes, db_path=db_path, parquet_dir=parquet_dir)

    scored: List[Tuple[str, float]] = []
    breakdowns: Dict[str, Dict[str, Any]] = {}
    for b in result.baskets:
        row, flags = build_tier_row(b, fctx, stage_scores=stage_scores,
                                    k4_unavailable=k4_unavailable)
        score = mech_score(row, weights)
        scored.append((b.basket_key, score))
        breakdowns[b.basket_key] = {
            "dims": {d: round(float(row[d]), 6) for d in sorted(_TIER_SCORE_INPUTS)},
            "weights": {d: round(float(weights[d]), 6) for d in sorted(weights)},
            "weights_raw": {k: float(v) for k, v in sorted(pack.tier_weights().items())},
            "contrib": {
                d: round(float(weights[d]) * float(row[d]), 6) for d in sorted(weights)
            },
            "flags": sorted(set(flags)),
            # V2-⑥-b-D:中性填充贡献的权重合计——只认 flags,不认数值(见
            # `neutral_filled_weight()` docstring 的 rank=2 撞车说明)。
            "neutral_filled_weight": round(neutral_filled_weight(flags, weights), 6),
            "score": round(float(score), 6),
            "pack_version": pack_version,
            "engine_api_version": engine_api.ENGINE_API_VERSION,
        }

    score_by_key = dict(scored)
    placement, dropped = assign_tiers(scored, quality_lines=quality_lines)
    if dropped:
        # V2-⑥-b-C:两种"没进来"是相反的市场结论,notes/日志都不许把它们揉成
        # 一句话——逐原因码分别计数披露。
        drop_counts: Dict[str, int] = {}
        for d in dropped:
            drop_counts[d.reason] = drop_counts.get(d.reason, 0) + 1
        for reason in sorted(drop_counts):
            notes.append(f"{reason}:{drop_counts[reason]}")
        logger.warning(
            "[tier] %s 有 %d 个篮子今日不定档(不落库),原因分布 %s,篮子:%s",
            trade_date_s, len(dropped), drop_counts, [d.basket_key for d in dropped],
        )

    mech_decisions = [
        TierDecision(
            basket_key=key, tier=placement[key][0],
            mech_score=float(score_by_key[key]), breakdown=breakdowns[key],
            rank_mech=placement[key][1], rank_in_tier=placement[key][1],
        )
        for key, _score in scored if key in placement
    ]
    by_tier: Dict[int, List[TierDecision]] = {t: [] for t in TIERS}
    for d in mech_decisions:
        by_tier[d.tier].append(d)
    for t in TIERS:
        by_tier[t].sort(key=lambda d: d.rank_mech)

    llm_stage = LLM_NOT_NEEDED
    narrative = ""
    rejected: List[RejectedAdjustment] = []
    decisions: List[TierDecision] = sorted(mech_decisions, key=lambda d: (d.tier, d.rank_in_tier))

    adjustable = any(len(v) > 1 for v in by_tier.values())
    if use_llm and adjustable:
        ledger = ledger or BudgetLedger()
        if provider is None:
            try:
                provider = get_provider(TASK_TIER_RANK, db_path=db_path)
            except Exception:  # noqa: BLE001
                logger.warning("[tier] 取 %s 的 provider 失败,微调段按缺席处理",
                               TASK_TIER_RANK, exc_info=True)
                provider = None
        names = {b.basket_key: b.name for b in result.baskets}
        narrative, proposals, llm_stage = run_tier_rank(
            by_tier, names, trade_date, provider=provider, ledger=ledger, transport=transport,
        )
        if proposals is None:
            notes.append(f"tier_rank_unadjusted:{llm_stage}")
        else:
            adjusted, rejected = apply_llm_adjustments(by_tier, proposals)
            decisions = sorted(adjusted, key=lambda d: (d.tier, d.rank_in_tier))
            if rejected:
                notes.append(f"tier_rank_rejected:{len(rejected)}")
    elif use_llm:
        notes.append("tier_rank_not_needed")

    return TierResult(
        trade_date=trade_date_s, decisions=tuple(decisions), dropped=tuple(dropped),
        rejected_adjustments=tuple(rejected), llm_stage=llm_stage, llm_narrative=narrative,
        pack_version=pack_version, weights=weights, quality_lines=quality_lines,
        notes=tuple(notes),
    )


def save_tier_result(
    agg_result: Any, tier_result: TierResult, *,
    db_path: Optional[Path] = None, via: str = "auto",
) -> Dict[str, Any]:
    """【事务 1】把定档结果与篮子三表**一次事务**落地(实现在
    `basket_store.save_tier_decision`,本函数只负责把 `TierDecision` 摊成留痕行)。

    ⚠ **只落定了档的篮子**:容量溢出的篮子(`tier_result.dropped`)今日不定档,
    `baskets.tier` 是 `NOT NULL`,给它们臆造一个 tier 才是错的。溢出留痕在
    `TierResult.dropped` 里,由报告层如实披露。
    """
    tiers = tier_result.tier_by_basket_key()
    history = {
        d.basket_key: {
            "basket_key": d.basket_key, "tier": d.tier, "mech_score": d.mech_score,
            "mech_breakdown": d.breakdown, "rank_in_tier": d.rank_in_tier,
            "rank_mech": d.rank_mech, "llm_rank_delta": d.llm_rank_delta,
            "llm_reason": d.llm_reason, "pack_version": tier_result.pack_version,
        }
        for d in tier_result.decisions
    }
    kept = [b for b in agg_result.baskets if b.basket_key in tiers]
    if len(kept) != len(agg_result.baskets):
        subset = _subset_result(agg_result, kept)
    else:
        subset = agg_result
    return basket_store.save_tier_decision(
        subset, tier_by_basket_key=tiers, tier_history_by_basket_key=history,
        db_path=db_path, via=via,
    )


def _subset_result(agg_result: Any, kept: Sequence[Any]) -> Any:
    """容量溢出时只落"定了档的那些篮子"。用 `dataclasses.replace` 而不是改原对象
    —— `AggregateResult` 是 frozen 快照,调用方手里那份不该被本层动过。"""
    return replace(agg_result, baskets=tuple(kept))


__all__ = [
    "DIM_SECTOR_STRENGTH",
    "DIM_DRIVER_FRESHNESS",
    "DIM_LEADER_CLARITY",
    "DIM_TRADABILITY",
    "DIM_CARD_DENSITY",
    "TIER_CAPACITY",
    "TIERS",
    "TIER1_MIN_SCORE",
    "TIER2_MIN_SCORE",
    "TIER3_MIN_SCORE",
    "NEUTRAL_DIM_SCORE",
    "LLM_OK",
    "LLM_NO_PROVIDER",
    "LLM_CALL_FAILED",
    "LLM_BUDGET_EXHAUSTED",
    "LLM_PARSE_FAILED",
    "LLM_NOT_NEEDED",
    "REJECT_CROSS_TIER",
    "REJECT_UNKNOWN_BASKET",
    "REJECT_BAD_RANK",
    "REJECT_SLOT_TAKEN",
    "REJECT_DUPLICATE_KEY",
    "REJECT_MALFORMED",
    "DROP_CAPACITY_OVERFLOW",
    "DROP_BELOW_QUALITY_LINE",
    "FLAG_SECTOR_MISSING",
    "FLAG_STAGE_MISSING",
    "FLAG_STAGE_SCORES_ABSENT",
    "FLAG_STAGE_UNMAPPED",
    "FLAG_LEADER_MISSING",
    "FLAG_TRADABILITY_MISSING",
    "FLAG_CARD_DENSITY_MISSING",
    "TIER_RANK_SYSTEM_PROMPT",
    "TierFeatureContext",
    "TierDecision",
    "DroppedBasket",
    "RejectedAdjustment",
    "TierResult",
    "build_feature_context",
    "build_tier_row",
    "resolve_weights",
    "resolve_quality_lines",
    "mech_score",
    "neutral_filled_weight",
    "assign_tiers",
    "build_tier_rank_context",
    "run_tier_rank",
    "apply_llm_adjustments",
    "score_and_tier",
    "save_tier_result",
]
