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
成员 `reason` / 角色 / **`position_verdict` + `position_reason` + 当次读数**),本模块
只做归并与留痕。三引擎并跑只体现在**阈值分支**上,⛔ 不体现在调用次数上。

**留痕**:`gate_evaluations`(append-only,每候选每关一行;成员级关口〔核心/位置〕
每成员一行)。写入口只有 `save_gate_evaluations()`,零 UPDATE/DELETE。
🔴 **位置关行的硬要求(plan ③-C 末段)**:`gate_kind='llm'` 且 `evidence_json`
**必须同时存下当次读数与 LLM 理由** —— 判定不再是一组可回放的数字而是一段模型输出,
不把这两样存在一起,事后无法复核它到底在拿什么下判断(P3-49 的证伪义务不减反增)。
⚠ 读数取自 `BasketMemberCandidate.position_metrics`(= **当次喂进 prompt 的那一份**),
⛔ 本模块不另读一遍 `landing_metrics_daily` —— 另读会存下「事后那一份」,与模型当时
看到的可能不是同一份,留痕就白留了。

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
读数由 ⑤ 随成员带进来,本模块不碰那张表)。
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
from neckline.selection.aggregate import (
    POSITION_OK,
    POSITION_UNFIT,
    POSITION_VERDICT_FALLBACK,
    POSITION_VERDICTS,
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
    regime_available: bool = False
    t1_max_evidence_degrades: int = T1_MAX_EVIDENCE_DEGRADES_DEFAULT
    t2_max_evidence_degrades: int = T2_MAX_EVIDENCE_DEGRADES_DEFAULT

    @property
    def t1_eligible(self) -> bool:
        """③-D 的 T1:**机械关 ①③ pass + 证据关 ②④⑤⑥ 全 pass(含
        `position_verdict=ok`)** + 全部输入可得 + `market_regime` 可得。
        (「四件套齐」在 ⑦ 卡生成之后才验,见 `tier.enforce_plan_completeness`。)

        🔴 裁定 #11:**⛔ 不再要求任何机械态枚举** —— 原「全员 `liftoff_confirmed`」
        那一条正是把 T1 掐成近乎不可达的东西(实测 14 个 D0 回放零 T1),已整体作废。"""
        return (
            not self.excluded
            and not self.position_unfit
            and self.evidence_degrades <= self.t1_max_evidence_degrades
            and not self.degraded_gates
            and not self.blocks_t1
            and self.regime_available
        )

    @property
    def t2_eligible(self) -> bool:
        """③-D 的 T2:机械关全过 且 证据关(**含位置关**)降级处数 ≤ 引擎 T2 上限。
        任一成员位置 `unfit` → 退出正式候选(⛔ 但票仍进 ③b,不消失)。"""
        return (not self.excluded
                and not self.position_unfit
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


def build_gate_context(
    trade_date: date,
    codes: Sequence[str],                 # noqa: ARG001  —— 裁定 #11 后六关无成员级取数
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,   # noqa: ARG001  —— 签名对齐管线;当前六关全部只读表
    skeleton: Optional[Pack] = None,
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

    return ctx


# ══════════════════════════════════════════════════════════════════════════
# 引擎阈值读取(键名契约 = pack._ENGINE_GATE_SCHEMA;缺键 = 该引擎不设这道分支)
# ══════════════════════════════════════════════════════════════════════════

def _gate_value(engine: Pack, section: str, key: str) -> Any:
    """`config.engine.gates.<section>.<key>.value`;缺键/形状不对 → `None`
    (闸 1 已在激活时校验过形状,这里的宽容只服务于测试替身与历史行)。"""
    leaf = ((engine.config.get("engine") or {}).get("gates") or {}).get(section, {}).get(key)
    if isinstance(leaf, Mapping):
        return leaf.get("value")
    return None


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

def _market_gate(engine: Pack, ctx: GateContext, industries: Sequence[str]) -> GateCheck:
    """① 市场关(机械):读 `market_regime_daily.regime`,按引擎 `gates.market`
    三态分支取门槛;缺行 = 不拦但不给 T1(plan ③-B 原文)。"""
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
            return GateCheck(GATE_MARKET, VERDICT_REJECT, score=p, threshold=float(thr),
                             reason=f"market.breadth_pctile={p:.4f}<{float(thr):.4f}",
                             evidence=ev)
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
            return GateCheck(
                GATE_MARKET, VERDICT_REJECT,
                reason=f"market.stage={stages}∉{sorted(required)}", evidence=ev)

    # 非主场且引擎包没为该态声明分支 → 不拦但不给 T1(保守方向与「缺行」一致:
    # 该引擎的主场前提不成立,但包没说要拒 —— ⛔ 不替包发明一条拒绝规则)。
    return GateCheck(GATE_MARKET, VERDICT_PASS, blocks_t1=True,
                     reason=f"market.non_primary_regime:{regime}", evidence=ev)


def _sector_gate(
    engine: Pack, ctx: GateContext, industries: Sequence[str], pool_size: Optional[int],
) -> GateCheck:
    """③ 板块关(机械):行业强度名次 / 近 5 日强度日 / 阶段态 / 簇成员数,按引擎
    包声明的键逐条判(哪个键在包里,哪条就生效)。"""
    ev: Dict[str, Any] = {"industries": list(industries), "seed_pool_size": pool_size}
    unavailable: List[str] = []
    score: Optional[float] = None
    threshold: Optional[float] = None

    rank_max = _gate_value(engine, "sector", "industry_rank_max")
    sdays_min = _gate_value(engine, "sector", "strength_days_min_5d")
    if isinstance(rank_max, (int, float)) and not isinstance(rank_max, bool):
        ranks = {i: ctx.industry_rank[i] for i in industries if i in ctx.industry_rank}
        if not ctx.industry_available or not ranks:
            unavailable.append("missing:industry_strength")
        else:
            best_ind = min(ranks, key=lambda i: (ranks[i], i))
            ev["industry_rank"] = {i: ranks[i] for i in sorted(ranks)}
            score = float(ranks[best_ind])
            threshold = float(rank_max)
            qualified = [i for i in ranks if ranks[i] <= int(rank_max)]
            if not qualified:
                return GateCheck(
                    GATE_SECTOR, VERDICT_REJECT, score=score, threshold=threshold,
                    reason=f"sector.industry_rank={int(ranks[best_ind])}>{int(rank_max)}",
                    evidence=ev)
            if isinstance(sdays_min, (int, float)) and not isinstance(sdays_min, bool):
                if ctx.strength_days_window <= 0:
                    unavailable.append("missing:strength_days")
                else:
                    ev["strength_days_5d"] = {
                        i: ctx.strength_days_5d.get(i, 0) for i in sorted(qualified)
                    }
                    ok = [i for i in qualified
                          if ctx.strength_days_5d.get(i, 0) >= int(sdays_min)]
                    if not ok:
                        best_days = max(ctx.strength_days_5d.get(i, 0) for i in qualified)
                        return GateCheck(
                            GATE_SECTOR, VERDICT_REJECT,
                            score=float(best_days), threshold=float(sdays_min),
                            reason=f"sector.strength_days_5d={best_days}<{int(sdays_min)}",
                            evidence=ev)

    stage_allowed = _gate_value(engine, "sector", "stage_allowed")
    if isinstance(stage_allowed, list) and stage_allowed:
        if not ctx.stage_available:
            unavailable.append("missing:industry_stage")
        else:
            stages = sorted({s for s in (ctx.stage_of.get(i) for i in industries) if s})
            ev["stages"] = stages
            if not stages:
                unavailable.append("missing:member_industry_stage")
            elif not (set(stages) & set(stage_allowed)):
                return GateCheck(
                    GATE_SECTOR, VERDICT_REJECT,
                    reason=f"sector.stage={stages}∉{sorted(stage_allowed)}", evidence=ev)

    cluster_min = _gate_value(engine, "sector", "cluster_members_min")
    if isinstance(cluster_min, (int, float)) and not isinstance(cluster_min, bool):
        if pool_size is None:
            unavailable.append("missing:seed_pool_size")
        elif pool_size < int(cluster_min):
            return GateCheck(
                GATE_SECTOR, VERDICT_REJECT, score=float(pool_size), threshold=float(cluster_min),
                reason=f"sector.cluster_members={pool_size}<{int(cluster_min)}", evidence=ev)

    if unavailable:
        return GateCheck(GATE_SECTOR, VERDICT_PASS, available=False, blocks_t1=True,
                         score=score, threshold=threshold,
                         reason=";".join(unavailable), evidence=ev)
    return GateCheck(GATE_SECTOR, VERDICT_PASS, score=score, threshold=threshold,
                     reason="sector.ok", evidence=ev)


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


def _core_member_check(engine: Pack, member: Any) -> GateCheck:
    """④ 核心关(证据关,成员级):`leader_rs_rank ≤ gates.core.leader_rs_rank_max`
    (H10 唯一过闸的量)。名次取不到 → 不拦不给 T1;超限 → **degrade**(只降级,
    ③-A:证据关不除名)。"""
    code = member.ts_code
    rank_max = _gate_value(engine, "core", "leader_rs_rank_max")
    rs_rank = getattr(member, "rs_rank", None)
    if not isinstance(rank_max, (int, float)) or isinstance(rank_max, bool):
        return GateCheck(GATE_CORE, VERDICT_PASS, ts_code=code, reason="core.no_threshold")
    if rs_rank is None:
        return GateCheck(GATE_CORE, VERDICT_PASS, ts_code=code, available=False,
                         blocks_t1=True, threshold=float(rank_max), reason="missing:rs_rank")
    if int(rs_rank) > int(rank_max):
        return GateCheck(
            GATE_CORE, VERDICT_DEGRADE, ts_code=code,
            score=float(rs_rank), threshold=float(rank_max),
            reason=f"core.rs_rank={int(rs_rank)}>{int(rank_max)}")
    return GateCheck(GATE_CORE, VERDICT_PASS, ts_code=code, score=float(rs_rank),
                     threshold=float(rank_max), reason=f"core.rs_rank={int(rs_rank)}")


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


def _evaluate_under_engine(basket: Any, engine: Pack, ctx: GateContext) -> _EngineEval:
    industries = _basket_industries(basket.members)
    pool_size = None
    raw_pool = (basket.aux or {}).get("seed_pool_size") if hasattr(basket, "aux") else None
    if isinstance(raw_pool, int) and not isinstance(raw_pool, bool):
        pool_size = raw_pool

    checks: List[GateCheck] = []
    market = _market_gate(engine, ctx, industries)
    sector = _sector_gate(engine, ctx, industries, pool_size)
    checks.append(market)
    checks.append(_driver_gate(basket))
    checks.append(sector)

    kept: List[Any] = []
    removed: List[MemberRemoval] = []
    unfit_notes: List[str] = []
    for m in basket.members:
        checks.append(_core_member_check(engine, m))
        pos_check, is_unfit = _position_member_check(engine, m)
        checks.append(pos_check)
        # ⚠ 裁定 #11:位置关**永不让成员出篮**(证据关只降级)。`kept` 因此恒等于
        # 全体成员 —— 成员出篮的通路留着,但当前没有成员级机械关会触发它。
        kept.append(m)
        if is_unfit:
            unfit_notes.append(f"{m.ts_code}:{pos_check.reason}")

    checks.append(_evidence_gate(engine, basket))

    mech_reject = next((c for c in (market, sector) if c.verdict == VERDICT_REJECT), None)
    return _EngineEval(
        checks=tuple(checks), kept_members=tuple(kept), removed=tuple(removed),
        mech_reject_check=mech_reject,
        position_unfit=bool(unfit_notes), position_unfit_detail=";".join(unfit_notes),
    )


def _fits(ev: _EngineEval) -> bool:
    """机械兜底的「该引擎装得下这个篮子」判据:**篮子级机械关(市场/板块)不拒**。
    ⛔ 不看证据关 —— 证据关只降级,不该影响归属(裁定 #11 后位置关也在其中,
    故位置判定**不参与**引擎归属选择:LLM 判位置不佳不代表该篮该换个引擎)。"""
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
    "ENGINE_SOURCE_LLM", "ENGINE_SOURCE_MECH_FALLBACK",
    "EXCLUDE_NO_ACTIVE_ENGINE", "EXCLUDE_ENGINE_UNRESOLVED",
    "EXCLUDE_MECH_GATE_REJECTED", "EXCLUDE_MEMBERS_ALL_REMOVED",
    "classify_evidence_kind", "independent_evidence_kinds",
    "GateCheck", "MemberRemoval", "BasketGateSummary", "GateDayOutcome", "GateContext",
    "build_gate_context", "evaluate_day",
    "save_gate_evaluations", "load_gate_evaluations",
]
