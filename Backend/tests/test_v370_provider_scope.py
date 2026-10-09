"""Provider refusal scope, exercised with B92 CLI-owned task bindings.

Admission is the boundary under test, so the worker handler deliberately calls
the real ledger instead of the report model. Legacy sibling tasks below are
prerequisites for that helper; their bindings are created by enqueue_task, not
patched after a simulated report run. No provider or socket is used.
"""
from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import json
import socket
import sqlite3

import pytest

from neckline.k10 import pipeline, store
from neckline.k10.providers import resolve_deepseek_v4_pro
from neckline.k10.worker import TaskResult, run_once
from neckline.settings_store import ProviderRecord
from tests import test_b92_report_loopback as current


@pytest.fixture(autouse=True)
def no_provider_socket(monkeypatch):
    def rejected(*_args, **_kwargs):
        raise AssertionError("provider scope regression may not open a socket")
    monkeypatch.setattr(socket.socket, "connect", rejected)


def _task(tmp_path):
    database = tmp_path / "provider-scope.sqlite"
    run_id, run_rev, exec_id, exec_rev, _, _ = current._bindings(database)
    task_id = current._cli("enqueue", "--db", str(database), "--kind", "evening",
        "--trading-day", current.DAY.isoformat(), "--config-id", run_id,
        "--config-revision", str(run_rev), "--execution-config-id", exec_id,
        "--execution-config-revision", str(exec_rev))
    binding = store.task_execution_input(task_id=task_id, db_path=database)
    discovery = binding["executionProfile"]["payload"]["discovery"]
    assert discovery["reportInputContract"] == "k10-collected-input-3.6.1-b92"
    assert discovery["investigationPromptContractRevision"] == "k10-research-3.6.1-b92"
    return database, task_id, exec_id, exec_rev


def _admit(database, task_id, service, key, *, wire=None, receipt_only=False):
    stage = {"tavily": "search", "model": "investigation"}.get(service, service)
    kwargs = dict(task_id=task_id, stage=stage, item_key=key, attempt_key=key,
        input_sha256=wire or sha256(key.encode()).hexdigest(),
        started_at=current.RUN_AT.isoformat(), db_path=database)
    if service == "model":
        return store.begin_model_external_attempt(**kwargs, reuse_scope_sha256="b" * 64,
            receipt_only=receipt_only)
    return store.begin_external_attempt(**kwargs)


def _settle(database, admission, code=None):
    assert admission["state"] == "started", admission
    store.settle_external_attempt(attempt_id=admission["attemptId"],
        outcome="failed" if code else "succeeded", usage=None,
        settled_at=current.RUN_AT.isoformat(), error_code=code, db_path=database)


def _worker(database, task_id, body):
    faults = []
    def handler(context):
        try:
            return body(context)
        except Exception as exc:
            faults.append(f"{type(exc).__name__}: {exc}")
            raise
    result = run_once(db_path=database, task_id=task_id, worker_id="v370-admission",
        lease_for=timedelta(minutes=5), clock=lambda: current.RUN_AT,
        handlers={"evening_scan": handler}, require_b76_contract=True)
    assert result is not None and result.status == "completed", faults
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT count(*) FROM k10_external_attempts "
            "WHERE state IN ('started','unknown')").fetchone()[0] == 0


@pytest.mark.parametrize("first_service", ["tavily", "model"])
@pytest.mark.parametrize("code", ["insufficient_balance", "provider_authorization_failed"])
def test_b92_worker_refusal_blocks_same_service_but_keeps_other_service(tmp_path, first_service, code):
    database, task_id, _, _ = _task(tmp_path)
    def body(context):
        assert context.task.task_id == task_id
        first = _admit(database, task_id, first_service, "first")
        _settle(database, first, code)
        other_service = "model" if first_service == "tavily" else "tavily"
        unrelated = _admit(database, task_id, other_service, "unrelated")
        # This assertion reproduces both original cross-provider global vetoes.
        assert unrelated["state"] == "started", unrelated
        _settle(database, unrelated)
        blocked = _admit(database, task_id, first_service, "same-service-new-input")
        assert blocked == {"state": "terminal", "reason": code, "attemptId": first["attemptId"]}
        return TaskResult("completed", "provider_scope_verified", context.checkpoint)
    _worker(database, task_id, body)


