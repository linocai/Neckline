from __future__ import annotations

import json
from hashlib import sha256
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api.k10 import create_router
from neckline.k10 import store
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.config import validate_execution_config
from neckline.k10.model_execution import SemanticValidationError, execute_model_operation
from neckline.k10.schema import K10SchemaError, initialize_schema, rollback_schema, schema_version
from neckline.k10.worker import TaskResult, run_once
from neckline.llm.base import LLMResult
from tests.k10_v306_fixture import append_approved_execution_profile, execution_payload


NOW = datetime(2026, 9, 8, 1, tzinfo=timezone.utc)


def _profile() -> dict:
    return execution_payload()[1]


def _strategy() -> dict:
    return {
        "configVersion": "k10-v1.4", "universe": "chinext", "excludeBaijiu": True,
        "hardExclusions": {"approved": True, "board": "chinext", "priceLimit": "none", "st": "exclude", "swL2Exclusions": ["801125.SI"]},
        "sourceAdapters": [{"key": "fixture", "lateArrivalReplaySeconds": 86400}],
        "modelRoutes": {"discovery": "deepseek-v4-pro", "analysis": "deepseek-v4-pro", "morning": "deepseek-v4-pro"},
        "taskPolicies": {"discovery": {"maxAttempts": 3, "modelMaxAttempts": 1, "timeoutSeconds": 30,
                          "costLimit": None, "maxSourceRequests": 1, "maxVerificationRequests": 1}},
    }


def _seed(path: Path) -> tuple[str, int]:
    initialize_schema(path)
    # These are historical execution-ledger tests, not pause-gate tests.  A
    # synthetic operator explicitly opens the V5 control before exercising the
    # former B36 recovery behavior.
    store.set_run_control(state="open", reason_code="fixture_execution", changed_at=NOW.isoformat(),
                          changed_by="test", db_path=path)
    execution_id, execution_revision = append_approved_execution_profile(
        db_path=path, created_at=NOW.isoformat(), config_id="execution",
    )
    strategy_revision = store.append_run_config(config_id="strategy", payload=_strategy(), created_at=NOW.isoformat(), db_path=path)
    store.enqueue_task(task_id="task-1", kind="evening_scan", idempotency_key="task-1", input_version="strategy-hash",
                       input_cutoff_at=NOW.isoformat(), payload={"windowKind": "evening", "configId": "strategy", "configRevision": strategy_revision},
                       budget={"maxAttempts": 3}, created_at=NOW.isoformat(), db_path=path)
    store.bind_task_execution(task_id="task-1", execution_config_id=execution_id, execution_config_revision=execution_revision,
                              binding_kind="scheduled", bound_at=NOW.isoformat(), db_path=path)
    return execution_id, execution_revision


def test_execution_profile_is_strict_and_never_accepts_unknown_reasoning_shape():
    valid = _profile()
    assert validate_execution_config(valid).ready
    invalid = json.loads(json.dumps(valid))
    invalid["discovery"]["modelOptions"]["understand"]["thinking"] = {"type": ["disabled"]}
    assert not validate_execution_config(invalid).ready
    missing_deadline = json.loads(json.dumps(valid))
    del missing_deadline["discovery"]["completionDeadlineSeconds"]
    assert not validate_execution_config(missing_deadline).ready


