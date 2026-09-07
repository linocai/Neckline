"""Single-consumer K10 worker with durable leases and explicit task handlers.

Importing this module starts no worker and opens no database. The process entry
point supplies the database, handlers and operational timing explicitly.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from neckline.k10 import store
from neckline.k10.schema import read_connection, require_schema
from neckline.k10.types import Task

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskResult:
    status: str
    stage: str
    checkpoint: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"completed", "failed", "not_configured", "cancelled"}:
            raise ValueError("TaskResult requires a terminal task status")


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

    def require_lease(self) -> None:
        """Handlers call this before publishing artifacts or doing another call."""
        if self.lease_lost.is_set():
            raise store.K10Conflict("任务租约已失效，请等待恢复")
        if self.assert_lease is not None:
            self.assert_lease()


TaskHandler = Callable[[TaskContext], TaskResult]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _context(task: Task, db_path: Path, lease_lost: threading.Event, clock: Callable[[], datetime]) -> TaskContext:
    with read_connection(db_path) as connection:
        require_schema(connection)
        row = connection.execute(
            "SELECT budget_json,checkpoint_json,input_version,input_cutoff_at "
            "FROM k10_tasks WHERE task_id=?", (task.task_id,),
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
    return TaskContext(task, json.loads(row[0]), json.loads(row[1]), row[2], row[3], db_path, lease_lost, assert_lease)


def run_once(
    *, db_path: Path, worker_id: str, lease_for: timedelta,
    handlers: Mapping[str, TaskHandler], clock: Callable[[], datetime] = _utc_now,
    task_id: str | None = None,
) -> Task | None:
    """Claim one job; retries never invent a budget or invoke an unknown handler."""
    if lease_for.total_seconds() <= 0:
        raise ValueError("lease_for must be positive")
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
        maximum = context.budget.get("maxAttempts")
        handler = handlers.get(task.kind)
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
            result = TaskResult("not_configured", "configuration", error="任务重试上限未配置")
        elif task.attempt_count > maximum:
            result = TaskResult("failed", "attempt_limit", context.checkpoint, "任务已达到重试上限")
        elif handler is None:
            result = TaskResult("not_configured", "configuration", error="任务处理器尚未配置")
        else:
            try:
                result = handler(context)
                if not isinstance(result, TaskResult):
                    raise TypeError("K10 handler must return TaskResult")
            except store.K10Conflict:
                raise
            except Exception as exc:
                # Do not persist exception bodies, URLs or upstream headers;
                # they can contain provider credentials or untrusted content.
                logger.warning("K10 task failed: %s (%s)", task.task_id, type(exc).__name__)
                result = TaskResult("failed", "execution", context.checkpoint, "任务执行失败，可查看已完成资料并重试")
        context.require_lease()
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
