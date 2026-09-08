"""Durable, bounded model-operation execution for one frozen K10 task.

The ledger stores only a domain-validated JSON derivative.  Provider responses,
prompts and exception messages are intentionally kept in process memory only.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from time import monotonic
from typing import Any, Callable, Literal, Mapping

from neckline.llm.base import LLMResult

from . import store
from .schema import require_schema, write_connection


_OPERATIONS = frozenset({
    "titleBatch", "titleReconcile", "understand", "verify", "map", "compare", "classify", "prioritize",
    "investigation_extract_claims", "investigation_plan_gaps", "investigation_plan_queries",
    "investigation_assess_evidence", "investigation_close_research", "investigation_compare_companies",
})
_JSON_CODES = frozenset({"json_invalid", "json_root_invalid", "response_json_invalid", "response_structure_invalid"})
_NETWORK_CODES = frozenset({
    "provider_configuration", "provider_dependency", "provider_transport", "provider_http_error",
    "response_empty", "response_filtered", "provider_tool_limit",
})
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_UNSAFE_RESULT_KEYS = frozenset({"prompt", "rawResponse", "raw_response", "originalText", "original_text"})


class ModelOperationError(RuntimeError):
    """Safe model-operation signal.  ``code`` is the only durable diagnostic."""

    def __init__(self, *, code: str, input_tokens: int | None = None,
                 output_tokens: int | None = None, total_tokens: int | None = None) -> None:
        super().__init__(code)
        self.code = _safe_code(code, fallback="model_execution_invalid")
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens


class ModelNetworkError(ModelOperationError):
    pass


class JsonRepairError(ModelOperationError):
    pass


class SemanticValidationError(ModelOperationError):
    pass


@dataclass(frozen=True)
class ModelInvocation:
    """A parsed callback result with provider-reported usage, never raw output."""

    value: Any
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ModelOperationResult:
    """A safe result for discovery orchestration; ``value`` is normalized JSON only."""

    status: Literal["completed", "failed"]
    value: Mapping[str, Any] | list[Any] | None
    reused: bool
    safe_error_code: str | None
    attempt_count: int
    network_attempt_count: int
    repair_attempt_count: int
    elapsed_ms: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True)
class _Reservation:
    state: Literal["reserved", "completed", "failed"]
    attempt_count: int
    network_attempt_count: int
    repair_attempt_count: int
    elapsed_ms: int
    input_tokens: int | None
    output_tokens: int | None
    safe_error_code: str | None = None
    value: Mapping[str, Any] | list[Any] | None = None


def _safe_code(value: object, *, fallback: str) -> str:
    return value if isinstance(value, str) and _SAFE_CODE.fullmatch(value) else fallback


def _operation_key(*, operation: str, item_key: str, input_sha256: str) -> str:
    # The primary key has no input hash column.  Include it in the opaque item
    # key so a genuinely changed frozen input is a distinct ledger item rather
    # than an illegal rewrite of a completed result.
    digest = sha256(f"{operation}\x1f{item_key}\x1f{input_sha256}".encode("utf-8")).hexdigest()
    return f"model:{operation}:{digest}"


def _item_kind(operation: str) -> str:
    if operation == "understand":
        return "document"
    if operation == "titleBatch":
        return "global"
    return "global" if operation in {"titleReconcile", "prioritize"} else "event"


def _stage(operation: str) -> str:
    return f"model:{operation}"


def _policy_limits(policy: Mapping[str, Any]) -> tuple[int, int]:
    if not isinstance(policy, Mapping):
        raise ValueError("模型执行必须传入显式执行策略")
    network, repairs = policy.get("networkMaxAttempts"), policy.get("jsonRepairMaxAttempts")
    if isinstance(network, bool) or not isinstance(network, int) or network < 1:
        raise ValueError("模型执行策略缺少 networkMaxAttempts")
    if isinstance(repairs, bool) or not isinstance(repairs, int) or repairs < 0:
        raise ValueError("模型执行策略缺少 jsonRepairMaxAttempts")
    return network, repairs


def _normal_json(value: Any) -> Mapping[str, Any] | list[Any]:
    """Reject non-JSON and persistence-prohibited fields before completion."""
    def visit(item: Any) -> Any:
        if isinstance(item, Mapping):
            keys = {str(key) for key in item}
            if keys & _UNSAFE_RESULT_KEYS:
                raise SemanticValidationError(code="model_result_unsafe")
            return {str(key): visit(value) for key, value in item.items()}
        if isinstance(item, list):
            return [visit(entry) for entry in item]
        if item is None or isinstance(item, (str, int, float, bool)):
            return item
        raise SemanticValidationError(code="model_result_not_json")

    normalized = visit(value)
    if not isinstance(normalized, (dict, list)):
        raise SemanticValidationError(code="model_result_root_invalid")
    return normalized


def _safe_provider_failure(result: LLMResult) -> ModelNetworkError:
    code = _safe_code(result.error_code, fallback="model_network_failed")
    return ModelNetworkError(code=code, input_tokens=result.prompt_tokens,
                             output_tokens=result.completion_tokens, total_tokens=result.total_tokens)


def _parse_invocation(value: Any) -> tuple[Any, int | None, int | None, int | None]:
    if isinstance(value, ModelInvocation):
        return value.value, value.input_tokens, value.output_tokens, value.total_tokens
    if isinstance(value, LLMResult):
        if not value.ok:
            raise _safe_provider_failure(value)
        try:
            parsed = json.loads(value.content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise JsonRepairError(code="model_json_invalid", input_tokens=value.prompt_tokens,
                                  output_tokens=value.completion_tokens, total_tokens=value.total_tokens) from exc
        if not isinstance(parsed, (Mapping, list)):
            raise JsonRepairError(code="model_json_root_invalid", input_tokens=value.prompt_tokens,
                                  output_tokens=value.completion_tokens, total_tokens=value.total_tokens)
        return parsed, value.prompt_tokens, value.completion_tokens, value.total_tokens
    return value, None, None, None


def _classify_error(exc: BaseException) -> ModelOperationError:
    if isinstance(exc, ModelOperationError):
        return exc
    if isinstance(exc, (json.JSONDecodeError, TypeError)):
        return JsonRepairError(code="model_json_invalid")
    code = getattr(exc, "code", None)
    safe = _safe_code(code, fallback="model_execution_invalid")
    if safe in _JSON_CODES or "json" in safe:
        return JsonRepairError(code=safe)
    if safe in _NETWORK_CODES or safe.startswith("provider_http_") or safe.startswith("provider_"):
        return ModelNetworkError(code=safe)
    return SemanticValidationError(code=safe)


def _aggregate_usage(*, prior_network: int, prior_input: int | None, prior_output: int | None,
                     current_input: int | None, current_output: int | None) -> tuple[int | None, int | None]:
    def merge(previous: int | None, current: int | None) -> int | None:
        if previous is not None and (isinstance(previous, bool) or not isinstance(previous, int) or previous < 0):
            previous = None
        if current is not None and (isinstance(current, bool) or not isinstance(current, int) or current < 0):
            current = None
        # Once an actual earlier call did not report usage, aggregate usage is
        # unavailable.  Never fill that gap with an estimate or partial total.
        if prior_network > 0 and previous is None:
            return None
        if current is None:
            return None
        return current if prior_network == 0 else int(previous) + current
    return merge(prior_input, current_input), merge(prior_output, current_output)


def _read_result(raw: object) -> Mapping[str, Any] | list[Any] | None:
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, (dict, list)) else None


def _reserve(
    *, task_id: str, operation: str, item_key: str, input_sha256: str, network_limit: int,
    repair_limit: int, db_path, leaseguard: Callable[[], None] | None, updated_at: str,
) -> _Reservation:
    if leaseguard is not None:
        leaseguard()
    ledger_key, stage, kind = _operation_key(operation=operation, item_key=item_key, input_sha256=input_sha256), _stage(operation), _item_kind(operation)
    with write_connection(db_path) as conn:
        require_schema(conn)
        # This must remain a read-only callback.  With BEGIN IMMEDIATE held, a
        # later claim cannot land between this proof and the reservation write.
        if leaseguard is not None:
            leaseguard()
        if conn.execute("SELECT 1 FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone() is None:
            raise store.K10Conflict("模型执行任务不存在")
        if conn.execute("SELECT 1 FROM k10_task_execution_bindings WHERE task_id=?", (task_id,)).fetchone() is None:
            raise store.K10Conflict("模型执行缺少显式执行配置绑定")
        row = conn.execute(
            "SELECT status,attempt_count,network_attempt_count,repair_attempt_count,elapsed_ms,input_tokens,output_tokens,"
            "result_json,safe_error_code FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND item_kind=? AND item_key=? AND stage=?",
            (task_id, kind, ledger_key, stage),
        ).fetchone()
        if row is not None and row[0] == "completed":
            cached = _read_result(row[7])
            if cached is None:
                return _Reservation("failed", int(row[1]), int(row[2]), int(row[3]), int(row[4]), row[5], row[6],
                                    "model_cache_corrupt")
            return _Reservation("completed", int(row[1]), int(row[2]), int(row[3]), int(row[4]), row[5], row[6], value=cached)
        if row is not None and row[0] == "running":
            # A process may have died after the upstream accepted the request.
            # The reservation remains charged, but a later slice may use one
            # of the *remaining* explicit attempts.  This avoids both a free
            # retry and a permanent wedge after an interrupted call.
            prior_attempts, prior_network, prior_repairs = int(row[1]), int(row[2]), int(row[3])
            if prior_network >= network_limit:
                return _Reservation("failed", prior_attempts, prior_network, prior_repairs, int(row[4]), row[5], row[6],
                                    "model_request_outcome_unknown")
            attempt_count, network_count = prior_attempts + 1, prior_network + 1
            conn.execute(
                "UPDATE k10_execution_item_checkpoints SET attempt_count=?,network_attempt_count=?,"
                "safe_error_code=NULL,safe_error_ref=NULL,updated_at=? "
                "WHERE task_id=? AND item_kind=? AND item_key=? AND stage=?",
                (attempt_count, network_count, updated_at, task_id, kind, ledger_key, stage),
            )
            return _Reservation("reserved", attempt_count, network_count, prior_repairs, int(row[4]), row[5], row[6])
        prior_attempts = int(row[1]) if row is not None else 0
        prior_network = int(row[2]) if row is not None else 0
        prior_repairs = int(row[3]) if row is not None else 0
        prior_elapsed = int(row[4]) if row is not None else 0
        prior_input, prior_output = (row[5], row[6]) if row is not None else (None, None)
        previous_code = str(row[8]) if row is not None and isinstance(row[8], str) else None
        is_json_retry = previous_code is not None and (previous_code in _JSON_CODES or "json" in previous_code)
        if is_json_retry and (prior_repairs >= repair_limit or prior_network >= network_limit):
            return _Reservation("failed", prior_attempts, prior_network, prior_repairs, prior_elapsed, prior_input, prior_output,
                                "model_json_repair_exhausted")
        if prior_network >= network_limit:
            return _Reservation("failed", prior_attempts, prior_network, prior_repairs, prior_elapsed, prior_input, prior_output,
                                "model_network_attempts_exhausted")
        is_network_retry = (previous_code == "model_network_failed" or previous_code in _NETWORK_CODES or
                            (previous_code is not None and (previous_code.startswith("provider_http_") or previous_code.startswith("provider_"))))
        if row is not None and previous_code is not None and not is_json_retry and not is_network_retry:
            return _Reservation("failed", prior_attempts, prior_network, prior_repairs, prior_elapsed, prior_input, prior_output,
                                previous_code)
        if is_json_retry and prior_repairs >= repair_limit:
            return _Reservation("failed", prior_attempts, prior_network, prior_repairs, prior_elapsed, prior_input, prior_output,
                                "model_json_repair_exhausted")
        attempt_count, network_count = prior_attempts + 1, prior_network + 1
        repair_count = prior_repairs + (1 if is_json_retry else 0)
        if row is None:
            conn.execute(
                "INSERT INTO k10_execution_item_checkpoints(task_id,item_kind,item_key,stage,input_sha256,status,"
                "attempt_count,network_attempt_count,repair_attempt_count,elapsed_ms,input_tokens,output_tokens,"
                "result_json,safe_error_code,safe_error_ref,updated_at) VALUES(?,?,?,?,?,'running',?,?,?,?,?,?,NULL,NULL,NULL,?)",
                (task_id, kind, ledger_key, stage, input_sha256, attempt_count, network_count, repair_count,
                 prior_elapsed, prior_input, prior_output, updated_at),
            )
        else:
            conn.execute(
                "UPDATE k10_execution_item_checkpoints SET status='running',attempt_count=?,network_attempt_count=?,"
                "repair_attempt_count=?,safe_error_code=NULL,safe_error_ref=NULL,updated_at=? "
                "WHERE task_id=? AND item_kind=? AND item_key=? AND stage=?",
                (attempt_count, network_count, repair_count, updated_at, task_id, kind, ledger_key, stage),
            )
        return _Reservation("reserved", attempt_count, network_count, repair_count, prior_elapsed, prior_input, prior_output)


def _persist(
    *, task_id: str, operation: str, item_key: str, input_sha256: str, status: str, reservation: _Reservation,
    elapsed_ms: int, input_tokens: int | None, output_tokens: int | None, value: Mapping[str, Any] | list[Any] | None,
    safe_error_code: str | None, db_path, leaseguard: Callable[[], None] | None, updated_at: str,
) -> None:
    store.record_execution_checkpoint(
        task_id=task_id, item_kind=_item_kind(operation), item_key=_operation_key(operation=operation, item_key=item_key, input_sha256=input_sha256),
        stage=_stage(operation), input_sha256=input_sha256, status=status,
        attempt_count=reservation.attempt_count, network_attempt_count=reservation.network_attempt_count,
        repair_attempt_count=reservation.repair_attempt_count, elapsed_ms=elapsed_ms,
        input_tokens=input_tokens, output_tokens=output_tokens, result=value,
        safe_error_code=safe_error_code, safe_error_ref=item_key if safe_error_code else None,
        updated_at=updated_at, db_path=db_path, leaseguard=leaseguard,
    )


def execute_model_operation(
    *, task_id: str, operation: str, item_key: str, input_sha256: str, policy: Mapping[str, Any],
    operation_call: Callable[[], Any], validate: Callable[[Any], Mapping[str, Any] | list[Any]],
    db_path, leaseguard: Callable[[], None] | None = None, repair_call: Callable[[], Any] | None = None,
    spend_context_factory: Callable[[int, bool], Any] | None = None,
    now: Callable[[], datetime] | None = None, monotonic_clock: Callable[[], float] = monotonic,
) -> ModelOperationResult:
    """Execute at most one provider call and durably account for it first.

    ``operation_call`` and optional ``repair_call`` must make *one* provider
    request each.  ``validate`` performs all K10 domain checks and returns the
    normalized JSON value; a syntactically valid provider object is never
    cached before this succeeds.
    """
    if operation not in _OPERATIONS or not item_key or not isinstance(input_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", input_sha256):
        raise ValueError("模型执行需要受支持 operation、itemKey 与 SHA-256 输入指纹")
    if (not callable(operation_call) or not callable(validate) or (repair_call is not None and not callable(repair_call))
            or (spend_context_factory is not None and not callable(spend_context_factory))):
        raise ValueError("模型执行回调无效")
    network_limit, repair_limit = _policy_limits(policy)
    timestamp = (now or (lambda: datetime.now(timezone.utc)))()
    if timestamp.tzinfo is None:
        raise ValueError("模型执行时间必须带时区")
    updated_at = timestamp.astimezone(timezone.utc).isoformat(timespec="seconds")
    reservation = _reserve(task_id=task_id, operation=operation, item_key=item_key, input_sha256=input_sha256,
                           network_limit=network_limit, repair_limit=repair_limit, db_path=db_path,
                           leaseguard=leaseguard, updated_at=updated_at)
    if reservation.state == "completed":
        return ModelOperationResult("completed", reservation.value, True, None, reservation.attempt_count,
                                    reservation.network_attempt_count, reservation.repair_attempt_count,
                                    reservation.elapsed_ms, reservation.input_tokens, reservation.output_tokens,
                                    None if reservation.input_tokens is None or reservation.output_tokens is None
                                    else reservation.input_tokens + reservation.output_tokens)
    if reservation.state == "failed":
        return ModelOperationResult("failed", None, False, reservation.safe_error_code, reservation.attempt_count,
                                    reservation.network_attempt_count, reservation.repair_attempt_count,
                                    reservation.elapsed_ms, reservation.input_tokens, reservation.output_tokens,
                                    None if reservation.input_tokens is None or reservation.output_tokens is None
                                    else reservation.input_tokens + reservation.output_tokens)

    started = monotonic_clock()
    provider_input = provider_output = provider_total = None
    try:
        is_repair = reservation.repair_attempt_count > 0 and repair_call is not None
        call = repair_call if is_repair else operation_call
        manager = (spend_context_factory(reservation.network_attempt_count, is_repair)
                   if spend_context_factory is not None else nullcontext())
        with manager:
            candidate, provider_input, provider_output, provider_total = _parse_invocation(call())
        normalized = _normal_json(validate(candidate))
    except Exception as exc:
        failure = _classify_error(exc)
        if provider_input is None:
            provider_input = failure.input_tokens
        if provider_output is None:
            provider_output = failure.output_tokens
        if provider_total is None:
            provider_total = failure.total_tokens
        elapsed = reservation.elapsed_ms + max(0, round((monotonic_clock() - started) * 1000))
        inputs, outputs = _aggregate_usage(prior_network=reservation.network_attempt_count - 1,
                                           prior_input=reservation.input_tokens, prior_output=reservation.output_tokens,
                                           current_input=provider_input, current_output=provider_output)
        _persist(task_id=task_id, operation=operation, item_key=item_key, input_sha256=input_sha256, status="failed",
                 reservation=reservation, elapsed_ms=elapsed, input_tokens=inputs, output_tokens=outputs, value=None,
                 safe_error_code=failure.code, db_path=db_path, leaseguard=leaseguard, updated_at=updated_at)
        return ModelOperationResult("failed", None, False, failure.code, reservation.attempt_count,
                                    reservation.network_attempt_count, reservation.repair_attempt_count, elapsed,
                                    inputs, outputs, provider_total)

    elapsed = reservation.elapsed_ms + max(0, round((monotonic_clock() - started) * 1000))
    inputs, outputs = _aggregate_usage(prior_network=reservation.network_attempt_count - 1,
                                       prior_input=reservation.input_tokens, prior_output=reservation.output_tokens,
                                       current_input=provider_input, current_output=provider_output)
    _persist(task_id=task_id, operation=operation, item_key=item_key, input_sha256=input_sha256, status="completed",
             reservation=reservation, elapsed_ms=elapsed, input_tokens=inputs, output_tokens=outputs, value=normalized,
             safe_error_code=None, db_path=db_path, leaseguard=leaseguard, updated_at=updated_at)
    return ModelOperationResult("completed", normalized, False, None, reservation.attempt_count,
                                reservation.network_attempt_count, reservation.repair_attempt_count, elapsed,
                                inputs, outputs, provider_total)


__all__ = [
    "JsonRepairError", "ModelInvocation", "ModelNetworkError", "ModelOperationError", "ModelOperationResult",
    "SemanticValidationError", "execute_model_operation",
]
