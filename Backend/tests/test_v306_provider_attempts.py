"""V3 provider-attempt ledger regressions: trace calls, never meter a total allowance."""
from __future__ import annotations

from pathlib import Path

from neckline.k10 import store
from neckline.k10.metering import MeteredProvider, bind_provider_execution_spending, provider_spend_context
from neckline.k10.schema import initialize_schema
from neckline.llm.base import ChatMessage, LLMResult
from neckline.llm.openai_compat import OpenAICompatProvider
from tests.k10_v306_fixture import append_approved_execution_profile


NOW = "2026-09-08T06:00:00+00:00"


def _bound_provider(path: Path) -> MeteredProvider:
    config_id, revision = append_approved_execution_profile(
        db_path=path, created_at=NOW, config_id="attempt-execution",
    )
    store.enqueue_task(task_id="attempt-task", kind="evening_scan", idempotency_key="attempt-task",
                       input_version="fixture", input_cutoff_at=NOW, payload={}, budget={}, created_at=NOW, db_path=path)
    store.bind_task_execution(task_id="attempt-task", execution_config_id=config_id,
                              execution_config_revision=revision, binding_kind="scheduled", bound_at=NOW, db_path=path)
    provider = MeteredProvider(ledger_db=path, ledger_task="discovery", api_key="fixture", model="deepseek-v4-pro",
                               name="fixture", api_url="https://api.deepseek.com/chat/completions")
    bind_provider_execution_spending(provider=provider, task_id="attempt-task",
                                     execution_profile=store.task_execution_profile(task_id="attempt-task", db_path=path))
    return provider


def test_v3_attempt_ledger_records_actual_usage_without_any_budget_admission(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "attempts.sqlite"
    initialize_schema(path)
    provider = _bound_provider(path)
    store.set_run_control(state="open", reason_code="fixture", changed_at=NOW, changed_by="test", db_path=path)
    calls = 0

    def upstream(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return LLMResult(ok=True, content="{}", prompt_tokens=7, completion_tokens=3, total_tokens=10,
                         usage_unavailable=False)

    monkeypatch.setattr(OpenAICompatProvider, "chat", upstream)
    with provider_spend_context(provider=provider, task_id="attempt-task", stage="titleBatch", item_key="batch-1", attempt=1):
        result = provider.chat([ChatMessage(role="user", content="标题")], enable_search=False,
                               model_options={"maxTokens": 128, "thinking": {"type": "disabled"}})
    assert result.ok and calls == 1
    summary = store.external_attempt_summary(task_id="attempt-task", db_path=path)
    assert summary["succeeded"] == 1 and summary["actualUsage"] == {
        "promptTokens": 7, "completionTokens": 3, "totalTokens": 10, "searchRequests": 0, "searchCredits": 0,
    }


def test_v3_paused_attempt_never_opens_a_socket(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "paused.sqlite"
    initialize_schema(path)
    provider = _bound_provider(path)
    monkeypatch.setattr(OpenAICompatProvider, "chat", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("socket")))
    with provider_spend_context(provider=provider, task_id="attempt-task", stage="titleBatch", item_key="batch-1", attempt=1):
        result = provider.chat([ChatMessage(role="user", content="标题")], enable_search=False,
                               model_options={"maxTokens": 128, "thinking": {"type": "disabled"}})
    assert not result.ok and result.error_code == "execution_paused"
    assert store.external_attempt_summary(task_id="attempt-task", db_path=path)["started"] == 0
