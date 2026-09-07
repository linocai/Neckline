from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from neckline.k10.schema import initialize_schema
from neckline.k10.store import (
    K10Conflict,
    append_candidate_action,
    append_company_mapping,
    append_document_version,
    append_event_revision,
    append_analysis_revision,
    append_run_config,
    append_source_watermark,
    candidate_state,
    claim_tasks,
    create_candidate,
    create_scan,
    finalize_scan,
    finish_task,
    get_task,
    latest_document_version,
    latest_source_watermark,
    list_company_window_evaluations,
    list_source_document_versions,
    load_candidate_context,
    load_document_versions,
    load_observation_context,
    load_analysis_revision,
    load_task_analysis_config,
    read_run_config,
    observe_candidate,
    renew_task_lease,
)


NOW = "2026-09-06T13:00:00+00:00"


def _seed_candidate(path, *, independent_evidence=False):
    initialize_schema(path)
    first = append_document_version(
        document_id="doc-1", source_key="source-a", external_id="external-1", canonical_url="https://e/1",
        content_sha256="a" * 64, published_at="2026-09-06T12:00:00+00:00", published_precision="exact",
        fetched_at=NOW, original_text="原文", excerpt="摘录", fetch_version="source-v1", metadata={"page": 1},
        created_at=NOW, db_path=path,
    )
    assert first.revision == 1
    if independent_evidence:
        append_document_version(
            document_id="doc-verify", source_key="verification", external_id="verify-1", canonical_url="https://e/verify",
            content_sha256="v" * 64, published_at="2026-09-06T12:30:00+00:00", published_precision="exact",
            fetched_at="2026-09-06T12:40:00+00:00", original_text="独立核验原文", excerpt="独立摘录",
            fetch_version="verification-v1", metadata={"provider": "fixture"}, created_at="2026-09-06T12:40:00+00:00",
            db_path=path,
        )
    facts = {"fact": "new"}
    if independent_evidence:
        facts["verification"] = {"state": "verified", "evidenceRefs": [{"documentId": "doc-verify", "revision": 1}]}
    event = append_event_revision(
        event_id="event-1", stable_key="canonical-event-1", headline="事件", event_kind="policy",
        facts=facts, source_refs=[{"documentId": "doc-1", "revision": 1}],
        supersedes_revision=None, created_at=NOW, db_path=path,
    )
    append_company_mapping(
        mapping_id="mapping-1", event_id=event.event_id, event_revision=event.revision, company_code="300001.SZ",
        affected_stage="upstream", relation_evidence=[{"documentId": "doc-1", "revision": 1}]
        + ([{"documentId": "doc-verify", "revision": 1}] if independent_evidence else []), inference={"step": 1},
        uncertainty="medium", created_at=NOW, db_path=path,
    )
    create_scan(
        scan_id="scan-1", window_kind="evening", cutoff_at="2026-09-06T13:00:00+00:00", config_id=None,
        config_revision=None, status="not_configured", coverage={"source-a": "unconfigured"}, created_at=NOW,
        completed_at=NOW, db_path=path,
    )
    create_candidate(
        candidate_id="candidate-1", scan_id="scan-1", event_id="event-1", event_revision=1,
        company_code="300001.SZ", comparison={"reason": "synthetic", **(
            {"evidenceRefs": [{"documentId": "doc-verify", "revision": 1}]} if independent_evidence else {})},
        evidence=[{"mappingId": "mapping-1"}],
        created_at=NOW, db_path=path,
    )


def test_document_reads_return_source_key_and_enforce_explicit_source_allow_list(tmp_path):
    path = tmp_path / "source-filter.sqlite"
    _seed_candidate(path)
    append_document_version(
        document_id="doc-verification", source_key="tavily_verification", external_id="search-1",
        canonical_url="https://e/search", content_sha256="s" * 64,
        published_at=NOW, published_precision="exact", fetched_at=NOW, original_text=None,
        excerpt="定向核验", fetch_version="tavily-v1", metadata={}, created_at=NOW, db_path=path,
    )

    approved = list_source_document_versions(cutoff_at=None, source_keys=("source-a",), db_path=path)
    assert [(row["documentId"], row["sourceKey"]) for row in approved] == [("doc-1", "source-a")]
    assert list_source_document_versions(cutoff_at=None, source_keys=(), db_path=path) == []
    frozen = load_document_versions(
        refs=({"documentId": "doc-1", "revision": 1}, {"documentId": "doc-verification", "revision": 1}),
        source_keys=("source-a",), db_path=path,
    )
    assert [(row["documentId"], row["sourceKey"]) for row in frozen] == [("doc-1", "source-a")]