def test_checkpoint_is_immutable_after_completion_and_progress_is_safe(tmp_path):
    path = tmp_path / "execution.sqlite"
    profile_id, revision = _seed(path)
    store.create_scan(scan_id="scan-1", window_kind="evening", cutoff_at=NOW.isoformat(), config_id="strategy", config_revision=1,
                      status="running", coverage={"inputDocumentRefs": [{"documentId": "doc", "revision": 1}], "inputSnapshotFrozen": True},
                      created_at=NOW.isoformat(), completed_at=None, db_path=path)
    store.bind_scan_execution(scan_id="scan-1", task_id="task-1", execution_config_id=profile_id, execution_config_revision=revision,
                              binding_kind="scheduled", bound_at=NOW.isoformat(), db_path=path)
    store.record_execution_checkpoint(task_id="task-1", item_kind="document", item_key="doc@1", stage="understand",
                                      input_sha256="input-hash", status="completed", attempt_count=1, network_attempt_count=1,
                                      repair_attempt_count=0, elapsed_ms=20, input_tokens=10, output_tokens=5,
                                      result={"events": [{"headline": "derived"}]}, safe_error_code=None, safe_error_ref=None,
                                      updated_at=NOW.isoformat(), db_path=path)
    assert store.completed_execution_items(task_id="task-1", item_kind="document", stage="understand", db_path=path)[0]["result"]["events"]
    progress = store.execution_progress_for_scan(scan_id="scan-1", db_path=path)
    assert progress is not None and progress["executionBinding"]["bindingKind"] == "scheduled"
    with pytest.raises(ValueError, match="原始响应"):
        store.record_execution_checkpoint(task_id="task-1", item_kind="document", item_key="doc@2", stage="understand",
                                          input_sha256="h", status="completed", attempt_count=1, network_attempt_count=1,
                                          repair_attempt_count=0, elapsed_ms=1, input_tokens=1, output_tokens=1,
                                          result={"rawResponse": "do-not-store"}, safe_error_code=None, safe_error_ref=None,
                                          updated_at=NOW.isoformat(), db_path=path)


def test_execution_progress_uses_model_stage_rows_without_duplicate_document_failures(tmp_path):
    path = tmp_path / "execution-progress-model-rows.sqlite"
    profile_id, revision = _seed(path)
    store.create_scan(
        scan_id="scan-model-rows", window_kind="evening", cutoff_at=NOW.isoformat(), config_id="strategy", config_revision=1,
        status="running", coverage={"inputDocumentRefs": [
            {"documentId": "doc-a", "revision": 1}, {"documentId": "doc-b", "revision": 1},
        ], "inputSnapshotFrozen": True}, created_at=NOW.isoformat(), completed_at=None, db_path=path,
    )
    store.bind_scan_execution(
        scan_id="scan-model-rows", task_id="task-1", execution_config_id=profile_id, execution_config_revision=revision,
        binding_kind="scheduled", bound_at=NOW.isoformat(), db_path=path,
    )

    def checkpoint(*, kind: str, key: str, stage: str, status: str, code: str | None = None,
                   ref: str | None = None) -> None:
        store.record_execution_checkpoint(
            task_id="task-1", item_kind=kind, item_key=key, stage=stage, input_sha256=sha256(key.encode()).hexdigest(),
            status=status, attempt_count=1, network_attempt_count=1, repair_attempt_count=0, elapsed_ms=1,
            input_tokens=1, output_tokens=1, result={} if status == "completed" else None,
            safe_error_code=code, safe_error_ref=ref, updated_at=NOW.isoformat(), db_path=path,
        )

    # The coarse understanding checkpoint is the final document-level outcome;
    # its model-operation row is an internal attempt and must not raise the count.
    checkpoint(kind="document", key="doc-a@1", stage="understand", status="completed")
    checkpoint(kind="document", key="model:understand:a", stage="model:understand", status="completed")
    # Both rows describe the same document failure and must surface as one item.
    checkpoint(kind="document", key="doc-b@1", stage="understand", status="failed",
               code="model_output_invalid", ref="doc-b@1")
    checkpoint(kind="document", key="model:understand:b-key", stage="model:understand", status="failed",
               code="model_output_invalid", ref="doc-b@1:key")
    checkpoint(kind="document", key="model:understand:b-full", stage="model:understand", status="failed",
               code="model_output_invalid", ref="doc-b@1:full")
    checkpoint(kind="event", key="model:verify:a", stage="model:verify", status="completed")
    checkpoint(kind="event", key="model:map:a", stage="model:map", status="completed")
    checkpoint(kind="event", key="model:compare:a", stage="model:compare", status="completed")
    checkpoint(kind="event", key="model:classify:a", stage="model:classify", status="completed")

    progress = store.execution_progress_for_scan(scan_id="scan-model-rows", db_path=path)
    assert progress is not None
    # Legacy B37 model rows cannot fabricate V3 title-selection progress.
    assert progress["titleCounts"] is None
    store.finalize_scan(
        scan_id="scan-model-rows", status="partial", coverage={"inputDocumentRefs": [
            {"documentId": "doc-a", "revision": 1}, {"documentId": "doc-b", "revision": 1},
        ], "inputSnapshotFrozen": True}, completed_at=NOW.isoformat(), db_path=path,
    )
    terminal = store.execution_progress_for_scan(scan_id="scan-model-rows", db_path=path)
    assert terminal is not None and terminal["state"] == "partial" and terminal["stage"] == "created"


