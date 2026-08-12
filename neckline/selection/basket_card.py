"""④篮子卡冻结(plan §五 V2-⑦,蓝图 §4.6)。D0 盘后为每个篮子冻结一张不可回溯
修改的卡。**每个数字要么机械算出、要么过机械闸(夹逼 / 白名单 / 对拍),无裸奔
LLM 数字。**

**卡内容 = 蓝图 4.6 全项(逐项,缺一不可)**,来源分工如下 —— ⑦ **不重问 ⑤ 已经
产出的东西**(那是重复消耗预算,也会产出与 `baskets` 表打架的第二份说法):

    1. 篮子名称与共同驱动      ← ⑤(`BasketCandidate.name` / `.driver` / `.driver_kind`)
    2. 驱动证据与信息来源(带日期)← ⑤(`.evidence` / `.evidence_status`)
    3. 为什么是现在            ← ⑤(`.why_now`)
    4. 成员、角色与比较结果     ← ⑤(成员 + 角色对拍分歧)+ 机械价量 + ⑦-K7 标注
    5. Tier 及分层理由         ← ⑥(duck-typed 传入:tier / 机械分 / 五维 breakdown /
                                 同档微调理由)
    6. 预期上涨路径            ← **⑦ 的 LLM**(TASK_SCRIPT,不联网)
                                 出处 K8.md **§十 第 8 项「预期上涨路径」**(D0 盘后
                                 十一项产物之一)+ **§十一 第 1 项「上涨判断:驱动与
                                 预期运行路径」**(下单资格四件之一)。V2.3.3 批 ① 由
                                 「次日强 / 平 / 弱三剧本」**换问题、不换槽位**:同一次
                                 `TASK_SCRIPT` 调用,LLM 调用数增量 = 0。**开盘那一刻
                                 怎么办由次日 9:26 的竞价确认层负责**(`neckline/auction/`),
                                 ⛔ 不再由这张卡出分支指引。
    7. 建仓观察区间 + 最高追价  ← ⑦ 的 LLM 给数字,**过夹逼闸**(见下)
    8. 篮子验证条件(结构化 + 人话双份)← 结构化由 ⑦ 机械算(⑧ 唯一判据源),人话 LLM
    9. 篮子失效条件(结构化 + 人话双份)← 同上
   10. 主要风险               ← ⑦ 的 LLM
   11. `disclaimer`           ← `BASKET_CARD_DISCLAIMER` 固定文案**单一源**

**工程体例继承 v1.5 参考件(逐条,不重新发明)**
    · **夹逼**:建仓观察区间与最高追价的每个数字必须落在**次日**涨跌停闭区间内 ——
      `limit_derived.compute_intraday_limit_prices(pre_close=D0 close, board, is_st,
      trade_date=next_trading_day(D0))`;`board`/`is_st` 走
      `sentinel.universe.load_stock_meta`(唯一源,不自己判 ST 前缀 / 不自己分板块);
      `next_trading_day` 走 `neckline.calendar`。**越界不显示** + 四态分开落行
      (`rejected_out_of_limit` / `rejected_malformed` / `rejected_no_limit` / `absent`)。
    · **离场参考不夹涨跌停**(压力位可能几个交易日后才到,拿明日涨跌停夹它是错的),
      但自 ⑪-D(2026-08-03 planner 裁定)起多一道**下界语义闸**:`exit_low > D0 close`,
      不满足落 `rejected_not_above_close`、该项不落卡 —— 这份数字经 ⑩ 继承会成为 APNs
      `take_profit` kind 的触发位置(§2.8-C-3 记名豁免的四条前提之一就是"已过机械闸"),
      **零发明阈值,靠的是定义**(压力位在现价之上)。它是 ⑩ `position_plans.plan_json`
      「建仓区间/最高追价/**离场参考**/验证失效/主要风险」要继承的那一项,故卡上必须有。
    · **止损价系统算、不由 LLM 给**:`round(close × (1 − stop_pct), 2)`,`stop_pct`
      读现役章程 config(§2.1 唯一源,**禁硬编 0.05**)。
    · **口径指纹落行**:`stop_pct` / `take_profit_retrace` / `charter_version` /
      `pack_version` / `engine_api_version`;**纪律标签动态生成**(缺指纹就退化成不带
      数字的说法,禁把「−5%」「8%」写进模板)。
    · **结构化阈值先算、再喂 LLM**:`verification_spec` / `invalidation_spec` 在调用
      LLM **之前**算好并写进上下文,使人话剧本与盘中自动警报**同频**(v1.5-①-A 体例)。
    · **条件集与聚合规则不住在本模块**(⑦-b,2026-08-02 planner 裁定):验证 / 失效
      条件、`min_members_hit = ceil(n/2)`、四态映射、比较语义全部**唯一定义在
      `neckline/selection/verification_rules.py`**,本模块只把阈值**填进** spec 并
      冻结;⑧ 哨兵读同一份规则模块 + 卡里冻结的阈值。本模块再导出 `COND_*` 只为
      调用点少改字(同 `aggregate.py` 再导出 `save_baskets` 的体例)。
    · **v1.5.1 标签劫持案**:LLM 输出一律走 `llm/json_block.py` 先剥 JSON 再解析;
      本模块**没有**「结论:通过|否决」标签,也**不复用** `judge._parse_verdict`。

**第〇原则(§2.0)在本块的落点**:卡上的**文本**(剧本 / 人话验证失效 / 风险 / 成员
理由)全是参考件,不进任何机器判据;**唯一的机器消费出口**是结构化的
`verification_spec` / `invalidation_spec`(⑧ 篮子验证状态机的唯一判据源,plan §五
V2-⑦ 明文的例外)。本模块因此**不 import `neckline.sentinel.*` 的任何判定逻辑**
(只 import `universe.load_stock_meta` 这一条纯查元数据的路径,同 v1.5 参考件先例),
**不读写任何纪律参数**(只**读** `stop_pct` / `take_profit_retrace` 做指纹与止损线)。

**落库**:⑦ **只写 `basket_cards` 一张表**(`baskets`/`basket_members`/`tier_history`
由 ⑥ 的【事务 1】写,2026-08-02 planner 裁定改判)。写入口
`basket_store.save_basket_card()` 是**独立的【事务 2】**,在本模块的 LLM 调用**之后**
才开 —— 跨 LLM 调用持 SQLite 事务是错的。本模块自身**不落库**。

**不 import ⑥**(plan §五【跨块】D 条:「⑥⑦ 自身只提供纯函数 + 写入口,**不互相
import**、不各自开编排」)—— ⑥ 的定档结果按 **duck-typed** 传入(只要求
`tier`/`rank_in_tier`/`rank_mech`/`mech_score`/`breakdown`/`llm_reason` 几个属性),
⑤ 的篮子同理。全链路编排入口在 ⑭-A 的 `report/pipeline.py`。
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.calendar import next_trading_day
from neckline.data.limit_derived import compute_intraday_limit_prices
from neckline.llm.base import ChatMessage, LLMProvider
from neckline.llm.budget import LEDGER_REASON, BudgetLedger
from neckline.llm.json_block import split_narrative_and_reference_json
from neckline.llm.prompt_context import TIMELINESS_RULES, date_anchor_line
from neckline.llm.router import TASK_SCRIPT
from neckline.selection import member_tags as mt
from neckline.selection import verification_rules as vr
from neckline.sentinel.universe import load_stock_meta
from neckline.strategy import brain

logger = logging.getLogger(__name__)

# —— 卡片形状版本(冻结快照的自描述位;⑮ 客户端与 ⑧ 状态机据此判断"这张卡是哪一版
#    形状"。**改形状必须同时改这个串**,不许悄悄换键)——————————————————————
# v2(⑦-b,2026-08-02):`fingerprint` 增 `verification_ruleset_version`;失效 spec 的
# 第 ③ 条由「收盘 < MA20」单条改成复合条件(见 `verification_rules`),`members[]` 行
# 里那一格从标量变成 `{ref_close, ma20}` 映射 —— **形状变了就 bump,这正是
# `spec_version` 存在的意义**(条件集本身的版本另有 `VERIFICATION_RULESET_VERSION`)。
# V2.2-③:v2 → v3 = 新增引擎归属三键(engine_code/engine_version/skeleton_version,
# 裁定 #9 单篮子单引擎;纯增量、老键一字未动 —— 「spec_version 恒随形状变化而变」
# 的既定纪律,老卡照常按 v2 读回)。
# ⚠ **裁定 #11 的位置关三键(position_verdict/position_reason/position_metrics)
# 与裁定 #12 的核心关三键(core_verdict/core_reason/core_metrics)并入同一个 v3,
# ⛔ 不另 bump v4/v5**:v3 本身**一天都没上过产**(V2.2 批 2 未部署),同一个未发版
# 形状里再 bump 只会造出一个没有任何卡携带的幽灵版本号。规则不变 ——
# **一旦 v3 上产,再改形状必须 bump**。
# 🔴 **v3 已于 2026-08-11 上产** → V2.3.3 批 ① 换卡 #6 的形状(`scripts` 三格 →
# `upside_path` 一段话)因此**必须** bump 到 v4,上面那条规则原样兑现。
# ⚠ 库里 v3 老卡照常读回:`_upside_path_present()` 的判据是 **`upside_path` 或
# 老 `scripts` 任一格非空** 的 OR(冻结卡 `INSERT OR IGNORE` 永不回填新键,只读新键
# 会让昨天冻的那批篮子今天开仓时全部"缺上涨判断" = 凭空多一条假警示)。
# 🔴 **v4 已于 2026-08-12 上产**(V2.3.3)→ V2.4.0 P1.5+ 改了 `tier_breakdown.gates`
# 的形状,因此**必须** bump 到 v5。形状变更**恰好三处**(`tier.py::_gate_breakdown`):
#   ① 增**逐关 `gate_available`** 映射 —— 格级「判不出」终于查得到(补上 `CLAUDE.md`
#      明载的「六关的判不出是篮级不是格级」那个缺口;老形状里 `unknown` 长得跟 `pass`
#      一模一样,界面上是把「没看」讲成了「没问题」);
#   ② 增逐关 `gate_support` / `gate_counter_evidence` / `gate_missing`(P1.5+ 结构化
#      检查的留痕,P3.4 审计层的原料);
#   ③ 增 `t2_formal_policy` / `has_unavailable`(这一档当时按哪套 T2 规则定的、有没有
#      判不出的关)。⚠ `removed_members` 键**早就在**(v3 起),只是 P1.4 之后才真的
#      会非空 —— 那不是形状变更,别把它算成第四处。
# ⚠ 老 v4 / v3 卡照常读回:新键缺席时消费方按老形状读(`_upside_path_present()` 的
# OR 体例),⛔ 不许让昨天冻的卡今天全部"缺件"。
CARD_SPEC_VERSION = "basket_card_v5"
VERIFY_SPEC_VERSION = "basket_verify_v2"
INVALIDATE_SPEC_VERSION = "basket_invalidate_v2"

# —— 固定文案单一源(蓝图 4.6 第 11 项「客户端原样透传不改写」)————————————
BASKET_CARD_DISCLAIMER = (
    "参考,非指令 —— 买卖与终选在你,系统不代下单;纪律以章程为准。"
)

# —— 夹逼四态(v1.5 参考件 ①-C 体例逐条平移;「没给」与「给了被拦」分开)————
CLAMP_OK = "ok"
CLAMP_ABSENT = "absent"
CLAMP_REJECTED_OUT_OF_LIMIT = "rejected_out_of_limit"
CLAMP_REJECTED_MALFORMED = "rejected_malformed"
CLAMP_REJECTED_NO_LIMIT = "rejected_no_limit"
# ⑪-D-B 闸①(2026-08-03 planner 裁定):离场参考**必须高于 D0 收盘**。语义驱动、
# 零发明阈值 —— 离场参考按定义是「本轮上涨的压力位」,压力位在现价之上;
# `exit_low ≤ D0 close` 的东西根本不是离场参考。⚠ 这**不是**给它加涨跌停夹逼
# (⑦ 原决定不变:压力位可能几个交易日后才到),只管**下界的语义合法性**。
CLAMP_REJECTED_NOT_ABOVE_CLOSE = "rejected_not_above_close"
# ⑪-D 闸① 的「没有」态:D0 收盘价本身算不出 → **无从核对**,与「核对了不满足」
# 刻意分成两个码(项目一贯的「没有 ≠ 不满足」纪律,同 ⑧-E `anchor_unconfirmed`)。
# 处置与 rejected 家族相同(不落卡),但审计时能一眼分清是被拦还是没得比。
# ⚠ 非 plan 逐字要求,是 builder 补的第三态,已在 ⑪-D 完工记录如实登记。
CLAMP_REJECTED_NO_CLOSE = "rejected_no_close"

_CLAMP_REASON_TEXT: Dict[str, str] = {
    CLAMP_ABSENT: "本次未生成该项",
    CLAMP_REJECTED_OUT_OF_LIMIT: "生成的数字超出次日涨跌停范围,已拦截",
    CLAMP_REJECTED_MALFORMED: "生成的数字格式不合法或自相矛盾,已拦截",
    CLAMP_REJECTED_NO_LIMIT: "无法算出次日涨跌停价,该项不显示",
    CLAMP_REJECTED_NOT_ABOVE_CLOSE: "生成的离场参考不高于当日收盘价(压力位按定义在现价之上),已拦截",
    CLAMP_REJECTED_NO_CLOSE: "当日收盘价算不出,无从核对该离场参考,该项不显示",
}

# 离场参考只校验格式 + ⑪-D 闸①(不夹涨跌停),复用上面的码,语义不新造。
EXIT_CLAMP_OK = CLAMP_OK
EXIT_CLAMP_ABSENT = CLAMP_ABSENT
EXIT_CLAMP_REJECTED_MALFORMED = CLAMP_REJECTED_MALFORMED
EXIT_CLAMP_REJECTED_NOT_ABOVE_CLOSE = CLAMP_REJECTED_NOT_ABOVE_CLOSE
EXIT_CLAMP_REJECTED_NO_CLOSE = CLAMP_REJECTED_NO_CLOSE

# —— LLM 段状态(同 ⑤/⑥ 的三态精神:「没做」与「做了没结果」不合并)——————
LLM_OK = "ok"
LLM_NO_PROVIDER = "no_provider"
LLM_CALL_FAILED = "call_failed"
LLM_BUDGET_EXHAUSTED = "budget_exhausted"
LLM_PARSE_FAILED = "parse_failed"
LLM_DISABLED = "disabled"          # 调用方显式 `use_llm=False`(单测/离线冒烟)

# —— 验证 / 失效结构化条件码与聚合规则:**唯一定义处 = `verification_rules.py`**
#    (⑦-b 落地要求「集中到一处」),本模块只**再导出**同名常量,不另抄一份 ——
#    照 `aggregate.py` 再导出 `save_baskets` / `json_block` 搬迁的既有体例,⑦ 的
#    既有调用点与单测按符号名引用即可、不必改。————————————————————————————
COND_CLOSE_AT_OR_ABOVE_REF = vr.COND_CLOSE_AT_OR_ABOVE_REF
COND_HOLDS_MA20 = vr.COND_HOLDS_MA20
COND_CLOSE_BELOW_STOP_LINE = vr.COND_CLOSE_BELOW_STOP_LINE
COND_LIMIT_DOWN_TOUCH = vr.COND_LIMIT_DOWN_TOUCH
# ⑦-b-B 修订:原 `COND_CLOSE_BELOW_MA20`(单条「收盘 < MA20」)**已退役** —— 它与
# 验证侧的「≥MA20」互为反面,擦边跌破就判证伪。现为复合条件(< D0 收盘 **且**
# < D0 MA20),理由见 `verification_rules` 该常量注释。
COND_BELOW_REF_AND_MA20 = vr.COND_BELOW_REF_AND_MA20
VERIFICATION_RULESET_VERSION = vr.VERIFICATION_RULESET_VERSION

_COND_DESC: Dict[str, str] = vr.COND_DESC


# ══════════════════════════════════════════════════════════════════════════
# 机械件:每只成员的价量锚 + 涨跌停 + 止损线
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MemberMech:
    """一只成员在 D0 的机械锚点(**全部机械算,零 LLM**)。任一项算不出就是 `None`,
    **不猜、不补默认值** —— 下游据此把对应的卡面项落 `rejected_no_limit` / 该条
    结构化条件置 `null`(⑧ 见到 `null` 就跳过这一条,不当成"不满足")。"""

    ts_code: str
    name: str = ""
    close: Optional[float] = None
    ma20: Optional[float] = None
    limit_up: Optional[float] = None
    limit_down: Optional[float] = None
    stop_price: Optional[float] = None
    no_limit_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts_code": self.ts_code, "name": self.name, "close": self.close,
            "ma20": self.ma20, "limit_up": self.limit_up, "limit_down": self.limit_down,
            "stop_price": self.stop_price, "no_limit_reason": self.no_limit_reason,
        }


def resolve_charter_pcts(db_path: Optional[Path] = None) -> Tuple[Optional[float], Optional[float]]:
    """现役章程的两个口径指纹 `(stop_pct, take_profit_retrace)`。**唯一源 = 现役
    `strategy_versions` config**(§2.1),一次 `active_config` 读两个。任一未配置 →
    该位 `None`,纪律标签退化成不带数字的说法,**不拿字面量补位**(禁硬编 0.05/0.08)。
    读库失败也返回 `(None, None)` —— 口径指纹缺失是可如实表达的状态,不该掀翻卡生成。
    """
    try:
        cfg = brain.active_config(db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[basket_card] 读现役章程 config 失败,口径指纹本次为空", exc_info=True)
        return None, None

    def _ratio(v: Any) -> Optional[float]:
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    return _ratio(cfg.get("stop_pct")), _ratio(cfg.get("take_profit_retrace"))


def resolve_loss_warning(db_path: Optional[Path] = None) -> Tuple[Optional[float], Optional[str]]:
    """现役章程的**对外退出语义**指纹 `(loss_warning_pct, loss_warning_action)`
    (K8.md §十九,`v2.3-k8` 起;V2.3.2-⑤)。

    **唯一源同 `resolve_charter_pcts`** = 现役 `strategy_versions` config。老章程行
    (`v2.2-k8` 及以前)没有这两个字段 → 两位都是 `None` = **该章程没有声明过这个语义**,
    ⛔ 不是"声明为强制条件单",更不拿 `stop_pct` 的值顶上去 —— 顶上去就等于替一版没说过
    这话的章程发言。读库失败同样返 `(None, None)`,不掀翻卡生成(与上面同一姿势)。

    ⚠ 它**不参与任何判定**:止损价仍由 `stop_pct` 算(⑤ 明写「值与唯一源地位一字不动」),
    这两位只进卡的口径指纹,供事后回看「这张卡是在哪套对外语义下产出的」。
    """
    try:
        cfg = brain.active_config(db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[basket_card] 读现役章程 config 失败,退出语义指纹本次为空", exc_info=True)
        return None, None
    pct = cfg.get("loss_warning_pct")
    action = cfg.get("loss_warning_action")
    return (
        float(pct) if isinstance(pct, (int, float)) and not isinstance(pct, bool) else None,
        action if isinstance(action, str) and action else None,
    )


def discipline_labels(stop_pct: Optional[float], take_profit_retrace: Optional[float]) -> List[str]:
    """卡上的纪律标签(**动态生成**,plan 明文:「缺指纹就退化成不带数字的说法,
    禁把『−5%』『8%』写进模板」)。章程一改,标签跟着走。"""
    stop = (f"章程止损 −{stop_pct:.1%}" if stop_pct is not None
            else "章程止损(现役章程未配置比例)")
    tpr = (f"回落止盈 {take_profit_retrace:.1%}" if take_profit_retrace is not None
           else "回落止盈(现役章程未配置比例)")
    return [stop, tpr]


def build_member_mech(
    codes_close: Mapping[str, Optional[float]],
    trade_date: date,
    *,
    stop_pct: Optional[float],
    names: Optional[Mapping[str, str]] = None,
    ma20_of: Optional[Mapping[str, Optional[float]]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, MemberMech]:
    """逐票算 D0 机械锚点。`codes_close` = `ts_code -> D0 收盘价`(前复权口径与报告
    同源;`aggregate.MechContext` 已有一份,调用方传进来别重算)。

    涨跌停用**次日**(`next_trading_day(D0)`)算 —— ST 幅度有制度分界日,传错日期会
    取错幅度(v1.5 参考件 `_resolve_next_day_limit_prices` 同一条注意事项)。
    `board`/`is_st` 唯一源 `load_stock_meta`,不自判 ST 前缀 / 不自分板块。
    """
    wanted = [c for c in codes_close if c]
    out: Dict[str, MemberMech] = {}
    if not wanted:
        return out

    try:
        meta_map = load_stock_meta(list(wanted), db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[basket_card] load_stock_meta 失败,本批全体算不出涨跌停", exc_info=True)
        meta_map = {}
    try:
        next_day: Optional[date] = next_trading_day(trade_date)
    except Exception:  # noqa: BLE001
        logger.warning("[basket_card] 算不出 %s 的下一交易日,本批全体算不出涨跌停",
                       trade_date, exc_info=True)
        next_day = None

    for code in wanted:
        close = codes_close.get(code)
        close_f = float(close) if isinstance(close, (int, float)) and not isinstance(close, bool) else None
        if close_f is not None and not (math.isfinite(close_f) and close_f > 0):
            close_f = None
        meta = meta_map.get(code)
        name = (names or {}).get(code) or (meta.name if meta is not None else "") or code
        ma20 = (ma20_of or {}).get(code)
        ma20_f = float(ma20) if isinstance(ma20, (int, float)) and not isinstance(ma20, bool) else None
        if ma20_f is not None and not math.isfinite(ma20_f):
            ma20_f = None

        limit_up = limit_down = None
        reason: Optional[str] = None
        if close_f is None:
            reason = "基准日收盘价缺失或非正,无法算涨跌停"
        elif meta is None:
            reason = "查无股票元数据(stock_basic 缺该代码),无法判定板块 / 是否 ST"
        elif next_day is None:
            reason = "无法确定下一交易日(交易日历数据异常)"
        else:
            limit_up, limit_down = compute_intraday_limit_prices(
                close_f, meta.board, meta.is_st, next_day)
            if limit_up is None or limit_down is None:
                reason = "涨跌停价计算返回空"

        stop_price = (round(close_f * (1 - stop_pct), 2)
                      if (stop_pct is not None and close_f is not None) else None)
        out[code] = MemberMech(
            ts_code=code, name=name, close=close_f, ma20=ma20_f,
            limit_up=limit_up, limit_down=limit_down, stop_price=stop_price,
            no_limit_reason=reason,
        )
    return out


# ══════════════════════════════════════════════════════════════════════════
# 结构化 spec(⑧ 篮子验证状态机的**唯一判据源**;先算、再喂 LLM)
# ══════════════════════════════════════════════════════════════════════════

def _min_members_hit(n: int) -> int:
    """篮子级聚合门槛(**唯一定义在 `verification_rules.min_members_hit`**,⑦-b 裁定
    后本函数只是本模块内的别名,行为逐位不变:`ceil(n/2)`,验证 / 失效两侧同一个数)。"""
    return vr.min_members_hit(n)


def build_verification_spec(
    basket_key: str,
    trade_date: date,
    mechs: Sequence[MemberMech],
    *,
    next_trade_date: Optional[date] = None,
    min_members_hit: Optional[int] = None,
) -> Dict[str, Any]:
    """「篮子验证条件」结构化半份 —— **⑧ 盘中 / EOD 判定的唯一阈值来源**,不另写
    一份阈值。只用**价量结构**(承 §2.4 铁律:盘中主力资金流免费源不可靠)。

    读法(写给 ⑧ 看):对每个成员,`require` 里列的条件**全部满足**才算这只命中;
    命中数 ≥ `min_members_hit` → 篮子这一侧成立。某成员的某条阈值是 `null` =
    **该条对该成员不判**(基准数据算不出,「没有」与「没看」分得开),**不许当成
    "不满足"** —— 于是 `require` 里只要有一条判不了、又没有哪一条已经确定不满足,
    这只成员**这一侧就整体不下结论、不计命中**(⑧ 的 Kleene 三值,判定线审计 🟡-1
    定案;⑧ 会打 `spec_levels_partial`)。

    ⚠ **`evaluable_members` 只是留痕计数,不是第二道门槛**(判定线审计 🔵-1 更正):
    本字段记「D0 当天至少算得出一条锚的成员数」,**⑧ 从不读它** —— 聚合门槛
    `min_members_hit` 按**全员数**算,阈值全 null 的成员照占分母。这是刻意的保守方向
    (缺数据多到够不着门槛 → 落 `unclear`,⑦-b 原文),不是漏实现。原 docstring 写的
    「该成员本次不计入分母」是**未兑现的承诺**,已按实际行为改口;⛔ 别照旧注释去
    "补全"出第二套门槛。
    """
    members: List[Dict[str, Any]] = []
    evaluable = 0
    for m in mechs:
        row = {
            "ts_code": m.ts_code,
            "ref_close": m.close,
            COND_CLOSE_AT_OR_ABOVE_REF: m.close,
            COND_HOLDS_MA20: m.ma20,
        }
        if m.close is not None or m.ma20 is not None:
            evaluable += 1
        members.append(row)
    n = len(members)
    return {
        "spec_version": VERIFY_SPEC_VERSION,
        "ruleset_version": VERIFICATION_RULESET_VERSION,
        "basket_key": basket_key,
        "trade_date": trade_date.strftime("%Y%m%d"),
        "next_trade_date": next_trade_date.strftime("%Y%m%d") if next_trade_date else None,
        "member_count": n,
        "evaluable_members": evaluable,
        "min_members_hit": int(min_members_hit) if min_members_hit is not None else _min_members_hit(n),
        "require": list(vr.VERIFY_REQUIRE_ALL),
        "conditions": vr.conditions_block(vr.VERIFY_REQUIRE_ALL),
        "members": members,
    }


def build_invalidation_spec(
    basket_key: str,
    trade_date: date,
    mechs: Sequence[MemberMech],
    *,
    next_trade_date: Optional[date] = None,
    stop_pct: Optional[float] = None,
    min_members_hit: Optional[int] = None,
) -> Dict[str, Any]:
    """「篮子失效条件」结构化半份 —— 同上,**⑧ 的唯一判据源**。`any_of` 里任一条
    命中即算该成员失效;失效成员数 ≥ `min_members_hit` → 篮子这一侧成立。

    ⚠ `close_below_stop_line` 用的是**现役章程 `stop_pct` 算出的价位**(系统算、
    不由 LLM 给)。它在这里的用途是**判「这个篮子的驱动是不是被证伪」**,与哨兵对
    真实持仓执行的止损纪律是两回事:本 spec 不触发任何交易动作,也不改任何纪律参数
    (§2.0 第〇原则)。同一个数字、同一个单一源,不是第二套阈值。
    """
    members: List[Dict[str, Any]] = []
    evaluable = 0
    for m in mechs:
        row = {
            "ts_code": m.ts_code,
            "ref_close": m.close,
            COND_CLOSE_BELOW_STOP_LINE: m.stop_price,
            COND_LIMIT_DOWN_TOUCH: m.limit_down,
            # 复合条件(⑦-b-B):两个子阈值一起给,**任一为 null 则整条不判**
            # (半条判不了就整条不判,不猜)。
            COND_BELOW_REF_AND_MA20: {vr.LEVEL_REF_CLOSE: m.close, vr.LEVEL_MA20: m.ma20},
        }
        if m.stop_price is not None or m.ma20 is not None or m.limit_down is not None:
            evaluable += 1
        members.append(row)
    n = len(members)
    return {
        "spec_version": INVALIDATE_SPEC_VERSION,
        "ruleset_version": VERIFICATION_RULESET_VERSION,
        "basket_key": basket_key,
        "trade_date": trade_date.strftime("%Y%m%d"),
        "next_trade_date": next_trade_date.strftime("%Y%m%d") if next_trade_date else None,
        "member_count": n,
        "evaluable_members": evaluable,
        "min_members_hit": int(min_members_hit) if min_members_hit is not None else _min_members_hit(n),
        "any_of": list(vr.INVALIDATE_ANY_OF),
        "stop_pct": stop_pct,
        "conditions": vr.conditions_block(vr.INVALIDATE_ANY_OF),
        "members": members,
    }


def spec_threshold_text(verify: Mapping[str, Any], invalidate: Mapping[str, Any]) -> str:
    """把两份结构化 spec 摊成人读文本,**喂给 LLM**(v1.5-①-A 体例:让人话剧本与
    盘中自动警报同频)。plan 验收有一条专门锁「结构化阈值确实出现在喂给 LLM 的
    上下文里」—— 这个函数就是那条通路,别绕过它另拼一段。"""
    lines = ["——盘中 / 收盘会自动判定的机械阈值(你的剧本与人话条款必须与其同频,不得矛盾)——"]
    lines.append(
        f"验证条件({verify.get('spec_version')}):成员需**同时**满足 "
        + "、".join(_COND_DESC[c] for c in verify.get("require", []))
        + f";命中成员数 ≥ {verify.get('min_members_hit')} 时判本篮被验证。"
    )
    for m in verify.get("members", []):
        ref = m.get(COND_CLOSE_AT_OR_ABOVE_REF)
        ma = m.get(COND_HOLDS_MA20)
        lines.append(
            f"   · {m.get('ts_code')}:收盘需 ≥ "
            + (f"{ref:.2f}" if isinstance(ref, (int, float)) else "(基准价算不出,该条不判)")
            + " 且 ≥ MA20 "
            + (f"{ma:.2f}" if isinstance(ma, (int, float)) else "(算不出,该条不判)")
        )
    lines.append(
        f"失效条件({invalidate.get('spec_version')}):命中**任一**条即该成员失效 —— "
        + "、".join(_COND_DESC[c] for c in invalidate.get("any_of", []))
        + f";失效成员数 ≥ {invalidate.get('min_members_hit')} 时判本篮失效。"
    )
    for m in invalidate.get("members", []):
        stop = m.get(COND_CLOSE_BELOW_STOP_LINE)
        both = m.get(COND_BELOW_REF_AND_MA20) or {}
        ma = both.get(vr.LEVEL_MA20) if isinstance(both, Mapping) else None
        ref = both.get(vr.LEVEL_REF_CLOSE) if isinstance(both, Mapping) else None
        down = m.get(COND_LIMIT_DOWN_TOUCH)
        lines.append(
            f"   · {m.get('ts_code')}:止损线 "
            + (f"{stop:.2f}" if isinstance(stop, (int, float)) else "(章程比例未配置,该条不判)")
            + " / 破位需**同时**低于基准收盘 "
            + (f"{ref:.2f}" if isinstance(ref, (int, float)) else "(算不出,该条不判)")
            + " 与 MA20 "
            + (f"{ma:.2f}" if isinstance(ma, (int, float)) else "(算不出,该条不判)")
            + " / 跌停 "
            + (f"{down:.2f}" if isinstance(down, (int, float)) else "(算不出,该条不判)")
        )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# 夹逼(唯一底线,防幻觉)
# ══════════════════════════════════════════════════════════════════════════

def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def clamp_entry_zone(
    raw: Any, limit_up: Optional[float], limit_down: Optional[float]
) -> Tuple[Optional[float], Optional[float], str]:
    """建仓观察区间夹逼。返回 `(low, high, clamp)`。判定优先级**刻意如此,不是随意**
    (v1.5 `_clamp_buy` 逐条平移):① 压根没给 → `absent`(**不管涨跌停算不算得出**
    ——「没给」与「给了被拦」是两件事);② 给了但数字非法(非数 / NaN / 缺一个 /
    `low>high` / 非正)→ `rejected_malformed`;③ 数字合法但算不出涨跌停 →
    `rejected_no_limit`;④ 越界 → `rejected_out_of_limit`;⑤ 通过 → `ok`。"""
    if not isinstance(raw, Mapping):
        return None, None, CLAMP_ABSENT
    low, high = raw.get("low"), raw.get("high")
    if low is None and high is None:
        return None, None, CLAMP_ABSENT
    if not (_finite(low) and _finite(high)) or not (0 < float(low) <= float(high)):
        return None, None, CLAMP_REJECTED_MALFORMED
    low, high = float(low), float(high)
    if limit_up is None or limit_down is None:
        return None, None, CLAMP_REJECTED_NO_LIMIT
    if not (limit_down <= low <= limit_up and limit_down <= high <= limit_up):
        return None, None, CLAMP_REJECTED_OUT_OF_LIMIT
    return round(low, 2), round(high, 2), CLAMP_OK


def clamp_max_chase(
    raw: Any, limit_up: Optional[float], limit_down: Optional[float],
    *, zone_high: Optional[float] = None,
) -> Tuple[Optional[float], str]:
    """最高追价夹逼。同 `clamp_entry_zone` 的四态,外加一条**自洽校验**:若建仓区间
    也过了闸,则「最高追价 < 区间上沿」是自相矛盾(追价上限比观察区间还低,读者无从
    执行)→ 记 `rejected_malformed`。**区间与追价各自独立落态**,一个被拦不牵连另一个。
    """
    if raw is None:
        return None, CLAMP_ABSENT
    if not _finite(raw) or float(raw) <= 0:
        return None, CLAMP_REJECTED_MALFORMED
    price = float(raw)
    if limit_up is None or limit_down is None:
        return None, CLAMP_REJECTED_NO_LIMIT
    if not (limit_down <= price <= limit_up):
        return None, CLAMP_REJECTED_OUT_OF_LIMIT
    if zone_high is not None and price < float(zone_high):
        return None, CLAMP_REJECTED_MALFORMED
    return round(price, 2), CLAMP_OK


def clamp_exit_reference(raw: Any, close: Optional[float]) -> Tuple[Optional[float], Optional[float], str]:
    """离场参考的格式校验 + **⑪-D-B 闸①**(`exit_low > D0 close`)。**仍不夹涨跌停、
    仍不加上界**(plan 明文两条:压力位可能几个交易日后才到,拿明日涨跌停夹它是错的;
    `exit_high` 荒谬地高只会永不触发,无假推送无伤害,⛔ 别为它发明上限)。

    五态,判定优先级与 `clamp_entry_zone` 同构:`absent`(压根没给)→
    `rejected_malformed`(数字非法 / 自相矛盾)→ `rejected_no_close`(数字合法但
    D0 收盘算不出,**无从核对**)→ `rejected_not_above_close`(核对了,不高于收盘)
    → `ok`。

    **为什么这道闸必须在这里**(⑪-D-A):这份数字经 ⑩ 开仓继承会成为 APNs
    `take_profit` kind 的**触发位置**(§2.8-C-3 的记名豁免)——一个没有下限的 LLM
    数字驱动立即级推送,极端例 `exit_low=0.01` 买入后第一拍即推,喂给「立即级」通道
    的噪声比不推更糟。**这不是发明阈值,是定义**:离场参考 = 本轮上涨的压力位,
    压力位在现价之上。

    `close` 是**必填位置参数**(不给默认值):这是一道红线闸,调用方"忘了传"就等于
    静默关闸,签名层面不留这个口子(与 ⑧-E `MemberObservation.pre_close` 那种
    "老调用点安全降级"的可选锚**刻意相反** —— 那是加检测,这是加闸)。
    """
    if not isinstance(raw, Mapping):
        return None, None, EXIT_CLAMP_ABSENT
    low, high = raw.get("low"), raw.get("high")
    if low is None and high is None:
        return None, None, EXIT_CLAMP_ABSENT
    if not (_finite(low) and _finite(high)) or not (0 < float(low) <= float(high)):
        return None, None, EXIT_CLAMP_REJECTED_MALFORMED
    if not _finite(close) or float(close) <= 0:
        return None, None, EXIT_CLAMP_REJECTED_NO_CLOSE
    # 浮点容差复用 `verification_rules.EPS`(本模块唯一的容差源,⛔ 不另立一份;
    # 恰好等于收盘 → 拦,压力位不能就是现价本身)。
    if float(low) <= float(close) + vr.EPS:
        return None, None, EXIT_CLAMP_REJECTED_NOT_ABOVE_CLOSE
    return round(float(low), 2), round(float(high), 2), EXIT_CLAMP_OK


def clamp_reason_text(clamp: str) -> Optional[str]:
    """人读理由(与 clamp 码一一对应,**单一源**,不由渲染层各自拍文案)。"""
    return None if clamp == CLAMP_OK else _CLAMP_REASON_TEXT.get(clamp, clamp)


# ══════════════════════════════════════════════════════════════════════════
# LLM 段(TASK_SCRIPT → ② 路由;不联网;预算走推理账)
# ══════════════════════════════════════════════════════════════════════════

# ⚠ `TIMELINESS_RULES` 必须内嵌(判定线审计 🔵-3;§五铁律「日期锚 + 时效纪律」,
# 与 ⑤⑥⑪ 同体例):本段**不联网**,但资料里的驱动证据是检索环节更早一步取回的
# **带日期**条目,模型照样可能把一条几个月前的旧证据当"现在正在发生"来讲。日期锚在
# user 消息第一行(`build_card_context`),时效纪律在这里,两半凑齐才算齐。
CARD_SYSTEM_PROMPT = """你是「颈线」系统的盘后篮子参谋。系统本身只做审计、不代客下单,读者是一位短线交易者。
你现在要为**一个已经定好的股票篮子**(1—3 只同驱动的票)写卡面上"给人看"的那几段。

