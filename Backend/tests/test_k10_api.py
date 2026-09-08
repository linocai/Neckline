from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from neckline.api import k10 as k10_api
from neckline.api.k10 import _comparison, _metrics, _source_ref, create_router
from neckline.api.k10_schemas import CompanyWindowEvaluationOut, MarketDayOut
from neckline.k10 import research_store, store
from neckline.k10.research_contracts import ResearchSnapshot, ResearchStageResult
from neckline.k10.schema import initialize_schema
from neckline.k10.types import OpportunityPublicationInput


NOW = "2026-09-06T12:00:00+00:00"


def _client(path: Path, *, config_binding: tuple[str | None, int | None, str | None] = (None, None, None),
            execution_config_binding: tuple[str | None, int | None, str | None] = (None, None, None)) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(lambda: path, lambda: None, lambda: path.parent / "parquet",
                                    current_config_binding_provider=lambda: config_binding,
                                    current_execution_config_binding_provider=lambda: execution_config_binding))
    return TestClient(app)


def _freeze_k10_clocks(monkeypatch: pytest.MonkeyPatch, at: str) -> None:
    """Keep API and store projections at the fixture's intended observation instant."""
    fixed = datetime.fromisoformat(at)

    class FixtureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(k10_api, "_now", lambda: at)
    monkeypatch.setattr(k10_api, "datetime", FixtureDateTime)
    monkeypatch.setattr(store, "datetime", FixtureDateTime)


def _ready_config() -> dict:
    return json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())


def _execution_config() -> dict:
    # Test-only V3 execution boundary. Production keeps this pack unbound
    # until an operator supplies an approved policy revision.
    policy_content = json.loads(
        (Path(__file__).parents[1] / "neckline/config/k10-title-triage-policy-v1.json").read_text()
    )
    options = {
        stage: {"maxTokens": 64, "thinking": {"type": "disabled"}}
        for stage in ("titleBatch", "titleReconcile", "understand", "verify", "companyComparison", "prioritize", "morning", "analysisPro", "analysisCon", "investigation")
    }
    return {
        "executionVersion": "k10-execution-v3",
        "discovery": {
            "model": "deepseek-v4-pro",
            "titleTriagePolicy": {"policyId": "api-fixture-policy", "revision": 1, "contentSha256": "0" * 64,
                                  "approvalState": "approved", "content": policy_content},
            "articleLimits": {"evening": 80, "morning": 40},
            "titleBatchSize": 8, "titleTriageConcurrency": 1, "deepReadConcurrency": 1,
            "networkMaxAttempts": 1, "jsonRepairMaxAttempts": 0, "retryBackoffSeconds": [1],
            "taskSliceSeconds": 30, "completionDeadlineSeconds": 60, "continuationDelaySeconds": 1,
            "investigationPromptContractRevision": "k10-investigation-v1",
            "modelOptions": options,
        },
    }


def _append_bound_v3_execution(path: Path, *, task_id: str) -> tuple[str, int]:
    payload = _execution_config()
    policy = payload["discovery"]["titleTriagePolicy"]
    policy_revision = store.append_title_triage_policy(
        policy_id=str(policy["policyId"]), content=policy["content"], approval_state="approved",
        created_at=NOW, approved_at=NOW, db_path=path,
    )
    stored_policy = store.read_title_triage_policy(policy_id=str(policy["policyId"]), revision=policy_revision, db_path=path)
    assert stored_policy is not None
    payload["discovery"]["titleTriagePolicy"] = {
        "policyId": stored_policy["policyId"], "revision": stored_policy["revision"],
        "contentSha256": stored_policy["contentSha256"], "approvalState": "approved", "content": stored_policy["content"],
    }
    revision = store.append_execution_config(config_id="api-v306-execution", payload=payload, created_at=NOW, db_path=path)
    store.bind_task_execution(task_id=task_id, execution_config_id="api-v306-execution", execution_config_revision=revision,
                              binding_kind="scheduled", bound_at=NOW, db_path=path)
    return "api-v306-execution", revision