def _paid_reply(database, task_id, key="paid-wire"):
    request_sha = sha256(key.encode()).hexdigest()
    admitted = _admit(database, task_id, "model", key, wire=request_sha)
    assert admitted["state"] == "started"
    payload = {"receiptVersion": "v370-isolated-response", "ok": True, "content": "{}",
        "provider": "fixture", "model": "fixture-model", "promptTokens": 1,
        "completionTokens": 1, "totalTokens": 2, "usageUnavailable": False,
        "errorCode": None, "retryAfterSeconds": None, "finishReason": "stop",
        "rawResponses": [{"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}],
        "responseReceived": True, "rawReceiptOnly": False}
    store.settle_model_response_attempt(attempt_id=admitted["attemptId"],
        request_sha256=request_sha, reuse_scope_sha256="b" * 64, payload=payload,
        outcome="succeeded", usage={"promptTokens": 1, "completionTokens": 1, "totalTokens": 2,
            "searchRequests": None, "searchCredits": None},
        settled_at=current.RUN_AT.isoformat(), error_code=None, db_path=database)
    return admitted, request_sha


@pytest.mark.parametrize("refusal_service", ["model", "tavily"])
def test_b92_worker_paid_exact_reply_reusable_after_later_refusal(tmp_path, refusal_service):
    database, task_id, _, _ = _task(tmp_path)
    def body(context):
        paid, wire = _paid_reply(database, task_id)
        refused = _admit(database, task_id, refusal_service, "later-refusal")
        _settle(database, refused, "insufficient_balance")
        reused = _admit(database, task_id, "model", "new-parser", wire=wire)
        assert reused["state"] == "receipt_reused", reused
        assert reused["receiptAttemptId"] == paid["attemptId"]
        assert reused["receipt"]["rawResponses"][0]["choices"][0]["message"]["content"] == "{}"
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            assert connection.execute("SELECT count(*) FROM k10_external_attempts WHERE task_id=?",
                (task_id,)).fetchone()[0] == 2
        return TaskResult("completed", "provider_scope_verified", context.checkpoint)
    _worker(database, task_id, body)


def test_b92_worker_paid_search_identity_remains_reusable_after_refusal(tmp_path):
    database, task_id, _, _ = _task(tmp_path)
    def body(context):
        paid = _admit(database, task_id, "tavily", "already-searched")
        _settle(database, paid)
        refused = _admit(database, task_id, "tavily", "later-search-refusal")
        _settle(database, refused, "provider_authorization_failed")
        reused = _admit(database, task_id, "tavily", "already-searched")
        assert reused == {"state": "reused", "reason": None, "attemptId": paid["attemptId"]}
        with pytest.raises(store.K10Conflict, match="不同输入"):
            _admit(database, task_id, "tavily", "already-searched", wire="e" * 64)
        return TaskResult("completed", "provider_scope_verified", context.checkpoint)
    _worker(database, task_id, body)


def test_refusal_does_not_cross_a_second_real_cli_report(tmp_path):
    database, first_task, exec_id, exec_rev = _task(tmp_path)
    first = _admit(database, first_task, "model", "first-report-model")
    _settle(database, first, "insufficient_balance")
    second_task = current._cli("enqueue", "--db", str(database), "--kind", "morning",
        "--trading-day", current.DAY.isoformat(), "--config-id", "b92-isolated-run",
        "--config-revision", "1", "--execution-config-id", exec_id,
        "--execution-config-revision", str(exec_rev))
    assert second_task != first_task
    second = _admit(database, second_task, "model", "second-report-model")
    assert second["state"] == "started"
    _settle(database, second)


@pytest.mark.parametrize("foreign_stage", ["jin10:get_news", "tushare:major_news", "unregistered-service"])
def test_other_external_services_are_not_implicitly_models(tmp_path, foreign_stage):
    database, task_id, _, _ = _task(tmp_path)
    def body(context):
        failed = _admit(database, task_id, foreign_stage, "other-service")
        _settle(database, failed, "provider_authorization_failed")
        model = _admit(database, task_id, "model", "model-after-other-service")
        assert model["state"] == "started", model
        _settle(database, model)
        search = _admit(database, task_id, "tavily", "search-after-other-service")
        assert search["state"] == "started", search
        _settle(database, search)
        return TaskResult("completed", "provider_scope_verified", context.checkpoint)
    _worker(database, task_id, body)


def _legacy_child(database, task_id, parent_scan, exec_id, exec_rev, *, provider="fixture",
                  endpoint="https://fixture.invalid/chat", model="fixture-model"):
    # The current report producer owns reviews in its parent task. This isolated
    # prerequisite retains the older task-owned sibling boundary for ledger
    # compatibility; it is not evidence for current report production.
    store.enqueue_task(task_id=task_id, kind="morning_review", idempotency_key=task_id,
        input_version="isolated-legacy-child", input_cutoff_at=current.RUN_AT.isoformat(),
        payload={"parentScanId": parent_scan}, budget={}, created_at=current.RUN_AT.isoformat(),
        execution_binding={"configId": exec_id, "revision": exec_rev, "bindingKind": "scheduled"},
        db_path=database)
    configuration = store.read_run_config(config_id="b92-isolated-run", revision=1, db_path=database)
    resolution = resolve_deepseek_v4_pro(configuration=configuration["payload"], task="morning",
        db_path=database, task_id=task_id, provider_records=[ProviderRecord(
            id=0, name=provider, base_url=endpoint, model=model, api_key="fixture-never-sent",
            has_web_search=False, search_engine=None, notes=None, enabled=True,
            created_at=current.RUN_AT.isoformat(), updated_at=current.RUN_AT.isoformat())])
    assert resolution.state == "configured"


def _parent_scan(database, parent_task):
    # The production handler creates the parent scan binding itself. No source
    # adapter or credentials are provided; there is no input requiring a wire.
    run_once(db_path=database, task_id=parent_task, worker_id="v370-parent",
        lease_for=timedelta(minutes=5), clock=lambda: current.RUN_AT,
        handlers={"evening_scan": lambda context: pipeline.production_scan_handler(context,
            tushare_token=None, parquet_dir=database.parent / "parquet", now=lambda: current.RUN_AT)},
        require_b76_contract=True)
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT scan_id FROM k10_scan_execution_bindings WHERE task_id=?",
            (parent_task,)).fetchone()
    assert row is not None
    return row[0]


@pytest.mark.parametrize("refusal_service", ["model", "tavily", "jin10:get_news"])
def test_sibling_refusal_is_scoped_to_report_execution_and_provider(tmp_path, refusal_service):
    database, parent_task, exec_id, exec_rev = _task(tmp_path)
    parent_scan = _parent_scan(database, parent_task)
    for child in ("first-child", "same-report-child"):
        _legacy_child(database, child, parent_scan, exec_id, exec_rev)
    failed = _admit(database, "first-child", refusal_service, "sibling-refusal")
    _settle(database, failed, "insufficient_balance")
    for requested in ("model", "tavily"):
        admitted = _admit(database, "same-report-child", requested, "same-report-" + requested)
        if requested == refusal_service:
            assert admitted["state"] == "terminal", admitted
            assert admitted["attemptId"] == failed["attemptId"]
        else:
            assert admitted["state"] == "started", admitted
            _settle(database, admitted)
    _legacy_child(database, "other-provider", parent_scan, exec_id, exec_rev, provider="different-model-provider")
    other_provider = _admit(database, "other-provider", "model", "other-provider-model")
    assert other_provider["state"] == "started"
    _settle(database, other_provider)
    # A parent ID that is not a real scan never creates a sibling grouping key.
    _legacy_child(database, "unverified-parent", "another-report", exec_id, exec_rev)
    other_report = _admit(database, "unverified-parent", "model", "other-report-model")
    assert other_report["state"] == "started"
    _settle(database, other_report)
    execution = json.loads(current._cli("configure-execution", "--db", str(database),
        "--config-id", "other-frozen-execution", "--file",
        str(current.CONFIG_DIR / "k10-execution-v4.json")))
    _legacy_child(database, "different-execution", parent_scan,
        execution["configId"], execution["revision"])
    distinct = _admit(database, "different-execution", "model", "different-execution-model")
    assert distinct["state"] == "started"
    _settle(database, distinct)


def test_paid_exact_reply_is_reused_before_sibling_refusal(tmp_path):
    database, parent_task, exec_id, exec_rev = _task(tmp_path)
    parent_scan = _parent_scan(database, parent_task)
    for child in ("paid-child", "refusing-child"):
        _legacy_child(database, child, parent_scan, exec_id, exec_rev)
    paid, wire = _paid_reply(database, "paid-child")
    refused = _admit(database, "refusing-child", "model", "sibling-model-refusal")
    _settle(database, refused, "insufficient_balance")
    reused = _admit(database, "paid-child", "model", "new-parser", wire=wire)
    assert reused["state"] == "receipt_reused"
    assert reused["receiptAttemptId"] == paid["attemptId"]
    assert _admit(database, "paid-child", "model", "new-model-wire")["state"] == "terminal"


@pytest.mark.parametrize("changed", ["name", "endpoint", "model"])
def test_sibling_model_refusal_requires_all_provider_binding_fields(tmp_path, changed):
    database, parent_task, exec_id, exec_rev = _task(tmp_path)
    parent_scan = _parent_scan(database, parent_task)
    _legacy_child(database, "refusing-child", parent_scan, exec_id, exec_rev)
    refused = _admit(database, "refusing-child", "model", "model-refusal")
    _settle(database, refused, "provider_authorization_failed")
    values = {"provider": "fixture", "endpoint": "https://fixture.invalid/chat", "model": "fixture-model"}
    values[{"name": "provider"}.get(changed, changed)] += "-other"
    _legacy_child(database, "other-provider-binding", parent_scan, exec_id, exec_rev, **values)
    admitted = _admit(database, "other-provider-binding", "model", "distinct-model-binding")
    assert admitted["state"] == "started"
    _settle(database, admitted)


def test_model_refusal_does_not_cross_another_verified_parent_scan(tmp_path):
    database, parent_task, exec_id, exec_rev = _task(tmp_path)
    parent_scan = _parent_scan(database, parent_task)
    _legacy_child(database, "refusing-child", parent_scan, exec_id, exec_rev)
    refused = _admit(database, "refusing-child", "model", "model-refusal")
    _settle(database, refused, "insufficient_balance")
    another_task = current._cli("enqueue", "--db", str(database), "--kind", "evening",
        "--trading-day", current.DAY.isoformat(), "--config-id", "b92-isolated-run",
        "--config-revision", "1", "--execution-config-id", exec_id,
        "--execution-config-revision", str(exec_rev), "--bootstrap-cutoff",
        (current.SLOT - timedelta(days=1)).isoformat())
    another_scan = _parent_scan(database, another_task)
    assert another_scan != parent_scan
    _legacy_child(database, "another-parent-child", another_scan, exec_id, exec_rev)
    admitted = _admit(database, "another-parent-child", "model", "another-parent-model")
    assert admitted["state"] == "started"
    _settle(database, admitted)


def test_explicit_model_wire_namespace_covers_new_model_stages(tmp_path):
    database, task_id, _, _ = _task(tmp_path)
    first = store.begin_model_external_attempt(task_id=task_id, stage="new-model-stage",
        item_key="first", attempt_key="provider:isolated-new-stage", input_sha256="a" * 64,
        reuse_scope_sha256="b" * 64, started_at=current.RUN_AT.isoformat(), db_path=database)
    _settle(database, first, "insufficient_balance")
    assert _admit(database, task_id, "model", "new-model-input")["state"] == "terminal"
    search = _admit(database, task_id, "tavily", "unrelated-search")
    assert search["state"] == "started"
    _settle(database, search)


@pytest.mark.parametrize("service", ["model", "tavily"])
def test_uncertain_attempt_is_not_reclassified_as_a_settled_refusal(tmp_path, service):
    database, task_id, _, _ = _task(tmp_path)
    first = _admit(database, task_id, service, "uncertain")
    store.settle_external_attempt(attempt_id=first["attemptId"], outcome="unknown", usage=None,
        settled_at=current.RUN_AT.isoformat(), error_code="insufficient_balance", db_path=database)
    repeated = _admit(database, task_id, service, "uncertain")
    assert repeated["state"] == "pending_outcome"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT count(*) FROM k10_external_attempts WHERE task_id=?",
            (task_id,)).fetchone()[0] == 1


