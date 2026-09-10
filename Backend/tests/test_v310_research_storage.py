from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from neckline.k10 import migration, schema, store
from neckline.k10.config import validate_execution_config
from neckline.k10.research_contracts import (
    Claim, EvidenceDisclosure, FullTextRequest, Question, ResearchContractError,
    ResearchSnapshot, ResearchStageResult,
)
from neckline.k10.research_store import (
    advance_research_snapshot, create_research_snapshot, find_research_snapshot, list_research_assessments,
    list_research_claims, list_research_fulltext_requests, list_research_questions,
    load_prior_research_evidence, read_research_snapshot, read_research_state, research_summary_for_scan,
)
from tests.k10_v306_fixture import append_approved_execution_profile
from tests.k10_v306_fixture import execution_payload


NOW = "2026-09-08T13:05:00+00:00"
LATER = "2026-09-08T13:06:00+00:00"


def _seed(path):
    schema.initialize_schema(path)
    store.enqueue_task(task_id="task-1", kind="evening_scan", idempotency_key="research-task",
                       input_version="fixture", input_cutoff_at=NOW, payload={}, budget={}, created_at=NOW, db_path=path)
    store.append_document_version(document_id="source-1", source_key="fixture", external_id="source-1",
                                  canonical_url="https://example.test/source-1", content_sha256="a" * 64,
                                  published_at=NOW, published_precision="exact", fetched_at=NOW,
                                  original_text="frozen source", excerpt="excerpt", fetch_version="fixture",
                                  metadata={}, created_at=NOW, db_path=path)
    store.append_event_revision(event_id="event-1", stable_key="research:event-1", headline="事件",
                                event_kind="disclosure", facts={}, source_refs=[{"documentId": "source-1", "revision": 1}],
                                supersedes_revision=None, created_at=NOW, db_path=path)
    return ResearchSnapshot("snapshot-1", "task-1", "event-1", 1, NOW, NOW, "b" * 64,
                            "k10-investigation-v1", "c" * 64, "continue_research", "ok", 1, NOW, NOW)


def _claim():
    return Claim("claim-1", "供应商称项目进入送样", "rumor", "new_fact", "供应商", "项目", "产品", "送样",
                 "需确认", "今日", "unverified", "会影响阶段判断", {"documentId": "source-1", "revision": 1}, "p:2")


def _question():
    # Event-level truth questions deliberately have no company mapping yet.
    return Question("question-1", ("claim-1",), (), "项目是否进入送样", ({"documentId": "source-1", "revision": 1},),
                    ("独立项目方确认",), "项目方确认送样", "项目方否认", "会改变公司比较", "open", "取得独立来源")


def _assessment():
    disclosure = EvidenceDisclosure("unverified", True, "unknown", None, ("原始发布者未知",), "若项目主体确认则重排")
    return {
        "companyCode": "300001.SZ", "role": "primary", "rank": 1, "summary": "条件化题材推断",
        "priorityReason": "关系待核", "gap": "原始来源未知", "rankChangeConditions": "主体否认则撤回",
        "twoDayReason": "新传闻或带来关注", "evidenceDisclosure": disclosure.to_dict(),
    }


