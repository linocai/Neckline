"""自然语言临时提醒的**解析层**(plan §五 V2-⑪-C:「**LLM 只做解析**」)。

    用户说的话  ──[本模块]──►  一份候选结构化规则  ──[custom_alerts 白名单]──►
    确认卡  ──[用户点确认]──►  落库  ──[sentinel/custom.py 逐拍执行]──►  通知

**本模块的边界(三条,一条都不能越)**:

    1. **只解析,不落库、不执行**。它连 `db_path` 都只用来查 provider 路由,不写
       任何业务表。
    2. **产出必须过白名单**。模型给的 JSON 一律先送 `custom_alerts.normalize_rule()`;
       不合规就是解析失败(`ok=False` + 可读原因),**⛔ 不做"贴心修正"** —— 把
       `-5` 猜成 `-0.05` 就是给用户建了一条他没要的提醒。
    3. **LLM 不可用 → 降级为手填结构化表单,不静默失败**(⑪-C 原文)。降级结果里
       带一份 `manual_form`(有哪些字段、各自取值域),客户端据此直接渲染表单,
       **而不是弹一句"解析失败"就没了**。

**日期锚必挂**(§铁律 + CLAUDE.md 2026-07-30 事故):`llm/prompt_context.py` 是唯一
实现,本模块 import 它 —— 「今天 13:30 以后」这种相对时间,模型没有"今天"的概念就
会算错生效窗,这是本链路最容易踩的一脚。

**解析结果里的 JSON 用 `llm/json_block.py` 剥**(项目通用件,⑤/⑦ 都在用):提示词
要求模型「先写一句人话,再给一段 ```json 围栏」,`split_narrative_and_reference_json`
负责把两段分开。⚠ 顺带守住 CLAUDE.md 那条坑:**机器可读部分排在最后**,不在它后面
再挂自由文本。

**联网搜索一律关**(`enable_search=False`):把一句「跌到 15 通知我」变成结构化规则
不需要任何外部信息,开搜索只会拖慢一个交互式接口(用户在等确认卡)并且平白烧预算。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline import custom_alerts as ca
from neckline.llm.base import ChatMessage, LLMProvider
from neckline.llm.json_block import split_narrative_and_reference_json
from neckline.llm.prompt_context import TIMELINESS_RULES, date_anchor_line
from neckline.llm.router import TASK_NL_ALERT

logger = logging.getLogger(__name__)

# 解析出来的意图(蓝图 5.6:「用户可随时用自然语言**查询、修改、取消**」)。
ACTION_CREATE = "create"
ACTION_QUERY = "query"
ACTION_CANCEL = "cancel"
ACTION_MODIFY = "modify"
ALL_ACTIONS = (ACTION_CREATE, ACTION_QUERY, ACTION_CANCEL, ACTION_MODIFY)

# 降级时给客户端的手填表单描述(⑪-C:「入口降级为手填结构化表单,**不静默失败**」)。
# **由白名单常量生成,不是另抄一份** —— 加一个 metric 时这里自动跟着变。
def manual_form_schema() -> Dict[str, Any]:
    return {
        "fields": [
            {"name": "tsCode", "type": "string", "required": False,
             "note": "标的代码(留空 = 大盘级提醒,此时只能用指数涨跌幅条件)"},
            {"name": "conditions", "type": "array", "required": True,
             "maxItems": ca.MAX_CONDITIONS,
             "item": {
                 "metric": {"enum": list(ca.ALL_METRICS), "labels": dict(ca.METRIC_LABEL)},
                 "op": {"enum": list(ca.ALL_OPS), "labels": dict(ca.OP_LABEL)},
                 "value": {"type": "number", "note": "百分比类指标用小数(-0.05 = −5%)"},
                 "ref": {"type": "string", "note": "仅 index_chg_pct 需要,填指数代码"},
             }},
            {"name": "logic", "type": "enum", "enum": list(ca.ALL_LOGICS), "default": ca.LOGIC_ALL},
            {"name": "activeFrom", "type": "string", "required": False, "note": "HH:MM"},
            {"name": "activeTo", "type": "string", "required": False, "note": "HH:MM"},
            {"name": "persist", "type": "bool", "default": False,
             "note": "false = 今日收盘自动失效"},
            {"name": "maxFires", "type": "int", "default": ca.DEFAULT_MAX_FIRES,
             "note": "0 = 不限次;默认 1 = 命中一次后不再轰炸"},
            {"name": "cooldownSeconds", "type": "int", "default": ca.DEFAULT_COOLDOWN_SECONDS},
        ],
        "note": "LLM 解析当前不可用,请直接填这张表;字段语义与 LLM 解析出来的完全一致。",
    }


@dataclass
class NLAlertParse:
    """一次解析的结果。`ok=False` 时 `reason` 一定可读(会原样给用户看)。"""

    ok: bool
    action: str = ACTION_CREATE
    ts_code: Optional[str] = None
    rule: Optional[Dict[str, Any]] = None      # 已过白名单的规范化规则
    active_from: Optional[str] = None
    active_to: Optional[str] = None
    expires_at: Optional[str] = None
    persist: bool = False
    cooldown_seconds: int = ca.DEFAULT_COOLDOWN_SECONDS
    max_fires: int = ca.DEFAULT_MAX_FIRES
    target_alert_id: Optional[int] = None      # cancel / modify 时模型指认的目标
    narrative: str = ""                        # 模型那句人话(只展示,不进判据)
    reason: str = "ok"
    degraded: bool = False                     # True = LLM 不可用,已降级
    manual_form: Optional[Dict[str, Any]] = None
    provider: str = ""
    model: str = ""
    raw_json: Dict[str, Any] = field(default_factory=dict)


SYSTEM_PROMPT_HEAD = """你是「颈线」系统的临时提醒解析器。用户会用一句口语描述一个盯盘提醒,
你的**唯一任务**是把它翻译成系统能执行的结构化规则。你不做分析、不给建议、不预测涨跌,
也不评价这个提醒好不好。