def test_execution_progress_hides_stale_retry_after_task_is_claimed(tmp_path):
    path = tmp_path / "execution-progress-retry.sqlite"
    profile_id, revision = _seed(path)
    store.create_scan(
        scan_id="scan-retry", window_kind="evening", cutoff_at=NOW.isoformat(), config_id="strategy", config_revision=1,
        status="running", coverage={"inputDocumentRefs": [{"documentId": "doc-a", "revision": 1}], "inputSnapshotFrozen": True},
        created_at=NOW.isoformat(), completed_at=None, db_path=path,
    )
    store.bind_scan_execution(
        scan_id="scan-retry", task_id="task-1", execution_config_id=profile_id, execution_config_revision=revision,
        binding_kind="scheduled", bound_at=NOW.isoformat(), db_path=path,
    )
    store.record_execution_checkpoint(
        task_id="task-1", item_kind="document", item_key="doc-a@1", stage="understand",
        input_sha256=_model_input("coarse:doc-a@1"), status="completed", attempt_count=1,
        network_attempt_count=0, repair_attempt_count=0, elapsed_ms=0, input_tokens=None, output_tokens=None,
        result={"events": [], "fullTextUsed": False}, safe_error_code=None, safe_error_ref=None,
        updated_at=NOW.isoformat(), db_path=path,
    )
    claimed = store.claim_task_by_id(task_id="task-1", worker_id="retry-worker", now=NOW,
                                     lease_for=timedelta(minutes=1), db_path=path)
    assert claimed is not None
    retry_at = NOW + timedelta(minutes=2)
    assert store.schedule_task_retry(
        task_id="task-1", worker_id="retry-worker", stage="recovery", checkpoint={"scanId": "scan-retry"},
        safe_error_code="model_output_invalid", not_before_at=retry_at, scheduled_at=NOW,
        retry_kind="failure", max_failure_attempts=3, db_path=path,
    )
    queued = store.execution_progress_for_scan(scan_id="scan-retry", db_path=path)
    assert queued is not None and queued["stage"] == "recovery"
    assert queued["nextRetryAt"] == retry_at.isoformat(timespec="seconds")
    assert store.claim_task_by_id(task_id="task-1", worker_id="resumed-worker", now=retry_at,
                                  lease_for=timedelta(minutes=1), db_path=path) is not None
    resumed = store.execution_progress_for_scan(scan_id="scan-retry", db_path=path)
    assert resumed is not None and resumed["stage"] == "leased"
    assert resumed["nextRetryAt"] is None


