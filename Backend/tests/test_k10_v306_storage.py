from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from neckline.k10 import store
from neckline.k10.config import validate_execution_config
from neckline.k10 import schema
from neckline.k10.schema import initialize_schema, schema_version
from tests.k10_v306_fixture import append_approved_execution_profile


NOW = datetime(2026, 9, 8, 6, tzinfo=timezone.utc).isoformat()


def _task(path, task_id: str = "task") -> tuple[str, int]:
    assert initialize_schema(path) == 6
    config_id, revision = append_approved_execution_profile(db_path=path, created_at=NOW)
    store.enqueue_task(task_id=task_id, kind="evening_scan", idempotency_key=task_id, input_version="frozen",
                       input_cutoff_at=NOW, payload={}, budget={}, created_at=NOW, db_path=path)
    store.bind_task_execution(task_id=task_id, execution_config_id=config_id, execution_config_revision=revision,
                              binding_kind="scheduled", bound_at=NOW, db_path=path)
    return config_id, revision


def _progress_scan(path, *, task_id: str, scan_id: str, coverage: dict) -> tuple[str, int]:
    config_id, revision = _task(path, task_id)
    strategy_revision = store.append_run_config(config_id="strategy", payload={}, created_at=NOW, db_path=path)
    store.create_scan(scan_id=scan_id, window_kind="evening", cutoff_at=NOW, config_id="strategy",
                      config_revision=strategy_revision, status="partial", coverage=coverage,
                      created_at=NOW, completed_at=NOW, db_path=path)
    store.bind_scan_execution(scan_id=scan_id, task_id=task_id, execution_config_id=config_id,
                              execution_config_revision=revision, binding_kind="scheduled", bound_at=NOW, db_path=path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE k10_tasks SET stage='leased' WHERE task_id=?", (task_id,))
    return config_id, revision


def test_v306_config_is_title_first_and_v2_cannot_bind(tmp_path):
    path = tmp_path / "v306.sqlite"
    config_id, revision = _task(path)
    payload = store.read_execution_config(config_id=config_id, revision=revision, db_path=path)["payload"]
    assert validate_execution_config(payload).ready
    old = {"executionVersion": "k10-execution-v2", "discovery": {}}
    assert not validate_execution_config(old).ready


def test_v1_profile_remains_readable_but_cannot_bind_a_live_task(tmp_path):
    path = tmp_path / "historic-profile.sqlite"
    _task(path)
    historic = json.loads((Path(__file__).parents[1] / "neckline/config/k10-execution-v1.json").read_text())
    revision = store.append_execution_config(config_id="historic", payload=historic, created_at=NOW, db_path=path)
    assert store.read_execution_config(config_id="historic", revision=revision, db_path=path)["payload"] == historic
    with pytest.raises(store.K10Conflict, match="V1/V2"):
        store.bind_task_execution(task_id="task", execution_config_id="historic", execution_config_revision=revision,
                                  binding_kind="scheduled", bound_at=NOW, db_path=path)


def test_v306_freezes_all_title_audit_and_preoccupies_selected_articles(tmp_path):
    path = tmp_path / "selection.sqlite"
    _task(path)
    refs = [{"documentId": "a", "revision": 1}, {"documentId": "b", "revision": 1}, {"documentId": "copy", "revision": 1}]
    policy = store.read_title_triage_policy(policy_id="v306-fixture-policy", revision=1, db_path=path)
    assert policy is not None
    manifest = store.freeze_title_triage_manifest(
        task_id="task", input_manifest_sha256=store._hash(refs), window_kind="evening", policy_id=policy["policyId"],
        policy_revision=1, policy_content_sha256=policy["contentSha256"], article_limit=80, input_refs=refs,
        batch_count=1, title_status="frozen", created_at=NOW, db_path=path,
    )
    assert manifest["articleLimit"] == 80
    store.record_title_triage_item(task_id="task", document_id="a", revision=1, batch_index=0, disposition="candidate",
                                   matter_key="a", merged_ref=None, selection_rank=1, audit_reason="新事实", created_at=NOW, db_path=path)
    store.record_title_triage_item(task_id="task", document_id="b", revision=1, batch_index=0, disposition="candidate",
                                   matter_key="b", merged_ref=None, selection_rank=2, audit_reason="新事实", created_at=NOW, db_path=path)
    store.record_title_triage_item(task_id="task", document_id="copy", revision=1, batch_index=0, disposition="exact_duplicate",
                                   matter_key="a", merged_ref={"documentId": "a", "revision": 1}, selection_rank=None,
                                   audit_reason="精确重复", created_at=NOW, db_path=path)
    selected = refs[:2]
    frozen = store.freeze_title_selection_manifest(task_id="task", selection_manifest_sha256=store._hash(selected),
                                                   selected_refs=selected, created_at=NOW, db_path=path)
    assert frozen["selectedRefs"] == selected
    assert store.admit_article(task_id="task", document_id="a", revision=1, admission_kind="selected", created_at=NOW, db_path=path)["state"] == "reused"
    assert store.record_article_outcome(task_id="task", document_id="a", revision=1, state="completed", reason_code=None,
                                        updated_at=NOW, db_path=path)["state"] == "completed"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_article_admissions WHERE task_id='task'").fetchone()[0] == 2
    with pytest.raises(store.K10Conflict, match="不可改写"):
        store.record_title_triage_item(task_id="task", document_id="a", revision=1, batch_index=0, disposition="candidate",
                                       matter_key="a", merged_ref=None, selection_rank=1, audit_reason="变化", created_at=NOW, db_path=path)


def test_v306_attempts_are_idempotent_without_a_total_cap(tmp_path):
    path = tmp_path / "attempt.sqlite"
    _task(path)
    store.set_run_control(state="open", reason_code="isolated_test", changed_at=NOW, changed_by="test", db_path=path)
    attempt = store.begin_external_attempt(task_id="task", stage="titleBatch", item_key="batch-0", attempt_key="batch-0:1",
                                           input_sha256="a" * 64, started_at=NOW, db_path=path)
    assert attempt["state"] == "started"
    assert store.settle_external_attempt(attempt_id=attempt["attemptId"], outcome="succeeded",
                                         usage={"promptTokens": 3, "completionTokens": 2, "totalTokens": 5, "searchRequests": 0, "searchCredits": 0},
                                         settled_at=NOW, error_code=None, db_path=path)["state"] == "succeeded"
    assert store.begin_external_attempt(task_id="task", stage="titleBatch", item_key="batch-0", attempt_key="batch-0:1",
                                        input_sha256="a" * 64, started_at=NOW, db_path=path)["state"] == "reused"
    assert store.external_attempt_summary(task_id="task", db_path=path)["actualUsage"]["totalTokens"] == 5


def test_v306_progress_uses_durable_title_and_tavily_records_not_coverage_guesses(tmp_path):
    path = tmp_path / "progress.sqlite"
    _progress_scan(path, task_id="deep-read-task", scan_id="deep-read-scan",
                   coverage={"executionState": "deep_read", "tavilyExcerptCount": 99})
    refs = [{"documentId": "title-a", "revision": 1}, {"documentId": "title-b", "revision": 1}]
    policy = store.read_title_triage_policy(policy_id="v306-fixture-policy", revision=1, db_path=path)
    assert policy is not None
    store.freeze_title_triage_manifest(
        task_id="deep-read-task", input_manifest_sha256=store._hash(refs), window_kind="evening",
        policy_id=policy["policyId"], policy_revision=1, policy_content_sha256=policy["contentSha256"],
        article_limit=80, input_refs=refs, batch_count=1, title_status="frozen", created_at=NOW, db_path=path,
    )
    store.record_title_triage_item(task_id="deep-read-task", document_id="title-a", revision=1, batch_index=0,
                                   disposition="candidate", matter_key="a", merged_ref=None, selection_rank=1,
                                   audit_reason="新事实", created_at=NOW, db_path=path)
    store.record_title_triage_item(task_id="deep-read-task", document_id="title-b", revision=1, batch_index=0,
                                   disposition="not_selected", matter_key="b", merged_ref=None, selection_rank=None,
                                   audit_reason="无新增价值", created_at=NOW, db_path=path)
    store.freeze_title_selection_manifest(task_id="deep-read-task", selection_manifest_sha256=store._hash(refs[:1]),
                                          selected_refs=refs[:1], created_at=NOW, db_path=path)
    tavily_refs = []
    for identifier, digest in (("tavily-a", "a" * 64), ("tavily-b", "b" * 64)):
        version = store.append_document_version(
            document_id=identifier, source_key="tavily_verification", external_id=identifier, canonical_url=None,
            content_sha256=digest, published_at=NOW, published_precision="exact", fetched_at=NOW,
            original_text=None, excerpt="可核验摘要", fetch_version="tavily-basic-general-v2", metadata={},
            created_at=NOW, db_path=path,
        )
        tavily_refs.append({"documentId": version.document_id, "revision": version.revision})
    for key, rows in (("tavily-one", [tavily_refs[0], tavily_refs[1]]), ("tavily-two", [tavily_refs[0]])):
        store.record_execution_checkpoint(
            task_id="deep-read-task", item_kind="event", item_key=key, stage="tavily_evidence",
            input_sha256=key.ljust(64, "0"), status="completed", attempt_count=1, network_attempt_count=1,
            repair_attempt_count=0, elapsed_ms=0, input_tokens=None, output_tokens=None,
            result={"coverage": {"requestState": "completed"}, "documentRefs": rows},
            safe_error_code=None, safe_error_ref=None, updated_at=NOW, db_path=path,
        )
    progress = store.execution_progress_for_scan(scan_id="deep-read-scan", db_path=path)
    assert progress is not None
    assert progress["stage"] == "deep_read"
    assert progress["articleCounts"]["tavilyExcerpt"] == 2

    _progress_scan(path, task_id="incomplete-task", scan_id="incomplete-scan",
                   coverage={"executionState": "title_incomplete", "pipelineState": "partial"})
    store.freeze_title_triage_manifest(
        task_id="incomplete-task", input_manifest_sha256=store._hash(refs), window_kind="evening",
        policy_id=policy["policyId"], policy_revision=1, policy_content_sha256=policy["contentSha256"],
        article_limit=80, input_refs=refs, batch_count=1, title_status="frozen", created_at=NOW, db_path=path,
    )
    incomplete = store.execution_progress_for_scan(scan_id="incomplete-scan", db_path=path)
    assert incomplete is not None
    assert incomplete["stage"] == "title_triage"
    assert incomplete["titleCounts"]["partial"] == 2


def test_v306_schema_has_no_unpublished_budget_tables(tmp_path):
    path = tmp_path / "schema.sqlite"
    assert initialize_schema(path) == 6 == schema_version(path)
    with sqlite3.connect(path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"k10_title_triage_policy_revisions", "k10_title_triage_manifests", "k10_title_triage_items",
            "k10_title_selection_manifests", "k10_article_admissions", "k10_external_attempts", "k10_discovery_retirements"} <= names
    assert not {"k10_screening_template_revisions", "k10_task_execution_spend_reservations", "k10_screening_runs"} & names


def test_v306_forwards_schema_four_and_removes_unreleased_schema_five_tables(tmp_path):
    path = tmp_path / "schema-five.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE k10_schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        schema._apply_v1(conn)
        schema._apply_v2(conn)
        schema._apply_v3(conn)
        schema._apply_v4(conn)
        for version in range(1, 5):
            conn.execute("INSERT INTO k10_schema_migrations VALUES(?,?)", (version, NOW))
        # These tables model the never-released Schema 5 surface.  Their
        # contents must not survive the direct 5→6 cleanup.
        conn.execute("CREATE TABLE k10_screening_template_revisions(id TEXT)")
        conn.execute("CREATE TABLE k10_task_execution_spend_reservations(id TEXT)")
        conn.execute("CREATE TABLE k10_screening_runs(id TEXT)")
        conn.execute("INSERT INTO k10_schema_migrations VALUES(5,?)", (NOW,))
    assert initialize_schema(path) == 6
    with sqlite3.connect(path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"k10_screening_template_revisions", "k10_task_execution_spend_reservations", "k10_screening_runs"} & names
        assert {"k10_run_controls", "k10_fact_cache", "k10_title_triage_manifests"} <= names


def test_v306_retired_discovery_cannot_be_claimed_reopened_or_reenqueued(tmp_path):
    path = tmp_path / "retired.sqlite"
    config_id, revision = _task(path)
    strategy_revision = store.append_run_config(config_id="strategy", payload={}, created_at=NOW, db_path=path)
    store.create_scan(scan_id="incident", window_kind="evening", cutoff_at=NOW, config_id="strategy",
                      config_revision=strategy_revision, status="running", coverage={"inputDocumentRefs": []},
                      created_at=NOW, completed_at=None, db_path=path)
    store.bind_scan_execution(scan_id="incident", task_id="task", execution_config_id=config_id,
                              execution_config_revision=revision, binding_kind="scheduled", bound_at=NOW, db_path=path)
    receipt = store.retire_interrupted_discovery(scan_id="incident", task_id="task",
                                                 reason_code="user_abandoned_incident_batch", retired_at=NOW, db_path=path)
    assert receipt["scanId"] == "incident"
    assert store.is_discovery_retired(task_id="task", db_path=path)["reasonCode"] == "user_abandoned_incident_batch"
    assert not store.reopen_scan(scan_id="incident", db_path=path)
    assert store.claim_tasks(worker_id="test", now=datetime.now(timezone.utc), lease_for=timedelta(minutes=1),
                             limit=1, db_path=path) == []
    with pytest.raises(store.K10Conflict, match="弃用"):
        store.enqueue_task(task_id="new", kind="evening_scan", idempotency_key="new", input_version="fresh",
                           input_cutoff_at=NOW, payload={}, budget={}, created_at=NOW, db_path=path)
