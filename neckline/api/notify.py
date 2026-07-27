"""APNs 推送编排(plan 4B.5 / v1.1-A/B / v1.2-A2 / v1.3-②,🔴)。**只推六类**(§2.4 拍板,
推送白名单 = 六类,各自独立开关 + 独立 APNs category):
    ① 16:35 盘后报告就绪 —— `push_report_ready()`,受 `app_settings.push_report` 开关。
    ② 退潮红色刹车     —— `push_retreat_brake()`,受 `app_settings.push_retreat` 开关。
    ③ 9:26 盘前校准汇总 —— `push_precall_summary()`,受 `app_settings.push_precall` 开关。
    ④ D5 时间退出      —— `push_d5_exit()`,受 `app_settings.push_d5exit` 开关。
    ⑤ 熔断提醒        —— `push_circuit_breaker()`,受 `app_settings.push_circuit` 开关
                          (v1.2-A2 第五类,§2.1 第 7 条;默认开、与退潮刹车同级)。
    ⑥ K4 持仓派发警报  —— `push_holding_alert()`,受 `app_settings.push_holding_alert` 开关
                          (v1.3-② 第六类,用户 2026-07-26 拍板独立 category + 独立开关;默认开。
                          **只推强价量证据命中**:年线下涨停/放量大阳派发/换手>10%;题材天数=概念
                          板块成分弱证据,只进看板不推,守 §2.4「证伪只用价量结构」)。
**买点/证伪/普通警示一律不推**(只进看板)——本模块只暴露这六个入口,不给第七类事件
留推送路径,是「白名单六类」的落点保证(单测按 `__all__` 结构性守护)。

每类推送:先查开关(关 → 直接跳过)→ 遍历 `devices` 表所有 token → 逐个 `apns.send_push`
(transport 可注入,单测免真连 Apple)。任何设备失败只记日志,不拖累其它设备 / 主流程
(推送是尽力而为,失败绝不能反过来打断报告落库或哨兵主循环)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from neckline.api.stores import list_device_tokens
from neckline.push import apns
from neckline.settings_store import get_app_settings

logger = logging.getLogger(__name__)


@dataclass
class NotifyOutcome:
    sent: int = 0
    failed: int = 0
    skipped_reason: str = ""     # 非空 = 未推送的原因(开关关 / 无设备 / 无 APNs 配置)


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


def push_report_ready(
    trade_date_disp: str, *, db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """16:00 盘后报告就绪推送(受 push_report 开关)。`trade_date_disp` 供文案(如 '2026-07-17')。"""
    st = get_app_settings(db_path=db_path)
    if not st.push_report:
        return NotifyOutcome(skipped_reason="push_report_off")
    if not apns.settings.has_apns_config:
        return NotifyOutcome(skipped_reason="no_apns_config")
    return _fanout(
        "今日盘后报告已生成",
        f"{trade_date_disp} 盘后报告与候选已就绪,点开查看今日计划。",
        category=apns.CATEGORY_REPORT,
        custom={"kind": "report", "tradeDate": trade_date_disp},
        db_path=db_path, transport=transport,
    )


def push_retreat_brake(
    reason: str, *, db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """退潮红色刹车推送(受 push_retreat 开关)。`reason` = 退潮哨兵的刹车文案。"""
    st = get_app_settings(db_path=db_path)
    if not st.push_retreat:
        return NotifyOutcome(skipped_reason="push_retreat_off")
    if not apns.settings.has_apns_config:
        return NotifyOutcome(skipped_reason="no_apns_config")
    body = "今日计划作废、禁开新仓。" + (f" 依据:{reason}" if reason else "")
    return _fanout(
        "退潮红色刹车",
        body,
        category=apns.CATEGORY_RETREAT,
        custom={"kind": "retreat"},
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
    """9:26 盘前校准汇总推送(受 push_precall 开关,plan v1.1-A.4)。`counts` = 盘前
    校准四类判定的计数 dict(`gap_up` 买点变形 / `low_open` 开盘证伪 / `position_low_open`
    持仓预警 / `auction` 竞价量能异常附注)。文案汇总「N 只买点变形 / M 只开盘证伪 /
    K 只持仓预警」,点开跳盘中看板。**盘前不产新票、不推荐买入**(§2.4 铁原则),只汇总
    「前晚计划被集合竞价作废/预警」的条数。

    `circuit_locked`(2026-07-27 审计 🟡-4):熔断仍锁定 → **标题与正文前置「熔断中:今日只减
    不加」**(§2.1 第 7 条纪律的「次日」那一半)。调用方(`_sentinel_loop`)按
    `PrecallResult.should_push_summary` 决定推不推——锁定期间即便零判定也推,不被「平静清晨
    不轰炸」的门槛吞掉。**纯提醒层**(§3.8):本函数只发文字,绝不代下单/撤单。"""
    st = get_app_settings(db_path=db_path)
    if not st.push_precall:
        return NotifyOutcome(skipped_reason="push_precall_off")
    if not apns.settings.has_apns_config:
        return NotifyOutcome(skipped_reason="no_apns_config")
    n = int(counts.get("gap_up", 0))
    m = int(counts.get("low_open", 0))
    k = int(counts.get("position_low_open", 0))
    a = int(counts.get("auction", 0))
    body = f"集合竞价校准:{n} 只买点变形、{m} 只开盘证伪、{k} 只持仓止损预警"
    if a:
        body += f"(另 {a} 只竞价量能异常)"
    body += "。点开看板核对,前晚计划按校准结果执行。"
    title = "盘前校准提醒"
    if circuit_locked:
        title = f"{_CIRCUIT_LOCKED_NOTE}(盘前提醒)"
        body = f"{_CIRCUIT_LOCKED_NOTE}——熔断未解锁,今日禁开新仓、只许减仓。" + body
    return _fanout(
        title,
        body,
        category=apns.CATEGORY_PRECALL,
        custom={"kind": "precall", "circuitLocked": bool(circuit_locked)},
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
    """时间退出推送(受 push_d5exit 开关,plan v1.1-B.2 / v1.3-①-D 两档)。`d_count` = 现役
    `max_hold_days`(不硬编 5;改 config 则随之)。**两档文案**(§五 v1.3-①-D):
      · `kind='hard_cap_exit'`:「D{n} 已达浮盈硬上限 D{k},按计划离场」(浮盈豁免单到 D15 硬退)。
      · `kind='time_exit_next_day'`:非浮盈单——`two_tier=True`(v1.3 章程激活)标「净浮盈 ≤0」;
        `two_tier=False`(K1 单档无条件时间退出)不标净浮盈(单档退出与浮亏浮盈无关,兜底 v1.1 文案)。
    浮盈豁免单(`profit_exempt`)**不推**本函数(它没到退出,只在客户端 D 徽标转 D{n}/D{15} 档)。
    白名单 `__all__` **六入口 / APNs 六类**(v1.3-② 起,本函数不新增第七类)。点开跳今日计划持仓区。"""
    st = get_app_settings(db_path=db_path)
    if not st.push_d5exit:
        return NotifyOutcome(skipped_reason="push_d5exit_off")
    if not apns.settings.has_apns_config:
        return NotifyOutcome(skipped_reason="no_apns_config")
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
    return _fanout(
        title, body,
        category=apns.CATEGORY_D5EXIT,
        custom={"kind": "d5exit", "code": code, "timeExitState": kind},
        db_path=db_path, transport=transport,
    )


def push_circuit_breaker(
    episode: Any, *, db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """熔断提醒推送(受 push_circuit 开关,plan v1.2-A2.6,第五类白名单)。`episode` =
    `neckline.sentinel.circuit.CircuitEpisode`(触发行)——文案取其 `note`(诚实边界:
    「基于台账 N 笔已补录成交」已在 note 内),点开跳今日计划(熔断处置最相关)。
    **熔断是纯提醒层**(§3.8):本函数只发提醒,绝不代下单/撤单。"""
    st = get_app_settings(db_path=db_path)
    if not st.push_circuit:
        return NotifyOutcome(skipped_reason="push_circuit_off")
    if not apns.settings.has_apns_config:
        return NotifyOutcome(skipped_reason="no_apns_config")
    note = getattr(episode, "note", "") or "触发熔断:今日停开新仓、次日只减不加,完成一次强制复盘后解锁。"
    reason = getattr(episode, "trigger_reason", "")
    return _fanout(
        "熔断提醒",
        note,
        category=apns.CATEGORY_CIRCUIT,
        custom={"kind": "circuit", "triggerReason": reason},
        db_path=db_path, transport=transport,
    )


def push_holding_alert(
    name: str, code: str, hit_labels: List[str], *,
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """K4 持仓派发警报推送(第六类白名单,受 push_holding_alert 开关,plan §五 v1.3-②-C)。
    每只触发**强价量证据**命中的持仓推一条(≤3 仓,逐仓推比汇总更可执行)。`hit_labels`
    = 该持仓命中的强警示项人读文案(如「年线下涨停疑似派发」)。**只推强价量证据命中**
    (年线下涨停/放量大阳派发/换手>10%)——题材持续天数是概念板块成分弱证据(K2 成分洞),
    只进盘中看板不推(§2.4「证伪只用价量结构」,弱证据不触发 APNs)。

    **系统永不代交易动作**(§3.8):本函数只发提醒(建议减仓/勿追),绝不代下单/撤单/改止损。
    独立 category `HOLDINGALERT`(用户 2026-07-26 拍板不复用 D5EXIT:D5=「持有到期」、K4=「可能被
    派发」是两回事,合并后关一个会连坐另一个)。点开跳今日计划持仓区。"""
    st = get_app_settings(db_path=db_path)
    if not st.push_holding_alert:
        return NotifyOutcome(skipped_reason="push_holding_alert_off")
    if not apns.settings.has_apns_config:
        return NotifyOutcome(skipped_reason="no_apns_config")
    disp = name or code
    reason = ";".join(hit_labels) if hit_labels else "触发年线下派发信号"
    return _fanout(
        "持仓派发警报",
        f"{disp} {reason}。疑似诱多做局,建议减仓/勿追(系统不代下单)。",
        category=apns.CATEGORY_HOLDING_ALERT,
        custom={"kind": "holding_alert", "code": code},
        db_path=db_path, transport=transport,
    )


__all__ = [
    "NotifyOutcome",
    "push_report_ready",
    "push_retreat_brake",
    "push_precall_summary",
    "push_d5_exit",
    "push_circuit_breaker",
    "push_holding_alert",
]