信息边界(铁律,不可违反):
1. 你只能依据下面提供的结构化资料做判断。**本次没有联网检索工具**——驱动证据已由检索环节在更早
一步取得并原样列在资料里,你不得补充资料之外的新闻、公告、传闻或题材,也不得编造数字。
2. 资料里标注「算不出」「未知」的项,就照实当作未知,**不要用推测填补**。
3. 系统的选股规则是一套减损纪律系统而非高胜率信号。你的角色是把已有依据讲清楚、把风险讲透,
不是给出收益预测,不要暗示"这个篮子会涨"。

**你产出的一切都只是参考,不是指令**——买卖时机、价格与是否下单的终选权始终在用户,系统永远不会
代替用户下单。**不得使用"止盈线""目标价""建议买入""推荐买点""必涨"这类措辞**;止损是系统按现役
章程自动算的,**不要给出任何止损数字**(资料里已经给了,你只能引用,不能另给一个)。

""" + TIMELINESS_RULES + """

输出格式(两部分,顺序不可颠倒,中间空一行):

第一部分:一段连贯的自由叙述,像分析师口头点评这个篮子——为什么这几只、谁强谁弱、分歧在哪。
禁止分点列表与"技术面/资金面/消息面"这类固定分栏模板。

第二部分:一个 ```json 围栏代码块,严格是下面这个形状(不要多余字段,不要在围栏外重复):

```json
{"upside_path": "一段话:驱动怎么推动价格、预期沿什么结构与节奏往上走、走到哪算走完",
 "entries": [{"ts_code": "股票代码",
              "low": 数字, "high": 数字,
              "max_chase": 数字,
              "exit_low": 数字, "exit_high": 数字,
              "why": "一两句话说明这个建仓观察区间怎么来的"}],
 "verification": "用人话写:什么样的次日表现算这个篮子的逻辑被验证了",
 "invalidation": "用人话写:什么样的次日表现算这个篮子的逻辑失效了",
 "risks": ["主要风险一", "主要风险二"],
 "tier_note": "一句话说明这个档位与档内次序合不合理(可为 null)"}
