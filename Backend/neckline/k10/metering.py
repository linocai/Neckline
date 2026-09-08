"""Actual provider usage accounting; no estimates, prices, keys or prompt text."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import logging
import json
from pathlib import Path
from time import monotonic
from typing import Iterator, Mapping
from zoneinfo import ZoneInfo

from neckline.llm.openai_compat import OpenAICompatProvider
from neckline.llm.base import LLMResult
from neckline.llm import usage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderSpendContext:
    """The explicit identity of one K10 provider HTTP attempt.

    Context variables intentionally do not cross worker threads.  Discovery's
    concurrent document work must bind this object around every call instead
    of relying on an inherited task-global value.
    """

    task_id: str
    stage: str
    item_key: str
    attempt: int
    kind: str = "model"
    full_text: bool = False


_SPEND_CONTEXT: ContextVar[ProviderSpendContext | None] = ContextVar("k10_provider_spend", default=None)
_SPEND_FIELDS = (
    "calls", "inputTokens", "outputTokens", "totalTokens",
    "fullTextCalls", "retries", "searchRequests", "searchCredits",
)


def _zero_spend() -> dict[str, int]:
    return {field: 0 for field in _SPEND_FIELDS}


def _safe_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

_SAFE_PROVIDER_FAILURES = frozenset({
    "provider_configuration", "provider_dependency", "provider_transport", "provider_http_error",
    "response_json_invalid", "response_structure_invalid", "response_truncated", "response_empty",
    "response_filtered", "provider_tool_limit",
})


def _safe_failure(result) -> str | None:
    if result is not None and result.ok:
        return None
    code = getattr(result, "error_code", None)
    if isinstance(code, str) and (code in _SAFE_PROVIDER_FAILURES or
        (code.startswith("provider_http_") and len(code) == 17 and code[-3:].isdigit())):
        return code
    return "provider_call_failed"


class MeteredProvider(OpenAICompatProvider):
    def __init__(self, *, ledger_db: Path, ledger_task: str, **kwargs):
        super().__init__(**kwargs)
        self._ledger_db, self._ledger_task = ledger_db, ledger_task
        self._spend_task_id: str | None = None
        self._spend_payload: Mapping[str, object] | None = None

    def bind_execution_spending(self, *, task_id: str, execution_profile: Mapping[str, object] | None) -> None:
        """Turn on V3.0.5's fail-closed per-attempt admission for one task.

        B36 profiles cannot be treated as a weaker paid-call fallback.  K10
        deliberately uses one HTTP attempt per ``chat`` call; model-operation
        retry creates a fresh, separately reserved call.
        """
        payload = execution_profile.get("payload") if isinstance(execution_profile, Mapping) else None
        self._spend_task_id = task_id
        self._spend_payload = payload if isinstance(payload, Mapping) else None
        self.max_attempts = 1
        self.max_tool_rounds = 1

    @contextmanager
    def spend_context(
        self, *, task_id: str, stage: str, item_key: str, attempt: int,
        kind: str = "model", full_text: bool = False,
    ) -> Iterator[None]:
        """Bind a task/stage explicitly around exactly one provider request."""
        if not task_id or not stage or not item_key or isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("K10 provider spend context is incomplete")
        token = _SPEND_CONTEXT.set(ProviderSpendContext(task_id, stage, item_key, attempt, kind, full_text))
        try:
            yield
        finally:
            _SPEND_CONTEXT.reset(token)

    def _reservation(self, context: ProviderSpendContext) -> dict[str, int] | None:
        payload = self._spend_payload
        if not isinstance(payload, Mapping) or payload.get("executionVersion") != "k10-execution-v2":
            return None
        discovery = payload.get("discovery")
        budgets = discovery.get("budgets") if isinstance(discovery, Mapping) else None
        reservation = budgets.get("reservation") if isinstance(budgets, Mapping) else None
        limit = reservation.get(context.stage) if isinstance(reservation, Mapping) else None
        if not isinstance(limit, Mapping):
            return None
        keys = {
            "calls": "maxModelCalls", "inputTokens": "maxInputTokens", "outputTokens": "maxOutputTokens",
            "totalTokens": "maxTotalTokens", "fullTextCalls": "maxFullTextCalls", "retries": "maxRetries",
            "searchRequests": "maxSearchRequests", "searchCredits": "maxSearchCredits",
        }
        if any(isinstance(limit.get(key), bool) or not isinstance(limit.get(key), int) or limit[key] < 0 for key in keys.values()):
            return None
        reserved = _zero_spend()
        reserved["calls"] = 1
        reserved["inputTokens"] = int(limit["maxInputTokens"])
        reserved["outputTokens"] = int(limit["maxOutputTokens"])
        reserved["totalTokens"] = int(limit["maxTotalTokens"])
        if context.full_text:
            reserved["fullTextCalls"] = 1
        if context.attempt > 1:
            reserved["retries"] = 1
        return reserved

    @staticmethod
    def _request_input_bound(args: tuple[object, ...], kwargs: Mapping[str, object]) -> int | None:
        """Conservative, deterministic upper bound without retaining prompt text.

        UTF-8 bytes dominate the token count for the OpenAI-compatible JSON
        payload used here.  The fixed 512-byte transport allowance covers the
        wire envelope and request fields which are not represented by a chat
        message.  A missing/unsupported message shape is not guessed.
        """
        messages = args[0] if args else kwargs.get("messages")
        if not isinstance(messages, list):
            return None
        rendered: list[object] = []
        try:
            for message in messages:
                to_api = getattr(message, "to_api", None)
                rendered.append(to_api() if callable(to_api) else message)
            body = {"model": "deepseek-v4-pro", "messages": rendered,
                    "response_format": kwargs.get("response_format"),
                    "model_options": kwargs.get("model_options")}
            return len(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")) + 512
        except (TypeError, ValueError):
            return None

    def _preflight_budget(self, *, context: ProviderSpendContext, args: tuple[object, ...], kwargs: Mapping[str, object]) -> str | None:
        """Reject incompatible prompt/output bounds before admission or HTTP."""
        payload = self._spend_payload
        discovery = payload.get("discovery") if isinstance(payload, Mapping) else None
        budgets = discovery.get("budgets") if isinstance(discovery, Mapping) else None
        reservation = budgets.get("reservation") if isinstance(budgets, Mapping) else None
        limit = reservation.get(context.stage) if isinstance(reservation, Mapping) else None
        if not isinstance(limit, Mapping):
            return "execution_not_configured"
        input_bound = self._request_input_bound(args, kwargs)
        options = kwargs.get("model_options")
        output_bound = options.get("maxTokens") if isinstance(options, Mapping) else None
        if (input_bound is None or isinstance(output_bound, bool) or not isinstance(output_bound, int) or output_bound < 1):
            return "execution_request_bound_missing"
        input_limit, output_limit, total_limit = (limit.get("maxInputTokens"), limit.get("maxOutputTokens"), limit.get("maxTotalTokens"))
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (input_limit, output_limit, total_limit)):
            return "execution_not_configured"
        if input_bound > input_limit or output_bound > output_limit or input_bound + output_bound > total_limit:
            return "pending_budget"
        if context.full_text and limit.get("maxFullTextCalls") == 0:
            return "pending_budget"
        if context.attempt > 1 and limit.get("maxRetries") == 0:
            return "pending_budget"
        return None

    def _reserve_attempt(self, *, args: tuple[object, ...], kwargs: Mapping[str, object]) -> tuple[str | None, str | None]:
        """Reserve before the socket can be opened; never synthesize a budget."""
        context = _SPEND_CONTEXT.get()
        if self._spend_task_id is None:
            return None, None
        if context is None or context.task_id != self._spend_task_id:
            return None, "execution_spend_context_missing"
        preflight = self._preflight_budget(context=context, args=args, kwargs=kwargs)
        if preflight is not None:
            return None, preflight
        reserved = self._reservation(context)
        if reserved is None:
            return None, "execution_not_configured"
        # This key names one model-operation attempt.  No task-wide implicit
        # counter is used, so parallel document threads cannot share it.
        identity = "\x1f".join((context.task_id, context.stage, context.item_key, str(context.attempt), context.kind))
        reservation_key = "provider:" + sha256(identity.encode("utf-8")).hexdigest()
        from . import store
        result = store.admit_execution_spend(
            task_id=context.task_id, item_key=context.item_key, stage=context.stage, kind=context.kind,
            reservation_key=reservation_key, reserved=reserved, created_at=_safe_now(), db_path=self._ledger_db,
        )
        if result.get("state") != "reserved":
            state = result.get("state")
            return None, {
                "paused": "execution_paused",
                "not_configured": "execution_not_configured",
                "pending_budget": "pending_budget",
                "pending_outcome": "provider_request_outcome_unknown",
                "reused": "provider_reservation_reused",
            }.get(str(state), "provider_budget_admission_failed")
        reservation_id = result.get("reservationId")
        return (str(reservation_id), None) if isinstance(reservation_id, str) else (None, "provider_budget_admission_failed")

    def _settle_attempt(self, *, reservation_id: str, result: LLMResult | None) -> None:
        from . import store
        if result is None or bool(getattr(result, "usage_unavailable", True)):
            store.settle_execution_spend(reservation_id=reservation_id, outcome="unknown", actual=None,
                                          settled_at=_safe_now(), db_path=self._ledger_db)
            return
        values = (result.prompt_tokens, result.completion_tokens, result.total_tokens)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            store.settle_execution_spend(reservation_id=reservation_id, outcome="unknown", actual=None,
                                          settled_at=_safe_now(), db_path=self._ledger_db)
            return
        context = _SPEND_CONTEXT.get()
        actual = _zero_spend()
        actual.update({"calls": 1, "inputTokens": int(values[0]), "outputTokens": int(values[1]), "totalTokens": int(values[2])})
        if context is not None and context.full_text:
            actual["fullTextCalls"] = 1
        if context is not None and context.attempt > 1:
            actual["retries"] = 1
        store.settle_execution_spend(reservation_id=reservation_id, outcome="settled", actual=actual,
                                      settled_at=_safe_now(), db_path=self._ledger_db)

    def chat(self, *args, **kwargs):
        reservation_id, blocked = self._reserve_attempt(args=args, kwargs=kwargs)
        if blocked is not None:
            return LLMResult(ok=False, reason="K10 execution spend is unavailable", provider=self.name, model=self.model,
                             error_code=blocked, usage_unavailable=True)
        started = monotonic()
        result = None
        try:
            result = super().chat(*args, **kwargs)
            return result
        finally:
            if reservation_id is not None:
                try:
                    self._settle_attempt(reservation_id=reservation_id, result=result)
                except Exception as exc:
                    # A request outcome is already paid.  Preserve the reservation
                    # if settlement itself cannot be trusted; do not retry upstream.
                    logger.warning("K10 spend settlement pending (%s)", type(exc).__name__)
            try:
                usage.record(
                    task=self._ledger_task, result=result,
                    report_date=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
                    duration_ms=max(0, round((monotonic() - started) * 1000)),
                    failure_reason=_safe_failure(result),
                    db_path=self._ledger_db,
                )
            except Exception as exc:
                # Completed analysis still carries the provider's actual usage;
                # a ledger fault must not cause a second billable model call.
                logger.warning("K10 usage ledger unavailable (%s)", type(exc).__name__)


def bind_provider_execution_spending(*, provider: object, task_id: str,
                                    execution_profile: Mapping[str, object] | None) -> None:
    """Enable fail-closed spending only on the concrete K10 provider.

    Test doubles deliberately remain ordinary deterministic transports.  In
    production ``resolve_deepseek_v4_pro`` returns ``MeteredProvider``.
    """
    if isinstance(provider, MeteredProvider):
        provider.bind_execution_spending(task_id=task_id, execution_profile=execution_profile)


def provider_spend_context(
    *, provider: object, task_id: str, stage: str, item_key: str, attempt: int,
    kind: str = "model", full_text: bool = False,
):
    """Return an explicit context manager; never leak task context to threads."""
    if isinstance(provider, MeteredProvider):
        return provider.spend_context(task_id=task_id, stage=stage, item_key=item_key, attempt=attempt,
                                      kind=kind, full_text=full_text)
    return nullcontext()


def execution_model_options(
    *, execution_profile: Mapping[str, object] | None, stage: str, option_stage: str,
) -> dict[str, object] | None:
    """Read an approved V2 output cap; this never supplies a fallback value."""
    payload = execution_profile.get("payload") if isinstance(execution_profile, Mapping) else None
    discovery = payload.get("discovery") if isinstance(payload, Mapping) else None
    budgets = discovery.get("budgets") if isinstance(discovery, Mapping) else None
    reservation = budgets.get("reservation") if isinstance(budgets, Mapping) else None
    stage_budget = reservation.get(stage) if isinstance(reservation, Mapping) else None
    choices = discovery.get("modelOptions") if isinstance(discovery, Mapping) else None
    base = choices.get(option_stage) if isinstance(choices, Mapping) else None
    maximum = stage_budget.get("maxOutputTokens") if isinstance(stage_budget, Mapping) else None
    if (not isinstance(base, Mapping) or isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1):
        return None
    # Config validation owns the complete shape; copy only after this local
    # guard so a malformed frozen row cannot reach the wire client.
    result = dict(base)
    result["maxTokens"] = maximum
    return result


__all__ = ["MeteredProvider", "ProviderSpendContext", "bind_provider_execution_spending", "provider_spend_context",
           "execution_model_options"]