def _title_manifest_hash(refs: list[dict[str, object]]) -> str:
    return sha256(json.dumps(refs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _seed(path: Path) -> str:
    initialize_schema(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES('SSE',?,?)", [("20260904", 1), ("20260905", 0), ("20260906", 0), ("20260907", 1), ("20260908", 1), ("20260909", 1)])
    config = {
        "configVersion": "k10-v1.4", "universe": "chinext", "excludeBaijiu": True,
        "hardExclusions": {"approved": True, "board": "chinext", "priceLimit": "none", "st": "exclude", "swL2Exclusions": ["801125.SI"]},
        "sourceAdapters": ["fixture"], "modelRoutes": {"analysis": "deepseek-v4-pro"},
        "taskPolicies": {"analysis": {"maxAttempts": 1, "costLimit": 0}},
        "marketCollection": {"retryIntervalSeconds": 300, "retryUntilMinutesAfterClose": 120},
        "evaluationPolicy": {"version": "k10-evaluation-v1.4", "selectionFreeze": "d1_open_0930", "window": "d1_d2", "primaryMetric": "close_limit_up_any_d1_d2"},
    }
    revision = store.append_run_config(config_id="cfg", payload=config, created_at=NOW, db_path=path)
    store.append_document_version(document_id="doc-1", source_key="fixture-news", external_id="notice-1",
                                  canonical_url="https://example.test/notice", content_sha256="a" * 64,
                                  published_at="2026-09-06T10:00:00+00:00", published_precision="exact", fetched_at=NOW,
                                  original_text="合成原文", excerpt="原文摘要", fetch_version="fixture",
                                  metadata={"title": "合成公告"}, created_at=NOW, db_path=path)
    refs = [{"documentId": "doc-1", "revision": 1}]
    store.append_event_revision(event_id="event-1", stable_key="event", headline="合成催化", event_kind="policy",
                                facts={"共同事实": "政策已正式发布", "订单": {"text": "新增订单 10 亿元", "unit": "CNY"}},
                                source_refs=refs, supersedes_revision=None, created_at=NOW, db_path=path)
    store.create_scan(scan_id="scan-1", window_kind="evening", cutoff_at=NOW, config_id="cfg", config_revision=revision, status="completed", coverage={"status": "complete"}, created_at=NOW, completed_at=NOW, db_path=path)
    comparison = {
        "summary": "比较",
        "differences": {"role": "primary", "priorityReason": "直接受益", "gap": "备选缺少证据", "rankChangeConditions": "新证据", "twoDayReason": "窗口内催化"},
        "evidenceRefs": refs, "rank": 1,
        "classification": {"kind": "initial", "opportunityKey": "300001:initial", "reason": "首发", "newFacts": "新增披露", "changedJudgment": None, "twoDayReason": "两日可核", "relatedOpportunityId": None},
    }
    store.create_candidate(candidate_id="cand-1", scan_id="scan-1", event_id="event-1", event_revision=1, company_code="300001.SZ", comparison=comparison, evidence=refs, created_at=NOW, db_path=path)
    alternate = {**comparison, "rank": 2, "differences": {**comparison["differences"], "role": "alternative", "priorityReason": "受益较弱", "gap": "订单兑现较慢", "rankChangeConditions": "订单超预期", "twoDayReason": "催化尚可"},
                 "classification": {**comparison["classification"], "opportunityKey": "300002:initial"}}
    store.create_candidate(candidate_id="cand-2", scan_id="scan-1", event_id="event-1", event_revision=1, company_code="300002.SZ", comparison=alternate, evidence=refs, created_at=NOW, db_path=path)
    batch = store.publish_opportunities(batch_id="batch-1", scan_id="scan-1", publication_kind="evening", inputs=[
        OpportunityPublicationInput(candidate_id="cand-1", company_code="300001.SZ", event_id="event-1", event_revision=1, opportunity_key="300001:initial", catalyst_stage="initial", category="primary", comparison=comparison, evidence_refs=tuple(refs), source_marker="evening"),
        OpportunityPublicationInput(candidate_id="cand-2", company_code="300002.SZ", event_id="event-1", event_revision=1, opportunity_key="300002:initial", catalyst_stage="initial", category="alternative", comparison=alternate, evidence_refs=tuple(refs), source_marker="evening"),
    ], db_path=path, clock=lambda: datetime(2026, 9, 6, 20, tzinfo=timezone.utc))
    return batch.batch_id


def test_b39_evidence_disclosure_preserves_unverified_rumor_and_legacy_absence() -> None:
    disclosure = {
        "verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
        "originEvidenceRef": None, "unverifiedReasons": ["尚无独立来源核验"],
        "conditionalAnalysis": "仅在后续披露确认时重新评估。",
    }
    comparison = _comparison({"summary": "传闻影响待核", "differences": {"evidenceDisclosure": disclosure}})
    assert comparison.evidenceDisclosure is not None
    assert comparison.evidenceDisclosure.verificationStatus == "unverified"
    assert comparison.evidenceDisclosure.isRumor is True
    assert comparison.evidenceDisclosure.originStatus == "unknown"
    assert comparison.evidenceDisclosure.unverifiedReasons == ["尚无独立来源核验"]

    # B36/B38 had no disclosure contract.  Null preserves that historical
    # absence and never lets a client infer a verified status.
    assert _comparison({"summary": "旧比较", "differences": {}}).evidenceDisclosure is None
    assert _comparison({"differences": {"evidenceDisclosure": {
        "verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
        "unverifiedReasons": [], "conditionalAnalysis": "条件说明",
    }}}).evidenceDisclosure is None


def test_b39_historical_scan_keeps_research_absence_distinct_from_verified(tmp_path: Path) -> None:
    path = tmp_path / "historical-research-absence.sqlite"
    _seed(path)
    with _client(path) as client:
        scan = client.get("/api/v1/k10/scans/scan-1")
        summary = client.get("/api/v1/k10/scans/scan-1/research-summary")
        assessments = client.get("/api/v1/k10/scans/scan-1/assessments")
    assert scan.status_code == assessments.status_code == 200
    assert scan.json()["researchSummary"] is None
    assert summary.status_code == 404
    assert assessments.json() == {"schemaVersion": "k10-api-v2", "scanId": "scan-1", "items": []}


def test_b39_research_summary_and_complete_assessments_are_safe_and_additive(tmp_path: Path) -> None:
    path = tmp_path / "b39-research-api.sqlite"
    _seed(path)
    store.enqueue_task(task_id="b39-research-task", kind="evening_scan", idempotency_key="b39-research-task",
                       input_version="frozen", input_cutoff_at=NOW, payload={"windowKind": "evening"},
                       budget={"maxAttempts": 1}, created_at=NOW, db_path=path)
    execution_id, execution_revision = _append_bound_v3_execution(path, task_id="b39-research-task")
    store.bind_scan_execution(scan_id="scan-1", task_id="b39-research-task", execution_config_id=execution_id,
                              execution_config_revision=execution_revision, binding_kind="scheduled", bound_at=NOW, db_path=path)
    snapshot = ResearchSnapshot(
        snapshot_id="snapshot-1", task_id="b39-research-task", event_id="event-1", event_revision=1,
        news_cutoff_at=NOW, verification_cutoff_at=NOW, context_sha256="d" * 64,
        prompt_contract_revision="investigation-v1", model_parameters_sha256="e" * 64,
        research_status="ready_for_comparison", execution_status="ok", revision=1, created_at=NOW, updated_at=NOW,
    )
    research_store.create_research_snapshot(snapshot=snapshot, db_path=path)
    research_store.advance_research_snapshot(
        snapshot_id=snapshot.snapshot_id, expected_revision=1, research_status="comparison_complete", execution_status="failed",
        input_sha256="f" * 64, updated_at=NOW, db_path=path,
        stage_result=ResearchStageResult(action="compare_companies", safe_error_code="comparison_interrupted", company_assessments=(
            {"companyCode": "300001.SZ", "role": "primary", "rank": 1, "summary": "传闻映射待核",
             "priorityReason": "现有说法直接指向公司", "gap": "缺独立来源", "rankChangeConditions": "正式披露确认",
             "twoDayReason": "只作消息观察", "evidenceDisclosure": {
                "verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown", "originEvidenceRef": None,
                "unverifiedReasons": ["尚无独立来源核验"], "conditionalAnalysis": "仅在正式披露确认时重新评估。",
             }},
            {"companyCode": "300002.SZ", "role": "pending", "rank": None, "summary": "竞争对象仍待核",
             "priorityReason": "存在关联线索", "gap": "关键竞争信息缺失", "rankChangeConditions": "补齐反证或确认关系",
             "twoDayReason": "待核前不作排序", "evidenceDisclosure": {
                "verificationStatus": "partially_supported", "isRumor": False, "originStatus": "identified",
                "originEvidenceRef": {"documentId": "doc-1", "revision": 1}, "unverifiedReasons": [], "conditionalAnalysis": None,
             }},
        )),
    )

    with _client(path) as client:
        scan = client.get("/api/v1/k10/scans/scan-1")
        summary = client.get("/api/v1/k10/scans/scan-1/research-summary")
        assessments = client.get("/api/v1/k10/scans/scan-1/assessments")

    assert scan.status_code == summary.status_code == assessments.status_code == 200
    body = scan.json()["researchSummary"]
    assert body["comparisonComplete"] is True and body["executionFailed"] is True
    assert body["safeFailureCounts"] == {"comparison_interrupted": 1}
    assert body["companyCounts"] == {"primary": 1, "alternative": 0, "tied": 0, "pending": 1, "excluded": 0, "comparable": 1}
    assert summary.json() == body
    assert {item["role"] for item in assessments.json()["items"]} == {"primary", "pending"}
    primary = next(item for item in assessments.json()["items"] if item["role"] == "primary")
    assert primary["evidenceDisclosure"]["verificationStatus"] == "unverified"
    assert primary["evidenceDisclosure"]["isRumor"] is True
    assert primary["evidenceDisclosure"]["originStatus"] == "unknown"
    assert primary["safeErrorCode"] == "comparison_interrupted"
    assert "prompt" not in json.dumps(assessments.json()).lower()
    assert "https://" not in json.dumps(assessments.json())


def test_get_on_missing_schema_is_503_and_never_creates_a_database(tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite"
    with _client(path) as client:
        response = client.get("/api/v1/k10/publications")
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "not_configured"
    assert not path.exists()


def test_configuration_uses_explicit_ready_binding_before_any_scan_and_never_writes(tmp_path: Path) -> None:
    path = tmp_path / "configuration.sqlite"
    initialize_schema(path)
    assert store.append_run_config(config_id="current", payload=_ready_config(), created_at=NOW, db_path=path) == 1
    assert store.append_execution_config(config_id="execution", payload=_execution_config(), created_at=NOW, db_path=path) == 1
    before = sha256(path.read_bytes()).hexdigest()

    with _client(path, config_binding=("current", 1, None), execution_config_binding=("execution", 1, None)) as client:
        response = client.get("/api/v1/k10/configuration")

    assert response.status_code == 200
    body = response.json()
    assert body["configId"] == "current" and body["configRevision"] == 1
    assert {scope["scope"] for scope in body["scopes"]} == {"candidate", "discovery", "analysis", "evaluation"}
    assert all(scope["state"] == "configured" for scope in body["scopes"])
    assert store.list_scans(window_kind=None, db_path=path) == []
    assert sha256(path.read_bytes()).hexdigest() == before


def test_operations_exposes_durable_pause_and_client_pause_stays_closed(tmp_path: Path) -> None:
    path = tmp_path / "operations.sqlite"
    initialize_schema(path)
    with _client(path) as client:
        initial = client.get("/api/v1/k10/operations/readiness")
        paused = client.post("/api/v1/k10/operations/pause")
        after = client.get("/api/v1/k10/operations/readiness")

    assert initial.status_code == paused.status_code == after.status_code == 200
    assert initial.json()["runControl"] == {
        "state": "paused", "reasonCode": "unconfigured_closed", "changedAt": "1970-01-01T00:00:00+00:00",
    }
    assert paused.json()["runControl"]["state"] == "paused"
    assert paused.json()["runControl"]["reasonCode"] == "user_paused"
    assert after.json()["runControl"] == paused.json()["runControl"]
    assert store.run_control_status(db_path=path)["state"] == "closed"



@pytest.mark.parametrize(("scan_status", "expected_state_after_pause"), [("running", "paused"), ("partial", "partial")])
def test_scan_progress_exposes_v306_title_article_and_safe_attempt_aggregates(
    tmp_path: Path, scan_status: str, expected_state_after_pause: str,
) -> None:
    path = tmp_path / "v306-progress.sqlite"
    _seed(path)
    store.set_run_control(state="open", reason_code="fixture_authorized", changed_at=NOW,
                          changed_by="test", db_path=path)
    store.enqueue_task(
        task_id="v306-progress-task", kind="evening_scan", idempotency_key="v306-progress-task",
        input_version="frozen", input_cutoff_at=NOW, payload={"windowKind": "evening"},
        budget={"maxAttempts": 1}, created_at=NOW, db_path=path,
    )
    execution_id, execution_revision = _append_bound_v3_execution(path, task_id="v306-progress-task")
    store.create_scan(
        scan_id="v306-progress-scan", window_kind="evening", cutoff_at=NOW, config_id="cfg",
        config_revision=1, status=scan_status, coverage={"status": "partial", "inputSnapshotFrozen": True,
        "receivedTitleCount": 2, "factCacheHits": 2}, created_at=NOW,
        completed_at=NOW if scan_status == "partial" else None, db_path=path,
    )
    store.bind_scan_execution(
        scan_id="v306-progress-scan", task_id="v306-progress-task", execution_config_id=execution_id,
        execution_config_revision=execution_revision, binding_kind="scheduled", bound_at=NOW, db_path=path,
    )
    refs = [{"documentId": "title-a", "revision": 1}, {"documentId": "title-b", "revision": 1}]
    store.freeze_title_triage_manifest(
        task_id="v306-progress-task", input_manifest_sha256=_title_manifest_hash(refs), window_kind="evening",
        policy_id="api-fixture-policy", policy_revision=1,
        policy_content_sha256=store.read_title_triage_policy(policy_id="api-fixture-policy", revision=1, db_path=path)["contentSha256"],
        article_limit=80, input_refs=refs, batch_count=1, title_status="frozen", created_at=NOW, db_path=path,
    )
    store.record_title_triage_item(task_id="v306-progress-task", document_id="title-a", revision=1, batch_index=0,
                                   disposition="candidate", matter_key="matter-a", merged_ref=None, selection_rank=1,
                                   audit_reason="独立事项", created_at=NOW, db_path=path)
    store.record_title_triage_item(task_id="v306-progress-task", document_id="title-b", revision=1, batch_index=0,
                                   disposition="merged", matter_key="matter-a", merged_ref=refs[0], selection_rank=None,
                                   audit_reason="同事项转载", created_at=NOW, db_path=path)
    selected = [refs[0]]
    store.freeze_title_selection_manifest(task_id="v306-progress-task", selection_manifest_sha256=_title_manifest_hash(selected),
                                          selected_refs=selected, created_at=NOW, db_path=path)
    store.admit_article(task_id="v306-progress-task", document_id="title-a", revision=1, admission_kind="selected", created_at=NOW, db_path=path)
    store.record_article_outcome(task_id="v306-progress-task", document_id="title-a", revision=1, state="missing_body",
                                 reason_code="source_unavailable", updated_at=NOW, db_path=path)
    tavily_refs = []
    for document_id, digest in (("tavily-a", "b" * 64), ("tavily-b", "c" * 64)):
        version = store.append_document_version(
            document_id=document_id, source_key="tavily_verification", external_id=document_id,
            canonical_url=None, content_sha256=digest, published_at=NOW, published_precision="exact",
            fetched_at=NOW, original_text=None, excerpt="已持久化的 Tavily 核验摘录",
            fetch_version="tavily-basic-general-v2", metadata={}, created_at=NOW, db_path=path,
        )
        tavily_refs.append({"documentId": version.document_id, "revision": version.revision})
    store.record_execution_checkpoint(
        task_id="v306-progress-task", item_kind="event", item_key="tavily-evidence",
        stage="tavily_evidence", input_sha256="tavily-evidence".ljust(64, "0"), status="completed",
        attempt_count=1, network_attempt_count=1, repair_attempt_count=0, elapsed_ms=0,
        input_tokens=None, output_tokens=None,
        result={"coverage": {"requestState": "completed"}, "documentRefs": tavily_refs},
        safe_error_code=None, safe_error_ref=None, updated_at=NOW, db_path=path,
    )
    store.admit_article(task_id="v306-progress-task", document_id="tavily-a", revision=1, admission_kind="tavily_full_article", created_at=NOW, db_path=path)
    attempt = store.begin_external_attempt(task_id="v306-progress-task", stage="model:titleBatch", item_key="batch-0",
                                           attempt_key="batch-0:1", input_sha256="a" * 64, started_at=NOW, db_path=path)
    store.settle_external_attempt(attempt_id=attempt["attemptId"], outcome="succeeded",
                                  usage={"promptTokens": 4, "completionTokens": 2, "totalTokens": 6, "searchRequests": 0, "searchCredits": 0},
                                  settled_at=NOW, error_code=None, db_path=path)

    with _client(path) as client:
        running = client.get("/api/v1/k10/scans/v306-progress-scan")
        pause = client.post("/api/v1/k10/operations/pause")
        paused = client.get("/api/v1/k10/scans/v306-progress-scan")

    assert running.status_code == pause.status_code == paused.status_code == 200
    initial_progress = running.json()["executionProgress"]
    assert initial_progress["state"] == scan_status
    # This fixture writes the durable title manifest directly; it deliberately
    # does not simulate a worker claiming and advancing the task stage.
    assert initial_progress["stage"] == "created"
    assert initial_progress["runControl"]["state"] == "ready"
    assert initial_progress["titleCounts"] == {
        "received": 2, "exactDeduplicated": 0, "triaged": 2, "merged": 1, "notSelected": 1, "protected": 0, "partial": 0,
    }
    assert initial_progress["articleCounts"] == {
        "limit": 80, "selected": 1, "admitted": 2, "completed": 0, "missingBody": 1, "tavilyExcerpt": 2, "tavilyFullArticle": 1,
    }
    assert initial_progress["attemptCounts"] == {"started": 0, "succeeded": 1, "failed": 0, "unknown": 0}
    assert initial_progress["factCacheHits"] == 2
    assert "budget" not in initial_progress and "actualUsage" not in initial_progress["attemptCounts"]
    paused_progress = paused.json()["executionProgress"]
    assert pause.json()["runControl"]["state"] == "paused"
    assert paused_progress["state"] == expected_state_after_pause
    assert paused_progress["runControl"]["reasonCode"] == "user_paused"
    assert paused_progress["titleCounts"] == initial_progress["titleCounts"]
    assert paused_progress["factCacheHits"] == 2

def test_configuration_uses_bound_revision_not_an_old_scan_or_another_config(tmp_path: Path) -> None:
    path = tmp_path / "configuration.sqlite"
    initialize_schema(path)
    old_revision = store.append_run_config(config_id="old", payload=_ready_config(), created_at=NOW, db_path=path)
    current_revision = store.append_run_config(config_id="current", payload=_ready_config(), created_at=NOW, db_path=path)
    current_update = _ready_config()
    current_update["sourceAdapters"] = [{"key": "newer-current-source", "lateArrivalReplaySeconds": 86400}]
    assert store.append_run_config(config_id="current", payload=current_update, created_at=NOW, db_path=path) == 2
    assert store.append_execution_config(config_id="execution", payload=_execution_config(), created_at=NOW, db_path=path) == 1
    store.create_scan(scan_id="old-scan", window_kind="evening", cutoff_at=NOW, config_id="old",
                      config_revision=old_revision, status="completed", coverage={"status": "complete"},
                      created_at=NOW, completed_at=NOW, db_path=path)

    with _client(path, config_binding=("current", current_revision, None), execution_config_binding=("execution", 1, None)) as client:
        response = client.get("/api/v1/k10/configuration")

    assert response.status_code == 200
    assert response.json()["configId"] == "current"
    assert response.json()["configRevision"] == current_revision
    assert all(scope["state"] == "configured" for scope in response.json()["scopes"])


@pytest.mark.parametrize(
    ("binding", "error"),
    [
        ((None, None, None), "未绑定 K10_CONFIG_ID"),
        (("current", True, None), "未绑定有效的 K10_CONFIG_REVISION"),
        (("current", None, "K10_CONFIG_REVISION 必须是正整数"), "K10_CONFIG_REVISION 必须是正整数"),
        (("missing", 1, None), "指向的配置修订不存在"),
        (("current", 99, None), "指向的配置修订不存在"),
    ],
)
def test_configuration_reports_missing_invalid_or_unknown_explicit_binding(tmp_path: Path, binding, error: str) -> None:
    path = tmp_path / "configuration.sqlite"
    initialize_schema(path)
    store.append_run_config(config_id="current", payload=_ready_config(), created_at=NOW, db_path=path)

    with _client(path, config_binding=binding) as client:
        response = client.get("/api/v1/k10/configuration")

    assert response.status_code == 200
    body = response.json()
    assert body["configId"] is None and body["configRevision"] is None
    assert all(scope["state"] == "not_configured" for scope in body["scopes"])
    assert all(any(error in message for message in scope["errors"]) for scope in body["scopes"])


@pytest.mark.parametrize(
    ("execution_binding", "error"),
    [
        ((None, None, None), "未绑定 K10_EXECUTION_CONFIG_ID"),
        (("execution", None, "K10_EXECUTION_CONFIG_REVISION 必须是正整数"), "K10_EXECUTION_CONFIG_REVISION 必须是正整数"),
        (("missing", 1, None), "执行配置修订不存在"),
    ],
)
def test_candidate_configuration_requires_current_explicit_execution_binding_only(tmp_path: Path, execution_binding, error: str) -> None:
    path = tmp_path / "execution-configuration.sqlite"
    initialize_schema(path)
    store.append_run_config(config_id="current", payload=_ready_config(), created_at=NOW, db_path=path)
    with _client(path, config_binding=("current", 1, None), execution_config_binding=execution_binding) as client:
        body = client.get("/api/v1/k10/configuration").json()
    scopes = {item["scope"]: item for item in body["scopes"]}
    assert scopes["candidate"]["state"] == "not_configured"
    assert any(error in message for message in scopes["candidate"]["errors"])
    assert scopes["analysis"]["state"] == scopes["evaluation"]["state"] == "configured"


def test_publications_project_company_cards_and_multifield_wire_contract(tmp_path: Path) -> None:
    path = tmp_path / "api.sqlite"; _seed(path)
    with _client(path) as client:
        publication = client.get("/api/v1/k10/publications").json()["items"][0]
        opportunity = client.get("/api/v1/k10/opportunities").json()["items"][0]
        card = client.get("/api/v1/k10/company-windows").json()["items"][0]
        detail = client.get("/api/v1/k10/opportunities/" + opportunity["opportunityId"]).json()
    assert publication["schemaVersion"] == "k10-api-v2" and publication["sampleCount"] == 2
    assert opportunity["sampleClass"] == "primary" and opportunity["d1TradeDate"] == "2026-09-07"
    assert card["selection"] is None and {sample["companyCandidateId"] for sample in card["samples"]} <= {"cand-1", "cand-2"}
    assert detail["lifecycleEvents"][0]["kind"] == "published"
    assert detail["eventHeadline"] == "合成催化"
    assert {item["text"] for item in detail["commonFacts"]} == {"政策已正式发布", "新增订单 10 亿元"}
    assert {item["companyCandidateId"] for item in detail["samples"]} == {"cand-1", "cand-2"}
    compared = {item["companyCandidateId"]: item["comparison"] for item in detail["samples"]}
    assert compared["cand-1"].get("priorityReason") == "直接受益"
    assert compared["cand-2"] == {"summary": "比较", "rationale": None, "rank": 2,
                                     "priorityReason": "受益较弱", "gap": "订单兑现较慢",
                                     "rankChangeConditions": "订单超预期", "twoDayReason": "催化尚可",
                                         "eventRank": None, "rankNamespace": None,
                                         "evidenceDisclosure": None,
                                         "classification": {"kind": "initial", "reason": "首发", "newFacts": "新增披露",
                                                        "changedJudgment": None, "twoDayReason": "两日可核",
                                                        "relatedOpportunityId": None},
                                     "historicalCases": [], "historicalCoverage": None}
    evidence = compared and detail["samples"][0]["evidence"][0]["sourceRef"]
    assert evidence["documentId"] == "doc-1" and evidence["revision"] == 1
    assert evidence["sourceKey"] == "fixture-news" and evidence["title"] == "合成公告"
    assert evidence["fetchedAt"] == NOW and evidence["url"] == "https://example.test/notice"
    assert "pricePlan" not in str(card) and "planStatus" not in str(opportunity)


def test_keep_is_idempotent_and_selection_freeze_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "selection.sqlite"; _seed(path)
    action_at = "2026-09-07T01:20:00+00:00"  # 09:20 CST, before the 09:30 D1 freeze.
    _freeze_k10_clocks(monkeypatch, action_at)
    with _client(path) as client:
        window_id = client.get("/api/v1/k10/company-windows").json()["items"][0]["companyWindowId"]
        first = client.post(f"/api/v1/k10/company-windows/{window_id}/selection", json={"action": "keep", "idempotencyKey": "keep-1"})
        replay = client.post(f"/api/v1/k10/company-windows/{window_id}/selection", json={"action": "keep", "idempotencyKey": "keep-1"})
        store.freeze_company_window_selection(company_window_id=window_id, frozen_at="2026-09-08T09:30:00+08:00", db_path=path)
        card = client.get(f"/api/v1/k10/company-windows/{window_id}").json()
        selection = client.get(f"/api/v1/k10/selections/{window_id}").json()
    assert first.status_code == replay.status_code == 200
    assert first.json()["observationId"] == replay.json()["observationId"]
    assert first.json()["lastActionAt"] == action_at and first.json()["postFreeze"] is False
    assert replay.json()["replayed"] is True
    assert card["selection"]["state"] == "selected"
    assert selection["state"] == "kept" and selection["analysisJobId"]


def test_opportunity_lifecycle_keeps_risk_until_verified_morning_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "lifecycle.sqlite"; _seed(path)
    _freeze_k10_clocks(monkeypatch, "2026-09-07T02:10:00+00:00")
    opportunity = next(item for item in store.list_opportunities(db_path=path) if item["companyCode"] == "300001.SZ")
    opportunity_id = str(opportunity["opportunityId"])
    d2_before = str(opportunity["d2TradeDate"])
    store.append_opportunity_update(
        lifecycle_event_id="risk-1", opportunity_id=opportunity_id, kind="risk", reason="反证待核",
        source_refs=(), content={"reasonStatus": "needs_review", "sourceStatus": "complete"},
        occurred_at="2026-09-07T01:00:00+00:00", created_at="2026-09-07T01:00:00+00:00", db_path=path,
    )
    with _client(path) as client:
        cards = client.get("/api/v1/k10/opportunities").json()["items"]
    assert next(item for item in cards if item["opportunityId"] == opportunity_id)["lifecycle"] == "risk"

    # A normal discovery continuation has no verified morning status and must
    # not make a still-unresolved risk disappear from the card.
    store.append_opportunity_update(
        lifecycle_event_id="continuation-1", opportunity_id=opportunity_id, kind="evidence_update", reason="常规补充",
        source_refs=(), content={"classification": {"kind": "continuation"}},
        occurred_at="2026-09-07T02:00:00+00:00", created_at="2026-09-07T02:00:00+00:00", db_path=path,
    )
    with _client(path) as client:
        cards = client.get("/api/v1/k10/opportunities").json()["items"]
    assert next(item for item in cards if item["opportunityId"] == opportunity_id)["lifecycle"] == "risk"

    # The validated morning output is the only current event that clears it.
    store.append_opportunity_update(
        lifecycle_event_id="morning-current-1", opportunity_id=opportunity_id, kind="evidence_update", reason="晨间复核完成",
        source_refs=(), content={"reasonStatus": "current", "sourceStatus": "complete"},
        occurred_at="2026-09-07T03:00:00+00:00", created_at="2026-09-07T03:00:00+00:00", db_path=path,
    )
    with _client(path) as client:
        cards = client.get("/api/v1/k10/opportunities").json()["items"]
    assert next(item for item in cards if item["opportunityId"] == opportunity_id)["lifecycle"] == "evidence_update"

    store.withdraw_opportunity(opportunity_id=opportunity_id, reason="核心事实已推翻", source_refs=(),
                               withdrawn_at="2026-09-07T04:00:00+00:00", db_path=path)
    with _client(path) as client:
        card = next(item for item in client.get("/api/v1/k10/opportunities").json()["items"]
                    if item["opportunityId"] == opportunity_id)
    assert card["lifecycle"] == "withdrawal"
    assert card["d2TradeDate"] == d2_before


def test_document_reader_projects_tushare_html_without_mutating_frozen_source(tmp_path: Path) -> None:
    path = tmp_path / "document-reader.sqlite"; _seed(path)
    original_html = "<p>第一段 &amp; 内容</p><script>广告脚本();</script><style>.ad { display:none; }</style><p>第二段</p>"
    store.append_document_version(
        document_id="tushare-html", source_key="tushare-major-news", external_id="news-1", canonical_url=None,
        content_sha256="b" * 64, published_at=NOW, published_precision="exact", fetched_at=NOW,
        original_text=original_html, excerpt="摘要", fetch_version="fixture", metadata={"title": "通讯"},
        created_at=NOW, db_path=path,
    )
    plain_text = "保持原样\n含 &amp; 字符"
    store.append_document_version(
        document_id="plain-text", source_key="fixture-news", external_id="news-2", canonical_url=None,
        content_sha256="c" * 64, published_at=NOW, published_precision="exact", fetched_at=NOW,
        original_text=plain_text, excerpt="摘要", fetch_version="fixture", metadata={"title": "纯文本"},
        created_at=NOW, db_path=path,
    )
    store.append_document_version(
        document_id="tavily-excerpt", source_key="tavily_verification", external_id="search-1", canonical_url="https://example.test/tavily",
        content_sha256="d" * 64, published_at=None, published_precision="unknown", fetched_at=NOW,
        original_text=None, excerpt="Tavily 可读摘要", fetch_version="tavily-basic-general-v2", metadata={"title": "搜索资料"},
        created_at=NOW, db_path=path,
    )
    with _client(path) as client:
        first = client.get("/api/v1/k10/documents/tushare-html", params={"limit": 5}).json()
        pages = [first["body"]]
        cursor = first["page"]["nextCursor"]
        while cursor is not None:
            page = client.get("/api/v1/k10/documents/tushare-html", params={"offset": cursor, "limit": 5}).json()
            pages.append(page["body"])
            cursor = page["page"]["nextCursor"]
        plain = client.get("/api/v1/k10/documents/plain-text").json()
        tavily = client.get("/api/v1/k10/documents/tavily-excerpt", params={"offset": 9, "limit": 5}).json()
    rendered = "".join(pages)
    assert rendered == "第一段 & 内容\n\n第二段"
    assert "<" not in rendered and "广告脚本" not in rendered and ".ad" not in rendered
    assert plain["body"] == plain_text
    assert tavily["body"] is None and tavily["excerpt"] == "Tavily 可读摘要" and tavily["page"]["nextCursor"] is None
    stored = {item["documentId"]: item for item in store.list_source_document_versions(cutoff_at=None, db_path=path)}
    assert stored["tushare-html"]["originalText"] == original_html


def test_production_verification_summary_hides_technical_fact_keys_but_keeps_raw_detail(tmp_path: Path) -> None:
    path = tmp_path / "production-facts.sqlite"; _seed(path)
    facts = {
        "design": {"eventState": "ongoing", "phase": "trial", "stageKey": "pilot"},
        "eventState": "ongoing", "status": "trial", "coverage": {"source": "complete"},
        "verification": {
            "state": "verified", "summary": "核验资料显示项目仍在推进，相关公司具备直接受益条件。",
            "evidenceRefs": [{"documentId": "doc-1", "revision": 1}], "coverage": {"state": "available"},
        },
    }
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE k10_event_revisions SET facts_json=? WHERE event_id='event-1' AND revision=1",
                     (json.dumps(facts, ensure_ascii=False),))
    with _client(path) as client:
        opportunity_id = client.get("/api/v1/k10/opportunities").json()["items"][0]["opportunityId"]
        detail = client.get(f"/api/v1/k10/opportunities/{opportunity_id}").json()
    assert detail["commonFacts"] == [{"key": "共同事实", "text": "核验资料显示项目仍在推进，相关公司具备直接受益条件。", "rawDetail": facts}]
    assert "stageKey" not in " ".join(f"{item['key']}：{item['text']}" for item in detail["commonFacts"])
    with sqlite3.connect(path) as conn:
        stored = json.loads(conn.execute("SELECT facts_json FROM k10_event_revisions WHERE event_id='event-1' AND revision=1").fetchone()[0])
        needs_review = {**facts, "verification": {**facts["verification"], "state": "needs_review"}}
        conn.execute("UPDATE k10_event_revisions SET facts_json=? WHERE event_id='event-1' AND revision=1",
                     (json.dumps(needs_review, ensure_ascii=False),))
    with _client(path) as client:
        pending = client.get(f"/api/v1/k10/opportunities/{opportunity_id}").json()
    assert stored == facts
    assert pending["commonFacts"][0]["key"] == "待核事实"
    assert pending["commonFacts"][0]["rawDetail"] == needs_review


def test_market_snapshot_source_refs_keep_collection_separate_from_data_fetch_and_reject_invalid_urls() -> None:
    daily = _source_ref({
        "url": "market-data://daily/2026-08-24/300436.SZ", "tradeDate": "2026-08-24",
        "collectedAt": "2026-09-06T14:47:08+00:00", "dataFetchedAt": "unknown",
    })
    assert daily.sourceKey == "market_snapshot" and daily.companyCode == "300436.SZ" and daily.tradeDate == "2026-08-24"
    assert daily.title == "日行情快照 · 300436.SZ · 2026-08-24"
    assert daily.collectedAt == "2026-09-06T14:47:08+00:00" and daily.fetchedAt is None
    assert daily.factId is None and daily.revision is None and daily.documentId is None

    factor = _source_ref({
        "url": "market-data://adj_factor/300436.SZ", "companyCode": "300436.SZ",
        "collectedAt": "2026-09-06T14:47:08+00:00", "dataFetchedAt": "2026-09-06T14:40:00+00:00",
    })
    assert factor.sourceKey == "market_snapshot" and factor.title == "复权因子快照 · 300436.SZ"
    assert factor.tradeDate is None and factor.fetchedAt == "2026-09-06T14:40:00+00:00"

    mismatched = _source_ref({
        "url": "market-data://daily/2026-08-24/300436.SZ", "tradeDate": "2026-08-25",
        "collectedAt": "2026-09-06T14:47:08+00:00", "dataFetchedAt": "2026-09-06T14:40:00+00:00",
    })
    malformed = _source_ref({"url": "market-data://daily/2026-02-30/300436.SZ"})
    for ref in (mismatched, malformed):
        assert ref.sourceKey is None and ref.title is None and ref.companyCode is None and ref.tradeDate is None
        assert ref.factId is None and ref.revision is None and ref.url is None


def test_results_keep_overlap_and_missing_data_out_of_primary_denominator(tmp_path: Path) -> None:
    path = tmp_path / "results.sqlite"; _seed(path)
    with _client(path) as client:
        window_id = next(item["companyWindowId"] for item in client.get("/api/v1/k10/company-windows").json()["items"] if item["companyCode"] == "300001.SZ")
    market_revision = store.append_market_day_fact(company_code="300001.SZ", trade_date="2026-09-07", availability="data_gap",
        open_price=None, high_price=None, low_price=None, close_price=None, pre_close=None, limit_up_price=None,
        close_limit_up=None, touched_limit_up=None, source_refs=(), obtained_at="2026-09-07T16:01:00+08:00",
        created_at=NOW, db_path=path)
    store.freeze_company_window_selection(company_window_id=window_id, frozen_at="2026-09-08T09:30:00+08:00", db_path=path)
    store.append_company_window_evaluation(company_window_id=window_id, state="incomplete", fact_refs=[{"factId": store.market_day_fact_id(company_code="300001.SZ", trade_date="2026-09-07"), "companyCode": "300001.SZ", "tradeDate": "2026-09-07", "revision": market_revision}], result={"companyWindowId": window_id, "companyCode": "300001.SZ", "sampleClass": "primary", "selection": {"state": "unhandled", "actionIds": [], "frozenAt": "2026-09-07T09:30:00+08:00"}, "d1": {"tradeDate": "2026-09-07", "availability": "data_gap", "sourceRefs": []}, "d2": {"tradeDate": "2026-09-08", "availability": "data_gap", "sourceRefs": []}, "primaryEligible": False, "closeLimitHitAny": None, "firstTouchDay": None, "d1OpenGap": None, "gaps": [{"day": "D1", "reason": "data_gap"}]}, evaluated_at="2026-09-08T15:00:00+08:00", created_at=NOW, db_path=path)
    with _client(path) as client:
        result = client.get("/api/v1/k10/results").json()
    assert result["primary"]["unhandled"]["sampleCount"] == 1
    assert result["primary"]["unhandled"]["eligibleCount"] == 0
    assert result["records"][0]["d2"]["availability"] == "data_gap"
    fact = result["records"][0]["factRefs"][0]
    assert fact["factId"] == store.market_day_fact_id(company_code="300001.SZ", trade_date="2026-09-07")
    assert fact["companyCode"] == "300001.SZ" and fact["tradeDate"] == "2026-09-07" and fact["revision"] == 1
    assert fact["sourceKey"] == "market" and fact["fetchedAt"] == "2026-09-07T16:01:00+08:00" and fact["documentId"] is None


def test_metrics_require_boolean_limit_observation_and_never_assign_unfrozen_group() -> None:
    complete_day = lambda day, touched: MarketDayOut(tradeDate=day, availability="available", closeLimitUp=False,
        touchedLimitUp=touched, open=10, high=11, low=9, close=10, preClose=10, limitUpPrice=11)
    item = CompanyWindowEvaluationOut(companyWindowId="window", opportunityIds=[], companyCode="300001.SZ",
        sampleClass="primary", selection=None, state="completed", revision=1, updatedAt="2026-01-03T15:00:00+08:00",
        d1=complete_day("2026-01-02", True), d2=complete_day("2026-01-03", False), primaryEligible=True,
        closeLimitHitAny=False)
    metrics = _metrics([item], windows={"window": {"d2CloseAt": "2026-01-03T15:00:00+08:00"}})
    assert metrics.eligibleCount == 1 and metrics.touchRate == 1.0 and metrics.selectionPendingCount == 1
    incomplete_limit = item.model_copy(update={"d2": item.d2.model_copy(update={"touchedLimitUp": None})})
    assert _metrics([incomplete_limit], windows={"window": {"d2CloseAt": "2026-01-03T15:00:00+08:00"}}).eligibleCount == 0


def test_touch_rate_uses_the_same_eligible_or_observed_samples_for_numerator_and_denominator() -> None:
    day = lambda trade_date, touched: MarketDayOut(tradeDate=trade_date, availability="available", closeLimitUp=False,
        touchedLimitUp=touched, open=10, high=11, low=9, close=10, preClose=10, limitUpPrice=11)
    matured = CompanyWindowEvaluationOut(companyWindowId="matured", opportunityIds=[], companyCode="300001.SZ",
        sampleClass="primary", selection=None, state="completed", revision=1, updatedAt="2026-01-03T15:00:00+08:00",
        d1=day("2026-01-02", True), d2=day("2026-01-03", False), primaryEligible=True, closeLimitHitAny=False)
    not_due = matured.model_copy(update={"companyWindowId": "not-due"})
    primary = _metrics([matured, not_due], windows={
        "matured": {"d2CloseAt": "2026-01-03T15:00:00+08:00"},
        "not-due": {"d2CloseAt": "2027-01-03T15:00:00+08:00"},
    })
    assert primary.eligibleCount == 1 and primary.touchRate == 1.0 and primary.touchCount == 2

    incomplete_overlap = not_due.model_copy(update={"companyWindowId": "incomplete-overlap", "d2": day("2026-01-03", None)})
    overlap = _metrics([matured, incomplete_overlap], touch_denominator="observed")
    assert overlap.observedCompleteCount == 1 and overlap.touchRate == 1.0 and overlap.touchCount == 2
