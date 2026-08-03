"""APNs 推送编排(plan §五 **V2-⑪-B**「通知三级」重构;前身 = v1.1/v1.2-A2/v1.3-②
的「白名单六类」,🔴 高危区)。

**V2 结构 = 三级 category × N 个 kind**(D5 拍板):

    · **三个 APNs category** —— `NKIMMEDIATE`(立即)/ `NKIMPORTANT`(重要不紧急)/
      `NKDIGEST`(盘后汇总)。category 决定**怎么响**(锁屏呈现 / 打扰级别)。
    · **每条事件带 `kind`** —— 决定**响不响**:`app_settings.push_kinds` 里按 kind
      配的开关。**⛔ 不按 category 配开关**:那会连坐(关掉「重要不紧急」= 同时
      关掉板块、大盘、时间退出三件完全不同的事;V1 拆 `HOLDINGALERT` 就是被这个
      坑逼出来的)。
    · **kind → level → category 的唯一源 = `neckline/notify_kinds.py`**,本模块不
      自己判归属、不散抄字面量。

**唯一扇出入口 = `push_event()`**。下面那些 `push_*` 具名函数**只负责措辞**(把
「发生了什么」拼成一句人话),扇出、查开关、查 APNs 配置、遍历设备全部交给
`push_event` —— 白名单的保证从「本模块只暴露六个函数」升级成「**本模块只有一条
扇出路径,而它只接受 `ALL_KINDS` 里的 kind**」(未登记的 kind 直接抛 `ValueError`,
不给第 12 类事件留一条静默的推送路径)。守门单测按 `__all__` + kind 集合结构性锁死。

**文案纪律(⑪-B 原文)**:只回答三件事 —— **发生了什么、触碰了哪条计划、要不要
打开 APP**;详细解释点开再给。⛔ 不在推送正文里塞分析、不给收益预测、不写「建议
买入」类表述(§2.8-B 语义红线)。

**系统永不代交易动作**(§3.8):本模块只发文字通知,绝不下单 / 撤单 / 改止损。

每类推送:先查该 kind 开关(关 → 直接跳过)→ 查 APNs 配置 → 遍历 `devices` 表所有
token → 逐个 `apns.send_push`(transport 可注入,单测免真连 Apple)。任何设备失败
只记日志,不拖累其它设备 / 主流程(推送是尽力而为,失败绝不能反过来打断报告落库
或哨兵主循环)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline import notify_kinds
from neckline.api.stores import list_device_tokens
from neckline.notify_kinds import (
    KIND_BASKET_PEERS_WEAK,
    KIND_CIRCUIT,
    KIND_CUSTOM_ALERT,
    KIND_D5EXIT,
    KIND_HOLDING_ALERT,
    KIND_HOLDING_DECOUPLED,
    KIND_MARKET_SHOCK,
    KIND_PRECALL,
    KIND_REPORT_READY,
    KIND_RETREAT,
    KIND_SECTOR_BID_FADE,
    KIND_SECTOR_DIVE,
    KIND_STOP_APPROACH,
    KIND_TAKE_PROFIT,
)
from neckline.push import apns
from neckline.settings_store import push_kind_enabled

logger = logging.getLogger(__name__)

# 「要不要打开 APP」那一句的固定收尾(三件事里的第三件;各处措辞统一,便于用户
# 一眼分辨「这条要动手」还是「这条看看就行」)。
_OPEN_APP_NOW = "点开 APP 核对。"
_OPEN_APP_LATER = "有空点开 APP 看详情。"


@dataclass
class NotifyOutcome:
    sent: int = 0
    failed: int = 0
    skipped_reason: str = ""     # 非空 = 未推送的原因(开关关 / 无设备 / 无 APNs 配置)
    kind: str = ""               # 本次事件的 kind(便于调用方落账 / 单测断言)
    level: str = ""              # 三级之一(kind 决定,不由调用方指定)


def _fanout(
    title: str, body: str, *, category: str, custom: Optional[dict],
    db_path: Optional[Path], transport: Optional[Any],
) -> NotifyOutcome:
    tokens = list_device_tokens(db_path=db_path)
    if not tokens:
        return NotifyOutcome(skipped_reason="no_devices")
    out = NotifyOutcome()
    for tok in tokens:
        try:
            res = apns.send_push(tok, title, body, category=category, custom=custom, transport=transport)
        except Exception as e:  # noqa: BLE001  send_push 内已兜底,这里双保险
            logger.warning("APNs 推送异常(token 尾4=%s):%s", tok[-4:] if tok else "", e)
            out.failed += 1
            continue
        if res.ok:
            out.sent += 1
        else:
            out.failed += 1
            logger.warning("APNs 推送失败(status=%s reason=%s)", res.status, res.reason)
    return out


def push_event(
    kind: str, title: str, body: str, *,
    custom_extra: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """**本模块唯一的扇出路径**(⑪-B)。`kind` 决定「响不响」(按 kind 开关)与
    「怎么响」(kind → level → category);调用方**不能**自己指定 category —— 那等于
    绕开三级归属表自定义打扰级别。

    未登记的 kind → `ValueError`(`notify_kinds.level_of` 抛):白名单不开后门,拼错
    的串宁可炸在调用点,也不要静默变成一条真推送。

    `custom` 载荷恒带 `kind` / `level` 两键(客户端按 `kind` 路由到具体页面 —— 三个
    category 已不足以区分去哪儿,这是 V2 与 V1「category 即路由」的关键差异)。"""
    level = notify_kinds.level_of(kind)           # 未登记 → 抛,见 docstring
    category = notify_kinds.CATEGORY_OF_LEVEL[level]
    if not push_kind_enabled(kind, db_path=db_path):
        return NotifyOutcome(skipped_reason=f"kind_off:{kind}", kind=kind, level=level)
    if not apns.settings.has_apns_config:
        return NotifyOutcome(skipped_reason="no_apns_config", kind=kind, level=level)
    custom: Dict[str, Any] = {"kind": kind, "level": level}
    if custom_extra:
        custom.update(custom_extra)
    out = _fanout(title, body, category=category, custom=custom,
                  db_path=db_path, transport=transport)
    out.kind = kind
    out.level = level
    return out


# ══════════════════════════════════════════════════════════════════════════
# 措辞层(V1 六类迁移):每个函数只拼文案 + 挑 kind,扇出一律交给 push_event
# ══════════════════════════════════════════════════════════════════════════

def push_report_ready(
    trade_date_disp: str, *, db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """16:35 盘后报告就绪(kind=`report_ready`,**盘后汇总**级)。"""
    return push_event(
        KIND_REPORT_READY,
        "今日盘后报告已生成",
        f"{trade_date_disp} 盘后报告与今日篮子已就绪。{_OPEN_APP_LATER}",
        custom_extra={"tradeDate": trade_date_disp},
        db_path=db_path, transport=transport,
    )


def push_retreat_brake(
    reason: str, *, db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """退潮红色刹车(kind=`retreat`,**立即**级)。`reason` = 退潮哨兵的刹车文案。"""
    body = "今日计划作废、禁开新仓。" + (f" 依据:{reason}" if reason else "")
    return push_event(
        KIND_RETREAT, "退潮红色刹车", body + _OPEN_APP_NOW,
        db_path=db_path, transport=transport,
    )


# 熔断锁定期盘前提醒的固定措辞(= `sentinel/precall.py::CIRCUIT_LOCKED_PRECALL_NOTE`;
# 此处按字面量引用以免 notify → sentinel 重耦合,与 `_KIND_TIME_EXIT` 同惯例,
# 一致性由 `tests/test_notify.py` 结构性断言守护)。
_CIRCUIT_LOCKED_NOTE = "熔断中:今日只减不加"


def push_precall_summary(
    counts: dict, *, circuit_locked: bool = False,
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """9:26 盘前校准汇总(kind=`precall`,**重要不紧急**级,plan v1.1-A.4)。`counts` =
    盘前校准四类判定的计数 dict(`gap_up` 买点变形 / `low_open` 开盘证伪 /
    `position_low_open` 持仓预警 / `auction` 竞价量能异常附注 / `member_ex_rights`
    疑似除权除息导致**今日核对不了**的成员数)。**盘前不产新票、不推荐买入**
    (§2.4 铁原则),只汇总「前晚计划被集合竞价作废/预警」的条数。

    `member_ex_rights`(判定线审计 🟡-2,2026-08-03)照 `auction` 的体例做附注:
    它既不是判定也不触发推送(缺键 → 0,老调用方零感知),但**「今天没核对」必须与
    「核对过、没异常」分得开**,否则用户会把沉默读成平安。

    `circuit_locked`(2026-07-27 审计 🟡-4):熔断仍锁定 → **标题与正文前置「熔断中:今日只减
    不加」**(§2.1 第 7 条纪律的「次日」那一半)。调用方(`_sentinel_loop`)按
    `PrecallResult.should_push_summary` 决定推不推——锁定期间即便零判定也推,不被「平静清晨
    不轰炸」的门槛吞掉。**纯提醒层**(§3.8):本函数只发文字,绝不代下单/撤单。"""
    n = int(counts.get("gap_up", 0))
    m = int(counts.get("low_open", 0))
    k = int(counts.get("position_low_open", 0))
    a = int(counts.get("auction", 0))
    x = int(counts.get("member_ex_rights", 0))
    body = f"集合竞价校准:{n} 只买点变形、{m} 只开盘证伪、{k} 只持仓止损预警"
    if a:
        body += f"(另 {a} 只竞价量能异常)"
    if x:
        body += f"(另 {x} 只疑似除权除息、冻结锚失效,今日未核对)"
    body += "。前晚计划按校准结果执行," + _OPEN_APP_NOW
    title = "盘前校准提醒"
    if circuit_locked:
        title = f"{_CIRCUIT_LOCKED_NOTE}(盘前提醒)"
        body = f"{_CIRCUIT_LOCKED_NOTE}——熔断未解锁,今日禁开新仓、只许减仓。" + body
    return push_event(
        KIND_PRECALL, title, body,
        custom_extra={"circuitLocked": bool(circuit_locked)},
        db_path=db_path, transport=transport,
    )


# v1.3 两档时间退出状态码(= `PositionOut.timeExitState` 契约 / `sentinel/precall.py` 常量,
# 唯一源在那两处;此处按字面量引用以免 notify → precall 重耦合,与 CLOSE_REASON Literal 惯例同)。
_KIND_TIME_EXIT = "time_exit_next_day"
_KIND_HARD_CAP = "hard_cap_exit"


def push_d5_exit(
    name: str, code: str, d_count: int, *,
    kind: str = _KIND_TIME_EXIT, max_hold_effective: Optional[int] = None, two_tier: bool = False,
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """时间退出(通知 kind=`d5exit`,**重要不紧急**级;plan v1.1-B.2 / v1.3-①-D 两档)。
    ⚠ 参数名 `kind` 是**时间退出状态码**(`time_exit_next_day`/`hard_cap_exit`),
    与通知 kind 是两回事,签名沿用 v1.1 不改(调用点已在 `_sentinel_loop`)。

    `d_count` = 现役 `max_hold_days`(不硬编 5;改 config 则随之)。**两档文案**:
      · `kind='hard_cap_exit'`:「D{n} 已达浮盈硬上限 D{k},按计划离场」(浮盈豁免单到 D15 硬退)。
      · `kind='time_exit_next_day'`:非浮盈单——`two_tier=True`(v1.3 章程激活)标「净浮盈 ≤0」;
        `two_tier=False`(K1 单档无条件时间退出)不标净浮盈(单档退出与浮亏浮盈无关,兜底 v1.1 文案)。
    浮盈豁免单(`profit_exempt`)**不推**本函数(它没到退出,只在客户端 D 徽标转 D{n}/D{15} 档)。"""
    disp = name or code
    if kind == _KIND_HARD_CAP:
        title = "浮盈硬上限时间退出"
        body = f"{disp} 今日 D{d_count} 已达浮盈硬上限 D{max_hold_effective},按计划离场。"
    elif two_tier:
        title = "时间退出"
        body = f"{disp} 今日 D{d_count} 时间退出日(净浮盈 ≤0),按计划离场。"
    else:
        title = "D5 时间退出"
        body = f"{disp} 今日 D{d_count} 时间退出日,按计划离场(时间退出是规则 v1 采纳纪律)。"
    return push_event(
        KIND_D5EXIT, title, body + _OPEN_APP_NOW,
        custom_extra={"code": code, "timeExitState": kind},
        db_path=db_path, transport=transport,
    )


def push_circuit_breaker(
    episode: Any, *, db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """熔断提醒(kind=`circuit`,**立即**级,plan v1.2-A2.6)。`episode` =
    `neckline.sentinel.circuit.CircuitEpisode`(触发行)——文案取其 `note`(诚实边界:
    「基于台账 N 笔已补录成交」已在 note 内)。**熔断是纯提醒层**(§3.8):本函数
    只发提醒,绝不代下单/撤单。"""
    note = getattr(episode, "note", "") or "触发熔断:今日停开新仓、次日只减不加,完成一次强制复盘后解锁。"
    reason = getattr(episode, "trigger_reason", "")
    return push_event(
        KIND_CIRCUIT, "熔断提醒", note + _OPEN_APP_NOW,
        custom_extra={"triggerReason": reason},
        db_path=db_path, transport=transport,
    )


def push_holding_alert(
    name: str, code: str, hit_labels: List[str], *,
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """K4 持仓派发警报(kind=`holding_alert`,**重要不紧急**级 —— 三条强证据〔年线下
    涨停 / 放量大阳派发 / 换手>10%〕全属蓝图 5.5「放量异动」一族,不是止损那种秒级
    事件;plan §五 v1.3-②-C)。每只触发**强价量证据**命中的持仓推一条(≤3 仓,逐仓
    推比汇总更可执行)。**只推强价量证据命中** —— 题材持续天数是概念板块成分弱证据
    (K2 成分洞),只进盘中看板不推(§2.4「证伪只用价量结构」)。

    **系统永不代交易动作**(§3.8):本函数只发提醒(建议减仓/勿追),绝不代下单/撤单/改止损。"""
    disp = name or code
    reason = ";".join(hit_labels) if hit_labels else "触发年线下派发信号"
    return push_event(
        KIND_HOLDING_ALERT, "持仓派发警报",
        f"{disp} {reason}。疑似诱多做局,建议减仓/勿追(系统不代下单)。{_OPEN_APP_NOW}",
        custom_extra={"code": code},
        db_path=db_path, transport=transport,
    )


# ══════════════════════════════════════════════════════════════════════════
# V2-⑪ 新增措辞层:四监测(⑪-A)+ NL 临时提醒命中(⑪-C)
# ══════════════════════════════════════════════════════════════════════════
#
# 四监测的文案统一遵守 ⑪-B 的三句式:**发生了什么 / 触碰了哪条计划 / 要不要打开
# APP**。判定与阈值一律不在这里 —— 那是 `sentinel/attention.py` 的事,本层只措辞。

def push_attention_alert(
    kind: str, title: str, what_happened: str, *,
    plan_touched: str = "", code: str = "", position_id: Optional[int] = None,
    merged_exposure_note: str = "",
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """⑪-A 四监测的共用措辞入口(kind ∈ 四个监测 kind,**重要不紧急**级)。

    三句式装配:`what_happened`(发生了什么,由监测器给出的事实句)+ `plan_touched`
    (触碰了哪条计划;监测器给不出就省略这一句 —— **⛔ 不编一句**)+ 固定的「要不要
    打开 APP」。`merged_exposure_note` 是同篮合并敞口提示(蓝图 6.2),有才带。

    **参考、非指令**:四监测全是旁路观察,不构成任何交易指令,也不改任何纪律判定。"""
    if kind not in (KIND_BASKET_PEERS_WEAK, KIND_SECTOR_BID_FADE,
                    KIND_HOLDING_DECOUPLED, KIND_MARKET_SHOCK):
        raise ValueError(f"push_attention_alert 只接受四监测 kind,收到 {kind!r}")
    parts = [what_happened.rstrip("。") + "。"]
    if plan_touched:
        parts.append(plan_touched.rstrip("。") + "。")
    if merged_exposure_note:
        parts.append(merged_exposure_note.rstrip("。") + "。")
    parts.append("参考、非指令(系统不代下单)。" + _OPEN_APP_LATER)
    extra: Dict[str, Any] = {}
    if code:
        extra["code"] = code
    if position_id is not None:
        extra["positionId"] = position_id
    return push_event(
        kind, title, "".join(parts),
        custom_extra=extra or None, db_path=db_path, transport=transport,
    )


def push_custom_alert(
    alert_id: int, subject: str, condition_text: str, *,
    code: str = "", quote_delay_note: str = "",
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """NL 临时提醒命中(kind=`custom_alert`,**立即**级 —— 蓝图 5.5 逐字点名的第一条
    「用户自定义价格条件」;plan ⑪-C)。

    `condition_text` = 命中的那条规则的人读描述(由 `custom_alerts.describe_rule`
    生成,**不是** LLM 自由文本);`quote_delay_note` = 行情延迟披露(确认卡上答应过
    用户的那句,命中时再说一遍 —— 免得用户拿一条延迟行情当成真的瞬时成交价)。

    **只通知不自动交易**(⑪-C 安全要求 + §3.8):这条固定尾巴不可删。"""
    body = f"{subject} {condition_text}。"
    if quote_delay_note:
        body += quote_delay_note.rstrip("。") + "。"
    body += "只通知不自动交易。" + _OPEN_APP_NOW
    extra: Dict[str, Any] = {"alertId": int(alert_id)}
    if code:
        extra["code"] = code
    return push_event(
        KIND_CUSTOM_ALERT, "临时提醒命中", body,
        custom_extra=extra, db_path=db_path, transport=transport,
    )


# ══════════════════════════════════════════════════════════════════════════
# 2026-08-03 用户拍板新增措辞层:持仓哨兵既有三事件升级立即级
# ══════════════════════════════════════════════════════════════════════════

def push_holding_risk_alert(
    kind: str, title: str, reason: str, *, code: str = "",
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """持仓哨兵三事件的 APNs 旁路(kind ∈ `stop_approach`/`take_profit`/
    `sector_dive`,**立即**级;2026-08-03 用户定向拍板 —— ⑪-B 完工记录曾登记
    「未开」,今日由用户拍板开闸,plan §五 V2-⑪-B 定向任务书)。

    `title`/`reason` 由调用方(`sentinel/engine.py::run_tick`)原样传入,按 kind
    分两条路:
      · `stop_approach` / `sector_dive` —— 与看板/Bark 通道(`sentinel/channels.py
        ::push_all`)喂的是**同一份**文案(`_maybe_push` 内部转手调用,复用同一次
        `already_pushed`/`record_pushed` 去重),不二次措辞、不二次去重。
      · `take_profit` —— **不是** `sentinel/holding.py::check_take_profit`
        (回落止盈,现役章程机械纪律,继续独立驱动 console/Bark、一字不动),而是
        `check_exit_reference_reached`(触达 `position_plans` 继承的离场参考区间)
        ——旁路专属、独立去重。两者刻意不同源,见该函数 docstring。

    本函数只补⑪-B 三句式缺的第三句「要不要打开APP」,不改事实本身(`reason` 已经
    讲清「发生了什么 / 触碰了哪条计划」)。**系统永不代交易动作**(§3.8):即便止损
    已跌破、即便已触达离场参考,本函数不追加任何"该卖了 / 建议减仓 / 建议止盈"式
    表述——离场参考是参考、回落止盈才是纪律,语义红线不因走了 APNs 通道而松动。"""
    if kind not in (KIND_STOP_APPROACH, KIND_TAKE_PROFIT, KIND_SECTOR_DIVE):
        raise ValueError(f"push_holding_risk_alert 只接受持仓三事件 kind,收到 {kind!r}")
    body = reason.rstrip("。") + "。" + _OPEN_APP_NOW
    return push_event(
        kind, title, body, custom_extra={"code": code} if code else None,
        db_path=db_path, transport=transport,
    )


__all__ = [
    "NotifyOutcome",
    # 唯一扇出路径(白名单的真正落点)
    "push_event",
    # 措辞层(V1 六类迁移)
    "push_report_ready",
    "push_retreat_brake",
    "push_precall_summary",
    "push_d5_exit",
    "push_circuit_breaker",
    "push_holding_alert",
    # 措辞层(V2-⑪ 新增)
    "push_attention_alert",
    "push_custom_alert",
    # 措辞层(2026-08-03 用户拍板新增)
    "push_holding_risk_alert",
]
