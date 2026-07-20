"""APNs 推送编排(plan 4B.5,🔴)。**只推两类**(§2.4 拍板):
    ① 16:00 盘后报告就绪 —— `push_report_ready()`,受 `app_settings.push_report` 开关。
    ② 退潮红色刹车     —— `push_retreat_brake()`,受 `app_settings.push_retreat` 开关。
**买点/证伪/持仓一律不推**(只进 4A.3 看板)——本模块只暴露这两个入口,不给第三类
事件留推送路径,是「只推两类」的落点保证。

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


__all__ = ["NotifyOutcome", "push_report_ready", "push_retreat_brake"]
