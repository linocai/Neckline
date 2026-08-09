"""②驱动聚合层(plan §五 V2-⑤,**V2 心脏**)。把 ④ 市场扫描层的**驱动种子**
(`neckline/scan/seeds.py::SeedSet`)变成**篮子候选**:合并同驱动异名题材、命名共同
驱动 + 证据链(来源 + 日期)、「为什么是现在」、成员选择 + 角色标注。

**两段式 LLM 编排(② 完工记录明言留给本块落地)**
    · **检索段**(`TASK_DRIVER_SEARCH` → 检索型 Agent,带联网):逐颗种子问
      「这个题材现在到底被什么驱动、证据是什么、什么时候发生的」,产出**带来源与
      日期**的证据条目。预算走 `budget.LEDGER_SEARCH`。
    · **推理段**(`TASK_BASKET_REASON` → 推理型 Agent,不联网):把全部种子 +
      检索产物 + 成员机械数据一次性喂进去,产出篮子提案。预算走
      `budget.LEDGER_REASON`。
    · **单侧故障单侧降级**:检索段缺席 → `evidence_status='search_unavailable'`,
      证据链留空并明示,**推理段照跑、篮子照出**;推理段缺席 → **不成篮**(不拿
      机械数据硬凑一个"驱动",那才是编故事)。

**两道机械闸(定死,守门单测;是校验不是恳求 —— prompt 里的禁令只是背带)**
    1. **成员白名单闸**:LLM 只能从**系统喂给它的成员集合**里选票。出现集合外的
       代码 → **整条建议拒收**(不静默丢那一只,因为成员集被污染意味着这条驱动的
       成员选择整体不可信)+ WARNING 落日志。
    2. **角色对拍闸**:LLM 标注的角色 vs `leader_structure_daily.role_mech` 冲突
       → `basket_members.role_conflict=1`,**两说并存**,不静默采信任何一方。

**第〇原则(§2.0 / §2.8-C)在本块的落点**:本块产出的篮子、驱动叙述、成员理由
**全部是参考件**——不进机械分(⑥ 的 `_TIER_SCORE_INPUTS` 白名单里不会有本模块
任何字段)、不进哨兵判据(哨兵只读 ⑦ 冻结的结构化 spec 与现役章程 config)、不进
推送触发条件、**不做闸门**(`hard_cut` 之类的机械闸仍由 K4 advisory 与卫生线承担,
不由 LLM 承担)。本模块因此**不 import `neckline.sentinel.*`,不读写任何纪律参数**。

**v1.5.1 verdict 劫持案的教训在本块的落法**:结构化产出一律走**独立解析层**
(`neckline/llm/json_block.py`,唯一实现),**不复用 `judge.py::_parse_verdict`**
——两段式的输出里根本没有"结论:通过|否决"标签,硬套那套 last-match 锚点就是给
自己埋雷。prompt 里的格式要求只是背带,安全带是解析层 + 下面这些机械闸。

**落表**:`baskets` / `basket_members`(① 已建)。⚠ `baskets.tier` 是 `NOT NULL`
而 tier 由 ⑥ 定档,故写入口**强制要求调用方显式给出 tier**、绝不臆造,
`aggregate_baskets()` 本身不落库。**写入块 2026-08-02 由 ⑦ 改判为 ⑥**,写入口
实现随之搬进 `neckline/selection/basket_store.py`(本模块保留同名再导出,行为
逐字节不变;详见该模块头「运行期次序」)。
"""

from __future__ import annotations

import json
import logging
import re
import time
import zlib
from collections import Counter
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import polars as pl

from neckline.data.market_data import get_market_slice, resolve_stock_names
from neckline.llm.base import ChatMessage, LLMProvider
from neckline.llm.budget import LEDGER_REASON, LEDGER_SEARCH, BudgetLedger
from neckline.llm.factory import get_provider
from neckline.llm.json_block import split_narrative_and_reference_json
from neckline.llm.prompt_context import (
    TIMELINESS_RULES,
    date_anchor_line,
    search_subject_with_recency,
)
from neckline.llm.router import TASK_BASKET_REASON, TASK_DRIVER_SEARCH
from neckline.report.industry_strength import load_industry_map
from neckline.report.industry_strength import _MIN_MEMBERS as _INDUSTRY_STRENGTH_MIN_MEMBERS
from neckline.report.sectors import load_index_names
from neckline.scan import corr as corr_mod
from neckline.scan import leader as leader_mod
from neckline.scan import seeds as seeds_mod
from neckline.scan.seeds import DriverSeed, SeedSet
from neckline.selection import basket_store as _basket_store
from neckline.selection import engine_api
from neckline.selection import member_hygiene
from neckline.selection.pack import Pack, get_active_engines, get_active_pack, get_pack
from neckline.strategy import brain

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# 枚举与引擎常量(**引擎本体,不进包** —— plan §五 V2-③「插槽边界」原文:
# 「②驱动聚合的两道机械闸 = 引擎本体,不进包」)
# ══════════════════════════════════════════════════════════════════════════

# `baskets.evidence_status` 三态(DDL 枚举,语义不合并)
EVIDENCE_OK = "ok"
EVIDENCE_SEARCH_UNAVAILABLE = "search_unavailable"
EVIDENCE_PARTIAL = "partial"

# `baskets.driver_kind` 七值(DDL 枚举)
DRIVER_KINDS: Tuple[str, ...] = (
    "theme", "policy", "event", "commodity", "overseas", "rotation", "limit_cluster",
)

# `basket_members.role_llm` / `leader_structure_daily.role_mech` 三值(机械侧还有
# 第四值 `unknown` = 算不出,**不是一种角色**,见 `_resolve_mech_role`)
ROLE_LEADER, ROLE_CORE, ROLE_ELASTIC = "leader", "core", "elastic"
ROLES: Tuple[str, ...] = (ROLE_LEADER, ROLE_CORE, ROLE_ELASTIC)
ROLE_MECH_UNKNOWN = "unknown"

# 篮子成员数(蓝图 4.2「每个篮子允许 1—3 只股票」)
MIN_MEMBERS = 1
MAX_MEMBERS = 3

# 一次聚合最多喂给 LLM 多少颗种子。**真正的governor 是 `BudgetLedger`**(检索段
# 每颗种子一次带联网调用,生产实测 30-60s+),这个上限只是"别把上百颗涨停簇种子
# 一股脑塞进上下文"的工程护栏:④ 单日实测能产出 224 颗涨停簇种子,而 Tier 容量
# 上限总共 T1≤2 + T2≤5 = 7 篮(V2.1-② T3 退役前是 17 篮),搜到第 20 颗以后的边际
# 价值已经很低。
# ⚠ **V2.1-② 起篮子上限 17→7,本常量刻意不动**:检索段按**种子数**计费,与篮子数
# 无关,调小它省不到钱、只会砍掉聚合的选择面(plan §五 V2.1 附「成本与超时算术」
# 第 1 / 5 条:联网检索段一分不省,P3-41 并发化与本版无关)。
MAX_SEEDS_AGGREGATED = 20

# 每颗种子在**喂给 LLM 的上下文**里最多列几只成员。热点行业种子的
# `member_codes` 是该行业全部成员(可上百只),全量列出既撑爆上下文也没意义。
# ⚠ **这个截断同时定义了白名单闸的白名单**(见 `_shortlist` 与
# `_whitelist_gate`):"LLM 只能从扫描层给出的成员集合里选票"落地成"只能从**系统
# 实际展示给它的那份清单**里选" —— 更严、更可审计,也不会出现"它猜中了一只我们
# 没给它看的真成员"这种无法与幻觉区分的情形。
MAX_MEMBERS_IN_CONTEXT = 20

# ⑤-c(2026-08-02 planner 裁定,§五 V2-⑤-c):主归属 lift 的最小成分数门槛。**引擎
# 常量,不进包**——治的是统计有效性(涨停簇成分常只 2–5 只,lift 在这个样本量级下
# 会算出 70~90 倍失真,与 v1.3.1 先例〔板块成员数以百计〕不是一个统计量级),不是
# 策略偏好;进包会被误当成可调的 alpha 旋钮。**显式引用 `industry_strength.
# _MIN_MEMBERS`(不是另抄一份 5)**——与它、以及 K7 五态「成员≥5」同源同值,防两处
# 漂移。⚠ 正因为是「引用」而不是模块级数值字面量,`ast.literal_eval` 对 `Name`
# 节点无法求值,不会被 `test_selection_primitives.py` 的裸字面量扫描器命中,因此
# **不登记进 `_ENGINE_CONSTANT_WHITELIST`**(登记一个扫不到的键反而会让该文件的
# 反向存在性校验 `test_engine_constant_whitelist_entries_are_still_present` 失败)
# ——这是比"字面量 + 白名单登记"更强的防漂移手段,如实登记与 ⑤ 既有四个常量体例
# 的这一处刻意不同。
MIN_LIFT_SAMPLE_SIZE = _INDUSTRY_STRENGTH_MIN_MEMBERS

# 「算不出」≠「等于 0」的 lift 专属原因码(⑤-c):成分数 < `MIN_LIFT_SAMPLE_SIZE`
# 时该篮 `industry_lift` 记 `None` 并附此原因,与"该票无行业/全市场查无该行业占比"
# 这个既有的、⑤ 原有的"算不出"路径分开——后者语义更早、不特指小样本,不套这个原因码。
LIFT_REASON_SAMPLE_TOO_SMALL = "sample_too_small"

# 主归属决定路径(⑤-c 新增字段 `primary_reason`,只标在 `is_primary=1` 的那一行):
# 正常路径 = 在达标篮之间按 lift 比出来的;兜底路径 = 该票全部候选篮都不达标,退化
# 到确定性兜底(成员数降序 → basket_key 升序)。
PRIMARY_REASON_LIFT = "highest_lift"
PRIMARY_REASON_FALLBACK = "fallback_no_qualified_lift"

# 章程版本取不到时的占位(V2 红线:现役章程恒 v1.3.3,生产不会走到这里;测试用
# 空库时会。**不写空串冒充"没有章程"**——「没有」与「没看」必须能分开)。
CHARTER_UNKNOWN = "unknown"

# —— 段状态(诚实披露,语义不合并)————————————————————————————————————————
STAGE_OK = "ok"
STAGE_NO_PROVIDER = "no_provider"          # 路由解不出 / 无 key / 被禁用
STAGE_CALL_FAILED = "call_failed"          # 发起了调用但 `LLMResult.ok=False`
STAGE_BUDGET_EXHAUSTED = "budget_exhausted"
STAGE_PARSE_FAILED = "parse_failed"        # 调用成功但结构化产出解不出来
STAGE_NO_SEEDS = "no_seeds"                # 压根没有种子可聊
STAGE_PARTIAL = "partial"                  # 一部分种子搜成了、一部分没有(**不合并进 ok**)

# —— 拒收码(机械闸判定结果,**语义不合并**:每一种"为什么没成篮"都要能分开查)——
REJECT_MALFORMED = "malformed"                     # 形状不对(缺键/类型错/成员重复)
REJECT_UNKNOWN_SEED = "unknown_seed"               # 声明了不存在的种子(出处被污染)
REJECT_FABRICATED_MEMBER = "fabricated_member"     # 白名单闸:凭空造票
REJECT_MEMBER_COUNT = "member_count_out_of_range"  # 不是 1-3 只
REJECT_NO_DRIVER = "no_driver"                     # 共同驱动文本为空
REJECT_NO_EVIDENCE = "no_evidence"                 # 检索段跑过、零证据 → 仅历史相关性不成篮
REJECT_BAD_ROLE = "bad_role"                       # 角色不在三值枚举内(对拍闸无从比起)
REJECT_DUPLICATE_KEY = "duplicate_basket_key"      # 同日两条提案撞同一个 basket_key

# 种子类型 → `driver_kind` 的机械兜底映射(LLM 给的 `driver_kind` 不在七值枚举内时
# 用它,**不整条拒收**:一个分类标签写错不代表成员选择不可信,与白名单闸的"整条
# 拒收"性质不同)。映射依据蓝图 4.1 的「共同驱动可以来自」清单:热点行业/异动簇 =
# 「同一板块资金轮动」→ rotation;暴起概念 = 「同一题材」→ theme;涨停簇本身就是
# DDL 里的一个枚举值。**这是 builder 的工程判断,plan 未给映射表。**
_SEED_KIND_TO_DRIVER_KIND: Dict[str, str] = {
    seeds_mod.LIMIT_CLUSTER: "limit_cluster",
    seeds_mod.SURGING_CONCEPT: "theme",
    seeds_mod.HOT_INDUSTRY: "rotation",
    seeds_mod.ANOMALY_CLUSTER: "rotation",
}
# 一篮合并多颗不同类种子时,兜底 `driver_kind` 取哪一颗的 —— 固定优先级,保证
# 可复现(不看字典序、不看谁成员多)。
_SEED_KIND_PRIORITY: Tuple[str, ...] = (
    seeds_mod.LIMIT_CLUSTER, seeds_mod.SURGING_CONCEPT,
    seeds_mod.HOT_INDUSTRY, seeds_mod.ANOMALY_CLUSTER,
)

