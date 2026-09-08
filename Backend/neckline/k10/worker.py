"""Single-consumer K10 worker with durable leases and explicit task handlers.

Importing this module starts no worker and opens no database. The process entry
point supplies the database, handlers and operational timing explicitly.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from neckline.k10 import store
from neckline.k10.config import validate_execution_config
from neckline.k10.schema import read_connection, require_schema
from neckline.k10.types import Task

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskResult:
    status: str
    stage: str
    checkpoint: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    retry_at: datetime | None = None
    retry_kind: str | None = None
    safe_error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"completed", "failed", "not_configured", "cancelled"}:
            raise ValueError("TaskResult requires a terminal task status")
        if self.retry_at is None:
            if self.retry_kind is not None or self.safe_error_code is not None:
                raise ValueError("retry metadata requires retry_at")
        elif (self.retry_at.tzinfo is None or self.status != "failed" or
              self.retry_kind not in {"continuation", "failure"} or not self.safe_error_code):
            raise ValueError("deferred TaskResult requires failed status, timezone, retry kind and safe error code")


@dataclass(frozen=True)
class TaskContext:
    task: Task
    budget: Mapping[str, Any]
    checkpoint: Mapping[str, Any]
    input_version: str
    input_cutoff_at: str
    db_path: Path
    lease_lost: threading.Event
    assert_lease: Callable[[], None] | None = None
    execution_profile: Mapping[str, Any] | None = None
    failure_attempt_count: int = 0
    execution_started_at: datetime | None = None
    execution_deadline_at: datetime | None = None

    def require_lease(self) -> None:
        """Handlers call this before publishing artifacts or doing another call."""
        if self.lease_lost.is_set():
            raise store.K10Conflict("任务租约已失效，请等待恢复")
        if self.assert_lease is not None:
            self.assert_lease()


TaskHandler = Callable[[TaskContext], TaskResult]
_PAID_TASK_KINDS = frozenset({"evening_scan", "morning_scan", "analysis", "morning_review"})


def _research_snapshot_ids(checkpoint: Mapping[str, Any]) -> tuple[str, ...]:
    """Read the explicit B39 research links without guessing from task input."""
    values: list[Any] = []
    single = checkpoint.get("researchSnapshotId")
    if single is not None:
        values.append(single)
    multiple = checkpoint.get("researchSnapshotIds")
    if multiple is not None:
        if not isinstance(multiple, list):
            raise ValueError("研究快照引用无效")
        values.extend(multiple)
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("研究快照引用无效")
    if len(set(values)) != len(values):
        raise ValueError("研究快照引用重复")
    return tuple(values)


def _truthful_terminal_result(result: TaskResult, *, db_path: Path) -> TaskResult:
    """Do not let a completed task overwrite an explicitly failed B39 snapshot.

    A handler is responsible for emitting the durable snapshot link.  Once it
    does, this final worker fence treats a missing, paused, or failed snapshot
    as a terminal failure before task completion and notification dispatch.
    Older tasks without a research link remain readable historical work.
    """
    if result.status != "completed":
        return result
    required = result.checkpoint.get("researchRequired")
    if required is not None and not isinstance(required, bool):
        return TaskResult("failed", "research_state", result.checkpoint, "研究状态标记无效，比较未完成")
    try:
        snapshot_ids = _research_snapshot_ids(result.checkpoint)
    except ValueError:
        return TaskResult("failed", "research_state", result.checkpoint, "研究快照引用无效，比较未完成")
    if required and not snapshot_ids:
        return TaskResult("failed", "research_state", result.checkpoint, "研究快照缺失，比较未完成")
    if not snapshot_ids:
        return result
    try:
        from .research_store import read_research_snapshot
        snapshots = [read_research_snapshot(snapshot_id=item, db_path=db_path) for item in snapshot_ids]
    except Exception:
        return TaskResult("failed", "research_state", result.checkpoint, "研究状态不可读取，比较未完成")
    if any(snapshot is None or snapshot.execution_status != "ok" for snapshot in snapshots):
        return TaskResult("failed", "research_state", result.checkpoint, "研究执行失败，比较未完成")
    return result


def _v3_execution_ready(context: TaskContext) -> bool:
    profile = context.execution_profile
    payload = profile.get("payload") if isinstance(profile, Mapping) else None
    return (isinstance(payload, Mapping) and payload.get("executionVersion") == "k10-execution-v3"
            and validate_execution_config(payload).ready)


def _retry_maximum(context: TaskContext) -> int | None:
    """Read the explicit finite retry boundary without resurrecting a budget."""
    if context.task.kind in _PAID_TASK_KINDS:
        payload = context.execution_profile.get("payload") if isinstance(context.execution_profile, Mapping) else None
        discovery = payload.get("discovery") if isinstance(payload, Mapping) else None
        maximum = discovery.get("networkMaxAttempts") if isinstance(discovery, Mapping) else None
    else:
        maximum = context.budget.get("maxAttempts")
    return None if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1 else maximum


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _execution_deadline(*, profile: Mapping[str, Any], started_at: datetime,
                        runtime_repair: Mapping[str, Any] | None = None) -> datetime:
    payload = profile.get("payload")
    discovery = payload.get("discovery") if isinstance(payload, Mapping) else None
    seconds = discovery.get("completionDeadlineSeconds") if isinstance(discovery, Mapping) else None
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1:
        raise ValueError("执行配置缺少 completionDeadlineSeconds")
    if runtime_repair is not None:
        if runtime_repair.get("originalExecutionContentSha256") != profile.get("contentSha256"):
            raise ValueError("恢复时限的原始配置绑定不匹配")
        extension = runtime_repair.get("completionDeadlineSeconds", seconds)
        if isinstance(extension, bool) or not isinstance(extension, int) or extension < seconds:
            raise ValueError("恢复时限无效")
        seconds = extension
    return started_at + timedelta(seconds=seconds)


def _context(task: Task, db_path: Path, lease_lost: threading.Event, clock: Callable[[], datetime]) -> TaskContext:
    with read_connection(db_path) as connection:
        require_schema(connection)
        row = connection.execute(
            "SELECT t.budget_json,t.checkpoint_json,t.input_version,t.input_cutoff_at,"
            "b.execution_config_id,b.execution_config_revision,b.execution_content_sha256,b.binding_kind,c.payload_json,"
            "COALESCE(r.failure_attempt_count,0) FROM k10_tasks t "
            "LEFT JOIN k10_task_execution_bindings b ON b.task_id=t.task_id "
            "LEFT JOIN k10_execution_config_revisions c ON c.config_id=b.execution_config_id AND c.revision=b.execution_config_revision "
            "LEFT JOIN k10_task_retry_schedules r ON r.task_id=t.task_id WHERE t.task_id=?", (task.task_id,),
        ).fetchone()
    if row is None:
        raise store.K10Conflict("任务已不存在")
    def assert_lease() -> None:
        with read_connection(db_path) as connection:
            current = connection.execute(
                "SELECT status,lease_owner,lease_until FROM k10_tasks WHERE task_id=?", (task.task_id,),
            ).fetchone()
        now = clock().astimezone(timezone.utc).isoformat(timespec="seconds")
        if current is None or current[0] != "running" or current[1] != task.lease_owner or not current[2] or current[2] < now:
            lease_lost.set()
            raise store.K10Conflict("任务租约已失效，请等待恢复")
    profile = None if row[4] is None else {"configId": row[4], "revision": int(row[5]), "contentSha256": row[6],
                                            "bindingKind": row[7], "payload": json.loads(row[8])}
    return TaskContext(task, json.loads(row[0]), json.loads(row[1]), row[2], row[3], db_path, lease_lost,
                       assert_lease, profile, int(row[9]))


def run_once(
    *, db_path: Path, worker_id: str, lease_for: timedelta,
    handlers: Mapping[str, TaskHandler], clock: Callable[[], datetime] = _utc_now,
    task_id: str | None = None,
) -> Task | None:
    """Claim one job; retries are bounded and never invoke an unknown handler."""
    if lease_for.total_seconds() <= 0:
        raise ValueError("lease_for must be positive")
    # This gate precedes claim so a disabled timer/worker cannot turn a queued
    # historical B36 task into a running task which later reaches a provider.
    control = store.run_control_status(db_path=db_path)
    if control.get("state") != "open":
        return None
    if task_id is None:
        claimed = store.claim_tasks(
            worker_id=worker_id, now=clock(), lease_for=lease_for, limit=1, db_path=db_path,
        )
    else:
        selected = store.claim_task_by_id(
            task_id=task_id, worker_id=worker_id, now=clock(), lease_for=lease_for, db_path=db_path,
        )
        claimed = [selected] if selected is not None else []
    if not claimed:
        return None
    task = claimed[0]
    stopped, lease_lost = threading.Event(), threading.Event()
    context = _context(task, db_path, lease_lost, clock)

    def heartbeat() -> None:
        while not stopped.wait(lease_for.total_seconds() / 3):
            try:
                store.renew_task_lease(
                    task_id=task.task_id, worker_id=worker_id, now=clock(),
                    lease_for=lease_for, db_path=db_path,
                )
            except Exception:
                # A lost lease must not be converted into a successful result.
                lease_lost.set()
                logger.warning("K10 task lease renewal failed: %s", task.task_id)
                return

    heartbeat_thread = threading.Thread(target=heartbeat, name="k10-lease", daemon=True)
    heartbeat_thread.start()
    try:
        maximum = _retry_maximum(context)
        handler = handlers.get(task.kind)
        if task.kind in _PAID_TASK_KINDS and not _v3_execution_ready(context):
            result = TaskResult("not_configured", "configuration", context.checkpoint,
                                "K10 外部调用要求已批准的 V3 执行配置")
        elif maximum is None:
            result = TaskResult("not_configured", "configuration", error="任务有限重试参数未配置")
        elif context.failure_attempt_count >= maximum:
            result = TaskResult("failed", "attempt_limit", context.checkpoint, "任务已达到重试上限")
        elif handler is None:
            result = TaskResult("not_configured", "configuration", error="任务处理器尚未配置")
        elif store.run_control_status(db_path=db_path).get("state") != "open":
            # The task may have been claimed immediately before an operator
            # closed the durable switch.  Do not enter a handler (which may
            # still read sources before its provider-level admission gate).
            result = TaskResult("failed", "paused", context.checkpoint, "K10 运行已暂停")
        else:
            try:
                if context.execution_profile is not None:
                    started_text = store.ensure_task_execution_started(
                        task_id=task.task_id, worker_id=worker_id, started_at=clock(), db_path=db_path,
                    )
                    started_at = datetime.fromisoformat(started_text)
                    context = replace(context, execution_started_at=started_at,
                                      execution_deadline_at=_execution_deadline(profile=context.execution_profile,
                                                                               runtime_repair=context.checkpoint.get("runtimeRepair"),
                                                                               started_at=started_at))
                result = handler(context)
                if not isinstance(result, TaskResult):
                    raise TypeError("K10 handler must return TaskResult")
                result = _truthful_terminal_result(result, db_path=db_path)
            except store.K10Conflict:
                raise
            except Exception as exc:
                # Do not persist exception bodies, URLs or upstream headers;
                # they can contain provider credentials or untrusted content.
                logger.warning("K10 task failed: %s (%s)", task.task_id, type(exc).__name__)
                result = TaskResult("failed", "execution", context.checkpoint, "任务执行失败，可查看已完成资料并重试")
        context.require_lease()
        if result.retry_at is not None:
            if store.run_control_status(db_path=db_path).get("state") != "open":
                # A pause never discards already-settled handler output, but
                # it must prevent this task from arranging a future attempt.
                result = TaskResult("failed", "paused", result.checkpoint, "K10 运行已暂停")
            else:
                scheduled = store.schedule_task_retry(
                    task_id=task.task_id, worker_id=worker_id, stage=result.stage, checkpoint=result.checkpoint,
                    safe_error_code=str(result.safe_error_code), not_before_at=result.retry_at, scheduled_at=clock(),
                    retry_kind=str(result.retry_kind), max_failure_attempts=maximum, db_path=db_path,
                )
                if not scheduled:
                    if store.run_control_status(db_path=db_path).get("state") != "open":
                        result = TaskResult("failed", "paused", result.checkpoint, "K10 运行已暂停")
                    else:
                        result = TaskResult("failed", "attempt_limit", result.checkpoint, "任务已达到重试上限")
        if result.retry_at is None:
            store.finish_task(
                task_id=task.task_id, worker_id=worker_id, status=result.status, stage=result.stage,
                checkpoint=result.checkpoint, error_text=result.error, finished_at=clock(), db_path=db_path,
            )
    finally:
        stopped.set()
        heartbeat_thread.join()
    return store.get_task(task_id=task.task_id, db_path=db_path)


def run_worker(
    *, db_path: Path, worker_id: str, lease_for: timedelta, idle_seconds: float,
    handlers: Mapping[str, TaskHandler], stop: threading.Event,
    maintenance: Callable[[], None] | None = None,
) -> None:
    """Serial dispatch keeps the small service bounded; the caller handles signals."""
    if idle_seconds <= 0:
        raise ValueError("idle_seconds must be positive")
    if store.run_control_status(db_path=db_path).get("state") != "open":
        return
    while not stop.is_set():
        try:
            task = run_once(db_path=db_path, worker_id=worker_id, lease_for=lease_for, handlers=handlers)
        except store.K10Conflict:
            logger.warning("K10 worker lost ownership; leaving the task for recovery")
            task = None
        if maintenance is not None:
            try:
                maintenance()
            except Exception as exc:
                logger.warning("K10 notification maintenance pending (%s)", type(exc).__name__)
        if task is None:
            stop.wait(idle_seconds)


__all__ = ["TaskContext", "TaskHandler", "TaskResult", "run_once", "run_worker"]
