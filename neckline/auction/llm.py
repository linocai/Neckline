"""竞价层的 **LLM 段**(V2.3.3-③,K8.md §二十「机械层与 LLM 分工」的 LLM 那一半)。

**一次 `provider.chat(...)` 调用覆盖全部篮子**(⛔ 不逐篮调用;全仓守门单测会数
本包里的调用点,必须恰好 1 个)。任务常量 `TASK_AUCTION` 已进 `ALL_TASKS` 与
🔴 `LONG_CONTEXT_TASKS` —— 后者让 `use_streaming_for_task()` 与
`read_timeout_for_task()` **两项同路接线**(只接一半 = §七 P0-40/P0-44 原病复发)。
⚠ 流式下 read timeout `90` 的含义是 **chunk 间隔**(判「还在不在吐字」),**不是**整段
生成上限;⛔ 看见 90 别以为回退了。**真正的天花板是 9:29 硬截止**(`pipeline.py`)。

🔴 **必须 import `llm/prompt_context.py`**(全仓守门 AST 扫):`TIMELINESS_RULES` 进
system prompt + `date_anchor_line()` 放 user 首行 —— **竞价层尤其需要"今天是哪天"**,
它满篇在讲「D0 那天」与「今天开盘」。**本链路不联网** → ⛔ 不传 `search_query`。

🔴 **三道机械夹逼闸**(`clamp_verdict`,⛔ 不靠 LLM 自觉):
    闸 1 数据缺失只能形成中性(**无例外**)· 闸 2 Z1 只有一只竞价强股时保持中性 ·
    闸 3 Y1 的否决须「命中失效位 **或** 三项一致负面」。
三道闸的输入**都是 K8 §二十 原文明令的形式约束,没有一个新数字**(§五 ⑨-A 第 6/7 行)。

⚠ **C1 没有专属闸**:K8 对 C1 说的是「主线、核心和篮子共同转强可以形成确认,共同弱化
可以形成否决」—— 那是**给模型的判断口径**,不是形式约束,故只进 prompt、不设闸。
⛔ 别"顺手"给 C1 补一道。

**解析与降级**(⛔ 不另写一套):围栏解析复用 `llm/json_block.py`(全仓唯一实现);
篮子标识不在资料里 → **整条丢弃 + 记 note**(同 `entries` 既有纪律);整段解不出 →
`llm_stage='parse_failed'`,全部篮子 `verdict='pending_explanation'`。
⛔ **不复用 `judge._parse_verdict`** —— 本链路没有「结论:通过|否决」标签
(v1.5.1 标签劫持案的既定纪律)。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.auction import (
    AUCTION_MANUAL_NOTE,
    CLAMPED_BY_DATA_QUALITY,
    CLAMPED_BY_MISSING_STRONG_EVIDENCE,
    CLAMPED_BY_SINGLE_STRONG,
    CLAMPED_BY_Y1_LOW_WEIGHT,
    DQ_OK,
    LLM_BUDGET_EXHAUSTED,
    LLM_CALL_FAILED,
    LLM_NO_PROVIDER,
    LLM_OK,
    LLM_PARSE_FAILED,
    RISK_EVIDENCE_CONFLICT,
    RISK_VERDICT_CLAMPED,
    VERDICT_CONFIRM,
    VERDICT_NEUTRAL,
    VERDICT_PENDING_EXPLANATION,
    VERDICT_VETO,
    VERDICTS,
)
from neckline.llm.base import ChatMessage, LLMProvider
from neckline.llm.budget import LEDGER_REASON, BudgetLedger
from neckline.llm.json_block import split_narrative_and_reference_json
from neckline.llm.prompt_context import TIMELINESS_RULES, date_anchor_line
from neckline.llm.router import TASK_AUCTION

logger = logging.getLogger(__name__)

#: 本模块消费的 LLM 任务(任务常量单一源在 `llm/router.py`,这里只再导出一次,
#: 调用方一律从这两处取,⛔ 别散抄字符串)。
AUCTION_TASK = TASK_AUCTION


# ══════════════════════════════════════════════════════════════════════════
# system prompt(边界 / 判断顺序 / 三种结论 / 三引擎权重 **逐字照 K8.md §二十**)
# ══════════════════════════════════════════════════════════════════════════

AUCTION_SYSTEM_PROMPT = """你是「颈线」系统的**集合竞价解释者**。系统本身只做审计、不代客下单,读者是一位短线交易者。
你读的是 9:25 已经形成的集合竞价结果,要解释的是:**市场对昨天(D0)那份交易假设投出的第一次票**。

