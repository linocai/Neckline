"""Safe provider failures shared by the selected-company and morning workers."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Mapping

from .worker import TaskContext, TaskResult


def failure_message(code: str | None) -> str:
    return {
        "insufficient_balance": "模型账户余额不足，任务已停止",
        "rate_limited": "模型服务限流",
    }.get(code, "模型调用失败")


def provider_deadline_result(*, context: TaskContext,
                             checkpoint: Mapping[str, Any] | None = None) -> TaskResult | None:
    """Recheck at the paid step, including a retry claimed long after its due time."""
    if context.execution_deadline_at is None or context.clock() < context.execution_deadline_at:
        return None
    return TaskResult("failed", "deadline",
                      {**(checkpoint if checkpoint is not None else context.checkpoint),
                       "safeErrorCode": "completion_deadline_exceeded"},
                      "任务已超过完成时限，已完成内容保留")


def provider_failure_result(*, context: TaskContext, stage: str, code: str | None,
                            retry_after_seconds: float | None,
                            checkpoint: Mapping[str, Any] | None = None,
                            received_at: datetime | None = None) -> TaskResult:
    # Only protocol codes cross this boundary; never store upstream error text.
    code = code if code in {"insufficient_balance", "rate_limited"} else "provider_call_failed"
    checkpoint = {**(checkpoint if checkpoint is not None else context.checkpoint), "safeErrorCode": code}
    error = failure_message(code)
    if code != "rate_limited":
        return TaskResult("failed", stage, checkpoint, error)
    profile = context.execution_profile
    policy = profile["payload"]["discovery"] if profile is not None else None
    if policy is None:
        return TaskResult("not_configured", "configuration", checkpoint, "模型重试参数未配置")
    # These are the same explicit frozen bounds used by discovery and the worker.
    if context.failure_attempt_count + 1 >= policy["networkMaxAttempts"]:
        return TaskResult("failed", stage, checkpoint, error + "，已达到重试上限")
    delays = policy["retryBackoffSeconds"]
    delay = retry_after_seconds
    if isinstance(delay, bool) or not isinstance(delay, (float, int)) or not math.isfinite(delay) or delay < 0:
        delay = delays[min(context.failure_attempt_count, len(delays) - 1)]
    now = context.clock()
    origin = received_at if received_at is not None else now
    # Compare before adding to datetime: an untrusted Retry-After may be enormous.
    if context.execution_deadline_at is not None and (now >= context.execution_deadline_at or delay >= (context.execution_deadline_at - origin).total_seconds()):
        return TaskResult("failed", stage, checkpoint, error + "，无法在本次任务完成时限内重试")
    return TaskResult("failed", stage, checkpoint, error + "，等待延后重试",
                      retry_at=origin + timedelta(seconds=delay), retry_kind="failure", safe_error_code=code)


def recover_provider_failure(*, context: TaskContext,
                             checkpoint: Mapping[str, Any] | None = None) -> TaskResult | None:
    """Restore a settled failure after a crash before its task transition committed."""
    if context.task.kind not in {"analysis", "morning_review"}:
        return None
    from . import store
    saved = store.task_execution_input(task_id=context.task.task_id, db_path=context.db_path)
    receipt = saved["checkpoint"].get("providerFailureReceipt") if saved is not None else None
    if receipt is None:
        return None
    stages = {"analysisPro": "pro_failed", "analysisCon": "con_failed", "morning": "model"}
    return provider_failure_result(context=context, stage=stages[receipt["stage"]],
                                   code=receipt["errorCode"], retry_after_seconds=receipt["retryAfterSeconds"],
                                   received_at=datetime.fromisoformat(receipt["receivedAt"]),
                                   checkpoint=checkpoint if checkpoint is not None else saved["checkpoint"])
