"""APNs 推送编排(plan 4B.5 / v1.1-A/B,🔴)。**只推四类**(§2.4 v1.1 拍板,推送
白名单 = 四类,各自独立开关 + 独立 APNs category):
    ① 16:35 盘后报告就绪 —— `push_report_ready()`,受 `app_settings.push_report` 开关。
    ② 退潮红色刹车     —— `push_retreat_brake()`,受 `app_settings.push_retreat` 开关。
    ③ 9:26 盘前校准汇总 —— `push_precall_summary()`,受 `app_settings.push_precall` 开关。
    ④ D5 时间退出      —— `push_d5_exit()`,受 `app_settings.push_d5exit` 开关。
**买点/证伪/持仓一律不推**(只进看板)——本模块只暴露这四个入口,不给第五类事件
留推送路径,是「白名单四类」的落点保证(单测按 `__all__` 结构性守护)。

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


def push_precall_summary(
    counts: dict, *, db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """9:26 盘前校准汇总推送(受 push_precall 开关,plan v1.1-A.4)。`counts` = 盘前
    校准四类判定的计数 dict(`gap_up` 买点变形 / `low_open` 开盘证伪 / `position_low_open`
    持仓预警 / `auction` 竞价量能异常附注)。文案汇总「N 只买点变形 / M 只开盘证伪 /
    K 只持仓预警」,点开跳盘中看板。**盘前不产新票、不推荐买入**(§2.4 铁原则),只汇总
    「前晚计划被集合竞价作废/预警」的条数。"""
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
    return _fanout(
        "盘前校准提醒",
        body,
        category=apns.CATEGORY_PRECALL,
        custom={"kind": "precall"},
        db_path=db_path, transport=transport,
    )


def push_d5_exit(
    name: str, code: str, d_count: int, *,
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """D5 时间退出推送(受 push_d5exit 开关,plan v1.1-B.2)。`d_count` = 现役
    `max_hold_days`(不硬编 5;改 config 到 3 则 D3 触发,文案随之)。点开跳今日计划
    持仓区。"""
    st = get_app_settings(db_path=db_path)
    if not st.push_d5exit:
        return NotifyOutcome(skipped_reason="push_d5exit_off")
    if not apns.settings.has_apns_config:
        return NotifyOutcome(skipped_reason="no_apns_config")
    disp = name or code
    return _fanout(
        "D5 时间退出",
        f"{disp} 今日 D{d_count} 时间退出日,按计划离场(时间退出是规则 v1 采纳纪律)。",
        category=apns.CATEGORY_D5EXIT,
        custom={"kind": "d5exit", "code": code},
        db_path=db_path, transport=transport,
    )


__all__ = [
    "NotifyOutcome",
    "push_report_ready",
    "push_retreat_brake",
    "push_precall_summary",
    "push_d5_exit",
]