系统硬约束(必须在你的输出里体现,不可违反):
1. 提醒**只通知,不自动交易**。系统永远不会因为提醒命中而下单、撤单或改止损。
2. 规则里只能出现下面白名单里的指标与比较符,**不许发明新指标**。
3. 百分比一律用小数(涨 5% = 0.05,跌 5% = -0.05)。价格用元。
4. 你不确定的字段就**不要填**(留 null),不要猜。用户没说的生效时间、到期时间、
   通知次数,全部留 null,由系统用默认值。
"""


def _whitelist_block() -> str:
    metrics = "\n".join(
        f"  · {m} —— {ca.METRIC_LABEL[m]}" for m in ca.ALL_METRICS
    )
    return f"""可用指标(metric,只能取这些):
{metrics}

口径说明(照此翻译,别自创):
  · chg_pct / vs_cost / index_chg_pct 是小数比例;
  · from_day_high 是「相对日内最高价」的偏离,**恒为负或零**:-0.03 = 从日内高点回落 3%;
  · volume_ratio 是量比(折算全天量 / 前 5 日均量),1.0 = 与均量持平;
  · basket_weak_ratio 是「该股来源篮子里转弱成员的占比」,0~1;
  · index_chg_pct 必须带 ref(指数代码,如 000001.SH = 上证综指、399001.SZ = 深证成指)。

可用比较符(op):{list(ca.ALL_OPS)}
多个条件的组合方式(logic):"all" = 全部满足,"any" = 任一满足。最多 {ca.MAX_CONDITIONS} 个条件。
可用动作(action):{list(ALL_ACTIONS)} —— 用户是想「新建提醒」「查已有提醒」「取消」还是「改」。
"""


OUTPUT_SPEC = """输出格式(严格遵守):

先用**一句话**复述你的理解(给用户看,不超过 40 字),然后另起一行给出一段 JSON 围栏:

```json
{
  "action": "create",
  "ts_code": "600519.SH",
  "logic": "all",
  "conditions": [{"metric": "price", "op": "<=", "value": 15.0}],
  "active_from": "13:30",
  "active_to": null,
  "persist": false,
  "max_fires": null,
  "cooldown_seconds": null,
  "target_alert_id": null
}
```

