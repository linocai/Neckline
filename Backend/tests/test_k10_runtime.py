from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Event

from neckline.k10 import initialize_schema
from neckline.k10 import runtime, store
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import TaskContext
from neckline.llm.base import LLMResult
from tests.debate_fixture import debate_text


NOW = "2026-09-06T13:00:00+00:00"


class FakeProvider:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.results.pop(0)


def _config():
    return {
        "configVersion": "k10-v1.4", "universe": "chinext", "excludeBaijiu": True,
        "hardExclusions": {"approved": True, "board": "chinext", "priceLimit": "none", "st": "exclude", "swL2Exclusions": ["801125.SI"]},
        "modelRoutes": {"analysis": "deepseek-v4-pro"},
        "taskPolicies": {"analysis": {"maxAttempts": 3, "modelMaxAttempts": 3, "timeoutSeconds": 90, "costLimit": None}},
    }


def _seed(path: Path, *, cutoff=NOW):
    initialize_schema(path)
    config_revision = store.append_run_config(config_id="cfg-1", payload=_config(), created_at=NOW, db_path=path)
    store.append_document_version(document_id="doc-1", source_key="fixture", external_id="external-1",
                                  canonical_url="https://example.invalid/1", content_sha256="a" * 64,
                                  published_at=NOW, published_precision="exact", fetched_at=NOW,
                                  original_text="原始资料", excerpt=None, fetch_version="fixture", metadata={}, created_at=NOW, db_path=path)
    store.append_event_revision(event_id="event-1", stable_key="event", headline="事件", event_kind="policy",
                                facts={}, source_refs=[{"documentId": "doc-1", "revision": 1, "fetchedAt": NOW}],
                                supersedes_revision=None, created_at=NOW, db_path=path)
    store.create_scan(scan_id="scan-1", window_kind="evening", cutoff_at=NOW, config_id="cfg-1",
                      config_revision=config_revision, status="completed", coverage={}, created_at=NOW, completed_at=NOW, db_path=path)
    store.create_candidate(candidate_id="candidate-1", scan_id="scan-1", event_id="event-1", event_revision=1,
                           company_code="300001.SZ", comparison={}, evidence=[], created_at=NOW, db_path=path)
    observation = store.observe_candidate(action_id="action-1", observation_id="observation-1", task_id="task-1",
                                          outbox_id="outbox-1", candidate_id="candidate-1", idempotency_key="observe-1",
                                          task_input_version="k10-api-v1", task_input_cutoff_at=cutoff,
                                          task_payload={"configId": "cfg-1", "configRevision": config_revision},
                                          task_budget={"maxAttempts": 3}, created_at=NOW, db_path=path)
    assert observation.created
    return store.get_task(task_id="task-1", db_path=path)


def _context(task, path, checkpoint=None, *, cutoff=NOW):
    return TaskContext(task=task, budget={"maxAttempts": 3}, checkpoint=checkpoint or {}, input_version="k10-api-v1",
                       input_cutoff_at=cutoff, db_path=path, lease_lost=Event())