定位与职责(K8 §二十,铁律,不可违反):
- 不改变 D0 的行情状态、T1、T2、主引擎和交易预案;
- 不从竞价排行中临时增加交易标的;
- 新发现的强势股只作为市场锚点和后续研究线索;
- 报告发出后结束本次任务;
- 不持续观察 9:30 以后的价格;
- **不输出 `qualified`、`wait`、`cancelled` 等盘中交易状态**;
- **竞价结论只说明竞价反映出的信息,不等于买入指令。**

信息边界:
1. 你只能依据下面资料里的读数做判断。**本次没有联网检索工具**,也没有实时行情——资料是 9:26 冻结的那一份,
不得补充资料之外的新闻、公告、传闻或题材,也不得编造数字。
2. 资料里标注「算不出」「未记录」「没判」的项,就照实当作未知,**不要用推测填补**。
特别是标了「冻结锚今日失效(疑似除权除息)」的票:它的失效位与高开偏离**是没判**,不是"没问题"。
3. **「自身历史对照」这一项有一条机械判据,系统已经替你判好了,而且是「逐票」判的**:
每只票后面都会写「**允许形成历史比较**」或「**本项样本不足**」——**同一个篮子里两种标记会同时出现**
(老面孔攒够了、今天才进池的没攒够),⛔ 别拿其中一只的标记去套整篮。
标了**允许比较**的,资料里同时给了它窗口内的历史读数(最低 / 中位 / 最高),**比就比这些数**。
标了**样本不足**的,资料里只给逐日原始值:**只展示 / 只描述原始值,⛔ 不得据此做任何比较结论**
(⛔ 不许说"明显放量""高于平时水平""比往常清淡")。⛔ 这条不由你重新判断,也不许因为"看起来像放量"就绕过去。
⚠ 写「0 天 / 窗口内一条历史竞价快照都没有」的,是**没有可比的东西**,⛔ 不是"跟平时一样"。
4. **「相对板块」与「相对市场」是两个不同基准的数**:前者减的是板块基准(同行业对照股中位数),
后者减的是该票对应的市场指数。⛔ 别把两者当同一个数,也别在其中一个「没有这个读数」时拿另一个顶替。
资料里写「没有这个读数」的,就是**没有**——⛔ 不是"持平",更不是 0。
⚠ 板块协同那一行里的「**所属上市板块对照指数**」是**第三个东西**:它按上市板块(主板 / 创业板 /
科创板 / 北交所)取指数,**主板票落到的就是市场指数本身**。它只描述上市板块环境,
⛔ **不是**本次的板块基准 —— 判「相对板块强弱」只能用上面那个「相对板块」。

判断顺序(K8 §二十,按此逐步走):
1. 固定 D0 的主线、核心、引擎、入场区间和失效位置;
2. 比较主要指数竞价方向,识别市场普遍变化;
3. 结合最近数日板块强度,判断主线延续、分歧或弱化;
4. 检查龙头与容量核心的共同表现;
5. 检查强度是否扩散至多只核心或前排;
6. 比较候选相对板块、核心和指数的强弱;
7. 核对最终开盘价是否符合 D0 预案;
8. 输出竞价结论和主要证据,结束本次竞价模块。