**JSON 必须是你输出的最后一段**,后面不要再写任何文字。"""


def build_messages(nl_text: str, *, ts_code_hint: Optional[str] = None,
                   name_hint: Optional[str] = None,
                   existing: Optional[List[Dict[str, Any]]] = None) -> List[ChatMessage]:
    """拼提示词。**日期锚在第一行**(`prompt_context.date_anchor_line`,唯一实现)。"""
    sys_parts = [
        SYSTEM_PROMPT_HEAD,
        _whitelist_block(),
        TIMELINESS_RULES,
        OUTPUT_SPEC,
    ]
    user_parts = [date_anchor_line()]
    if ts_code_hint:
        who = f"{name_hint}({ts_code_hint})" if name_hint else ts_code_hint
        user_parts.append(f"用户当前正在看的标的:{who}(用户没另外点名时就用它)。")
    if existing:
        listing = "\n".join(
            f"  · id={e.get('id')} 标的={e.get('ts_code') or '大盘'} 条件={e.get('condition')}"
            for e in existing
        )
        user_parts.append("用户已有的提醒(要取消/修改时从这里指认 target_alert_id):\n" + listing)
    user_parts.append(f"用户原话:{nl_text}")
    return [
        ChatMessage(role="system", content="\n\n".join(sys_parts)),
        ChatMessage(role="user", content="\n".join(user_parts)),
    ]


def _degraded(reason: str) -> NLAlertParse:
    return NLAlertParse(
        ok=False, reason=reason, degraded=True, manual_form=manual_form_schema(),
    )


def _as_opt_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _as_opt_int(v: Any, default: int) -> int:
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def parse_nl_alert(
    nl_text: str,
    *,
    ts_code_hint: Optional[str] = None,
    name_hint: Optional[str] = None,
    existing: Optional[List[Dict[str, Any]]] = None,
    provider: Optional[LLMProvider] = None,
    db_path: Optional[Path] = None,
    transport: Optional[Any] = None,
) -> NLAlertParse:
    """自然语言 → 候选规则。**永不抛异常**(交互式接口,任何失败都要变成可读结果)。

    `provider=None` 时按 `TASK_NL_ALERT` 路由现取(`llm/factory.get_provider`);取不到
    (没配 provider / 没 key / 被禁用)→ **降级**为手填表单,`degraded=True`。"""
    text = (nl_text or "").strip()
    if not text:
        return NLAlertParse(ok=False, reason="没有收到提醒内容")

    if provider is None:
        from neckline.llm.factory import get_provider
        provider = get_provider(TASK_NL_ALERT, db_path=db_path)
    if provider is None:
        return _degraded("LLM 未配置或不可用(无可用 Provider / 未设 key),已切换为手填表单")

    messages = build_messages(text, ts_code_hint=ts_code_hint, name_hint=name_hint, existing=existing)
    try:
        res = provider.chat(messages, enable_search=False, transport=transport)
    except Exception as e:  # noqa: BLE001 —— provider 内已兜底,这里双保险
        logger.warning("[nl_alert] provider 调用异常:%s", e, exc_info=True)
        return _degraded(f"LLM 调用异常({e}),已切换为手填表单")
    if not res.ok:
        return _degraded(f"LLM 调用失败({res.reason}),已切换为手填表单")

    narrative, payload = split_narrative_and_reference_json(res.content or "")
    if not isinstance(payload, dict):
        # 模型答了,但没给出机器可读部分 —— 这是**解析失败**,不是 LLM 不可用:
        # 不降级成手填(那会掩盖"提示词/模型不配合"这个真问题),如实报,让用户换句话说。
        return NLAlertParse(
            ok=False, reason="LLM 没有给出结构化规则(输出里找不到 JSON 段),请换一种说法再试",
            narrative=(narrative or "").strip(), provider=res.provider, model=res.model,
        )

    action = str(payload.get("action") or ACTION_CREATE).strip().lower()
    if action not in ALL_ACTIONS:
        action = ACTION_CREATE
    code = _as_opt_str(payload.get("ts_code")) or _as_opt_str(ts_code_hint)
    if code:
        code = code.upper()

    base = NLAlertParse(
        ok=True, action=action, ts_code=code,
        active_from=_as_opt_str(payload.get("active_from")),
        active_to=_as_opt_str(payload.get("active_to")),
        expires_at=_as_opt_str(payload.get("expires_at")),
        persist=bool(payload.get("persist")),
        cooldown_seconds=max(0, _as_opt_int(payload.get("cooldown_seconds"), ca.DEFAULT_COOLDOWN_SECONDS)),
        max_fires=max(0, _as_opt_int(payload.get("max_fires"), ca.DEFAULT_MAX_FIRES)),
        target_alert_id=(int(payload["target_alert_id"])
                         if str(payload.get("target_alert_id") or "").strip().isdigit() else None),
        narrative=(narrative or "").strip(), provider=res.provider, model=res.model,
        raw_json=payload,
    )

    if action in (ACTION_QUERY, ACTION_CANCEL):
        # 查询 / 取消不需要规则本体(取消靠 target_alert_id 指认)。
        return base

    try:
        base.rule = ca.normalize_rule(
            {"logic": payload.get("logic") or ca.LOGIC_ALL,
             "conditions": payload.get("conditions") or []},
            ts_code=code,
        )
    except ca.RuleValidationError as e:
        # 白名单拒收 —— 如实回原因(它写给用户看的),**不修正、不落库**。
        return NLAlertParse(
            ok=False, reason=f"解析出的规则不合法:{e}",
            narrative=base.narrative, provider=res.provider, model=res.model, raw_json=payload,
        )
    return base


def confirmation_card_for(parse: NLAlertParse, *, name: Optional[str] = None) -> Optional[ca.ConfirmationCard]:
    """把一份**成功的 create/modify 解析**变成七项确认卡(⑪-C)。解析没带规则 →
    `None`(查询 / 取消这两种意图没有卡可确认)。"""
    if not parse.ok or not parse.rule:
        return None
    return ca.build_confirmation_card(
        rule=parse.rule, ts_code=parse.ts_code, name=name,
        active_from=parse.active_from, active_to=parse.active_to,
        expires_at=parse.expires_at, persist=parse.persist,
        cooldown_seconds=parse.cooldown_seconds, max_fires=parse.max_fires,
    )


__all__ = [
    "ACTION_CREATE", "ACTION_QUERY", "ACTION_CANCEL", "ACTION_MODIFY", "ALL_ACTIONS",
    "NLAlertParse", "manual_form_schema", "build_messages", "parse_nl_alert",
    "confirmation_card_for", "SYSTEM_PROMPT_HEAD", "OUTPUT_SPEC",
]
