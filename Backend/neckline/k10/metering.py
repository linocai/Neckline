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
from typing import Callable, Iterator, Mapping
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
    clock: Callable[[], datetime] | None = None


_SPEND_CONTEXT: ContextVar[ProviderSpendContext | None] = ContextVar("k10_provider_spend", default=None)
def _safe_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

_SAFE_PROVIDER_FAILURES = frozenset({
    "provider_configuration", "provider_dependency", "provider_transport", "provider_http_error",
    "response_json_invalid", "response_structure_invalid", "response_truncated", "response_empty",
    "response_filtered", "provider_tool_limit",
    "insufficient_balance", "rate_limited",
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
        """Bind the immutable V3 task identity to every paid request.

        This is an attempt ledger, never a quota gate.  The binding still
        makes a missing context fail closed, and disables provider-internal
        retries so every HTTP attempt has a durable identity.
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
        clock: Callable[[], datetime] | None = None,
    ) -> Iterator[None]:
        """Bind a task/stage explicitly around exactly one provider request."""
        if not task_id or not stage or not item_key or isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("K10 provider spend context is incomplete")
        token = _SPEND_CONTEXT.set(ProviderSpendContext(task_id, stage, item_key, attempt, kind, full_text, clock))
        try:
            yield
        finally:
            _SPEND_CONTEXT.reset(token)

    @staticmethod
    def _request_input_sha256(args: tuple[object, ...], kwargs: Mapping[str, object]) -> str | None:
        """Fingerprint the bounded request without retaining its prompt text."""
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
            encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            return sha256(encoded).hexdigest()
        except (TypeError, ValueError):
            return None

    def _preflight_request(self, *, context: ProviderSpendContext, args: tuple[object, ...], kwargs: Mapping[str, object]) -> tuple[str | None, str | None]:
        """Validate protocol bounds, not a total token or spend allowance."""
        payload = self._spend_payload
        if not isinstance(payload, Mapping) or payload.get("executionVersion") != "k10-execution-v4":
            return None, "execution_not_configured"
        input_sha256 = self._request_input_sha256(args, kwargs)
        options = kwargs.get("model_options")
        output_bound = options.get("maxTokens") if isinstance(options, Mapping) else None
        if input_sha256 is None or isinstance(output_bound, bool) or not isinstance(output_bound, int) or output_bound < 1:
            return None, "execution_request_bound_missing"
        return input_sha256, None

    def _begin_attempt(self, *, args: tuple[object, ...], kwargs: Mapping[str, object]) -> tuple[str | None, str | None]:
        """Durably begin one external attempt before its socket can open."""
        context = _SPEND_CONTEXT.get()
        if self._spend_task_id is None:
            return None, None
        if context is None or context.task_id != self._spend_task_id:
            return None, "execution_spend_context_missing"
        input_sha256, preflight = self._preflight_request(context=context, args=args, kwargs=kwargs)
        if preflight is not None or input_sha256 is None:
            return None, preflight or "execution_request_bound_missing"
        identity = "\x1f".join((context.task_id, context.stage, context.item_key, str(context.attempt), context.kind))
        attempt_key = "provider:" + sha256(identity.encode("utf-8")).hexdigest()
        from . import store
        result = store.begin_external_attempt(
            task_id=context.task_id, item_key=context.item_key, stage=context.stage,
            attempt_key=attempt_key, input_sha256=input_sha256, started_at=_safe_now(), db_path=self._ledger_db,
        )
        if result.get("state") != "started":
            state = result.get("state")
            return None, {
                "paused": "execution_paused",
                "not_configured": "execution_not_configured",
                "pending_outcome": "provider_request_outcome_unknown",
                "retired": "execution_retired_by_user",
                "reused": "provider_attempt_reused",
            }.get(str(state), "provider_attempt_admission_failed")
        attempt_id = result.get("attemptId")
        return (str(attempt_id), None) if isinstance(attempt_id, str) else (None, "provider_attempt_admission_failed")

    def _settle_attempt(self, *, attempt_id: str, result: LLMResult | None) -> None:
        from . import store
        outcome = "unknown" if result is None else ("succeeded" if result.ok else "failed")
        actual = None
        error_code = None if result is None or result.ok else _safe_failure(result)
        if result is not None and not bool(getattr(result, "usage_unavailable", True)):
            values = (result.prompt_tokens, result.completion_tokens, result.total_tokens)
            if all(not isinstance(value, bool) and isinstance(value, int) and value >= 0 for value in values):
                actual = {
                    "promptTokens": int(values[0]), "completionTokens": int(values[1]), "totalTokens": int(values[2]),
                    "searchRequests": None, "searchCredits": None,
                }
        context = _SPEND_CONTEXT.get()
        scoped = context is not None and context.stage in {"analysisPro", "analysisCon", "morning"}
        settled_at = context.clock().isoformat() if scoped and context.clock is not None else _safe_now()
        store.settle_external_attempt(attempt_id=attempt_id, outcome=outcome, usage=actual,
                                      error_code=error_code, settled_at=settled_at, db_path=self._ledger_db,
                                      record_provider_failure=scoped,
                                      retry_after_seconds=getattr(result, "retry_after_seconds", None))

    def chat(self, *args, **kwargs):
        attempt_id, blocked = self._begin_attempt(args=args, kwargs=kwargs)
        if blocked is not None:
            return LLMResult(ok=False, reason="K10 execution spend is unavailable", provider=self.name, model=self.model,
                             error_code=blocked, usage_unavailable=True)
        started = monotonic()
        result = None
        try:
            result = super().chat(*args, **kwargs)
            return result
        finally:
            if attempt_id is not None:
                try:
                    self._settle_attempt(attempt_id=attempt_id, result=result)
                except Exception as exc:
                    # A request outcome is already paid.  Preserve the attempt
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
    clock: Callable[[], datetime] | None = None,
):
    """Return an explicit context manager; never leak task context to threads."""
    if isinstance(provider, MeteredProvider):
        return provider.spend_context(task_id=task_id, stage=stage, item_key=item_key, attempt=attempt,
                                      kind=kind, full_text=full_text, clock=clock)
    return nullcontext()


def execution_model_options(
    *, execution_profile: Mapping[str, object] | None, stage: str, option_stage: str,
) -> dict[str, object] | None:
    """Read an approved V3 single-request protocol boundary."""
    payload = execution_profile.get("payload") if isinstance(execution_profile, Mapping) else None
    discovery = payload.get("discovery") if isinstance(payload, Mapping) else None
    choices = discovery.get("modelOptions") if isinstance(discovery, Mapping) else None
    base = choices.get(option_stage) if isinstance(choices, Mapping) else None
    if not isinstance(payload, Mapping) or payload.get("executionVersion") != "k10-execution-v4" or not isinstance(base, Mapping):
        return None
    # Config validation owns the complete shape; copy only after this local
    # guard so a malformed frozen row cannot reach the wire client.
    maximum = base.get("maxTokens")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        return None
    return dict(base)


__all__ = ["MeteredProvider", "ProviderSpendContext", "bind_provider_execution_spending", "provider_spend_context",
           "execution_model_options"]
