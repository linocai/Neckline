"""Focused regressions for the B77 review repairs.

Each test owns a small isolated database and uses a deterministic transport;
none may contact a provider, APNs, or a production database.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api.k10 import create_router
from neckline.k10 import analysis, metering, store
from neckline.k10.delivery import runtime_contract
from neckline.k10.metering import MeteredProvider
from neckline.k10.pipeline import DeepSeekDiscoveryModel, _CheckpointedDiscoveryModel
from neckline.k10.providers import resolve_deepseek_v4_pro
from neckline.k10.investigation import decode_stage_result
from neckline.k10.research_contracts import QueryPath, Question, ResearchSnapshot, ResearchStageResult
from neckline.k10.research_store import (advance_research_snapshot, create_research_snapshot,
                                         mark_research_round_failed, read_research_state)
from neckline.k10.schema import initialize_schema
from neckline.llm.base import ChatMessage, LLMResult
from neckline.settings_store import ProviderRecord
from tests.k10_v306_fixture import append_approved_execution_profile


_NOW = "2026-09-20T04:10:00+00:00"


def _research_adapter(tmp_path, monkeypatch):
    db_path = tmp_path / "receipt-recovery.sqlite"
    initialize_schema(db_path)
    store.set_run_control(state="open", reason_code="offline_test", changed_at=_NOW,
                          changed_by="test_v341", db_path=db_path)
    store.enqueue_task(task_id="receipt-recovery", kind="evening_scan", idempotency_key="receipt-recovery",
                       input_version="fixture", input_cutoff_at=_NOW, payload={}, budget={},
                       created_at=_NOW, db_path=db_path)
    config_id, revision = append_approved_execution_profile(
        db_path=db_path, created_at=_NOW, config_id="receipt-recovery-profile",
    )
    store.bind_task_execution(task_id="receipt-recovery", execution_config_id=config_id,
                              execution_config_revision=revision, binding_kind="scheduled",
                              bound_at=_NOW, db_path=db_path)
    profile = store.task_execution_profile(task_id="receipt-recovery", db_path=db_path)

    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content":
                '{"action":"research_round","conclusion":{"researchStatus":"pending_verification","companyMappings":[]}}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 13, "completion_tokens": 8, "total_tokens": 21},
        })

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(**{**kwargs, "transport": httpx.MockTransport(respond)}))
    monkeypatch.setitem(metering._MODEL_CAPABILITIES, ("https://example.invalid/chat", "deepseek-flash"), {
        "contextTokens": 1_000_000, "maxOutputTokens": 384_000, "counter": "offline-test",
    })
    provider = MeteredProvider(ledger_db=db_path, ledger_task="receipt-recovery", api_key="fixture",
                               model="deepseek-flash", name="fixture", api_url="https://example.invalid/chat",
                               read_timeout=1, use_streaming=False)
    base = DeepSeekDiscoveryModel(provider)
    base.set_execution_policy(profile["payload"]["discovery"])
    adapter = _CheckpointedDiscoveryModel(
        base=base, task_id="receipt-recovery", execution_profile=profile,
        cutoff_at=datetime(2026, 9, 20, 4, 10, tzinfo=timezone.utc), db_path=db_path,
        leaseguard=None, allow_failed_research_resume=True,
    )
    store.append_event_revision(event_id="event-recovery", stable_key="fixture:event-recovery",
                                headline="receipt recovery", event_kind="fixture", facts={}, source_refs=[],
                                supersedes_revision=None, created_at=_NOW, db_path=db_path)
    snapshot = ResearchSnapshot("snapshot-recovery", "receipt-recovery", "event-recovery", 1,
                                _NOW, _NOW, "a" * 64,
                                profile["payload"]["discovery"]["investigationPromptContractRevision"],
                                "b" * 64, "continue_research", "ok", 1, _NOW, _NOW)
    packet = {"allowedEvidenceRefs": [], "contextProtocol": "k10-v2-context-3.2.1",
              "companyScope": {"fixedPool": []}, "claims": [], "questions": [],
              "openQuestionIds": [], "attemptedPathSignatures": []}
    return db_path, adapter, snapshot, packet, calls


def test_running_compound_checkpoint_recovers_only_its_committed_exact_receipt(tmp_path, monkeypatch):
    db_path, adapter, snapshot, packet, calls = _research_adapter(tmp_path, monkeypatch)
    original = store.record_execution_checkpoint
    tripped = False

    def interrupt_after_receipt(**kwargs):
        nonlocal tripped
        if (kwargs.get("stage") == "model:investigation_research_round"
                and kwargs.get("status") == "completed" and not tripped):
            tripped = True
            raise sqlite3.OperationalError("test interruption after committed receipt")
        return original(**kwargs)

    monkeypatch.setattr(store, "record_execution_checkpoint", interrupt_after_receipt)
    with pytest.raises(sqlite3.OperationalError, match="after committed receipt"):
        adapter.advance_research_round(snapshot=snapshot, evidence_packet=packet)
    monkeypatch.setattr(store, "record_execution_checkpoint", original)

    recovered = adapter.advance_research_round(snapshot=snapshot, evidence_packet=packet)
    assert recovered.action == "research_round"
    assert calls == ["/chat"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status,attempt_count,network_attempt_count FROM k10_execution_item_checkpoints").fetchall() == [
            ("completed", 1, 1),
        ]
        assert conn.execute("SELECT state,count(*) FROM k10_external_attempts GROUP BY state").fetchall() == [
            ("succeeded", 1),
        ]
        assert conn.execute("SELECT count(*) FROM k10_model_response_receipts").fetchone()[0] == 1


@pytest.mark.parametrize("receipt_mutation", ["missing", "corrupt"])
def test_running_checkpoint_without_a_trusted_receipt_stays_blocked_without_post(tmp_path, monkeypatch, receipt_mutation):
    db_path, adapter, snapshot, packet, calls = _research_adapter(tmp_path, monkeypatch)
    original = store.record_execution_checkpoint

    def interrupt_after_receipt(**kwargs):
        if kwargs.get("stage") == "model:investigation_research_round" and kwargs.get("status") == "completed":
            raise sqlite3.OperationalError("test interruption after committed receipt")
        return original(**kwargs)

    monkeypatch.setattr(store, "record_execution_checkpoint", interrupt_after_receipt)
    with pytest.raises(sqlite3.OperationalError):
        adapter.advance_research_round(snapshot=snapshot, evidence_packet=packet)
    monkeypatch.setattr(store, "record_execution_checkpoint", original)
    with sqlite3.connect(db_path) as conn:
        if receipt_mutation == "missing":
            conn.execute("DELETE FROM k10_model_response_receipts")
        else:
            conn.execute("UPDATE k10_model_response_receipts SET payload_sha256=?", ("0" * 64,))

    with pytest.raises(Exception) as raised:
        adapter.advance_research_round(snapshot=snapshot, evidence_packet=packet)
    assert getattr(raised.value, "code", None) == "model_request_outcome_unknown"
    assert calls == ["/chat"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status,attempt_count,network_attempt_count FROM k10_execution_item_checkpoints").fetchone() == (
            "running", 1, 1,
        )
        assert conn.execute("SELECT count(*) FROM k10_external_attempts").fetchone()[0] == 1



def _failure_receipt(*, code: str) -> dict[str, object]:
    return {
        "receiptVersion": "k10-model-response-receipt-v1", "ok": False, "content": "",
        "provider": "fixture", "model": "deepseek-flash", "promptTokens": None,
        "completionTokens": None, "totalTokens": None, "usageUnavailable": True,
        "errorCode": code, "retryAfterSeconds": None, "finishReason": None,
        "rawResponses": [], "responseReceived": False, "rawReceiptOnly": False,
    }


def _provider_record(*, name: str = "fixture") -> ProviderRecord:
    return ProviderRecord(1, name, "https://api.deepseek.com", "deepseek-flash", "offline-key",
                          False, None, None, True, _NOW, _NOW)


def _morning_provider_configuration() -> dict[str, object]:
    return {"modelRoutes": {"morning": "deepseek-v4-pro"},
            "taskPolicies": {"morning": {"timeoutSeconds": 1, "modelMaxAttempts": 1}}}


def _parented_morning_children(tmp_path, *, name: str, profile_id: str = "report-profile", db_path=None,
                               execution_binding=None):
    if db_path is None:
        db_path = tmp_path / (name + ".sqlite")
        initialize_schema(db_path)
        store.set_run_control(state="open", reason_code="offline_test", changed_at=_NOW,
                              changed_by="test_v341", db_path=db_path)
    execution_id, execution_revision = execution_binding or append_approved_execution_profile(
        db_path=db_path, created_at=_NOW, config_id=profile_id,
    )
    parent_task, scan_id = "parent-" + name, "scan-" + name
    store.enqueue_task(task_id=parent_task, kind="morning_scan", idempotency_key=parent_task,
                       input_version="fixture", input_cutoff_at=_NOW,
                       payload={"windowKind": "morning", "runtimeContract": runtime_contract()},
                       budget={"maxAttempts": 1}, created_at=_NOW, db_path=db_path)
    store.bind_task_execution(task_id=parent_task, execution_config_id=execution_id,
                              execution_config_revision=execution_revision, binding_kind="scheduled",
                              bound_at=_NOW, db_path=db_path)
    store.create_scan(scan_id=scan_id, window_kind="morning", cutoff_at=_NOW, config_id=None,
                      config_revision=None, status="running", coverage={}, created_at=_NOW,
                      completed_at=None, db_path=db_path)
    store.bind_scan_execution(scan_id=scan_id, task_id=parent_task, execution_config_id=execution_id,
                              execution_config_revision=execution_revision, binding_kind="scheduled",
                              bound_at=_NOW, db_path=db_path)
    child_ids = []
    for suffix in ("first", "sibling", "tavily"):
        task_id = f"{name}-{suffix}"
        store.enqueue_task(task_id=task_id, kind="morning_review", idempotency_key=task_id,
                           input_version="fixture", input_cutoff_at=_NOW,
                           payload={"parentScanId": scan_id, "runtimeContract": runtime_contract()},
                           budget={"maxAttempts": 1}, created_at=_NOW, db_path=db_path)
        store.bind_task_execution(task_id=task_id, execution_config_id=execution_id,
                                  execution_config_revision=execution_revision, binding_kind="scheduled",
                                  bound_at=_NOW, db_path=db_path)
        child_ids.append(task_id)
    return db_path, execution_id, execution_revision, scan_id, tuple(child_ids)


@pytest.mark.parametrize("failure_code", ["provider_authorization_failed", "insufficient_balance"])
def test_same_verified_morning_parent_blocks_only_its_provider_scope(tmp_path, failure_code):
    db_path, execution_id, execution_revision, _scan_id, (first, sibling, tavily_child) = _parented_morning_children(
        tmp_path, name="same-parent",
    )
    configuration = _morning_provider_configuration()
    first_resolution = resolve_deepseek_v4_pro(configuration=configuration, task="morning", db_path=db_path,
                                                provider_records=[_provider_record()], task_id=first)
    sibling_resolution = resolve_deepseek_v4_pro(configuration=configuration, task="morning", db_path=db_path,
                                                  provider_records=[_provider_record()], task_id=sibling)
    assert first_resolution.state == sibling_resolution.state == "configured"
    started = store.begin_model_external_attempt(
        task_id=first, stage="morning", item_key="first", attempt_key="first-wire",
        input_sha256="a" * 64, reuse_scope_sha256="b" * 64, started_at=_NOW, db_path=db_path,
    )
    assert started["state"] == "started"
    assert store.settle_model_response_attempt(
        attempt_id=str(started["attemptId"]), request_sha256="a" * 64, reuse_scope_sha256="b" * 64,
        payload=_failure_receipt(code=failure_code), outcome="failed", usage=None,
        settled_at=_NOW, error_code=failure_code, record_provider_failure=True,
        db_path=db_path,
    )["state"] == "failed"

    provider = sibling_resolution.provider
    assert isinstance(provider, MeteredProvider)
    profile = store.task_execution_profile(task_id=sibling, db_path=db_path)
    assert profile is not None
    provider.bind_execution_spending(task_id=sibling, execution_profile=profile)
    with provider.spend_context(task_id=sibling, stage="morning", item_key="sibling", attempt=1):
        attempt_id, code, receipt = provider._begin_attempt(
            args=([ChatMessage("user", "offline")],),
            kwargs={"model_options": profile["payload"]["discovery"]["modelOptions"]["investigation"]},
        )
    assert (attempt_id, code, receipt) == (None, failure_code, None)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM k10_external_attempts").fetchone()[0] == 1

    # A Tavily admission under the same report remains independent from the
    # model credential failure and is allowed to reserve its own attempt.
    tavily = store.begin_external_attempt(task_id=tavily_child, stage="search", item_key="tavily",
                                          attempt_key="tavily-wire", input_sha256="c" * 64,
                                          started_at=_NOW, db_path=db_path)
    assert tavily["state"] == "started"
    assert store.settle_external_attempt(attempt_id=str(tavily["attemptId"]), outcome="failed", usage=None,
                                         settled_at=_NOW, error_code="provider_authorization_failed",
                                         record_provider_failure=False, db_path=db_path)["state"] == "failed"

    # The same binding on a different real parent scan has no inherited block.
    _, _, _, _, (other_first, _, _) = _parented_morning_children(
        tmp_path, name="other-parent", db_path=db_path,
        execution_binding=(execution_id, execution_revision),
    )
    other_resolution = resolve_deepseek_v4_pro(configuration=configuration, task="morning", db_path=db_path,
                                                provider_records=[_provider_record()], task_id=other_first)
    assert other_resolution.state == "configured"
    assert store.begin_model_external_attempt(
        task_id=other_first, stage="morning", item_key="other", attempt_key="other-wire",
        input_sha256="d" * 64, reuse_scope_sha256="e" * 64, started_at=_NOW, db_path=db_path,
    )["state"] == "started"

    # A child with a different frozen binding is not in the failed provider
    # scope even when it genuinely belongs to this same parent scan.
    other_binding = append_approved_execution_profile(
        db_path=db_path, created_at=_NOW, config_id="other-report-profile",
    )
    differently_bound = "same-parent-other-binding"
    store.enqueue_task(task_id=differently_bound, kind="morning_review", idempotency_key=differently_bound,
                       input_version="fixture", input_cutoff_at=_NOW,
                       payload={"parentScanId": _scan_id, "runtimeContract": runtime_contract()},
                       budget={"maxAttempts": 1}, created_at=_NOW, db_path=db_path)
    store.bind_task_execution(task_id=differently_bound, execution_config_id=other_binding[0],
                              execution_config_revision=other_binding[1], binding_kind="scheduled",
                              bound_at=_NOW, db_path=db_path)
    assert store.begin_model_external_attempt(
        task_id=differently_bound, stage="morning", item_key="other-binding", attempt_key="other-binding-wire",
        input_sha256="f" * 64, reuse_scope_sha256="g" * 64, started_at=_NOW, db_path=db_path,
    )["state"] == "started"


def test_analysis_keeps_authorization_reason_from_provider_result():
    class RefusedProvider:
        def chat(self, _messages, **_kwargs):
            return LLMResult(ok=False, content="", provider="fixture", model="deepseek-flash",
                             error_code="provider_authorization_failed")

    text, provider, model, message, _usage, code, retry_after = analysis._call(
        RefusedProvider(), [ChatMessage("user", "offline")], model_options={"maxTokens": 1},
    )
    assert (text, provider, model, code, retry_after) == ("", "fixture", "deepseek-flash",
                                                            "provider_authorization_failed", None)
    assert message == "模型服务鉴权或权限校验失败，任务已停止"


def _write_counts(db_path):
    with sqlite3.connect(db_path) as conn:
        return tuple(
            int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("k10_tasks", "k10_task_execution_bindings", "k10_task_outbox",
                          "k10_company_window_actions", "k10_company_window_observations",
                          "k10_analysis_requests", "k10_task_retry_schedules")
        )


def test_closed_control_rejects_real_api_producers_and_legacy_retry_without_writes(tmp_path, monkeypatch):
    """Exercise selection/follow-up/retry through the public router, not store SQL."""
    from tests.k10_v302_fixture import build_fixture
    from tests.test_k10_api import _freeze_k10_clocks
    from neckline.api import k10 as k10_api

    db_path = tmp_path / "closed-api-producers.sqlite"
    ids = build_fixture(db_path)
    profile = store.task_execution_profile(task_id="fixture-analysis-1", db_path=db_path)
    assert profile is not None
    # Seed a terminal old-protocol task while the fixture control is still open.
    store.enqueue_task(task_id="legacy-retry", kind="analysis", idempotency_key="legacy-retry",
                       input_version="legacy", input_cutoff_at=_NOW, payload={"legacy": True}, budget={},
                       created_at=_NOW, db_path=db_path)
    claimed = store.claim_task_by_id(task_id="legacy-retry", worker_id="fixture", now=datetime.fromisoformat(_NOW),
                                     lease_for=__import__("datetime").timedelta(minutes=1), db_path=db_path)
    assert claimed is not None
    store.finish_task(task_id="legacy-retry", worker_id="fixture", status="failed", stage="fixture",
                      checkpoint={}, error_text="fixture", finished_at=datetime.fromisoformat(_NOW), db_path=db_path)
    _freeze_k10_clocks(monkeypatch, "2026-08-31T01:20:00+00:00")
    app = FastAPI()
    app.include_router(create_router(
        lambda: db_path, lambda: None, lambda: tmp_path / "parquet",
        current_execution_config_binding_provider=lambda: (profile["configId"], profile["revision"], None),
    ))
    store.set_run_control(state="closed", reason_code="offline_pause", changed_at=_NOW,
                          changed_by="test_v341", db_path=db_path)
    before = _write_counts(db_path)
    with TestClient(app) as client:
        # Existing, fully bound observe action is a read-only idempotent replay.
        replay = client.post(f"/api/v1/k10/company-windows/{ids['primaryWindowId']}/selection",
                             json={"action": "keep", "idempotencyKey": "fixture-keep"})
        blocked_selection = client.post(f"/api/v1/k10/company-windows/{ids['unhandledWindowId']}/selection",
                                        json={"action": "keep", "idempotencyKey": "closed-new-keep"})
        blocked_follow_up = client.post(f"/api/v1/k10/company-windows/{ids['primaryWindowId']}/analysis-requests",
                                        json={"kind": "user_question", "question": "新增问题", "sourceRefs": [],
                                              "idempotencyKey": "closed-follow-up"})
        legacy_retry = client.post("/api/v1/k10/jobs/legacy-retry/retry", json={"expectedAttemptCount": 1})
    assert replay.status_code == 200 and replay.json()["replayed"] is True, replay.text
    assert blocked_selection.status_code == blocked_follow_up.status_code == legacy_retry.status_code == 409
    assert "旧协议任务不能重试" in legacy_retry.json()["detail"]["message"]
    assert _write_counts(db_path) == before
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status,stage,error_text FROM k10_tasks WHERE task_id='legacy-retry'").fetchone() == (
            "failed", "fixture", "fixture",
        )


def test_semantic_route_identity_survives_real_decoder_and_persistence(tmp_path):
    from neckline.k10.research_context import collapse_repeated_work, question_dependency

    question = {
        "questionId": "q-1", "question": "是否形成订单", "claimIds": ["claim-1"],
        "companyCodes": ["300001.SZ"], "knownEvidence": [{"documentId": "doc-1", "revision": 1}],
        "supportCondition": "订单确认", "refuteCondition": "订单否认", "state": "open",
        "missingEvidence": ["订单确认"], "decisionImpact": "影响比较", "resumeCondition": "公司确认",
    }
    def raw_path(path_id, *, query, source_locator=None, state="planned", target_source="公司公告"):
        path = {
        "pathId": path_id, "questionId": "q-1", "query": query, "intent": "核对订单",
        "targetSource": target_source, "newPathReason": "已有来源需要核对", "expectedInformationGain": "订单状态",
        "expectedJudgmentChange": "改变比较", "purposeKind": "event_fact",
        "targetRefs": [{"kind": "claim", "claimId": "claim-1"}], "state": state,
        }
        if source_locator is not None:
            path["sourceLocator"] = source_locator
        return path

    decode_packet = {"contextProtocol": "k10-v2-context-3.2.1", "allowedEvidenceRefs": [question["knownEvidence"][0]],
                     "openQuestionIds": ["q-1"], "attemptedPathSignatures": [], "_localState": {"questions": [question]}}
    old = raw_path("old", query="公告 订单", source_locator={"documentId": "doc-1", "revision": 1, "locator": "p:1"})
    decoded = decode_stage_result({"action": "plan_queries", "queryPaths": [old]},
                                  action="plan_queries", evidence_packet=decode_packet)
    assert decoded.query_paths[0].to_dict()["sourceLocator"] == old["sourceLocator"]

    # Persist the decoded route through the real Schema-9 stage writer and
    # recover it as a route, rather than comparing an ad-hoc response dict.
    db_path = tmp_path / "route-identity.sqlite"
    initialize_schema(db_path)
    store.set_run_control(state="open", reason_code="offline_test", changed_at=_NOW,
                          changed_by="test_v341", db_path=db_path)
    store.enqueue_task(task_id="route-task", kind="evening_scan", idempotency_key="route-task",
                       input_version="fixture", input_cutoff_at=_NOW, payload={}, budget={},
                       created_at=_NOW, db_path=db_path)
    store.append_document_version(document_id="doc-1", source_key="fixture", external_id="doc-1",
                                  canonical_url="https://example.invalid/doc-1", content_sha256="f" * 64,
                                  published_at=_NOW, published_precision="exact", fetched_at=_NOW,
                                  original_text="frozen evidence", excerpt="evidence", fetch_version="fixture",
                                  metadata={}, created_at=_NOW, db_path=db_path)
    store.append_event_revision(event_id="route-event", stable_key="fixture:route-event", headline="订单事件",
                                event_kind="disclosure", facts={}, source_refs=[question["knownEvidence"][0]],
                                supersedes_revision=None, created_at=_NOW, db_path=db_path)
    snapshot = ResearchSnapshot("route-snapshot", "route-task", "route-event", 1, _NOW, _NOW,
                                "a" * 64, "fixture", "b" * 64, "continue_research", "ok", 1, _NOW, _NOW)
    create_research_snapshot(snapshot=snapshot, db_path=db_path)
    current = advance_research_snapshot(
        snapshot_id=snapshot.snapshot_id, expected_revision=1, research_status="continue_research",
        execution_status="ok", stage_result=ResearchStageResult("plan_gaps", questions=(Question.from_dict(question),)),
        input_sha256="c" * 64, updated_at=_NOW, db_path=db_path,
    )
    current = advance_research_snapshot(
        snapshot_id=snapshot.snapshot_id, expected_revision=current.revision, research_status="continue_research",
        execution_status="ok", stage_result=decoded, input_sha256="d" * 64, updated_at=_NOW, db_path=db_path,
    )
    searched = ResearchStageResult("assess_evidence", query_paths=(
        decoded.query_paths[0].__class__.from_dict({**decoded.query_paths[0].to_dict(), "state": "searched"}),
    ))
    advance_research_snapshot(
        snapshot_id=snapshot.snapshot_id, expected_revision=current.revision, research_status="continue_research",
        execution_status="ok", stage_result=searched, input_sha256="e" * 64, updated_at=_NOW, db_path=db_path,
    )
    state = read_research_state(snapshot_id=snapshot.snapshot_id, db_path=db_path)
    assert state is not None and state["paths"] == [searched.query_paths[0].to_dict()]

    rewritten = raw_path("rewritten", query="公司最新披露是否确认订单",
                         source_locator={"documentId": "doc-1", "revision": 1, "locator": "p:2"})
    rewritten_decoded = decode_stage_result({"action": "plan_queries", "queryPaths": [rewritten]},
                                            action="plan_queries", evidence_packet=decode_packet)
    persisted_packet = {"contextProtocol": "k10-v2-context-3.2.1", "_localState": {
        "questions": state["questions"], "queryPaths": state["paths"],
        "pathDependencies": {"old": question_dependency(state["questions"][0])},
    }}
    assert collapse_repeated_work({"queryPaths": [rewritten_decoded.query_paths[0].to_dict()]}, persisted_packet)["queryPaths"] == [
        rewritten_decoded.query_paths[0].to_dict(),
    ]

    # Existing routes have no typed locator.  Query, intent, source labels and
    # even an unverified URL stay non-identities, so they cannot reopen search.
    legacy_old = raw_path("legacy-old", query="公告 订单", state="searched")
    legacy_rewrite = raw_path("legacy-new", query="订单最新披露", target_source="https://unverified.invalid/order")
    legacy_packet = {"contextProtocol": "k10-v2-context-3.2.1", "_localState": {
        "questions": [question], "queryPaths": [legacy_old],
        "pathDependencies": {"legacy-old": question_dependency(question)},
    }}
    assert collapse_repeated_work({"queryPaths": [legacy_rewrite]}, legacy_packet)["queryPaths"] == []

    # A frozen source revision is an actual increment even with no locator.
    revised_question = {**question, "knownEvidence": [{"documentId": "doc-1", "revision": 2}]}
    revised_packet = {"contextProtocol": "k10-v2-context-3.2.1", "_localState": {
        "questions": [revised_question], "queryPaths": [legacy_old],
        "pathDependencies": {"legacy-old": question_dependency(question)},
    }}
    assert collapse_repeated_work({"queryPaths": [legacy_rewrite]}, revised_packet)["queryPaths"] == [legacy_rewrite]


def _direct_path_identity_runtime(db_path):
    from neckline.k10.research_runtime import _Investigation

    runtime = object.__new__(_Investigation)
    runtime.db_path = db_path
    runtime.documents = {}
    return runtime


def _direct_question_and_path(document_id, *, question_id):
    question = Question(
        question_id, ("claim-1",), ("300001.SZ",), "是否形成订单",
        ({"documentId": document_id, "revision": 1},), ("订单确认",), "订单确认", "订单否认",
        "影响比较", "open", "出现公司公告",
    )
    path = QueryPath(
        "path-" + question_id, question_id, "订单公告", "核对订单", "公司公告", "需要确认",
        "订单状态", "改变比较", "planned", purpose_kind="event_fact",
        target_refs=({"kind": "claim", "claimId": "claim-1"},),
    )
    return question, path


def test_direct_route_identity_treats_changed_body_as_new_visible_evidence(tmp_path):
    db_path = tmp_path / "direct-revision.sqlite"
    initialize_schema(db_path)
    for document_id, body in (("doc-r1", "订单仍待确认"), ("doc-r2", "公司已确认订单")):
        store.append_document_version(
            document_id=document_id, source_key="fixture", external_id=document_id,
            canonical_url="https://example.invalid/" + document_id,
            content_sha256=("1" if document_id == "doc-r1" else "2") * 64,
            published_at=_NOW, published_precision="exact", fetched_at=_NOW,
            original_text=body, excerpt=body, fetch_version="fixture", metadata={},
            created_at=_NOW, db_path=db_path,
        )
    runtime = _direct_path_identity_runtime(db_path)
    first_question, first_path = _direct_question_and_path("doc-r1", question_id="q-before")
    revised_question, revised_path = _direct_question_and_path("doc-r2", question_id="q-after")

    assert runtime._b78_path_identity(first_path, first_question) != runtime._b78_path_identity(
        revised_path, revised_question,
    )


def test_direct_route_identity_collapses_same_body_from_different_document_ids(tmp_path):
    db_path = tmp_path / "direct-syndicated.sqlite"
    initialize_schema(db_path)
    for document_id in ("publisher-copy", "syndicated-copy"):
        store.append_document_version(
            document_id=document_id, source_key="fixture", external_id=document_id,
            canonical_url="https://example.invalid/" + document_id,
            content_sha256=("a" if document_id == "publisher-copy" else "b") * 64,
            published_at=_NOW, published_precision="exact", fetched_at=_NOW,
            original_text="相同的订单公告正文", excerpt="相同的订单公告正文", fetch_version="fixture",
            metadata={"url": "different"}, created_at=_NOW, db_path=db_path,
        )
    runtime = _direct_path_identity_runtime(db_path)
    original_question, original_path = _direct_question_and_path("publisher-copy", question_id="q-source")
    syndicated_question, syndicated_path = _direct_question_and_path("syndicated-copy", question_id="q-copy")

    assert runtime._b78_path_identity(original_path, original_question) == runtime._b78_path_identity(
        syndicated_path, syndicated_question,
    )


@pytest.mark.parametrize("receipt_scope", ["valid", "wrong"])
def test_failed_research_contract_shape_replays_original_db_receipt_without_post(tmp_path, monkeypatch, receipt_scope):
    """A B82 prompt-shape change recovers the exact old paid reply locally.

    The first operation is a real ``MeteredProvider`` request.  We then mark
    that completed derivative as the explicitly authorised semantic failure
    that a recovery producer would have frozen.  Changing the request payload
    proves recovery cannot find it by rebuilding the new wire: it must locate
    the old task/stage/natural-item receipt and feed its raw response through
    the current parser.  No diagnostics sidecar participates.
    """
    db_path, adapter, snapshot, packet, calls = _research_adapter(tmp_path, monkeypatch)
    create_research_snapshot(snapshot=snapshot, db_path=db_path)
    original = adapter.advance_research_round(snapshot=snapshot, evidence_packet=packet)
    assert original.action == "research_round" and calls == ["/chat"]

    operation, item_key, _item, old_digest, ledger_key, _row = adapter._research_operation_target(
        snapshot=snapshot, action="research_round", evidence_packet=packet,
    )
    assert adapter.reject_research_result(
        snapshot=snapshot, action="research_round", evidence_packet=packet,
        safe_error_code="investigation_result_invalid",
    )
    failed_snapshot = mark_research_round_failed(
        snapshot_id=snapshot.snapshot_id, expected_revision=snapshot.revision,
        input_packet=packet, safe_error_code="investigation_result_invalid",
        updated_at=_NOW, db_path=db_path,
    )
    with sqlite3.connect(db_path) as connection:
        checkpoint = json.loads(connection.execute(
            "SELECT checkpoint_json FROM k10_tasks WHERE task_id='receipt-recovery'"
        ).fetchone()[0])
        checkpoint["recoveryAuthorized"] = {"failedModelInputSha256": [old_digest]}
        connection.execute(
            "UPDATE k10_tasks SET checkpoint_json=? WHERE task_id='receipt-recovery'",
            (json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
        )
        # The receipt is tied to the original identity; a neighbouring natural
        # item remains ineligible even before a current shape is constructed.
        assert store.load_model_response_receipts_for_operation(
            task_id="receipt-recovery", stage="investigation",
            item_key=f"{operation}:{item_key}-similar:{old_digest}", db_path=db_path,
        ) == ()
        if receipt_scope == "wrong":
            connection.execute("UPDATE k10_model_response_receipts SET reuse_scope_sha256=?", ("0" * 64,))

    # The failed-round packet and B81 frozen renderer reconstruct the old
    # wire/scope before the current local decoder is called.  The derived
    # recovery checkpoint has a different digest; socket use remains denied.
    if receipt_scope == "wrong":
        with pytest.raises(Exception) as rejected:
            adapter.advance_research_round(snapshot=failed_snapshot, evidence_packet=packet)
        assert getattr(rejected.value, "code", None) == "provider_response_receipt_unverifiable"
    else:
        recovered = adapter.advance_research_round(snapshot=failed_snapshot, evidence_packet=packet)
        assert recovered.action == "research_round"
    assert calls == ["/chat"]
    with sqlite3.connect(db_path) as connection:
        receipt_payload = json.loads(connection.execute(
            "SELECT payload_json FROM k10_model_response_receipts"
        ).fetchone()[0])
        assert receipt_payload["replayMetadata"] == {
            "rendererRevision": "k10-investigation-v1", "repairFeedback": None,
        }
        rows = connection.execute(
            "SELECT status,input_sha256,safe_error_ref FROM k10_execution_item_checkpoints "
            "WHERE task_id='receipt-recovery' AND stage='model:investigation_research_round' ORDER BY updated_at"
        ).fetchall()
        assert len(rows) == (1 if receipt_scope == "wrong" else 2)
        old_row = next(row for row in rows if row[1] == old_digest)
        assert old_row == ("failed", old_digest, ledger_key)
        if receipt_scope == "valid":
            current_row = next(row for row in rows if row[1] != old_digest)
            assert current_row[0] == "completed" and current_row[2] is None
        assert connection.execute(
            "SELECT state,count(*) FROM k10_external_attempts GROUP BY state"
        ).fetchall() == [("succeeded", 1)]