def test_snapshot_cas_append_preserves_unverified_rumor_and_event_level_question(tmp_path):
    path = tmp_path / "research.sqlite"
    initial = _seed(path)
    assert create_research_snapshot(snapshot=initial, db_path=path) == initial

    result = ResearchStageResult(
        "extract_claims", claims=(_claim(),), questions=(_question(),),
        evidence_updates=({"claimId": "claim-1", "sourceRef": {"documentId": "source-1", "revision": 1},
                           "relation": "partially_supports", "location": "p:2", "applicability": {"scope": "event"}},),
        fulltext_requests=(FullTextRequest("full-1", "question-1", {"documentId": "source-1", "revision": 1},
                                           "摘要无法判断项目阶段", "会改变公司比较", "requested"),),
    )
    advanced = advance_research_snapshot(snapshot_id="snapshot-1", expected_revision=1,
                                         research_status="continue_research", execution_status="ok", stage_result=result,
                                         input_sha256="d" * 64, updated_at=LATER, db_path=path)
    assert advanced.revision == 2
    assert list_research_claims(snapshot_id="snapshot-1", db_path=path)[0]["novelty"] == "new_fact"
    assert list_research_questions(snapshot_id="snapshot-1", db_path=path)[0]["companyCodes"] == []
    assert list_research_fulltext_requests(snapshot_id="snapshot-1", db_path=path)[0]["state"] == "requested"

    # Exact recovery after the commit cannot make a second semantic revision.
    replay = advance_research_snapshot(snapshot_id="snapshot-1", expected_revision=1,
                                       research_status="continue_research", execution_status="ok", stage_result=result,
                                       input_sha256="d" * 64, updated_at=LATER, db_path=path)
    assert replay.revision == 2
    recovered = find_research_snapshot(task_id="task-1", event_id="event-1", event_revision=1,
                                       context_sha256="b" * 64, prompt_contract_revision="k10-investigation-v1",
                                       model_parameters_sha256="c" * 64, db_path=path)
    assert recovered == replay
    alternate_id = ResearchSnapshot("snapshot-retry", "task-1", "event-1", 1, NOW, NOW, "b" * 64,
                                    "k10-investigation-v1", "c" * 64, "continue_research", "ok", 1, NOW, NOW)
    assert create_research_snapshot(snapshot=alternate_id, db_path=path) == replay
    with pytest.raises(store.K10Conflict, match="已被其他执行者推进"):
        advance_research_snapshot(snapshot_id="snapshot-1", expected_revision=1,
                                  research_status="continue_research", execution_status="ok", stage_result=result,
                                  input_sha256="e" * 64, updated_at=LATER, db_path=path)


def test_comparison_persists_complete_company_coverage_and_safe_summary(tmp_path):
    path = tmp_path / "comparison.sqlite"
    initial = _seed(path)
    create_research_snapshot(snapshot=initial, db_path=path)
    primary = _assessment()
    pending = {**primary, "companyCode": "300002.SZ", "role": "pending", "rank": None,
               "summary": "关联尚待确认", "priorityReason": "先核关系", "gap": "映射证据不足",
               "rankChangeConditions": "关系确认后比较", "twoDayReason": "暂不作为正式推荐"}
    current = advance_research_snapshot(snapshot_id="snapshot-1", expected_revision=1,
                                        research_status="comparison_complete", execution_status="ok",
                                        stage_result=ResearchStageResult("compare_companies", company_assessments=(primary, pending)),
                                        input_sha256="e" * 64, updated_at=LATER, db_path=path)
    assessments = list_research_assessments(snapshot_id="snapshot-1", db_path=path)
    assert [(item["companyCode"], item["role"], item["rank"]) for item in assessments] == [
        ("300001.SZ", "primary", 1), ("300002.SZ", "pending", None),
    ]
    assert assessments[0]["evidenceDisclosure"]["isRumor"] is True
    assert read_research_snapshot(snapshot_id="snapshot-1", db_path=path) == current

    profile_id, profile_revision = append_approved_execution_profile(db_path=path, created_at=NOW, config_id="research")
    store.bind_task_execution(task_id="task-1", execution_config_id=profile_id, execution_config_revision=profile_revision,
                              binding_kind="scheduled", bound_at=NOW, db_path=path)
    store.create_scan(scan_id="scan-1", window_kind="evening", cutoff_at=NOW, config_id=None, config_revision=None,
                      status="running", coverage={}, created_at=NOW, completed_at=None, db_path=path)
    store.bind_scan_execution(scan_id="scan-1", task_id="task-1", execution_config_id=profile_id,
                              execution_config_revision=profile_revision, binding_kind="scheduled", bound_at=NOW, db_path=path)
    summary = research_summary_for_scan(scan_id="scan-1", db_path=path)
    assert summary == {
        "scanId": "scan-1", "taskId": "task-1", "eventCount": 1,
        "questionCounts": {"open": 0, "answered": 0, "blocked": 0, "abandoned": 0},
        "companyCounts": {"primary": 1, "alternative": 0, "tied": 0, "pending": 1, "excluded": 0, "comparable": 1},
        "researchStatusCounts": {"comparison_complete": 1}, "executionStatusCounts": {"ok": 1},
        "safeFailureCounts": {},
        "comparisonComplete": True, "executionFailed": False,
    }


