"""Actual provider usage accounting; no estimates, prices, keys or prompt text."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import logging
import json
import os
from pathlib import Path
from time import monotonic
from threading import local
from typing import Callable, Iterator, Mapping
from zoneinfo import ZoneInfo

from neckline.llm.openai_compat import OpenAICompatProvider, _actual_usage
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
    "provider_request_outcome_unknown",
    "response_json_invalid", "response_structure_invalid", "response_truncated", "response_empty",
    "response_filtered", "provider_tool_limit",
    "insufficient_balance", "rate_limited",
})

# DeepSeek's 2026-09-14 public capability table maps the frozen official
# ``deepseek-flash`` route to V4.1-Flash (1M context, 384K output).  A model
# name by itself is insufficient: a proxy or a different vendor can give that
# name materially different limits.
_DEEPSEEK_CHAT_COMPLETIONS = "https://api.deepseek.com/chat/completions"
_MODEL_CAPABILITIES = {
    (_DEEPSEEK_CHAT_COMPLETIONS, "deepseek-flash"): {
        "contextTokens": 1_000_000, "maxOutputTokens": 384_000,
        "counter": "utf8_wire_bytes_upper_v2",
    },
}
_RECEIPT_CONTRACT_VERSION = "k10-model-response-receipt-v1"

# DeepSeek's published V4.1 prompt renderer at deepseek-recipe commit
# 8cadfede7063c896b944e7bae05daa3549ae97ea.  The accompanying inspected
# tokenizer has an empty normalizer and ByteLevel+BPE input path: for the
# supported text-only shape below its token count cannot exceed rendered UTF-8
# bytes.  This is a conservative admission bound, never accounting usage or a
# claim about token-cost savings.  Evidence hashes are retained in the B69
# release record's official-encoding sources.
_V41_BOS = "<｜begin▁of▁sentence｜>"
_V41_SYSTEM = "<｜System｜>"
_V41_USER = "<｜User｜>"
_V41_ASSISTANT = "<｜Assistant｜>"
_V41_THINK_START = "<think>"
_V41_THINK_END = "</think>"
_V41_EOS = "<｜end▁of▁sentence｜>"
_V41_JSON_FORMAT = "\n\n## Response Format:\n\nYou MUST strictly adhere to the following schema to reply:\n{'type': 'json_object'}"


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
        self._thread_usage = local()
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

    def _wire_request(self, args: tuple[object, ...], kwargs: Mapping[str, object]) -> dict[str, object] | None:
        """Return the exact first HTTP body plus the actual endpoint identity."""
        messages = args[0] if args else kwargs.get("messages")
        if not isinstance(messages, list):
            return None
        try:
            payload = self.initial_wire_payload(
                messages,
                enable_search=bool(kwargs.get("enable_search", True)),
                search_query=kwargs.get("search_query"),
                response_format=kwargs.get("response_format"),
                model_options=kwargs.get("model_options"),
                json_array_key=kwargs.get("json_array_key"),
            )
            return {"endpoint": self.api_url.rstrip("/"), "body": payload}
        except (TypeError, ValueError):
            return None

    def _request_input_sha256(self, args: tuple[object, ...], kwargs: Mapping[str, object]) -> str | None:
        body = self._wire_request(args, kwargs)
        if body is None:
            return None
        try:
            return sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            return None

    def _reuse_scope_sha256(
        self, *, context: ProviderSpendContext, args: tuple[object, ...], kwargs: Mapping[str, object],
        request_sha256: str | None,
    ) -> str | None:
        """Record the producing caller's parser contract and stage for audit.

        The request digest names the external wire and is the only receipt
        lookup identity.  This scope stays immutable on the source receipt so
        callers can audit which parser first consumed it; a later parser or
        checkpoint revision revalidates the saved raw response locally rather
        than treating the old semantic result as completed or reposting it.
        """
        if request_sha256 is None:
            return None
        scope = {
            "receiptContract": _RECEIPT_CONTRACT_VERSION,
            "requestSha256": request_sha256,
            "stage": context.stage,
            "jsonArrayKey": kwargs.get("json_array_key"),
        }
        try:
            return sha256(json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            return None

    def _capability(self) -> Mapping[str, object] | None:
        return _MODEL_CAPABILITIES.get((self.api_url.rstrip("/"), self.model.casefold()))

    def _context_upper_bound(self, args: tuple[object, ...], kwargs: Mapping[str, object]) -> tuple[int | None, str | None]:
        capability = self._capability()
        if capability is None:
            # A name or proxy endpoint without an inspected renderer and limit
            # contract cannot borrow DeepSeek Flash capacity.  Spending fails
            # closed; deterministic tests must bind an explicit supported
            # endpoint/model pair instead of relying on a bypass.
            return None, "execution_model_capability_missing"
        body = self._wire_request(args, kwargs)
        if body is None or not isinstance(body.get("body"), Mapping):
            return None, "execution_request_bound_missing"
        return self._deepseek_v41_text_prompt_upper_bound(body["body"])

    @staticmethod
    def _deepseek_v41_text_prompt_upper_bound(body: Mapping[str, object]) -> tuple[int | None, str | None]:
        """Render the official V4.1 text-only framing and return a safe bound.

        K10 never uses provider-native tools, images or tool-call continuations.
        Those features have separately rendered prompt blocks, so admitting one
        here without its own renderer would be unsafe and is rejected before a
        socket is opened.
        """
        raw_messages = body.get("messages")
        if not isinstance(raw_messages, list) or body.get("tools"):
            return None, "execution_prompt_shape_unsupported"
        response_format = body.get("response_format")
        if response_format not in (None, {"type": "json_object"}):
            return None, "execution_prompt_shape_unsupported"
        thinking = body.get("thinking")
        if thinking is None:
            thinking_mode = False
        elif thinking == {"type": "enabled"}:
            thinking_mode = True
        elif thinking == {"type": "disabled"}:
            thinking_mode = False
        else:
            return None, "execution_prompt_shape_unsupported"
        effort = body.get("reasoning_effort")
        if effort not in (None, "low", "high", "max"):
            return None, "execution_prompt_shape_unsupported"
        # The recipe maps absent/high to 75, low to 50 and max to 100.
        effort_score = {"low": 50, "max": 100}.get(effort, 75)
        messages: list[tuple[str, str]] = []
        for raw in raw_messages:
            if (not isinstance(raw, Mapping) or raw.get("role") not in {"system", "user", "assistant"}
                    or not isinstance(raw.get("content"), str) or raw.get("tool_calls") is not None):
                return None, "execution_prompt_shape_unsupported"
            messages.append((str(raw["role"]), str(raw["content"])))
        if not messages:
            return None, "execution_prompt_shape_unsupported"
        # ``render_conversation`` inserts an empty system message before the
        # fixed JSON instruction when no system message exists.
        if response_format == {"type": "json_object"}:
            if messages[0][0] != "system":
                messages.insert(0, ("system", ""))
            role, content = messages[0]
            messages[0] = (role, content + _V41_JSON_FORMAT)
        rendered = _V41_BOS
        for index, (role, content) in enumerate(messages):
            previous = messages[index - 1][0] if index else None
            reasoning = (f"Reasoning Effort: {effort_score} (range 1-100, the higher the value, the more thorough the reasoning)\n\n"
                         if index == 0 and thinking_mode else "")
            if index == 0 and (reasoning or role == "system"):
                rendered += _V41_SYSTEM
            rendered += reasoning
            if role == "system":
                if index:
                    rendered += _V41_SYSTEM
                rendered += content
            elif role == "user":
                rendered += "\n\n" if previous == "user" else _V41_USER
                rendered += content
            else:  # assistant text, with no tool or hidden-reasoning content
                rendered += _V41_ASSISTANT + (_V41_THINK_START + _V41_THINK_END if thinking_mode and index else _V41_THINK_END)
                rendered += content + _V41_EOS
        rendered += _V41_ASSISTANT + (_V41_THINK_START if thinking_mode else _V41_THINK_END)
        return len(rendered.encode("utf-8")), None

    def _preflight_request(self, *, context: ProviderSpendContext, args: tuple[object, ...], kwargs: Mapping[str, object]) -> tuple[tuple[str, str] | None, str | None]:
        """Validate protocol bounds, not a total token or spend allowance."""
        payload = self._spend_payload
        if not isinstance(payload, Mapping) or payload.get("executionVersion") != "k10-execution-v4":
            return None, "execution_not_configured"
        input_sha256 = self._request_input_sha256(args, kwargs)
        reuse_scope_sha256 = self._reuse_scope_sha256(context=context, args=args, kwargs=kwargs, request_sha256=input_sha256)
        options = kwargs.get("model_options")
        output_bound = options.get("maxTokens") if isinstance(options, Mapping) else None
        if (input_sha256 is None or reuse_scope_sha256 is None or isinstance(output_bound, bool)
                or not isinstance(output_bound, int) or output_bound < 1):
            return None, "execution_request_bound_missing"
        capability = self._capability()
        input_upper, context_error = self._context_upper_bound(args, kwargs)
        if context_error is not None:
            return None, context_error or "execution_model_capability_missing"
        if input_upper is None:
            return None, "execution_request_bound_missing"
        if output_bound > int(capability["maxOutputTokens"]):
            return None, "execution_output_bound_exceeded"
        if input_upper + output_bound > int(capability["contextTokens"]):
            return None, "execution_context_exceeded"
        return (input_sha256, reuse_scope_sha256), None

    def request_context_error(self, messages, **kwargs) -> str | None:
        """Read-only capacity check for constructing a source-material request.

        This uses the same endpoint-bound V4.1 renderer and effective wire
        options as the paid call, but does not reserve an attempt or perform a
        network operation.  Callers use it to choose a complete body versus a
        structural index; it is never a quota/spend calculation.
        """
        args = (messages,)
        capability = self._capability()
        if capability is None:
            return "execution_model_capability_missing"
        input_upper, error = self._context_upper_bound(args, kwargs)
        options = kwargs.get("model_options")
        output_bound = options.get("maxTokens") if isinstance(options, Mapping) else None
        if error is not None:
            return error
        if (input_upper is None or isinstance(output_bound, bool) or not isinstance(output_bound, int)
                or output_bound < 1):
            return "execution_request_bound_missing"
        if output_bound > int(capability["maxOutputTokens"]):
            return "execution_output_bound_exceeded"
        if input_upper + output_bound > int(capability["contextTokens"]):
            return "execution_context_exceeded"
        return None

    def _begin_attempt(
        self, *, args: tuple[object, ...], kwargs: Mapping[str, object],
    ) -> tuple[str | None, str | None, Mapping[str, object] | None]:
        """Durably reserve one wire request or recover its exact committed response."""
        context = _SPEND_CONTEXT.get()
        if self._spend_task_id is None:
            return None, None, None
        if context is None or context.task_id != self._spend_task_id:
            return None, "execution_spend_context_missing", None
        hashes, preflight = self._preflight_request(context=context, args=args, kwargs=kwargs)
        if preflight is not None or hashes is None:
            return None, preflight or "execution_request_bound_missing", None
        input_sha256, reuse_scope_sha256 = hashes
        identity = "\x1f".join((context.task_id, context.stage, context.item_key, str(context.attempt), context.kind))
        attempt_key = "provider:" + sha256(identity.encode("utf-8")).hexdigest()
        from . import store
        # Wire and response-contract identities are deliberately distinct:
        # parsing changes can consume the private raw reply locally without
        # pretending an older caller checkpoint has completed.
        result = store.begin_model_external_attempt(
            task_id=context.task_id, item_key=context.item_key, stage=context.stage,
            attempt_key=attempt_key, input_sha256=input_sha256, reuse_scope_sha256=reuse_scope_sha256,
            started_at=_safe_now(), db_path=self._ledger_db,
        )
        if result.get("state") == "receipt_reused":
            receipt = result.get("receipt")
            receipt_attempt_id = result.get("receiptAttemptId")
            if isinstance(receipt, Mapping) and isinstance(receipt_attempt_id, str) and receipt_attempt_id:
                # Private process metadata only: retain the paid source
                # attempt for stage/checkpoint audit without altering the
                # immutable persisted receipt payload.
                return None, None, {**receipt, "_receiptAttemptId": receipt_attempt_id}
            return None, "provider_response_receipt_invalid", None
        if result.get("state") != "started":
            state = result.get("state")
            return None, {
                "paused": "execution_paused",
                "not_configured": "execution_not_configured",
                "pending_outcome": "provider_request_outcome_unknown",
                "receipt_unreplayable": "provider_response_receipt_unreplayable",
                "retired": "execution_retired_by_user",
                "terminal": "insufficient_balance",
                "reused": "provider_attempt_reused",
            }.get(str(state), "provider_attempt_admission_failed"), None
        attempt_id = result.get("attemptId")
        return ((str(attempt_id), None, None) if isinstance(attempt_id, str)
                else (None, "provider_attempt_admission_failed", None))

    @staticmethod
    def _receipt_payload(result: LLMResult) -> dict[str, object]:
        return {
            "receiptVersion": _RECEIPT_CONTRACT_VERSION, "ok": bool(result.ok), "content": result.content,
            "provider": result.provider or "", "model": result.model or "", "promptTokens": result.prompt_tokens,
            "completionTokens": result.completion_tokens, "totalTokens": result.total_tokens,
            "usageUnavailable": bool(result.usage_unavailable), "errorCode": result.error_code,
            "retryAfterSeconds": result.retry_after_seconds, "finishReason": result.finish_reason,
            # Keep only the provider's already-received JSON body for exact local
            # revalidation; prompts, headers and credentials are never present.
            "rawResponses": [dict(item) for item in result.raw_responses if isinstance(item, Mapping)],
            "responseReceived": bool(result.ok or result.raw_responses),
            # This immutable first receipt is committed immediately after an
            # HTTP JSON body arrives.  Reuse locally reapplies the same JSON
            # protocol checks before returning it to the caller.
            "rawReceiptOnly": bool(getattr(result, "raw_receipt_only", False)),
        }

    def _result_from_receipt(self, payload: Mapping[str, object], *, kwargs: Mapping[str, object]) -> LLMResult:
        if payload.get("rawReceiptOnly") is True:
            raw = payload.get("rawResponses")
            if not isinstance(raw, list):
                raise ValueError("模型响应回执无法恢复")
            result = self.revalidate_received_response(
                [dict(item) for item in raw if isinstance(item, Mapping)],
                enable_search=bool(kwargs.get("enable_search", True)),
                response_format=kwargs.get("response_format") if isinstance(kwargs.get("response_format"), Mapping) else None,
                json_array_key=kwargs.get("json_array_key") if isinstance(kwargs.get("json_array_key"), str) else None,
            )
            result.local_reuse = True
            return result
        try:
            result = LLMResult(
                ok=bool(payload["ok"]), content=str(payload["content"]), provider=str(payload["provider"]),
                model=str(payload["model"]), prompt_tokens=payload["promptTokens"],
                completion_tokens=payload["completionTokens"], total_tokens=payload["totalTokens"],
                usage_unavailable=bool(payload["usageUnavailable"]), error_code=payload["errorCode"],
                retry_after_seconds=payload["retryAfterSeconds"], finish_reason=payload["finishReason"],
                raw_responses=[dict(item) for item in payload["rawResponses"] if isinstance(item, Mapping)],
                reason="ok" if bool(payload["ok"]) else "K10 provider call failed",
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("模型响应回执无法恢复") from exc
        # This flag is process-local and deliberately never persisted or exposed.
        result.local_reuse = True
        return result

    def _received_provider_body(self, body: Mapping[str, object]) -> None:
        """Commit raw provider JSON before protocol/domain parsing can run."""
        active = getattr(self._thread_usage, "active_response_receipt", None)
        if (not isinstance(active, dict) or active.get("settled") or active.get("settlementFailed")
                or active.get("rawReceiptStarted")):
            return
        attempt_id = active.get("attemptId")
        request_sha256 = active.get("requestSha256")
        reuse_scope_sha256 = active.get("reuseScopeSha256")
        if not all(isinstance(value, str) and value for value in (attempt_id, request_sha256, reuse_scope_sha256)):
            return
        active["rawReceiptStarted"] = True
        raw_result = LLMResult(
            # The provider has delivered a complete HTTP JSON body.  Its
            # caller's JSON/domain validation may still fail, but that cannot
            # rewrite this immutable paid-response receipt into a different
            # external outcome.
            ok=True, content="", reason="provider response awaiting local protocol validation",
            provider=self.name, model=self.model,
            raw_responses=[dict(body)], **_actual_usage([dict(body)]),
        )
        # ``raw_receipt_only`` is persisted so a later caller replays the raw
        # response through OpenAICompat's pure local protocol parser.  It is
        # never returned to the original caller.
        raw_result.raw_receipt_only = True
        active["providerResult"] = raw_result
        try:
            self._settle_attempt(
                attempt_id=attempt_id,
                request_sha256=request_sha256,
                reuse_scope_sha256=reuse_scope_sha256,
                result=raw_result,
            )
        except Exception as exc:  # noqa: BLE001 - retain started/unknown, never repost
            logger.warning("K10 raw response receipt settlement pending (%s)", type(exc).__name__)
            active["settlementFailed"] = True
            return
        active["settled"] = True
        active["rawReceiptOnly"] = True

    def _provider_request_outcome_unknown(self) -> None:
        """Keep a dispatched request reserved when transport delivery is uncertain.

        Read/write failures after ``client.post`` starts are not evidence that
        DeepSeek did not receive or bill the request.  ``begin_model_external_attempt``
        recognizes this un-settled row on a later checkpoint or process and
        rejects a blind repeat of the same wire.
        """
        active = getattr(self._thread_usage, "active_response_receipt", None)
        if isinstance(active, dict) and active.get("attemptId"):
            active["providerOutcomeUnknown"] = True

    def _received_http_refusal(self, *, status: int, body: str) -> None:
        # A definite refusal is not a paid model reply. Keep its diagnostic
        # separate from immutable response-reuse semantics and public results.
        active = getattr(self._thread_usage, "active_response_receipt", None)
        if not isinstance(active, dict) or not active.get("attemptId"):
            return
        try:
            folder = Path(self._ledger_db).parent / "provider-diagnostics" / sha256(self._spend_task_id.encode()).hexdigest()
            folder.parent.mkdir(mode=0o700, exist_ok=True)
            folder.mkdir(mode=0o700, exist_ok=True)
            value = {"taskId": self._spend_task_id, "attemptId": active["attemptId"],
                     "requestSha256": active["requestSha256"], "statusCode": status,
                     "responseBody": body.replace(self.api_key, "[redacted]") if self.api_key else body}
            content = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()
            target = folder / (sha256(content).hexdigest() + ".json")
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass
        except Exception as exc:
            # Failure to save diagnostics must not turn a received HTTP
            # refusal into a transport exception and trigger another POST.
            logger.warning("Provider refusal diagnostics unavailable (%s)", type(exc).__name__)

    def _settle_attempt(self, *, attempt_id: str, request_sha256: str, reuse_scope_sha256: str, result: LLMResult | None) -> None:
        from . import store
        if result is None:
            # No deterministic provider outcome exists.  Leave the started row
            # unmodified so another checkpoint cannot blindly resend it.
            return
        outcome = "succeeded" if result.ok else "failed"
        actual = None
        error_code = None if result.ok else _safe_failure(result)
        if not bool(getattr(result, "usage_unavailable", True)):
            values = (result.prompt_tokens, result.completion_tokens, result.total_tokens)
            if all(not isinstance(value, bool) and isinstance(value, int) and value >= 0 for value in values):
                actual = {
                    "promptTokens": int(values[0]), "completionTokens": int(values[1]), "totalTokens": int(values[2]),
                    "searchRequests": None, "searchCredits": None,
                }
        context = _SPEND_CONTEXT.get()
        scoped = context is not None and context.stage in {"analysisPro", "analysisCon", "morning"}
        settled_at = context.clock().isoformat() if scoped and context.clock is not None else _safe_now()
        store.settle_model_response_attempt(
            attempt_id=attempt_id, request_sha256=request_sha256, reuse_scope_sha256=reuse_scope_sha256,
            payload=self._receipt_payload(result), outcome=outcome, usage=actual, error_code=error_code,
            settled_at=settled_at, db_path=self._ledger_db, record_provider_failure=scoped,
            retry_after_seconds=getattr(result, "retry_after_seconds", None),
        )

    def _finalize_provider_result(self, result: LLMResult) -> LLMResult:
        """Settle non-body outcomes and expose a parsed received body.

        Bodies were already made durable by ``_received_provider_body`` before
        any protocol parser reads them.  This later hook records an ordinary
        no-body provider failure and keeps the parsed result for the original
        caller and usage ledger without mutating the immutable raw receipt.
        """
        active = getattr(self._thread_usage, "active_response_receipt", None)
        if not isinstance(active, dict) or not result.raw_responses:
            return result
        # The raw body hook has already committed the immutable response.  The
        # parsed result below is for this caller and the auxiliary usage
        # ledger; it must not replace or double-settle that receipt.
        if active.get("rawReceiptOnly"):
            active["providerResult"] = result
            return result
        if active.get("settlementFailed"):
            active["providerResult"] = result
            return LLMResult(
                ok=False, reason="K10 response persistence failed", provider=self.name, model=self.model,
                error_code="provider_response_persist_failed", usage_unavailable=True,
            )
        if active.get("settled"):
            return result
        attempt_id = active.get("attemptId")
        request_sha256 = active.get("requestSha256")
        reuse_scope_sha256 = active.get("reuseScopeSha256")
        if not all(isinstance(value, str) and value for value in (attempt_id, request_sha256, reuse_scope_sha256)):
            return result
        active["providerResult"] = result
        try:
            self._settle_attempt(
                attempt_id=attempt_id,
                request_sha256=request_sha256,
                reuse_scope_sha256=reuse_scope_sha256,
                result=result,
            )
        except Exception as exc:  # noqa: BLE001 - preserve unknown outcome; never issue a retry
            logger.warning("K10 response receipt settlement pending (%s)", type(exc).__name__)
            active["settlementFailed"] = True
            return LLMResult(
                ok=False, reason="K10 response persistence failed", provider=self.name, model=self.model,
                error_code="provider_response_persist_failed", usage_unavailable=True,
            )
        active["settled"] = True
        return result

    def chat(self, *args, **kwargs):
        # DeepSeek's extension is not part of the Chat Completions protocol.
        effective_kwargs = dict(kwargs)
        if not self.model.lower().startswith("deepseek") and isinstance(effective_kwargs.get("model_options"), Mapping):
            effective_kwargs["model_options"] = {key: value for key, value in effective_kwargs["model_options"].items()
                                       if key not in {"thinking", "reasoningEffort"}}
        request_sha256 = self._request_input_sha256(args, effective_kwargs)
        context = _SPEND_CONTEXT.get()
        reuse_scope_sha256 = (self._reuse_scope_sha256(context=context, args=args, kwargs=effective_kwargs,
                                                        request_sha256=request_sha256)
                              if context is not None else None)
        attempt_id, blocked, receipt = self._begin_attempt(args=args, kwargs=effective_kwargs)
        self._thread_usage.last_external_attempt_id = attempt_id
        self._thread_usage.last_model_receipt_attempt_id = None
        if receipt is not None:
            receipt_attempt_id = receipt.get("_receiptAttemptId") if isinstance(receipt, Mapping) else None
            try:
                result = self._result_from_receipt(receipt, kwargs=effective_kwargs)
            except ValueError:
                return LLMResult(ok=False, reason="K10 response receipt is invalid", provider=self.name, model=self.model,
                                 error_code="provider_response_receipt_invalid", usage_unavailable=True)
            if isinstance(receipt_attempt_id, str) and receipt_attempt_id:
                result.reused_attempt_id = receipt_attempt_id
                self._thread_usage.last_model_receipt_attempt_id = receipt_attempt_id
            # It is a local read of an already-settled provider result: neither
            # external-attempt usage nor the auxiliary usage ledger is written again.
            return result
        if blocked is not None:
            return LLMResult(ok=False, reason="K10 execution spend is unavailable", provider=self.name, model=self.model,
                             error_code=blocked, usage_unavailable=True)
        started = monotonic()
        result: LLMResult | None = None
        returned: LLMResult | None = None
        self._thread_usage.active_response_receipt = {
            "attemptId": attempt_id,
            "requestSha256": request_sha256,
            "reuseScopeSha256": reuse_scope_sha256,
            "settled": False,
        }
        try:
            result = super().chat(*args, **effective_kwargs)
            returned = result
            active = self._thread_usage.active_response_receipt
            if (attempt_id is not None and not active.get("settled") and not active.get("settlementFailed")
                    and not active.get("providerOutcomeUnknown")):
                if request_sha256 is None:
                    raise ValueError("缺少已派发模型请求指纹")
                if reuse_scope_sha256 is None:
                    raise ValueError("缺少已派发模型调用语义范围")
                self._settle_attempt(attempt_id=attempt_id, request_sha256=request_sha256,
                                     reuse_scope_sha256=reuse_scope_sha256, result=result)
        except Exception as exc:
            if result is not None and attempt_id is not None:
                # The provider may already have billed the request.  A failed local
                # commit remains a started/unknown admission and is never retried.
                logger.warning("K10 response receipt settlement pending (%s)", type(exc).__name__)
                returned = LLMResult(ok=False, reason="K10 response persistence failed", provider=self.name, model=self.model,
                                     error_code="provider_response_persist_failed", usage_unavailable=True)
            else:
                raise
        finally:
            if receipt is None:
                try:
                    active = getattr(self._thread_usage, "active_response_receipt", {})
                    usage_result = active.get("providerResult") if isinstance(active, dict) else None
                    ledger_result = usage_result if isinstance(usage_result, LLMResult) else result
                    usage.record(
                        task=self._ledger_task, result=ledger_result,
                        report_date=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
                        duration_ms=max(0, round((monotonic() - started) * 1000)),
                        failure_reason=_safe_failure(ledger_result), db_path=self._ledger_db,
                    )
                except Exception as exc:
                    # A completed analysis still carries the provider's actual usage;
                    # a ledger fault must not make the next step call upstream again.
                    logger.warning("K10 usage ledger unavailable (%s)", type(exc).__name__)
            self._thread_usage.active_response_receipt = None
        if returned is None:
            # A transport exception without a provider result remains an unknown
            # external outcome; preserve the original exception behaviour.
            raise RuntimeError("provider call returned no result")
        return returned


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
