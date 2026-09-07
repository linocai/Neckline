"""Durable, K10-only APNs notification outbox.

This module owns a small independently versioned schema on the K10 database.
Its migration is explicit: reads and dispatch never create tables.  The worker
calls :func:`on_task_terminal` after the K10 task commit; a separate dispatcher
then fans one notification to devices with per-device delivery records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence
from uuid import uuid4

from neckline import notify_kinds

from .schema import SchemaUnavailable, read_connection, require_schema, write_connection


NOTIFICATION_SCHEMA_VERSION = 1
NOTIFICATION_KINDS = frozenset(notify_kinds.ALL_KINDS)
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "not_configured", "cancelled"})
_PERMANENT_DEVICE_REASONS = frozenset({"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"})


class NotificationError(RuntimeError):
    pass


class NotificationConflict(NotificationError):
    pass


class NotificationSchemaUnavailable(NotificationError):
    pass


@dataclass(frozen=True)
class Notification:
    notification_id: str
    task_id: str
    kind: str
    terminal_status: str
    task_attempt_count: int
    title: str
    body: str
    deep_link: Mapping[str, str]
    status: str
    attempt_count: int
    created_at: str


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    permanent_invalid: bool = False
    reason: str = ""


class NotificationSender(Protocol):
    def __call__(
        self, *, token: str, title: str, body: str, kind: str,
        deep_link: Mapping[str, str], collapse_id: str,
    ) -> DeliveryResult:
        ...


DeviceTokens = Callable[[], Sequence[str]]
DeleteDevice = Callable[[str], bool]


_V1 = """
CREATE TABLE k10_notification_schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);
CREATE TABLE k10_task_notifications (
  notification_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  task_id TEXT NOT NULL REFERENCES k10_tasks(task_id) ON DELETE RESTRICT,
  task_attempt_count INTEGER NOT NULL CHECK(task_attempt_count >= 1),
  kind TEXT NOT NULL CHECK(kind IN ('k10_evening','k10_morning','k10_analysis','k10_failure')),
  terminal_status TEXT NOT NULL CHECK(terminal_status IN ('completed','failed','not_configured','cancelled')),
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  deep_link_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','sending','sent')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
  last_error TEXT,
  lease_owner TEXT,
  lease_until TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_k10_notifications_claim
  ON k10_task_notifications(status, lease_until, created_at);
CREATE TABLE k10_notification_deliveries (
  notification_id TEXT NOT NULL REFERENCES k10_task_notifications(notification_id) ON DELETE RESTRICT,
  device_key TEXT NOT NULL,
  delivered_at TEXT NOT NULL,
  PRIMARY KEY(notification_id, device_key)
);
"""
_DROP_V1 = ("k10_notification_deliveries", "k10_task_notifications", "k10_notification_schema_migrations")


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("通知时间必须带时区")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _version(conn) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='k10_notification_schema_migrations'"
    ).fetchone()
    if exists is None:
        return 0
    row = conn.execute("SELECT MAX(version) FROM k10_notification_schema_migrations").fetchone()
    return int(row[0] or 0)


def _require_notifications_schema(conn) -> None:
    require_schema(conn)
    version = _version(conn)
    if version != NOTIFICATION_SCHEMA_VERSION:
        raise NotificationSchemaUnavailable(
            f"K10 notification schema 版本为 {version or '未建立'}，当前需要 {NOTIFICATION_SCHEMA_VERSION}；请走受控迁移"
        )


def initialize_notifications_schema(db_path: Path, *, applied_at: datetime | None = None) -> int:
    """Explicit write-only migration; it is never reached from API reads or dispatch."""
    stamp = _utc_text(applied_at or datetime.now(timezone.utc))
    with write_connection(db_path) as conn:
        require_schema(conn)
        existing = _version(conn)
        if existing > NOTIFICATION_SCHEMA_VERSION:
            raise NotificationError("K10 notification schema 比运行时新")
        if existing == 0:
            for statement in _V1.split(";"):
                statement = statement.strip()
                if statement:
                    conn.execute(statement)
            conn.execute(
                "INSERT INTO k10_notification_schema_migrations(version,applied_at) VALUES (?,?)",
                (NOTIFICATION_SCHEMA_VERSION, stamp),
            )
        elif existing != NOTIFICATION_SCHEMA_VERSION:
            raise NotificationError("缺少 K10 notification schema 迁移")
    return NOTIFICATION_SCHEMA_VERSION


def rollback_notifications_schema(db_path: Path, *, target_version: int = 0) -> int:
    """Explicit rollback for migration rehearsal; caller must have a verified backup."""
    if target_version != 0:
        raise ValueError("当前仅支持回滚 notification schema 到 0")
    with write_connection(db_path) as conn:
        require_schema(conn)
        version = _version(conn)
        if version == 0:
            return 0
        if version != NOTIFICATION_SCHEMA_VERSION:
            raise NotificationError("不能回滚未知 K10 notification schema")
        for table in _DROP_V1:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    return 0


_CURRENT_DEEP_LINK_KEYS = frozenset(("companyWindowId", "opportunityId", "batchId", "scanId"))
_LEGACY_DEEP_LINK_KEYS = frozenset(("observationId", "companyCandidateId"))
_LEGACY_ANALYSIS_BODY = "可查看正反全文，以及资料与价位草案的完成状态。"


def _deep_link(payload: Mapping[str, object]) -> dict[str, str]:
    return {key: value for key in _CURRENT_DEEP_LINK_KEYS if isinstance((value := payload.get(key)), str) and value}


def _legacy_deep_link_only(payload: Mapping[str, object]) -> bool:
    """Recognize a B31 outbox value without treating arbitrary keys as compatible.

    Existing queued rows are data, rather than a schema version.  They may have
    been enqueued before the V1.4 ID allow-list and contain only the two retired
    identifiers plus the still-valid scan ID.  A duplicate terminal hook must be
    idempotent across that upgrade, but arbitrary persisted keys must still be
    rejected as a content conflict.
    """
    keys = set(payload)
    return bool(keys & _LEGACY_DEEP_LINK_KEYS) and keys <= _LEGACY_DEEP_LINK_KEYS | _CURRENT_DEEP_LINK_KEYS


def _stored_deep_link(value: object) -> dict[str, str]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else {}
    except json.JSONDecodeError:
        parsed = {}
    return _deep_link(parsed) if isinstance(parsed, Mapping) else {}


def _outbound_message(notification: Notification) -> tuple[str, str]:
    """Normalize user-visible B31 analysis rows without rewriting the outbox."""
    if notification.kind == notify_kinds.KIND_K10_ANALYSIS:
        return _message(notification.kind, terminal_status=notification.terminal_status, stage="")
    return notification.title, notification.body


def _kind(*, task_kind: str, terminal_status: str, payload: Mapping[str, object], requested: str | None) -> str:
    if requested is not None:
        if requested not in NOTIFICATION_KINDS:
            raise ValueError("未知 K10 notification kind")
        return requested
    if terminal_status != "completed":
        return notify_kinds.KIND_K10_FAILURE
    if task_kind == "analysis":
        return notify_kinds.KIND_K10_ANALYSIS
    return notify_kinds.KIND_K10_MORNING if payload.get("windowKind") == "morning" else notify_kinds.KIND_K10_EVENING


def _message(kind: str, *, terminal_status: str, stage: str) -> tuple[str, str]:
    if kind == notify_kinds.KIND_K10_ANALYSIS:
        return "K10 分析已更新", "可查看正反分析全文，以及资料完成状态。"
    if kind == notify_kinds.KIND_K10_MORNING:
        return "K10 晨间变化已更新", "隔夜资料与相关观察对象已更新，请查看重大反证和待核事项。"
    if kind == notify_kinds.KIND_K10_EVENING:
        return "K10 晚间机会已更新", "本次扫描结果已就绪，请查看事件、公司候选与资料覆盖情况。"
    reason = {
        "configuration": "配置未完成",
        "input": "冻结资料不完整",
        "execution": "执行失败",
        "attempt_limit": "达到重试上限",
    }.get(stage, "任务未完成")
    if terminal_status == "cancelled":
        reason = "任务已取消"
    return "K10 任务需要查看", f"{reason}，未自动执行任何交易操作。请打开 APP 查看详情或重试。"


def enqueue_task_notification(
    *, task_id: str, db_path: Path, created_at: datetime,
    notification_kind: str | None = None,
) -> Notification:
    """Create one notification per task attempt, terminal state, and kind.

    A task retry increments ``k10_tasks.attempt_count`` on its next lease.  A later
    terminal failure is therefore a new user-visible incident, while duplicate
    terminal hooks for the same attempt remain idempotent.
    """
    stamp = _utc_text(created_at)
    with write_connection(db_path) as conn:
        _require_notifications_schema(conn)
        row = conn.execute(
            "SELECT kind,status,stage,attempt_count,payload_json,checkpoint_json FROM k10_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise NotificationError("K10 任务不存在")
        task_kind, terminal_status, stage, task_attempt_count = str(row[0]), str(row[1]), str(row[2]), int(row[3])
        if terminal_status not in TERMINAL_TASK_STATUSES:
            raise NotificationConflict("通知只能由已经终态的 K10 任务触发")
        if task_attempt_count < 1:
            raise NotificationConflict("未领取过的 K10 任务不能生成终态通知")
        payload = json.loads(row[4])
        payload = payload if isinstance(payload, Mapping) else {}
        kind = _kind(task_kind=task_kind, terminal_status=terminal_status, payload=payload, requested=notification_kind)
        title, body = _message(kind, terminal_status=terminal_status, stage=stage)
        checkpoint = json.loads(row[5])
        checkpoint = checkpoint if isinstance(checkpoint, Mapping) else {}
        deep_link = _deep_link({**payload, **checkpoint})
        idempotency_key = f"k10-notification:{task_id}:attempt:{task_attempt_count}:{terminal_status}:{kind}"
        existing = conn.execute(
            "SELECT notification_id,task_id,kind,terminal_status,task_attempt_count,title,body,deep_link_json,status,attempt_count,created_at "
            "FROM k10_task_notifications WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if existing is not None:
            expected = (task_id, kind, terminal_status, task_attempt_count, title, body, _json(deep_link))
            try:
                stored_payload = json.loads(existing[7])
            except json.JSONDecodeError:
                raise NotificationConflict("K10 通知幂等键对应深链无效") from None
            if not isinstance(stored_payload, Mapping) or set(stored_payload) - (_CURRENT_DEEP_LINK_KEYS | _LEGACY_DEEP_LINK_KEYS):
                raise NotificationConflict("K10 通知幂等键对应深链无效")
            if kind == notify_kinds.KIND_K10_ANALYSIS and str(existing[6]) not in {body, _LEGACY_ANALYSIS_BODY}:
                raise NotificationConflict("K10 通知幂等键对应内容已变化")
            stored_link = _stored_deep_link(existing[7])
            actual = (*tuple(existing[1:6]), body if str(existing[2]) == notify_kinds.KIND_K10_ANALYSIS else existing[6],
                      _json(stored_link))
            immutable_match = tuple(existing[1:6]) == (task_id, kind, terminal_status, task_attempt_count, title)
            permitted_analysis_body = kind == notify_kinds.KIND_K10_ANALYSIS and str(existing[6]) in {body, _LEGACY_ANALYSIS_BODY}
            matching_current_ids = all(
                key not in stored_payload or stored_payload[key] == deep_link.get(key)
                for key in _CURRENT_DEEP_LINK_KEYS
            )
            legacy_upgrade_match = (immutable_match and permitted_analysis_body and
                                    _legacy_deep_link_only(stored_payload) and matching_current_ids)
            if actual != expected and not legacy_upgrade_match:
                raise NotificationConflict("K10 通知幂等键对应内容已变化")
            # Keep a legacy row readable but return the current public contract to
            # the task hook.  Dispatch applies the same filter for rows which never
            # receive another terminal hook.
            return Notification(str(existing[0]), task_id, kind, terminal_status, task_attempt_count,
                                title, body, deep_link, str(existing[8]), int(existing[9]), str(existing[10]))
        notification_id = str(uuid4())
        conn.execute(
            "INSERT INTO k10_task_notifications(notification_id,idempotency_key,task_id,kind,terminal_status,task_attempt_count,title,body,"
            "deep_link_json,status,attempt_count,last_error,lease_owner,lease_until,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,'queued',0,NULL,NULL,NULL,?,?)",
            (notification_id, idempotency_key, task_id, kind, terminal_status, task_attempt_count, title, body, _json(deep_link), stamp, stamp),
        )
    return Notification(notification_id, task_id, kind, terminal_status, task_attempt_count, title, body, deep_link, "queued", 0, stamp)


def on_task_terminal(**kwargs) -> Notification:
    """Worker-friendly terminal hook; intentionally a named wrapper for runtime wiring."""
    return enqueue_task_notification(**kwargs)


def _notification(row) -> Notification:
    return Notification(
        notification_id=str(row[0]), task_id=str(row[1]), kind=str(row[2]), terminal_status=str(row[3]),
        task_attempt_count=int(row[4]), title=str(row[5]), body=str(row[6]), deep_link=json.loads(row[7]), status=str(row[8]),
        attempt_count=int(row[9]), created_at=str(row[10]),
    )


def get_notification(*, notification_id: str, db_path: Path) -> Notification | None:
    """Strict read-only inspection for API/UI diagnostics; no migration or task trigger."""
    try:
        with read_connection(db_path) as conn:
            _require_notifications_schema(conn)
            row = conn.execute(
                "SELECT notification_id,task_id,kind,terminal_status,task_attempt_count,title,body,deep_link_json,status,attempt_count,created_at "
                "FROM k10_task_notifications WHERE notification_id=?", (notification_id,)
            ).fetchone()
    except (SchemaUnavailable, NotificationSchemaUnavailable):
        raise
    return _notification(row) if row is not None else None


def _device_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _claim_next(
    conn, *, worker_id: str, now_text: str, lease_until: str, excluded_ids: set[str],
):
    exclusions = "" if not excluded_ids else " AND notification_id NOT IN (" + ",".join("?" for _ in excluded_ids) + ")"
    row = conn.execute(
        "SELECT notification_id FROM k10_task_notifications WHERE "
        "(status='queued' OR (status='sending' AND lease_until < ?))" + exclusions +
        " ORDER BY created_at,notification_id LIMIT 1",
        (now_text, *sorted(excluded_ids)),
    ).fetchone()
    if row is None:
        return None
    notification_id = str(row[0])
    changed = conn.execute(
        "UPDATE k10_task_notifications SET status='sending',attempt_count=attempt_count+1,lease_owner=?,lease_until=?,updated_at=? "
        "WHERE notification_id=? AND (status='queued' OR (status='sending' AND lease_until < ?))",
        (worker_id, lease_until, now_text, notification_id, now_text),
    ).rowcount
    if changed != 1:
        return None
    return conn.execute(
        "SELECT notification_id,task_id,kind,terminal_status,task_attempt_count,title,body,deep_link_json,status,attempt_count,created_at "
        "FROM k10_task_notifications WHERE notification_id=?", (notification_id,)
    ).fetchone()


def _finish_delivery(
    *, db_path: Path, notification_id: str, worker_id: str, now_text: str,
    delivered: Sequence[str], transient_error: bool,
) -> None:
    with write_connection(db_path) as conn:
        _require_notifications_schema(conn)
        row = conn.execute(
            "SELECT status,lease_owner,lease_until FROM k10_task_notifications WHERE notification_id=?", (notification_id,)
        ).fetchone()
        if row is None or row[0] != "sending" or row[1] != worker_id or row[2] < now_text:
            raise NotificationConflict("通知租约已失效，拒绝覆盖另一 dispatcher 的状态")
        for key in delivered:
            conn.execute(
                "INSERT OR IGNORE INTO k10_notification_deliveries(notification_id,device_key,delivered_at) VALUES(?,?,?)",
                (notification_id, key, now_text),
            )
        status = "queued" if transient_error else "sent"
        conn.execute(
            "UPDATE k10_task_notifications SET status=?,last_error=?,lease_owner=NULL,lease_until=NULL,updated_at=? "
            "WHERE notification_id=?",
            (status, "delivery_retry_needed" if transient_error else None, now_text, notification_id),
        )


def dispatch_task_notifications(
    *, db_path: Path, list_device_tokens: DeviceTokens, delete_device: DeleteDevice,
    sender: NotificationSender, worker_id: str, now: datetime,
    lease_for: timedelta = timedelta(minutes=2), limit: int = 20,
    clock: Callable[[], datetime] | None = None,
) -> int:
    """Dispatch bounded queued notifications through injected common device/APNs seams.

    A process crash after APNs accepts a payload but before delivery persistence can replay the
    stable ``collapse_id``.  That is the recoverable APNs boundary; normal duplicate terminal
    hooks and ordinary retry paths never send an already recorded device again.
    """
    if not worker_id or limit < 1 or lease_for.total_seconds() <= 0:
        raise ValueError("worker_id、limit、lease_for 必须有效")
    dispatched = 0
    claimed_ids: set[str] = set()
    for _ in range(limit):
        current = clock() if clock is not None else now
        stamp = _utc_text(current)
        lease_until = _utc_text(current + lease_for)
        with write_connection(db_path) as conn:
            _require_notifications_schema(conn)
            row = _claim_next(
                conn, worker_id=worker_id, now_text=stamp, lease_until=lease_until, excluded_ids=claimed_ids,
            )
        if row is None:
            break
        notification = _notification(row)
        claimed_ids.add(notification.notification_id)
        current_tokens = tuple(token for token in list_device_tokens() if isinstance(token, str) and token)
        with read_connection(db_path) as conn:
            _require_notifications_schema(conn)
            delivered_rows = conn.execute(
                "SELECT device_key FROM k10_notification_deliveries WHERE notification_id=?", (notification.notification_id,)
            ).fetchall()
        delivered_already = {str(item[0]) for item in delivered_rows}
        delivered_now: list[str] = []
        seen_now: set[str] = set()
        transient_error = False
        for token in current_tokens:
            key = _device_key(token)
            if key in delivered_already or key in seen_now:
                continue
            seen_now.add(key)
            current = clock() if clock is not None else now
            with write_connection(db_path) as conn:
                owned = conn.execute(
                    "UPDATE k10_task_notifications SET lease_until=? WHERE notification_id=? "
                    "AND status='sending' AND lease_owner=? AND lease_until>=?",
                    (_utc_text(current + lease_for), notification.notification_id, worker_id, _utc_text(current)),
                ).rowcount
                if owned != 1:
                    raise NotificationConflict("通知租约已失效")
            title, body = _outbound_message(notification)
            try:
                result = sender(token=token, title=title, body=body, kind=notification.kind,
                                deep_link=_deep_link(notification.deep_link), collapse_id=notification.notification_id)
            except Exception:
                transient_error = True
                continue
            if result.ok:
                delivered_now.append(key)
                with write_connection(db_path) as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO k10_notification_deliveries(notification_id,device_key,delivered_at) "
                        "SELECT notification_id,?,? FROM k10_task_notifications WHERE notification_id=? "
                        "AND status='sending' AND lease_owner=? AND lease_until>=?",
                        (key, _utc_text(clock() if clock is not None else now), notification.notification_id,
                         worker_id, _utc_text(clock() if clock is not None else now)),
                    )
            elif result.permanent_invalid or result.reason in _PERMANENT_DEVICE_REASONS:
                delete_device(token)
            else:
                transient_error = True
        _finish_delivery(
            db_path=db_path, notification_id=notification.notification_id, worker_id=worker_id,
            now_text=_utc_text(clock() if clock is not None else now), delivered=delivered_now, transient_error=transient_error,
        )
        dispatched += 1
    return dispatched


__all__ = [
    "DeliveryResult", "NOTIFICATION_KINDS", "NOTIFICATION_SCHEMA_VERSION", "Notification", "NotificationConflict",
    "NotificationError", "NotificationSchemaUnavailable", "dispatch_task_notifications", "enqueue_task_notification",
    "get_notification", "initialize_notifications_schema", "on_task_terminal", "rollback_notifications_schema",
]