# `driver_slug` 归一化:只留中日韩统一表意文字与字母数字,其余(空格/标点/emoji)
# 一律丢弃后转小写 —— 目的是让「固态电池 · 产业化提速」与「固态电池产业化提速」
# 算同一个 slug,而不是让 basket_key 随 LLM 的标点习惯漂移。
_SLUG_DROP_RE = re.compile("[^0-9A-Za-z一-鿿]+")

_NEG_INF = float("-inf")


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 位置关(落地起跳)的 LLM 判定件 —— **2026-08-09 用户裁定 #11**
#
# 位置关**由机械关改判为证据关**:机械层只出读数(`landing_metrics_daily`),
# 判定直接交大模型,**只降级不除名**(§2.0 第〇原则第 4 锁「LLM 不做闸门」因此
# 完好无损)。⛔ 不得改回机械阈值 —— K8 §二 对「落地起跳」只有五句定性、零个
# 数字,工程侧翻译出的十二个阈值连乘后交集近乎为空(14 个 D0 回放零 T1)。
#
# 🔴 **成本铁律(附「成本与超时算术」五条写死的第 1 条,一字不变)**:位置判定
# **搭 `basket_reason` 那一次调用**,⛔ **不新增任何 LLM 调用,增量仍是 0**。
# 本模块的 LLM 调用点恒为 2 个(检索段 1 + 推理段 1),守门单测按 AST 数死。
# ══════════════════════════════════════════════════════════════════════════

# LLM 位置判定三值(唯一源;`gates.py` 与契约层都读这里,⛔ 不抄第二份)。
POSITION_OK = "ok"          # 位置合适 → 位置关 pass(T1 的必要条件之一)
POSITION_WEAK = "weak"      # 位置勉强 → 位置关 degrade(降一档)
POSITION_UNFIT = "unfit"    # 位置不合适 → 退出正式候选,**仍在 ③b 列名**
POSITION_VERDICTS: Tuple[str, ...] = (POSITION_OK, POSITION_WEAK, POSITION_UNFIT)

# LLM 没给 / 给了枚举外取值时的**保守兜底**(⛔ 不静默当 ok —— 「没判」不能被
# 讲成「判过了、没问题」;取 weak = 降一档,与「证据关只降级」同一姿势)。
POSITION_VERDICT_FALLBACK = POSITION_WEAK
POSITION_REASON_FALLBACK = "position.verdict_missing:LLM 未给位置判定,保守按 weak 处理"

# K8 §二「核心逻辑」原文(⛔ 逐字,不改写不缩写)—— prompt 里给 LLM 的第 ① 样。
K8_POSITION_CRITERIA = (
    "选择完成下落或调整、确认支撑并刚刚向上启动的核心股票。"
    "股票此前可以经历长期下跌、横盘整理或趋势内回撤。入选时必须具备以下状态:\n"
    "  1. 下跌或调整已经结束;\n"
    "  2. 关键位置形成有效支撑;\n"
    "  3. 抛压明显衰减;\n"
    "  4. 价格开始向上转强;\n"
    "  5. 当前仍处于启动早期。"
)

# `landing_metrics_daily.metrics_json` 的**键名契约**(🔴 两个施工面共用,
# ⛔ 不许改名、不许增减:写侧 `neckline/scan/landing.py`,读侧本模块)。
# 分组 = K8 §二 五句话各自对应的可观测事实(plan §五 ③-C 读数表逐行)。
# ⚠ 全部是**事实读数**,⛔ 不含任何阈值比较结果、不含四态枚举。
POSITION_METRIC_GROUPS: Tuple[Tuple[str, Tuple[Tuple[str, str], ...]], ...] = (
    ("下跌或调整已经结束", (
        ("low5_over_low20_ratio", "近5日最低价÷前20日区间最低价"),
        ("is_new_low_20d", "当日创20日新低"),
    )),
    ("关键位置形成有效支撑", (
        ("close_over_ma20_dev", "收盘相对MA20偏离"),
        ("close_over_platform_floor_dev", "收盘相对平台下沿偏离"),
    )),
    ("抛压明显衰减", (
        ("down_day_amount_ratio_5v20", "近5日下跌日均额÷近20日均额"),
        ("max_daily_drop_5d", "近5日最大单日跌幅"),
    )),
    ("价格开始向上转强", (
        ("close_over_ma5_dev", "收盘相对MA5偏离"),
        ("pct_chg", "当日涨跌幅"),
        ("rs5", "RS5(相对所属行业中位5日超额)"),
    )),
    ("当前仍处于启动早期", (
        ("dist_from_high_60d", "距60日高点"),
        ("cum_return_3d", "近3日累计涨幅"),
        ("is_limit_up", "当日涨停"),
        ("is_new_high_60d", "创60日新高"),
        ("platform_days", "平台天数"),
    )),
)
POSITION_METRIC_KEYS: Tuple[str, ...] = tuple(
    k for _group, items in POSITION_METRIC_GROUPS for k, _label in items
)


def _fmt_metric(value: Any) -> str:
    """一个读数 → 人读串。**布尔必须排在数值之前**(CLAUDE.md `NKJSON` 同款坑:
    `True` 在 Python 里也是 `int`,顺序反了「是/否」会变成「1/0」)。
    `None` = 这一项没取到 —— ⛔ 不填 0、不填默认值(plan ③-C:喂给 LLM 的必须是
    「这项没取到」而不是一个假数)。"""
    if value is None:
        return "未取到"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return f"{float(value):.4g}"
    return str(value)


def _fmt_metrics_missing(raw: Any) -> str:
    """`landing_metrics_daily.metrics_missing` → 人读串。写侧落的是
    `{读数键: 原因码}` 的 JSON(`scan/landing.py::REASON_*` 词汇),**原因码原样透传**
    —— 让模型知道"没取到"具体是哪一类,不是笼统一个 null(plan ③-C 的诚实披露)。"""
    if raw in (None, "", "{}"):
        return ""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw.strip()
    else:
        parsed = raw
    if isinstance(parsed, Mapping):
        return "、".join(f"{k}={parsed[k]}" for k in sorted(parsed))
    if isinstance(parsed, (list, tuple)):
        return "、".join(str(x) for x in parsed)
    return str(parsed)


def _load_position_metrics(
    trade_date: date, codes: Sequence[str], *, db_path: Optional[Path] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], bool]:
    """读 `landing_metrics_daily`(**只读预计算表**,P0-23 纪律:⛔ 不在线现算)。

    返回 `(code → 读数字典, code → metrics_missing 串, 当日表里有没有行)`。
    整段包保险丝(§五铁律:核心管线对可选情报输入的调用必须包保险丝)——
    读不到只是位置关的输入缺席(prompt 里如实写「本次未取得」、gates 侧
    `available=False` 不拦但不给 T1),⛔ 绝不让当日无篮子。"""
    metrics: Dict[str, Dict[str, Any]] = {}
    missing: Dict[str, str] = {}
    wanted = {c for c in codes if c}
    if not wanted:
        return metrics, missing, False
    try:
        # 惰性 import:写侧 `scan/landing*.py` 与本模块是两个施工面,把耦合收在
        # 这一句里 —— 名字对不上时是这里一行 WARNING,不是整条晚间链 ImportError。
        from neckline.scan.landing_store import load_landing_metrics

        df = load_landing_metrics(trade_date, db_path=db_path)
        day_present = not df.is_empty()
        if day_present:
            sub = df.filter(pl.col("ts_code").is_in(sorted(wanted)))
            for r in sub.iter_rows(named=True):
                try:
                    parsed = json.loads(r["metrics_json"]) if r["metrics_json"] else {}
                except (json.JSONDecodeError, TypeError):
                    parsed = {}
                metrics[r["ts_code"]] = parsed if isinstance(parsed, dict) else {}
                missing[r["ts_code"]] = _fmt_metrics_missing(r["metrics_missing"])
        return metrics, missing, day_present
    except Exception:  # noqa: BLE001
        logger.warning(
            "[aggregate] 落地起跳读数表读取失败(位置关本次无读数可喂,按「未取得」如实披露);"
            "写侧唯一入口应为 `neckline.scan.landing_store.load_landing_metrics`",
            exc_info=True,
        )
        return {}, {}, False


def _load_engine_position_guidance(db_path: Optional[Path] = None) -> Dict[str, str]:
    """三条引擎线的 `config.engine.gates.position.guidance`(**定性文本、无数字**,
    裁定 #11:三引擎的位置差别自此由定性描述 + LLM 判断承担)。⛔ 不走 `provenance`
    闸(它不是阈值),读不到只是 prompt 里少一段引擎准则,不影响成篮。"""
    out: Dict[str, str] = {}
    try:
        for code, pk in get_active_engines(db_path).items():
            gates = ((pk.config.get("engine") or {}).get("gates") or {})
            text = str((gates.get("position") or {}).get("guidance") or "").strip()
            if text:
                out[code] = text
    except Exception:  # noqa: BLE001
        logger.warning("[aggregate] 引擎线位置准则读取失败,prompt 少这一段", exc_info=True)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 数据形状
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class EvidenceItem:
    """一条驱动证据。**`source` 与 `date` 都必须非空**(蓝图 4.6 第 2 项「驱动证据
    与信息来源,每条带日期」;plan §五 V2-⑤「证据链(来源 + 日期)」)——两者缺一
    的条目在 `_parse_evidence_items` 里直接丢弃,不补默认值、不写"未知"冒充。"""

    claim: str
    source: str
    date: str
    url: str = ""

    def key(self) -> Tuple[str, str, str]:
        return (self.claim.strip(), self.source.strip(), self.date.strip())


@dataclass(frozen=True)
class DriverEvidence:
    """一颗种子的检索段产物。`status`:`ok` = 检索**真的跑了**(哪怕 0 条命中);
    `search_unavailable` = 检索段缺席(无 provider / 调用失败 / 预算耗尽 / 解析
    失败)。**这两者绝不能合并** —— 「搜过了、确实没证据」是"这条驱动站不住"的
    证据,「压根没搜」什么都不能说明,下游成篮判据据此分叉(见 `_evidence_for`)。
    """

    seed_key: str
    status: str
    items: Tuple[EvidenceItem, ...] = ()
    narrative: str = ""
    skip_reason: str = ""
    search_hits: int = 0
    provider: str = ""


@dataclass(frozen=True)
class BasketMemberCandidate:
    """`basket_members` 一行的内存形状(`basket_id` 落库时才有)。"""

    ts_code: str
    role_llm: str
    role_mech: Optional[str]          # None = 机械侧无判定(`unknown` 或簇里没这只)
    role_conflict: int                # 1 = 两侧不一致,分歧入卡、不静默采信任一方
    reason: str
    is_primary: int = 1
    industry: Optional[str] = None
    industry_lift: Optional[float] = None
    # ⑤-c:lift 因样本不足而算不出时的原因码(`LIFT_REASON_SAMPLE_TOO_SMALL`),
    # 「算不出」≠「等于 0」——`industry_lift is None` 本身可能因多种原因(无行业 /
    # 全市场查无该行业 / 本条新增的样本量不足),只有最后一种才附这个原因码。
    lift_reason: Optional[str] = None
    # ⑤-c:只在 `is_primary=1` 的那一行上有意义,标注主归属是靠 lift 比出来的
    # 还是走了确定性兜底(`PRIMARY_REASON_LIFT` / `PRIMARY_REASON_FALLBACK`)。
    primary_reason: Optional[str] = None
    rs_rank: Optional[int] = None
    name: str = ""
    # ⑤-b:K4 安检的 avoid_flag 标(hard_cut 命中已在装配阶段被剔,不会走到这里;
    # `k4_advisory_gate` 原语的两档语义照 ③ 的包——`hard_cut→exclude`/
    # `avoid_flag→tag`,这里落的是"tag"那一档,供未来 ⑦ 卡面展示 + ⑥ `card_density`
    # 消费)。`None` = 未命中任何 K4 分区,或 K4 评估本次不可用(降级不拦、不打标)。
    k4_tag: Optional[str] = None
    # —— V2.2-③-C 位置关(裁定 #11:判定交 LLM,机械层只出读数)————————————
    # 🔴 `position_metrics` / `position_metrics_missing` 是**当次喂给 LLM 的那份
    # 读数原样**(⛔ 不是 gates 侧另读一遍的),`position_verdict` /
    # `position_reason` 是模型据此给的判定与理由 —— 两样一起被 `gates.py` 写进
    # `gate_evaluations.evidence_json`。裁定 #11 之后「当时按什么标准判的」不再是
    # 一组可回放的数字而是一段模型输出,**不把这两样存在一起,事后就无法复核它
    # 到底在拿什么下判断**(plan ③-C 末段的硬要求)。
    position_verdict: str = ""             # ok|weak|unfit;"" = 本次没走过位置判定
    position_reason: str = ""              # 模型那句人话(或兜底原因码)
    position_metrics: Optional[Dict[str, Any]] = None      # None = 当次没有读数可喂
    position_metrics_missing: str = ""     # 哪几项没取到 + 为什么(诚实披露)