```

硬约束:
· `upside_path` 讲的是**路径**——驱动怎么推动价格、预期沿什么结构与什么节奏往上走、走到哪算走完。
**⛔ 不许写成"明早高开怎么办 / 低开怎么办"的分支指引**:开盘那一刻的解释由**次日 9:26 的集合竞价
确认层**负责,不是这里的事。**一段话,不分支。**
· `entries` 只能包含资料里列出的成员代码,**多一个都会被系统整条丢弃**。
· `low` / `high` / `max_chase` **必须落在资料给出的该票「次日涨跌停参考价」闭区间内**,
超出的会被系统丢弃、不展示给用户;且必须满足 `low ≤ high ≤ max_chase`。
· `exit_low` / `exit_high` 是本轮上涨的压力位参考,**不受涨跌停约束**(可能几个交易日后才触及),
但 `exit_low` **必须严格高于资料里给出的该票「今日收盘价」**——压力位按定义在现价之上,
不高于收盘的会被系统丢弃、不展示给用户;且必须满足 `exit_low ≤ exit_high`。
它不是止盈线——回落止盈是系统纪律,独立生效、不受你的判断影响。
· `verification` / `invalidation` 两段人话**必须与资料里给出的机械阈值同频**,不得给出与之矛盾的
说法;那些阈值是盘中自动判定用的,你写的是同一件事的人话版本。
· 某一项确实无法给出合理数字时,**宁可该字段写 null,也不要编造**。
"""


def build_card_context(
    basket: Any,
    trade_date: date,
    mechs: Mapping[str, MemberMech],
    verify_spec: Mapping[str, Any],
    invalidate_spec: Mapping[str, Any],
    *,
    tier_decision: Any = None,
    tag_batch: Optional[mt.MemberTagBatch] = None,
    discipline: Optional[Sequence[str]] = None,
) -> str:
    """喂给 LLM 的上下文(纯文本块,不是 JSON —— 同 `judge.build_context_block` /
    `aggregate.build_reason_context` 的理由:降低模型把它误当输出模板抄回来的概率)。

    **顺序是有意义的**:日期锚(`prompt_context` 唯一实现)→ 篮子身份与 ⑤ 已产出的
    驱动/证据/为什么是现在 → ⑥ 的定档与五维 → 逐票机械数据(含次日涨跌停闭区间 =
    夹逼的锚)→ ⑦-K7 标注 → **结构化阈值块**(`spec_threshold_text`)→ 出题。
    """
    lines: List[str] = [date_anchor_line(trade_date, name_tomorrow=True), ""]
    lines.append(f"篮子:{getattr(basket, 'name', '')}(basket_key {getattr(basket, 'basket_key', '')})")
    lines.append(f"共同驱动({getattr(basket, 'driver_kind', '')}):{getattr(basket, 'driver', '')}")
    lines.append(f"为什么是现在:{getattr(basket, 'why_now', '') or '(上一环节未产出)'}")

    evidence = list(getattr(basket, "evidence", ()) or ())
    status = getattr(basket, "evidence_status", "")
    if evidence:
        lines.append(f"驱动证据(检索状态 {status},每条带日期):")
        for e in evidence:
            url = f" <{e.url}>" if getattr(e, "url", "") else ""
            lines.append(f"   · [{e.date}] {e.claim}(来源:{e.source}){url}")
    else:
        lines.append(f"驱动证据:本次无条目(检索状态 {status or '未知'})——不得据此编造证据。")

    if tier_decision is not None:
        dims = (getattr(tier_decision, "breakdown", {}) or {}).get("dims", {})
        dim_txt = "、".join(f"{k} {float(v):.2f}" for k, v in sorted(dims.items())) or "(无)"
        lines.append(
            f"定档:T{getattr(tier_decision, 'tier', '?')} 档内第 "
            f"{getattr(tier_decision, 'rank_in_tier', '?')} 位"
            f"(机械序第 {getattr(tier_decision, 'rank_mech', '?')} 位,"
            f"机械分 {float(getattr(tier_decision, 'mech_score', 0.0)):.3f});五维:{dim_txt}"
        )
        if getattr(tier_decision, "llm_reason", None):
            lines.append(f"   同档次序备注:{tier_decision.llm_reason}")

    lines.append("")
    lines.append("成员逐票机械数据(**建仓区间与最高追价必须落在各自的次日涨跌停闭区间内**):")
    for m in getattr(basket, "members", ()) or ():
        code = m.ts_code
        mech = mechs.get(code)
        close_s = f"{mech.close:.2f}" if (mech and mech.close is not None) else "未知"
        ma20_s = f"{mech.ma20:.2f}" if (mech and mech.ma20 is not None) else "未知"
        if mech and mech.limit_up is not None and mech.limit_down is not None:
            band = f"次日涨跌停参考价 [{mech.limit_down:.2f}, {mech.limit_up:.2f}]"
        else:
            band = f"次日涨跌停算不出({(mech.no_limit_reason if mech else '缺机械数据')})——" \
                   "本票的建仓区间与最高追价将不会展示给用户"
        stop_s = f"{mech.stop_price:.2f}" if (mech and mech.stop_price is not None) else "未配置"
        role_mech = getattr(m, "role_mech", None) or "未判定"
        conflict = "(⚠ 与机械侧角色对拍不一致,两说并存)" if getattr(m, "role_conflict", 0) else ""
        lines.append(
            f"   · {code} {getattr(m, 'name', '') or ''}|角色 模型={m.role_llm}/机械={role_mech}{conflict}"
        )
        lines.append(f"       收盘 {close_s}、MA20 {ma20_s}、章程止损线 {stop_s};{band}")
        if getattr(m, "reason", ""):
            lines.append(f"       上一环节给的成员理由:{m.reason}")
        if getattr(m, "k4_tag", None):
            lines.append("       风险标注:命中 K4 黄牌分区(avoid_flag)——机器不禁、人需复核")
        if tag_batch is not None:
            res = tag_batch.get(code)
            if res.tags:
                lines.append("       结构标注(只作参考,不参与任何排序与去留):"
                             + "、".join(f"{t.label}" for t in res.tags))

    lines.append("")
    lines.append(spec_threshold_text(verify_spec, invalidate_spec))
    if discipline:
        lines.append("纪律标签(系统按现役章程动态生成,你只能引用不能另给):" + "、".join(discipline))
    lines.append("")
    lines.append("请按上面的输出格式,先写自由叙述,再给 json 围栏块。")
    return "\n".join(lines)


def run_card_llm(
    context_text: str,
    *,
    provider: Optional[LLMProvider],
    ledger: BudgetLedger,
    transport: Optional[Any] = None,
    system_prompt: str = CARD_SYSTEM_PROMPT,
) -> Tuple[str, Optional[Dict[str, Any]], str]:
    """卡的 LLM 段一次调用,返回 `(叙述, payload or None, 段状态)`。

    · 路由走 **`TASK_SCRIPT`**(② 的任务常量单一源;由调用方 `get_provider(TASK_SCRIPT)`
      解析出 provider 后传进来 —— 本函数不碰工厂,方便单测直接注入桩)。
    · **不联网**(`enable_search=False`):证据在 ⑤ 的检索段已经取过,⑦ 只做归纳与
      表达,不需要新证据,也不该再花一次检索预算。
    · 预算走**推理账** `LEDGER_REASON`(② 的三本账之一)。⚠ 卡冻结本身**永不在可丢
      清单里**(`budget.NEVER_DROPPED` 含 `basket_card_freeze`)—— 那说的是"卡这件事
      不因预算被跳过",不是"卡的 LLM 段不记账";预算耗尽时卡照出,只是人话半份缺席。
    · **先剥 JSON 再谈解析**(v1.5.1 标签劫持案),且本链路**没有**结论标签,不复用
      `judge._parse_verdict`。
    """
    if provider is None:
        return "", None, LLM_NO_PROVIDER
    if ledger.exhausted(LEDGER_REASON):
        return "", None, LLM_BUDGET_EXHAUSTED

    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=context_text),
    ]
    started = time.monotonic()
    try:
        result = provider.chat(messages, enable_search=False, transport=transport)
    except Exception as exc:  # noqa: BLE001
        ledger.spend(LEDGER_REASON, time.monotonic() - started)
        logger.warning("[basket_card] 卡生成调用抛异常,本卡人话半份缺席", exc_info=True)
        return "", None, f"{LLM_CALL_FAILED}:{type(exc).__name__}"
    ledger.spend(LEDGER_REASON, time.monotonic() - started)

    if not getattr(result, "ok", False):
        return "", None, f"{LLM_CALL_FAILED}:{getattr(result, 'reason', '')}"

    narrative, payload = split_narrative_and_reference_json(result.content or "")
    if not isinstance(payload, dict):
        logger.warning("[basket_card] 卡生成输出解不出 JSON 块,本卡人话半份缺席")
        return narrative, None, LLM_PARSE_FAILED
    return narrative, payload, LLM_OK


# ══════════════════════════════════════════════════════════════════════════
# 卡装配
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MemberCardEntry:
    """卡上「成员、角色与比较结果」节的一行(= 蓝图 4.6 第 4 项 + 第 7 项的逐票半)。"""

    ts_code: str
    name: str
    role_llm: str
    role_mech: Optional[str]
    role_conflict: int
    reason: str
    is_primary: int
    industry: Optional[str]
    industry_lift: Optional[float]
    lift_reason: Optional[str]
    primary_reason: Optional[str]
    rs_rank: Optional[int]
    k4_tag: Optional[str]
    mech: MemberMech
    # V2.2-③-C 位置关(裁定 #11:判定交 LLM、只降级不除名)。`position_metrics` 是
    # **当次喂给模型的那份读数原样** —— 卡是 D0 冻结件,存在这里 = 事后复核「它拿
    # 什么下的判断」不必回头猜(与 `gate_evaluations.evidence_json` 互为两处留痕)。
    position_verdict: str = ""
    position_reason: str = ""
    position_metrics: Optional[Dict[str, Any]] = None
    # V2.2-③-C2 核心关(裁定 #12:核心关也退出机械闸,判定交 LLM、只降级不除名)。
    # 同款理由:卡是 D0 冻结件,把**当次读数**存在这里 = 事后复核「它拿什么下的判断」
    # 不必回头猜(与 `gate_evaluations.evidence_json` 互为两处留痕)。
    core_verdict: str = ""
    core_reason: str = ""
    core_metrics: Optional[Dict[str, Any]] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    entry_clamp: str = CLAMP_ABSENT
    entry_why: Optional[str] = None
    max_chase: Optional[float] = None
    max_chase_clamp: str = CLAMP_ABSENT
    exit_low: Optional[float] = None
    exit_high: Optional[float] = None
    exit_clamp: str = EXIT_CLAMP_ABSENT
    tags: Tuple[mt.MemberTag, ...] = ()
    tags_absent: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        d = self.mech.to_dict()
        d.pop("ts_code", None)
        d.pop("name", None)
        return {
            "ts_code": self.ts_code,
            "name": self.name,
            "role_llm": self.role_llm,
            "role_mech": self.role_mech,
            "role_conflict": int(self.role_conflict),
            "reason": self.reason,
            "is_primary": int(self.is_primary),
            "industry": self.industry,
            "industry_lift": self.industry_lift,
            "lift_reason": self.lift_reason,
            "primary_reason": self.primary_reason,
            "rs_rank": self.rs_rank,
            "k4_tag": self.k4_tag,
            "position_verdict": self.position_verdict,
            "position_reason": self.position_reason,
            "position_metrics": self.position_metrics,
            "core_verdict": self.core_verdict,
            "core_reason": self.core_reason,
            "core_metrics": self.core_metrics,
            "mech": d,
            "entry_zone": ({"low": self.entry_low, "high": self.entry_high,
                            "why": self.entry_why or ""} if self.entry_clamp == CLAMP_OK else None),
            "entry_zone_clamp": self.entry_clamp,
            "entry_zone_unavailable_reason": clamp_reason_text(self.entry_clamp),
            "max_chase": self.max_chase if self.max_chase_clamp == CLAMP_OK else None,
            "max_chase_clamp": self.max_chase_clamp,
            "max_chase_unavailable_reason": clamp_reason_text(self.max_chase_clamp),
            "exit_reference": ({"low": self.exit_low, "high": self.exit_high}
                               if self.exit_clamp == EXIT_CLAMP_OK else None),
            "exit_reference_clamp": self.exit_clamp,
            "exit_reference_unavailable_reason": clamp_reason_text(self.exit_clamp),
            # ⑦-K7:标注件落在成员节里,**与卡同生共死**(不新建表、不新建列)。
            "tags": [t.to_dict() for t in self.tags],
            "tags_absent": list(self.tags_absent),
        }


@dataclass(frozen=True)
class BasketCard:
    """一张篮子卡(D0 冻结件)。`to_card_json()` 就是 `basket_cards.card_json` 的内容。

    **键形状是给 ⑮ 客户端的契约**:`card_json` 是**写入当时冻住**的历史快照,不会因
    服务端升级而补全新键(CLAUDE.md「落库快照两类论」的第二类,同
    `reviews.result_json`)—— 故客户端解码这份 JSON **必须**手写 `init(from:)` 做
    `decodeIfPresent` 兜底。本模块这一侧的责任是:**键只增不改、语义不复用**,并且
    `spec_version` 恒随形状变化而变。
    """

    basket_key: str
    trade_date: str
    version: int
    name: str
    driver: str
    driver_kind: str
    why_now: str
    evidence: Tuple[Any, ...]
    evidence_status: str
    members: Tuple[MemberCardEntry, ...]
    verification_spec: Dict[str, Any]
    invalidation_spec: Dict[str, Any]
    tier: Optional[int] = None
    rank_in_tier: Optional[int] = None
    rank_mech: Optional[int] = None
    mech_score: Optional[float] = None
    tier_breakdown: Dict[str, Any] = field(default_factory=dict)
    tier_reason: Optional[str] = None
    tier_note: Optional[str] = None
    narrative: str = ""
    # V2.3.3-①(K8.md §十 第 8 项 / §十一 第 1 项):卡 #6 由「次日强 / 平 / 弱三剧本」
    # 换成「预期上涨路径」**一段话**。⚠ 四件套判据码 `upside_script` 字符串**没有跟着
    # 改**(它已写进历史 `position_plans.plan_json` / `trade_clock.entry_plan_json`,
    # 改了会让旧行假装缺件)—— 只有卡键与中文标签变了。
    upside_path: Optional[str] = None
    verification_text: Optional[str] = None
    invalidation_text: Optional[str] = None
    risks: Tuple[str, ...] = ()
    stop_pct: Optional[float] = None
    take_profit_retrace: Optional[float] = None
    # V2.3.2-⑤:对外退出语义指纹(K8.md §十九)。⚠ 与 `stop_pct` **并列而不是取代**它
    # ——`stop_pct` 仍是止损价的唯一算料,这两位只回答「−5% 触发的是什么」。老卡没有
    # 这两键是**正常的**(冻结快照不回填),⛔ 别渲染成"配置丢了"。
    loss_warning_pct: Optional[float] = None
    loss_warning_action: Optional[str] = None
    charter_version: Optional[str] = None
    pack_version: Optional[str] = None
    engine_api_version: Optional[int] = None
    llm_stage: str = LLM_DISABLED
    next_trade_date: Optional[str] = None
    notes: Tuple[str, ...] = ()
    # V2.2-③-E(裁定 #9 单篮子单引擎):篮子级引擎归属,成员继承 —— 卡上每票的
    # 「唯一主引擎及准确版本」(K8 §四)由这三键 + 成员节共同表达,⛔ 成员节不
    # 另存一份引擎字段。老卡(v2 及以前)没有这三键 = 「当时没有引擎归属概念」。
    engine_code: Optional[str] = None
    engine_version: Optional[str] = None
    skeleton_version: Optional[str] = None

    @property
    def degraded(self) -> bool:
        """人话半份缺席 = 降级(**结构化半份照出**,plan 的降级规格)。"""
        return self.llm_stage != LLM_OK

    def to_card_json(self) -> Dict[str, Any]:
        """`basket_cards.card_json` 的内容(蓝图 4.6 全项 + 结构化 spec + disclaimer)。

        键名用 **snake_case** —— plan §五 V2-⑦ 原文写的就是 `card_json.
        verification_spec` / `card_json.members[].tags`,且 ⑧ 哨兵(Python)是结构化
        spec 的直接消费方;转 camelCase 是 ⑭ 契约层的事,不在冻结件里做。
        """
        return {
            "spec_version": CARD_SPEC_VERSION,
            "version": int(self.version),
            "basket_key": self.basket_key,
            "trade_date": self.trade_date,
            "next_trade_date": self.next_trade_date,
            # 1 篮子名称与共同驱动
            "name": self.name,
            "driver": self.driver,
            "driver_kind": self.driver_kind,
            # 1b 引擎归属(V2.2-③-E,裁定 #9:篮子标、成员继承)
            "engine_code": self.engine_code,
            "engine_version": self.engine_version,
            "skeleton_version": self.skeleton_version,
            # 2 驱动证据与信息来源(每条带日期)
            "evidence": [
                {"claim": e.claim, "source": e.source, "date": e.date,
                 "url": getattr(e, "url", "") or ""}
                for e in self.evidence
            ],
            "evidence_status": self.evidence_status,
            # 3 为什么是现在
            "why_now": self.why_now,
            # 4 成员、角色与比较结果(含对拍分歧)+ ⑦-K7 标注
            "members": [m.to_dict() for m in self.members],
            "role_conflicts": [m.ts_code for m in self.members if m.role_conflict],
            # 5 Tier 及分层理由
            "tier": self.tier,
            "rank_in_tier": self.rank_in_tier,
            "rank_mech": self.rank_mech,
            "mech_score": self.mech_score,
            "tier_breakdown": dict(self.tier_breakdown or {}),
            "tier_reason": self.tier_reason,
            "tier_note": self.tier_note,
            # 6 预期上涨路径(V2.3.3-①;⛔ `scripts` / `scripts_unavailable_reason`
            #   两键**直接停发**,不走两步淘汰 —— 客户端 `BasketCard.scripts` 本就是
            #   `decodeIfPresent`,停发安全。老 v3 卡里那两键照常读得回来。)
            "upside_path": self.upside_path,
            "upside_path_unavailable_reason": (None if self.upside_path else
                                               f"本次未生成预期上涨路径({self.llm_stage})"),
            # 8 / 9 验证与失效:结构化(机器) + 人话(LLM)双份
            "verification_spec": self.verification_spec,
            "verification_text": self.verification_text,
            "invalidation_spec": self.invalidation_spec,
            "invalidation_text": self.invalidation_text,
            # 10 主要风险
            "risks": list(self.risks),
            # 11 disclaimer(固定文案单一源,客户端原样透传不改写)
            "disclaimer": BASKET_CARD_DISCLAIMER,
            # 口径指纹 + 动态纪律标签
            "fingerprint": {
                "stop_pct": self.stop_pct,
                "take_profit_retrace": self.take_profit_retrace,
                # V2.3.2-⑤(K8.md §十九):对外退出语义。`stop_pct` **保留不删**
                # (客户端两步淘汰第一步:本版只加键,服务端删键是下一版的事)。
                "loss_warning_pct": self.loss_warning_pct,
                "loss_warning_action": self.loss_warning_action,
                "charter_version": self.charter_version,
                "pack_version": self.pack_version,
                "engine_api_version": self.engine_api_version,
                # ⑦-b:验证 / 失效**条件集**的版本(**与跟形状的 `spec_version` 分开**)。
                # ⑨ 评价引擎按它分层,才谈得上「这套条件集的验证率是多少」;没有它,
                # 日后回看会把两套条件集的成绩混成一锅。**条件或阈值一改就 bump。**
                "verification_ruleset_version": VERIFICATION_RULESET_VERSION,
            },
            "discipline_labels": discipline_labels(self.stop_pct, self.take_profit_retrace),
            # 降级如实披露
            "narrative": self.narrative,
            "llm_stage": self.llm_stage,
            "degraded": self.degraded,
            "notes": list(self.notes),
        }


def _entries_by_code(payload: Optional[Mapping[str, Any]], allowed: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """把 LLM 的 `entries` 数组收进 `ts_code -> 条目`,**成员白名单闸**:出现成员集合
    外的代码 → 整条丢弃 + WARNING(同 ⑤ 白名单闸的精神:成员集被污染意味着这条建议
    不可信)。重复代码取**第一条**(后到丢弃,可复现)。"""
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(payload, Mapping):
        return out
    raw = payload.get("entries")
    if not isinstance(raw, list):
        return out
    allow = set(allowed)
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        code = item.get("ts_code")
        if not isinstance(code, str) or code not in allow:
            logger.warning("[basket_card] 丢弃成员集合外 / 形状不对的 entries 条目:%r", item)
            continue
        out.setdefault(code, dict(item))
    return out


def _clean_text(v: Any) -> Optional[str]:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _clean_upside_path(payload: Optional[Mapping[str, Any]]) -> Optional[str]:
    """预期上涨路径(V2.3.3-①,K8.md §十 第 8 项):**一段话,不分支**。空 / 非字符串
    → `None`(= 本次没生成),读侧据此出 `upside_path_unavailable_reason`。

    ⚠ 这里**只读 `upside_path` 一个键**:老三剧本 `scripts` 是**冻结卡上的历史形状**,
    不是本次 LLM 的合法产物 —— 兼容发生在**读侧**(`_upside_path_present()` 的 OR),
    ⛔ 不在写侧回捞老键(那会让新卡里出现一个本版已经不问的东西)。
    """
    if not isinstance(payload, Mapping):
        return None
    return _clean_text(payload.get("upside_path"))


def build_basket_card(
    basket: Any,
    trade_date: date,
    *,
    tier_decision: Any = None,
    mechs: Optional[Mapping[str, MemberMech]] = None,
    tag_batch: Optional[mt.MemberTagBatch] = None,
    payload: Optional[Mapping[str, Any]] = None,
    narrative: str = "",
    llm_stage: str = LLM_DISABLED,
    stop_pct: Optional[float] = None,
    take_profit_retrace: Optional[float] = None,
    loss_warning_pct: Optional[float] = None,
    loss_warning_action: Optional[str] = None,
    version: int = 1,
    with_tags: bool = True,
    next_trade_date: Optional[date] = None,
    min_members_hit: Optional[int] = None,
    notes: Sequence[str] = (),
) -> BasketCard:
    """**纯装配**:已经拿到的机械数据 + (可选的)LLM 产出 → 一张 `BasketCard`。
    **不发起任何 LLM 调用、不落库、不读库**(除非 `mechs` 没传 —— 那时会为了不让
    调用方漏算而报错,见下)。

    `with_tags=False`:关掉 ⑦-K7 标注(**只**用于守门单测「打标前后 Tier 序与成员
    去留逐位不变」——把开关做成参数,才能把那条断言写成真的两次运行对比,而不是
    嘴上保证)。

    `llm_stage != ok` 时:**结构化半份照出**(spec / 机械锚 / 指纹 / 标注全在),
    人话半份(剧本 / 验证失效人话 / 风险)如实缺席并在 `llm_stage` 里标明原因 ——
    这正是 plan 的降级规格,不是把整张卡丢掉。
    """
    if mechs is None:
        raise ValueError(
            "build_basket_card:`mechs` 必填 —— 机械锚点(收盘/MA20/涨跌停/止损线)是"
            "夹逼与结构化 spec 的地基,本函数刻意不自己去读库,免得同一批 I/O 被每个"
            "篮子各做一遍(调用方用 `build_member_mech()` 一次算好、全篮复用)。"
        )

    members_in = list(getattr(basket, "members", ()) or ())
    codes = [m.ts_code for m in members_in]
    mech_list = [mechs.get(c) or MemberMech(ts_code=c) for c in codes]

    nd = next_trade_date
    if nd is None:
        try:
            nd = next_trading_day(trade_date)
        except Exception:  # noqa: BLE001 —— 日期锚算不出不该掀翻卡
            nd = None

    basket_key = getattr(basket, "basket_key", "")
    verify_spec = build_verification_spec(
        basket_key, trade_date, mech_list, next_trade_date=nd, min_members_hit=min_members_hit)
    invalidate_spec = build_invalidation_spec(
        basket_key, trade_date, mech_list, next_trade_date=nd, stop_pct=stop_pct,
        min_members_hit=min_members_hit)

    entries = _entries_by_code(payload, codes)
    out_members: List[MemberCardEntry] = []
    for m, mech in zip(members_in, mech_list):
        item = entries.get(m.ts_code, {})
        low, high, entry_clamp = clamp_entry_zone(
            {"low": item.get("low"), "high": item.get("high")} if item else None,
            mech.limit_up, mech.limit_down,
        )
        chase, chase_clamp = clamp_max_chase(
            item.get("max_chase") if item else None, mech.limit_up, mech.limit_down,
            zone_high=high,
        )
        ex_low, ex_high, exit_clamp = clamp_exit_reference(
            {"low": item.get("exit_low"), "high": item.get("exit_high")} if item else None,
            mech.close,      # ⑪-D-B 闸①:D0 收盘是机械锚,与 limit_up/limit_down 同源同批
        )
        tag_res = tag_batch.get(m.ts_code) if (with_tags and tag_batch is not None) else None
        out_members.append(MemberCardEntry(
            ts_code=m.ts_code,
            name=getattr(m, "name", "") or mech.name or m.ts_code,
            role_llm=getattr(m, "role_llm", ""),
            role_mech=getattr(m, "role_mech", None),
            role_conflict=int(getattr(m, "role_conflict", 0) or 0),
            reason=getattr(m, "reason", "") or "",
            is_primary=int(getattr(m, "is_primary", 1) or 0),
            industry=getattr(m, "industry", None),
            industry_lift=getattr(m, "industry_lift", None),
            lift_reason=getattr(m, "lift_reason", None),
            primary_reason=getattr(m, "primary_reason", None),
            rs_rank=getattr(m, "rs_rank", None),
            k4_tag=getattr(m, "k4_tag", None),
            position_verdict=getattr(m, "position_verdict", "") or "",
            position_reason=getattr(m, "position_reason", "") or "",
            position_metrics=getattr(m, "position_metrics", None),
            core_verdict=getattr(m, "core_verdict", "") or "",
            core_reason=getattr(m, "core_reason", "") or "",
            core_metrics=getattr(m, "core_metrics", None),
            mech=mech,
            entry_low=low, entry_high=high, entry_clamp=entry_clamp,
            entry_why=_clean_text(item.get("why")) if item else None,
            max_chase=chase, max_chase_clamp=chase_clamp,
            exit_low=ex_low, exit_high=ex_high, exit_clamp=exit_clamp,
            tags=tuple(tag_res.tags) if tag_res is not None else (),
            tags_absent=tuple(tag_res.absent) if tag_res is not None else (),
        ))

    risks_raw = payload.get("risks") if isinstance(payload, Mapping) else None
    risks = tuple(t for t in ((_clean_text(x) for x in risks_raw) if isinstance(risks_raw, list) else ()) if t)

    return BasketCard(
        basket_key=basket_key,
        trade_date=getattr(basket, "trade_date", "") or trade_date.strftime("%Y%m%d"),
        version=int(version),
        name=getattr(basket, "name", ""),
        driver=getattr(basket, "driver", ""),
        driver_kind=getattr(basket, "driver_kind", ""),
        why_now=getattr(basket, "why_now", "") or "",
        evidence=tuple(getattr(basket, "evidence", ()) or ()),
        evidence_status=getattr(basket, "evidence_status", ""),
        members=tuple(out_members),
        verification_spec=verify_spec,
        invalidation_spec=invalidate_spec,
        tier=getattr(tier_decision, "tier", None) if tier_decision is not None else None,
        rank_in_tier=getattr(tier_decision, "rank_in_tier", None) if tier_decision is not None else None,
        rank_mech=getattr(tier_decision, "rank_mech", None) if tier_decision is not None else None,
        mech_score=getattr(tier_decision, "mech_score", None) if tier_decision is not None else None,
        tier_breakdown=dict(getattr(tier_decision, "breakdown", {}) or {}) if tier_decision is not None else {},
        tier_reason=getattr(tier_decision, "llm_reason", None) if tier_decision is not None else None,
        tier_note=_clean_text(payload.get("tier_note")) if isinstance(payload, Mapping) else None,
        narrative=narrative or "",
        upside_path=_clean_upside_path(payload),
        verification_text=_clean_text(payload.get("verification")) if isinstance(payload, Mapping) else None,
        invalidation_text=_clean_text(payload.get("invalidation")) if isinstance(payload, Mapping) else None,
        risks=risks,
        stop_pct=stop_pct,
        take_profit_retrace=take_profit_retrace,
        loss_warning_pct=loss_warning_pct,
        loss_warning_action=loss_warning_action,
        charter_version=getattr(basket, "charter_version", None),
        pack_version=getattr(basket, "pack_version", None),
        engine_api_version=getattr(basket, "engine_api_version", None),
        llm_stage=llm_stage,
        next_trade_date=nd.strftime("%Y%m%d") if nd is not None else None,
        notes=tuple(notes),
        engine_code=getattr(basket, "engine_code", None),
        engine_version=getattr(basket, "engine_version", None),
        skeleton_version=getattr(basket, "skeleton_version", None),
    )


def build_cards(
    baskets: Sequence[Any],
    trade_date: date,
    *,
    tier_by_basket_key: Optional[Mapping[str, Any]] = None,
    provider: Optional[LLMProvider] = None,
    use_llm: bool = False,
    ledger: Optional[BudgetLedger] = None,
    transport: Optional[Any] = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    close_of: Optional[Mapping[str, Optional[float]]] = None,
    with_tags: bool = True,
    version: int = 1,
) -> List[BasketCard]:
    """⑦ 的编排入口:一批篮子 → 一批卡(**不落库**,落库走
    `basket_store.save_basket_card()` 的【事务 2】)。

    `use_llm`:默认 **False**(纯机械,零 LLM 调用)—— 与 ⑥ `score_and_tier` 同一
    姿势:传了 `provider` 但没开 `use_llm` 也不会调用,免得"注入个桩"就意外上路。
    ⑭ 的管线会显式打开并传 `provider`(`get_provider(TASK_SCRIPT)`)与 `ledger`。

    **每篮各自包保险丝**:一张卡炸了不牵连其余(核心管线对可选情报输入必须包保险丝,
    项目 CLAUDE.md 铁律);整批共用一次机械 I/O(价量面板 / 龙头结构 / 元数据 / 章程)。
    """
    if not baskets:
        return []
    ledger = ledger or BudgetLedger()
    stop_pct, tpr = resolve_charter_pcts(db_path)
    lw_pct, lw_action = resolve_loss_warning(db_path)      # V2.3.2-⑤ 对外退出语义指纹
    labels = discipline_labels(stop_pct, tpr)

    codes: List[str] = []
    for b in baskets:
        # 连"这个篮子的成员长什么样"都读不动的篮子,在这里就要被隔离掉 —— 否则一颗
        # 坏数据会在**攒 codes 这一步**掀翻整批,保险丝就成了摆设(下面每篮的
        # try/except 根本轮不到)。
        try:
            for m in getattr(b, "members", ()) or ():
                if m.ts_code not in codes:
                    codes.append(m.ts_code)
        except Exception:  # noqa: BLE001
            logger.error("[basket_card] 篮子 %s 的成员清单读不动,本篮无卡(篮子不回删)",
                         getattr(b, "basket_key", "?"), exc_info=True)

    panel_rows = mt.load_tag_panel_rows(codes, trade_date, parquet_dir=parquet_dir)
    tag_batch = mt.tags_for_members(
        codes, trade_date, db_path=db_path, parquet_dir=parquet_dir, panel_rows=panel_rows)

    closes: Dict[str, Optional[float]] = {}
    ma20s: Dict[str, Optional[float]] = {}
    for c in codes:
        row = panel_rows.get(c) or {}
        closes[c] = (close_of or {}).get(c, row.get("close"))
        ma20s[c] = row.get("ma20")
    mechs = build_member_mech(closes, trade_date, stop_pct=stop_pct, ma20_of=ma20s, db_path=db_path)

    try:
        nd: Optional[date] = next_trading_day(trade_date)
    except Exception:  # noqa: BLE001
        nd = None

    out: List[BasketCard] = []
    for b in baskets:
        key = getattr(b, "basket_key", "")
        decision = (tier_by_basket_key or {}).get(key)
        notes: List[str] = []
        narrative, payload, stage = "", None, LLM_DISABLED
        try:
            member_mechs = {m.ts_code: mechs.get(m.ts_code) or MemberMech(ts_code=m.ts_code)
                            for m in (getattr(b, "members", ()) or ())}
            if use_llm:
                verify_preview = build_verification_spec(
                    key, trade_date, list(member_mechs.values()), next_trade_date=nd)
                invalidate_preview = build_invalidation_spec(
                    key, trade_date, list(member_mechs.values()), next_trade_date=nd,
                    stop_pct=stop_pct)
                context = build_card_context(
                    b, trade_date, member_mechs, verify_preview, invalidate_preview,
                    tier_decision=decision, tag_batch=tag_batch if with_tags else None,
                    discipline=labels,
                )
                narrative, payload, stage = run_card_llm(
                    context, provider=provider, ledger=ledger, transport=transport)
            out.append(build_basket_card(
                b, trade_date, tier_decision=decision, mechs=member_mechs,
                tag_batch=tag_batch, payload=payload, narrative=narrative, llm_stage=stage,
                stop_pct=stop_pct, take_profit_retrace=tpr,
                loss_warning_pct=lw_pct, loss_warning_action=lw_action, version=version,
                with_tags=with_tags, next_trade_date=nd, notes=notes,
            ))
        except Exception:  # noqa: BLE001 —— 一张卡炸了不牵连其余(「有篮子无卡」合法)
            logger.error("[basket_card] 篮子 %s 的卡生成整体失败,本篮无卡(篮子不回删)",
                         key, exc_info=True)
    return out


# ══════════════════════════════════════════════════════════════════════════
# V2.2-③-E:交易资格四件套(K8 §十一)的**唯一判定实现**
# ══════════════════════════════════════════════════════════════════════════
# 四件套 = 上涨判断(卡 #6 三剧本)/ 入场区间(成员 entry_zone)/ 目标离场区间
# (成员 exit_reference)/ 判断失效位置(卡 invalidation_spec)。消费方两处:
#   · ⑥→⑦ 编排(`tier.enforce_plan_completeness`):四件齐是 **T1 的必要条件**,
#     缺任一 → 降 T2(⛔ 不是拦截,系统只审计不代下单,§3.8);
#   · ⑩ 开仓继承(`positions_entry.build_inherited_plan`):缺件 → 客户端与周复盘
#     各出一条**警示**(照旧不拦截)。
# 两处都从这里拿判据,⛔ 不各写一份。

# 四件的机器码(稳定标识,⑨ 归因可 grep;顺序即 K8 §十一 原文顺序)。
# 🔴 **`upside_script` 这个字符串一字不改**(V2.3.3-①,〇-2):它已写进历史
# `position_plans.plan_json` 与 `trade_clock.entry_plan_json`,改了会让旧行**假装缺件**
# (④ 按码归因当场断线)。V2.3.3 只换了它背后问的问题与下面那个中文标签。
TRADE_PLAN_PIECES: Tuple[str, ...] = (
    "upside_script", "entry_zone", "exit_reference", "invalidation",
)
TRADE_PLAN_PIECE_LABELS: Dict[str, str] = {
    "upside_script": "上涨判断(预期上涨路径)",
    "entry_zone": "入场区间",
    "exit_reference": "目标离场区间",
    "invalidation": "判断失效位置",
}


def _upside_path_present(card: Optional[Mapping[str, Any]]) -> bool:
    """卡上有没有「上涨判断」这一件(四件套第 1 件的判据)。

    🔴 **判据是 OR,不是只读新键**(V2.3.3-① 硬要求):`basket_cards` 是
    `INSERT OR IGNORE` 的冻结件,**新键永不回填** —— 今天开仓读的可能是昨天冻的那张
    **v3 老卡**(它只有 `scripts` 三格)。只认 `upside_path` 会让昨天那批篮子今天
    全部"缺上涨判断",凭空多一条假警示。**新键优先、老键兜底。**
    """
    c = card or {}
    if str(c.get("upside_path") or "").strip():
        return True
    scripts = c.get("scripts")          # v3 及更早的老卡形状
    return isinstance(scripts, Mapping) and any(
        str(v or "").strip() for v in scripts.values()
    )


def _invalidation_present(card: Optional[Mapping[str, Any]]) -> bool:
    spec = (card or {}).get("invalidation_spec")
    return isinstance(spec, Mapping) and bool(spec)


def _zone_present(entry: Optional[Mapping[str, Any]], key: str) -> bool:
    zone = (entry or {}).get(key)
    if not isinstance(zone, Mapping):
        return False
    low = zone.get("low")
    return isinstance(low, (int, float)) and not isinstance(low, bool)


def member_trade_plan_missing(
    card: Optional[Mapping[str, Any]], member_entry: Optional[Mapping[str, Any]],
) -> List[str]:
    """某一名成员视角的四件套缺件清单(⑩ 开仓继承的警示判据)。卡整体缺 →
    四件全缺(`card=None` 时按「一件都没有」如实报,⛔ 不猜)。"""
    missing: List[str] = []
    if not _upside_path_present(card):
        missing.append("upside_script")
    if not _zone_present(member_entry, "entry_zone"):
        missing.append("entry_zone")
    if not _zone_present(member_entry, "exit_reference"):
        missing.append("exit_reference")
    if not _invalidation_present(card):
        missing.append("invalidation")
    return missing


def trade_plan_missing_pieces(card: Optional[Mapping[str, Any]]) -> List[str]:
    """篮子视角的四件套缺件清单(T1 必要条件的判据;空列表 = 四件齐)。
    成员级两件(入场/离场区间)要求**每一名成员**都有 —— 缺的按
    `entry_zone:<ts_code>` 逐票列出(哪只缺一目了然,③-E「缺任一 → 不进 T1」)。"""
    missing: List[str] = []
    if not _upside_path_present(card):
        missing.append("upside_script")
    members = (card or {}).get("members") or []
    for m in members:
        if not isinstance(m, Mapping):
            continue
        code = str(m.get("ts_code") or "?")
        if not _zone_present(m, "entry_zone"):
            missing.append(f"entry_zone:{code}")
        if not _zone_present(m, "exit_reference"):
            missing.append(f"exit_reference:{code}")
    if not members:
        missing.append("entry_zone")
        missing.append("exit_reference")
    if not _invalidation_present(card):
        missing.append("invalidation")
    return missing


def trade_plan_missing_label(missing: Sequence[str]) -> str:
    """缺件清单 → 人读文案(警示的单一文案源,客户端/周复盘/渲染共用)。"""
    if not missing:
        return ""
    names: List[str] = []
    for token in missing:
        base = token.split(":", 1)[0]
        label = TRADE_PLAN_PIECE_LABELS.get(base, base)
        if label not in names:
            names.append(label)
    return "次日交易预案不完整,缺:" + "、".join(names)


__all__ = [
    "CARD_SPEC_VERSION",
    "VERIFY_SPEC_VERSION",
    "INVALIDATE_SPEC_VERSION",
    "BASKET_CARD_DISCLAIMER",
    "CLAMP_OK",
    "CLAMP_ABSENT",
    "CLAMP_REJECTED_OUT_OF_LIMIT",
    "CLAMP_REJECTED_MALFORMED",
    "CLAMP_REJECTED_NO_LIMIT",
    "CLAMP_REJECTED_NOT_ABOVE_CLOSE",
    "CLAMP_REJECTED_NO_CLOSE",
    "EXIT_CLAMP_OK",
    "EXIT_CLAMP_ABSENT",
    "EXIT_CLAMP_REJECTED_MALFORMED",
    "EXIT_CLAMP_REJECTED_NOT_ABOVE_CLOSE",
    "EXIT_CLAMP_REJECTED_NO_CLOSE",
    "LLM_OK",
    "LLM_NO_PROVIDER",
    "LLM_CALL_FAILED",
    "LLM_BUDGET_EXHAUSTED",
    "LLM_PARSE_FAILED",
    "LLM_DISABLED",
    "COND_CLOSE_AT_OR_ABOVE_REF",
    "COND_HOLDS_MA20",
    "COND_CLOSE_BELOW_STOP_LINE",
    "COND_BELOW_REF_AND_MA20",
    "COND_LIMIT_DOWN_TOUCH",
    "VERIFICATION_RULESET_VERSION",
    "CARD_SYSTEM_PROMPT",
    "MemberMech",
    "MemberCardEntry",
    "BasketCard",
    "resolve_charter_pcts",
    "resolve_loss_warning",
    "discipline_labels",
    "build_member_mech",
    "build_verification_spec",
    "build_invalidation_spec",
    "spec_threshold_text",
    "clamp_entry_zone",
    "clamp_max_chase",
    "clamp_exit_reference",
    "clamp_reason_text",
    "build_card_context",
    "run_card_llm",
    "build_basket_card",
    "build_cards",
    "TRADE_PLAN_PIECES",
    "TRADE_PLAN_PIECE_LABELS",
    "member_trade_plan_missing",
    "trade_plan_missing_pieces",
    "trade_plan_missing_label",
]
