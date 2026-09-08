"""Regression coverage for the Build 35 read contract."""
from __future__ import annotations

from hashlib import sha256

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from neckline.api.k10 import create_router
from neckline.k10 import store
from neckline.k10.evaluation import evaluate_company_window, evaluation_state


def _client(path, tmp_path):
    app = FastAPI()
    app.include_router(create_router(
        lambda: path, lambda: None, lambda: tmp_path / "parquet",
        current_config_binding_provider=lambda: ("cfg-fixture", 1, None),
    ))
    return TestClient(app)


@pytest.mark.parametrize("persisted_kind", ["completed", "incomplete"])
def test_v304_results_keep_an_unconfigured_window_visible_without_scoring_it(tmp_path, persisted_kind):
    from tests.k10_v304_fixture import build_fixture

    path = tmp_path / "configuration-gap.sqlite"
    ids = build_fixture(path)
    with _client(path, tmp_path) as client:
        baseline = client.get("/api/v1/k10/results").json()
    window = next(item for item in store.list_company_windows(db_path=path)
                  if item["companyWindowId"] == ids["unconfiguredWindowId"])
    facts = (store.list_market_day_facts(company_code=window["companyCode"], db_path=path)
             if persisted_kind == "completed" else [])
    persisted = evaluate_company_window(window=window, market_facts=facts,
                                        as_of="2026-09-04T16:00:00+08:00")
    assert evaluation_state(persisted) == persisted_kind
    store.append_company_window_evaluation(
        company_window_id=window["companyWindowId"], state=evaluation_state(persisted),
        fact_refs=persisted.fact_refs, result=persisted.to_dict(),
        evaluated_at="2026-09-04T16:00:00+08:00", created_at="2026-09-04T16:00:00+08:00", db_path=path,
    )
    with _client(path, tmp_path) as client:
        result = client.get("/api/v1/k10/results")
    assert result.status_code == 200, result.text
    body = result.json()
    row = next(item for item in body["records"] if item["companyWindowId"] == ids["unconfiguredWindowId"])

    assert body["state"] == body["configurationState"] == "not_configured"
    assert "evaluationPolicy" in body["configurationMissing"]
    assert row["state"] == "not_configured"
    assert row["evaluationConfigurationState"] == "not_configured"
    assert "evaluationPolicy" in row["evaluationConfigurationMissing"]
    assert row["closeLimitHitAny"] is None and row["primaryEligible"] is False
    assert row["knownTouchDays"] == [] and row["factRefs"] == []
    assert body["primary"]["all"]["notConfiguredCount"] >= 1
    assert body["primary"]["all"]["knownHitCount"] == baseline["primary"]["all"]["knownHitCount"]
    assert body["primary"]["all"]["touchCount"] == baseline["primary"]["all"]["touchCount"]
    stored = next(item for item in store.list_company_window_evaluations(db_path=path)
                  if item["companyWindowId"] == window["companyWindowId"])
    assert stored["state"] == persisted_kind


def test_v304_valid_frozen_evaluation_remains_readable_when_another_window_is_invalid(tmp_path):
    from tests.k10_v304_fixture import build_fixture

    path = tmp_path / "valid-frozen-evaluation.sqlite"
    ids = build_fixture(path)
    with _client(path, tmp_path) as client:
        result = client.get("/api/v1/k10/results")
    assert result.status_code == 200, result.text
    row = next(item for item in result.json()["records"] if item["companyWindowId"] == ids["primaryWindowId"])
    assert row["evaluationConfigurationState"] == "configured"
    assert row["state"] in {"completed", "incomplete", "pending", "due"}


def test_v304_scan_and_lifecycle_hydrate_time_uncertainty_and_independent_evidence(tmp_path):
    from tests.k10_v304_fixture import build_fixture

    path = tmp_path / "source-and-withdrawal.sqlite"
    ids = build_fixture(path)
    with _client(path, tmp_path) as client:
        scan = client.get("/api/v1/k10/scans/scan-v304-coverage")
        detail = client.get("/api/v1/k10/opportunities/" + ids["withdrawnOpportunityId"])
    assert scan.status_code == detail.status_code == 200
    assert scan.json()["executionProgress"] is None, "旧扫描没有新检查点时不能伪造零进度"
    source = scan.json()["sourceCoverage"][0]
    assert scan.json()["coverageStatus"] == "partial"
    assert source["timeCoverage"] == "partial"
    assert source["unknownPublicationTimeCount"] == 1
    assert source["uncertainTimeDocumentRefs"] == [{
        "documentId": source["uncertainTimeDocumentRefs"][0]["documentId"],
        "factId": None, "companyCode": None, "tradeDate": None, "revision": 1,
        "sourceKey": "tushare-major-news", "title": "公开时间待核的合成资料", "url": None,
        "excerpt": None, "publishedAt": None, "publishedPrecision": "unknown",
        "fetchedAt": "2026-09-07T13:00:00+00:00", "collectedAt": None,
    }]
    withdrawal = next(item for item in detail.json()["lifecycleEvents"] if item["kind"] == "withdrawal")
    assert {ref["documentId"] for ref in withdrawal["sourceRefs"]} >= {
        "doc-v304-morning", "doc-v304-independent"
    }
    assert {ref["documentId"] for ref in withdrawal["independentVerificationRefs"]} == {"doc-v304-independent"}


