"""Worker-only notification wiring; import and API reads have no side effects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import time
from typing import Callable

from neckline import notify_kinds
from neckline.api.stores import delete_device, list_device_tokens
from neckline.push.apns import apns_readiness, send_push
from neckline.settings_store import push_kind_enabled

from .notifications import (
    DeliveryResult, NOTIFICATION_SCHEMA_VERSION, NotificationSchemaUnavailable, dispatch_task_notifications,
    NotificationRetryPolicy, on_task_terminal, resume_configuration_blocked_notifications,
    suspend_notifications_for_configuration,
)
from .schema import SchemaUnavailable, read_connection, require_schema


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationReadiness:
    """Read-only operations DTO; names map directly to API notificationReadiness."""

    state: str
    reason_code: str | None
    next_retry_at: str | None
    checked_at: str


_SAFE_BLOCK_REASONS = frozenset(("credentials_missing", "key_unreadable", "key_invalid"))
_NOTIFICATION_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "notification-delivery-v1.json"


@dataclass(frozen=True)
class NotificationDeliveryConfig:
    retry_policy: NotificationRetryPolicy
    dispatch_batch_size: int


class NotificationDeliveryConfigError(RuntimeError):
    """The public readiness DTO maps this to a safe, non-secret code."""


def load_notification_delivery_config(path: Path | None = None) -> NotificationDeliveryConfig:
    """Read the explicit delivery engineering configuration; no numeric fallback exists."""
    actual_path = path or _NOTIFICATION_RUNTIME_CONFIG_PATH
    try:
        payload = json.loads(actual_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise NotificationDeliveryConfigError("missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise NotificationDeliveryConfigError("invalid") from exc
    expected = {"configVersion", "initialBackoffSeconds", "maxBackoffSeconds", "dispatchBatchSize"}
    if not isinstance(payload, dict) or set(payload) != expected or payload.get("configVersion") != "notification-delivery-v1":
        raise NotificationDeliveryConfigError("invalid")
    initial, maximum, batch = (payload["initialBackoffSeconds"], payload["maxBackoffSeconds"], payload["dispatchBatchSize"])
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (initial, maximum, batch)):
        raise NotificationDeliveryConfigError("invalid")
    try:
        return NotificationDeliveryConfig(
            retry_policy=NotificationRetryPolicy(timedelta(seconds=initial), timedelta(seconds=maximum)),
            dispatch_batch_size=batch,
        )
    except ValueError as exc:
        raise NotificationDeliveryConfigError("invalid") from exc


def _delivery_config_state(path: Path | None = None) -> tuple[NotificationDeliveryConfig | None, str | None]:
    try:
        return load_notification_delivery_config(path), None
    except NotificationDeliveryConfigError as exc:
        return None, "notification_runtime_config_missing" if str(exc) == "missing" else "notification_runtime_config_invalid"


def notification_readiness(*, db_path: Path, now: datetime | None = None) -> NotificationReadiness:
    """Return safe APNs/outbox state without DDL, delivery, or credential disclosure."""
    current = now or datetime.now(timezone.utc)
    checked_at = current.astimezone(timezone.utc).isoformat(timespec="seconds")
    apns = apns_readiness()
    try:
        with read_connection(db_path) as conn:
            require_schema(conn)
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='k10_notification_schema_migrations'"
            ).fetchone()
            if exists is None:
                return NotificationReadiness("notConfigured", "notification_schema_unavailable", None, checked_at)
            version = int(conn.execute("SELECT MAX(version) FROM k10_notification_schema_migrations").fetchone()[0] or 0)
            if version != NOTIFICATION_SCHEMA_VERSION:
                return NotificationReadiness("notConfigured", "notification_schema_unavailable", None, checked_at)
            blocked = conn.execute(
                "SELECT blocked_reason FROM k10_task_notifications WHERE status='queued' "
                "AND next_attempt_at IS NULL AND blocked_reason IS NOT NULL ORDER BY updated_at LIMIT 1"
            ).fetchone()
            retry = conn.execute(
                "SELECT MIN(next_attempt_at) FROM k10_task_notifications WHERE status='queued' "
                "AND next_attempt_at IS NOT NULL"
            ).fetchone()
    except (SchemaUnavailable, NotificationSchemaUnavailable):
        return NotificationReadiness("notConfigured", "notification_schema_unavailable", None, checked_at)
    _, runtime_config_error = _delivery_config_state()
    if runtime_config_error is not None:
        return NotificationReadiness("notConfigured", runtime_config_error, None, checked_at)
    if not apns.ready:
        state = "notConfigured" if apns.code == "credentials_missing" else "blocked"
        return NotificationReadiness(state, apns.code, None, checked_at)
    if blocked is not None:
        reason = str(blocked[0])
        return NotificationReadiness("blocked", reason if reason in _SAFE_BLOCK_REASONS else "delivery_blocked", None, checked_at)
    return NotificationReadiness("ready", None, str(retry[0]) if retry and retry[0] else None, checked_at)


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


def create_notification_maintenance(
    *, db_path: Path, worker_id: str, delivery_config_path: Path | None = None,
    delivery_config: NotificationDeliveryConfig | None = None,
) -> Callable[[], None]:
    """Assemble actual APNs only in the explicitly started worker process."""
    def sender(*, token, title, body, kind, deep_link, evidence_disclosure, collapse_id):
        if not push_kind_enabled(kind, db_path=db_path):
            return DeliveryResult(ok=True)  # A disabled preference is terminal, not delayed delivery.
        custom = {"kind": kind, **deep_link}
        if evidence_disclosure is not None:
            custom["evidenceDisclosure"] = dict(evidence_disclosure)
        result = send_push(token, title, body, category=notify_kinds.category_of(kind),
                           thread_id="neckline-k10", custom=custom, collapse_id=collapse_id)
        return DeliveryResult(ok=result.ok, reason=result.reason,
                              configuration_unavailable=result.reason in {
                                  "apns_credentials_missing", "apns_key_unreadable", "apns_key_invalid",
                              },
                              permanent_invalid=result.reason in {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"})

    next_error_log_at = 0.0

    def maintain() -> None:
        nonlocal next_error_log_at
        current = datetime.now(timezone.utc)
        try:
            runtime_config, runtime_config_error = ((delivery_config, None) if delivery_config is not None
                                                    else _delivery_config_state(delivery_config_path))
            if runtime_config_error is not None or runtime_config is None:
                return
            # First repair the task→outbox crash boundary, then decide whether
            # the outbox may talk to APNs at all.
            reconcile_terminal_notifications(db_path=db_path, now=current)
            readiness = apns_readiness()
            if not readiness.ready:
                suspend_notifications_for_configuration(
                    db_path=db_path, reason_code=readiness.code, now=current,
                )
                return
            resume_configuration_blocked_notifications(db_path=db_path, now=current)
            dispatch_task_notifications(
                db_path=db_path, list_device_tokens=lambda: list_device_tokens(db_path=db_path),
                delete_device=lambda token: delete_device(token, db_path=db_path), sender=sender,
                worker_id=worker_id + "-notifications", now=current,
                clock=lambda: datetime.now(timezone.utc), limit=runtime_config.dispatch_batch_size,
                retry_policy=runtime_config.retry_policy,
            )
        except Exception as exc:  # DB/device failures must never hold up task dispatch.
            monotonic_now = time.monotonic()
            if monotonic_now >= next_error_log_at:
                logger.warning("K10 notification maintenance pending (%s)", type(exc).__name__)
                next_error_log_at = monotonic_now + 60
    return maintain


__all__ = ["NotificationDeliveryConfig", "NotificationDeliveryConfigError", "NotificationReadiness",
           "create_notification_maintenance", "load_notification_delivery_config", "notification_readiness",
           "reconcile_terminal_notifications"]
