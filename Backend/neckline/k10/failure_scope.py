"""Known external-content failures, distinct from protected runtime state."""

MODEL_CONTENT_FAILURE_CODES = frozenset({
    "content_policy_refused", "json_invalid", "json_root_invalid", "model_json_invalid",
    "model_json_root_invalid", "model_result_not_json", "model_result_root_invalid",
    "model_result_unsafe", "response_json_invalid", "response_structure_invalid", "model_json_repair_exhausted",
    "execution_context_exceeded", "model_network_attempts_exhausted", "response_empty",
    "response_truncated", "insufficient_balance", "provider_authorization_failed",
    "prioritize_json_contract_invalid",
    # Receipt-only replay preserves the original stage error without spending
    # a fresh JSON repair. Its content scope must match the fresh-call path.
    "titlebatch_json_output_truncated", "titlereconcile_json_output_truncated",
    "understand_json_output_truncated", "prioritize_json_output_truncated",
    "investigation_json_output_truncated",
})


def local_model_failure_code(exc: BaseException) -> str | None:
    code = getattr(exc, "code", None)
    return code if code in MODEL_CONTENT_FAILURE_CODES else None