def test_latest_company_window_evaluation_is_selected_per_window(tmp_path):
    path = tmp_path / "evaluation-latest.sqlite"
    initialize_schema(path)
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO k10_company_window_evaluation_revisions(company_window_id,revision,state,fact_refs_json,result_json,evaluated_at,created_at) VALUES(?,?,?,?,?,?,?)",
            [
                ("window-a", 1, "pending", "[]", "{}", NOW, NOW),
                ("window-a", 2, "completed", "[]", "{}", NOW, NOW),
                ("window-b", 1, "incomplete", "[]", "{}", NOW, NOW),
                ("window-b", 2, "completed", "[]", "{}", NOW, NOW),
                ("window-b", 3, "completed", "[]", "{}", NOW, NOW),
            ],
        )

    latest = list_company_window_evaluations(db_path=path)
    assert [(row["companyWindowId"], row["revision"]) for row in latest] == [("window-a", 2), ("window-b", 3)]


def test_document_and_event_versions_are_append_only_and_idempotent(tmp_path):
    path = tmp_path / "k10.db"
    _seed_candidate(path)
    assert latest_document_version(document_id="doc-1", db_path=path).revision == 1

    same = append_document_version(
        document_id="doc-1", source_key="source-a", external_id="external-1", canonical_url="https://e/1",
        content_sha256="a" * 64, published_at="2026-09-06T12:00:00+00:00", published_precision="exact",
        fetched_at=NOW, original_text="原文", excerpt="摘录", fetch_version="source-v1", metadata={"page": 1},
        created_at=NOW, db_path=path,
    )
    assert same.revision == 1
    newer = append_document_version(
        document_id="doc-1", source_key="source-a", external_id="external-1", canonical_url="https://e/1",
        content_sha256="b" * 64, published_at="2026-09-06T12:01:00+00:00", published_precision="exact",
        fetched_at="2026-09-06T13:01:00+00:00", original_text="更正原文", excerpt="更正", fetch_version="source-v1",
        metadata={"page": 1}, created_at="2026-09-06T13:01:00+00:00", db_path=path,
    )
    assert newer.revision == 2
    assert latest_document_version(document_id="doc-1", db_path=path) == newer

    with pytest.raises(K10Conflict):
        append_event_revision(
            event_id="event-2", stable_key="canonical-event-1", headline="不同事件", event_kind="policy",
            facts={}, source_refs=[], supersedes_revision=None, created_at=NOW, db_path=path,
        )


def test_observation_uses_event_frozen_document_revision_even_when_fetch_is_after_cutoff(tmp_path):
    path = tmp_path / "late-fetch.sqlite"
    _seed_candidate(path)
    # Revision 1 was published before the report cutoff but was only fetched after it;
    # revision 2 is a later correction and must never silently backfill this observation.
    append_document_version(
        document_id="doc-1", source_key="source-a", external_id="external-1", canonical_url="https://e/1",
        content_sha256="b" * 64, published_at="2026-09-06T12:01:00+00:00", published_precision="exact",
        fetched_at="2026-09-06T14:00:00+00:00", original_text="后续更正", excerpt="更正", fetch_version="source-v2",
        metadata={"page": 2}, created_at="2026-09-06T14:00:00+00:00", db_path=path,
    )
    observation = observe_candidate(
        candidate_id="candidate-1", observation_id="observation-1", action_id="observe-1",
        idempotency_key="observe-request-1", task_id="analysis-1", outbox_id="outbox-1",
        task_input_version="fixture", task_input_cutoff_at="2026-09-06T13:00:00+00:00",
        task_payload={}, task_budget={"maxAttempts": 1}, created_at="2026-09-06T13:01:00+00:00", db_path=path,
    )
    assert observation.created is True
    context = load_observation_context(observation_id="observation-1", cutoff_at="2026-09-06T13:00:00+00:00", db_path=path)
    assert context["documents"] == [{
        "documentId": "doc-1", "revision": 1, "contentSha256": "a" * 64,
        "publishedAt": "2026-09-06T12:00:00+00:00", "publishedPrecision": "exact",
        "fetchedAt": NOW, "originalText": "原文", "excerpt": "摘录", "fetchVersion": "source-v1",
        "metadata": {"page": 1}, "createdAt": NOW, "sourceKey": "source-a",
    }]
    candidate = load_candidate_context(candidate_id="candidate-1", cutoff_at="2026-09-06T13:00:00+00:00", db_path=path)
    assert candidate["candidate"]["state"] == "observed"
    assert [item["revision"] for item in candidate["documents"]] == [1]


