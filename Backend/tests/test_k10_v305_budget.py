from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from neckline.k10 import store
from neckline.k10.config import validate_execution_config
from neckline.k10.schema import initialize_schema, schema_version


NOW = datetime(2026, 9, 8, 4, tzinfo=timezone.utc).isoformat()
FIELDS = ("maxModelCalls", "maxInputTokens", "maxOutputTokens", "maxTotalTokens",
          "maxFullTextCalls", "maxRetries", "maxSearchRequests", "maxSearchCredits")
STAGES = ("lightweight", "fullText", "verify", "map", "companyComparison", "classify", "prioritize",
          "morning", "analysisPro", "analysisCon", "search", "retry")


def _limits(value: int) -> dict[str, int]:
    return {key: value for key in FIELDS}


def _spend(*, calls: int = 0, tokens: int = 0, search: int = 0, retries: int = 0, full: int = 0) -> dict[str, int]:
    return {"calls": calls, "inputTokens": tokens, "outputTokens": tokens, "totalTokens": tokens * 2,
            "fullTextCalls": full, "retries": retries, "searchRequests": search, "searchCredits": search}


def _rules() -> list[dict]:
    return [{"ruleId": "notice-correction", "revision": 1, "action": "protect", "auditReason": "保留更正",
             "match": {"allPatterns": [{"patternId": "correction", "regex": "更正"}]}}]


def _profile(*, template_id: str, revision: int, content_hash: str, max_calls: int = 2) -> dict:
    limit = _limits(20)
    limit["maxModelCalls"] = max_calls
    return {
        "executionVersion": "k10-execution-v2",
        "discovery": {
            "model": "deepseek-v4-pro",
            "screeningTemplate": {"templateId": template_id, "revision": revision, "contentSha256": content_hash,
                                  "approvalState": "approved", "rules": _rules()},
            "packageLimits": {"maxDocuments": 4, "maxKeyPassageCharacters": 800, "fullTextEnabled": True},
            "documentBatchSize": 4, "understandConcurrency": 1, "keyPassageMaxCharacters": 800,
            "networkMaxAttempts": 1, "jsonRepairMaxAttempts": 0, "retryBackoffSeconds": [1],
            "taskSliceSeconds": 30, "completionDeadlineSeconds": 60, "continuationDelaySeconds": 1,
            "modelOptions": {
                "understand": {"maxTokens": 20, "thinking": {"type": "disabled"}},
                "verify": {"maxTokens": 20, "thinking": {"type": "enabled"}, "reasoningEffort": "high"},
                "companyComparison": {"maxTokens": 20, "thinking": {"type": "enabled"}, "reasoningEffort": "high"},
                "prioritize": {"maxTokens": 20, "thinking": {"type": "enabled"}, "reasoningEffort": "high"},
            },
            "budgets": {"round": limit, "stages": {stage: _limits(20) for stage in STAGES},
                        "reservation": {stage: _limits(20) for stage in STAGES}},
            "priorityOrder": ["publishedMajorContrary", "changedKnownFact", "newEvent"],
        },
    }


def _seed(path: Path, *, max_calls: int = 2) -> tuple[str, int]:
    assert initialize_schema(path) == 5
    template_payload = {"templateVersion": "k10-screening-template-v1", "rules": _rules()}
    template_revision = store.append_screening_template(
        template_id="test-template", payload=template_payload, approval_state="approved", approved_at=NOW,
        created_at=NOW, db_path=path,
    )
    template = store.read_screening_template(template_id="test-template", revision=template_revision, db_path=path)
    assert template is not None
    profile = _profile(template_id="test-template", revision=template_revision, content_hash=template["contentSha256"], max_calls=max_calls)
    assert validate_execution_config(profile).ready
    execution_revision = store.append_execution_config(config_id="v305", payload=profile, created_at=NOW, db_path=path)
    store.enqueue_task(task_id="task", kind="evening_scan", idempotency_key="task", input_version="frozen",
                       input_cutoff_at=NOW, payload={}, budget={"maxAttempts": 1}, created_at=NOW, db_path=path)
    store.bind_task_execution(task_id="task", execution_config_id="v305", execution_config_revision=execution_revision,
                              binding_kind="scheduled", bound_at=NOW, db_path=path)
    return "v305", execution_revision