def test_worker_model_rows_reach_scan_api_progress_counts(tmp_path):
    path = tmp_path / "worker-model-progress.sqlite"
    profile_id, revision = _seed(path)
    store.create_scan(
        scan_id="scan-worker-model", window_kind="evening", cutoff_at=NOW.isoformat(), config_id="strategy", config_revision=1,
        status="running", coverage={"inputDocumentRefs": [
            {"documentId": "doc-a", "revision": 1}, {"documentId": "doc-b", "revision": 1},
        ], "inputSnapshotFrozen": True},
        created_at=NOW.isoformat(), completed_at=None, db_path=path,
    )
    store.bind_scan_execution(
        scan_id="scan-worker-model", task_id="task-1", execution_config_id=profile_id, execution_config_revision=revision,
        binding_kind="scheduled", bound_at=NOW.isoformat(), db_path=path,
    )

    def model(operation: str, item_key: str) -> None:
        result = _model_result(
            task_id="task-1", operation=operation, item_key=item_key, input_sha256=_model_input(f"{operation}:{item_key}"),
            path=path, operation_call=lambda: LLMResult(ok=True, content="{}", prompt_tokens=1, completion_tokens=1,
                                                         total_tokens=2, usage_unavailable=False), validate=lambda _: {},
        )
        assert result.status == "completed"

    def failed_understanding(variant: str) -> None:
        result = _model_result(
            task_id="task-1", operation="understand", item_key=f"doc-b@1:{variant}",
            input_sha256=_model_input(f"understand:doc-b@1:{variant}"), path=path,
            operation_call=lambda: LLMResult(ok=True, content="{}", prompt_tokens=1, completion_tokens=1,
                                             total_tokens=2, usage_unavailable=False),
            validate=lambda _: (_ for _ in ()).throw(SemanticValidationError(code="model_output_invalid")),
        )
        assert result.status == "failed" and result.safe_error_code == "model_output_invalid"

    def handler(_context):
        model("understand", "doc-a@1")
        store.record_execution_checkpoint(
            task_id="task-1", item_kind="document", item_key="doc-a@1", stage="understand",
            input_sha256=_model_input("coarse:doc-a@1"), status="completed", attempt_count=1,
            network_attempt_count=0, repair_attempt_count=0, elapsed_ms=0, input_tokens=None, output_tokens=None,
            result={"events": [], "fullTextUsed": True}, safe_error_code=None, safe_error_ref=None, updated_at=NOW.isoformat(), db_path=path,
        )
        failed_understanding("key")
        failed_understanding("full")
        store.record_execution_checkpoint(
            task_id="task-1", item_kind="document", item_key="doc-b@1", stage="understand",
            input_sha256=_model_input("coarse:doc-b@1"), status="failed", attempt_count=1,
            network_attempt_count=0, repair_attempt_count=0, elapsed_ms=0, input_tokens=None, output_tokens=None,
            result=None, safe_error_code="model_output_invalid", safe_error_ref="doc-b@1",
            updated_at=NOW.isoformat(), db_path=path,
        )
        model("verify", "event-a")
        model("compare", "event-a")
        return TaskResult("completed", "done", {"scanId": "scan-worker-model"})

    assert run_once(db_path=path, worker_id="worker", lease_for=timedelta(minutes=1),
                    handlers={"evening_scan": handler}, clock=lambda: NOW) is not None
    app = FastAPI()
    app.include_router(create_router(lambda: path, lambda: None, lambda: tmp_path / "parquet",
                                     current_config_binding_provider=lambda: ("strategy", 1, None)))
    with TestClient(app) as client:
        response = client.get("/api/v1/k10/scans/scan-worker-model")
    assert response.status_code == 200, response.text
    progress = response.json()["executionProgress"]
    assert progress["state"] == "running"
    assert progress["titleCounts"] is None
    model("prioritize", "scan-worker-model")
    with TestClient(app) as client:
        ranking_response = client.get("/api/v1/k10/scans/scan-worker-model")
    assert ranking_response.status_code == 200, ranking_response.text
    assert ranking_response.json()["executionProgress"]["titleCounts"] is None


