from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from neckline.k10 import store
from neckline.k10.metering import MeteredProvider, bind_provider_execution_spending, provider_spend_context
from neckline.k10.schema import initialize_schema
from neckline.llm.base import LLMResult
from neckline.llm.base import ChatMessage
from neckline.llm.openai_compat import OpenAICompatProvider


NOW = datetime(2026, 9, 8, 2, tzinfo=timezone.utc)
_STAGES = ("lightweight", "fullText", "verify", "map", "companyComparison", "classify", "prioritize",
           "morning", "analysisPro", "analysisCon", "search", "retry")
_BUDGET = {
    "maxModelCalls": 9, "maxInputTokens": 1000, "maxOutputTokens": 500, "maxTotalTokens": 1500,
    "maxFullTextCalls": 9, "maxRetries": 9, "maxSearchRequests": 9, "maxSearchCredits": 9,
}


def _profile(rules: list[dict], content_hash: str) -> dict:
    return {
        "executionVersion": "k10-execution-v2",
        "discovery": {
            "model": "deepseek-v4-pro",
            "screeningTemplate": {"templateId": "fixture-template", "revision": 1,
                                  "contentSha256": content_hash, "approvalState": "approved", "rules": rules},
            "packageLimits": {"maxDocuments": 2, "maxKeyPassageCharacters": 100, "fullTextEnabled": True},
            "budgets": {"round": dict(_BUDGET), "stages": {stage: dict(_BUDGET) for stage in _STAGES},
                        "reservation": {stage: dict(_BUDGET) for stage in _STAGES}},
            "priorityOrder": ["publishedMajorContrary", "changedKnownFact", "newEvent"],
            "documentBatchSize": 2, "understandConcurrency": 1, "keyPassageMaxCharacters": 100,
            "networkMaxAttempts": 2, "jsonRepairMaxAttempts": 1, "retryBackoffSeconds": [1, 2],
            "taskSliceSeconds": 10, "completionDeadlineSeconds": 60, "continuationDelaySeconds": 1,
            "modelOptions": {
                "understand": {"maxTokens": 100, "thinking": {"type": "disabled"}},
                "verify": {"maxTokens": 100, "thinking": {"type": "disabled"}},
                "companyComparison": {"maxTokens": 100, "thinking": {"type": "disabled"}},
                "prioritize": {"maxTokens": 100, "thinking": {"type": "disabled"}},
            },
        },
    }


def _seed(path: Path) -> dict:
    initialize_schema(path)
    rules = [{"ruleId": "fixture-defer", "action": "defer", "auditReason": "fixture unknown",
              "match": {"allPatterns": [{"patternId": "fixture", "regex": "fixture"}]}}]
    template = {"rules": rules}
    content_hash = sha256(json.dumps(template, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert store.append_screening_template(template_id="fixture-template", payload=template, approval_state="approved",
                                           created_at=NOW.isoformat(), approved_at=NOW.isoformat(), db_path=path) == 1
    revision = store.append_execution_config(config_id="execution", payload=_profile(rules, content_hash),
                                             created_at=NOW.isoformat(), db_path=path)
    store.enqueue_task(task_id="task-spend", kind="analysis", idempotency_key="task-spend", input_version="fixture",
                       input_cutoff_at=NOW.isoformat(), payload={}, budget={"maxAttempts": 2},
                       created_at=NOW.isoformat(), db_path=path)
    store.bind_task_execution(task_id="task-spend", execution_config_id="execution", execution_config_revision=revision,
                              binding_kind="scheduled", bound_at=NOW.isoformat(), db_path=path)
    profile = store.task_execution_profile(task_id="task-spend", db_path=path)
    assert profile is not None
    return profile


def _provider(path: Path, profile: dict) -> MeteredProvider:
    provider = MeteredProvider(ledger_db=path, ledger_task="analysis", api_key="fixture", model="deepseek-v4-pro",
                               name="fixture", api_url="https://api.deepseek.com/chat/completions")
    bind_provider_execution_spending(provider=provider, task_id="task-spend", execution_profile=profile)
    return provider


def test_provider_budget_is_closed_by_default_and_never_opens_a_socket(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "closed.sqlite"
    provider = _provider(path, _seed(path))
    calls = 0

    def upstream(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return LLMResult(ok=True, content="{}", prompt_tokens=1, completion_tokens=1, total_tokens=2,
                         usage_unavailable=False)

    monkeypatch.setattr(OpenAICompatProvider, "chat", upstream)
    with provider_spend_context(provider=provider, task_id="task-spend", stage="analysisPro", item_key="candidate", attempt=1):
        result = provider.chat([], enable_search=False, model_options={"maxTokens": 100, "thinking": {"type": "disabled"}})
    assert not result.ok and result.error_code == "execution_paused"
    assert calls == 0
    assert store.execution_budget_snapshot(task_id="task-spend", db_path=path)["reservationCount"] == 0


def test_provider_reserves_then_settles_one_actual_http_attempt(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "settled.sqlite"
    provider = _provider(path, _seed(path))
    store.set_run_control(state="open", reason_code="fixture_approved", changed_at=NOW.isoformat(), changed_by="test", db_path=path)
    calls = 0

    def upstream(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return LLMResult(ok=True, content="{}", prompt_tokens=7, completion_tokens=3, total_tokens=10,
                         usage_unavailable=False)

    monkeypatch.setattr(OpenAICompatProvider, "chat", upstream)
    with provider_spend_context(provider=provider, task_id="task-spend", stage="analysisPro", item_key="candidate", attempt=1):
        result = provider.chat([], enable_search=False, model_options={"maxTokens": 100, "thinking": {"type": "disabled"}})
    assert result.ok and calls == 1
    snapshot = store.execution_budget_snapshot(task_id="task-spend", db_path=path)
    assert snapshot["reservationCount"] == 1
    assert snapshot["actual"] == {"calls": 1, "inputTokens": 7, "outputTokens": 3, "totalTokens": 10,
                                  "fullTextCalls": 0, "retries": 0, "searchRequests": 0, "searchCredits": 0}


def test_provider_without_explicit_thread_context_cannot_call_upstream(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "missing-context.sqlite"
    provider = _provider(path, _seed(path))
    store.set_run_control(state="open", reason_code="fixture_approved", changed_at=NOW.isoformat(), changed_by="test", db_path=path)
    monkeypatch.setattr(OpenAICompatProvider, "chat", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("upstream")))
    result = provider.chat([], enable_search=False, model_options={"maxTokens": 100, "thinking": {"type": "disabled"}})
    assert not result.ok and result.error_code == "execution_spend_context_missing"


def test_provider_rejects_a_prompt_that_exceeds_the_frozen_reservation_before_http(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "oversized.sqlite"
    provider = _provider(path, _seed(path))
    store.set_run_control(state="open", reason_code="fixture_approved", changed_at=NOW.isoformat(), changed_by="test", db_path=path)
    monkeypatch.setattr(OpenAICompatProvider, "chat", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("upstream")))
    with provider_spend_context(provider=provider, task_id="task-spend", stage="analysisPro", item_key="candidate", attempt=1):
        result = provider.chat([ChatMessage(role="user", content="x" * 2000)], enable_search=False,
                               model_options={"maxTokens": 100, "thinking": {"type": "disabled"}})
    assert not result.ok and result.error_code == "pending_budget"
    assert store.execution_budget_snapshot(task_id="task-spend", db_path=path)["reservationCount"] == 0