@pytest.mark.parametrize("state", ["started", "unknown"])
def test_unchanged_unknown_wire_and_attempt_identity_protection(tmp_path, state):
    database, task_id, _, _ = _task(tmp_path)
    first = _admit(database, task_id, "model", "pending")
    assert first["state"] == "started"
    if state == "unknown":
        store.settle_external_attempt(attempt_id=first["attemptId"], outcome="unknown", usage=None,
            settled_at=current.RUN_AT.isoformat(), error_code="provider_request_outcome_unknown", db_path=database)
    wire = sha256(b"pending").hexdigest()
    repeated = _admit(database, task_id, "model", "different-item", wire=wire)
    assert repeated["state"] == "pending_outcome"
    assert repeated["attemptId"] == first["attemptId"]
    with pytest.raises(store.K10Conflict, match="不同输入"):
        _admit(database, task_id, "model", "pending", wire="c" * 64)
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT count(*) FROM k10_external_attempts WHERE task_id=?",
            (task_id,)).fetchone()[0] == 1


def test_corrupted_paid_receipt_and_pause_remain_protected(tmp_path):
    database, task_id, _, _ = _task(tmp_path)
    paid, wire = _paid_reply(database, task_id)
    store.set_run_control(state="closed", reason_code="user_closed", changed_at=current.RUN_AT.isoformat(),
        changed_by="fixture", db_path=database)
    assert _admit(database, task_id, "model", "ordinary-paused", wire=wire)["state"] == "paused"
    reused = _admit(database, task_id, "model", "paid-wire", wire=wire, receipt_only=True)
    assert reused["state"] == "receipt_reused"
    assert _admit(database, task_id, "model", "never-paid", receipt_only=True)["state"] == "receipt_missing"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE k10_model_response_receipts SET payload_sha256=? WHERE attempt_id=?",
            ("0" * 64, paid["attemptId"]))
    assert _admit(database, task_id, "model", "paid-wire", wire=wire,
        receipt_only=True)["state"] == "receipt_invalid"


def test_corrupted_receipt_cannot_be_hidden_by_a_later_refusal(tmp_path):
    database, task_id, _, _ = _task(tmp_path)
    paid, wire = _paid_reply(database, task_id)
    failed = _admit(database, task_id, "model", "later-refusal")
    _settle(database, failed, "insufficient_balance")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE k10_model_response_receipts SET payload_sha256=? WHERE attempt_id=?",
            ("0" * 64, paid["attemptId"]))
    with pytest.raises(store.K10Conflict, match="哈希不匹配"):
        _admit(database, task_id, "model", "new-parser", wire=wire)