def test_contract_rejects_verified_rumor_and_migration_forwards_schema_six(tmp_path):
    with pytest.raises(ResearchContractError, match="传闻不能标记"):
        EvidenceDisclosure("verified", True, "unknown", None, (), "若证实再判断")

    path = tmp_path / "schema-six.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE k10_schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        schema._apply_v1(conn); schema._apply_v2(conn); schema._apply_v3(conn); schema._apply_v4(conn); schema._apply_v6(conn)
        for version in (1, 2, 3, 4, 6):
            conn.execute("INSERT INTO k10_schema_migrations VALUES(?,?)", (version, NOW))
    assert schema.initialize_schema(path) == 8 == schema.schema_version(path)
    with sqlite3.connect(path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"k10_research_snapshot_revisions", "k10_research_stage_results", "k10_research_company_assessments"} <= tables


def test_b39_execution_profile_requires_explicit_investigation_option_and_prompt_contract():
    _policy, payload = execution_payload()
    assert validate_execution_config(payload).ready
    missing_prompt = {**payload, "discovery": {**payload["discovery"]}}
    del missing_prompt["discovery"]["investigationPromptContractRevision"]
    assert not validate_execution_config(missing_prompt).ready
    missing_option = {**payload, "discovery": {**payload["discovery"], "modelOptions": dict(payload["discovery"]["modelOptions"])}}
    del missing_option["discovery"]["modelOptions"]["investigation"]
    assert not validate_execution_config(missing_option).ready


