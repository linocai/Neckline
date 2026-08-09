"""③Tier 分层引擎(plan §五 V2-⑥;V2.1-② 起只剩两档;**V2.2-③-D 起定档改门槛制**)。
把 ⑤ 产出的**篮子候选**定档 T1/T2。

**目标一句话**:定档**全机械、可完整复现**;LLM 只能在**同档内**微调次序并留痕,
**不得跨档**(§2.8-C 第 1 条 —— 「LLM 不进排序」精确化 = 不进**机械分**)。

════════════════════════════════════════════════════════════════════════════
**V2.2-③-D 门槛制(K8 §八,🔴 换心脏;冷启动先读这一节)**

- **定档的闸自此是六道关口**(`selection/gates.py`,唯一实现),不再是机械分:
    · **T1** = **机械关 ①③ pass + 证据关 ②④⑤⑥ 全 pass(含位置关 `position_verdict=ok`)**
      且 `market_regime` 可得 且 **次日交易预案四件套齐**(四件套在 ⑦ 卡生成后由
      `enforce_plan_completeness()` 补验 —— 缺任一 → 降 T2,⛔ 不是拦截);上限 ≤2,允许为空。
    · **T2** = 机械关全过、证据关(**含位置关**)至多
      `tier_evidence.t2.max_evidence_degrades`(引擎包)处 degrade;上限 ≤5。
    · 达不到 T2 → **退出正式候选**,进 `TierResult.dropped` → 报告 ③b
      (名 / 分 / 卡在哪一关、差多少 / 原因码;**票永远不会从报告里消失**,§2.9-C-2)。
  🔴 **2026-08-09 用户裁定 #11**:位置关由机械关改判为**证据关**(判定交 LLM、只降级
  不除名),**T1 不再要求任何机械态枚举** —— 原文那套「T1 要 `liftoff_confirmed`」
  整体作废,⛔ 别照原文改回:那正是把 T1 掐成近乎不可达的那一条(实测全市场当日
  `liftoff_confirmed` 仅 1~2 / 5526、14 个 D0 回放零 T1)。位置 `unfit` → 出局码
  `DROP_POSITION_UNFIT`(与 `DROP_EVIDENCE_DEGRADED_OUT` **不合并**)。
- **机械分五维 / `_TIER_SCORE_INPUTS` 白名单锁 / `tier_history.mech_breakdown` /
  V2.1-④ 百分制打分卡 —— 全部原样保留,⛔ 一个都不许删**(plan ③-D 原文):它们
  **不再是定档的闸**,降级为「档内排序 + 展示标度」。删了会连带作废百分制卡与
  ⑨ 归因的一整条标度。
- **`quality_lines` 降级为档内排序的辅助下限**(§七 P3-33 主体随之作废):不再拦
  任何篮子进出任何档(否则「tier2_min 取多少」仍是判定正确性问题,P3-33 就死不掉),
  只在 `mech_breakdown_json.below_tier_line` 上做**展示标度**(分低于本档辅助线的
  篮子被标出来给人看)。`DROP_BELOW_QUALITY_LINE` 码保留(历史归因读回 + 展示文案),
  **新运行不再产生它**。
- **容量 T1≤2 / T2≤5 / 合计 ≤7:V2.1-② 已改到位,本块零改动**(plan ③-D 如实登记)。
- **缺 `market_regime` 行 = 市场关不拦,但该票不得进 T1**(② 已定;gates.py 的
  `blocks_t1` 统一姿势把它推广到一切「判定输入取不到」的关口)。
════════════════════════════════════════════════════════════════════════════

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

**容量(V2.1-② 起两档)**:T1 ≤ 2 / T2 ≤ 5,**全部是上限非配额**(市场混沌时
不许凑数,允许**任何一档**为空——V2-⑥-b-B 纠正:档位无下限时容量会退化成事实
上的配额,这正是 V1「每天硬凑 20 只候选」的病)。落地 = 每档一道**质量线**(机械
分下限):够不到某档线就进不了那一档,哪怕那一档空着;连 T2 线都够不到 →
**当天不进任何档**(`DROP_BELOW_QUALITY_LINE`,与容量溢出的
`DROP_CAPACITY_OVERFLOW` 是两种不同的"没进来",不许合并,见 ⑥-b-C)。

**V2.1-② T3 退役(2026-08-07 用户裁定「彻底删除,不留影子档」)**:`T3 ≤ 10` 与
`TIER3_MIN_SCORE` 一并删除,代价(防错杀对照消失、Tier 单调性检验降为两档)已
当面告知并接受。⚠ **只收窄写侧,读侧一律宽容** —— `tier_history` / `baskets` /
`basket_review_daily` 里的历史 tier=3 行照常读回、照常渲染、照常进 ⑨ 评价引擎的
归因(`eval/metrics.py::tier_monotonicity` 按**数据中实际出现的档位**算),
⛔ 不许把历史 T3 样本丢掉或并进 T2(那是伪造归因)。

**V2-⑥-b 追加(2026-08-02 planner 裁定)**:档位质量线的**权威**从"引擎常量"
移到"现役包" `config.tier.quality_lines`(与 `weights` 同标度,换权重会静默
改变 T1 选择性,两个数必须住在一起才谈得上一起校准)。`TIER1_MIN_SCORE` /
`TIER2_MIN_SCORE` 两个模块常量**降级为「包未给 `quality_lines` 时的缺省回退
值」**,不再是权威;读取一律经 `resolve_quality_lines()`,**不直接读模块常量**
(K4-pack-v1 没有这个键,回退到这两个数正是它作为回滚锚必须保持的行为)。

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
from neckline.selection import gates as gates_mod
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
# ⚠ **V2.1-② 起两档**(T3 彻底退役):⛔ 别往这两个字典里加回 `3` —— 反向守门
# `tests/test_selection_tier.py` 会红。历史 tier=3 数据的读回不经过这里(读侧
# 一律按数据实际出现的档位构造,见模块头「T3 退役」节)。
TIER_CAPACITY: Dict[int, int] = {1: 2, 2: 5}
TIERS: Tuple[int, ...] = (1, 2)

# 每档的**质量线**(机械分下限)。质量线的职责是**防止把平庸篮子抬进任何一档**
# ——档位有容量而无下限时,容量会退化成事实上的配额("市场混沌时不许凑数")。
#
# ⚠ **以下两个数是「包未给 `config.tier.quality_lines` 时的缺省回退值」,
# 不再是权威**(V2-⑥-b-A planner 裁定:质量线归属改判"进包",理由是它与五维
# 权重同标度、换权重会静默改变 T1 选择性,两者必须住在一起才能一起校准)。
# 读取一律经 `resolve_quality_lines()`,业务代码不直接读这两个模块常量。
# **数值本身仍是临时工程默认**(同 ⑤-c `MIN_LIFT_SAMPLE_SIZE=5` 的处置姿势,
# 未获证据支持):五维各自归一到 [0,1]、权重归一后加权和亦在 [0,1],`0.5` 是
# "整体中性"的自然基准 —— T1 线取 `0.60`(明显好于中性)、T2 线取 `0.40`
# (不明显差于中性)。前向校准走 ⑨ 评价引擎周报 + 进化门禁(= 换包),⛔ 不许
# 顺手改这里的数、也不许顺手改包里的数(§七 P3-33 挂账)。
TIER1_MIN_SCORE = 0.60
TIER2_MIN_SCORE = 0.40

# `assign_tiers()` 的 `quality_lines` 形参默认值——保证既有调用方(测试与任何
# 未来的直接调用)不传这个参数时行为不变。两键只读不改,共享同一个字典
# 对象是安全的。
_DEFAULT_QUALITY_LINES: Dict[str, float] = {
    "tier1_min": TIER1_MIN_SCORE, "tier2_min": TIER2_MIN_SCORE,
}

# **已退役的质量线子键**(V2.1-②)。包里出现它 → `resolve_quality_lines()` 打一行
# WARNING 并忽略,⛔ 不静默(静默忽略等于让包以为自己配了个生效的旋钮);校验侧
# 仍**受理**它,理由见 `pack._RETIRED_QUALITY_LINE_KEYS`(回滚锚不许作废)。
_RETIRED_QUALITY_LINE_KEYS: Tuple[str, ...] = ("tier3_min",)

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

DROP_CAPACITY_OVERFLOW = "capacity_overflow"       # 关口过了、位置满 → 机会多到装不下
# ⚠ **码字符串一字不改**(V2.1-②):⑨ 评价引擎按原因码归因,改码 = 历史归因断线。
# **V2.2-③-D 起为历史码**:门槛制下质量线不再拦档(见模块头),新运行不再产生它;
# 保留 = 历史报告读回 + `DROPPED_REASON_LABEL` 展示文案不断线。
DROP_BELOW_QUALITY_LINE = "below_quality_line"     # 〔历史〕连 tier2_min 都没过
# V2.2-③-D:证据关降级到出局(③-A:T1→T2→退出正式候选;仍在 ③b 列名)。
DROP_EVIDENCE_DEGRADED_OUT = "evidence_degraded_out"
# 🔴 V2.2-③-C(裁定 #11):位置关(**证据关**)被 LLM 判 `unfit` → 退出正式候选。
# ⚠ 与 `DROP_EVIDENCE_DEGRADED_OUT` **刻意分开**:一个是「证据没撑住逻辑」、一个是
# 「位置不对」,指向完全不同的复盘结论(④ 周度按关口归因要分得开)。
# ⛔ 这**不是硬否决**:票没从报告里消失,③b 逐条写明是哪只成员、模型的理由是什么。
DROP_POSITION_UNFIT = "position_unfit"
# gates 侧的四个除名码(硬否决 / 引擎归属失败)直接沿用 `gates.EXCLUDE_*` 字面,
# `DroppedBasket.reason` 与 ③b/⑨ 消费同一套码,⛔ 不在这里再抄一份字符串。
# ⚠ 各码指向**不同的市场/系统结论**,⛔ 不许合并成一个"未入选"(⑥-b-C 纪律扩容)。

TIER_RANK_SYSTEM_PROMPT = f"""你是 A 股短线交易系统里的「同档次序参谋」。