def test_v305_empty_or_unapproved_template_is_not_a_configured_allow_all() -> None:
    profile = _profile(template_id="template", revision=1, content_hash="0" * 64)
    profile["discovery"]["screeningTemplate"]["rules"] = []
    status = validate_execution_config(profile)
    assert not status.ready
    assert "不得为空" in " ".join(status.errors)


def test_v305_schema_starts_closed_and_never_allows_spend_while_paused(tmp_path: Path) -> None:
    path = tmp_path / "closed.sqlite"
    _seed(path)
    assert schema_version(path) == 5
    assert store.run_control_status(db_path=path)["state"] == "closed"
    result = store.admit_execution_spend(task_id="task", item_key="doc@1", stage="lightweight", kind="model",
                                         reservation_key="doc@1:1", reserved=_spend(calls=1, tokens=2), created_at=NOW, db_path=path)
    assert result == {"state": "paused", "reason": "unconfigured_closed", "reservationId": None}
    assert store.execution_budget_snapshot(task_id="task", db_path=path)["reservationCount"] == 0


def test_v305_forwards_a_v4_database_without_rewriting_frozen_execution_rows(tmp_path: Path) -> None:
    path = tmp_path / "v4-forward.sqlite"
    _seed(path)
    with sqlite3.connect(path) as conn:
        before = conn.execute(
            "SELECT config_id,revision,payload_json,content_sha256,created_at FROM k10_execution_config_revisions"
        ).fetchall()
        conn.execute("DROP TABLE k10_task_execution_spend_reservations")
        conn.execute("DROP TABLE k10_run_controls")
        conn.execute("DROP TABLE k10_screening_template_revisions")
        conn.execute("DROP TABLE k10_screening_runs")
        conn.execute("DROP TABLE k10_fact_cache")
        conn.execute("DELETE FROM k10_schema_migrations WHERE version=5")
    assert initialize_schema(path) == 5
    with sqlite3.connect(path) as conn:
        after = conn.execute(
            "SELECT config_id,revision,payload_json,content_sha256,created_at FROM k10_execution_config_revisions"
        ).fetchall()
    assert after == before
    assert store.run_control_status(db_path=path)["state"] == "closed"


def test_v305_admission_is_atomic_and_unknown_outcome_remains_charged(tmp_path: Path) -> None:
    path = tmp_path / "atomic.sqlite"
    _seed(path, max_calls=1)
    store.set_run_control(state="open", reason_code="test_authorized", changed_at=NOW, changed_by="test", db_path=path)

    def reserve(key: str) -> dict:
        return store.admit_execution_spend(task_id="task", item_key=key, stage="lightweight", kind="model",
                                           reservation_key=key + ":1", reserved=_spend(calls=1, tokens=2), created_at=NOW, db_path=path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, ("a", "b")))
    admitted = [item for item in outcomes if item["state"] == "reserved"]
    denied = [item for item in outcomes if item["state"] == "pending_budget"]
    assert len(admitted) == len(denied) == 1
    reservation = admitted[0]["reservationId"]
    assert reservation
    assert store.settle_execution_spend(reservation_id=reservation, outcome="unknown", actual=None, settled_at=NOW, db_path=path)["state"] == "unknown"
    retry = reserve("retry")
    assert retry["state"] == "pending_budget"
    snapshot = store.execution_budget_snapshot(task_id="task", db_path=path)
    assert snapshot["unknown"]["calls"] == snapshot["occupied"]["calls"] == 1


