"""APNs 推送：只保留盘后报告与次日竞价核对表。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from neckline import notify_kinds
from neckline.api.stores import delete_device, list_device_tokens
from neckline.notify_kinds import KIND_PRECALL, KIND_REPORT_READY
from neckline.push import apns
from neckline.settings_store import push_kind_enabled

logger = logging.getLogger(__name__)
_OPEN_APP_NOW = "点开 APP 核对。"
_OPEN_APP_LATER = "有空点开 APP 看详情。"
_PERMANENT_DEVICE_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}


def _permanently_invalid(result: apns.PushResult) -> bool:
    return result.status == 410 or result.reason in _PERMANENT_DEVICE_REASONS


@dataclass
class NotifyOutcome:
    sent: int = 0
    failed: int = 0
    skipped_reason: str = ""
    kind: str = ""
    level: str = ""


def _fanout(title: str, body: str, *, category: str, custom: Optional[dict],
            db_path: Optional[Path], transport: Optional[Any]) -> NotifyOutcome:
    tokens = list_device_tokens(db_path=db_path)
    if not tokens:
        return NotifyOutcome(skipped_reason="no_devices")
    out = NotifyOutcome()
    for token in tokens:
        try:
            result = apns.send_push(token, title, body, category=category,
                                    custom=custom, transport=transport)
        except Exception as exc:  # noqa: BLE001
            logger.warning("APNs 推送异常(token 尾4=%s):%s", token[-4:] if token else "", exc)
            out.failed += 1
            continue
        if result.ok:
            out.sent += 1
        else:
            out.failed += 1
            if _permanently_invalid(result):
                delete_device(token, db_path=db_path)
                logger.warning("APNs 设备已永久失效，已移除(token 尾4=%s status=%s reason=%s)",
                               token[-4:] if token else "", result.status, result.reason)
            else:
                logger.warning("APNs 推送失败(token 尾4=%s status=%s reason=%s)",
                               token[-4:] if token else "", result.status, result.reason)
    return out


def push_event(kind: str, title: str, body: str, *,
               custom_extra: Optional[Dict[str, Any]] = None,
               db_path: Optional[Path] = None,
               transport: Optional[Any] = None) -> NotifyOutcome:
    level = notify_kinds.level_of(kind)
    if not push_kind_enabled(kind, db_path=db_path):
        return NotifyOutcome(skipped_reason=f"kind_off:{kind}", kind=kind, level=level)
    if not apns.settings.has_apns_config:
        return NotifyOutcome(skipped_reason="no_apns_config", kind=kind, level=level)
    custom: Dict[str, Any] = {"kind": kind, "level": level}
    if custom_extra:
        custom.update(custom_extra)
    out = _fanout(title, body, category=notify_kinds.category_of(kind), custom=custom,
                  db_path=db_path, transport=transport)
    out.kind = kind
    out.level = level
    return out


def _report_date_label(report_date_disp: str) -> str:
    try:
        value = datetime.strptime(report_date_disp, "%Y-%m-%d")
    except (TypeError, ValueError):
        return str(report_date_disp)
    return f"{value.month}月{value.day}日"


def push_report_ready(report_date_disp: str, *, data_date_disp: Optional[str] = None,
                      selection_state: Optional[str] = None,
                      db_path: Optional[Path] = None,
                      transport: Optional[Any] = None) -> NotifyOutcome:
    state = str(selection_state or "").strip().lower()
    report_label = _report_date_label(report_date_disp)
    data_date = data_date_disp or report_date_disp
    data_suffix = f"（行情截至 {data_date}）" if data_date != report_date_disp else ""
    if state == "partial":
        title = f"{report_label}报告已生成，选股部分完成"
        body = f"{report_date_disp} 报告可看{data_suffix}；今日选股仍有方向未完成。{_OPEN_APP_NOW}"
    elif state == "unavailable":
        title = f"{report_label}报告已生成，选股未完成"
        body = f"{report_date_disp} 报告可看{data_suffix}；今日选股未完整跑成，不代表今天没有机会。{_OPEN_APP_NOW}"
    elif state == "processing":
        title = f"{report_label}报告已生成，选股仍在整理"
        body = f"{report_date_disp} 报告可看{data_suffix}；今日选股尚未形成最终结果。{_OPEN_APP_LATER}"
    else:
        title = f"{report_label}盘后报告已生成"
        body = f"{report_date_disp} 盘后报告与今日选股已就绪{data_suffix}。{_OPEN_APP_LATER}"
    return push_event(
        KIND_REPORT_READY, title, body,
        custom_extra={"reportDate": report_date_disp, "tradeDate": data_date,
                      **({"selectionState": state} if state else {})},
        db_path=db_path, transport=transport,
    )


def push_checklist_summary(counts: dict, *, db_path: Optional[Path] = None,
                           transport: Optional[Any] = None) -> NotifyOutcome:
    from neckline.auction.checklist import CHECKLIST_FOOTNOTE

    rejected = int(counts.get("rejected", 0))
    pending = int(counts.get("pendingOpen", 0))
    no_quote = int(counts.get("noQuote", 0))
    no_playbook = int(counts.get("noPlaybook", 0))
    quality = str(counts.get("dataQuality", "") or "")
    body = f"竞价核对表:{rejected} 只已触发放弃、{pending} 只待开盘后观察。"
    if no_quote:
        body += f"(其中 {no_quote} 只本次没有可用读数,已按待观察列出)"
    if no_playbook:
        body += f"(另 {no_playbook} 只没有冻结预案,今日核对不了)"
    if quality and quality != "ok":
        body += f"(本次数据质量:{quality})"
    return push_event(KIND_PRECALL, "竞价核对表", body + CHECKLIST_FOOTNOTE,
                      db_path=db_path, transport=transport)


def push_previous_report_not_run(trade_date: str, *, db_path: Optional[Path] = None,
                                 transport: Optional[Any] = None) -> NotifyOutcome:
    """前一交易日报告未跑成时明确提醒；可信空清单保持静默。"""
    body = f"{trade_date} 的盘后报告没有完整跑成，因此今天没有可核对的清单。{_OPEN_APP_NOW}"
    return push_event(KIND_PRECALL, "昨日报告未跑成", body,
                      custom_extra={"tradeDate": trade_date, "reportState": "not_run"},
                      db_path=db_path, transport=transport)


__all__ = [
    "NotifyOutcome", "push_event", "push_report_ready", "push_checklist_summary",
    "push_previous_report_not_run",
]