三种竞价结论(K8 §二十,判据逐条):
- **确认(confirm)**:最终开盘价与 D0 预案相容;主线、板块、核心和候选方向基本一致;
候选没有明显弱于板块和市场;强度具有板块协同。
- **中性(neutral)**,符合以下任一情况:没有新增确认,也没有触发失效;主线、核心、板块和候选信号互相矛盾;
只有一只高位股强、板块协同不足;关键数据缺失;Y1 平台结构仍完整但竞价表现偏弱。
**数据缺失只能形成中性。**
- **否决(veto)**,符合以下任一情况:最终开盘价直接触发 D0 失效;主线核心、板块和候选同步明显走弱;
D0 的延续或启动假设已经被竞价直接否定。

确认、中性和否决**只描述集合竞价对 D0 假设的支持程度**。

三个引擎的竞价权重(K8 §二十):
- **C1 高权重**:验证既有主线是否延续、回调核心是否重新获得竞价支持。主线、核心和篮子共同转强可以形成确认,
共同弱化可以形成否决。
- **Z1 高权重**:验证新方向是否持续增强。板块扩散和核心协同优先,**只有一只竞价强股时保持中性**。
- **Y1 低权重**:验证中期平台启动当天的竞价表现。普通弱竞价只形成中性,不否定仍然完整的平台结构。
Y1 **只有**在最终价格直接触发 D0 失效,或中期驱动、板块核心和候选形成一致且明确的负面证据时,才形成竞价否决。

关键字段缺失时,对应股票标记为「中性｜数据不足」。市场锚点只解释资金方向,**不取得交易资格**。

""" + TIMELINESS_RULES + """

输出格式(两部分,顺序不可颠倒,中间空一行):

第一部分:一段连贯的自由叙述,像交易员开盘前口头点评这一早的竞价——市场整体什么气氛、原主线还在不在、
哪个篮子有问题。禁止分点列表与"技术面/资金面/消息面"这类固定分栏模板。

第二部分:一个 ```json 围栏代码块,严格是下面这个形状(不要多余字段,不要在围栏外重复):

```json
{"market": {"overview": "一段话:指数环境、原主线状态、核心协同、市场锚点",
            "anchors_note": "对竞价强势股这批市场锚点的一句解释(⛔ 它们不取得交易资格)"},
 "baskets": [
   {"basket_key": "资料里给出的篮子标识,⛔ 多一个都会被系统整条丢弃",
    "verdict": "confirm | neutral | veto",
    "reasons": ["白话理由一", "白话理由二"],
    "auction_strong_codes": ["资料里你认为竞价表现强的标的代码", "…"],
    "driver_negative": true,
    "sector_core_negative": false,
    "candidate_negative": null,
    "evidence_conflict": false,
    "members": [{"ts_code": "…", "note": "这只票的一句话"}]}],
 "risks": ["异常与风险一", "异常与风险二"]}