系统已经用**机械分**把今天的篮子定好了档位(T1/T2)。你的权限**只有一个**:
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
    # 涨停命中、但**一字与否判不出来**的码(当日 `daily` 分区整段缺失,或该码在
    # `daily` 里没有行 / 没有最低价、`limit_derived` 没给涨停价)。⚠ 与 `one_word`
    # 是两个集合、语义相反的"知道"与"不知道":空集 = 全都判过了,不是"都不是一字"
    # (2026-08-04 判定线审计 🔵-5)。
    one_word_unresolved: Set[str] = field(default_factory=set)
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
    """③b 一行的内存形状。V2.2-③ 起带「卡在哪一关、差多少」(`gate`/`gate_detail`,
    机器可读原因码串已内嵌数值)与篮子名 —— 三个新字段全部**追加默认**,既有
    `DroppedBasket(key, reason, score)` 位置构造(跨进程交接/测试)原样成立。"""

    basket_key: str
    reason: str
    mech_score: float
    name: str = ""
    gate: Optional[str] = None          # 卡在哪一关(gates.GATE_* 六码之一;None = 非关口原因)
    gate_detail: Optional[str] = None   # 差多少(原因码串,数值内嵌)


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
    # 本次定档实际用的两档质量线(`resolve_quality_lines()` 解出来的那份,已经
    # 是"包给了就用包的、缺了就回退引擎默认"之后的最终值)——同 `weights`,
    # 是审计快照,不是可写配置。
    quality_lines: Dict[str, float] = field(default_factory=dict)
    notes: Tuple[str, ...] = ()
    # V2.2-③:关口对拍后的 `AggregateResult`(成员已出篮、引擎三件套已回填、被除名
    # 候选已摘除)。**落库/卡生成必须用它**,⛔ 不许再用调用方手里未对拍的原 result
    # (那会把被移除的成员写回库 —— `save_tier_result` 已优先取它)。
    gated_result: Optional[Any] = None
    # 关口汇总(basket_key → gates.BasketGateSummary;含被除名候选)。报告/卡要
    # 「卡在哪关」细节时从这里拿,⛔ 不重判。
    gate_summaries: Dict[str, Any] = field(default_factory=dict)

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
            if ctx.limit_up:
                # ⚠ `daily` 整段缺失时**不能**当"这些涨停都开过板"(那是半罚混过,
                # 判定线审计 🔵-5):一个都判不出来,全部记进 `one_word_unresolved`。
                lows = (
                    {
                        r["ts_code"]: r["low"]
                        for r in daily.filter(pl.col("ts_code").is_in(list(ctx.limit_up)))
                        .select(["ts_code", "low"]).iter_rows(named=True)
                    }
                    if not daily.is_empty() else {}
                )
                for r in hit.select(["ts_code", "limit_up_price"]).iter_rows(named=True):
                    low = lows.get(r["ts_code"])
                    price = r["limit_up_price"]
                    if low is None or price is None:
                        # 缺最低价 / 缺涨停价 = 这只票的一字与否**判不出来**,
                        # 既不算一字也不算"开过板"(「没有」与「没看」分开)。
                        ctx.one_word_unresolved.add(r["ts_code"])
                    elif float(low) >= float(price) - _EPS:
                        # 一字板 = 全天最低价都没低于涨停价 → 根本没有买进的机会。
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
    """一字比例与涨停占比 —— **买不进的涨停不算机会**(蓝图 4.9)。

    ⚠ **本篮有"涨停了但一字与否判不出来"的成员 → 整维走中性分 + `tradability_
    missing`**(2026-08-04 判定线审计 🔵-5):`limit_derived` 有涨停命中、当日 `daily`
    分区却缺失(或该码缺最低价)时,老实现让 `one_word` 恒空 → 一字板被当成"开过板"
    只扣半罚(0.5)悄悄混过去 —— 那是拿"没看"当"没有",而且方向偏松(买不进的票被
    打成"买得进")。判不出来就别给分,如实标。"""
    if not codes:
        return NEUTRAL_DIM_SCORE, [FLAG_TRADABILITY_MISSING]
    if not fctx.tradability_available:
        return NEUTRAL_DIM_SCORE, [FLAG_TRADABILITY_MISSING]
    if any(c in fctx.one_word_unresolved for c in codes):
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
    """从**现役包**读两档质量线(V2-⑥-b-A 裁定;V2.1-② 起只剩 T1/T2)。**逐键独立
    回退引擎默认**——与 `resolve_weights()` 的 fail-loud 姿势刻意不同,两条理由各自
    写死、别去"统一"成同一种:`weights` 每个包 schema 都必须给全(缺了就是包坏了),
    `quality_lines` 缺(整段缺,或只缺其中一个子键)一律回退
    `TIER1_MIN_SCORE`/`TIER2_MIN_SCORE` —— 因为 `K4-pack-v1` 不重发版、是 ⑯-E 的
    回滚锚,不给回退路径就等于把回滚锚作废。

    **包里出现已退役的 `tier3_min`(如回滚锚 `K7-pack-v1`)→ 打一行 WARNING 并忽略**
    (V2.1-② 定死)。⛔ 不静默:静默忽略等于让包以为自己配了个生效的旋钮;⛔ 也不
    报错:那会把两个回滚锚当场作废(与 ⑥-b-A 立 `quality_lines` 时"缺键回退保回滚锚"
    同源理由)。

    无现役包 → 抛(同 `resolve_weights`:定档必须有包,哪怕这次只是为了取
    质量线的回退默认,也不该在没有包的情况下悄悄定档)。"""
    if pack is None:
        raise ValueError("Tier 定档需要现役策略包(质量线只住包里或回退引擎默认),当前无现役包")
    raw = pack.tier_quality_lines()
    retired = [k for k in _RETIRED_QUALITY_LINE_KEYS if k in raw]
    if retired:
        logger.warning(
            "[tier] 策略包 %s 的 config.tier.quality_lines 含已退役键 %s —— "
            "`tier3_min` 已于 V2.1 退役(T3 全链删除),本次忽略;"
            "定档只用 tier1_min/tier2_min。",
            getattr(pack, "pack_version", "?"), retired,
        )
    return {
        "tier1_min": float(raw.get("tier1_min", TIER1_MIN_SCORE)),
        "tier2_min": float(raw.get("tier2_min", TIER2_MIN_SCORE)),
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


def assign_tiers(
    scored: Sequence[Tuple[str, float]],
    want_by_key: Mapping[str, int],
) -> Tuple[Dict[str, Tuple[int, int]], List[DroppedBasket]]:
    """门槛制放位(V2.2-③-D)。输入 `[(basket_key, mech_score), ...]` +
    `want_by_key`(**六关给出的目标档**,1 = T1 资格、2 = T2 资格 —— 由
    `score_and_tier` 从 `gates.BasketGateSummary` 推出;⛔ 分数在这里只定档内序,
    「够不够格」不归它管,那正是门槛制与排序制的分界)。输出
    `({basket_key: (tier, rank_mech)}, dropped)`。

    **确定性铁律**:先按 `(机械分降序, basket_key 升序)` 排定 —— 分数并列时靠
    `basket_key`(crc32 十六进制,跨进程可复现)打破,**不靠行序**(CLAUDE.md:
    `rank(method="ordinal")` 的并列由行序打散 = 不确定性)。

    **上限非配额**:T1 资格但 T1 已满 → **向下顺延**到 T2(不是挤进去,也不是丢掉);
    两档都满 → 今日不定档(`DROP_CAPACITY_OVERFLOW`,「关口过了、位置满」)。
    **允许任何一档为空**(V2-⑥-b-B 原文在门槛制下照旧成立:没有篮子过某档的关,
    那一档就是空的,不许拿别档凑数)。

    `want_by_key` 缺键 / 取值不在 {1,2} → `ValueError` fail loud(那是调用方把
    「达不到 T2 该走 ③b」的候选漏筛进来了 —— 静默兜一个档等于把门槛制改回配额)。
    """
    order = sorted(scored, key=lambda kv: (-kv[1], kv[0]))
    used = {t: 0 for t in TIERS}
    counts = {t: 0 for t in TIERS}
    out: Dict[str, Tuple[int, int]] = {}
    dropped: List[DroppedBasket] = []
    for key, score in order:
        want = want_by_key.get(key)
        if want not in TIERS:
            raise ValueError(
                f"assign_tiers:basket_key={key!r} 的目标档 {want!r} 不在 {TIERS} —— "
                "达不到 T2 的候选应由调用方先落 dropped(evidence_degraded_out / 关口除名),"
                "不进放位"
            )
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

def _gate_breakdown(summary: Any) -> Dict[str, Any]:
    """`gates.BasketGateSummary` → `mech_breakdown_json.gates` 节(审计快照,
    ⑨ 归因与百分制卡的展示原料;⛔ 不进机械分 —— `_TIER_SCORE_INPUTS` 白名单不含它)。"""
    if summary is None:
        return {"available": False}
    verdicts: Dict[str, str] = {}
    rank = {gates_mod.VERDICT_PASS: 0, gates_mod.VERDICT_DEGRADE: 1, gates_mod.VERDICT_REJECT: 2}
    for c in summary.checks:
        cur = verdicts.get(c.gate)
        if cur is None or rank[c.verdict] > rank[cur]:
            verdicts[c.gate] = c.verdict
    return {
        "available": True,
        "engine_code": summary.engine_code,
        "engine_version": summary.engine_version,
        "skeleton_version": summary.skeleton_version,
        "engine_source": summary.engine_source,
        "verdicts": verdicts,
        "evidence_degrades": summary.evidence_degrades,
        "degraded_gates": list(summary.degraded_gates),
        "blocks_t1": summary.blocks_t1,
        # 🔴 裁定 #11:位置关的判定是**模型输出**而不是可回放的数字 —— 快照里逐票
        # 记下它给的三值,归因链才接得上(全量读数与理由在 `gate_evaluations`)。
        "position_unfit": bool(getattr(summary, "position_unfit", False)),
        "position_verdicts": {
            c.ts_code: (c.evidence or {}).get("position_verdict")
            for c in summary.checks
            if c.gate == gates_mod.GATE_POSITION and c.ts_code
        },
        "removed_members": [
            {"ts_code": r.ts_code, "gate": r.gate, "reason": r.reason}
            for r in summary.removed_members
        ],
    }


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
    gates_outcome: Optional[Any] = None,
    engines: Optional[Mapping[str, Pack]] = None,
    skeleton: Optional[Pack] = None,
) -> TierResult:
    """⑥ 唯一编排入口(V2.2-③-D 门槛制):`AggregateResult` → **六道关口**(定档的
    闸)→ 机械分(档内排序)→ 放位 → (可选)LLM 同档微调。

    `pack`:默认取现役骨架包(五维权重与 `stage_scores` 都只住包里)。**无现役包 →
    抛** —— 打分必须有权重,臆造一份默认权重等于在代码里藏第二套策略。

    `result`:🔴 **必须是对拍前的那批候选**(= 喂给 `gates.evaluate_day` 的同一份)。
    传 `gates_outcome.result`(对拍后)会让被关口除名的候选静默消失,本函数为此
    fail loud —— 理由见下面 `missing_from_result` 那段。

    `gates_outcome`:⑭ 管线里由 `evening.py` 先跑 `gates.evaluate_day()`(顺手落
    `gate_evaluations` 留痕)再传进来;不传 → 本函数自己跑一遍(零 LLM、只读表)。
    `engines`/`skeleton` 只在自己跑 gates 时透传(测试注入 Pack 替身的口子,照
    `pack=` 的既有姿势)。

    **⚠ 落库/卡生成必须用 `TierResult.gated_result`**(关口对拍后的 result:成员已
    出篮、引擎三件套已回填)—— `save_tier_result` 已自动取它。

    `use_llm`:默认 **False**(纯机械,零 LLM 调用)。传了 `provider` 但
    `use_llm=False` 时**不会**调用 —— 两个开关刻意分开。

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
                          quality_lines=quality_lines, gated_result=result,
                          notes=tuple(notes + ["no_baskets"]))

    # —— V2.2-③:六道关口(定档的闸,唯一实现 `gates.py`)——————————————————
    if gates_outcome is None:
        gates_outcome = gates_mod.evaluate_day(
            result, trade_date, db_path=db_path, parquet_dir=parquet_dir,
            engines=engines, skeleton=skeleton,
        )
    gated = gates_outcome.result
    summaries = dict(gates_outcome.summaries)
    notes.extend(gates_outcome.notes)

    # 🔴 **`result` 必须是「喂给 gates 的那一批」= 对拍前的候选**(⛔ 不是
    # `gates_outcome.result`)。被关口除名的候选**只存在于 `summaries` 里**,本函数
    # 靠遍历 `result.baskets` 把它们转成 ③b 行;传对拍后的 result 进来 = 那些票在
    # `baskets` 表里没有、在 ③b 里也没有 = **从报告里消失**,正是 §2.9-C-2 明令禁止的
    # 那一种失败,而且**静默**。故这里 fail loud:少一票都要当场炸,别让它悄悄没。
    missing_from_result = sorted(set(summaries) - {b.basket_key for b in result.baskets})
    if missing_from_result:
        raise ValueError(
            f"score_and_tier:gates 汇总里有 {missing_from_result} 不在 result.baskets 里 —— "
            "`result` 必须是**喂给 gates.evaluate_day 的那一批**(对拍前),传 "
            "`gates_outcome.result`(对拍后)会让被关口除名的候选既不进 baskets 表、"
            "也不进 ③b 未定档披露 = 票从报告里消失(§2.9-C-2 禁止)"
        )

    # 机械分(自此只做**档内排序 + 展示标度**):留在正式候选里的篮子按**对拍后**
    # 的成员打(诚实——出篮的成员不该再抬分);被关口除名的按原成员打,只服务
    # ③b 的「分」列(它们不进任何档,分数不再有判定含义)。
    k4_unavailable = "k4_unavailable" in tuple(getattr(result, "notes", ()) or ())
    kept_by_key = {b.basket_key: b for b in gated.baskets}
    score_basket_of = {b.basket_key: kept_by_key.get(b.basket_key, b) for b in result.baskets}
    codes = sorted({m.ts_code for b in score_basket_of.values() for m in b.members})
    fctx = build_feature_context(trade_date, codes, db_path=db_path, parquet_dir=parquet_dir)

    score_by_key: Dict[str, float] = {}
    breakdowns: Dict[str, Dict[str, Any]] = {}
    for b in result.baskets:
        sb = score_basket_of[b.basket_key]
        row, flags = build_tier_row(sb, fctx, stage_scores=stage_scores,
                                    k4_unavailable=k4_unavailable)
        score = mech_score(row, weights)
        score_by_key[b.basket_key] = score
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
            # V2.2-③:六关判定快照(定档依据;⛔ 不进机械分)。
            "gates": _gate_breakdown(summaries.get(b.basket_key)),
        }

    # —— 门槛制筛选:除名 → ③b;够 T2 才进放位;分数只定档内序 ————————————
    dropped: List[DroppedBasket] = []
    want_by_key: Dict[str, int] = {}
    scored_eligible: List[Tuple[str, float]] = []
    for b in result.baskets:
        key = b.basket_key
        s = summaries.get(key)
        if s is None:
            raise ValueError(
                f"score_and_tier:gates 汇总缺 basket_key={key!r} —— "
                "gates_outcome 与 result 不是同一批候选(调用方传串了)"
            )
        if s.excluded:
            dropped.append(DroppedBasket(
                basket_key=key, reason=s.exclusion_reason or "excluded",
                mech_score=score_by_key[key], name=b.name,
                gate=s.stuck_gate, gate_detail=s.stuck_detail,
            ))
            continue
        if not s.t2_eligible:
            # 🔴 裁定 #11:位置关 `unfit` 与「证据关降级超上限」是两种不同的出局,
            # ⛔ 不合并(④ 周度按关口归因要分得开)。位置关优先报 —— 它是**这一只
            # 具体成员**的位置判定,比"降级处数超了"这句更说得清卡在哪。
            if getattr(s, "position_unfit", False):
                dropped.append(DroppedBasket(
                    basket_key=key, reason=DROP_POSITION_UNFIT,
                    mech_score=score_by_key[key], name=b.name,
                    gate=gates_mod.GATE_POSITION,
                    gate_detail=(s.position_unfit_detail or "位置关判定 unfit"),
                ))
                continue
            detail = ";".join(
                c.reason for c in s.checks if c.verdict == gates_mod.VERDICT_DEGRADE
            )
            first_gate = next(
                (g for g in gates_mod.GATE_ORDER if g in s.degraded_gates), None)
            dropped.append(DroppedBasket(
                basket_key=key, reason=DROP_EVIDENCE_DEGRADED_OUT,
                mech_score=score_by_key[key], name=b.name,
                gate=first_gate, gate_detail=detail or "证据关降级超出 T2 上限",
            ))
            continue
        want_by_key[key] = 1 if s.t1_eligible else 2
        scored_eligible.append((key, score_by_key[key]))

    placement, cap_dropped = assign_tiers(scored_eligible, want_by_key)
    name_of = {b.basket_key: b.name for b in result.baskets}
    dropped.extend(
        replace(d, name=name_of.get(d.basket_key, "")) for d in cap_dropped
    )

    # 质量线降级为「档内排序的辅助下限」= 纯展示标度(模块头「V2.2-③-D 门槛制」节)。
    for key, (t, _r) in placement.items():
        line = quality_lines.get(f"tier{t}_min")
        if line is not None:
            breakdowns[key]["below_tier_line"] = bool(
                score_by_key[key] < float(line) - _EPS)

    if dropped:
        # ⑥-b-C 纪律扩容:每种"没进来"指向不同结论,notes/日志逐原因码分别计数披露。
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
        for key, _score in scored_eligible if key in placement
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
        notes=tuple(notes), gated_result=gated, gate_summaries=summaries,
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

    **V2.2-③**:落库基底优先取 `tier_result.gated_result`(关口对拍后的 result:
    成员已出篮、引擎三件套已回填)—— 调用方传进来的 `agg_result` 若是对拍前的原
    结果,直接用它会把已出篮的成员写回库。
    """
    if tier_result.gated_result is not None:
        agg_result = tier_result.gated_result
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