def _ok(text, *, cache=2):
    return LLMResult(ok=True, content=debate_text(text), provider="deepseek", model="deepseek-v4-pro", prompt_tokens=10,
                     completion_tokens=5, total_tokens=15, usage_unavailable=False,
                     raw_usage={"responses": [{"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                                                "prompt_cache_hit_tokens": cache}]})


def test_handler_persists_two_full_debate_artifacts_and_usage(tmp_path, monkeypatch):
    path = tmp_path / "k10.db"; task = _seed(path)
    provider = FakeProvider([_ok("正方全文"), _ok("反方全文")])
    monkeypatch.setattr(runtime, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution("configured", provider, "deepseek", None))
    result = runtime.analysis_handler(_context(task, path))
    assert result.status == "completed"
    assert len(provider.calls) == 2
    pro = store.load_analysis_revision(observation_id="observation-1", input_cutoff_at=NOW, analysis_kind="pro", db_path=path)
    con = store.load_analysis_revision(observation_id="observation-1", input_cutoff_at=NOW, analysis_kind="con", db_path=path)
    assert (pro["content"]["fullText"], con["content"]["fullText"]) == ("正方全文", "反方全文")
    assert pro["content"]["usage"]["inputTokens"] == 10
    assert pro["content"]["usage"]["cacheTokens"] == 2
    assert pro["content"]["usage"]["cost"]["amount"] is None
    assert pro["content"]["inputLineage"]["marketContext"]["status"] == "unavailable"
    assert "pricePlan" not in result.checkpoint


def test_con_failure_is_immutable_and_retry_only_calls_con(tmp_path, monkeypatch):
    path = tmp_path / "k10.db"; task = _seed(path)
    first = FakeProvider([_ok("正方"), LLMResult(ok=False, reason="secret upstream body", provider="deepseek", model="deepseek-v4-pro")])
    monkeypatch.setattr(runtime, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution("configured", first, "deepseek", None))
    failed = runtime.analysis_handler(_context(task, path))
    assert failed.status == "failed" and failed.stage == "con_failed"
    second = FakeProvider([_ok("反方重试")])
    monkeypatch.setattr(runtime, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution("configured", second, "deepseek", None))
    completed = runtime.analysis_handler(_context(task, path, failed.checkpoint))
    assert completed.status == "completed" and len(second.calls) == 1
    assert store.load_analysis_revision(observation_id="observation-1", input_cutoff_at=NOW, analysis_kind="pro", db_path=path)["content"]["fullText"] == "正方"
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT revision,status,content_json FROM k10_analysis_revisions WHERE analysis_kind='con' ORDER BY revision").fetchall()
    assert [(revision, status) for revision, status, _ in rows] == [(1, "failed"), (1, "completed")]
    assert "secret upstream body" not in rows[0][2]


def test_initial_pro_failure_retries_same_revision_and_keeps_failed_attempt(tmp_path, monkeypatch):
    path = tmp_path / "k10.db"; task = _seed(path)
    first = FakeProvider([LLMResult(ok=False, provider="deepseek", model="deepseek-v4-pro")])
    monkeypatch.setattr(runtime, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution("configured", first, "deepseek", None))
    assert runtime.analysis_handler(_context(task, path)).stage == "pro_failed"
    second = FakeProvider([_ok("正方重试"), _ok("反方")])
    monkeypatch.setattr(runtime, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution("configured", second, "deepseek", None))
    assert runtime.analysis_handler(_context(task, path)).status == "completed"
    with sqlite3.connect(path) as connection:
        pro_rows = connection.execute("SELECT revision,status FROM k10_analysis_revisions WHERE analysis_kind='pro' ORDER BY rowid").fetchall()
        con_rows = connection.execute("SELECT revision,status FROM k10_analysis_revisions WHERE analysis_kind='con' ORDER BY rowid").fetchall()
    assert pro_rows == [(1, "failed"), (1, "completed")]
    assert con_rows == [(1, "completed")]


def test_retry_uses_pro_frozen_missing_market_context_not_late_payload(tmp_path, monkeypatch):
    path = tmp_path / "k10.db"; task = _seed(path)
    first = FakeProvider([_ok("正方"), LLMResult(ok=False, provider="deepseek", model="deepseek-v4-pro")])
    monkeypatch.setattr(runtime, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution("configured", first, "deepseek", None))
    assert runtime.analysis_handler(_context(task, path)).stage == "con_failed"
    second = FakeProvider([_ok("反方")])
    monkeypatch.setattr(runtime, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution("configured", second, "deepseek", None))
    task = type(task)(task.task_id, task.kind, task.status, task.attempt_count, task.lease_owner, task.lease_until, {
        **task.payload,
        "marketContext": {"status": "available", "asOf": NOW, "sourceRefs": [{"url": "market://later", "fetchedAt": NOW}], "recentDays": [{"tradeDate": "20260905", "close": 99.0}]},
    })
    assert runtime.analysis_handler(_context(task, path)).status == "completed"
    con = store.load_analysis_revision(observation_id="observation-1", input_cutoff_at=NOW, analysis_kind="con", db_path=path)
    assert con["content"]["inputLineage"]["marketContext"]["status"] == "unavailable"


def test_handler_missing_provider_is_not_configured_without_model_call(tmp_path, monkeypatch):
    path = tmp_path / "k10.db"; task = _seed(path)
    monkeypatch.setattr(runtime, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution("not_configured", None, None, "缺少连接"))
    result = runtime.analysis_handler(_context(task, path))
    assert result.status == "not_configured"
    assert result.error == "缺少连接"


def test_injected_resolver_runs_full_debate_without_provider_records(tmp_path):
    path = tmp_path / "isolated.db"; task = _seed(path)
    provider = FakeProvider([_ok("正方进程内"), _ok("反方进程内")])
    resolver = lambda **_: ProviderResolution("configured", provider, "process-only", None)
    handler = runtime.production_analysis_handler(provider_resolver=resolver)
    result = handler(_context(task, path))
    assert result.status == "completed"
    assert len(provider.calls) == 2
    con = store.load_analysis_revision(observation_id="observation-1", input_cutoff_at=NOW, analysis_kind="con", db_path=path)
    assert con["content"]["fullText"] == "反方进程内"
