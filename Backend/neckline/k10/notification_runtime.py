"""Worker-only notification wiring; import and API reads have no side effects."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from neckline import notify_kinds
from neckline.api.stores import delete_device, list_device_tokens
from neckline.push.apns import send_push
from neckline.settings_store import push_kind_enabled

from .notifications import DeliveryResult, dispatch_task_notifications, on_task_terminal
from .schema import read_connection, require_schema


def reconcile_terminal_notifications(*, db_path: Path, now: datetime, limit: int = 100) -> int:
    """Recover a crash between task completion and insertion of the notification."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        rows = conn.execute(
            "SELECT t.task_id FROM k10_tasks t WHERE t.kind IN ('analysis','evening_scan','morning_scan') "
            "AND t.status IN ('completed','failed','not_configured','cancelled') AND t.attempt_count>0 "
            "AND NOT EXISTS (SELECT 1 FROM k10_task_notifications n WHERE n.task_id=t.task_id "
            "AND n.task_attempt_count=t.attempt_count AND n.terminal_status=t.status) "
            "ORDER BY t.updated_at LIMIT ?", (limit,),
        ).fetchall()
    for task_id, in rows:
        on_task_terminal(task_id=task_id, db_path=db_path, created_at=now)
    return len(rows)


def create_notification_maintenance(*, db_path: Path, worker_id: str) -> Callable[[], None]:
    """Assemble actual APNs only in the explicitly started worker process."""
    def sender(*, token, title, body, kind, deep_link, collapse_id):
        if not push_kind_enabled(kind, db_path=db_path):
            return DeliveryResult(ok=True)  # A disabled preference is terminal, not delayed delivery.
        result = send_push(token, title, body, category=notify_kinds.category_of(kind),
                           thread_id="neckline-k10", custom={"kind": kind, **deep_link}, collapse_id=collapse_id)
        return DeliveryResult(ok=result.ok, reason=result.reason,
                              permanent_invalid=result.reason in {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"})

    def maintain() -> None:
        reconcile_terminal_notifications(db_path=db_path, now=datetime.now(timezone.utc))
        dispatch_task_notifications(
            db_path=db_path, list_device_tokens=lambda: list_device_tokens(db_path=db_path),
            delete_device=lambda token: delete_device(token, db_path=db_path), sender=sender,
            worker_id=worker_id + "-notifications", now=datetime.now(timezone.utc),
            clock=lambda: datetime.now(timezone.utc),
        )
    return maintain


__all__ = ["create_notification_maintenance", "reconcile_terminal_notifications"]