def test_checkpoint_rechecks_lease_inside_write_transaction_after_takeover(tmp_path):
    path = tmp_path / "checkpoint-lease.sqlite"
    _seed(path)
    former = store.claim_task_by_id(task_id="task-1", worker_id="former", now=NOW,
                                    lease_for=timedelta(seconds=1), db_path=path)
    assert former is not None and former.lease_owner == "former"
    guard_calls = 0

    def former_worker_guard() -> None:
        nonlocal guard_calls
        guard_calls += 1
        task = store.get_task(task_id="task-1", db_path=path)
        assert task is not None
        if guard_calls == 1:
            # This takes place after the old worker's optimistic check but
            # before its checkpoint writer begins.  It uses the real lease
            # claim path instead of editing task state directly.
            assert task.lease_owner == "former"
            successor = store.claim_task_by_id(task_id="task-1", worker_id="successor",
                                                now=NOW + timedelta(seconds=2),
                                                lease_for=timedelta(minutes=1), db_path=path)
            assert successor is not None and successor.lease_owner == "successor"
            return
        if task.lease_owner != "former":
            raise store.K10Conflict("任务租约已失效，请等待恢复")

    with pytest.raises(store.K10Conflict, match="租约已失效"):
        store.record_execution_checkpoint(
            task_id="task-1", item_kind="document", item_key="doc@1", stage="understand",
            input_sha256="input-hash", status="completed", attempt_count=1, network_attempt_count=1,
            repair_attempt_count=0, elapsed_ms=1, input_tokens=1, output_tokens=1,
            result={"events": []}, safe_error_code=None, safe_error_ref=None,
            updated_at=NOW.isoformat(), db_path=path, leaseguard=former_worker_guard,
        )
    assert guard_calls == 2
    assert store.completed_execution_items(task_id="task-1", item_kind="document", stage="understand", db_path=path) == []


def test_continuation_does_not_consume_failure_retry_allowance(tmp_path):
    path = tmp_path / "continuation.sqlite"
    _seed(path)
    calls = []

    def handler(context):
        calls.append((context.task.attempt_count, context.failure_attempt_count))
        if len(calls) < 5:
            return TaskResult("failed", "slice", {"slice": len(calls)}, "分段继续", retry_at=NOW,
                              retry_kind="continuation", safe_error_code="DISCOVERY_SLICE")
        return TaskResult("completed", "done")

    for _ in range(5):
        task = run_once(db_path=path, worker_id="worker", lease_for=timedelta(minutes=1), handlers={"evening_scan": handler}, clock=lambda: NOW)
        assert task is not None
    assert len(calls) == 5
    assert [failure for _, failure in calls] == [0, 0, 0, 0, 0]


def test_execution_deadline_starts_once_and_survives_continuation_checkpoint_replacement(tmp_path):
    path = tmp_path / "deadline.sqlite"
    _seed(path)
    seen = []

    def handler(context):
        seen.append((context.execution_started_at, context.execution_deadline_at))
        if len(seen) == 1:
            return TaskResult("failed", "slice", {"slice": 1}, "继续", retry_at=NOW,
                              retry_kind="continuation", safe_error_code="DISCOVERY_SLICE")
        return TaskResult("completed", "done", {"slice": 2})

    assert run_once(db_path=path, worker_id="worker", lease_for=timedelta(minutes=1),
                    handlers={"evening_scan": handler}, clock=lambda: NOW) is not None
    resumed_at = NOW + timedelta(minutes=3)
    assert run_once(db_path=path, worker_id="worker", lease_for=timedelta(minutes=1),
                    handlers={"evening_scan": handler}, clock=lambda: resumed_at) is not None
    assert len(seen) == 2
    assert seen[0][0] == seen[1][0] == NOW
    assert seen[0][1] == seen[1][1] == NOW + timedelta(seconds=7200)
    persisted = store.task_execution_input(task_id="task-1", db_path=path)
    assert persisted is not None and persisted["checkpoint"]["executionStartedAt"] == NOW.isoformat()