def test_context_unions_frozen_verification_comparison_and_bound_mapping_refs_without_newer_versions(tmp_path):
    path = tmp_path / "evidence-union.sqlite"
    _seed_candidate(path, independent_evidence=True)
    # This mapping belongs to the same event but not to candidate-1, so its evidence cannot
    # leak into candidate-1's analysis input.
    append_company_mapping(
        mapping_id="mapping-other", event_id="event-1", event_revision=1, company_code="300002.SZ",
        affected_stage="other", relation_evidence=[{"documentId": "doc-1", "revision": 1}],
        inference={}, uncertainty="fixture", created_at=NOW, db_path=path,
    )
    append_document_version(
        document_id="doc-verify", source_key="verification", external_id="verify-1", canonical_url="https://e/verify",
        content_sha256="w" * 64, published_at="2026-09-06T12:41:00+00:00", published_precision="exact",
        fetched_at="2026-09-06T14:00:00+00:00", original_text="未来更正", excerpt="未来摘录",
        fetch_version="verification-v2", metadata={}, created_at="2026-09-06T14:00:00+00:00", db_path=path,
    )
    observe_candidate(
        candidate_id="candidate-1", observation_id="observation-1", action_id="observe-1",
        idempotency_key="observe-request-1", task_id="analysis-1", outbox_id="outbox-1",
        task_input_version="fixture", task_input_cutoff_at="2026-09-06T13:00:00+00:00",
        task_payload={}, task_budget={"maxAttempts": 1}, created_at="2026-09-06T13:00:00+00:00", db_path=path,
    )
    expected = [{"documentId": "doc-1", "revision": 1}, {"documentId": "doc-verify", "revision": 1}]
    observation = load_observation_context(observation_id="observation-1", cutoff_at="2026-09-06T13:00:00+00:00", db_path=path)
    candidate = load_candidate_context(candidate_id="candidate-1", cutoff_at="2026-09-06T13:00:00+00:00", db_path=path)
    for context in (observation, candidate):
        assert context["frozenEvidenceRefs"] == expected
        assert [(item["documentId"], item["revision"]) for item in context["documents"]] == [
            ("doc-1", 1), ("doc-verify", 1)
        ]
        assert [item["mappingId"] for item in context["mappings"]] == ["mapping-1"]


def test_scan_finalization_and_source_watermarks_are_append_only_and_idempotent(tmp_path):
    path = tmp_path / "k10.db"
    initialize_schema(path)
    create_scan(
        scan_id="scan-running", window_kind="morning", cutoff_at="2026-09-07T01:00:00+00:00",
        config_id=None, config_revision=None, status="running", coverage={"source-a": "running"},
        created_at=NOW, completed_at=None, db_path=path,
    )
    finalize_scan(
        scan_id="scan-running", status="partial", coverage={"source-a": "page-limit"},
        completed_at="2026-09-07T01:02:00+00:00", db_path=path,
    )
    with pytest.raises(K10Conflict):
        finalize_scan(
            scan_id="scan-running", status="completed", coverage={}, completed_at="2026-09-07T01:03:00+00:00", db_path=path,
        )
    payload = dict(watermark_id="watermark-1", source_key="source-a", cursor_value="cursor-10",
                   success_cutoff_at="2026-09-07T01:00:00+00:00", fetched_at="2026-09-07T01:02:00+00:00",
                   scan_id="scan-running", created_at="2026-09-07T01:02:00+00:00", db_path=path)
    append_source_watermark(**payload)
    append_source_watermark(**payload)
    assert latest_source_watermark(source_key="source-a", db_path=path)["cursorValue"] == "cursor-10"


def test_actions_are_append_only_and_observe_enqueues_exactly_one_task_atomically(tmp_path):
    path = tmp_path / "k10.db"
    _seed_candidate(path)
    assert candidate_state(candidate_id="candidate-1", db_path=path) == "offered"
    append_candidate_action(
        action_id="skip-1", candidate_id="candidate-1", action="skip", idempotency_key="skip-request-1",
        reason="不关注", created_at=NOW, db_path=path,
    )
    assert candidate_state(candidate_id="candidate-1", db_path=path) == "skipped"
    append_candidate_action(
        action_id="restore-1", candidate_id="candidate-1", action="restore", idempotency_key="restore-request-1",
        reason=None, created_at="2026-09-06T13:01:00+00:00", db_path=path,
    )
    assert candidate_state(candidate_id="candidate-1", db_path=path) == "offered"

    observed = observe_candidate(
        action_id="observe-1", observation_id="observation-1", task_id="task-1", outbox_id="outbox-1",
        candidate_id="candidate-1", idempotency_key="observe-request-1", task_input_version="config-1",
        task_input_cutoff_at=NOW, task_payload={"mode": "full"}, task_budget={"maxTokens": 123},
        created_at="2026-09-06T13:02:00+00:00", db_path=path,
    )
    assert observed.created is True
    assert candidate_state(candidate_id="candidate-1", db_path=path) == "observed"
    task = get_task(task_id="task-1", db_path=path)
    assert task and task.status == "queued" and task.payload["observationId"] == "observation-1"

    replay = observe_candidate(
        action_id="ignored-on-replay", observation_id="ignored-on-replay", task_id="ignored-on-replay",
        outbox_id="ignored-on-replay", candidate_id="candidate-1", idempotency_key="observe-request-1",
        task_input_version="other", task_input_cutoff_at="other", task_payload={}, task_budget={},
        created_at="2026-09-06T13:03:00+00:00", db_path=path,
    )
    assert replay.observation_id == "observation-1"
    assert replay.created is False

    append_candidate_action(
        action_id="withdraw-1", candidate_id="candidate-1", action="withdraw", idempotency_key="withdraw-request-1",
        reason="later", created_at="2026-09-06T13:04:00+00:00", db_path=path,
    )
    resumed = observe_candidate(
        action_id="observe-2", observation_id="must-not-create-second-observation", task_id="must-not-create-second-task",
        outbox_id="must-not-create-second-outbox", candidate_id="candidate-1", idempotency_key="observe-request-2",
        task_input_version="config-1", task_input_cutoff_at=NOW, task_payload={}, task_budget={},
        created_at="2026-09-06T13:05:00+00:00", db_path=path,
    )
    assert resumed.observation_id == "observation-1"
    assert resumed.created is False