@dataclass(frozen=True)
class BasketCandidate:
    """一个篮子候选(= `baskets` 一行 **减去 `tier`**,tier 由 ⑥ 定档)。"""

    trade_date: str                   # 'YYYYMMDD'
    basket_key: str                   # crc32(trade_date|driver_slug) 十六进制
    name: str
    driver: str
    driver_kind: str
    why_now: str
    seed_keys: Tuple[str, ...]
    members: Tuple[BasketMemberCandidate, ...]
    evidence: Tuple[EvidenceItem, ...]
    evidence_status: str
    pack_version: str
    engine_api_version: int
    charter_version: str
    driver_kind_fallback: bool = False   # True = LLM 给的分类不合法,由种子类型兜底
    aux: Dict[str, Any] = field(default_factory=dict)   # 辅助证据(相关性等,机械算)
    # —— V2.2-③(K8 六道关口):⑤ 那**一次** `basket_reason` 调用顺带产出的结构化
    # 字段(成本算术铁律:三引擎并跑的 LLM 调用增量 = 0 次,⛔ 不新增调用)。
    # `engine_code_llm` 是 LLM 的**主张**、不是结论 —— 结论(下面的引擎三件套)由
    # `selection/gates.py` 机械对拍后经 `dataclasses.replace` 回填(§2.9-C-4:
    # ⛔ 不静默采信 LLM)。四问缺答/矛盾识别缺席 → 驱动关/证据关按 degrade 处置
    # (gates.py 的职责),⛔ 不在本层拒收(证据关只降级不除名,③-A)。
    engine_code_llm: Optional[str] = None
    common_trait: str = ""                 # 驱动关四问②:成员的共同特征(K8 §五-2)
    persistence: str = ""                  # 驱动关四问③:逻辑的持续性
    strengthen_and_invalidate: str = ""    # 驱动关四问④:什么会强化 / 证伪这个驱动
    evidence_conflicts: str = ""           # 证据关 LLM 侧:矛盾识别(纯披露,不进判据)
    # —— gates.py 机械对拍后的引擎归属(裁定 #9 单篮子单引擎,成员继承篮子引擎)——
    engine_code: Optional[str] = None
    engine_version: Optional[str] = None
    skeleton_version: Optional[str] = None
    engine_source: Optional[str] = None    # "llm" | "mech_fallback"(归属怎么来的)


@dataclass(frozen=True)
class RejectedProposal:
    """被机械闸拦下的提案(**留痕**:拒了什么、为什么、原样是什么)。"""

    reason: str
    detail: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AggregateResult:
    trade_date: str
    baskets: Tuple[BasketCandidate, ...] = ()
    rejected: Tuple[RejectedProposal, ...] = ()
    # ⑤-b:卫生线剔除的候选成员留痕,与 `rejected`(机械闸拒收 LLM 提案)**分开
    # 计数**——两种"没进来"语义不同:一个是"这只票不干净",一个是"这条 LLM 建议
    # 不可信"。
    hygiene_rejected: Tuple[member_hygiene.MemberRejection, ...] = ()
    evidence_by_seed: Dict[str, DriverEvidence] = field(default_factory=dict)
    search_stage: str = STAGE_NO_SEEDS
    reason_stage: str = STAGE_NO_SEEDS
    reason_narrative: str = ""
    pack_version: str = ""
    charter_version: str = CHARTER_UNKNOWN
    notes: Tuple[str, ...] = ()

    @property
    def degraded(self) -> bool:
        return self.search_stage != STAGE_OK or self.reason_stage != STAGE_OK


# 供调用方显式传 `None` 强制降级(与"没传、我去工厂要一个"区分开)。
class _Unset:
    def __repr__(self) -> str:   # pragma: no cover - 只为调试可读
        return "<unset>"


_UNSET = _Unset()


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def driver_slug(name: str, seed_keys: Sequence[str] = ()) -> str:
    """`baskets.basket_key = crc32(trade_date|driver_slug)` 里的那个 slug。

    **取篮子名而不是整句驱动**:`driver` 是一句完整的中文(标点、语气都会变),
    拿它做键会让同一个篮子在两次生成里得到两个 key;`name` 是短标签(「固态电池
    产业化提速」),归一化后稳定得多。名字为空 → 退回按种子键组合(仍然可复现)。
    如实登记:DDL 注释写的是 `driver_slug`,本实现把它定义为「篮子名的归一化」,
    是 builder 的口径选择,plan 未定义 slug 怎么算。"""
    s = _SLUG_DROP_RE.sub("", name or "").lower()
    if s:
        return s
    return "seeds-" + "-".join(sorted(seed_keys)) if seed_keys else "unnamed"


def make_basket_key(trade_date_s: str, slug: str) -> str:
    """`crc32(trade_date|driver_slug)` 十六进制(跨进程可复现,§五铁律禁内置
    `hash()`;与 `scan/cluster.py::make_cluster_key` 同一手法、不同命名空间)。"""
    return format(zlib.crc32(f"{trade_date_s}|{slug}".encode("utf-8")), "08x")


# ══════════════════════════════════════════════════════════════════════════
# 行业闸 lift(主归属规则的判据,**复用 v1.3.1 行业闸 lift 先例**)
# ══════════════════════════════════════════════════════════════════════════

def market_industry_shares(industry_of: Mapping[str, str]) -> Dict[str, float]:
    """全市场行业占比(lift 的分母)。分母 = `stock_basic` 里**有 industry** 的
    股票总数——无 industry 的票不计入(它们本就走"lift 未定义"分支,不该反过来
    稀释市场基准)。**口径与 `report/intel_candidates.py::_market_industry_shares`
    逐字一致**,交叉断言见 `tests/test_selection_aggregate.py`(不 import 那个
    私有函数:`intel_candidates.py` 按 plan §五 V2-⑬-1 将随候选榜退役,生产代码
    不该挂在一个计划删除的模块上;等价性由测试锁,同 v1.5 自选/持仓两侧 K4 镜像
    的交叉断言体例)。"""
    counts = Counter(ind for ind in industry_of.values() if ind)
    total = sum(counts.values())
    if total == 0:
        return {}
    return {ind: c / total for ind, c in counts.items()}


def industry_lift_map(
    members: Sequence[str],
    industry_of: Mapping[str, str],
    market_shares: Mapping[str, float],
) -> Dict[str, float]:
    """`行业 → lift`(lift = 该行业在这批成员里的占比 ÷ 该行业全市场占比)。
    **板内分母 = 全体成员**(无 industry 的成员计入分母、稀释板内占比,与
    `intel_candidates._dominant_industries` 的 denom 口径一致)。全市场查无该行业
    占比 → lift 未定义,该行业**不出现在返回字典里**(不写 0 冒充"不富集")。"""
    if not members:
        return {}
    counts = Counter(ind for m in members if (ind := industry_of.get(m)))
    denom = len(members)
    out: Dict[str, float] = {}
    for ind, c in counts.items():
        mkt_share = market_shares.get(ind)
        if not mkt_share:
            continue
        out[ind] = (c / denom) / mkt_share
    return out


# ══════════════════════════════════════════════════════════════════════════
# 成员机械数据(喂 LLM 的"确定性数据"侧,蓝图 4.3)
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class MechContext:
    """一天的成员机械数据快照。**只读**——本模块不改任何一张表。"""

    trade_date: date
    names: Dict[str, str] = field(default_factory=dict)
    industry_of: Dict[str, str] = field(default_factory=dict)
    market_shares: Dict[str, float] = field(default_factory=dict)
    amount_of: Dict[str, float] = field(default_factory=dict)
    close_of: Dict[str, float] = field(default_factory=dict)
    pct_chg_of: Dict[str, float] = field(default_factory=dict)
    # ts_code -> [(cluster_key, role_mech, rs_rank), ...](按 cluster_key 升序)
    mech_roles: Dict[str, List[Tuple[str, str, Optional[int]]]] = field(default_factory=dict)
    corr_by_pair: Dict[Tuple[str, str], float] = field(default_factory=dict)
    index_names: Dict[str, str] = field(default_factory=dict)
    # ⑤-b:成员卫生线闸的 K4 avoid_flag 标(`member_hygiene.apply_member_hygiene`
    # 算好后由 `aggregate_baskets()` 塞进来;hard_cut 命中已在装配阶段被剔,不会
    # 出现在这里)。`_gate_proposal` 构造 `BasketMemberCandidate` 时从这里取值。
    k4_tag_of: Dict[str, str] = field(default_factory=dict)
    # —— V2.2-③-C 位置关读数(裁定 #11):`landing_metrics_daily` 只读产物。
    # `position_metrics_available=False` = 当日整张表没行(引擎没跑 / 没数据),
    # 与「某只票单独缺行」是两回事,⛔ 不合并(「没有」与「没看」必须分得开)。
    position_metrics_of: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    position_metrics_missing_of: Dict[str, str] = field(default_factory=dict)
    position_metrics_available: bool = False
    # 三条引擎线的定性位置准则(engine_code → guidance;裁定 #11 后位置关零阈值,
    # 三引擎的位置差别由**定性描述 + LLM 判断**承担)。
    engine_position_guidance: Dict[str, str] = field(default_factory=dict)

    def display(self, code: str) -> str:
        name = self.names.get(code)
        return f"{name}({code})" if name else code

    def label_for(self, seed: DriverSeed) -> str:
        """种子的**人读标签**。④ 的涨停簇/异动簇种子在只有概念锚(没有行业锚)时
        `label` 是**裸指数代码**(`886086.TI`)——拿它当检索词等于什么都没查
        (CLAUDE.md v1.3.4 案底的同类病:检索词没有身份信息 → 命中泛泛新闻 → 模型
        退回训练数据),摊到卡面上也没人看得懂。这里统一过一遍 `ths_index` 名表
        (`report/sectors.py::load_index_names`,查名单一实现),查不到就原样保留。"""
        return self.index_names.get(seed.label, seed.label)