# 四件套缺件降档在 breakdown / 卡上留的原因码前缀(⑨ 归因可 grep;后接缺件清单)。
T1_DEMOTED_PLAN_INCOMPLETE = "t1_demoted:plan_incomplete"


def enforce_plan_completeness(
    tier_result: TierResult,
    missing_by_key: Mapping[str, Sequence[str]],
) -> TierResult:
    """③-E:**「次日交易预案四件套齐」是 T1 的必要条件**(上涨判断 / 入场区间 /
    目标离场区间 / 判断失效位置)。四件套住 ⑦ 的卡上、卡在定档**之后**才生成,
    故本函数由编排层(`report/evening.py`)在 ⑦ 卡构建完、**事务 1 落库之前**调用:

    - `missing_by_key`:{basket_key: [缺哪几件]}(判定唯一实现 =
      `basket_card.trade_plan_missing_pieces()`,本函数不重判)。**没进这张表的
      T1 篮 = 没有卡产物 = 四件套整套缺**(卡生成失败/LLM 缺席时 T1 必须降档 ——
      T1 的含义是「启动成立 + 预案完整可执行」,没有预案就没有 T1)。
    - T1 且缺件 → **降 T2**(⛔ 不是拦截,§3.8:票照留、卡照出,客户端与周复盘各出
      一条警示);T2 已满 → `DROP_CAPACITY_OVERFLOW` 出局(⛔ 不突破容量上限)。
    - 非 T1 篮不受影响(四件套只是 T1 的必要条件,不是 T2 的)。

    返回新 `TierResult`(降档后 T2 全档重排:先原 T2 按档内序、再降档篮按原 T1 档内
    序;`rank_mech` 按 `(机械分降序, basket_key 升序)` 在**最终 T2 集合**上重排 ——
    两个序都必须是 1..N 连续,`tier_history` 两列 NOT NULL 的可复现性要求)。
    幂等:无 T1 或无缺件 → 原样返回。"""
    t1 = [d for d in tier_result.decisions if d.tier == 1]
    if not t1:
        return tier_result
    demote = [d for d in t1 if missing_by_key.get(d.basket_key, ("no_card",))]
    if not demote:
        return tier_result

    keep_t1 = sorted((d for d in t1 if d not in demote), key=lambda d: d.rank_in_tier)
    old_t2 = sorted((d for d in tier_result.decisions if d.tier == 2),
                    key=lambda d: d.rank_in_tier)
    others = [d for d in tier_result.decisions if d.tier not in (1, 2)]

    new_decisions: List[TierDecision] = list(others)
    for idx, d in enumerate(keep_t1, start=1):
        new_decisions.append(replace(d, rank_in_tier=idx))

    demoted_sorted = sorted(demote, key=lambda d: d.rank_in_tier)
    final_t2 = old_t2 + [
        replace(
            d, tier=2,
            breakdown={
                **d.breakdown,
                "t1_demoted_reason": (
                    f"{T1_DEMOTED_PLAN_INCOMPLETE}:"
                    + ",".join(missing_by_key.get(d.basket_key, ("no_card",)))
                ),
            },
        )
        for d in demoted_sorted
    ]
    cap = TIER_CAPACITY[2]
    kept_t2, overflow = final_t2[:cap], final_t2[cap:]
    # 最终 T2 集合上的机械序(确定性:分降序 → key 升序;LLM delta 随之重算)。
    mech_order = {
        d.basket_key: i
        for i, d in enumerate(
            sorted(kept_t2, key=lambda d: (-d.mech_score, d.basket_key)), start=1)
    }
    for idx, d in enumerate(kept_t2, start=1):
        rm = mech_order[d.basket_key]
        new_decisions.append(replace(d, rank_in_tier=idx, rank_mech=rm,
                                     llm_rank_delta=rm - idx))

    dropped = list(tier_result.dropped)
    for d in overflow:
        dropped.append(DroppedBasket(
            basket_key=d.basket_key, reason=DROP_CAPACITY_OVERFLOW,
            mech_score=d.mech_score,
            gate=None,
            gate_detail=f"{T1_DEMOTED_PLAN_INCOMPLETE} 降档后 T2 已满",
        ))
    notes = list(tier_result.notes)
    notes.append(
        f"{T1_DEMOTED_PLAN_INCOMPLETE}:{len(demote)}"
        + (f"(其中 {len(overflow)} 个降档后 T2 满,capacity_overflow)" if overflow else "")
    )
    logger.warning(
        "[tier] %s 有 %d 个 T1 篮四件套不齐,降 T2(缺件:%s)%s",
        tier_result.trade_date, len(demote),
        {d.basket_key: list(missing_by_key.get(d.basket_key, ("no_card",))) for d in demote},
        f";{len(overflow)} 个因 T2 满出局" if overflow else "",
    )
    new_decisions.sort(key=lambda d: (d.tier, d.rank_in_tier))
    return replace(tier_result, decisions=tuple(new_decisions), dropped=tuple(dropped),
                   notes=tuple(notes))


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
    "DROP_EVIDENCE_DEGRADED_OUT",
    "DROP_POSITION_UNFIT",
    "T1_DEMOTED_PLAN_INCOMPLETE",
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
    "enforce_plan_completeness",
]