def test_operations_readiness_is_read_only_and_only_returns_safe_notification_codes(tmp_path):
    from tests.k10_v304_fixture import build_fixture

    path = tmp_path / "operations-readiness.sqlite"
    build_fixture(path)
    before = sha256(path.read_bytes()).hexdigest()
    with _client(path, tmp_path) as client:
        response = client.get("/api/v1/k10/operations/readiness")
    assert response.status_code == 200, response.text
    body = response.json()
    readiness = body["notificationReadiness"]
    assert body["schemaVersion"] == "k10-api-v2"
    assert readiness["state"] in {"ready", "blocked", "notConfigured"}
    assert readiness["reasonCode"] in {
        None, "credentials_missing", "key_unreadable", "key_invalid",
        "notification_schema_unavailable", "delivery_blocked",
    }
    assert "AuthKey" not in str(body) and "/" not in str(body)
    assert sha256(path.read_bytes()).hexdigest() == before


def test_scan_projects_sanitized_resumable_progress_without_task_or_provider_details(tmp_path):
    from tests.k10_v304_fixture import build_fixture

    path = tmp_path / "execution-progress.sqlite"
    build_fixture(path)
    stamp = "2026-09-08T21:00:00+00:00"
    store.create_scan(
        scan_id="scan-execution-progress", window_kind="evening", cutoff_at=stamp,
        config_id="cfg-fixture", config_revision=1, status="running",
        coverage={"inputDocumentRefs": [{"documentId": "doc-a", "revision": 1}, {"documentId": "doc-b", "revision": 1}]},
        created_at=stamp, completed_at=None, db_path=path,
    )
    store.enqueue_task(
        task_id="task-execution-progress", kind="evening_scan", idempotency_key="execution-progress",
        input_version="frozen-input", input_cutoff_at=stamp, payload={"scanId": "scan-execution-progress"},
        budget={}, created_at=stamp, db_path=path,
    )
    from tests.k10_v306_fixture import append_approved_execution_profile
    _, execution_revision = append_approved_execution_profile(
        config_id="execution-fixture", created_at=stamp, db_path=path)
    store.bind_task_execution(
        task_id="task-execution-progress", execution_config_id="execution-fixture",
        execution_config_revision=execution_revision, binding_kind="scheduled", bound_at=stamp, db_path=path,
    )
    store.bind_scan_execution(
        scan_id="scan-execution-progress", task_id="task-execution-progress", execution_config_id="execution-fixture",
        execution_config_revision=execution_revision, binding_kind="scheduled", bound_at=stamp, db_path=path,
    )
    store.record_execution_checkpoint(
        task_id="task-execution-progress", item_kind="document", item_key="doc-a@1", stage="understand",
        input_sha256="a" * 64, status="completed", attempt_count=1, network_attempt_count=0, repair_attempt_count=0,
        elapsed_ms=12, input_tokens=20, output_tokens=10, result={"events": []}, safe_error_code=None,
        safe_error_ref=None, updated_at=stamp, db_path=path,
    )
    store.record_execution_checkpoint(
        task_id="task-execution-progress", item_kind="document", item_key="doc-b@1", stage="understand",
        input_sha256="b" * 64, status="failed", attempt_count=1, network_attempt_count=1, repair_attempt_count=1,
        elapsed_ms=18, input_tokens=20, output_tokens=0, result=None, safe_error_code="model_output_invalid",
        safe_error_ref="doc-b@1", updated_at=stamp, db_path=path,
    )
    with _client(path, tmp_path) as client:
        response = client.get("/api/v1/k10/scans/scan-execution-progress")
    assert response.status_code == 200, response.text
    progress = response.json()["executionProgress"]
    assert progress["titleCounts"] is None, "未冻结标题清单，不能用旧正文检查点冒充标题进度"
    assert progress["articleCounts"] is None, "没有真实正文准入账，不能补造已选或已读篇数"
    assert progress["safeFailures"] == [{"stage": "understand", "code": "model_output_invalid", "ref": "doc-b@1"}]
    assert progress["state"] == "running" and progress["coverageStatus"] == "partial"
    assert progress["eventCounts"]["publishable"] is None, "标题筛选进度不能伪造可发布候选数"
    assert "taskId" not in progress and "raw" not in str(progress).lower() and "prompt" not in str(progress).lower()


def test_v304_event_groups_expose_overlap_metrics_separately_from_primary_rate(tmp_path):
    from tests.k10_v304_fixture import build_fixture

    path = tmp_path / "event-overlap.sqlite"
    ids = build_fixture(path)
    with _client(path, tmp_path) as client:
        result = client.get("/api/v1/k10/results")
    assert result.status_code == 200, result.text
    group = next(item for item in result.json()["eventGroups"] if ids["overlapWindowId"] in item["companyWindowIds"])

    assert group["primary"]["all"]["sampleCount"] == 0
    assert group["overlap"]["sampleCount"] == group["overlap"]["observedCompleteCount"] == 1
    assert group["overlap"]["hitCount"] == 1