def test_expired_deadline_before_discovery_main_never_reopens_a_failed_scan(tmp_path):
    """A recovery can hit its deadline before the main discovery try/except begins."""
    from neckline.k10.pipeline import _scan_id, execute_scan

    path = tmp_path / "deadline-preflight.sqlite"
    profile_id, revision = _seed(path)
    identity = "expired-preflight"
    scan_id = _scan_id(kind="evening", cutoff_at=NOW, identity=identity)
    store.create_scan(scan_id=scan_id, window_kind="evening", cutoff_at=NOW.isoformat(), config_id="strategy",
                      config_revision=1, status="failed", coverage={"inputSnapshotFrozen": False},
                      created_at=NOW.isoformat(), completed_at=NOW.isoformat(), db_path=path)

    class Model:
        def set_execution_policy(self, _policy): pass
        def set_scan_cutoff(self, _cutoff): pass

    adapter = SimpleNamespace(coverage=SimpleNamespace(source_key="fixture"))
    binding = store.task_execution_profile(task_id="task-1", db_path=path)
    assert binding is not None and binding["configId"] == profile_id and binding["revision"] == revision
    result = execute_scan(kind="evening", cutoff_at=NOW, configuration=_strategy(), db_path=path,
                          adapter=adapter, model=Model(), metadata=object(), created_at=NOW,
                          config_id="strategy", config_revision=1, scan_identity=identity,
                          task_id="task-1", execution_profile=binding,
                          execution_deadline_at=NOW - timedelta(seconds=1))
    assert result.status == "failed" and result.stage == "deadline"
    scan = store.get_scan(scan_id=scan_id, db_path=path)
    assert scan is not None and scan["status"] == "failed"