def build_mech_context(
    trade_date: date,
    codes: Sequence[str],
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> MechContext:
    """装配喂给 LLM 的确定性数据(蓝图 4.3「确定性数据负责…」那一侧)。

    **每一路都独立包保险丝**(§五铁律:核心管线对可选情报输入的调用必须包保险丝)
    —— 少一路只是上下文少一段,绝不让当日无篮子。行业映射/全市场占比读
    `stock_basic`(`load_industry_map`,与 `seeds.py` 同源);角色与 RS 名次读 ④ 的
    `leader_structure_daily`(**只读表,不现算**,P0-23 纪律);相关性读 ④ 的
    `corr_matrix_daily`(同样只读)。"""
    ctx = MechContext(trade_date=trade_date)
    wanted = sorted({c for c in codes if c})
    if not wanted:
        return ctx

    try:
        ctx.names = resolve_stock_names(wanted, db_path)
    except Exception:  # noqa: BLE001 —— 补名字失败只是展示降级
        logger.warning("[aggregate] 补股票名失败,降级为裸代码", exc_info=True)

    try:
        ctx.industry_of = load_industry_map(db_path)
        ctx.market_shares = market_industry_shares(ctx.industry_of)
    except Exception:  # noqa: BLE001
        logger.warning("[aggregate] 行业映射加载失败,主归属将退化为按 basket_key 定序", exc_info=True)

    try:
        daily = get_market_slice(trade_date, table="daily", parquet_dir=parquet_dir)
        if not daily.is_empty():
            sub = daily.filter(pl.col("ts_code").is_in(wanted))
            for r in sub.select(["ts_code", "close", "amount", "pct_chg"]).iter_rows(named=True):
                code = r["ts_code"]
                if r["amount"] is not None:
                    ctx.amount_of[code] = float(r["amount"])
                if r["close"] is not None:
                    ctx.close_of[code] = float(r["close"])
                if r["pct_chg"] is not None:
                    ctx.pct_chg_of[code] = float(r["pct_chg"])
    except Exception:  # noqa: BLE001
        logger.warning("[aggregate] 当日行情切片加载失败,成员排序退化为代码序", exc_info=True)

    try:
        ls = leader_mod.load_leader_structure(trade_date, db_path=db_path)
        if not ls.is_empty():
            rows = ls.filter(pl.col("ts_code").is_in(wanted)).sort(["ts_code", "cluster_key"])
            for r in rows.select(["ts_code", "cluster_key", "role_mech", "rs_rank"]).iter_rows(named=True):
                ctx.mech_roles.setdefault(r["ts_code"], []).append(
                    (r["cluster_key"], r["role_mech"], r["rs_rank"])
                )
    except Exception:  # noqa: BLE001
        logger.warning("[aggregate] 龙头结构表加载失败,角色对拍闸本次无机械侧可比", exc_info=True)

    try:
        ctx.index_names = load_index_names(parquet_dir)
    except Exception:  # noqa: BLE001 —— 查不到概念中文名只是标签退化成裸代码
        logger.warning("[aggregate] 概念指数名表加载失败,种子标签退化为裸代码", exc_info=True)

    try:
        cm = corr_mod.load_corr_matrix(trade_date, db_path=db_path)
        if not cm.is_empty():
            pairs = cm.filter(
                pl.col("code_a").is_in(wanted) & pl.col("code_b").is_in(wanted)
                & pl.col("corr").is_not_null()
            )
            acc: Dict[Tuple[str, str], List[float]] = {}
            for r in pairs.select(["code_a", "code_b", "corr"]).iter_rows(named=True):
                acc.setdefault((r["code_a"], r["code_b"]), []).append(float(r["corr"]))
            # 同一对可能出现在多个 scope(行业簇 + 概念板)里,取均值收敛成一个数;
            # `corr=NULL`(样本不足)已在上面被过滤掉,**不当 0 参与平均**。
            ctx.corr_by_pair = {k: sum(v) / len(v) for k, v in acc.items()}
    except Exception:  # noqa: BLE001
        logger.warning("[aggregate] 相关性表加载失败,辅助证据缺这一路", exc_info=True)

    # —— V2.2-③-C(裁定 #11):位置关读数 + 三引擎定性位置准则 —— 两路都只读表,
    # 各自包保险丝(缺了只是 prompt 少一段 + 位置关按「未取得」披露)。
    ctx.position_metrics_of, ctx.position_metrics_missing_of, ctx.position_metrics_available = (
        _load_position_metrics(trade_date, wanted, db_path=db_path)
    )
    ctx.engine_position_guidance = _load_engine_position_guidance(db_path)

    return ctx


def _shortlist(codes: Sequence[str], ctx: MechContext, limit: int) -> Tuple[str, ...]:
    """把一颗种子的原始成分裁成"展示给 LLM 的成员清单"。

    **排序 = 当日成交额降序 → `ts_code` 升序**(确定性 tie-break 在前、再截断,
    §五铁律;成交额是"注意力/流动性"的机械代理,不是收益预测)。缺成交额的票按
    `-inf` 排最后 —— **不当 0**(0 会把缺数据的票混进"成交额最小"那一档里)。

    ⚠ **本函数的输出同时就是白名单闸的白名单**(见 `MAX_MEMBERS_IN_CONTEXT`
    注释)。"""
    ordered = sorted(codes, key=lambda c: (-ctx.amount_of.get(c, _NEG_INF), c))
    return tuple(ordered[: max(0, int(limit))])


def _resolve_mech_role(
    code: str, ctx: MechContext, prefer_cluster_keys: Sequence[str] = ()
) -> Tuple[Optional[str], Optional[int]]:
    """机械侧角色 + 簇内 RS 名次。一只票可能同时属于多个簇(同日簇 / 连板簇 /
    不同 anchor),取值必须**可复现**:
        ① 优先取 `cluster_key` 落在本篮声明的种子里的那一行(涨停簇种子的
           `seed_key` 直接复用 `cluster_key`,见 `scan/seeds.py::_limit_cluster_seeds`);
        ② 否则取 `rs_rank` 最小(名次最靠前)的那一行,`rs_rank` 为空排最后;
        ③ 仍并列 → `cluster_key` 升序。
    `role_mech == 'unknown'`(机械侧算不出,如 RS20 窗口不满 20 天)→ 返回
    `(None, rs_rank)`:**「没判定」不是一种角色**,不能拿它去跟 LLM 对拍判冲突。"""
    rows = ctx.mech_roles.get(code)
    if not rows:
        return None, None
    prefer = set(prefer_cluster_keys)
    picked = None
    for cluster_key, role_mech, rs_rank in rows:
        if cluster_key in prefer:
            picked = (cluster_key, role_mech, rs_rank)
            break
    if picked is None:
        picked = sorted(
            rows, key=lambda t: (t[2] if t[2] is not None else float("inf"), t[0])
        )[0]
    _key, role_mech, rs_rank = picked
    if not role_mech or role_mech == ROLE_MECH_UNKNOWN:
        return None, rs_rank
    return role_mech, rs_rank


# ══════════════════════════════════════════════════════════════════════════
# 段一:驱动证据检索(`TASK_DRIVER_SEARCH` → 检索型 Agent,带联网)
# ══════════════════════════════════════════════════════════════════════════

DRIVER_SEARCH_SYSTEM_PROMPT = """你是「颈线」系统的盘后驱动证据检索员。系统本身只做审计、不代客下单,读者是一位
短线交易者。你的任务只有一件:**查清楚给定的这个题材/板块,当下到底被什么消息或事件驱动,并把证据连同
出处与日期一条条列出来**。你不负责选股、不负责判断能不能买。

信息边界(铁律,不可违反):
1. 你只能依据下面提供的结构化数据、以及联网搜索工具实际返回的内容作答。
2. 如果搜索没有找到相关消息,或搜到的内容与这个题材无关,必须明说"未搜到相关消息",
并把证据数组写成空数组。**绝不允许凭猜测编造新闻、公告、政策、传闻或研报。**
3. 一条证据如果找不到明确的信息来源或明确的日期,就**不要写进证据数组**——宁可少一条,
也不要给一条无从核实的。

""" + TIMELINESS_RULES + """

输出格式(两部分,顺序不可颠倒):

第一部分:两三句话的自然语言小结,说清楚这个题材当下的驱动是什么、有没有查到东西。
不要分点列表、不要打分表。

第二部分:空一行,给出一个```json 围栏代码块,严格是下面这个形状(不要多余字段):

```json
{"driver_hint": "一句话概括共同驱动(查不到就写 null)",
 "evidence": [{"claim": "这条证据本身说了什么",
               "source": "信息来源(媒体/机构/公告主体名称)",
               "date": "该材料的日期,格式 YYYY-MM-DD;只知道年月就写 YYYY-MM",
               "url": "链接,没有就写空字符串"}]}
```

一条都没查到时,`evidence` 写成 `[]`,`driver_hint` 写 null。**不要为了凑数把训练数据里
记得的旧新闻写进来。**
"""


def _parse_evidence_items(payload: Optional[Dict[str, Any]]) -> Tuple[EvidenceItem, ...]:
    """从检索段 JSON 里取合法证据条目。**`claim`/`source`/`date` 三者缺一即丢弃
    该条**(蓝图 4.6 第 2 项「每条带日期」是硬要求)——丢弃只影响那一条,不影响
    整次检索,但会打 WARNING 留痕。"""
    if not isinstance(payload, dict):
        return ()
    raw = payload.get("evidence")
    if not isinstance(raw, list):
        return ()
    out: List[EvidenceItem] = []
    seen: set = set()
    for item in raw:
        if not isinstance(item, dict):
            logger.warning("[aggregate] 检索段返回了非对象的证据条目,已丢弃:%r", item)
            continue
        claim = str(item.get("claim") or "").strip()
        source = str(item.get("source") or "").strip()
        date_s = str(item.get("date") or "").strip()
        if not (claim and source and date_s):
            logger.warning(
                "[aggregate] 证据条目缺 claim/source/date 之一,已丢弃(禁补默认值):%r", item
            )
            continue
        ev = EvidenceItem(claim=claim, source=source, date=date_s, url=str(item.get("url") or "").strip())
        if ev.key() in seen:
            continue
        seen.add(ev.key())
        out.append(ev)
    return tuple(out)


def build_search_context(seed: DriverSeed, presented: Sequence[str], ctx: MechContext) -> str:
    """检索段的 user 消息。**第一行永远是日期锚**(`prompt_context` 唯一实现;
    2026-07-30 报障根因 = 模型没有"现在"的概念)。"""
    lines = [
        date_anchor_line(ref_date=ctx.trade_date, name_tomorrow=True),
        f"待查题材:{ctx.label_for(seed)}(系统识别类型:{seed.seed_kind})",
        f"系统识别到它的机械依据:{seed.evidence}",
        f"该题材下的成员样本({len(presented)} 只,按当日成交额降序):"
        + "、".join(ctx.display(c) for c in presented),
        "请联网查证:这个题材当下(最近几个交易日)到底被什么消息、政策、公告、产业事件或"
        "海外映射驱动?把查到的证据逐条列出,每条带来源与日期。查不到就如实说查不到。",
    ]
    return "\n".join(lines)


def run_driver_search(
    seed: DriverSeed,
    presented: Sequence[str],
    ctx: MechContext,
    *,
    provider: Optional[LLMProvider],
    ledger: BudgetLedger,
    transport: Optional[Any] = None,
) -> DriverEvidence:
    """检索一颗种子的驱动证据。**任何失败都降级成 `search_unavailable`,不抛**
    ——检索段是可选情报输入,单侧故障不许掀翻整条聚合链路(§五铁律)。"""
    if provider is None:
        return DriverEvidence(seed.seed_key, EVIDENCE_SEARCH_UNAVAILABLE,
                              skip_reason=STAGE_NO_PROVIDER)
    if ledger.exhausted(LEDGER_SEARCH):
        return DriverEvidence(seed.seed_key, EVIDENCE_SEARCH_UNAVAILABLE,
                              skip_reason=STAGE_BUDGET_EXHAUSTED)

    messages = [
        ChatMessage(role="system", content=DRIVER_SEARCH_SYSTEM_PROMPT),
        ChatMessage(role="user", content=build_search_context(seed, presented, ctx)),
    ]
    started = time.monotonic()
    try:
        result = provider.chat(
            messages,
            enable_search=True,
            transport=transport,
            # v1.3.4 案底:不显式传时检索词跟**最后一条 user 消息**走,那是一大段
            # 结构化材料,推导出的检索词未必带得上题材身份、更不会带时效。
            search_query=search_subject_with_recency(ctx.label_for(seed)),
        )
    except Exception as exc:  # noqa: BLE001 —— 见 docstring
        ledger.spend(LEDGER_SEARCH, time.monotonic() - started)
        logger.warning("[aggregate] 驱动检索调用抛异常(种子 %s),降级为证据缺席",
                       ctx.label_for(seed), exc_info=True)
        return DriverEvidence(seed.seed_key, EVIDENCE_SEARCH_UNAVAILABLE,
                              skip_reason=f"{STAGE_CALL_FAILED}:{type(exc).__name__}")
    ledger.spend(LEDGER_SEARCH, time.monotonic() - started)

    if not getattr(result, "ok", False):
        return DriverEvidence(seed.seed_key, EVIDENCE_SEARCH_UNAVAILABLE,
                              skip_reason=f"{STAGE_CALL_FAILED}:{getattr(result, 'reason', '')}")

    narrative, payload = split_narrative_and_reference_json(result.content or "")
    if payload is None:
        # 调用成功但结构化产出解不出来 = 我们**没拿到**可用证据,不是"确认没有证据"。
        return DriverEvidence(seed.seed_key, EVIDENCE_SEARCH_UNAVAILABLE,
                              narrative=narrative, skip_reason=STAGE_PARSE_FAILED,
                              search_hits=len(getattr(result, "search_hits", []) or []),
                              provider=getattr(result, "provider", ""))
    items = _parse_evidence_items(payload)
    hits = len(getattr(result, "search_hits", []) or [])
    if not items:
        # 0 条命中必须显式说出来(v1.3.4 血训:GLM 会 ok=True 静默返 0 条,模型
        # 退回训练数据照样写得像模像样)。这里状态仍是 `ok` —— 检索**真的跑了**。
        logger.warning("[aggregate] 种子 %s 检索完成但零证据条目(search_hits=%d)",
                       ctx.label_for(seed), hits)
    return DriverEvidence(seed.seed_key, EVIDENCE_OK, items=items, narrative=narrative,
                          search_hits=hits, provider=getattr(result, "provider", ""))


# ══════════════════════════════════════════════════════════════════════════
# 段二:篮子推理(`TASK_BASKET_REASON` → 推理型 Agent,不联网)
# ══════════════════════════════════════════════════════════════════════════

BASKET_REASON_SYSTEM_PROMPT = """你是「颈线」系统的盘后选股参谋。系统本身只做审计、不代客下单,终选权永远在用户手里。
你的任务是把系统扫描出来的一批"驱动种子",整理成若干个**股票篮子**。

股票篮子的定义:**由同一个主要驱动因素影响、预计次日具有相似方向或明显联动关系的一组股票。**

你要做的七件事:
1. **合并名称不同但实际驱动相同的题材**(例如某个行业种子和某个概念种子其实是同一件事);
2. 给每个篮子命名,并用一句话说清**共同驱动**,再用一两句话说清**为什么是现在**;
3. 从系统给出的成员清单里**挑 1 到 3 只**股票,说明**为什么是这几只而不是同题材其他票**;
4. 给每个成员标一个角色:`leader`(高辨识度龙头)/ `core`(容量中军)/ `elastic`(弹性备选)。
优先覆盖不同角色,但**不强制凑齐三个角色**;宁可一只,也不要为了凑数塞进弱相关的票。
5. 给**每个篮子**标一个主引擎归属 `engine_code`(三选一;系统会拿机械数据对拍校验,
标错不符合机械事实的篮子成员会被系统剔除):
   · `C` = **已确认主线**里的核心股,健康回调结束后的再启动(主线逻辑与资金承接在延续);
   · `Z` = **新形成方向**的核心股,方向形成早期、率先转强的右侧启动;
   · `Y` = **中期平台**整理充分之后的核心启动(驱动是中期有效的,不要求当下最热)。
6. 补齐驱动判断的另外三问(与"为什么是现在"并列,每问一两句、写不出就写空字符串,
**不要编**):`common_trait`(这些成员的共同特征到底是什么)、`persistence`(这个逻辑
凭什么还能延续)、`strengthen_and_invalidate`(接下来出现什么会强化它、出现什么会证伪它);
另外若给出的各条证据之间**互相矛盾**,在 `evidence_conflicts` 里指出是哪几条打架、你如何取舍
(没有矛盾就写空字符串)。
7. 给**你选中的每一只成员**判一次**位置**(下面「位置关」一节给了判断标准与该票的读数),
产出 `position_verdict`(`ok` / `weak` / `unfit` 三选一)+ `position_reason`(一句人话说清依据):
   · `ok` = 位置符合「落地起跳」,现在正是值得投入注意力的位置;
   · `weak` = 位置勉强、有明显疑点(该票会被降一档);
   · `unfit` = 位置不合适(该票所在篮子退出正式候选,但**仍会在报告里列名并写明你的理由**)。
**读数里写「未取到」的项就是真的没取到**,请据实说明不确定性,⛔ 不要把它当成 0 或默认值。
判不准就给 `weak` 并说明缺什么,⛔ 不要为了让票留下而给 `ok`。

硬约束(系统会做机械校验,违反的建议会被整条丢弃,不是提醒而是规则):
· **成员只能从下面每颗种子给出的成员清单里选**。清单之外的任何代码都算凭空捏造,
一旦出现,**这条建议会被整条拒收**(不是丢掉那一只)。
· 每个篮子 **1 到 3 只**,不能是 0 只,也不能超过 3 只;同一篮里不能出现重复代码。
· `seed_keys` 只能填下面实际给出的种子编号。
· **仅有历史相关性不足以成篮**,必须能说出共同驱动是什么;说不出就不要造这个篮子。
· 角色只能写 `leader` / `core` / `elastic` 三者之一(英文小写)。

语义红线(不可违反):篮子和排序表达的是**注意力优先级,不是收益预测**。
**不得使用"推荐买入""建议买入""看好""值得买""目标价""止盈线"这类措辞**,
也不要暗示"这几只会涨"。你的产出全部是**参考,不是指令**。

""" + TIMELINESS_RULES + """

输出格式(两部分,顺序不可颠倒):

第一部分:三五句话的自然语言小结,说清今天你是怎么归并这些题材的。不要分点列表、不要打分表。

第二部分:空一行,给出一个```json 围栏代码块,严格是下面这个形状(不要多余字段):

```json
{"baskets": [
  {"name": "篮子名称(短标签)",
   "driver": "一句话说清共同驱动",
   "driver_kind": "theme|policy|event|commodity|overseas|rotation|limit_cluster 七选一",
   "engine_code": "C|Z|Y 三选一(该篮子的主引擎归属)",
   "why_now": "为什么是现在(一两句话)",
   "common_trait": "成员的共同特征(写不出就空字符串)",
   "persistence": "逻辑的持续性(写不出就空字符串)",
   "strengthen_and_invalidate": "什么会强化、什么会证伪(写不出就空字符串)",
   "evidence_conflicts": "证据之间的矛盾与取舍(没有就空字符串)",
   "seed_keys": ["这个篮子合并了哪几颗种子的编号"],
   "members": [{"ts_code": "必须来自该种子的成员清单",
                "role": "leader|core|elastic",
                "reason": "为什么是这只而不是同题材其他票",
                "position_verdict": "ok|weak|unfit 三选一(该票的落地起跳位置判定)",
                "position_reason": "一句话说清位置判定的依据"}]}
]}
```

今天如果没有任何一组种子能说出站得住的共同驱动,`baskets` 就写成 `[]`。
**空数组是合法答案,凑数不是。**
"""


def build_reason_context(
    seeds: Sequence[DriverSeed],
    presented_by_seed: Mapping[str, Tuple[str, ...]],
    evidence_by_seed: Mapping[str, DriverEvidence],
    ctx: MechContext,
) -> str:
    """推理段的 user 消息 = 日期锚 + **位置关判断标准** + 逐颗种子(机械依据 +
    检索证据 + 成员机械数据 + **该票的落地起跳读数**)。

    **检索段缺席的种子照样列出**,并显式标注「本次未取得联网证据」——藏起来会让
    模型误以为这颗种子没被查过就是没证据(「没有」与「没看」必须分得开)。

    **V2.2-③-C 位置关(裁定 #11)**:prompt 里给三样 —— ① K8 §二 五句原文;
    ② 三条引擎线的定性位置准则 `gates.position.guidance`;③ 该票的
    `landing_metrics_daily` 读数 + `metrics_missing`。⛔ **不新增 LLM 调用**,
    判定搭本次 `basket_reason` 一并产出。"""
    lines = [date_anchor_line(ref_date=ctx.trade_date, name_tomorrow=True), ""]
    lines.extend(_position_prompt_block(ctx))
    for seed in seeds:
        presented = presented_by_seed.get(seed.seed_key, ())
        ev = evidence_by_seed.get(seed.seed_key)
        lines.append(f"── 种子编号 {seed.seed_key}|类型 {seed.seed_kind}|名称 {ctx.label_for(seed)}")
        lines.append(f"   机械依据:{seed.evidence}")
        if ev is None or ev.status != EVIDENCE_OK:
            why = (ev.skip_reason if ev is not None else "not_run") or "unknown"
            lines.append(f"   联网证据:**本次未取得**(原因:{why})——请勿据此编造消息面,"
                         f"这颗种子只能靠机械依据判断驱动是否说得出口。")
        elif not ev.items:
            lines.append("   联网证据:已检索,**未查到任何相关消息**(0 条)。")
        else:
            lines.append(f"   联网证据({len(ev.items)} 条):")
            for it in ev.items:
                url = f" {it.url}" if it.url else ""
                lines.append(f"     · [{it.date}|{it.source}]{it.claim}{url}")
        lines.append(f"   成员清单({len(presented)} 只,**只能从这里选**):")
        for code in presented:
            role_mech, rs_rank = _resolve_mech_role(code, ctx, prefer_cluster_keys=(seed.seed_key,))
            bits = [ctx.display(code)]
            ind = ctx.industry_of.get(code)
            if ind:
                bits.append(f"行业 {ind}")
            if code in ctx.pct_chg_of:
                bits.append(f"当日 {ctx.pct_chg_of[code]:+.2f}%")
            if code in ctx.amount_of:
                # `daily.amount` 单位是**千元**(TuShare 口径,同
                # `research/panel.py::base_universe_expr` 的 `amount_ma20>=20000`
                # 〔千元 = 2000 万元〕),换亿元除 1e5,不是 1e8。
                bits.append(f"成交额 {ctx.amount_of[code] / 1e5:.2f} 亿元")
            bits.append(f"机械角色 {role_mech}" if role_mech else "机械角色 未判定")
            if rs_rank is not None:
                bits.append(f"簇内RS名次 {rs_rank}")
            lines.append("     · " + ";".join(bits))
            lines.append("       位置读数:" + _position_metrics_line(code, ctx))
        pair_note = _corr_note(presented, ctx)
        if pair_note:
            lines.append(f"   成员间 20 日相关性(**只作辅助证据,单凭相关性不足以成篮**):{pair_note}")
        lines.append("")
    lines.append("请据此给出今天的篮子。没有站得住的共同驱动就交空数组。")
    return "\n".join(lines)


def _position_prompt_block(ctx: MechContext) -> List[str]:
    """位置关判断标准段(裁定 #11 的 prompt 第 ①②样:K8 §二 五句 + 三引擎定性准则)。

    ⛔ **这一段里不许出现任何阈值/及格线** —— 位置关自此零阈值,数字只以「该票读数」
    的形式出现在成员清单里,由模型自己权衡。"""
    lines = [
        "── 位置关(落地起跳)判断标准 —— 给你选中的每一只成员判 position_verdict",
        "【K8 核心逻辑原文】" + K8_POSITION_CRITERIA,
    ]
    if ctx.engine_position_guidance:
        lines.append("【各引擎的位置准则(按你给该篮子标的 engine_code 取对应那条)】")
        for code in sorted(ctx.engine_position_guidance):
            lines.append(f"  · {code}:{ctx.engine_position_guidance[code]}")
    else:
        lines.append("【各引擎的位置准则】**本次未取得**(引擎线读取失败)——"
                     "只按上面 K8 原文判断,并在理由里注明缺这一条。")
    if not ctx.position_metrics_available:
        lines.append("⚠ 本次**整张落地起跳读数表都没有当日行**(引擎没跑或当日无数据)——"
                     "下面每只票的位置读数都会写「本次未取得」。⛔ 不要凭空想象读数,"
                     "据实说明不确定性即可。")
    lines.append("")
    return lines


def _position_metrics_line(code: str, ctx: MechContext) -> str:
    """一只票的落地起跳读数(按 K8 五句分组的人读串)。

    **缺行 / 缺项一律如实说「未取到」**,⛔ 不填 0、不填默认值(plan ③-C:喂给
    LLM 的必须是「这项没取到」而不是一个假数)。"""
    metrics = ctx.position_metrics_of.get(code)
    if metrics is None:
        why = ("当日读数表无行" if ctx.position_metrics_available
               else "当日读数表整张缺行")
        return f"**本次未取得**({why})"
    parts: List[str] = []
    for group, items in POSITION_METRIC_GROUPS:
        body = "、".join(f"{label} {_fmt_metric(metrics.get(key))}" for key, label in items)
        parts.append(f"[{group}]{body}")
    line = ";".join(parts)
    miss = (ctx.position_metrics_missing_of.get(code) or "").strip()
    if miss:
        line += f";(未取到的项与原因:{miss})"
    return line


def _corr_note(codes: Sequence[str], ctx: MechContext, top: int = 5) -> str:
    """成员两两相关性里最高的几对(确定性:先按 corr 降序、再按代码对升序)。"""
    pairs: List[Tuple[float, str, str]] = []
    ordered = sorted(codes)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            v = ctx.corr_by_pair.get((a, b))
            if v is None:
                v = ctx.corr_by_pair.get((b, a))
            if v is not None:
                pairs.append((v, a, b))
    if not pairs:
        return ""
    pairs.sort(key=lambda t: (-t[0], t[1], t[2]))
    return "、".join(f"{a}~{b} {v:.2f}" for v, a, b in pairs[:top])


def run_basket_reason(
    seeds: Sequence[DriverSeed],
    presented_by_seed: Mapping[str, Tuple[str, ...]],
    evidence_by_seed: Mapping[str, DriverEvidence],
    ctx: MechContext,
    *,
    provider: Optional[LLMProvider],
    ledger: BudgetLedger,
    transport: Optional[Any] = None,
) -> Tuple[str, Optional[List[Dict[str, Any]]], str]:
    """推理段一次调用,返回 `(叙述, 提案列表 or None, 段状态)`。

    **`None` 与 `[]` 语义不同**:`None` = 推理段缺席(该驱动不成篮,如实披露);
    `[]` = 模型跑了、明确说"今天没有站得住的篮子"(合法输出,当日无篮子)。"""
    if provider is None:
        return "", None, STAGE_NO_PROVIDER
    if ledger.exhausted(LEDGER_REASON):
        return "", None, STAGE_BUDGET_EXHAUSTED

    messages = [
        ChatMessage(role="system", content=BASKET_REASON_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=build_reason_context(seeds, presented_by_seed, evidence_by_seed, ctx),
        ),
    ]
    started = time.monotonic()
    try:
        # 推理段**不联网**(裁定 #2:DeepSeek 类推理 provider 无搜索能力;证据由
        # 检索段负责,推理段只做归并与比较——两段职责不混)。
        result = provider.chat(messages, enable_search=False, transport=transport)
    except Exception as exc:  # noqa: BLE001
        ledger.spend(LEDGER_REASON, time.monotonic() - started)
        logger.warning("[aggregate] 篮子推理调用抛异常,本次不成篮", exc_info=True)
        return "", None, f"{STAGE_CALL_FAILED}:{type(exc).__name__}"
    ledger.spend(LEDGER_REASON, time.monotonic() - started)

    if not getattr(result, "ok", False):
        return "", None, f"{STAGE_CALL_FAILED}:{getattr(result, 'reason', '')}"

    narrative, payload = split_narrative_and_reference_json(result.content or "")
    if not isinstance(payload, dict):
        logger.warning("[aggregate] 篮子推理输出解不出 JSON 块,本次不成篮")
        return narrative, None, STAGE_PARSE_FAILED
    raw = payload.get("baskets")
    if not isinstance(raw, list):
        logger.warning("[aggregate] 篮子推理 JSON 缺 baskets 数组,本次不成篮")
        return narrative, None, STAGE_PARSE_FAILED
    return narrative, raw, STAGE_OK


# ══════════════════════════════════════════════════════════════════════════
# 两道机械闸 + 篮子规则(**这里才是安全带,prompt 只是背带**)
# ══════════════════════════════════════════════════════════════════════════

def _evidence_for(
    seed_keys: Sequence[str], evidence_by_seed: Mapping[str, DriverEvidence]
) -> Tuple[Tuple[EvidenceItem, ...], str, bool]:
    """合并本篮各颗种子的证据 → `(去重后的证据链, evidence_status, 检索是否全跑过)`。

    **`evidence_status` 三态映射**:全 `ok` → `ok`;全 `search_unavailable` →
    `search_unavailable`;混合 → `partial`。第三个返回值 = 「本篮的每一颗种子都
    真的被检索过」,它决定「零证据要不要判不成篮」——plan 的两条规则只有这样读才
    自洽:
        · 「证据链条目为 0 → 不成篮」管的是**检索跑过、空手而归**(说明这条驱动
          站不住,仅历史相关性不足以成篮);
        · 「检索段缺席 → `evidence_status='search_unavailable'`,证据链留空并明示,
          篮子仍出」管的是**压根没搜成**(验收原文:检索段故障 → 篮子仍出)。
    两者若不分开,这两条 plan 条文会直接打架。"""
    items: List[EvidenceItem] = []
    seen: set = set()
    n_ok = 0
    for k in seed_keys:
        ev = evidence_by_seed.get(k)
        if ev is None:
            continue
        if ev.status == EVIDENCE_OK:
            n_ok += 1
        for it in ev.items:
            if it.key() in seen:
                continue
            seen.add(it.key())
            items.append(it)
    total = len(seed_keys)
    if total and n_ok == total:
        status = EVIDENCE_OK
    elif n_ok == 0:
        status = EVIDENCE_SEARCH_UNAVAILABLE
    else:
        status = EVIDENCE_PARTIAL
    return tuple(items), status, bool(total) and n_ok == total


def _resolve_driver_kind(
    raw_kind: Any, seed_kinds: Sequence[str]
) -> Tuple[str, bool]:
    """`driver_kind` 归一化 → `(取值, 是否走了兜底)`。不在七值枚举内 → 按种子
    类型机械兜底(见 `_SEED_KIND_TO_DRIVER_KIND` 注释:分类标签写错不等于成员
    选择不可信,不整条拒收)。"""
    kind = str(raw_kind or "").strip().lower()
    if kind in DRIVER_KINDS:
        return kind, False
    for sk in _SEED_KIND_PRIORITY:
        if sk in seed_kinds:
            return _SEED_KIND_TO_DRIVER_KIND[sk], True
    return "theme", True


def _parse_position_verdict(
    member_raw: Mapping[str, Any], *, code: str, name: str,
) -> Tuple[str, str]:
    """成员项 → `(position_verdict, position_reason)`(V2.2-③-C,裁定 #11)。

    🔴 **缺字段 / 枚举外取值 = 保守按 `weak` 处理 + 留痕,⛔ 不静默当 `ok`**:
    「模型没判」与「模型判过、说没问题」是两件事,合并成后者等于替它下结论
    (§2.0 第〇原则的同一条精神)。⛔ 也**不整条拒收** —— 位置关是证据关,
    证据关只降级不除名(③-A)。"""
    raw = str(member_raw.get("position_verdict") or "").strip().lower()
    reason = str(member_raw.get("position_reason") or "").strip()
    if raw in POSITION_VERDICTS:
        return raw, reason
    if raw:
        logger.warning(
            "[aggregate] 篮子 %r 成员 %s 的 position_verdict=%r 不在 %s 内,"
            "保守按 %s 处理(⛔ 不静默当 ok)", name, code, raw,
            list(POSITION_VERDICTS), POSITION_VERDICT_FALLBACK,
        )
    else:
        logger.warning(
            "[aggregate] 篮子 %r 成员 %s 未给 position_verdict,保守按 %s 处理",
            name, code, POSITION_VERDICT_FALLBACK,
        )
    detail = f"(模型给的是 {raw!r})" if raw else ""
    return POSITION_VERDICT_FALLBACK, (reason or (POSITION_REASON_FALLBACK + detail))


def _gate_proposal(
    proposal: Any,
    *,
    trade_date_s: str,
    seeds_by_key: Mapping[str, DriverSeed],
    presented_by_seed: Mapping[str, Tuple[str, ...]],
    evidence_by_seed: Mapping[str, DriverEvidence],
    ctx: MechContext,
    pack_version: str,
    charter_version: str,
    used_keys: set,
) -> Tuple[Optional[BasketCandidate], Optional[RejectedProposal]]:
    """把一条 LLM 提案过完所有机械闸 → `(篮子候选, None)` 或 `(None, 拒收记录)`。

    闸的顺序是刻意的:**先查出处、再查成员、最后才谈证据**——出处或成员集被污染
    时,后面所有判断都建立在不可信的基础上,没必要也不应该继续。"""
    if not isinstance(proposal, dict):
        return None, RejectedProposal(REJECT_MALFORMED, "提案不是 JSON 对象", {"raw": repr(proposal)[:200]})
    raw: Dict[str, Any] = proposal

    name = str(raw.get("name") or "").strip()
    driver = str(raw.get("driver") or "").strip()
    why_now = str(raw.get("why_now") or "").strip()

    # —— 闸 0:声明的种子必须真实存在(出处污染 = 整条不可信)——————————————
    seed_keys_raw = raw.get("seed_keys")
    if not isinstance(seed_keys_raw, list) or not seed_keys_raw:
        return None, RejectedProposal(REJECT_MALFORMED, "seed_keys 缺失或不是非空数组", raw)
    seed_keys: List[str] = []
    for k in seed_keys_raw:
        ks = str(k or "").strip()
        if ks and ks not in seed_keys:
            seed_keys.append(ks)
    unknown = [k for k in seed_keys if k not in seeds_by_key]
    if unknown:
        logger.warning(
            "[aggregate] 白名单闸(出处):提案 %r 声明了不存在的种子 %s,整条拒收", name, unknown
        )
        return None, RejectedProposal(REJECT_UNKNOWN_SEED, f"未知种子 {unknown}", raw)

    # —— 闸 1:共同驱动必须说得出口(蓝图 4.1:仅历史相关性不足以成篮)—————————
    if not driver:
        return None, RejectedProposal(REJECT_NO_DRIVER, "driver 文本为空", raw)

    # —— 闸 2:成员形状(1-3 只、无重复)————————————————————————————————
    members_raw = raw.get("members")
    if not isinstance(members_raw, list):
        return None, RejectedProposal(REJECT_MALFORMED, "members 不是数组", raw)
    if not (MIN_MEMBERS <= len(members_raw) <= MAX_MEMBERS):
        return None, RejectedProposal(
            REJECT_MEMBER_COUNT, f"成员数 {len(members_raw)} 不在 [{MIN_MEMBERS},{MAX_MEMBERS}]", raw
        )
    # (code, role, reason, position_verdict, position_reason)
    parsed_members: List[Tuple[str, str, str, str, str]] = []
    codes_seen: set = set()
    for m in members_raw:
        if not isinstance(m, dict):
            return None, RejectedProposal(REJECT_MALFORMED, "成员项不是对象", raw)
        code = str(m.get("ts_code") or "").strip()
        role = str(m.get("role") or "").strip().lower()
        reason = str(m.get("reason") or "").strip()
        pos_verdict, pos_reason = _parse_position_verdict(m, code=code, name=name)
        if not code:
            return None, RejectedProposal(REJECT_MALFORMED, "成员缺 ts_code", raw)
        if code in codes_seen:
            # 同一篮重复列同一只票 —— 落库会撞 UNIQUE(basket_id, ts_code),更重要的是
            # 说明这条提案本身没想清楚。
            return None, RejectedProposal(REJECT_MALFORMED, f"成员重复:{code}", raw)
        codes_seen.add(code)
        if role not in ROLES:
            # 角色是三值枚举、prompt 里逐字给过;写不对就没法跟机械侧对拍(对拍闸
            # 会失去意义),故按 malformed 拒收而不是猜一个角色塞进去。
            return None, RejectedProposal(REJECT_BAD_ROLE, f"{code} 的角色 {role!r} 不在 {ROLES}", raw)
        parsed_members.append((code, role, reason, pos_verdict, pos_reason))

    # —— 闸 3:**成员白名单闸**(plan §五 V2-⑤ 第 1 道)———————————————————
    allowed: set = set()
    for k in seed_keys:
        allowed.update(presented_by_seed.get(k, ()))
    fabricated = sorted(c for c, *_rest in parsed_members if c not in allowed)
    if fabricated:
        logger.warning(
            "[aggregate] 白名单闸:提案 %r 出现成员集合外的代码 %s(声明种子 %s),"
            "**整条拒收** —— 成员集被污染意味着这条驱动的成员选择整体不可信。",
            name, fabricated, seed_keys,
        )
        return None, RejectedProposal(REJECT_FABRICATED_MEMBER, f"集合外代码 {fabricated}", raw)

    # —— 闸 4:证据链(检索跑过却零证据 → 不成篮)——————————————————————
    evidence, evidence_status, all_searched = _evidence_for(seed_keys, evidence_by_seed)
    if all_searched and not evidence:
        logger.warning(
            "[aggregate] 提案 %r 的种子全部检索过但零证据条目 → 不成篮"
            "(仅历史相关性不足以成篮,相关性只作辅助证据)。", name
        )
        return None, RejectedProposal(REJECT_NO_EVIDENCE, "检索已跑、证据链为 0 条", raw)

    # —— 闸 5:basket_key 唯一(同日两条提案撞 slug → 后一条拒收,不静默丢)————
    slug = driver_slug(name or driver, seed_keys)
    basket_key = make_basket_key(trade_date_s, slug)
    if basket_key in used_keys:
        return None, RejectedProposal(REJECT_DUPLICATE_KEY, f"basket_key {basket_key} 已被同日另一篮占用", raw)

    # —— **角色对拍闸**(plan §五 V2-⑤ 第 2 道)———————————————————————
    members: List[BasketMemberCandidate] = []
    for code, role, reason, pos_verdict, pos_reason in parsed_members:
        role_mech, rs_rank = _resolve_mech_role(code, ctx, prefer_cluster_keys=seed_keys)
        conflict = 1 if (role_mech is not None and role_mech != role) else 0
        if conflict:
            logger.warning(
                "[aggregate] 角色对拍闸:%s 在篮子 %r 里 LLM 标 %s、机械侧 %s(rs_rank=%s)"
                " → role_conflict=1,两说并存入卡,不采信任何一方。",
                code, name, role, role_mech, rs_rank,
            )
        members.append(BasketMemberCandidate(
            ts_code=code, role_llm=role, role_mech=role_mech, role_conflict=conflict,
            reason=reason, industry=ctx.industry_of.get(code), rs_rank=rs_rank,
            name=ctx.names.get(code, ""), k4_tag=ctx.k4_tag_of.get(code),
            # 位置关(裁定 #11):判定 + **当次喂给它的那份读数**一起带下去 ——
            # `gates.py` 靠这两样把 `gate_evaluations.evidence_json` 写全。
            position_verdict=pos_verdict, position_reason=pos_reason,
            position_metrics=ctx.position_metrics_of.get(code),
            position_metrics_missing=ctx.position_metrics_missing_of.get(code, ""),
        ))

    seed_kinds = [seeds_by_key[k].seed_kind for k in seed_keys]
    kind, kind_fallback = _resolve_driver_kind(raw.get("driver_kind"), seed_kinds)
    if kind_fallback:
        logger.warning(
            "[aggregate] 提案 %r 的 driver_kind=%r 不在枚举内,按种子类型兜底为 %r",
            name, raw.get("driver_kind"), kind,
        )

    # —— V2.2-③:同一次调用顺带产出的关口字段(缺失/写错**不拒收**:engine_code
    # 归 gates.py 机械兜底,四问缺答归驱动关 degrade —— 证据关只降级不除名,③-A)。
    engine_raw = str(raw.get("engine_code") or "").strip().upper()
    engine_code_llm = engine_raw if engine_raw in ("C", "Z", "Y") else None
    if engine_raw and engine_code_llm is None:
        logger.warning(
            "[aggregate] 提案 %r 的 engine_code=%r 不在 C/Z/Y 内,交 gates.py 机械兜底",
            name, raw.get("engine_code"),
        )
    conflicts_raw = raw.get("evidence_conflicts")
    if isinstance(conflicts_raw, list):
        evidence_conflicts = ";".join(str(x).strip() for x in conflicts_raw if str(x).strip())
    else:
        evidence_conflicts = str(conflicts_raw or "").strip()

    return BasketCandidate(
        trade_date=trade_date_s,
        basket_key=basket_key,
        name=name or slug,
        driver=driver,
        driver_kind=kind,
        why_now=why_now,
        seed_keys=tuple(seed_keys),
        members=tuple(members),
        evidence=evidence,
        evidence_status=evidence_status,
        pack_version=pack_version,
        engine_api_version=engine_api.ENGINE_API_VERSION,
        charter_version=charter_version,
        driver_kind_fallback=kind_fallback,
        aux={
            "seed_labels": [ctx.label_for(seeds_by_key[k]) for k in seed_keys],
            "seed_kinds": seed_kinds,
            "corr_note": _corr_note([m.ts_code for m in members], ctx),
            # V2.2-③:篮子成分池大小(所声明种子的**全部原始成分**并集,与
            # `assign_primary` 的 lift 分母同口径)—— Z1 板块关 `cluster_members_min`
            # (簇内协同成员数)的机械读数,gates.py 消费。
            "seed_pool_size": len({c for k in seed_keys
                                   for c in seeds_by_key[k].member_codes}),
        },
        engine_code_llm=engine_code_llm,
        common_trait=str(raw.get("common_trait") or "").strip(),
        persistence=str(raw.get("persistence") or "").strip(),
        strengthen_and_invalidate=str(raw.get("strengthen_and_invalidate") or "").strip(),
        evidence_conflicts=evidence_conflicts,
    ), None


# ══════════════════════════════════════════════════════════════════════════
# 主归属(同票多篮 → `is_primary` 唯一 1,取行业闸 lift 最高的那篮)
# ══════════════════════════════════════════════════════════════════════════

def assign_primary(
    baskets: Sequence[BasketCandidate],
    seeds_by_key: Mapping[str, DriverSeed],
    ctx: MechContext,
) -> Tuple[BasketCandidate, ...]:
    """同一票可以留在多个篮子里(蓝图 4.2「同一股票可以保留多个题材标签」),但
    **`is_primary=1` 唯一**:取**行业闸 lift 最高**的那篮(plan §五 V2-⑤「主归属
    规则」,复用 v1.3.1 行业闸 lift 先例,防「挂靠票占位」重演)。

    lift 的"板内"分母 = 该篮**所声明种子的全部原始成分**的并集(不是最终 1-3 只
    成员!)——v1.3.1 那道闸问的就是"这只票的行业在这个板块里是否真的富集",分母
    必须是板块成员集合;拿 3 个成员算占比毫无统计意义。

    **⑤-c(2026-08-02 planner 裁定)最小成分数门槛**:该并集(下称"篮子成分池")
    大小 < `MIN_LIFT_SAMPLE_SIZE` 的篮子,其 lift **不参与主归属比较**——涨停簇
    成分池常只 2–5 只,lift 在这个样本量级下会失真(实测 70~90 倍),与 v1.3.1
    先例〔成分池以百计〕不是一个统计量级。**主归属规则因此改写为三段**:
        ① 一票的候选篮里,**只要有 ≥1 个达标篮**,主归属只在达标篮之间按
           lift 比(不达标篮完全不参与比较,无论其原始 lift 数值多高)。
        ② 一票的候选篮**全部不达标** → 退化到确定性兜底:按
           `(该篮成分池大小降序, basket_key 升序)` 取一个(可复现、不拍脑袋)。
        ③ 篮子本身**不因不达标被剔**——门槛只影响"主归属归谁",不影响"成不成篮"。
    **确定性 tie-break**(达标篮之间):lift 降序 → `basket_key` 升序。lift 算不出
    (该票无行业 / 全市场查无该行业占比)记 `-inf`,输给任何有数的篮 —— 但**不写
    0**,`industry_lift` 字段仍留 `None`(「算不出」与「算出来是 0」必须分得开;
    这与"成分池不达标"是两种不同的"算不出"原因,`lift_reason` 只标后者)。"""
    if not baskets:
        return ()

    # 每篮的成分池(其所声明种子的全部原始成分并集)与「行业 → lift」表。
    universe_by_basket: Dict[str, List[str]] = {}
    lift_by_basket: Dict[str, Dict[str, float]] = {}
    for b in baskets:
        universe: List[str] = []
        for k in b.seed_keys:
            seed = seeds_by_key.get(k)
            if seed is not None:
                universe.extend(seed.member_codes)
        universe = sorted(set(universe))
        universe_by_basket[b.basket_key] = universe
        lift_by_basket[b.basket_key] = industry_lift_map(universe, ctx.industry_of, ctx.market_shares)

    # ⑤-c:该篮成分池是否达标(>= MIN_LIFT_SAMPLE_SIZE)。
    qualified_of: Dict[str, bool] = {
        bk: len(u) >= MIN_LIFT_SAMPLE_SIZE for bk, u in universe_by_basket.items()
    }

    def _lift(basket_key: str, code: str) -> Optional[float]:
        ind = ctx.industry_of.get(code)
        if not ind:
            return None
        return lift_by_basket.get(basket_key, {}).get(ind)

    # code -> 该票出现的全部 (lift_or_None, basket_key, 该篮是否达标)。
    occurrences: Dict[str, List[Tuple[Optional[float], str, bool]]] = {}
    for b in baskets:
        qualified = qualified_of[b.basket_key]
        for m in b.members:
            occurrences.setdefault(m.ts_code, []).append(
                (_lift(b.basket_key, m.ts_code), b.basket_key, qualified)
            )

    primary_of: Dict[str, str] = {}
    primary_reason_of: Dict[str, str] = {}
    for code, occ in occurrences.items():
        qualified_occ = [(lift, bk) for lift, bk, q in occ if q]
        if qualified_occ:
            # 只在达标篮之间比:lift 降序 → basket_key 升序(纯确定性)。lift 算不出
            # 记 -inf 参与比较,不写 0。
            chosen = sorted(
                qualified_occ, key=lambda t: (-(t[0] if t[0] is not None else _NEG_INF), t[1])
            )[0]
            primary_of[code] = chosen[1]
            primary_reason_of[code] = PRIMARY_REASON_LIFT
        else:
            # 全部不达标 → 确定性兜底:(该篮成分池大小降序, basket_key 升序)。
            fallback = sorted(
                ((len(universe_by_basket[bk]), bk) for _lift_v, bk, _q in occ),
                key=lambda t: (-t[0], t[1]),
            )[0]
            primary_of[code] = fallback[1]
            primary_reason_of[code] = PRIMARY_REASON_FALLBACK

    out: List[BasketCandidate] = []
    for b in baskets:
        qualified = qualified_of[b.basket_key]
        # 🔴 **只改这四格,其余字段一律 `dc_replace` 原样带过**(V2.2-③-C 施工期真
        # 踩:这里原本是**逐字段手抄构造** `BasketMemberCandidate(...)`,新增的位置关
        # 四字段没抄进来 → 全篮成员的 `position_verdict` 被静默重置成默认值 → 六关
        # 侧全员回退成 `weak`、读数全丢,**日志一行警告都没有、界面上看起来像模型
        # 判的**。与下面那句对篮子级的告诫是同一条:逐字段手抄在加新字段时会静默丢
        # 字段,⛔ 别再改回手抄。
        members = tuple(
            dc_replace(
                m,
                is_primary=1 if primary_of.get(m.ts_code) == b.basket_key else 0,
                # 不达标篮:lift 不参与比较、也不展示数值(「算不出」≠「等于 0」,
                # 承 ⑤ 既有姿势;这里"算不出"的原因是样本太小,标 `lift_reason`)。
                industry_lift=(_lift(b.basket_key, m.ts_code) if qualified else None),
                lift_reason=(None if qualified else LIFT_REASON_SAMPLE_TOO_SMALL),
                primary_reason=(
                    primary_reason_of.get(m.ts_code)
                    if primary_of.get(m.ts_code) == b.basket_key else None
                ),
            )
            for m in b.members
        )
        # `dataclasses.replace` 只改 members —— 其余字段(含 V2.2-③ 新增的关口/引擎
        # 字段)原样保留;逐字段手抄在加新字段时会静默丢字段(施工期真踩过的坑型)。
        out.append(dc_replace(b, members=members))
    return tuple(out)


# ══════════════════════════════════════════════════════════════════════════
# 编排入口
# ══════════════════════════════════════════════════════════════════════════

def _select_seeds(seed_set: SeedSet, limit: int = MAX_SEEDS_AGGREGATED) -> Tuple[DriverSeed, ...]:
    """本次要聊的种子。`SeedSet.all_seeds()` 的次序本身就是确定的(热点行业 →
    暴起概念 → 涨停簇 → 异动簇,每类内部各自有序),题材类种子天然排在成百上千颗
    涨停簇之前,截断因此有意义而不是随机砍。**类内序是强弱序**(`scan/seeds.py::
    _sort_seeds`:语义主键〔行业名次 / 涨幅 / 簇大小〕→ `seed_key` crc32 两级序,
    2026-08-04 判定线审计 🔵-2),故某类超出剩余额度时被砍掉的是该类里最弱的那些,
    不是"crc32 恰好大"的那些。按 `seed_key` 去重(不同类之间理论上不会撞键,
    防御性去重不额外花钱)。"""
    out: List[DriverSeed] = []
    seen: set = set()
    for s in seed_set.all_seeds():
        if s.seed_key in seen:
            continue
        seen.add(s.seed_key)
        out.append(s)
        if len(out) >= limit:
            break
    return tuple(out)


def aggregate_baskets(
    trade_date: date,
    *,
    seed_set: Optional[SeedSet] = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    search_provider: Any = _UNSET,
    reason_provider: Any = _UNSET,
    ledger: Optional[BudgetLedger] = None,
    transport: Optional[Any] = None,
    max_seeds: int = MAX_SEEDS_AGGREGATED,
) -> AggregateResult:
    """驱动聚合层唯一编排入口:种子 → (检索段 → 推理段) → 两道机械闸 → 篮子候选。

    **永不抛异常**(§五铁律:任何一段异常都不许让当日无报告)——最坏情况返回一个
    空篮子集 + 如实的 `notes`。

    `search_provider` / `reason_provider`:默认从 ② 的路由工厂取
    (`TASK_DRIVER_SEARCH` / `TASK_BASKET_REASON`);**显式传 `None` = 强制该段
    缺席**(单测/无 key 环境用),与"没传"区分得开。

    `seed_set`:默认现算(`scan.seeds.generate_seeds`)。无现役策略包 → 那个函数
    返回 `None` → 本函数返回空结果并如实登记,**不造默认阈值、不拿昨日的凑数**
    (④「保险丝(四态)」原文:当日无篮子是合法输出)。
    """
    trade_date_s = _d(trade_date)
    notes: List[str] = []
    try:
        if seed_set is None:
            seed_set = seeds_mod.generate_seeds(trade_date, db_path=db_path, parquet_dir=parquet_dir)
        if seed_set is None:
            notes.append("no_active_pack_or_seed_set")
            logger.warning("[aggregate] %s 无种子集(无现役策略包?)—— 当日无篮子,合法输出。", trade_date_s)
            return AggregateResult(trade_date=trade_date_s, notes=tuple(notes))

        pack_version = seed_set.pack_version
        charter_version = _resolve_charter_version(db_path)
        seeds = _select_seeds(seed_set, max_seeds)
        if not seeds:
            notes.append("empty_seed_set")
            return AggregateResult(trade_date=trade_date_s, pack_version=pack_version,
                                   charter_version=charter_version, notes=tuple(notes))
        if len(seed_set.all_seeds()) > len(seeds):
            notes.append(f"seeds_truncated:{len(seeds)}/{len(seed_set.all_seeds())}")

        ledger = ledger or BudgetLedger()
        all_codes = sorted({c for s in seeds for c in s.member_codes})
        ctx = build_mech_context(trade_date, all_codes, db_path=db_path, parquet_dir=parquet_dir)

        # —— ⑤-b(2026-08-02 planner 裁定):成员卫生线闸 —————————————————
        # 落点定死在"装配给 LLM 看的成员清单"之前(`MAX_MEMBERS_IN_CONTEXT` 截断
        # **之前**先过滤再截断)——LLM 压根看不到脏票,既有白名单闸自动兜住。对
        # 候选成员集(`all_codes`,本日全部种子成分并集)**一次性**算好、全篮复用
        # (⛔ 不许每篮各算一遍)。
        pack = _resolve_pack(seed_set.pack_version, db_path)
        if pack is None:
            # 理论上不该发生(`seed_set` 非 None 已隐含刚刚取到过现役包);真出现
            # 时 ⑤-b 三原语无参数可读,判据无从谈起 —— 保守拒收候选成员集全部,
            # 与 tier-1「算不出就是异常、不放行」同一哲学,不静默放行任何票。
            logger.error(
                "[aggregate] %s 取不到策略包(%s)参数,⑤-b 卫生线判据无从谈起 —— "
                "保守拒收候选成员集全部 %d 只(fail closed)。",
                trade_date_s, seed_set.pack_version, len(all_codes),
            )
            notes.append("member_hygiene_pack_unavailable")
            hygiene = member_hygiene.MemberHygieneResult()
        else:
            hygiene = member_hygiene.apply_member_hygiene(
                all_codes, trade_date, pack,
                industry_of=ctx.industry_of, close_of=ctx.close_of,
                db_path=db_path, parquet_dir=parquet_dir,
            )
        ctx.k4_tag_of = hygiene.k4_tag_of
        if hygiene.hygiene_unavailable:
            notes.append("hygiene_unavailable")
        if hygiene.k4_unavailable:
            notes.append("k4_unavailable")

        # 显式引用模块级常量(而不是让它当默认参数在 def 时求值)——单测要能
        # monkeypatch 它来验证「白名单 = 实际展示给 LLM 的那份清单」。成员先过
        # ⑤-b 卫生线(`hygiene.kept`)再截断,脏票没有机会挤占展示位。
        presented_by_seed = {
            s.seed_key: _shortlist(
                [c for c in s.member_codes if c in hygiene.kept], ctx, MAX_MEMBERS_IN_CONTEXT,
            )
            for s in seeds
        }
        seeds_by_key = {s.seed_key: s for s in seeds}

        # —— 段一:检索 ————————————————————————————————————————————
        if isinstance(search_provider, _Unset):
            search_provider = _resolve_provider(TASK_DRIVER_SEARCH, db_path)
        evidence_by_seed: Dict[str, DriverEvidence] = {}
        for s in seeds:
            evidence_by_seed[s.seed_key] = run_driver_search(
                s, presented_by_seed[s.seed_key], ctx,
                provider=search_provider, ledger=ledger, transport=transport,
            )
        search_stage = _summarize_search_stage(evidence_by_seed)

        # —— 段二:推理 ————————————————————————————————————————————
        if isinstance(reason_provider, _Unset):
            reason_provider = _resolve_provider(TASK_BASKET_REASON, db_path)
        narrative, proposals, reason_stage = run_basket_reason(
            seeds, presented_by_seed, evidence_by_seed, ctx,
            provider=reason_provider, ledger=ledger, transport=transport,
        )
        if proposals is None:
            # 推理段缺席 → 该驱动不成篮(**不拿机械数据硬凑一个"驱动"**)。
            logger.warning("[aggregate] %s 推理段缺席(%s)—— 当日不成篮。", trade_date_s, reason_stage)
            return AggregateResult(
                trade_date=trade_date_s, evidence_by_seed=evidence_by_seed,
                hygiene_rejected=hygiene.rejected,
                search_stage=search_stage, reason_stage=reason_stage, reason_narrative=narrative,
                pack_version=pack_version, charter_version=charter_version, notes=tuple(notes),
            )

        # —— 机械闸 ————————————————————————————————————————————————
        accepted: List[BasketCandidate] = []
        rejected: List[RejectedProposal] = []
        used_keys: set = set()
        for p in proposals:
            basket, reject = _gate_proposal(
                p, trade_date_s=trade_date_s, seeds_by_key=seeds_by_key,
                presented_by_seed=presented_by_seed, evidence_by_seed=evidence_by_seed,
                ctx=ctx, pack_version=pack_version, charter_version=charter_version,
                used_keys=used_keys,
            )
            if basket is not None:
                used_keys.add(basket.basket_key)
                accepted.append(basket)
            elif reject is not None:
                rejected.append(reject)

        baskets = assign_primary(accepted, seeds_by_key, ctx)
        return AggregateResult(
            trade_date=trade_date_s, baskets=baskets, rejected=tuple(rejected),
            hygiene_rejected=hygiene.rejected,
            evidence_by_seed=evidence_by_seed, search_stage=search_stage,
            reason_stage=reason_stage, reason_narrative=narrative,
            pack_version=pack_version, charter_version=charter_version, notes=tuple(notes),
        )
    except Exception as exc:  # noqa: BLE001 —— 保险丝:聚合层塌了也不许当日无报告
        logger.error("[aggregate] %s 驱动聚合整体失败,当日无篮子(不阻断报告)", trade_date_s, exc_info=True)
        notes.append(f"aggregate_failed:{type(exc).__name__}")
        return AggregateResult(trade_date=trade_date_s, notes=tuple(notes))


def _resolve_provider(task: str, db_path: Optional[Path]) -> Optional[LLMProvider]:
    """按任务取 provider(② 的路由)。工厂本身在无 key / 被禁用时返回 `None`;
    这里再包一层是因为**取 provider 也可能抛**(库读不到等),而"取不到 provider"
    与"该段缺席"是同一件事,不该炸掉整条链路。"""
    try:
        return get_provider(task, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[aggregate] 取 %s 的 provider 失败,该段按缺席处理", task, exc_info=True)
        return None


def _resolve_charter_version(db_path: Optional[Path]) -> str:
    """口径指纹用的现役章程版本(`baskets.charter_version`)。**只读、绝不改**
    (V2 红线 1:全程不新建章程行、不改任何阈值)。取不到 → `CHARTER_UNKNOWN`,
    不写空串冒充。"""
    try:
        active = brain.get_active(db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[aggregate] 读现役章程失败,口径指纹记 unknown", exc_info=True)
        return CHARTER_UNKNOWN
    return active.version if active is not None else CHARTER_UNKNOWN


def _resolve_pack(pack_version: str, db_path: Optional[Path]) -> Optional[Pack]:
    """⑤-b 卫生线三原语要读的包对象(`Pack.seeds_config()`)。优先取 `seed_set`
    生成时那个**确切版本**(`get_pack`,避免与"当下现役包"因极端时序差异不一致);
    取不到才退回"现在的现役包"(`get_active_pack`)。两条路都失败 / 抛异常 →
    `None`,调用方按 tier-1 fail-closed 处理(不静默放行)。"""
    try:
        pack = get_pack(pack_version, db_path)
        if pack is None:
            pack = get_active_pack(db_path)
        return pack
    except Exception:  # noqa: BLE001
        logger.warning("[aggregate] 取策略包(%s)失败,⑤-b 卫生线无参数可读", pack_version, exc_info=True)
        return None


def _summarize_search_stage(evidence_by_seed: Mapping[str, DriverEvidence]) -> str:
    """检索段整体状态:全成功 → `ok`;全缺席 → 取第一条的 skip 原因(它们同因,
    通常是"没 provider");部分缺席 → `partial`(**不合并进 ok**——一半没搜到就是
    一半没搜到)。"""
    if not evidence_by_seed:
        return STAGE_NO_SEEDS
    ok = [e for e in evidence_by_seed.values() if e.status == EVIDENCE_OK]
    if len(ok) == len(evidence_by_seed):
        return STAGE_OK
    if not ok:
        first = next(iter(evidence_by_seed.values()))
        return first.skip_reason or STAGE_NO_PROVIDER
    return STAGE_PARTIAL


# ══════════════════════════════════════════════════════════════════════════
# 落库(`baskets` / `basket_members`)——**实现已搬去 `basket_store.py`**
# ══════════════════════════════════════════════════════════════════════════
#
# V2-⑥【planner 裁定 · 跨块】把篮子四表的写入口统一搬进
# `neckline/selection/basket_store.py`(四张表有事务边界要管,一族表一个 store,
# 同 `report/store.py` / `review/store.py` 既有体例)。这里保留**同名再导出**,
# 行为逐字节不变、⑤ 的既有调用方与单测一字不动 —— 照 ⑤ 自己刚做过的
# `llm/json_block.py` 搬迁体例。**新代码请直接 import `basket_store`。**

save_baskets = _basket_store.save_baskets


__all__ = [
    "EVIDENCE_OK",
    "EVIDENCE_SEARCH_UNAVAILABLE",
    "EVIDENCE_PARTIAL",
    "DRIVER_KINDS",
    "ROLES",
    "MIN_MEMBERS",
    "MAX_MEMBERS",
    "MAX_SEEDS_AGGREGATED",
    "MAX_MEMBERS_IN_CONTEXT",
    "MIN_LIFT_SAMPLE_SIZE",
    "LIFT_REASON_SAMPLE_TOO_SMALL",
    "PRIMARY_REASON_LIFT",
    "PRIMARY_REASON_FALLBACK",
    "CHARTER_UNKNOWN",
    "STAGE_OK",
    "STAGE_NO_PROVIDER",
    "STAGE_CALL_FAILED",
    "STAGE_BUDGET_EXHAUSTED",
    "STAGE_PARSE_FAILED",
    "STAGE_NO_SEEDS",
    "STAGE_PARTIAL",
    "REJECT_MALFORMED",
    "REJECT_UNKNOWN_SEED",
    "REJECT_FABRICATED_MEMBER",
    "REJECT_MEMBER_COUNT",
    "REJECT_NO_DRIVER",
    "REJECT_NO_EVIDENCE",
    "REJECT_BAD_ROLE",
    "REJECT_DUPLICATE_KEY",
    "DRIVER_SEARCH_SYSTEM_PROMPT",
    "BASKET_REASON_SYSTEM_PROMPT",
    "EvidenceItem",
    "DriverEvidence",
    "BasketMemberCandidate",
    "BasketCandidate",
    "RejectedProposal",
    "AggregateResult",
    "MechContext",
    "driver_slug",
    "make_basket_key",
    "market_industry_shares",
    "industry_lift_map",
    "build_mech_context",
    "build_search_context",
    "build_reason_context",
    "run_driver_search",
    "run_basket_reason",
    "assign_primary",
    "aggregate_baskets",
    "save_baskets",
]