```

逐键要求(硬约束):
· `verdict` **只能是 `confirm` / `neutral` / `veto` 这三个英文码**——⛔ 不许写中文,
⛔ 不许写 `qualified` / `wait` / `cancelled` 之类的盘中交易状态。
· `reasons` **一至两条**白话理由,不要长篇。
· `auction_strong_codes` 只能从资料里出现过的代码里选;**不确定就给空数组,⛔ 别编**。
· `driver_negative` / `sector_core_negative` / `candidate_negative` 是三个**独立布尔**:
分别指「中期驱动」「板块核心」「候选本身」是否形成了**明确的负面证据**。
**判不出就写 `null`,⛔ 不许猜成 `false`**——`false` 的意思是"我看过了、不是负面",与"我判不出"不是一回事。
· `evidence_conflict`:资料里的证据是否互相矛盾(用于决定要不要给用户挂一张人工观察小纸条)。
· `basket_key` 必须逐字取自资料;`members[].ts_code` 同理。
· 某一项确实无法给出时,**宁可该字段写 null / 空数组,也不要编造**。
"""


def build_auction_context(mech: Any) -> str:
    """喂给 LLM 的 user 消息。**首行是日期锚**(`prompt_context` 唯一实现),
    随后是机械层的短摘要(K8 §二十 机械层第 6 条职责)。

    ⚠ 竞价层尤其需要"今天是哪天":它满篇在讲「D0 那天」与「今天开盘」,没有日期锚
    模型没有"现在"的概念(2026-07-30 那次报障的同款病)。
    """
    from neckline.auction.mech import short_summary

    lines = [date_anchor_line(mech.trade_date), ""]
    lines.append(f"今天是 D1({mech.trade_date:%Y-%m-%d}),被验证的 D0 是 "
                 f"{mech.d0_date:%Y-%m-%d}。下面是 9:26 冻结的那一份集合竞价读数。")
    lines.append("")
    lines.append(short_summary(mech))
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# 输出解析
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class BasketFields:
    """一个篮子的**模型原话**结构化字段(= 三道闸的输入,原样存档可回溯)。"""

    basket_key: str
    verdict: Optional[str] = None                 # 夹逼**前**;不在三值内 → None
    reasons: List[str] = field(default_factory=list)
    auction_strong_codes: Optional[List[str]] = None    # None = 模型压根没给这个字段
    driver_negative: Optional[bool] = None              # None = 判不出(⛔ 不当负面)
    sector_core_negative: Optional[bool] = None
    candidate_negative: Optional[bool] = None
    evidence_conflict: Optional[bool] = None
    members: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "basket_key": self.basket_key, "verdict": self.verdict,
            "reasons": list(self.reasons), "auction_strong_codes": self.auction_strong_codes,
            "driver_negative": self.driver_negative,
            "sector_core_negative": self.sector_core_negative,
            "candidate_negative": self.candidate_negative,
            "evidence_conflict": self.evidence_conflict,
            "members": list(self.members),
        }


@dataclass
class AuctionLLMResult:
    llm_stage: str
    elapsed_ms: Optional[int] = None
    narrative: str = ""
    market_overview: Optional[str] = None
    anchors_note: Optional[str] = None
    risks: List[str] = field(default_factory=list)
    by_basket: Dict[str, BasketFields] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


def _b(v: Any) -> Optional[bool]:
    """三态布尔:`True` / `False` / `None`(判不出)。⛔ 不把 `null` 猜成 `False`。"""
    return v if isinstance(v, bool) else None


def _codes(v: Any) -> Optional[List[str]]:
    if not isinstance(v, list):
        return None                 # ⚠ **没给** ≠ 空数组(闸 2 靠这个区分)
    return [str(x) for x in v if isinstance(x, str) and x.strip()]


def parse_auction_payload(
    payload: Optional[Mapping[str, Any]], *, known_basket_keys: Sequence[str],
) -> Tuple[Dict[str, BasketFields], Optional[str], Optional[str], List[str], List[str]]:
    """把围栏 JSON 拆成 `(逐篮字段, market_overview, anchors_note, risks, notes)`。

    **篮子标识不在资料里 → 整条丢弃 + 记 note**(同 `basket_card` 对 `entries` 的既有
    纪律:模型编出来的标识没有任何东西可以对齐,采信它等于凭空多一个篮子)。
    """
    notes: List[str] = []
    if not isinstance(payload, Mapping):
        return {}, None, None, [], ["payload_not_a_mapping"]
    known = set(known_basket_keys)
    out: Dict[str, BasketFields] = {}
    for item in (payload.get("baskets") or []):
        if not isinstance(item, Mapping):
            notes.append("basket_item_not_a_mapping")
            continue
        key = item.get("basket_key")
        if not isinstance(key, str) or key not in known:
            logger.warning("[auction] 丢弃资料集合外 / 形状不对的 baskets 条目:%r", key)
            notes.append(f"dropped_unknown_basket_key:{key!r}")
            continue
        raw_verdict = item.get("verdict")
        out[key] = BasketFields(
            basket_key=key,
            verdict=raw_verdict if raw_verdict in VERDICTS else None,
            reasons=[str(r) for r in (item.get("reasons") or []) if str(r).strip()][:2],
            auction_strong_codes=_codes(item.get("auction_strong_codes")),
            driver_negative=_b(item.get("driver_negative")),
            sector_core_negative=_b(item.get("sector_core_negative")),
            candidate_negative=_b(item.get("candidate_negative")),
            evidence_conflict=_b(item.get("evidence_conflict")),
            members=[dict(m) for m in (item.get("members") or []) if isinstance(m, Mapping)],
        )
        if raw_verdict is not None and raw_verdict not in VERDICTS:
            notes.append(f"unrecognized_verdict:{raw_verdict!r}")
    market = payload.get("market") if isinstance(payload.get("market"), Mapping) else {}
    overview = market.get("overview") if isinstance(market.get("overview"), str) else None
    anchors_note = market.get("anchors_note") if isinstance(market.get("anchors_note"), str) else None
    risks = [str(r) for r in (payload.get("risks") or []) if str(r).strip()]
    return out, (overview or None), (anchors_note or None), risks, notes


# ══════════════════════════════════════════════════════════════════════════
# 🔴 三道机械夹逼闸
# ══════════════════════════════════════════════════════════════════════════

def engine_line_of(mech: Any) -> Optional[str]:
    """这个篮子属于哪条引擎线:`"C"` / `"Z"` / `"Y"`;判不出 → `None`。

    🔴 **⚠ 与施工图字面的一处出入,如实登记(施工时实测)**:§五 ③-C 的夹逼闸伪代码写的是
    `mech.engine_code == "Z1"`,但库里 `baskets.engine_code` 存的是**线码 `C`/`Z`/`Y`**,
    `baskets.engine_version` 才是 `C1`/`Z1`/`Y1`(见 `packs/*.json` 的
    `manifest.line_code` vs `manifest.pack_version`,以及
    `tests/test_v22_gated_flow.py` 里 `("k1", 1, "C", "C1", "K8-V0.6")` 那条既有断言)。
    照字面写 `== "Z1"` 的后果是**闸 2 / 闸 3 永远不触发,而且看不出来** —— 这正是本层
    最不能出的那类静默失败。

    **按线判而不是按版本判**也是对的:K8 §二十 的三档权重是**引擎种类**的属性
    (C = 验证既有主线延续 · Z = 验证新方向增强 · Y = 验证中期平台启动),不是某个
    版本号的属性;将来出 `Z2` 时这条闸应当照样管用(⛔ 别改成枚举具体版本号)。

    老篮子 `engine_code` 为 `None`(K8 之前的行,如实)→ 返回 `None` → 三档权重都不适用,
    只走闸 1。
    """
    code = getattr(mech, "engine_code", None)
    if isinstance(code, str) and code.strip():
        return code.strip().upper()[:1]
    ver = getattr(mech, "engine_version", None)
    if isinstance(ver, str) and ver.strip():
        return ver.strip().upper()[:1]
    return None


def clamp_verdict(fields: Optional[BasketFields], mech: Any) -> Tuple[str, Optional[str]]:
    """`(夹逼后的结论, clamped_by)`。🔴 **次序写死:闸 1 → 闸 2 → 闸 3**,只记
    **第一个**命中的码(`clamped_by` 是单值)。

    ⚠ 三道闸的输入**都是 K8 §二十 原文明令的形式约束,没有一个新数字**:
    闸 2 的「只有一只」里的 1 是 K8 原文给的;闸 3 的「一致且明确」落成三个布尔
    **全为 `True`**(`null` = 判不出,**不算负面**);闸 1 没有任何数字。
    """
    raw = fields.verdict if fields is not None else None
    if raw not in VERDICTS:
        # 模型没给 / 给了不认识的码 → 「待解释」,⛔ 不猜成中性(那是一个实质判断)。
        return VERDICT_PENDING_EXPLANATION, None

    # ── 闸 1:K8 §二十「数据缺失只能形成中性」(**无例外**)─────────────────
    # ⚠ 之所以可以无例外:「命中 D0 失效位」走 §五 ②-G 的**独立警报通道**
    # (`hit_invalidation_json` 机械段就落库、恒定进第 4 块与推送),被夹成 neutral
    # 时那条信息**一个字都没丢**。
    if getattr(mech, "data_quality", None) != DQ_OK and raw != VERDICT_NEUTRAL:
        return VERDICT_NEUTRAL, CLAMPED_BY_DATA_QUALITY

    line = engine_line_of(mech)     # ⚠ 线码 C/Z/Y,见 `engine_line_of` 的出入登记

    # ── 闸 2:K8 §二十「Z1 …只有一只竞价强股时保持中性」────────────────────
    if line == "Z" and raw == VERDICT_CONFIRM:
        if fields.auction_strong_codes is None:          # 模型压根没给这个字段
            return VERDICT_NEUTRAL, CLAMPED_BY_MISSING_STRONG_EVIDENCE
        if len(set(fields.auction_strong_codes)) <= 1:   # 「只有一只」= K8 原文给的 1
            return VERDICT_NEUTRAL, CLAMPED_BY_SINGLE_STRONG

    # ── 闸 3:K8 §二十「Y1 只有在最终价格直接触发 D0 失效,或中期驱动、板块核心和
    #          候选形成一致且明确的负面证据时,才形成竞价否决」──────────────
    if line == "Y" and raw == VERDICT_VETO:
        hit = bool(getattr(mech, "hit_invalidation_codes", None))
        all_neg = (fields.driver_negative is True
                   and fields.sector_core_negative is True
                   and fields.candidate_negative is True)   # ⚠ `is True`:null 不算负面
        if not (hit or all_neg):
            return VERDICT_NEUTRAL, CLAMPED_BY_Y1_LOW_WEIGHT

    return raw, None


def manual_note_attached(verdict: str, fields: Optional[BasketFields],
                         clamped_by: Optional[str]) -> bool:
    """小纸条挂不挂(K8 §二十:「只出现在**中性、证据冲突或临界标的**旁边」)。

    ⚠ 「**临界标的**」K8 没给判据 → **用「被夹逼过」代表它**(`clamped_by` 非空),
    ⛔ 不发明一个"接近阈值"的数(§五 ⑨-A 第 4 行,如实登记)。
    """
    if verdict == VERDICT_NEUTRAL:
        return True
    if fields is not None and fields.evidence_conflict is True:
        return True
    return clamped_by is not None


def clamp_risk_note(basket_key: str, raw: Optional[str], verdict: str,
                    clamped_by: Optional[str]) -> Optional[Dict[str, str]]:
    """🔴 **⛔ 禁止模型已输出的结论被静默丢弃**(同 V2.3.2 ⑧-0 路径 A 的裁定):
    每一次夹逼都必须留 `clamped_by` 码 **且**进小报告第 4 块「异常与风险」。"""
    if clamped_by is None:
        return None
    return {"kind": RISK_VERDICT_CLAMPED,
            "text": f"篮子 {basket_key}:模型给的是「{raw or '未给'}」,"
                    f"经机械夹逼闸({clamped_by})后系统记为「{verdict}」。"}


def evidence_conflict_note(basket_key: str, fields: Optional[BasketFields]) -> Optional[Dict[str, str]]:
    if fields is None or fields.evidence_conflict is not True:
        return None
    return {"kind": RISK_EVIDENCE_CONFLICT,
            "text": f"篮子 {basket_key}:模型报告证据互相矛盾。"}


# ══════════════════════════════════════════════════════════════════════════
# 唯一一次 provider.chat(...)
# ══════════════════════════════════════════════════════════════════════════

def explain(
    mech: Any,
    *,
    provider: Optional[LLMProvider],
    ledger: Optional[BudgetLedger] = None,
    transport: Optional[Any] = None,
    system_prompt: str = AUCTION_SYSTEM_PROMPT,
) -> AuctionLLMResult:
    """一次调用解释**全部篮子**。**不落库**(落库归 `store.py`)、**不自己拉价**。

    `provider is None` → `provider_none`,不发起任何网络调用。
    🔴 **⛔ 别把预算账当兜底**:`BudgetLedger.exhausted()` 是**调用前**检查,而整个
    竞价流程**只有一次**调用 → 预算账对这次调用**零上界**(V2.3.2 复审已订正过同款
    误解)。真正的天花板是 **9:29 硬截止**,在 `pipeline.py`。
    """
    known_keys = [b.basket_key for b in getattr(mech, "baskets", ()) or ()]
    if provider is None:
        return AuctionLLMResult(llm_stage=LLM_NO_PROVIDER,
                                notes=["未配置可用的 LLM provider,本次竞价结论全部标『待解释』"])
    if ledger is not None and ledger.exhausted(LEDGER_REASON):
        return AuctionLLMResult(llm_stage=LLM_BUDGET_EXHAUSTED,
                                notes=["推理预算账已耗尽,本次不发起竞价解释调用"])

    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=build_auction_context(mech)),
    ]
    t0 = time.monotonic()
    try:
        # ⚠ **本链路不联网**(资料是 9:26 冻结的读数,没有可检索的东西)→
        # `enable_search=False`,⛔ 不传 `search_query`(同 `basket_card.run_card_llm` 体例)。
        result = provider.chat(messages, enable_search=False, transport=transport)
    except Exception as exc:  # noqa: BLE001 —— 抛异常与 ok=False 都只让 LLM 半份缺席
        elapsed = time.monotonic() - t0
        if ledger is not None:
            ledger.spend(LEDGER_REASON, elapsed)
        logger.warning("[auction] 竞价解释调用抛异常,本次结论全部标『待解释』", exc_info=True)
        return AuctionLLMResult(llm_stage=f"{LLM_CALL_FAILED}:{type(exc).__name__}",
                                elapsed_ms=int(elapsed * 1000),
                                notes=[f"竞价解释调用抛异常:{type(exc).__name__}"])
    elapsed = time.monotonic() - t0
    if ledger is not None:
        ledger.spend(LEDGER_REASON, elapsed)
    ms = int(elapsed * 1000)
    if not result.ok:
        return AuctionLLMResult(llm_stage=f"{LLM_CALL_FAILED}:{result.reason}", elapsed_ms=ms,
                                notes=[f"竞价解释调用失败:{result.reason}"])

    narrative, payload = split_narrative_and_reference_json(result.content or "")
    if not isinstance(payload, Mapping):
        return AuctionLLMResult(llm_stage=LLM_PARSE_FAILED, elapsed_ms=ms, narrative=narrative,
                                notes=["竞价解释输出里没有可解析的 JSON 围栏"])
    by_basket, overview, anchors_note, risks, notes = parse_auction_payload(
        payload, known_basket_keys=known_keys)
    return AuctionLLMResult(
        llm_stage=LLM_OK, elapsed_ms=ms, narrative=narrative,
        market_overview=overview, anchors_note=anchors_note, risks=risks,
        by_basket=by_basket, notes=notes,
    )


__all__ = [
    "AUCTION_TASK",
    "AUCTION_SYSTEM_PROMPT",
    "AUCTION_MANUAL_NOTE",
    "BasketFields",
    "AuctionLLMResult",
    "build_auction_context",
    "parse_auction_payload",
    "engine_line_of",
    "clamp_verdict",
    "manual_note_attached",
    "clamp_risk_note",
    "evidence_conflict_note",
    "explain",
]