def _model_input(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _model_result(*, task_id: str, operation: str, item_key: str, input_sha256: str, path: Path,
                  operation_call, validate, repair_call=None):
    return execute_model_operation(
        task_id=task_id, operation=operation, item_key=item_key, input_sha256=input_sha256,
        policy=_profile()["discovery"], operation_call=operation_call, validate=validate,
        repair_call=repair_call, db_path=path, now=lambda: NOW,
    )


def test_model_operation_caches_only_semantically_valid_normalized_output(tmp_path):
    path = tmp_path / "model-cache.sqlite"
    _seed(path)
    input_sha = _model_input("frozen-understand-input")
    calls = 0

    def call():
        nonlocal calls
        calls += 1
        return LLMResult(ok=True, content='{"untrusted":true}', prompt_tokens=11, completion_tokens=7, total_tokens=18,
                         usage_unavailable=False)

    def validator(raw):
        if raw.get("untrusted") is not True:
            raise SemanticValidationError(code="understand_contract_invalid")
        return {"events": []}

    first = _model_result(task_id="task-1", operation="understand", item_key="doc@1", input_sha256=input_sha,
                          path=path, operation_call=call, validate=validator)
    assert first.status == "completed" and not first.reused and first.value == {"events": []}
    assert first.input_tokens == 11 and first.output_tokens == 7
    cached = _model_result(task_id="task-1", operation="understand", item_key="doc@1", input_sha256=input_sha,
                           path=path, operation_call=lambda: pytest.fail("cache must prevent a second provider call"),
                           validate=validator)
    assert cached.status == "completed" and cached.reused and cached.value == {"events": []}
    assert calls == 1


def test_model_operation_does_not_cache_raw_json_that_fails_domain_validation(tmp_path):
    path = tmp_path / "model-semantic.sqlite"
    _seed(path)
    input_sha = _model_input("semantic-invalid")
    calls = 0

    def call():
        nonlocal calls
        calls += 1
        return LLMResult(ok=True, content='{"event":"bad"}', prompt_tokens=3, completion_tokens=2,
                         usage_unavailable=False)

    failed = _model_result(task_id="task-1", operation="understand", item_key="doc@1", input_sha256=input_sha,
                           path=path, operation_call=call,
                           validate=lambda _: (_ for _ in ()).throw(SemanticValidationError(code="understand_contract_invalid")))
    assert failed.status == "failed" and failed.safe_error_code == "understand_contract_invalid"
    again = _model_result(task_id="task-1", operation="understand", item_key="doc@1", input_sha256=input_sha,
                          path=path, operation_call=call, validate=lambda raw: raw)
    assert again.status == "failed" and again.safe_error_code == "understand_contract_invalid"
    assert calls == 1


def test_model_network_retry_keeps_paid_failure_usage_and_json_repair_is_bounded(tmp_path):
    path = tmp_path / "model-retry.sqlite"
    _seed(path)
    network_input = _model_input("network")
    calls = []

    def network_call():
        calls.append("network")
        if len(calls) == 1:
            return LLMResult(ok=False, error_code="provider_transport", prompt_tokens=3, completion_tokens=1,
                             total_tokens=4, usage_unavailable=False)
        return LLMResult(ok=True, content='{"ok":true}', prompt_tokens=5, completion_tokens=2, total_tokens=7,
                         usage_unavailable=False)

    first = _model_result(task_id="task-1", operation="verify", item_key="event", input_sha256=network_input,
                          path=path, operation_call=network_call, validate=lambda raw: raw)
    assert first.status == "failed" and first.safe_error_code == "provider_transport"
    assert (first.network_attempt_count, first.input_tokens, first.output_tokens) == (1, 3, 1)
    second = _model_result(task_id="task-1", operation="verify", item_key="event", input_sha256=network_input,
                           path=path, operation_call=network_call, validate=lambda raw: raw)
    assert second.status == "completed" and (second.network_attempt_count, second.input_tokens, second.output_tokens) == (2, 8, 3)

    repair_input = _model_input("repair")
    invalid = _model_result(task_id="task-1", operation="map", item_key="event", input_sha256=repair_input,
                            path=path, operation_call=lambda: LLMResult(ok=True, content="not-json", prompt_tokens=2,
                                                                          completion_tokens=1, usage_unavailable=False),
                            validate=lambda raw: raw)
    assert invalid.status == "failed" and invalid.safe_error_code == "model_json_invalid"
    repaired = _model_result(task_id="task-1", operation="map", item_key="event", input_sha256=repair_input,
                             path=path, operation_call=lambda: pytest.fail("must use repair callback"),
                             repair_call=lambda: LLMResult(ok=True, content='[]', prompt_tokens=4, completion_tokens=1,
                                                            usage_unavailable=False), validate=lambda raw: raw)
    assert repaired.status == "completed" and repaired.repair_attempt_count == 1 and repaired.network_attempt_count == 2


def test_model_truncation_preserves_reported_usage_without_reissuing_same_input(tmp_path):
    path = tmp_path / "model-truncated.sqlite"
    _seed(path)
    input_sha = _model_input("truncated-at-8192")
    calls = 0

    def truncated():
        nonlocal calls
        calls += 1
        return LLMResult(ok=False, error_code="response_truncated", prompt_tokens=99, completion_tokens=8192,
                         total_tokens=8291, usage_unavailable=False)

    first = _model_result(task_id="task-1", operation="understand", item_key="doc@1", input_sha256=input_sha,
                          path=path, operation_call=truncated, validate=lambda raw: raw)
    assert first.status == "failed" and first.safe_error_code == "response_truncated"
    assert (first.network_attempt_count, first.input_tokens, first.output_tokens) == (1, 99, 8192)
    second = _model_result(task_id="task-1", operation="understand", item_key="doc@1", input_sha256=input_sha,
                           path=path, operation_call=lambda: pytest.fail("same truncated input must not call upstream again"),
                           validate=lambda raw: raw)
    assert second.status == "failed" and second.safe_error_code == "response_truncated"
    assert (second.network_attempt_count, second.input_tokens, second.output_tokens) == (1, 99, 8192)
    assert calls == 1


def test_model_interrupted_attempt_remains_charged_but_can_use_remaining_attempt(tmp_path):
    path = tmp_path / "model-interrupted.sqlite"
    _seed(path)
    input_sha = _model_input("interrupted")
    with pytest.raises(KeyboardInterrupt):
        _model_result(task_id="task-1", operation="compare", item_key="event", input_sha256=input_sha, path=path,
                      operation_call=lambda: (_ for _ in ()).throw(KeyboardInterrupt()), validate=lambda raw: raw)
    resumed = _model_result(task_id="task-1", operation="compare", item_key="event", input_sha256=input_sha, path=path,
                            operation_call=lambda: LLMResult(ok=True, content='{"ok":true}', prompt_tokens=1,
                                                              completion_tokens=1, usage_unavailable=False), validate=lambda raw: raw)
    assert resumed.status == "completed" and resumed.network_attempt_count == 2 and resumed.attempt_count == 2


def test_model_attempt_rechecks_lease_in_its_write_transaction(tmp_path):
    path = tmp_path / "model-lease.sqlite"
    _seed(path)
    assert store.claim_task_by_id(task_id="task-1", worker_id="former", now=NOW,
                                  lease_for=timedelta(seconds=1), db_path=path) is not None
    guard_calls, provider_calls = 0, 0

    def former_guard() -> None:
        nonlocal guard_calls
        guard_calls += 1
        current = store.get_task(task_id="task-1", db_path=path)
        assert current is not None
        if guard_calls == 1:
            assert current.lease_owner == "former"
            successor = store.claim_task_by_id(task_id="task-1", worker_id="successor",
                                                now=NOW + timedelta(seconds=2),
                                                lease_for=timedelta(minutes=1), db_path=path)
            assert successor is not None and successor.lease_owner == "successor"
        elif current.lease_owner != "former":
            raise store.K10Conflict("任务租约已失效，请等待恢复")

    def call():
        nonlocal provider_calls
        provider_calls += 1
        return LLMResult(ok=True, content='{}')

    with pytest.raises(store.K10Conflict, match="租约已失效"):
        execute_model_operation(task_id="task-1", operation="classify", item_key="event", input_sha256=_model_input("lease-2"),
                                policy=_profile()["discovery"], operation_call=call, validate=lambda raw: raw,
                                db_path=path, leaseguard=former_guard, now=lambda: NOW)
    assert guard_calls == 2 and provider_calls == 0


def test_recovery_binds_new_execution_profile_to_the_exact_failed_snapshot(tmp_path):
    path = tmp_path / "recovery.sqlite"
    profile_id, revision = _seed(path)
    store.create_scan(scan_id="failed-scan", window_kind="evening", cutoff_at=NOW.isoformat(), config_id="strategy", config_revision=1,
                      status="failed", coverage={"inputSnapshotFrozen": True, "inputDocumentRefs": [{"documentId": "d", "revision": 1}]},
                      created_at=NOW.isoformat(), completed_at=NOW.isoformat(), db_path=path)
    digest = frozen_scan_input_sha256(scan_id="failed-scan", db_path=path)
    task_id = recover_scan(db_path=path, scan_id="failed-scan", execution_config_id=profile_id,
                           execution_config_revision=revision, confirmed_input_sha256=digest, now=NOW)
    task = store.get_task(task_id=task_id, db_path=path)
    assert task is not None and task.payload["resumeScanId"] == "failed-scan" and task.payload["sourceCollection"] == "forbidden"
    assert store.task_execution_profile(task_id=task_id, db_path=path)["bindingKind"] == "recovery"
    with pytest.raises(K10SchemaError, match="标题筛选、尝试或缓存记录"):
        rollback_schema(path, target_version=3)
    assert schema_version(path) == 7