def test_task_leases_recover_after_expiry_and_prevent_stale_finish(tmp_path):
    path = tmp_path / "k10.db"
    _seed_candidate(path)
    observe_candidate(
        action_id="observe-1", observation_id="observation-1", task_id="task-1", outbox_id="outbox-1",
        candidate_id="candidate-1", idempotency_key="observe-request-1", task_input_version="config-1",
        task_input_cutoff_at=NOW, task_payload={}, task_budget={}, created_at=NOW, db_path=path,
    )
    base = datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc)
    first = claim_tasks(worker_id="worker-a", now=base, lease_for=timedelta(minutes=1), limit=1, db_path=path)
    assert [task.task_id for task in first] == ["task-1"]
    assert claim_tasks(worker_id="worker-b", now=base, lease_for=timedelta(minutes=1), limit=1, db_path=path) == []
    renewed = renew_task_lease(
        task_id="task-1", worker_id="worker-a", now=base + timedelta(seconds=30), lease_for=timedelta(minutes=2), db_path=path,
    )
    assert renewed.lease_owner == "worker-a"
    assert claim_tasks(worker_id="worker-b", now=base + timedelta(minutes=2), lease_for=timedelta(minutes=1), limit=1, db_path=path) == []
    second = claim_tasks(worker_id="worker-b", now=base + timedelta(minutes=3), lease_for=timedelta(minutes=1), limit=1, db_path=path)
    assert second[0].attempt_count == 2
    with pytest.raises(K10Conflict):
        finish_task(
            task_id="task-1", worker_id="worker-a", status="completed", stage="done", checkpoint={}, error_text=None,
            finished_at=base + timedelta(minutes=3), db_path=path,
        )
    finish_task(
        task_id="task-1", worker_id="worker-b", status="not_configured", stage="config", checkpoint={"missing": ["modelRoutes"]},
        error_text="参数未配置", finished_at=base + timedelta(minutes=3), db_path=path,
    )
    assert get_task(task_id="task-1", db_path=path).status == "not_configured"


def test_analysis_can_reload_the_exact_frozen_config_and_prior_revision(tmp_path):
    path = tmp_path / "k10.db"
    _seed_candidate(path)
    revision = append_run_config(config_id="config-1", payload={"modelRoutes": {"analysis": "deepseek-v4-pro"}},
                                 created_at=NOW, db_path=path)
    observe_candidate(
        action_id="observe-1", observation_id="observation-1", task_id="task-1", outbox_id="outbox-1",
        candidate_id="candidate-1", idempotency_key="observe-request-1", task_input_version="config-1",
        task_input_cutoff_at=NOW, task_payload={"configId": "config-1", "configRevision": revision},
        task_budget={}, created_at=NOW, db_path=path,
    )
    assert read_run_config(config_id="config-1", revision=revision, db_path=path)["payload"]["modelRoutes"]["analysis"] == "deepseek-v4-pro"
    assert load_task_analysis_config(task_id="task-1", db_path=path)["configRevision"] == revision
    append_analysis_revision(
        analysis_id="pro-1", observation_id="observation-1", revision=1, analysis_kind="pro",
        input_cutoff_at=NOW, input_lineage={"document": "doc-1"}, content={"answer": "saved"},
        status="completed", created_at=NOW, db_path=path,
    )
    reused = load_analysis_revision(observation_id="observation-1", input_cutoff_at=NOW, analysis_kind="pro", db_path=path)
    assert reused and reused["content"] == {"answer": "saved"}
