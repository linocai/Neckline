"""六道关口管线(plan §五 V2.2-③,K8 §五 的唯一实现;🔴 换心脏件)。

**③-A 关口二分是本模块的宪法**(2026-08-09 用户裁定 #6,**#11 改判位置关**,⛔ 不重开):

    ==========  ==================================  ======================================
    机械关       ① 市场 · ③ 板块                      全机械、可复现、可回放 → **硬否决**
                                                    (退出正式候选,落 `gate_evaluations`)
    证据关       ② 驱动 · ④ 核心 · ⑤ 位置 · ⑥ 证据    LLM 组织证据 + 机械最低标注 → **只降级**
                                                    (T1→T2→退出正式候选,**仍在 ③b 列名**)
    ==========  ==================================  ======================================

🔴 **2026-08-09 用户裁定 #11:位置关由机械关移入证据关,⛔ 不得改回**。原因:K8 §二
对「落地起跳」只有五句定性、零个数字;裁定 #4 授权的工程首版定量落成十二个阈值 /
13 个子门,实测**连乘交集近乎为空**(全市场当日 1~2 / 5526,14 个 D0 回放零 T1)。
用户原话「不要搞这个机械层了……这个地方的判定直接给到大模型」。**第 4 锁「LLM 不做
闸门」因此不但没被突破、反而更严** —— 六关里能硬否决的只剩两道读客观预计算量的关
(市场关读 `market_regime_daily`、板块关读 `industry_strength_daily`/`industry_stage_daily`)。

🔴 **2026-08-09 用户裁定 #12:核心关也退出机械闸,⛔ 不得改回**(同款处置)。原判据
`leader_rs_rank ≤ 3` 取数自 `leader_structure_daily` 的**簇内**口径,而簇 =「当天一起
涨停且同行业/概念的 ≥2 只票」—— 生产实测(20260807)全市场**只有 75 只票有 `rs_rank`
(1.4%)**,其余 98.6% 判不出核心地位 → 结构性进不了 T1。⚠ **不是阈值定严了**
(`≤3` 的 provenance 是 `audited`),**错的是尺子的取数域**:K8 三引擎找的是「还没怎么
涨、刚要动」的票,这道关的入场券却是「今天必须涨停」——**涨停是结果,要的是结果之前
那一刻**。现在:机械侧只出**行业域读数**(`selection/core_metrics.py`,**零阈值、零及格
线**,含「行业内前 X%」这类,用户明确否决),判定交 LLM(⛔ 只降级不除名)。簇内
`rs_rank` **降级为补充读数,缺席不挡任何档**。

🔴 **2026-08-11 策略线裁定 1(V2.3.2-①):市场关 / 板块关自此是"半机械半证据"的**
—— 二分不再按「关」走,而是按**该关的每一个阈值叶子**走:

    provenance.source == "audited"  → hard      机械硬否决(行为一字不改)
    其余(engineering_v1 等)        → evidence  机械只出读数 + 拟判影子,判定交 LLM

唯一实现 `enforcement_of()`(⛔ 全仓不许有第二处,AST 守门)。**本版实际改判三项**:
`C1.market.high_divergence_min_breadth_pctile` · `C1.sector.strength_days_min_5d` ·
`Z1.sector.cluster_members_min` —— 它们**不再 reject**,verdict 由 ⑤ 那一次调用顺带
产出的篮子级三值(`market_verdict` / `sector_verdict`)决定,后果与位置关 / 核心关
**逐字相同**(`ok` 过 / `weak` 降一档 / `unfit` 退出正式候选但仍在 ③b 列名)。
**四项 audited 继续硬否决**(裁定 2 / 已拍板 #3):`C1/Y1.sector.industry_rank_max`、
`Z1.market.trend_continuation_required_stages`、`Z1.sector.stage_allowed`。
⚠ `market.primary_regimes` 与 `C1.market.rotation_confirmed_blocks_t1` 本就只降级
(`PASS + blocks_t1`),**本版零改动**(已拍板 #4)。
⛔ **不得把任何一关改回机械硬否决**,包括"顺手补一条及格线" —— 恢复的唯一通道 =
裁定 6 的七项提交 → 用户确认 → **新引擎版本**里改 provenance(零自动升级)。
⚠ 第 4 锁「LLM 不做闸门」仍然完好:`verdict` 永不为 `reject`,「退出」发生在定档层。

**「退出正式候选」≠「从报告里消失」**(§2.9-C-2):被本模块除名的候选一律进
`TierResult.dropped` → 报告 ③b(名 / 分 / 卡在哪一关、差多少 / 原因码)。

**引擎归属(裁定 #9 单篮子单引擎,§2.9-C-4)**:`basket_reason` 那**一次**调用为每个
篮子主张一个 `engine_code`(`BasketCandidate.engine_code_llm`),本模块拿该引擎的
**机械关阈值**对拍。⛔ 不静默采信 LLM;LLM 没给 / 给错 → 机械兜底(按 C→Z→Y 确定性
序找第一个「篮子级机械关不拒」的引擎),仍无 → 退出正式候选(`engine_unresolved`,
③b 列名)。**零运行引擎 = 当日不产任何候选**(`pack.get_active_line` docstring 既定
语义),全部候选按 `no_active_engine` 落 ③b。

⚠ **裁定 #11 之后「成员出篮」这条路当前没有触发源(如实登记,不是遗漏)**:
唯一会成员级除名的是原来的机械位置关,它已改判为只降级的证据关;剩下两道机械关
(市场 / 板块)都是**篮子级**判据。留下 `removed_members` / `_repair_primary` /
`members_all_removed` 这套机械是给**未来真的出现成员级机械关**时用的通路,当前恒空
—— ⛔ 别据此以为"对拍闸没接线",也别为了"让它有用"把位置关改回硬否决。

**「缺数 = 不知道,⛔ 不许猜」的统一姿势**(承 ② 市场关缺行裁定,推广到六关):
任何一关的判定输入取不到 → 该关 **不拦**(verdict=pass)+ `available=False` +
`blocks_t1=True`(该票不得进 T1)+ 原因码 `missing:*`。「判不出」永远不是
「判过了」,也永远不是「拦下来」。

**成本铁律(附「成本与超时算术」)**:本模块 **零 LLM 调用** —— 证据关/驱动关/
核心关/**位置关**的 LLM 侧产出一律复用 ⑤ `basket_reason` 那一次调用带回的结构化字段
(`common_trait` / `persistence` / `strengthen_and_invalidate` / `evidence_conflicts` /
成员 `reason` / 角色 / **`position_verdict` + `core_verdict` + 各自的理由与当次读数**),
本模块只做归并与留痕。三引擎并跑只体现在**阈值分支**上,⛔ 不体现在调用次数上。

**留痕**:`gate_evaluations`(append-only,每候选每关一行;成员级关口〔核心/位置〕
每成员一行)。写入口只有 `save_gate_evaluations()`,零 UPDATE/DELETE。
🔴 **位置关行与核心关行的硬要求(plan ③-C 末段 / ③-C2 同款)**:`gate_kind='llm'` 且
`evidence_json` **必须同时存下当次读数与 LLM 理由** —— 判定不再是一组可回放的数字而是
一段模型输出,不把这两样存在一起,事后无法复核它到底在拿什么下判断(P3-49 的证伪义务
不减反增)。⚠ 读数取自 `BasketMemberCandidate.position_metrics` / `.core_metrics`
(= **当次喂进 prompt 的那一份**),⛔ 本模块不另读一遍 `landing_metrics_daily`、也不另
调一遍 `core_metrics.compute_core_metrics` —— 另读会存下「事后那一份」,与模型当时看到
的可能不是同一份,留痕就白留了。

**阈值唯一源 = 引擎包 `config.engine.gates`**(键名契约 =
`selection/pack.py::_ENGINE_GATE_SCHEMA`,⛔ 本模块不自创第二套键名、不硬编任何
阈值数字——包里没给的键 = 该引擎不设这道分支)。

**证据独立性的机械口径(工程判断,登记)**:K8 原文「高度相关的技术指标按一份计」
→ 机械侧按 `evidence_kind` 归并、同 kind 只计一份(plan ③-B 原文)。`EvidenceItem`
本身不带 kind,机械分类器 `classify_evidence_kind()` 按 claim/source 文本模式给出:
技术指标类**全体折成一份**(kind='technical'),其余按「类别:来源」成 kind(同一
来源的多条消息只算一份独立证据)。Z1 的「必须含一份消息/政策类来源」= 存在
非技术、非研报类的 kind。这是定性条款的首版机械翻译(`engineering_v1` 性质,
校准走选股时钟),⛔ 不冒充审计结论。

**反向守门**:零 import `neckline.report.score_display`(V2.1-④ 方向性规则)、
零 import `neckline.sentinel.*`、零 import `neckline.selection.tier`(tier 反过来
import 本模块,方向单一)、🆕 零 import `neckline.scan.landing*`(裁定 #11:位置关的
读数由 ⑤ 随成员带进来,本模块不碰那张表)、🆕 零 import
`neckline.selection.core_metrics`(裁定 #12 同款:核心读数也由 ⑤ 随成员带进来)。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.db import connection, init_schema
from neckline.report.industry_strength_store import load_industry_strength
from neckline.scan import stage as stage_mod
# 「无骨架线现役」哨兵串与 regime 同一个字面(同一条纪律,不抄第二份)。
from neckline.scan.regime import SKELETON_VERSION_FALLBACK
from neckline.scan.regime_store import load_market_regime
# 位置关三值的**唯一源**在 ⑤(判定就发生在那一次调用里),这里只消费,⛔ 不抄第二份。
# 🆕 V2.3.2-①-C:市场关 / 板块关的**篮子级**三值同源同构(与位置/核心关刻意同字面,
# 但是四个独立判定,⛔ 不许合并、不许拿一个的兜底解释另一个)。
from neckline.selection.aggregate import (
    CORE_OK,
    CORE_UNFIT,
    CORE_VERDICT_FALLBACK,
    CORE_VERDICTS,
    MARKET_UNFIT,
    MARKET_VERDICTS,
    POSITION_OK,
    POSITION_UNFIT,
    POSITION_VERDICT_FALLBACK,
    POSITION_VERDICTS,
    SECTOR_UNFIT,
    SECTOR_VERDICTS,
)
from neckline.selection.pack import Pack, get_active_engines, get_active_skeleton

logger = logging.getLogger(__name__)

TABLE = "gate_evaluations"

# —— 六关英文码(库列值 / 契约码 / ③b 原因码共用同一套,唯一源)————————————————
GATE_MARKET = "market"
GATE_DRIVER = "driver"
GATE_SECTOR = "sector"
GATE_CORE = "core"
GATE_POSITION = "position"
GATE_EVIDENCE = "evidence"

# K8 §五 的关口顺序(①市场 ②驱动 ③板块 ④核心 ⑤位置 ⑥证据)。
GATE_ORDER: Tuple[str, ...] = (
    GATE_MARKET, GATE_DRIVER, GATE_SECTOR, GATE_CORE, GATE_POSITION, GATE_EVIDENCE,
)

# ③-A 二分(宪法,⛔ 不重开;🔴 裁定 #11 把位置关从机械关移进证据关):
# 机械关硬否决,证据关只降级。
MECH_GATES = frozenset({GATE_MARKET, GATE_SECTOR})
EVIDENCE_GATES = frozenset({GATE_DRIVER, GATE_CORE, GATE_POSITION, GATE_EVIDENCE})

GATE_KIND_MECH = "mech"
GATE_KIND_LLM = "llm"
GATE_KIND_OF: Dict[str, str] = {
    GATE_MARKET: GATE_KIND_MECH, GATE_SECTOR: GATE_KIND_MECH,
    GATE_DRIVER: GATE_KIND_LLM, GATE_CORE: GATE_KIND_LLM,
    GATE_POSITION: GATE_KIND_LLM, GATE_EVIDENCE: GATE_KIND_LLM,
}

# 成员级关口(`gate_evaluations.ts_code` 非空的那两关,plan ③-B DDL 注释原文)。
MEMBER_LEVEL_GATES = frozenset({GATE_CORE, GATE_POSITION})

GATE_LABELS: Dict[str, str] = {
    GATE_MARKET: "市场关", GATE_DRIVER: "驱动关", GATE_SECTOR: "板块关",
    GATE_CORE: "核心关", GATE_POSITION: "位置关", GATE_EVIDENCE: "证据关",
}

# —— 三态判定码(③-A 机器判据:机械关只会 pass/reject,证据关只会 pass/degrade)——
VERDICT_PASS = "pass"
VERDICT_DEGRADE = "degrade"
VERDICT_REJECT = "reject"

# —— 引擎归属来源(`BasketCandidate.engine_source`)——————————————————————————
ENGINE_SOURCE_LLM = "llm"                    # LLM 主张 + 机械对拍通过采用
ENGINE_SOURCE_MECH_FALLBACK = "mech_fallback"  # LLM 缺席/给错,机械按 C→Z→Y 兜底

# —— 除名原因码(③b `DroppedBasket.reason` 的 gates 侧新增取值;⑨ 按码归因,
# 一经落库不改字面)——————————————————————————————————————————————————————
EXCLUDE_NO_ACTIVE_ENGINE = "no_active_engine"        # 零运行引擎 = 当日不产任何候选
EXCLUDE_ENGINE_UNRESOLVED = "engine_unresolved"      # LLM 没给/给错 + 机械兜底也找不到
EXCLUDE_MECH_GATE_REJECTED = "mech_gate_rejected"    # 篮子级机械关(市场/板块)硬否决
# ⚠ 裁定 #11 后**当前没有触发源**(唯一的成员级机械关〔位置关〕已改判为证据关);
# 码与通路保留给未来真的出现成员级机械关时用,⛔ 别据此把位置关改回硬否决。
EXCLUDE_MEMBERS_ALL_REMOVED = "members_all_removed"  # 成员级机械关对拍后成员全部出篮

# —— V2.3.2-①-A:市场关 / 板块关的**闸门模式二分**(唯一判据 = provenance.source)——
ENFORCEMENT_HARD = "hard"          # 机械硬否决(仅 `source=audited` 的叶子)
ENFORCEMENT_EVIDENCE = "evidence"  # 证据模式:机械只出读数 + 拟判,判定交 LLM
PROVENANCE_SOURCE_AUDITED = "audited"

# 影子台账与闸门模式判定要遍历的**市场关 / 板块关阈值键全集**(确定性序)。
# ⚠ 必须与 `pack._ENGINE_GATE_SCHEMA` 的 market/sector 两节逐键相等 —— 守门单测正面对拍
# (漏一个键 = 那条阈值悄悄不进影子台账,`①-E` 的通过率就少一维且**看不出来**)。
GOVERNED_THRESHOLD_KEYS: Tuple[Tuple[str, str], ...] = (
    (GATE_MARKET, "primary_regimes"),
    (GATE_MARKET, "high_divergence_min_breadth_pctile"),
    (GATE_MARKET, "rotation_confirmed_blocks_t1"),
    (GATE_MARKET, "trend_continuation_required_stages"),
    (GATE_SECTOR, "industry_rank_max"),
    (GATE_SECTOR, "strength_days_min_5d"),
    (GATE_SECTOR, "stage_allowed"),
    (GATE_SECTOR, "cluster_members_min"),
)

# 影子行的 `unavailable_reason` 前缀:**「这条规则今天不适用」≠「算不出」**
# (①-D 的 ⚠:`high_divergence_min_breadth_pctile` 只在高位分歧态适用,拿全体候选
# 当分母会把它稀释成一个好看的数)。⑤-E 出通过率时按适用域分母,靠的就是这个前缀。
NOT_APPLICABLE_PREFIX = "not_applicable:"

# tier_evidence 缺键时的引擎默认(K8 §八:T1 零降级 / T2 至多一处;包里给了以包为准)。
T1_MAX_EVIDENCE_DEGRADES_DEFAULT = 0
T2_MAX_EVIDENCE_DEGRADES_DEFAULT = 1

_EPS = 1e-9   # 阈值比较容差(`sentinel/holding.py` 体例)

# C1 强度日窗口(「近 5 日强度日 ≥ N 天」的"近 5 日",与包键 `strength_days_min_5d`
# 名字里的 5 同源 —— 窗口是键名语义的一部分,不是可调参数)。
STRENGTH_DAYS_WINDOW = 5

# —— 证据 kind 机械分类(工程首版口径,见模块头「证据独立性的机械口径」)——————
EVIDENCE_KIND_TECHNICAL = "technical"
_CAT_TECHNICAL = "technical"
_CAT_POLICY = "policy"
_CAT_ANNOUNCEMENT = "announcement"
_CAT_RESEARCH = "research"
_CAT_NEWS = "news"
# 消息/政策类 = 非技术、非研报(Z1 `require_news_policy_source` 的机械口径)。
_NEWS_POLICY_CATEGORIES = frozenset({_CAT_POLICY, _CAT_ANNOUNCEMENT, _CAT_NEWS})

_TECH_RE = re.compile(
    r"均线|MACD|KDJ|RSI|BOLL|金叉|死叉|K线|k线|技术形态|技术指标|放量|缩量|量能|"
    r"换手率|突破.{0,4}(压力|平台|新高)|支撑位|压力位|筹码"
)
_POLICY_RE = re.compile(
    r"政策|国务院|发改委|工信部|财政部|商务部|证监会|央行|人民银行|部委|监管|"
    r"规划|行动方案|指导意见|通知|条例|办法|细则|补贴|试点|立法|法案"
)
_ANNOUNCEMENT_RE = re.compile(r"公告|中标|订单|合同|业绩预告|业绩快报|年报|季报|增持|回购|扩产|投产")
_RESEARCH_RE = re.compile(r"研报|研究报告|研究所|券商|首次覆盖|评级|目标价")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def classify_evidence_kind(item: Any) -> Tuple[str, str]:
    """一条证据 → `(category, evidence_kind)`。技术指标类全体折成同一个 kind
    (「高度相关的技术指标按一份计」,K8 原文);其余 kind = `类别:归一化来源`
    (同来源多条只算一份独立证据)。duck-typed:只要求 `.claim`/`.source`。"""
    claim = str(getattr(item, "claim", "") or "")
    source = str(getattr(item, "source", "") or "")
    text = claim + " " + source
    if _TECH_RE.search(text):
        return _CAT_TECHNICAL, EVIDENCE_KIND_TECHNICAL
    if _POLICY_RE.search(text):
        cat = _CAT_POLICY
    elif _ANNOUNCEMENT_RE.search(text):
        cat = _CAT_ANNOUNCEMENT
    elif _RESEARCH_RE.search(text):
        cat = _CAT_RESEARCH
    else:
        cat = _CAT_NEWS
    return cat, f"{cat}:{source.strip().lower()}"


def independent_evidence_kinds(evidence: Sequence[Any]) -> Tuple[Tuple[str, ...], bool]:
    """去重后的 evidence_kind 元组(升序,确定性)+「是否含消息/政策类来源」。"""
    kinds: List[str] = []
    has_news_policy = False
    for item in evidence:
        cat, kind = classify_evidence_kind(item)
        if kind not in kinds:
            kinds.append(kind)
        if cat in _NEWS_POLICY_CATEGORIES:
            has_news_policy = True
    return tuple(sorted(kinds)), has_news_policy


# ══════════════════════════════════════════════════════════════════════════
# 数据形状
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class GateCheck:
    """一次关口判定(= `gate_evaluations` 一行)。`available=False` = 判定输入取不到
    (verdict 恒 pass + blocks_t1,「不知道」既不是「过了」也不是「拦下」——
    但它挡 T1)。`reason` 是机器可读原因码串,数值已内嵌(③b「差多少」直接用它)。"""

    gate: str
    verdict: str
    ts_code: Optional[str] = None
    score: Optional[float] = None
    threshold: Optional[float] = None
    reason: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    available: bool = True
    blocks_t1: bool = False

    @property
    def gate_kind(self) -> str:
        return GATE_KIND_OF[self.gate]


@dataclass(frozen=True)
class MemberRemoval:
    """引擎机械对拍出篮的成员(留痕;对应的 reject 行同时在 `checks` 里)。"""

    ts_code: str
    gate: str
    reason: str


@dataclass(frozen=True)
class ThresholdReading:
    """一条 **`enforcement=evidence`** 阈值叶子在本候选上的读数与拟判
    (V2.3.2-①-D `threshold_shadow_evals` 的唯一原料;裁定 5 的落点)。

    🔴 **计算与「硬门有没有先拒」完全解耦**:现行 `_sector_gate` 里
    `strength_days_min_5d` 曾嵌在 `industry_rank_max` 通过之后的分支里 —— 照原样接线,
    行业名次被硬门拒掉的候选就**根本不会产生强度日读数**,该条的单关通过率分母会悄悄
    变成「名次已过的那批」,与裁定 3 写死的分母(「进入市场关、板块关**之前**的召回
    候选或篮子」)不是同一个东西,**而且看不出来**。故读数一律由
    `collect_threshold_readings()` 独立算一遍,硬门照旧先拒、只是读数照算照存。

    `would_pass` = 该阈值**若按硬门运行**的拟判(`True`/`False`/`None`=算不出或不适用);
    `unavailable_reason` 以 `not_applicable:` 开头 = 规则本身今天不适用(⛔ 不是缺数)。"""

    threshold_key: str                       # `<gate>.<key>` 全称,如 `sector.cluster_members_min`
    gate: str
    reading: Optional[float] = None          # 该阈值对应的机械读数(类别型阈值恒 None)
    threshold_value: Optional[float] = None  # 数值型阈值的门槛(类别型恒 None)
    would_pass: Optional[bool] = None
    unavailable_reason: str = ""


@dataclass(frozen=True)
class BasketGateSummary:
    """一个篮子候选的六关判定汇总(⑥ 门槛制定档的直接输入)。"""

    basket_key: str
    name: str = ""
    engine_code: Optional[str] = None
    engine_version: Optional[str] = None
    skeleton_version: Optional[str] = None
    engine_source: Optional[str] = None
    checks: Tuple[GateCheck, ...] = ()
    removed_members: Tuple[MemberRemoval, ...] = ()
    kept_member_codes: Tuple[str, ...] = ()
    excluded: bool = False
    exclusion_reason: Optional[str] = None
    stuck_gate: Optional[str] = None       # 卡在哪一关(③b)
    stuck_detail: Optional[str] = None     # 差多少(③b,原因码串已带数值)
    evidence_degrades: int = 0
    degraded_gates: Tuple[str, ...] = ()
    blocks_t1: bool = False
    blocks_t1_reasons: Tuple[str, ...] = ()
    # 🔴 裁定 #11:任一成员被 LLM 判 `position_verdict='unfit'` → 该候选**退出正式
    # 候选**(③-A 证据关的最重后果:T1→T2→退出正式候选),**仍在 ③b 列名**。
    # ⛔ 这不是硬否决:位置关的 `verdict` 永远只有 pass/degrade(第 4 锁),
    # 「退出」发生在定档层(`t2_eligible=False`),不是在关口层把票删掉。
    position_unfit: bool = False
    position_unfit_detail: str = ""
    # 🔴 裁定 #12:核心关同款(任一成员被 LLM 判 `core_verdict='unfit'` → 该候选
    # **退出正式候选**,**仍在 ③b 列名**)。⛔ 与 `position_unfit` **分开两格、不合并**
    # —— 「不是龙头」与「位置不对」指向完全不同的复盘结论(④ 周度按关口归因要分得开)。
    core_unfit: bool = False
    core_unfit_detail: str = ""
    # 🆕 V2.3.2-①-C:市场关 / 板块关的 **evidence 半边**被 LLM 判 `unfit` → 该候选
    # 退出正式候选(OUT),**仍在 ③b 列名**。⛔ 与上面两格**分开四格、不合并** ——
    # 「大盘不适配」「板块不适配」「不是龙头」「位置不对」指向完全不同的复盘结论
    # (④ 周度按关口归因要分得开)。⛔ 这不是机械除名:`verdict` 永不为 `reject`,
    # 「退出」发生在定档层(`t2_eligible=False`)。
    market_unfit: bool = False
    market_unfit_detail: str = ""
    sector_unfit: bool = False
    sector_unfit_detail: str = ""
    # 🆕 V2.3.2-①-D:本候选在**该引擎全部 evidence 阈值**上的读数与拟判(影子台账原料)。
    # ⚠ 与 `checks` 是两件事:`checks` 是"这一关最终怎么判的",本项是"每条待定阈值
    # 若按硬门跑本可通过/本可否决" —— 后者才是裁定 4 五项通过率的分子分母来源。
    threshold_readings: Tuple[ThresholdReading, ...] = ()
    regime_available: bool = False
    t1_max_evidence_degrades: int = T1_MAX_EVIDENCE_DEGRADES_DEFAULT
    t2_max_evidence_degrades: int = T2_MAX_EVIDENCE_DEGRADES_DEFAULT

    @property
    def t1_eligible(self) -> bool:
        """③-D 的 T1:**机械关 ①③ pass + 证据关 ②④⑤⑥ 全 pass(含
        `position_verdict=ok`)** + 全部输入可得 + `market_regime` 可得。
        (「四件套齐」在 ⑦ 卡生成之后才验,见 `tier.enforce_plan_completeness`。)

        🔴 裁定 #11:**⛔ 不再要求任何机械态枚举** —— 原「全员 `liftoff_confirmed`」
        那一条正是把 T1 掐成近乎不可达的东西(实测 14 个 D0 回放零 T1),已整体作废。
        🔴 裁定 #12:核心关同样**不再有任何机械及格线**(原 `leader_rs_rank ≤ 3` 只有
        1.4% 的票判得出),`core_verdict = ok` 是 T1 的必要条件。"""
        return (
            not self.excluded
            and not self.any_unfit
            and self.evidence_degrades <= self.t1_max_evidence_degrades
            and not self.degraded_gates
            and not self.blocks_t1
            and self.regime_available
        )

    @property
    def any_unfit(self) -> bool:
        """四个 `unfit` 标里有任意一个立起来(V2.3.2-①-C 起从两格扩到四格)。
        ⛔ 四格**分开存**是刻意的(④ 归因要分得开),这里只做「要不要退出正式候选」
        这一个问题的归并。"""
        return bool(self.position_unfit or self.core_unfit
                    or self.market_unfit or self.sector_unfit)

    @property
    def t2_eligible(self) -> bool:
        """③-D 的 T2:机械关全过 且 证据关(**含核心关 / 位置关 / 🆕 市场关与板块关的
        evidence 半边**)降级处数 ≤ 引擎 T2 上限。任一 `unfit` → 退出正式候选
        (⛔ 但票仍进 ③b,不消失)。"""
        return (not self.excluded
                and not self.any_unfit
                and self.evidence_degrades <= self.t2_max_evidence_degrades)


@dataclass(frozen=True)
class GateDayOutcome:
    """一天的关口判定结果。`result` = 对拍后的 `AggregateResult`(成员已出篮、引擎
    三件套已回填、被除名的候选已从 `baskets` 摘除 —— 它们在 `summaries` 里留痕,
    由 ⑥ 转成 ③b 的 `DroppedBasket`,⛔ 不会消失)。"""

    trade_date: str
    result: Any
    summaries: Dict[str, BasketGateSummary] = field(default_factory=dict)
    engines: Tuple[str, ...] = ()
    skeleton_version: str = SKELETON_VERSION_FALLBACK
    notes: Tuple[str, ...] = ()

    def excluded_summaries(self) -> List[BasketGateSummary]:
        return [s for s in self.summaries.values() if s.excluded]


# ══════════════════════════════════════════════════════════════════════════
# 判定上下文(每天一次,全篮复用;每一路独立保险丝 —— 少一路只是对应关 unavailable)
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class GateContext:
    trade_date: date
    regime_row: Optional[Dict[str, Any]] = None
    regime_breadth_pctile: Optional[float] = None
    # ⚠ 裁定 #11 后**位置关不在这里取数**:读数由 ⑤ 随 `BasketMemberCandidate`
    # 带进来(= 当次喂给 LLM 的那一份),本上下文零 landing 字段、本模块零 import
    # `neckline.scan.landing*`(守门单测锁死)。
    industry_rank: Dict[str, int] = field(default_factory=dict)
    industry_available: bool = False
    strength_days_5d: Dict[str, int] = field(default_factory=dict)
    strength_days_window: int = 0           # 实际取到强度数据的交易日数(0 = 算不出)
    stage_of: Dict[str, str] = field(default_factory=dict)
    stage_available: bool = False
    skeleton_version: str = SKELETON_VERSION_FALLBACK
    # 🆕 V2.3.2-①-C:三条引擎线的市场关 / 板块关**待定阈值**(`enforcement=evidence`
    # 的那些),形状 `{engine_code: {gate: {key: value}}}`。⑤ 的 prompt 靠它明示
    # 「本项当前是证据、不是硬门」。⛔ 只放 evidence 项 —— audited 的四项是硬门,
    # 摊给模型看会让它以为那几条也归它判。
    evidence_thresholds: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)


def describe_evidence_thresholds(
    engines: Mapping[str, Pack],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """三条引擎线的 `enforcement=evidence` 市场关 / 板块关阈值 → prompt 用的描述件。

    ⚠ 判据只有 `enforcement_of()` 一处(①-A);本函数只是把它的结果按引擎摊平,
    ⛔ 不在这里另判一次 source。"""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for code, pk in engines.items():
        per_gate: Dict[str, Dict[str, Any]] = {}
        for gate, key in GOVERNED_THRESHOLD_KEYS:
            leaf = _gate_leaf(pk, gate, key)
            if leaf is None or enforcement_of(leaf) == ENFORCEMENT_HARD:
                continue
            per_gate.setdefault(gate, {})[key] = (
                leaf.get("value") if isinstance(leaf, Mapping) else None)
        if per_gate:
            out[code] = per_gate
    return out


def build_gate_context(
    trade_date: date,
    codes: Sequence[str],                 # noqa: ARG001  —— 裁定 #11 后六关无成员级取数
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,   # noqa: ARG001  —— 签名对齐管线;当前六关全部只读表
    skeleton: Optional[Pack] = None,
    engines: Optional[Mapping[str, Pack]] = None,
) -> GateContext:
    """装配六关所需的机械数据(**只读表**,P0-23 纪律:regime / industry_strength /
    stage 全是 EOD 预计算产物,本函数零现算、零 parquet 扫描)。

    ⚠ **位置关不在这里取数**(裁定 #11):它的读数与判定由 ⑤ 随成员带进来。
    `codes` 因此当前只用于签名对齐与将来的成员级机械关,本函数不按它取任何数。"""
    ctx = GateContext(trade_date=trade_date)

    # —— 骨架版本口径指纹 ——
    try:
        sk = skeleton if skeleton is not None else get_active_skeleton(db_path)
        ctx.skeleton_version = sk.pack_version if sk is not None else SKELETON_VERSION_FALLBACK
    except Exception:  # noqa: BLE001
        logger.warning("[gates] 读骨架线现役行失败,skeleton_version 记哨兵串", exc_info=True)

    # —— ① 市场关:行情状态(缺行 = 不拦但不给 T1,② 已定)——
    try:
        ctx.regime_row = load_market_regime(trade_date, db_path=db_path)
        if ctx.regime_row is not None:
            breadth = (ctx.regime_row.get("inputs") or {}).get("breadth") or {}
            p = breadth.get("pctile")
            if isinstance(p, (int, float)) and not isinstance(p, bool):
                ctx.regime_breadth_pctile = float(p)
    except Exception:  # noqa: BLE001
        logger.warning("[gates] 行情状态表读取失败,市场关按缺行处理", exc_info=True)

    # —— ③ 板块关:行业强度(D0 名次 + 近 5 交易日强度日数)——
    try:
        rows = load_industry_strength(trade_date, db_path=db_path)
        ctx.industry_rank = {r.industry: int(r.industry_rank) for r in rows}
        ctx.industry_available = bool(rows)
    except Exception:  # noqa: BLE001
        logger.warning("[gates] 行业强度表读取失败,板块关名次侧 unavailable", exc_info=True)
    try:
        from neckline.calendar import prev_trading_day

        days = [trade_date]
        cur = trade_date
        for _ in range(STRENGTH_DAYS_WINDOW - 1):
            cur = prev_trading_day(cur)
            days.append(cur)
        counts: Dict[str, int] = {}
        window = 0
        for d in days:
            day_rows = load_industry_strength(d, db_path=db_path)
            if not day_rows:
                continue
            window += 1
            for r in day_rows:
                if r.is_strength_day:
                    counts[r.industry] = counts.get(r.industry, 0) + 1
        ctx.strength_days_5d = counts
        ctx.strength_days_window = window
    except Exception:  # noqa: BLE001
        logger.warning("[gates] 近 %d 日强度日统计失败,板块关强度日侧 unavailable",
                       STRENGTH_DAYS_WINDOW, exc_info=True)
        ctx.strength_days_5d = {}
        ctx.strength_days_window = 0

    # —— ①(Z1 分支)/③(Z1 分支):行业阶段 ——
    try:
        stage_rows = stage_mod.load_industry_stage(trade_date, db_path=db_path)
        ctx.stage_of = stage_mod.stage_lookup(stage_rows)
        ctx.stage_available = bool(ctx.stage_of)
    except Exception:  # noqa: BLE001
        logger.warning("[gates] 行业阶段表读取失败,阶段分支 unavailable", exc_info=True)

    # —— ①-C:待定阈值描述件(给 ⑤ 的 prompt 用;取不到只是 prompt 少一段)——
    try:
        eng = engines if engines is not None else get_active_engines(db_path)
        ctx.evidence_thresholds = describe_evidence_thresholds(eng)
    except Exception:  # noqa: BLE001
        logger.warning("[gates] 引擎线待定阈值读取失败,⑤ prompt 少这一段", exc_info=True)

    return ctx


# ══════════════════════════════════════════════════════════════════════════
# 引擎阈值读取(键名契约 = pack._ENGINE_GATE_SCHEMA;缺键 = 该引擎不设这道分支)
# ══════════════════════════════════════════════════════════════════════════

def _gate_leaf(engine: Pack, section: str, key: str) -> Any:
    """`config.engine.gates.<section>.<key>` 的**整片叶子**(含 provenance);缺键 → `None`。
    `enforcement_of()` 要看 provenance,故与只取 `.value` 的 `_gate_value` 分开两个函数。"""
    return ((engine.config.get("engine") or {}).get("gates") or {}).get(section, {}).get(key)


def _gate_value(engine: Pack, section: str, key: str) -> Any:
    """`config.engine.gates.<section>.<key>.value`;缺键/形状不对 → `None`
    (闸 1 已在激活时校验过形状,这里的宽容只服务于测试替身与历史行)。"""
    leaf = _gate_leaf(engine, section, key)
    if isinstance(leaf, Mapping):
        return leaf.get("value")
    return None


def enforcement_of(leaf: Any) -> str:
    """一个**市场关 / 板块关**阈值叶子 → 闸门模式(V2.3.2-①-A,**全仓唯一实现**)。

    🔴 **唯一判据 = `provenance.source == "audited"`**(策略线裁定 1 / 2,用户已确认):
    `audited` → `hard`(机械硬否决,行为一字不改);其余(`engineering_v1` 等)→
    `evidence`(机械侧只出读数 + 拟判影子,判定交 LLM,**只能降低候选等级、不能机械除名**)。

    **为什么不需要第三个枚举值 / 新开关**:裁定 2 列的六条本身就是按 `source` 二分的
    —— 四项 `audited`(`C1.sector.industry_rank_max` / `Y1.sector.industry_rank_max` /
    `Z1.market.trend_continuation_required_stages` / `Z1.sector.stage_allowed`)留硬否决,
    其余 `engineering_v1 + calibration=pending` 降证据。**逐条核对过、无一例外**。

    🔴 **默认方向 = `evidence`**(读不到 provenance / 形状异常):误判成 evidence 的后果是
    「多留一个候选」(吵),误判成 hard 的后果是「静默除名」(漏审)—— 方向刻意选前者。
    ⚠ 与 `brain.stop_is_advisory` 的保守默认**方向相反**,⛔ 别抄错。

    ⛔ **全仓不许有第二处按 `source` 判闸门模式的代码**(AST 守门单测扫)。
    恢复硬否决的唯一通道 = 裁定 6 的七项提交 → 用户确认 → 在**新引擎版本**里把该叶子
    写成 `source: audited` + `ref` 指向提交件(⛔ 零自动升级)。"""
    if not isinstance(leaf, Mapping):
        return ENFORCEMENT_EVIDENCE
    prov = leaf.get("provenance")
    if not isinstance(prov, Mapping):
        return ENFORCEMENT_EVIDENCE
    if str(prov.get("source") or "").strip() == PROVENANCE_SOURCE_AUDITED:
        return ENFORCEMENT_HARD
    return ENFORCEMENT_EVIDENCE


def _enforcement(engine: Pack, section: str, key: str) -> str:
    return enforcement_of(_gate_leaf(engine, section, key))


def check_threshold_governance(
    governance: Mapping[str, Any], engines: Mapping[str, Pack],
) -> List[str]:
    """骨架包 `config.threshold_governance` **对账表** × 三个现役引擎包的逐条一致性
    (V2.3.2-④-A 闸 1 的第二半;形状校验在 `pack._validate_threshold_governance`)。

    🔴 **这张表不是第二个事实源**:闸门模式仍由 `provenance.source` 唯一决定
    (`enforcement_of`)。它只负责**让一次悄悄的 provenance 改动过不了闸 1** ——
    有人把某条阈值从 `engineering_v1` 改成 `audited`(= 悄悄恢复机械硬否决)却没同步
    改这张表,激活当场被拒。这是裁定 1「零自动升级」的物理落点。

    住在 `gates.py` 而不是 `pack.py`:`enforcement_of` 是**全仓唯一实现**,而 `pack.py`
    ⛔ 不能 import `gates`(gates 反向 import pack,会成环)。

    返回错误串列表(空 = 对得上)。`engines` 按 `pack_version`(C1/Z1/Y1)对号入座 ——
    ⚠ 用版本号而不是引擎码,**引擎升版就必须重新过一遍这张表**,那正是我们要的。"""
    errors: List[str] = []
    by_version = {pk.pack_version: pk for pk in engines.values()}
    seen: set = set()
    for key in sorted(governance):
        entry = governance[key]
        if not isinstance(entry, Mapping) or "mode" not in entry:
            continue                       # 形状错误已由 pack 侧报过,这里不重复报
        parts = str(key).split(".")
        if len(parts) != 3:
            continue
        version, gate, leaf_key = parts
        pk = by_version.get(version)
        if pk is None:
            errors.append(
                f"对账表 {key}:现役引擎线里没有版本 {version}"
                f"(现役 {sorted(by_version)})—— 引擎升过版就必须同步这张表")
            continue
        leaf = _gate_leaf(pk, gate, leaf_key)
        if leaf is None:
            errors.append(f"对账表 {key}:引擎包 {version} 里没有这个阈值叶子")
            continue
        actual = enforcement_of(leaf)
        if actual != entry["mode"]:
            errors.append(
                f"对账表 {key}:表里写 {entry['mode']!r},但按 provenance.source 推出来是 "
                f"{actual!r} —— **两处必须一致才过闸 1**(有人改了 provenance 却没改表,"
                f"或反过来;恢复硬否决的唯一通道是裁定 6 的七项提交 → 用户确认 → 新引擎版本)")
        seen.add((version, gate, leaf_key))
    # 反向:引擎包里有、对账表里漏登记的 —— 同样拒(漏一条 = 那条阈值没人盯着)
    for version, pk in sorted(by_version.items()):
        for gate, leaf_key in GOVERNED_THRESHOLD_KEYS:
            if _gate_leaf(pk, gate, leaf_key) is None:
                continue
            if (version, gate, leaf_key) not in seen:
                errors.append(
                    f"对账表缺登记:{version}.{gate}.{leaf_key} 在引擎包里存在却没进表 "
                    "—— 漏登记的那条阈值就没人盯着它的 provenance 有没有被改过")
    return errors


def _tier_evidence_max(engine: Pack, tier_key: str, default: int) -> int:
    leaf = ((engine.config.get("engine") or {}).get("tier_evidence") or {}).get(tier_key, {})
    v = leaf.get("max_evidence_degrades") if isinstance(leaf, Mapping) else None
    if isinstance(v, Mapping):
        v = v.get("value")
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    return default


def _basket_industries(members: Sequence[Any]) -> List[str]:
    return sorted({m.industry for m in members if getattr(m, "industry", None)})


# ══════════════════════════════════════════════════════════════════════════
# 逐关判定(纯函数,吃 GateContext + 引擎包)
# ══════════════════════════════════════════════════════════════════════════

def _regime_of(ctx: GateContext) -> Optional[str]:
    if ctx.regime_row is None:
        return None
    return str(ctx.regime_row.get("regime") or "") or None


def _best_industry_rank(ctx: GateContext, industries: Sequence[str]) -> Tuple[Optional[int], str]:
    """本篮最好的(最小的)行业强度名次。`(None, 原因码)` = 算不出。"""
    if not ctx.industry_available:
        return None, "missing:industry_strength"
    ranks = [ctx.industry_rank[i] for i in industries if i in ctx.industry_rank]
    if not ranks:
        return None, "missing:industry_strength"
    return min(ranks), ""


def _best_strength_days(ctx: GateContext, industries: Sequence[str]) -> Tuple[Optional[int], str]:
    """本篮各行业里最高的「近 5 日强度日数」。`(None, 原因码)` = 算不出。

    🔴 **口径刻意取全部成员行业、⛔ 不只取「名次已过的那批」**(①-D 的施工要求):
    这条阈值问的是「这个篮子的板块近期强不强」,而不是「名次过了之后再看强不强」——
    后者会让它的单关通过率分母变成名次关的子集,与裁定 3 写死的分母对不上。
    ⚠ 同一个函数同时服务判定侧与影子侧,**一份读数一个来源**(⛔ 不许两处各算一遍)。"""
    if ctx.strength_days_window <= 0:
        return None, "missing:strength_days"
    if not industries:
        return None, "missing:strength_days"
    return max(ctx.strength_days_5d.get(i, 0) for i in industries), ""


def _basket_stages(ctx: GateContext, industries: Sequence[str]) -> Tuple[Tuple[str, ...], str]:
    if not ctx.stage_available:
        return (), "missing:industry_stage"
    stages = tuple(sorted({s for s in (ctx.stage_of.get(i) for i in industries) if s}))
    if not stages:
        return (), "missing:member_industry_stage"
    return stages, ""


def _evidence_verdict_check(
    gate: str, *, basket: Any, ev: Dict[str, Any], head: str,
    score: Optional[float] = None, threshold: Optional[float] = None,
) -> GateCheck:
    """一条 **`enforcement=evidence`** 的阈值没过 → 判定交 ①-C 的 LLM 三值
    (V2.3.2-①-C;⛔ **机械侧永不 reject**,裁定 1「只能降低候选等级,不能机械除名」)。

    三值后果与位置关 / 核心关**逐字相同**:`ok` → 该关 pass;`weak` → `VERDICT_DEGRADE`
    (计入 `evidence_degrades`,降一档);`unfit` → 退出正式候选(OUT),**仍在 ③b 列名**。

    🔴 **LLM 没给 / 给错 / 整段解析失败 → `available=False + blocks_t1=True`**
    (⛔ 不默认 `ok`:让模型的沉默换来 T1,等于拿"没有依据"当依据;⛔ 也不像位置 /
    核心关那样兜底成 `weak` —— 那两关兜底成 weak 是因为**读数本身还在**、只是判定缺席,
    这里连"这条待定阈值该不该拦"都没人答过,如实标「判不出」才诚实)。

    `head` = 机器可读的读数串(③b「差多少」直接用它),数值已内嵌。"""
    verdicts = MARKET_VERDICTS if gate == GATE_MARKET else SECTOR_VERDICTS
    unfit_value = MARKET_UNFIT if gate == GATE_MARKET else SECTOR_UNFIT
    raw = str(getattr(basket, f"{gate}_verdict", "") or "").strip().lower()
    reason_text = str(getattr(basket, f"{gate}_reason", "") or "").strip()

    ev = dict(ev)
    ev.update({
        "enforcement": ENFORCEMENT_EVIDENCE,
        "evidence_threshold_miss": head,
        f"{gate}_verdict_raw": raw,
        f"{gate}_reason": reason_text,
    })
    if raw not in verdicts:
        ev["verdict_missing"] = True
        return GateCheck(gate, VERDICT_PASS, score=score, threshold=threshold,
                         available=False, blocks_t1=True,
                         reason=f"{head};missing:{gate}_verdict", evidence=ev)

    ev[f"{gate}_verdict"] = raw
    reason_code = f"{head};{gate}.{raw}[llm]"
    if reason_text:
        reason_code += f":{reason_text}"
    if raw == (MARKET_VERDICTS[0] if gate == GATE_MARKET else SECTOR_VERDICTS[0]):  # ok
        return GateCheck(gate, VERDICT_PASS, score=score, threshold=threshold,
                         reason=reason_code, evidence=ev)
    ev["unfit"] = raw == unfit_value
    return GateCheck(gate, VERDICT_DEGRADE, score=score, threshold=threshold,
                     blocks_t1=True, reason=reason_code, evidence=ev)


def _gate_unfit(check: GateCheck) -> bool:
    """该关的 evidence 半边是否被 LLM 判 `unfit`(⛔ 只看留痕,不重解析一遍)。"""
    return bool((check.evidence or {}).get("unfit"))


def _market_gate(
    engine: Pack, ctx: GateContext, industries: Sequence[str], *, basket: Any = None,
) -> GateCheck:
    """① 市场关:读 `market_regime_daily.regime`,按引擎 `gates.market` 三态分支取门槛;
    缺行 = 不拦但不给 T1(plan ③-B 原文)。

    🔴 **V2.3.2-①-B 起本关是"半机械半证据"**:`source=audited` 的叶子
    (`Z1.market.trend_continuation_required_stages`)继续硬否决、行为一字不动;
    `engineering_v1` 的叶子(`high_divergence_min_breadth_pctile`)**不再 reject**,
    改由 ①-C 的篮子级 LLM 三值裁决(`basket.market_verdict`)。
    ⚠ `primary_regimes` 与 `rotation_confirmed_blocks_t1` 本就只降级不除名
    (`PASS + blocks_t1`),**本版零改动**(已拍板 #4)。"""
    if ctx.regime_row is None:
        return GateCheck(GATE_MARKET, VERDICT_PASS, available=False, blocks_t1=True,
                         reason="missing:market_regime")
    regime = str(ctx.regime_row.get("regime") or "")
    primary = _gate_value(engine, "market", "primary_regimes") or []
    ev: Dict[str, Any] = {"regime": regime, "primary_regimes": list(primary)}
    if regime in primary:
        return GateCheck(GATE_MARKET, VERDICT_PASS, reason=f"market.regime={regime}:primary",
                         evidence=ev)

    # 非主场:按引擎包声明的分支键逐条判(键名契约 = _ENGINE_GATE_SCHEMA)。
    if regime == "high_divergence":
        thr = _gate_value(engine, "market", "high_divergence_min_breadth_pctile")
        if isinstance(thr, (int, float)) and not isinstance(thr, bool):
            p = ctx.regime_breadth_pctile
            if p is None:
                return GateCheck(GATE_MARKET, VERDICT_PASS, available=False, blocks_t1=True,
                                 reason="missing:breadth_pctile", evidence=ev)
            ev["breadth_pctile"] = p
            if p >= float(thr) - _EPS:
                return GateCheck(GATE_MARKET, VERDICT_PASS, score=p, threshold=float(thr),
                                 reason=f"market.breadth_pctile={p:.4f}>={float(thr):.4f}",
                                 evidence=ev)
            head = f"market.breadth_pctile={p:.4f}<{float(thr):.4f}"
            if _enforcement(engine, "market", "high_divergence_min_breadth_pctile") == ENFORCEMENT_HARD:
                ev["enforcement"] = ENFORCEMENT_HARD
                return GateCheck(GATE_MARKET, VERDICT_REJECT, score=p, threshold=float(thr),
                                 reason=head, evidence=ev)
            return _evidence_verdict_check(GATE_MARKET, basket=basket, ev=ev, head=head,
                                           score=p, threshold=float(thr))
    if regime == "rotation_confirmed" and _gate_value(engine, "market", "rotation_confirmed_blocks_t1") is True:
        return GateCheck(GATE_MARKET, VERDICT_PASS, blocks_t1=True,
                         reason="market.rotation_confirmed_blocks_t1", evidence=ev)
    if regime == "trend_continuation":
        required = _gate_value(engine, "market", "trend_continuation_required_stages")
        if isinstance(required, list) and required:
            if not ctx.stage_available:
                return GateCheck(GATE_MARKET, VERDICT_PASS, available=False, blocks_t1=True,
                                 reason="missing:industry_stage", evidence=ev)
            stages = sorted({s for s in (ctx.stage_of.get(i) for i in industries) if s})
            ev["stages"] = stages
            if not stages:
                return GateCheck(GATE_MARKET, VERDICT_PASS, available=False, blocks_t1=True,
                                 reason="missing:member_industry_stage", evidence=ev)
            if set(stages) & set(required):
                return GateCheck(GATE_MARKET, VERDICT_PASS,
                                 reason=f"market.stage∈{sorted(required)}", evidence=ev)
            head = f"market.stage={stages}∉{sorted(required)}"
            # `Z1.market.trend_continuation_required_stages` 是 audited(已拍板 #3)
            # → 继续硬否决,行为一字不动;判据仍走 `enforcement_of`,⛔ 不硬编引擎名。
            if _enforcement(engine, "market", "trend_continuation_required_stages") == ENFORCEMENT_HARD:
                ev["enforcement"] = ENFORCEMENT_HARD
                return GateCheck(GATE_MARKET, VERDICT_REJECT, reason=head, evidence=ev)
            return _evidence_verdict_check(GATE_MARKET, basket=basket, ev=ev, head=head)

    # 非主场且引擎包没为该态声明分支 → 不拦但不给 T1(保守方向与「缺行」一致:
    # 该引擎的主场前提不成立,但包没说要拒 —— ⛔ 不替包发明一条拒绝规则)。
    return GateCheck(GATE_MARKET, VERDICT_PASS, blocks_t1=True,
                     reason=f"market.non_primary_regime:{regime}", evidence=ev)


def _sector_gate(
    engine: Pack, ctx: GateContext, industries: Sequence[str], pool_size: Optional[int],
    *, basket: Any = None,
) -> GateCheck:
    """③ 板块关:行业强度名次 / 近 5 日强度日 / 阶段态 / 簇成员数,按引擎包声明的键
    逐条判(哪个键在包里,哪条就生效)。

    🔴 **V2.3.2-①-B 起本关是"半机械半证据"**:`source=audited` 的两项
    (`C1/Y1.sector.industry_rank_max`、`Z1.sector.stage_allowed`)继续硬否决、行为
    一字不动;`engineering_v1` 的两项(`strength_days_min_5d`、`cluster_members_min`)
    **不再 reject**,改由 ①-C 的篮子级 LLM 三值裁决(`basket.sector_verdict`)。

    🔴 **evidence 项一律"记账不早退"**:硬门(audited)照旧遇拒即返,但 evidence 项
    没过时**只记进 `evidence_fails` 继续往下跑** —— 早退会把后面的 audited 硬门跳过去,
    那是把一道该拦的关悄悄关掉(⛔ 比不改还糟)。
    ⚠ `strength_days_min_5d` 的读数**已从 `industry_rank_max` 的通过分支里解耦出来**
    (见 `_best_strength_days` docstring):它现在按**全部成员行业**算,与影子台账同一
    个来源;否则它的单关通过率分母会悄悄变成「名次已过的那批」。"""
    ev: Dict[str, Any] = {"industries": list(industries), "seed_pool_size": pool_size}
    unavailable: List[str] = []
    evidence_fails: List[str] = []
    score: Optional[float] = None
    threshold: Optional[float] = None

    # —— ③-1 行业强度名次(C1=10 / Y1=30,两项均 audited → 硬否决,行为一字不动)——
    rank_max = _gate_value(engine, "sector", "industry_rank_max")
    if isinstance(rank_max, (int, float)) and not isinstance(rank_max, bool):
        best_rank, why = _best_industry_rank(ctx, industries)
        if best_rank is None:
            unavailable.append(why)
        else:
            ev["industry_rank"] = {i: ctx.industry_rank[i]
                                   for i in sorted(industries) if i in ctx.industry_rank}
            score = float(best_rank)
            threshold = float(rank_max)
            if best_rank > int(rank_max):
                head = f"sector.industry_rank={best_rank}>{int(rank_max)}"
                if _enforcement(engine, "sector", "industry_rank_max") == ENFORCEMENT_HARD:
                    ev["enforcement"] = ENFORCEMENT_HARD
                    return GateCheck(GATE_SECTOR, VERDICT_REJECT, score=score,
                                     threshold=threshold, reason=head, evidence=ev)
                evidence_fails.append(head)

    # —— ③-2 近 5 日强度日(C1=3,engineering_v1 → 证据模式)——
    sdays_min = _gate_value(engine, "sector", "strength_days_min_5d")
    if isinstance(sdays_min, (int, float)) and not isinstance(sdays_min, bool):
        best_days, why = _best_strength_days(ctx, industries)
        if best_days is None:
            unavailable.append(why)
        else:
            ev["strength_days_5d"] = {i: ctx.strength_days_5d.get(i, 0)
                                      for i in sorted(industries)}
            if best_days < int(sdays_min):
                head = f"sector.strength_days_5d={best_days}<{int(sdays_min)}"
                if _enforcement(engine, "sector", "strength_days_min_5d") == ENFORCEMENT_HARD:
                    ev["enforcement"] = ENFORCEMENT_HARD
                    return GateCheck(GATE_SECTOR, VERDICT_REJECT, score=float(best_days),
                                     threshold=float(sdays_min), reason=head, evidence=ev)
                evidence_fails.append(head)

    # —— ③-3 行业阶段态(Z1,audited → 硬否决,行为一字不动)——
    stage_allowed = _gate_value(engine, "sector", "stage_allowed")
    if isinstance(stage_allowed, list) and stage_allowed:
        stages, why = _basket_stages(ctx, industries)
        if why:
            unavailable.append(why)
        else:
            ev["stages"] = list(stages)
            if not (set(stages) & set(stage_allowed)):
                head = f"sector.stage={sorted(stages)}∉{sorted(stage_allowed)}"
                if _enforcement(engine, "sector", "stage_allowed") == ENFORCEMENT_HARD:
                    ev["enforcement"] = ENFORCEMENT_HARD
                    return GateCheck(GATE_SECTOR, VERDICT_REJECT, reason=head, evidence=ev)
                evidence_fails.append(head)

    # —— ③-4 簇成员数(Z1=3,engineering_v1 → 证据模式)——
    cluster_min = _gate_value(engine, "sector", "cluster_members_min")
    if isinstance(cluster_min, (int, float)) and not isinstance(cluster_min, bool):
        if pool_size is None:
            unavailable.append("missing:seed_pool_size")
        elif pool_size < int(cluster_min):
            head = f"sector.cluster_members={pool_size}<{int(cluster_min)}"
            if _enforcement(engine, "sector", "cluster_members_min") == ENFORCEMENT_HARD:
                ev["enforcement"] = ENFORCEMENT_HARD
                return GateCheck(GATE_SECTOR, VERDICT_REJECT, score=float(pool_size),
                                 threshold=float(cluster_min), reason=head, evidence=ev)
            evidence_fails.append(head)

    # 「判不出」优先于「证据项没过」:连输入都取不到时,让 LLM 去裁一个它也看不到读数
    # 的东西没有意义(缺数姿势六关统一,⛔ 不因本版改判而动摇)。
    if unavailable:
        if evidence_fails:
            ev["evidence_threshold_miss"] = ";".join(evidence_fails)
        return GateCheck(GATE_SECTOR, VERDICT_PASS, available=False, blocks_t1=True,
                         score=score, threshold=threshold,
                         reason=";".join(unavailable), evidence=ev)
    if evidence_fails:
        return _evidence_verdict_check(GATE_SECTOR, basket=basket, ev=ev,
                                       head=";".join(evidence_fails),
                                       score=score, threshold=threshold)
    return GateCheck(GATE_SECTOR, VERDICT_PASS, score=score, threshold=threshold,
                     reason="sector.ok", evidence=ev)


def collect_threshold_readings(
    engine: Pack, ctx: GateContext, industries: Sequence[str], pool_size: Optional[int],
) -> Tuple[ThresholdReading, ...]:
    """本候选在该引擎**全部 `enforcement=evidence` 阈值**上的读数与拟判(①-D 唯一实现)。

    🔴 **与关口判定完全解耦**:本函数不看「前面哪道硬门先拒了」,对每条 evidence 阈值
    照算照存 —— 这正是裁定 3 写死的分母(「进入市场关、板块关**之前**的召回候选」)
    唯一能落地的方式。⛔ 别为了省几行把它塞回 `_sector_gate` 的分支里。

    ⚠ **只对 evidence 叶子出行**:`audited` 那四项的判定已经在 `gate_evaluations` 里,
    再写一份 = 两个事实源。

    ⚠ **`not_applicable:` ≠ 缺数**:`high_divergence_min_breadth_pctile` 只在高位分歧态
    适用,`rotation_confirmed_blocks_t1` 只在切换确认态适用 —— 不适用的日子照样出行
    (行数可预测 = 候选数 × 该引擎 evidence 阈值数),但拟判为 `None` 且原因码带
    `not_applicable:` 前缀,①-E 据此按**适用域**出分母,⛔ 不拿全体候选把它稀释。"""
    regime = _regime_of(ctx)
    out: List[ThresholdReading] = []

    def _add(gate: str, key: str, **kw: Any) -> None:
        out.append(ThresholdReading(threshold_key=f"{gate}.{key}", gate=gate, **kw))

    def _na(gate: str, key: str) -> None:
        why = (f"{NOT_APPLICABLE_PREFIX}regime={regime}" if regime
               else "missing:market_regime")
        _add(gate, key, unavailable_reason=why)

    for gate, key in GOVERNED_THRESHOLD_KEYS:
        leaf = _gate_leaf(engine, gate, key)
        if leaf is None:
            continue                                  # 该引擎不设这道分支
        if enforcement_of(leaf) == ENFORCEMENT_HARD:
            continue                                  # audited:判定已在 gate_evaluations 里
        value = leaf.get("value") if isinstance(leaf, Mapping) else None

        if key == "primary_regimes":
            if regime is None:
                _add(gate, key, unavailable_reason="missing:market_regime")
            else:
                _add(gate, key, would_pass=regime in (value or []))
        elif key == "high_divergence_min_breadth_pctile":
            if regime != "high_divergence":
                _na(gate, key)
            elif not isinstance(value, (int, float)) or isinstance(value, bool):
                _add(gate, key, unavailable_reason="missing:threshold_value")
            elif ctx.regime_breadth_pctile is None:
                _add(gate, key, threshold_value=float(value),
                     unavailable_reason="missing:breadth_pctile")
            else:
                p = float(ctx.regime_breadth_pctile)
                _add(gate, key, reading=p, threshold_value=float(value),
                     would_pass=p >= float(value) - _EPS)
        elif key == "rotation_confirmed_blocks_t1":
            if value is not True:
                _add(gate, key, unavailable_reason=f"{NOT_APPLICABLE_PREFIX}flag_off")
            elif regime != "rotation_confirmed":
                _na(gate, key)
            else:
                # 「若按硬门跑」= 切换确认态直接否决 → 拟判恒为「本可否决」。
                _add(gate, key, would_pass=False)
        elif key == "trend_continuation_required_stages":
            if regime != "trend_continuation":
                _na(gate, key)
            else:
                stages, why = _basket_stages(ctx, industries)
                if why:
                    _add(gate, key, unavailable_reason=why)
                else:
                    _add(gate, key, would_pass=bool(set(stages) & set(value or [])))
        elif key == "industry_rank_max":
            best, why = _best_industry_rank(ctx, industries)
            if best is None:
                _add(gate, key, unavailable_reason=why)
            else:
                _add(gate, key, reading=float(best), threshold_value=float(value),
                     would_pass=best <= int(value))
        elif key == "strength_days_min_5d":
            best_days, why = _best_strength_days(ctx, industries)
            if best_days is None:
                _add(gate, key, unavailable_reason=why)
            else:
                _add(gate, key, reading=float(best_days), threshold_value=float(value),
                     would_pass=best_days >= int(value))
        elif key == "stage_allowed":
            stages, why = _basket_stages(ctx, industries)
            if why:
                _add(gate, key, unavailable_reason=why)
            else:
                _add(gate, key, would_pass=bool(set(stages) & set(value or [])))
        elif key == "cluster_members_min":
            if pool_size is None:
                _add(gate, key, threshold_value=float(value),
                     unavailable_reason="missing:seed_pool_size")
            else:
                _add(gate, key, reading=float(pool_size), threshold_value=float(value),
                     would_pass=pool_size >= int(value))
    return tuple(out)


def _position_member_check(engine: Pack, member: Any) -> Tuple[GateCheck, bool]:
    """⑤ 位置关(**证据关**,成员级;🔴 裁定 #11 整节重写)。

    判定不在这里做 —— 它由 ⑤ `basket_reason` 那**一次**调用给出
    (`BasketMemberCandidate.position_verdict`),本函数只做三件事:
    ① 三值 → 三态映射(`ok`→pass / `weak`→degrade / `unfit`→degrade + 退出正式候选);
    ② 把**当次读数 + LLM 理由**一起塞进 `evidence`(→ `gate_evaluations.evidence_json`,
       plan ③-C 末段的硬要求);③ 读数整份缺席时标 `available=False` + `blocks_t1`
    (「判不出」既不是「判过了」也不是「拦下来」——⛔ 但它挡 T1:让 LLM 在零读数下
    给的 `ok` 直接换来 T1,等于拿"没有依据"当依据)。

    返回 `(check, unfit)`。**⛔ 永不返回 reject、永不让成员出篮** —— 位置关是证据关,
    第 4 锁「LLM 不做闸门」在这里必须完好(裁定 11-b,用户在两条自己的裁定冲突时
    选的那一边)。`unfit` 的后果发生在**定档层**(`t2_eligible=False` → ③b 列名)。"""
    code = getattr(member, "ts_code", "") or ""
    engine_code = str((engine.config.get("engine") or {}).get("engine_code") or "")
    guidance = str(((engine.config.get("engine") or {}).get("gates") or {})
                   .get("position", {}).get("guidance") or "")

    raw_verdict = str(getattr(member, "position_verdict", "") or "").strip().lower()
    reason_text = str(getattr(member, "position_reason", "") or "").strip()
    metrics = getattr(member, "position_metrics", None)
    metrics_missing = str(getattr(member, "position_metrics_missing", "") or "")

    verdict_llm = raw_verdict if raw_verdict in POSITION_VERDICTS else POSITION_VERDICT_FALLBACK
    # 🔴 evidence_json 的两样必需品:**当次读数** + **LLM 理由**。缺读数如实标
    # `metrics_available=False`,⛔ 不补 0、不补默认值。
    ev: Dict[str, Any] = {
        "position_verdict": verdict_llm,
        "position_verdict_raw": raw_verdict,
        "position_reason": reason_text,
        "metrics_available": metrics is not None,
        "metrics": dict(metrics) if isinstance(metrics, Mapping) else None,
        "metrics_missing": metrics_missing,
        "engine_code": engine_code,
        "position_guidance": guidance,
    }
    if raw_verdict not in POSITION_VERDICTS:
        ev["verdict_fallback"] = True

    reason_code = f"position.{verdict_llm}[{engine_code}]"
    if reason_text:
        reason_code += f":{reason_text}"
    metrics_absent = metrics is None

    if verdict_llm == POSITION_OK:
        return (GateCheck(GATE_POSITION, VERDICT_PASS, ts_code=code,
                          available=not metrics_absent, blocks_t1=metrics_absent,
                          reason=(reason_code if not metrics_absent
                                  else reason_code + ";missing:position_metrics"),
                          evidence=ev), False)
    unfit = verdict_llm == POSITION_UNFIT
    return (GateCheck(GATE_POSITION, VERDICT_DEGRADE, ts_code=code,
                      available=not metrics_absent, blocks_t1=True,
                      reason=reason_code, evidence=ev), unfit)


def _core_member_check(engine: Pack, member: Any) -> Tuple[GateCheck, bool]:
    """④ 核心关(**证据关**,成员级;🔴 裁定 #12 整段重写,与位置关完全同构)。

    判定不在这里做 —— 它由 ⑤ `basket_reason` 那**一次**调用给出
    (`BasketMemberCandidate.core_verdict`),本函数只做三件事:
    ① 三值 → 三态映射(`ok`→pass / `weak`→degrade / `unfit`→degrade + 退出正式候选);
    ② 把**当次读数 + LLM 理由**一起塞进 `evidence`(→ `gate_evaluations.evidence_json`);
    ③ 读数整份缺席时标 `available=False` + `blocks_t1`(「判不出」既不是「判过了」
       也不是「拦下来」——⛔ 但它挡 T1:让 LLM 在零读数下给的 `ok` 直接换来 T1,
       等于拿"没有依据"当依据)。

    🔴 **本函数零阈值、零及格线**(裁定 12-b,含「行业内前 X%」这类,用户明确否决)
    —— `score`/`threshold` 恒为 `None`,原 `leader_rs_rank_max` 已从包 schema 删除。
    ⚠ 成员上的 `rs_rank`(簇内名次)**不再是任何判据**:它降级为读数,随
    `core_metrics.cluster_rs_rank` 一起进 evidence,**缺席不挡任何档**(裁定 12-a)。

    返回 `(check, unfit)`。**⛔ 永不返回 reject、永不让成员出篮** —— 核心关是证据关,
    第 4 锁「LLM 不做闸门」在这里必须完好。`unfit` 的后果发生在**定档层**
    (`t2_eligible=False` → ③b 列名)。"""
    code = getattr(member, "ts_code", "") or ""
    engine_code = str((engine.config.get("engine") or {}).get("engine_code") or "")
    guidance = str(((engine.config.get("engine") or {}).get("gates") or {})
                   .get("core", {}).get("guidance") or "")

    raw_verdict = str(getattr(member, "core_verdict", "") or "").strip().lower()
    reason_text = str(getattr(member, "core_reason", "") or "").strip()
    metrics = getattr(member, "core_metrics", None)
    metrics_missing = str(getattr(member, "core_metrics_missing", "") or "")

    verdict_llm = raw_verdict if raw_verdict in CORE_VERDICTS else CORE_VERDICT_FALLBACK
    # 🔴 evidence_json 的两样必需品:**当次读数** + **LLM 理由**。缺读数如实标
    # `metrics_available=False`,⛔ 不补 0、不补默认值。
    ev: Dict[str, Any] = {
        "core_verdict": verdict_llm,
        "core_verdict_raw": raw_verdict,
        "core_reason": reason_text,
        "metrics_available": metrics is not None,
        "metrics": dict(metrics) if isinstance(metrics, Mapping) else None,
        "metrics_missing": metrics_missing,
        "engine_code": engine_code,
        "core_guidance": guidance,
    }
    if raw_verdict not in CORE_VERDICTS:
        ev["verdict_fallback"] = True

    reason_code = f"core.{verdict_llm}[{engine_code}]"
    if reason_text:
        reason_code += f":{reason_text}"
    metrics_absent = metrics is None

    if verdict_llm == CORE_OK:
        return (GateCheck(GATE_CORE, VERDICT_PASS, ts_code=code,
                          available=not metrics_absent, blocks_t1=metrics_absent,
                          reason=(reason_code if not metrics_absent
                                  else reason_code + ";missing:core_metrics"),
                          evidence=ev), False)
    unfit = verdict_llm == CORE_UNFIT
    return (GateCheck(GATE_CORE, VERDICT_DEGRADE, ts_code=code,
                      available=not metrics_absent, blocks_t1=True,
                      reason=reason_code, evidence=ev), unfit)


def _driver_gate(basket: Any) -> GateCheck:
    """② 驱动关(证据关):机械低保 = 驱动有 ≥1 条带日期的证据条目(检索缺席时
    判不了 → 不拦不给 T1);LLM 侧 = 四问必答(K8 §五-2),缺任一 → degrade。"""
    ev: Dict[str, Any] = {"evidence_status": basket.evidence_status}
    if basket.evidence_status == "search_unavailable":
        return GateCheck(GATE_DRIVER, VERDICT_PASS, available=False, blocks_t1=True,
                         reason="missing:driver_evidence", evidence=ev)
    fails: List[str] = []
    n = len(basket.evidence)
    ev["evidence_count"] = n
    if n < 1:
        fails.append("driver.evidence_count=0<1")
    answers = {
        "why_now": getattr(basket, "why_now", ""),
        "common_trait": getattr(basket, "common_trait", ""),
        "persistence": getattr(basket, "persistence", ""),
        "strengthen_and_invalidate": getattr(basket, "strengthen_and_invalidate", ""),
    }
    missing = sorted(k for k, v in answers.items() if not str(v or "").strip())
    ev["missing_answers"] = missing
    if missing:
        fails.append("driver.missing_answers:" + ",".join(missing))
    if fails:
        return GateCheck(GATE_DRIVER, VERDICT_DEGRADE, score=float(n), threshold=1.0,
                         reason=";".join(fails), evidence=ev)
    return GateCheck(GATE_DRIVER, VERDICT_PASS, score=float(n), threshold=1.0,
                     reason="driver.ok", evidence=ev)


def _evidence_gate(engine: Pack, basket: Any) -> GateCheck:
    """⑥ 证据关(证据关):证据条目按 `evidence_kind` 归并去重后 ≥
    `gates.evidence.independent_evidence_min`(同 kind 只计一份,技术指标折一份);
    Z1 另要求含一份消息/政策类来源。LLM 侧的矛盾识别(`evidence_conflicts`)只
    披露、不进判据(③-B 未给它机械判据,⛔ 不替 plan 发明一条)。"""
    ev: Dict[str, Any] = {"evidence_status": basket.evidence_status}
    if basket.evidence_status == "search_unavailable":
        return GateCheck(GATE_EVIDENCE, VERDICT_PASS, available=False, blocks_t1=True,
                         reason="missing:evidence", evidence=ev)
    kinds, has_news_policy = independent_evidence_kinds(basket.evidence)
    conflicts = str(getattr(basket, "evidence_conflicts", "") or "").strip()
    ev.update({"kinds": list(kinds), "has_news_policy_source": has_news_policy,
               "conflicts_noted": bool(conflicts)})
    fails: List[str] = []
    min_ind = _gate_value(engine, "evidence", "independent_evidence_min")
    threshold = None
    if isinstance(min_ind, (int, float)) and not isinstance(min_ind, bool):
        threshold = float(min_ind)
        if len(kinds) < int(min_ind):
            fails.append(f"evidence.independent={len(kinds)}<{int(min_ind)}")
    if _gate_value(engine, "evidence", "require_news_policy_source") is True and not has_news_policy:
        fails.append("evidence.no_news_policy_source")
    if fails:
        return GateCheck(GATE_EVIDENCE, VERDICT_DEGRADE, score=float(len(kinds)),
                         threshold=threshold, reason=";".join(fails), evidence=ev)
    return GateCheck(GATE_EVIDENCE, VERDICT_PASS, score=float(len(kinds)),
                     threshold=threshold, reason="evidence.ok", evidence=ev)


# ══════════════════════════════════════════════════════════════════════════
# 单引擎全套评估 + 引擎解析
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class _EngineEval:
    checks: Tuple[GateCheck, ...]
    kept_members: Tuple[Any, ...]
    removed: Tuple[MemberRemoval, ...]
    mech_reject_check: Optional[GateCheck]   # 篮子级机械关(市场/板块)首个 reject
    position_unfit: bool                     # 任一成员 `position_verdict='unfit'`
    position_unfit_detail: str = ""
    core_unfit: bool = False                 # 任一成员 `core_verdict='unfit'`(裁定 #12)
    core_unfit_detail: str = ""
    market_unfit: bool = False               # 市场关 evidence 半边被判 `unfit`(①-C)
    market_unfit_detail: str = ""
    sector_unfit: bool = False               # 板块关 evidence 半边被判 `unfit`(①-C)
    sector_unfit_detail: str = ""
    threshold_readings: Tuple[ThresholdReading, ...] = ()


def _evaluate_under_engine(basket: Any, engine: Pack, ctx: GateContext) -> _EngineEval:
    industries = _basket_industries(basket.members)
    pool_size = None
    raw_pool = (basket.aux or {}).get("seed_pool_size") if hasattr(basket, "aux") else None
    if isinstance(raw_pool, int) and not isinstance(raw_pool, bool):
        pool_size = raw_pool

    checks: List[GateCheck] = []
    market = _market_gate(engine, ctx, industries, basket=basket)
    sector = _sector_gate(engine, ctx, industries, pool_size, basket=basket)
    checks.append(market)
    checks.append(_driver_gate(basket))
    checks.append(sector)

    kept: List[Any] = []
    removed: List[MemberRemoval] = []
    pos_unfit_notes: List[str] = []
    core_unfit_notes: List[str] = []
    for m in basket.members:
        core_check, core_is_unfit = _core_member_check(engine, m)
        checks.append(core_check)
        pos_check, is_unfit = _position_member_check(engine, m)
        checks.append(pos_check)
        # ⚠ 裁定 #11 / #12:位置关与核心关**都永不让成员出篮**(证据关只降级)。
        # `kept` 因此恒等于全体成员 —— 成员出篮的通路留着,但当前没有成员级机械关
        # 会触发它。
        kept.append(m)
        if is_unfit:
            pos_unfit_notes.append(f"{m.ts_code}:{pos_check.reason}")
        if core_is_unfit:
            core_unfit_notes.append(f"{m.ts_code}:{core_check.reason}")

    checks.append(_evidence_gate(engine, basket))

    mech_reject = next((c for c in (market, sector) if c.verdict == VERDICT_REJECT), None)
    return _EngineEval(
        checks=tuple(checks), kept_members=tuple(kept), removed=tuple(removed),
        mech_reject_check=mech_reject,
        position_unfit=bool(pos_unfit_notes),
        position_unfit_detail=";".join(pos_unfit_notes),
        core_unfit=bool(core_unfit_notes),
        core_unfit_detail=";".join(core_unfit_notes),
        market_unfit=_gate_unfit(market), market_unfit_detail=market.reason,
        sector_unfit=_gate_unfit(sector), sector_unfit_detail=sector.reason,
        # 🔴 读数**独立算一遍**,与上面两关"哪道硬门先拒了"无关(①-D 分母口径)。
        threshold_readings=collect_threshold_readings(engine, ctx, industries, pool_size),
    )


def _fits(ev: _EngineEval) -> bool:
    """机械兜底的「该引擎装得下这个篮子」判据:**篮子级机械关(市场/板块)不拒**。
    ⛔ 不看证据关 —— 证据关只降级,不该影响归属(裁定 #11 后位置关也在其中,
    故位置判定**不参与**引擎归属选择:LLM 判位置不佳不代表该篮该换个引擎)。

    ⚠ **V2.3.2-①-B 登记:本判据随之变松,这是预期效果不是 bug**。三项
    (`high_divergence_min_breadth_pctile` / `strength_days_min_5d` /
    `cluster_members_min`)退出硬否决之后,能让本判据返回 `False` 的只剩四项
    `source=audited` 的叶子 → 机械兜底会更容易给出一个归属。裁定 1 的预期后果就是
    「联合门槛变松、T1/T2 数量可能上升」,⛔ 别为了"收紧回去"给它补一条及格线。"""
    return ev.mech_reject_check is None and bool(ev.kept_members)


# ══════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════

def evaluate_day(
    result: Any,
    trade_date: date,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    engines: Optional[Mapping[str, Pack]] = None,
    skeleton: Optional[Pack] = None,
    context: Optional[GateContext] = None,
) -> GateDayOutcome:
    """六道关口一天跑一遍:引擎归属对拍(成员出篮)→ 逐关判定 → 汇总。

    `engines` / `skeleton`:默认读库现役行(`get_active_engines` / `get_active_
    skeleton`);测试可直接注入 `Pack` 替身(同 `tier.score_and_tier(pack=…)` 姿势)。
    **零 LLM 调用、只读表**;返回的 `outcome.result` 是对拍后的 `AggregateResult`
    (被除名候选已摘除 —— 它们在 `summaries` 里留痕,⑥ 负责转成 ③b 行)。"""
    trade_date_s = _d(trade_date)
    notes: List[str] = []
    if engines is None:
        engines = get_active_engines(db_path)
    engine_codes = tuple(engines.keys())

    codes = sorted({m.ts_code for b in result.baskets for m in b.members})
    ctx = context if context is not None else build_gate_context(
        trade_date, codes, db_path=db_path, parquet_dir=parquet_dir, skeleton=skeleton,
    )

    summaries: Dict[str, BasketGateSummary] = {}
    kept_baskets: List[Any] = []

    if not engines:
        # 零运行引擎 = 当日不产任何候选(`pack.get_active_line` docstring 既定语义;
        # 候选**不消失**,全部按 no_active_engine 落 ③b)。
        notes.append(EXCLUDE_NO_ACTIVE_ENGINE)
        for b in result.baskets:
            summaries[b.basket_key] = BasketGateSummary(
                basket_key=b.basket_key, name=b.name,
                skeleton_version=ctx.skeleton_version,
                excluded=True, exclusion_reason=EXCLUDE_NO_ACTIVE_ENGINE,
                stuck_detail="无运行中的引擎线(C/Z/Y 均未激活)",
                regime_available=ctx.regime_row is not None,
            )
        gated = dc_replace(result, baskets=())
        return GateDayOutcome(trade_date=trade_date_s, result=gated, summaries=summaries,
                              engines=engine_codes, skeleton_version=ctx.skeleton_version,
                              notes=tuple(notes))

    for b in result.baskets:
        # —— 引擎解析(LLM 主张优先,机械对拍是裁判;缺席/给错 → C→Z→Y 兜底)——
        engine_code: Optional[str] = None
        engine_pack: Optional[Pack] = None
        source: Optional[str] = None
        ev: Optional[_EngineEval] = None
        if b.engine_code_llm in engines:
            engine_code, engine_pack, source = b.engine_code_llm, engines[b.engine_code_llm], ENGINE_SOURCE_LLM
            ev = _evaluate_under_engine(b, engine_pack, ctx)
        else:
            if b.engine_code_llm:
                logger.warning(
                    "[gates] 篮子 %r 的 LLM 引擎主张 %r 不在运行中引擎 %s 内,走机械兜底",
                    b.name, b.engine_code_llm, list(engines),
                )
            for code in engines:            # get_active_engines 已按 C→Z→Y 确定性序
                trial = _evaluate_under_engine(b, engines[code], ctx)
                if _fits(trial):
                    engine_code, engine_pack, source, ev = code, engines[code], ENGINE_SOURCE_MECH_FALLBACK, trial
                    break
            if ev is None:
                summaries[b.basket_key] = BasketGateSummary(
                    basket_key=b.basket_key, name=b.name,
                    skeleton_version=ctx.skeleton_version,
                    excluded=True, exclusion_reason=EXCLUDE_ENGINE_UNRESOLVED,
                    stuck_detail=(f"LLM 引擎主张 {b.engine_code_llm or '缺失'};"
                                  f"C→Z→Y 机械兜底下无引擎可容纳该篮"),
                    regime_available=ctx.regime_row is not None,
                )
                continue

        assert engine_pack is not None and ev is not None
        engine_version = engine_pack.pack_version
        degraded_gates = tuple(sorted({c.gate for c in ev.checks
                                       if c.verdict == VERDICT_DEGRADE}))
        blocks_reasons = tuple(c.reason for c in ev.checks
                               if c.blocks_t1 and c.ts_code is None) + tuple(
            c.reason for c in ev.checks
            if c.blocks_t1 and c.ts_code is not None
            and all(r.ts_code != c.ts_code for r in ev.removed))
        base = dict(
            basket_key=b.basket_key, name=b.name,
            engine_code=engine_code, engine_version=engine_version,
            skeleton_version=ctx.skeleton_version, engine_source=source,
            checks=ev.checks, removed_members=ev.removed,
            kept_member_codes=tuple(m.ts_code for m in ev.kept_members),
            evidence_degrades=len(degraded_gates),
            degraded_gates=degraded_gates,
            blocks_t1=bool(blocks_reasons),
            blocks_t1_reasons=blocks_reasons,
            position_unfit=ev.position_unfit,
            position_unfit_detail=ev.position_unfit_detail,
            core_unfit=ev.core_unfit,
            core_unfit_detail=ev.core_unfit_detail,
            market_unfit=ev.market_unfit,
            market_unfit_detail=ev.market_unfit_detail,
            sector_unfit=ev.sector_unfit,
            sector_unfit_detail=ev.sector_unfit_detail,
            threshold_readings=ev.threshold_readings,
            regime_available=ctx.regime_row is not None,
            t1_max_evidence_degrades=_tier_evidence_max(
                engine_pack, "t1", T1_MAX_EVIDENCE_DEGRADES_DEFAULT),
            t2_max_evidence_degrades=_tier_evidence_max(
                engine_pack, "t2", T2_MAX_EVIDENCE_DEGRADES_DEFAULT),
        )
        if ev.mech_reject_check is not None:
            c = ev.mech_reject_check
            summaries[b.basket_key] = BasketGateSummary(
                **base, excluded=True, exclusion_reason=EXCLUDE_MECH_GATE_REJECTED,
                stuck_gate=c.gate, stuck_detail=c.reason,
            )
            continue
        if not ev.kept_members:
            detail = ";".join(f"{r.ts_code}:{r.reason}" for r in ev.removed) or "成员全部出篮"
            summaries[b.basket_key] = BasketGateSummary(
                **base, excluded=True, exclusion_reason=EXCLUDE_MEMBERS_ALL_REMOVED,
                stuck_gate=GATE_POSITION, stuck_detail=detail,
            )
            continue

        summaries[b.basket_key] = BasketGateSummary(**base)
        kept_baskets.append(dc_replace(
            b, members=ev.kept_members,
            engine_code=engine_code, engine_version=engine_version,
            skeleton_version=ctx.skeleton_version, engine_source=source,
        ))
        if ev.removed:
            logger.warning(
                "[gates] 篮子 %r(引擎 %s)有 %d 名成员未过位置对拍,出篮:%s",
                b.name, engine_code, len(ev.removed),
                [(r.ts_code, r.reason) for r in ev.removed],
            )

    kept_baskets = _repair_primary(kept_baskets)
    gated = dc_replace(result, baskets=tuple(kept_baskets))
    return GateDayOutcome(trade_date=trade_date_s, result=gated, summaries=summaries,
                          engines=engine_codes, skeleton_version=ctx.skeleton_version,
                          notes=tuple(notes))


def _repair_primary(baskets: List[Any]) -> List[Any]:
    """成员出篮可能带走某只票的 `is_primary=1` 行(它的主篮把它删了,别的篮还留着
    它)。留一只「无主归属」的票会让 ⑩ 开仓来源与 ⑨ 归因少一条主线索 —— 按
    `basket_key` 升序把第一个仍持有该票的篮提升为主归属(确定性;`primary_reason`
    沿用兜底码,登记于交回)。"""
    has_primary: Dict[str, bool] = {}
    appears: Dict[str, List[Tuple[str, int, int]]] = {}
    for bi, b in enumerate(baskets):
        for mi, m in enumerate(b.members):
            appears.setdefault(m.ts_code, []).append((b.basket_key, bi, mi))
            if m.is_primary:
                has_primary[m.ts_code] = True
    orphans = [c for c in appears if not has_primary.get(c)]
    if not orphans:
        return baskets
    out = list(baskets)
    for code in sorted(orphans):
        _key, bi, mi = sorted(appears[code])[0]
        b = out[bi]
        members = list(b.members)
        members[mi] = dc_replace(members[mi], is_primary=1,
                                 primary_reason="fallback_no_qualified_lift")
        out[bi] = dc_replace(b, members=tuple(members))
        logger.warning("[gates] %s 的主归属篮成员被位置对拍移除,按 basket_key 升序"
                       "提升 %s 为主归属(确定性兜底)", code, _key)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 留痕(gate_evaluations,append-only)
# ══════════════════════════════════════════════════════════════════════════

_COLUMNS = ("trade_date, candidate_key, ts_code, gate, gate_kind, verdict, score, "
            "threshold, engine_code, engine_version, evidence_json, created_at")


def save_gate_evaluations(
    outcome: GateDayOutcome, *, db_path: Optional[Path] = None,
) -> int:
    """把一天的关口判定行写进 `gate_evaluations`(**append-only**:同日重跑 = 追加
    新批次,`created_at` 区分;审计表不做覆盖 —— 「上一次怎么判的」本身是审计对象)。
    返回写入行数。被除名候选的行**照写**(它们正是 ③b 与 ④ 归因最需要的)。"""
    now = _now()
    rows: List[tuple] = []
    for key in sorted(outcome.summaries):
        s = outcome.summaries[key]
        for c in s.checks:
            ev = dict(c.evidence)
            ev["reason"] = c.reason
            if not c.available:
                ev["available"] = False
            rows.append((
                outcome.trade_date, key, c.ts_code, c.gate, c.gate_kind, c.verdict,
                c.score, c.threshold, s.engine_code, s.engine_version,
                json.dumps(ev, ensure_ascii=False, sort_keys=True), now,
            ))
    if not rows:
        return 0
    init_schema(db_path)
    with connection(db_path) as conn:
        conn.executemany(
            f"INSERT INTO {TABLE} ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows,
        )
    return len(rows)


def load_gate_evaluations(
    trade_date: date | str, *, candidate_key: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """读某日关口判定行(升序 id = 写入序;回放 / ④ 周度归因用)。"""
    day = trade_date if isinstance(trade_date, str) else _d(trade_date)
    sql = (f"SELECT id, {_COLUMNS} FROM {TABLE} WHERE trade_date=?")
    args: List[Any] = [day]
    if candidate_key:
        sql += " AND candidate_key=?"
        args.append(candidate_key)
    sql += " ORDER BY id ASC"
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    keys = ["id"] + [c.strip() for c in _COLUMNS.split(",")]
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(zip(keys, r))
        try:
            d["evidence"] = json.loads(d.pop("evidence_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["evidence"] = {}
        out.append(d)
    return out


__all__ = [
    "TABLE",
    "GATE_MARKET", "GATE_DRIVER", "GATE_SECTOR", "GATE_CORE", "GATE_POSITION", "GATE_EVIDENCE",
    "GATE_ORDER", "MECH_GATES", "EVIDENCE_GATES", "MEMBER_LEVEL_GATES",
    "GATE_KIND_MECH", "GATE_KIND_LLM", "GATE_KIND_OF", "GATE_LABELS",
    "VERDICT_PASS", "VERDICT_DEGRADE", "VERDICT_REJECT",
    "ENFORCEMENT_HARD", "ENFORCEMENT_EVIDENCE", "PROVENANCE_SOURCE_AUDITED",
    "GOVERNED_THRESHOLD_KEYS", "NOT_APPLICABLE_PREFIX", "enforcement_of",
    "describe_evidence_thresholds", "check_threshold_governance",
    "ENGINE_SOURCE_LLM", "ENGINE_SOURCE_MECH_FALLBACK",
    "EXCLUDE_NO_ACTIVE_ENGINE", "EXCLUDE_ENGINE_UNRESOLVED",
    "EXCLUDE_MECH_GATE_REJECTED", "EXCLUDE_MEMBERS_ALL_REMOVED",
    "classify_evidence_kind", "independent_evidence_kinds",
    "GateCheck", "MemberRemoval", "ThresholdReading", "BasketGateSummary",
    "GateDayOutcome", "GateContext",
    "build_gate_context", "evaluate_day", "collect_threshold_readings",
    "save_gate_evaluations", "load_gate_evaluations",
]
