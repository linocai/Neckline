"""B76 acceptance: full-scale, deterministic, no-network production entry paths.

The transport is synthetic by design.  These tests prove the release's local
execution, persistence, API and client-fixture boundary; they do not claim a
new provider/model quality, price, or notification delivery result.
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from types import SimpleNamespace

from neckline.k10 import pipeline, store
from neckline.k10.delivery import REPORT_DELIVERY_CONTRACT
from neckline.k10.schema import initialize_schema
from neckline.k10.worker import run_once

from .v340_acceptance_fixture import (
    NOW,
    RUN_AT,
    SELECTED_EVENT_COUNT,
    TITLE_BATCH_SIZE,
    TITLE_COUNT,
    active_bindings,
    actual_api,
    run_full_scale_flow,
)


def _report(flow):
    config_id, config_revision, execution_id, execution_revision = active_bindings(flow.db_path)
    with actual_api(flow.db_path, config_id=config_id, config_revision=config_revision,
                    execution_id=execution_id, execution_revision=execution_revision) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=evening")
    assert response.status_code == 200
    return response.json()


def _assert_no_inflight_external_attempts(flow) -> None:
    """A terminal B76 delivery may not leave an admitted model/search call running."""
    with sqlite3.connect(flow.db_path) as connection:
        inflight = connection.execute(
            "SELECT stage,item_key,state FROM k10_external_attempts "
            "WHERE state IN ('started','running','unknown') ORDER BY stage,item_key"
        ).fetchall()
    assert inflight == []


def _assert_full_scale_complete(flow) -> None:
    assert flow.task_status == "completed"
    assert flow.worker_passes == flow.continuation_count + 1
    assert flow.calls["titleBatch"] == TITLE_COUNT // TITLE_BATCH_SIZE + 1
    assert flow.calls["titleGlobal"] == 1
    assert flow.calls.get("titleReview", 0) == 0
    assert flow.calls["understand"] == SELECTED_EVENT_COUNT
    assert flow.calls["research:research_round"] == SELECTED_EVENT_COUNT * 2
    assert flow.calls["prioritize"] == 1
    assert len(flow.gateway_calls) == SELECTED_EVENT_COUNT
    assert len(set(flow.gateway_calls)) == SELECTED_EVENT_COUNT
    assert all(query.startswith("离线验收 event-") for query in flow.gateway_calls)
    with sqlite3.connect(flow.db_path) as connection:
        tavily_attempts = connection.execute(
            "SELECT state,count(*),sum(search_requests),sum(search_credits) "
            "FROM k10_external_attempts WHERE task_id=? AND stage='search' GROUP BY state",
            (flow.task_id,),
        ).fetchall()
    assert tavily_attempts == [("succeeded", SELECTED_EVENT_COUNT, SELECTED_EVENT_COUNT, SELECTED_EVENT_COUNT)]
    assert store.external_attempt_summary(task_id=flow.task_id, db_path=flow.db_path)["actualUsage"]["searchCredits"] == SELECTED_EVENT_COUNT
    _assert_no_inflight_external_attempts(flow)

    scan = store.get_scan(scan_id=flow.scan_id, db_path=flow.db_path)
    assert scan is not None
    coverage = scan["coverage"]
    assert coverage["titleDispositionCounts"] == {
        "input": TITLE_COUNT, "processed": TITLE_COUNT, "failed": 0, "unprocessed": 0,
    }
    assert coverage["researchEventCount"] == SELECTED_EVENT_COUNT
    assert coverage["titleSelectionManifestSha256"]

    envelope = _report(flow)
    assert envelope["schemaVersion"] == 9
    assert envelope["state"] == "available"
    report = envelope["report"]
    assert report is not None
    assert report["status"] == "completed"
    assert report["availableAt"]
    delivery = report["delivery"]
    assert delivery["contractVersion"] == REPORT_DELIVERY_CONTRACT
    assert delivery["outcome"] == "complete"
    assert delivery["rankingScope"] == "all_processed"
    assert delivery["counts"] == {
        "titleInput": TITLE_COUNT, "titleProcessed": TITLE_COUNT, "titleFailed": 0, "titleUnprocessed": 0,
        "eventInput": SELECTED_EVENT_COUNT, "eventProcessed": SELECTED_EVENT_COUNT,
        "eventFailed": 0, "eventUnprocessed": 0,
        "comparableCompanies": 30, "publishedCompanies": 30,
    }
    assert delivery["gaps"] == []
    assert len(delivery["inputManifestSha256"]) == 64
    assert len(delivery["eligibleSetSha256"]) == 64
    assert len(delivery["rankingInputSha256"]) == 64
    assert len(report["eveningCards"]) == 30


def test_b76_full_scale_cli_worker_complete_produces_actual_api_report(tmp_path, monkeypatch):
    _assert_full_scale_complete(run_full_scale_flow(tmp_path, monkeypatch, name="complete"))


def test_b76_full_scale_pipeline_slice_continues_same_task_after_completed_model_checkpoint(tmp_path, monkeypatch):
    """Cross the bound fixture slice only after a paid model checkpoint is durable.

    The bound fixture profile matches production's checked-in 110-second slice.
    This test reads that bound value and advances only ``pipeline.time.monotonic``
    one second past it. No task row, retry row, deadline, or production handler
    is patched.
    """
    db_path = tmp_path / "forced-continuation.sqlite"

    class SliceClock:
        completed_model_checkpoint_seen = False
        slice_seconds: int | None = None

        def monotonic(self) -> float:
            if not db_path.exists():
                return 0.0
            with sqlite3.connect(db_path) as connection:
                completed = connection.execute(
                    "SELECT count(*) FROM k10_execution_item_checkpoints "
                    "WHERE status='completed' AND network_attempt_count > 0"
                ).fetchone()[0]
                raw_slice_seconds = connection.execute(
                    "SELECT json_extract(c.payload_json, '$.discovery.taskSliceSeconds') "
                    "FROM k10_task_execution_bindings b JOIN k10_execution_config_revisions c "
                    "ON c.config_id=b.execution_config_id AND c.revision=b.execution_config_revision "
                    "LIMIT 1"
                ).fetchone()[0]
            assert isinstance(raw_slice_seconds, int) and raw_slice_seconds > 0
            self.slice_seconds = raw_slice_seconds
            if completed:
                self.completed_model_checkpoint_seen = True
                return float(raw_slice_seconds + 1)
            return 0.0

    clock = SliceClock()
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(monotonic=clock.monotonic))
    flow = run_full_scale_flow(tmp_path, monkeypatch, name="forced-continuation")

    assert clock.completed_model_checkpoint_seen
    assert clock.slice_seconds == 110
    assert flow.continuation_count == 1
    _assert_full_scale_complete(flow)


def test_b76_full_scale_complete_zero_cards_is_not_an_empty_report(tmp_path, monkeypatch):
    """All 1,602 frozen titles are handled even when none becomes a research event."""
    flow = run_full_scale_flow(tmp_path, monkeypatch, name="complete-zero", selected_event_count=0)

    assert flow.task_status == "completed"
    assert flow.calls["titleBatch"] == TITLE_COUNT // TITLE_BATCH_SIZE + 1
    assert flow.calls["titleGlobal"] == 1
    assert flow.calls.get("titleReview", 0) == 0
    assert flow.calls.get("understand", 0) == 0
    assert flow.calls.get("research:research_round", 0) == 0
    assert flow.calls.get("prioritize", 0) == 0
    assert flow.gateway_calls == ()

    envelope = _report(flow)
    assert envelope["schemaVersion"] == 9
    assert envelope["state"] == "available"
    report = envelope["report"]
    assert report is not None
    assert report["status"] == "completed"
    assert report["availableAt"]
    assert report["eveningCards"] == []
    delivery = report["delivery"]
    assert delivery["contractVersion"] == REPORT_DELIVERY_CONTRACT
    assert delivery["outcome"] == "complete"
    assert delivery["rankingScope"] == "all_processed"
    assert delivery["counts"] == {
        "titleInput": TITLE_COUNT, "titleProcessed": TITLE_COUNT, "titleFailed": 0, "titleUnprocessed": 0,
        "eventInput": 0, "eventProcessed": 0, "eventFailed": 0, "eventUnprocessed": 0,
        "comparableCompanies": 0, "publishedCompanies": 0,
    }
    assert delivery["gaps"] == []
    assert len(delivery["inputManifestSha256"]) == 64
    assert len(delivery["eligibleSetSha256"]) == 64
    assert len(delivery["rankingInputSha256"]) == 64


def test_b76_full_scale_single_content_refusal_publishes_isolated_partial_subset(tmp_path, monkeypatch):
    """A content refusal removes its linked company before a fresh subset ranking."""
    flow = run_full_scale_flow(tmp_path, monkeypatch, name="partial", refusal_event=1)

    assert flow.task_status == "completed"
    assert flow.worker_passes == flow.continuation_count + 1
    assert flow.calls["titleBatch"] == TITLE_COUNT // TITLE_BATCH_SIZE + 1
    assert flow.calls["understand"] == SELECTED_EVENT_COUNT
    assert flow.calls["research:research_round"] == SELECTED_EVENT_COUNT * 2
    assert flow.calls["prioritize"] == 1
    assert len(flow.gateway_calls) == SELECTED_EVENT_COUNT
    assert sum(kind == "research:research_round" and event == "event-001"
               for kind, event in flow.transport_calls) == 2
    _assert_no_inflight_external_attempts(flow)

    envelope = _report(flow)
    assert envelope["schemaVersion"] == 9
    assert envelope["state"] == "available"
    report = envelope["report"]
    assert report is not None
    assert report["status"] == "partial"
    assert report["availableAt"]
    # Both current report content and the list envelope disclose the gap.
    # Missing research must never look like an ordinary complete report.
    assert report["coverageGaps"]
    assert envelope["reason"] is not None
    assert envelope["reason"]["message"]
    delivery = report["delivery"]
    assert delivery["contractVersion"] == REPORT_DELIVERY_CONTRACT
    assert delivery["outcome"] == "partial"
    assert delivery["rankingScope"] == "completed_subset"
    assert delivery["counts"] == {
        "titleInput": TITLE_COUNT, "titleProcessed": TITLE_COUNT, "titleFailed": 0, "titleUnprocessed": 0,
        "eventInput": SELECTED_EVENT_COUNT, "eventProcessed": SELECTED_EVENT_COUNT - 1,
        "eventFailed": 1, "eventUnprocessed": 0,
        # The selection capacity is 30: excluding A promotes the next
        # independently completed company, rather than reducing the cap.
        "comparableCompanies": 30, "publishedCompanies": 30,
    }
    assert len(delivery["gaps"]) == 1
    gap = delivery["gaps"][0]
    assert gap["unitKind"] == "event"
    assert gap["reasonCode"] == "content_policy_refused"
    assert gap["companyScopeKnown"] is True
    assert flow.company_codes[0] in gap["companyCodes"]
    card_codes = {card["companyCode"] for card in report["eveningCards"]}
    # event-000 completed for A, event-001 refused for the same A; partial
    # publication cannot retain A by treating the successful sibling as enough.
    assert flow.company_codes[0] not in card_codes
    assert flow.company_codes[1] in card_codes
    assert len(delivery["inputManifestSha256"]) == 64
    assert len(delivery["eligibleSetSha256"]) == 64
    assert len(delivery["rankingInputSha256"]) == 64


def test_b76_global_priority_content_refusal_returns_actual_failed_report(tmp_path, monkeypatch):
    """A final-ranking refusal cannot be relabeled as a partial subset report."""
    flow = run_full_scale_flow(tmp_path, monkeypatch, name="failed", refusal_operation="prioritize",
                               expect_handler_failure=True)

    assert flow.task_status == "failed"
    assert flow.calls["titleBatch"] == TITLE_COUNT // TITLE_BATCH_SIZE + 1
    assert flow.calls["research:research_round"] == SELECTED_EVENT_COUNT * 2
    assert flow.calls["prioritize"] == 1
    _assert_no_inflight_external_attempts(flow)
    envelope = _report(flow)
    assert envelope["schemaVersion"] == 9
    assert envelope["state"] == "available"
    assert envelope["reason"] == {
        "reason": "incomplete", "message": "今天没跑成 · 处理未完成", "missing": [],
    }
    report = envelope["report"]
    assert report is not None
    assert report["status"] == "failed"
    assert report["availableAt"] is None
    assert report["eveningCards"] == []
    delivery = report["delivery"]
    assert delivery["contractVersion"] == REPORT_DELIVERY_CONTRACT
    assert delivery["outcome"] == "failed"
    assert delivery["rankingScope"] == "none"
    assert delivery["rankingInputSha256"] is None


def test_b76_partial_zero_cards_is_not_an_empty_report(tmp_path, monkeypatch):
    """An isolated failed only event leaves a diagnostic partial report, never empty."""
    flow = run_full_scale_flow(tmp_path, monkeypatch, name="partial-zero", selected_event_count=1,
                               refusal_event=0, all_events_same_company=True)

    assert flow.task_status == "completed"
    _assert_no_inflight_external_attempts(flow)
    envelope = _report(flow)
    assert envelope["schemaVersion"] == 9
    assert envelope["state"] == "available"
    report = envelope["report"]
    assert report is not None
    assert report["status"] == "partial"
    assert report["availableAt"]
    assert report["eveningCards"] == []
    assert report["coverageGaps"]
    assert envelope["reason"] is not None
    delivery = report["delivery"]
    assert delivery["contractVersion"] == REPORT_DELIVERY_CONTRACT
    assert delivery["outcome"] == "partial"
    assert delivery["rankingScope"] == "none"
    assert delivery["rankingInputSha256"] is None
    assert delivery["counts"] == {
        "titleInput": TITLE_COUNT, "titleProcessed": TITLE_COUNT, "titleFailed": 0, "titleUnprocessed": 0,
        "eventInput": 1, "eventProcessed": 0, "eventFailed": 1, "eventUnprocessed": 0,
        "comparableCompanies": 0, "publishedCompanies": 0,
    }
    assert len(delivery["gaps"]) == 1
    assert delivery["gaps"][0]["reasonCode"] == "content_policy_refused"


def test_b76_worker_refuses_malformed_contract_before_any_claim(tmp_path):
    """Automatic and task-id worker paths both leave non-B76 work untouched."""
    db_path = tmp_path / "contract-gate.sqlite"
    initialize_schema(db_path)
    store.set_run_control(state="open", reason_code="fixture_approved", changed_at=NOW.isoformat(),
                          changed_by="v340_acceptance", db_path=db_path)
    contracts = {
        "missing": None,
        "unknown": {
            "reportDelivery": REPORT_DELIVERY_CONTRACT,
            "research": "unknown-research-contract",
        },
        "extra": {
            "reportDelivery": REPORT_DELIVERY_CONTRACT,
            "research": "k10-research-3.4.0-b76",
            "unrecognized": "must-reject",
        },
    }
    task_ids = []
    for label, contract in contracts.items():
        task_id = f"task_v340_contract_{label}"
        payload = {} if contract is None else {"runtimeContract": contract}
        store.enqueue_task(
            task_id=task_id, kind="evening_scan", idempotency_key=f"v340-contract:{label}",
            input_version="offline-contract-gate", input_cutoff_at=RUN_AT.isoformat(), payload=payload,
            budget={"maxAttempts": 1}, created_at=NOW.isoformat(), db_path=db_path,
        )
        task_ids.append(task_id)

    def must_not_run(_context):
        raise AssertionError("malformed B76 contract must be rejected before handler execution")

    worker_kwargs = {
        "db_path": db_path, "worker_id": "v340-contract-gate", "lease_for": timedelta(minutes=1),
        "handlers": {"evening_scan": must_not_run}, "clock": lambda: RUN_AT,
        "require_b76_contract": True,
    }
    assert run_once(**worker_kwargs) is None
    for task_id in task_ids:
        assert run_once(**worker_kwargs, task_id=task_id) is None
        task = store.get_task(task_id=task_id, db_path=db_path)
        assert task is not None
        assert task.status == "queued"
        assert task.attempt_count == 0
        assert store.task_execution_input(task_id=task_id, db_path=db_path)["checkpoint"] == {}
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM k10_external_attempts").fetchone()[0] == 0