def test_lease_guard_runs_inside_write_transaction_before_snapshot_or_cas_append(tmp_path):
    path = tmp_path / "lease.sqlite"
    initial = _seed(path)
    with pytest.raises(RuntimeError, match="lease lost"):
        create_research_snapshot(snapshot=initial, db_path=path,
                                 lease_guard=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")))
    assert read_research_snapshot(snapshot_id="snapshot-1", db_path=path) is None

    create_research_snapshot(snapshot=initial, db_path=path)
    with pytest.raises(RuntimeError, match="lease lost"):
        advance_research_snapshot(snapshot_id="snapshot-1", expected_revision=1,
                                  research_status="pending_verification", execution_status="paused",
                                  stage_result=ResearchStageResult("plan_gaps", safe_error_code="investigation_paused"),
                                  input_sha256="f" * 64, updated_at=LATER, db_path=path,
                                  lease_guard=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")))
    assert read_research_snapshot(snapshot_id="snapshot-1", db_path=path).revision == 1


def test_verification_cutoff_advances_monotonically_and_recovery_read_is_ddl_free(tmp_path):
    path = tmp_path / "cutoff.sqlite"
    initial = _seed(path)
    create_research_snapshot(snapshot=initial, db_path=path)
    first = advance_research_snapshot(
        snapshot_id="snapshot-1", expected_revision=1, research_status="continue_research", execution_status="ok",
        stage_result=ResearchStageResult("extract_claims", claims=(_claim(),)), input_sha256="a" * 64,
        updated_at=LATER, verification_cutoff_at=LATER, db_path=path,
    )
    assert first.verification_cutoff_at == LATER
    with pytest.raises(store.K10Conflict, match="不得倒退"):
        advance_research_snapshot(
            snapshot_id="snapshot-1", expected_revision=2, research_status="continue_research", execution_status="ok",
            stage_result=ResearchStageResult("plan_gaps"), input_sha256="b" * 64,
            updated_at="2026-09-08T13:07:00+00:00", verification_cutoff_at=NOW, db_path=path,
        )
    with pytest.raises(store.K10Conflict, match="不得晚于"):
        advance_research_snapshot(
            snapshot_id="snapshot-1", expected_revision=2, research_status="continue_research", execution_status="ok",
            stage_result=ResearchStageResult("plan_gaps"), input_sha256="b" * 64,
            updated_at=LATER, verification_cutoff_at="2026-09-08T13:07:00+00:00", db_path=path,
        )
    state = read_research_state(snapshot_id="snapshot-1", db_path=path)
    assert state is not None and state["snapshot"].revision == 2
    assert state["claims"][0]["claimId"] == "claim-1"
    assert state["evidenceUpdates"] == []
    assert state["stageResults"] == [{"revision": 2, "action": "extract_claims", "inputSha256": "a" * 64,
                                       "result": ResearchStageResult("extract_claims", claims=(_claim(),)).to_dict()}]
    with sqlite3.connect(path) as conn:
        before = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    assert read_research_state(snapshot_id="snapshot-1", db_path=path) is not None
    with sqlite3.connect(path) as conn:
        after = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    assert after == before


def test_research_backed_publication_cannot_drop_disclosure_and_bridge_is_exact(tmp_path):
    path = tmp_path / "publication-bridge.sqlite"
    snapshot = _seed(path)
    create_research_snapshot(snapshot=snapshot, db_path=path)
    profile_id, profile_revision = append_approved_execution_profile(db_path=path, created_at=NOW, config_id="publication")
    store.bind_task_execution(task_id="task-1", execution_config_id=profile_id, execution_config_revision=profile_revision,
                              binding_kind="scheduled", bound_at=NOW, db_path=path)
    store.create_scan(scan_id="scan-1", window_kind="evening", cutoff_at=NOW, config_id=None, config_revision=None,
                      status="running", coverage={}, created_at=NOW, completed_at=None, db_path=path)
    store.bind_scan_execution(scan_id="scan-1", task_id="task-1", execution_config_id=profile_id,
                              execution_config_revision=profile_revision, binding_kind="scheduled", bound_at=NOW, db_path=path)
    base = {"differences": {"role": "primary", "priorityReason": "关系", "gap": "待核",
                            "rankChangeConditions": "公告", "twoDayReason": "新消息"}}
    with store.write_connection(path) as conn:
        with pytest.raises(ValueError, match="不得省略"):
            store._validate_research_publication_bridge(
                conn, comparison=base, evidence_refs=({"documentId": "source-1", "revision": 1},),
                scan_id="scan-1", event_id="event-1",
            )
        valid = {"differences": {**base["differences"], "evidenceDisclosure": EvidenceDisclosure(
            "unverified", True, "unknown", None, ("来源待核",), "确认后重估").to_dict()},
                 "researchSnapshotId": "snapshot-1", "researchRevision": 1}
        store._validate_research_publication_bridge(
            conn, comparison=valid, evidence_refs=({"documentId": "source-1", "revision": 1},),
            scan_id="scan-1", event_id="event-1",
        )
        invalid = {**valid, "researchRevision": 2}
        with pytest.raises(store.K10Conflict, match="不存在"):
            store._validate_research_publication_bridge(
                conn, comparison=invalid, evidence_refs=({"documentId": "source-1", "revision": 1},),
                scan_id="scan-1", event_id="event-1",
            )


def test_prior_research_reuse_is_source_and_cutoff_bounded_without_fulltext_or_ddl(tmp_path):
    path = tmp_path / "prior-evidence.sqlite"
    snapshot = _seed(path)
    store.enqueue_task(task_id="current-task", kind="evening_scan", idempotency_key="current-reuse",
                       input_version="fixture", input_cutoff_at=NOW, payload={}, budget={}, created_at=NOW, db_path=path)
    store.enqueue_task(task_id="future-task", kind="evening_scan", idempotency_key="future-reuse",
                       input_version="fixture", input_cutoff_at=NOW, payload={}, budget={}, created_at=NOW, db_path=path)
    store.append_document_version(document_id="late-source", source_key="fixture", external_id="late-source",
                                  canonical_url=None, content_sha256="d" * 64,
                                  published_at="2026-09-08T13:07:00+00:00", published_precision="exact",
                                  fetched_at="2026-09-08T13:07:00+00:00", original_text="must not load", excerpt="late",
                                  fetch_version="fixture", metadata={}, created_at=NOW, db_path=path)
    create_research_snapshot(snapshot=snapshot, db_path=path)
    late = Claim("late-claim", "后时点传闻", "rumor", "new_fact", None, None, None, None, None, None,
                 "unverified", "不得提前使用", {"documentId": "late-source", "revision": 1}, "p:1")
    advanced = advance_research_snapshot(
        snapshot_id="snapshot-1", expected_revision=1, research_status="continue_research", execution_status="ok",
        stage_result=ResearchStageResult("extract_claims", claims=(_claim(), late)), input_sha256="1" * 64,
        updated_at=LATER, verification_cutoff_at=LATER, db_path=path,
    )
    advance_research_snapshot(
        snapshot_id="snapshot-1", expected_revision=advanced.revision, research_status="ready_for_comparison", execution_status="ok",
        stage_result=ResearchStageResult("close_research", conclusion={"companyMappings": [{
            "companyCode": "300001.SZ", "affectedStage": "送样", "relationEvidence": [{"documentId": "source-1", "revision": 1}],
            "inference": {"relationship": "supplier"}, "uncertainty": "待独立确认",
        }]}), input_sha256="2" * 64, updated_at=LATER, db_path=path,
    )
    current_snapshot = ResearchSnapshot("current-snapshot", "current-task", "event-1", 1, NOW, NOW, "b" * 64,
                                        "k10-investigation-v1", "c" * 64, "continue_research", "ok", 1, NOW, NOW)
    create_research_snapshot(snapshot=current_snapshot, db_path=path)
    current_claim = Claim("current-claim", "当前任务不得重复复用", "factual_assertion", "new_fact", None, None, None,
                          None, None, None, "unverified", "当前包已有", {"documentId": "source-1", "revision": 1}, "p:1")
    advance_research_snapshot(snapshot_id="current-snapshot", expected_revision=1, research_status="continue_research",
                              execution_status="ok", stage_result=ResearchStageResult("extract_claims", claims=(current_claim,)),
                              input_sha256="3" * 64, updated_at=LATER, db_path=path)
    future_at = "2026-09-08T13:08:00+00:00"
    create_research_snapshot(snapshot=ResearchSnapshot("future-snapshot", "future-task", "event-1", 1, future_at, future_at,
                                                        "b" * 64, "k10-investigation-v1", "c" * 64,
                                                        "continue_research", "ok", 1, future_at, future_at), db_path=path)
    with sqlite3.connect(path) as conn:
        before = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    packet = load_prior_research_evidence(
        task_id="current-task", canonical_key="research:event-1", input_source_refs=(
            {"documentId": "source-1", "revision": 1}, {"documentId": "late-source", "revision": 1},
        ), news_cutoff_at=NOW, verification_cutoff_at=LATER, prompt_contract_revision="k10-investigation-v1",
        model_parameters_sha256="c" * 64, db_path=path,
    )
    with sqlite3.connect(path) as conn:
        after = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    assert after == before
    assert [claim["claimId"] for claim in packet["claims"]] == ["claim-1"]
    assert packet["companyRelations"] == [{
        "companyCode": "300001.SZ", "affectedStage": "送样", "relationEvidence": [{"documentId": "source-1", "revision": 1}],
        "sourceTiming": [{"sourceRef": {"documentId": "source-1", "revision": 1}, "publishedAt": NOW, "fetchedAt": NOW}],
        "inference": {"relationship": "supplier"}, "uncertainty": "待独立确认",
        "provenance": {"taskId": "task-1", "snapshotId": "snapshot-1", "snapshotRevision": 3, "eventId": "event-1", "eventRevision": 1,
                       "newsCutoffAt": NOW, "verificationCutoffAt": LATER},
    }]
    assert any(item["kind"] == "claim" and item["reason"] == "published_after_cutoff" for item in packet["isolated"])
    assert any(item["kind"] == "snapshot" and item["reason"] == "snapshot_after_current_cutoff"
               and item["provenance"]["taskId"] == "future-task" for item in packet["isolated"])
    assert "current-claim" not in {item["claimId"] for item in packet["claims"]}
    assert "originalText" not in repr(packet) and "must not load" not in repr(packet)


_V7_TABLES = (
    "k10_research_company_assessments", "k10_research_fulltext_requests", "k10_research_query_paths",
    "k10_research_questions", "k10_research_evidence_links", "k10_research_claims",
    "k10_research_stage_results", "k10_research_snapshot_revisions",
)
_V6_TABLES = (
    "k10_discovery_retirements", "k10_external_attempts", "k10_article_admissions",
    "k10_title_selection_manifests", "k10_title_triage_items", "k10_title_triage_manifests",
    "k10_title_triage_policy_revisions", "k10_fact_cache", "k10_run_controls",
)


def _seed_pre_b39_database(path: Path, *, schema_version: int) -> dict[str, object]:
    schema.initialize_schema(path)
    store.enqueue_task(task_id="legacy-task", kind="evening_scan", idempotency_key="legacy-task",
                       input_version="legacy", input_cutoff_at=NOW, payload={}, budget={}, created_at=NOW, db_path=path)
    store.append_document_version(document_id="legacy-source", source_key="fixture", external_id="legacy-source",
                                  canonical_url=None, content_sha256="e" * 64, published_at=NOW,
                                  published_precision="exact", fetched_at=NOW, original_text="历史资料", excerpt="历史摘要",
                                  fetch_version="legacy", metadata={}, created_at=NOW, db_path=path)
    store.append_event_revision(event_id="legacy-event", stable_key="legacy:event", headline="历史事件", event_kind="disclosure",
                                facts={"stable": True}, source_refs=[{"documentId": "legacy-source", "revision": 1}],
                                supersedes_revision=None, created_at=NOW, db_path=path)
    store.create_scan(scan_id="legacy-scan", window_kind="evening", cutoff_at=NOW, config_id=None, config_revision=None,
                      status="completed", coverage={"state": "completed"}, created_at=NOW, completed_at=NOW, db_path=path)
    store.create_candidate(candidate_id="legacy-candidate", scan_id="legacy-scan", event_id="legacy-event", event_revision=1,
                           company_code="300001.SZ", comparison={"legacy": "candidate"},
                           evidence=[{"documentId": "legacy-source", "revision": 1}], created_at=NOW, db_path=path)
    state: dict[str, object] = {"candidateColumns": (), "candidateRows": 0, "titleRows": None, "attemptRows": None}
    if schema_version == 6:
        policy, _payload = execution_payload(policy_id="migration-policy")
        store.append_title_triage_policy(policy_id="migration-policy", content=policy, approval_state="approved",
                                         created_at=NOW, approved_at=NOW, db_path=path)
        refs = [{"documentId": "legacy-source", "revision": 1}]
        store.freeze_title_triage_manifest(task_id="legacy-task", input_manifest_sha256=store._hash(refs), window_kind="evening",
                                           policy_id="migration-policy", policy_revision=1,
                                           policy_content_sha256=store._hash(policy), input_count=len(refs), input_refs=refs,
                                           batch_count=1, title_status="frozen", created_at=NOW, db_path=path)
        store.record_title_triage_item(task_id="legacy-task", document_id="legacy-source", revision=1, batch_index=0,
                                       disposition="candidate", matter_key="legacy", merged_ref=None, selection_rank=1,
                                       audit_reason="历史入选", created_at=NOW, db_path=path)
        store.freeze_title_selection_manifest(task_id="legacy-task", selection_manifest_sha256=store._hash(refs),
                                              selected_refs=refs, created_at=NOW, db_path=path)
        store.admit_article(task_id="legacy-task", document_id="legacy-source", revision=1, admission_kind="selected",
                            created_at=NOW, db_path=path)
        store.record_article_outcome(task_id="legacy-task", document_id="legacy-source", revision=1, state="completed",
                                     reason_code=None, updated_at=NOW, db_path=path)
        execution_id, execution_revision = append_approved_execution_profile(
            db_path=path, created_at=NOW, config_id="migration-execution",
        )
        store.bind_task_execution(task_id="legacy-task", execution_config_id=execution_id,
                                  execution_config_revision=execution_revision, binding_kind="scheduled",
                                  bound_at=NOW, db_path=path)
        store.set_run_control(state="open", reason_code="fixture_seed", changed_at=NOW, changed_by="test", db_path=path)
        attempt = store.begin_external_attempt(task_id="legacy-task", stage="model:titleBatch", item_key="batch-0",
                                               attempt_key="legacy:1", input_sha256="f" * 64, started_at=NOW, db_path=path)
        store.settle_external_attempt(attempt_id=attempt["attemptId"], outcome="succeeded",
                                     usage={"promptTokens": 2, "completionTokens": 1, "totalTokens": 3,
                                            "searchRequests": 0, "searchCredits": 0},
                                     settled_at=NOW, error_code=None, db_path=path)
        store.set_run_control(state="closed", reason_code="fixture_paused", changed_at=LATER, changed_by="test", db_path=path)
    with sqlite3.connect(path) as conn:
        # Construct a genuine legacy snapshot: transfer seeded title evidence,
        # then remove only the newly added V2 namespace before the upgrade test.
        if schema_version == 6:
            conn.execute("INSERT INTO k10_title_triage_manifests SELECT task_id,input_manifest_sha256,window_kind,policy_id,policy_revision,policy_content_sha256,80,input_refs_json,batch_count,title_status,selection_status,created_at FROM k10_v2_title_triage_manifests")
            for table in ("title_triage_items", "title_selection_manifests", "article_admissions"):
                conn.execute(f"INSERT INTO k10_{table} SELECT * FROM k10_v2_{table}")
        conn.execute("DROP TABLE k10_morning_review_results")
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'k10_v2_%'").fetchall():
            conn.execute(f"DROP TABLE {row[0]}")
        for table in _V7_TABLES:
            conn.execute(f"DROP TABLE {table}")
        if schema_version == 4:
            for table in _V6_TABLES:
                conn.execute(f"DROP TABLE {table}")
            conn.execute("DELETE FROM k10_schema_migrations WHERE version>=5")
        else:
            conn.execute("DELETE FROM k10_schema_migrations WHERE version>=7")
        state["candidateColumns"] = tuple(row[1] for row in conn.execute("PRAGMA table_info(k10_candidates)"))
        state["candidateRows"] = conn.execute("SELECT COUNT(*) FROM k10_candidates").fetchone()[0]
        if schema_version == 6:
            state["titleRows"] = conn.execute("SELECT COUNT(*) FROM k10_title_triage_items").fetchone()[0]
            state["attemptRows"] = conn.execute("SELECT COUNT(*) FROM k10_external_attempts").fetchone()[0]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT MAX(version) FROM k10_schema_migrations").fetchone()[0] == schema_version
    return state


@pytest.mark.parametrize("prior_version", [4, 6])
def test_controlled_pre_b39_migration_and_restore_preserve_history_and_pause(tmp_path, prior_version):
    path = tmp_path / f"schema-{prior_version}.sqlite"
    before = _seed_pre_b39_database(path, schema_version=prior_version)
    backup = tmp_path / f"schema-{prior_version}.backup.sqlite"
    receipt = migration.migrate_to_v3(target=path, confirmed_target=path, backup=backup, writers_stopped=True)
    assert schema.schema_version(path) == 8
    assert receipt.backup_sha256 == migration.file_sha256(backup)
    with sqlite3.connect(path) as conn:
        assert tuple(row[1] for row in conn.execute("PRAGMA table_info(k10_candidates)")) == before["candidateColumns"]
        assert conn.execute("SELECT COUNT(*) FROM k10_candidates").fetchone()[0] == before["candidateRows"]
        control = conn.execute("SELECT state,reason_code FROM k10_run_controls WHERE control_key='k10_discovery'").fetchone()
        assert control == (("closed", "fixture_paused") if prior_version == 6 else ("closed", "unconfigured_closed"))
        if prior_version == 6:
            assert conn.execute("SELECT COUNT(*) FROM k10_title_triage_items").fetchone()[0] == before["titleRows"]
            assert conn.execute("SELECT COUNT(*) FROM k10_article_admissions WHERE state='completed'").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE state='succeeded'").fetchone()[0] == before["attemptRows"]
    migration.restore_backup(target=path, confirmed_target=path, backup=backup,
                             expected_sha256=receipt.backup_sha256, writers_stopped=True)
    assert migration.file_sha256(path) == receipt.backup_sha256
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT MAX(version) FROM k10_schema_migrations").fetchone()[0] == prior_version