def test_v305_settlement_uses_actual_usage_and_existing_reservation_never_reopens_call(tmp_path: Path) -> None:
    path = tmp_path / "settle.sqlite"
    _seed(path)
    store.set_run_control(state="open", reason_code="test_authorized", changed_at=NOW, changed_by="test", db_path=path)
    admitted = store.admit_execution_spend(task_id="task", item_key="doc", stage="lightweight", kind="model",
                                           reservation_key="doc:1", reserved=_spend(calls=1, tokens=6), created_at=NOW, db_path=path)
    assert admitted["state"] == "reserved"
    assert store.settle_execution_spend(reservation_id=admitted["reservationId"], outcome="settled",
                                        actual=_spend(calls=1, tokens=2), settled_at=NOW, db_path=path)["state"] == "settled"
    replay = store.admit_execution_spend(task_id="task", item_key="doc", stage="lightweight", kind="model",
                                         reservation_key="doc:1", reserved=_spend(calls=1, tokens=6), created_at=NOW, db_path=path)
    assert replay == {"state": "reused", "reason": None, "reservationId": admitted["reservationId"]}
    status = store.execution_budget_status(task_id="task", db_path=path)
    assert status["state"] == "available"
    assert status["actual"]["totalTokens"] == 4
    assert status["remaining"]["totalTokens"] == 16


def test_v305_explicitly_disabled_budget_is_not_marked_exhausted_without_pending_work(tmp_path: Path) -> None:
    path = tmp_path / "disabled-budget.sqlite"
    _seed(path, max_calls=0)
    status = store.execution_budget_status(task_id="task", db_path=path)
    assert status["state"] == "available"
    assert status["remaining"]["calls"] == 0
    assert status["reservationCount"] == 0


def test_v305_binding_refuses_profile_that_claims_a_different_template(tmp_path: Path) -> None:
    path = tmp_path / "template-mismatch.sqlite"
    _seed(path)
    template = store.read_screening_template(template_id="test-template", revision=1, db_path=path)
    assert template is not None
    profile = _profile(template_id="test-template", revision=1, content_hash=template["contentSha256"])
    profile["discovery"]["screeningTemplate"]["rules"][0]["auditReason"] = "伪造"
    profile_revision = store.append_execution_config(config_id="bad", payload=profile, created_at=NOW, db_path=path)
    store.enqueue_task(task_id="bad-task", kind="evening_scan", idempotency_key="bad-task", input_version="frozen",
                       input_cutoff_at=NOW, payload={}, budget={"maxAttempts": 1}, created_at=NOW, db_path=path)
    with pytest.raises(store.K10Conflict, match="相同的已批准筛分模板"):
        store.bind_task_execution(task_id="bad-task", execution_config_id="bad", execution_config_revision=profile_revision,
                                  binding_kind="scheduled", bound_at=NOW, db_path=path)


def test_v305_screening_audit_and_fact_cache_are_immutable_and_cutoff_aware(tmp_path: Path) -> None:
    path = tmp_path / "facts.sqlite"
    _seed(path)
    manifest = "a" * 64
    store.record_screening_run(task_id="task", input_manifest_sha256=manifest,
                               result={"counts": {"received": 2}, "packages": [{"packageId": "p"}]}, created_at=NOW, db_path=path)
    assert store.read_screening_run(task_id="task", db_path=path)["inputManifestSha256"] == manifest
    with pytest.raises(store.K10Conflict, match="不可被重写"):
        store.record_screening_run(task_id="task", input_manifest_sha256=manifest,
                                   result={"counts": {"received": 3}}, created_at=NOW, db_path=path)
    store.store_fact_cache(cache_key="fact:v1", source_refs=[{"documentId": "doc", "revision": 1}],
                           eligible_at="2026-09-08T04:00:00+00:00", template_content_sha256="b" * 64,
                           model="deepseek-v4-pro", prompt_input_sha256="c" * 64, result={"facts": ["derived"]},
                           created_at=NOW, db_path=path)
    assert store.read_fact_cache(cache_key="fact:v1", cutoff_at="2026-09-08T04:00:00+00:00", db_path=path)["result"] == {"facts": ["derived"]}
    assert store.read_fact_cache(cache_key="fact:v1", cutoff_at="2026-09-08T03:59:59+00:00", db_path=path) is None
