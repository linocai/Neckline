from __future__ import annotations

import pytest

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from neckline.k10 import store
from neckline.k10.opportunity_discovery import (
    ComparisonValidationError,
    publishable_assessments,
    validate_event_comparison,
)
from neckline.k10.morning import build_morning_update
from neckline.k10.morning_runtime import _frozen_evidence_disclosure
from neckline.k10.notifications import DeliveryResult, NotificationRetryPolicy, dispatch_task_notifications, enqueue_task_notification, initialize_notifications_schema
from neckline.k10.research_contracts import ResearchSnapshot
from neckline.k10 import research_store
from neckline.k10.schema import initialize_schema, read_connection
from neckline.k10.worker import TaskResult, run_once
from neckline.k10.worker import TaskContext
from neckline.k10.types import Task
from tests.k10_v306_fixture import append_approved_execution_profile


def _row(role: str, rank: int | None, *, disclosure=None, summary: str | None = None):
    differences = {"role": role, "priorityReason": "关系和增量更直接", "gap": "竞争对象仍有差距",
                   "rankChangeConditions": "关键反证或订单变化", "twoDayReason": "两日催化未落地"}
    if disclosure is not None:
        differences["evidenceDisclosure"] = disclosure
    return {"summary": summary or f"{role} 公司已完成比较", "differences": differences, "rank": rank}


def test_complete_comparison_keeps_pending_excluded_readable_but_publishes_only_derived_roles():
    comparisons = {
        "300001.SZ": _row("primary", 1),
        "300002.SZ": _row("alternative", 2),
        "300003.SZ": _row("pending", None, summary="关键竞争缺口仍会改变判断"),
        "300004.SZ": _row("excluded", None, summary="已确认不属于本轮影响路径"),
    }
    disclosure = {"verificationStatus": "partially_supported", "isRumor": False, "originStatus": "identified",
                  "originEvidenceRef": {"documentId": "source-1", "revision": 1}, "unverifiedReasons": [],
                  "conditionalAnalysis": None}
    comparisons = {code: _row(row["differences"]["role"], row["rank"], disclosure=disclosure, summary=row["summary"])
                   for code, row in comparisons.items()}
    validate_event_comparison(summary="同一事件比较覆盖全部输入公司", comparisons=comparisons,
                              company_codes=tuple(comparisons), require_evidence_disclosure=True)
    assert set(publishable_assessments(comparisons)) == {"300001.SZ", "300002.SZ"}
    assert comparisons["300003.SZ"]["summary"].startswith("关键竞争")
    assert comparisons["300004.SZ"]["summary"].startswith("已确认")


def test_publishable_assessments_accepts_the_flat_persisted_research_shape():
    rows = {
        "300001.SZ": {"role": "primary", "rank": 1},
        "300002.SZ": {"role": "pending", "rank": None},
        "300003.SZ": {"role": "excluded", "rank": None},
    }
    assert set(publishable_assessments(rows)) == {"300001.SZ"}


def test_unverified_rumor_can_be_formally_ranked_when_disclosure_is_complete():
    disclosure = {
        "verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
        "originEvidenceRef": None, "unverifiedReasons": ["尚无独立来源确认最初传闻"],
        "conditionalAnalysis": "若订单传闻在两日内获公司确认，比较结论才成立。",
    }
    comparisons = {"300001.SZ": _row("primary", 1, disclosure=disclosure)}
    validate_event_comparison(summary="传闻在完整比较后可进入正式候选", comparisons=comparisons,
                              company_codes=("300001.SZ",), require_evidence_disclosure=True)
    assert set(publishable_assessments(comparisons)) == {"300001.SZ"}


@pytest.mark.parametrize("role,rank,code", [
    ("pending", 1, "compare_company_ranking_invalid"),
    ("excluded", 2, "compare_company_ranking_invalid"),
])
def test_pending_or_excluded_cannot_be_smuggled_into_publish_ranking(role, rank, code):
    comparisons = {"300001.SZ": _row(role, rank)}
    with pytest.raises(ComparisonValidationError) as raised:
        validate_event_comparison(summary="完整覆盖", comparisons=comparisons, company_codes=("300001.SZ",))
    assert raised.value.code == code


def test_unverified_rumor_cannot_omit_its_conditions_or_claim_a_fake_origin_ref():
    missing_conditions = {"verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
                          "originEvidenceRef": None, "unverifiedReasons": ["来源未独立确认"],
                          "conditionalAnalysis": ""}
    fake_origin = {"verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
                   "originEvidenceRef": {"documentId": "invented", "revision": 1},
                   "unverifiedReasons": ["来源未独立确认"], "conditionalAnalysis": "需等待独立确认"}
    for disclosure in (missing_conditions, fake_origin):
        with pytest.raises(ComparisonValidationError) as raised:
            validate_event_comparison(summary="完整覆盖", comparisons={"300001.SZ": _row("primary", 1, disclosure=disclosure)},
                                      company_codes=("300001.SZ",))
        assert raised.value.code == "evidence_disclosure_invalid"


def test_b39_comparison_requires_a_disclosure_and_a_rumor_never_claims_verified():
    no_disclosure = {"300001.SZ": _row("primary", 1)}
    with pytest.raises(ComparisonValidationError, match="证据披露"):
        validate_event_comparison(summary="B39 比较", comparisons=no_disclosure, company_codes=("300001.SZ",),
                                  require_evidence_disclosure=True)
    verified_rumor = {"verificationStatus": "verified", "isRumor": True, "originStatus": "unknown",
                      "originEvidenceRef": None, "unverifiedReasons": [], "conditionalAnalysis": "等待证实"}
    with pytest.raises(ComparisonValidationError, match="传闻不能"):
        validate_event_comparison(summary="B39 比较", comparisons={"300001.SZ": _row("primary", 1, disclosure=verified_rumor)},
                                  company_codes=("300001.SZ",), require_evidence_disclosure=True)


def test_morning_preserves_the_frozen_rumor_disclosure_in_its_lifecycle_payload():
    disclosure = {
        "verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
        "originEvidenceRef": None, "unverifiedReasons": ["没有独立确认"],
        "conditionalAnalysis": "只在公司公告确认后重新判断。",
    }
    base = {"candidate": {"comparison": {"differences": {"evidenceDisclosure": disclosure}}}}
    frozen = _frozen_evidence_disclosure(payload={"evidenceDisclosure": disclosure}, base=base)
    update = build_morning_update(
        cutoff_at="2026-09-08T01:00:00+00:00", candidate_id="candidate-1", observation_id=None,
        reason_status="needs_review", source_status="complete", observation_status="needs_review",
        material_contrary_evidence=[], source_refs=[{"documentId": "doc-1", "revision": 1}],
        independent_verification_refs=[], summary="传闻尚未获得独立确认。", evidence_disclosure=frozen,
    )
    assert update.to_dict()["evidenceDisclosure"] == disclosure
    with pytest.raises(Exception, match="不得改写"):
        _frozen_evidence_disclosure(payload={**{"evidenceDisclosure": {**disclosure, "conditionalAnalysis": "伪造新结论"}}}, base=base)


def test_worker_refuses_completed_task_when_its_linked_research_snapshot_failed(tmp_path, monkeypatch):
    now = datetime(2026, 9, 8, 13, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "research-terminal.sqlite"
    initialize_schema(db_path)
    store.set_run_control(state="open", reason_code="fixture", changed_at=now.isoformat(), changed_by="test", db_path=db_path)
    task = store.enqueue_task(
        task_id="task-research", kind="analysis", idempotency_key="task-research", input_version="cfg",
        input_cutoff_at=now.isoformat(), payload={}, budget={"maxAttempts": 1}, created_at=now.isoformat(), db_path=db_path,
    )
    snapshot = ResearchSnapshot(
        "snapshot-failed", "task-research", "event-1", 1, now.isoformat(), now.isoformat(), "a" * 64,
        "k10-investigation-v1", "b" * 64, "continue_research", "failed", 1, now.isoformat(), now.isoformat(),
    )
    monkeypatch.setattr(research_store, "read_research_snapshot", lambda **_: snapshot)
    config_id, revision = append_approved_execution_profile(db_path=db_path, created_at=now.isoformat(), config_id="research-terminal")
    store.bind_task_execution(task_id=task.task_id, execution_config_id=config_id, execution_config_revision=revision,
                              binding_kind="scheduled", bound_at=now.isoformat(), db_path=db_path)
    result = run_once(
        db_path=db_path, worker_id="fixture", lease_for=timedelta(seconds=30), clock=lambda: now,
        handlers={"analysis": lambda _: TaskResult("completed", "published", {"researchSnapshotId": "snapshot-failed"})},
    )
    assert result is not None
    assert result.status == "failed"
    with read_connection(db_path) as connection:
        assert connection.execute("SELECT stage FROM k10_tasks WHERE task_id='task-research'").fetchone()[0] == "research_state"


def test_worker_refuses_research_required_completion_without_a_snapshot(tmp_path):
    now = datetime(2026, 9, 8, 13, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "research-missing.sqlite"
    initialize_schema(db_path)
    store.set_run_control(state="open", reason_code="fixture", changed_at=now.isoformat(), changed_by="test", db_path=db_path)
    task = store.enqueue_task(task_id="task-missing", kind="analysis", idempotency_key="task-missing", input_version="cfg",
                              input_cutoff_at=now.isoformat(), payload={}, budget={"maxAttempts": 1}, created_at=now.isoformat(), db_path=db_path)
    config_id, revision = append_approved_execution_profile(db_path=db_path, created_at=now.isoformat(), config_id="research-missing")
    store.bind_task_execution(task_id=task.task_id, execution_config_id=config_id, execution_config_revision=revision,
                              binding_kind="scheduled", bound_at=now.isoformat(), db_path=db_path)
    result = run_once(
        db_path=db_path, worker_id="fixture", lease_for=timedelta(seconds=30), clock=lambda: now,
        handlers={"analysis": lambda _: TaskResult("completed", "published", {"researchRequired": True})},
    )
    assert result is not None and result.status == "failed"


def test_morning_handler_carries_frozen_disclosure_into_model_and_report(monkeypatch):
    from neckline.k10 import morning_runtime

    disclosure = {
        "verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
        "originEvidenceRef": None, "unverifiedReasons": ["原始发布者未知"],
        "conditionalAnalysis": "公司确认前只按条件成立处理。",
    }
    messages = []

    class Provider:
        def chat(self, request, **_kwargs):
            messages.extend(request)
            return SimpleNamespace(ok=True, content=json.dumps({
                "material": False, "reasonStatus": "needs_review", "observationStatus": "needs_review",
                "summary": "传闻仍未获得独立确认。", "materialContraryEvidence": [],
            }, ensure_ascii=False))

    task = Task("morning-rumor", "morning_review", "queued", 1, None, None, {
        "candidateId": "candidate-rumor", "originalCutoffAt": "2026-09-08T00:00:00+00:00",
        "morningEvidenceRefs": [{"documentId": "doc-rumor", "revision": 1}], "independentVerificationRefs": [],
        "companyWindowId": "window-rumor", "displayRank": 1, "selectionState": "kept", "lifecycle": "active",
        "isNew": False, "sourceStatus": "complete", "configId": "cfg", "configRevision": 1,
        "evidenceDisclosure": disclosure,
    })
    context = TaskContext(task, {}, {}, "cfg", "2026-09-08T01:00:00+00:00", Path("/tmp/v310-morning.sqlite"), Event())
    monkeypatch.setattr(morning_runtime.store, "read_run_config", lambda **_: {"payload": {"modelRoutes": {"morning": "deepseek-v4-pro"}}})
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", lambda **_: SimpleNamespace(provider=Provider(), error=None))
    monkeypatch.setattr(morning_runtime.store, "load_candidate_context", lambda **_: {
        "observationIds": [], "opportunity": {"opportunityId": "opportunity-rumor", "state": "active"},
        "candidate": {"comparison": {"differences": {"evidenceDisclosure": disclosure}}},
    })
    monkeypatch.setattr(morning_runtime.store, "load_document_versions", lambda *, refs, **_: [
        {"documentId": item["documentId"], "revision": item["revision"], "fetchedAt": "2026-09-08T00:30:00+00:00"}
        for item in refs
    ])
    monkeypatch.setattr(morning_runtime, "record_morning_update", lambda **_: "lifecycle-rumor")
    result = morning_runtime.morning_review_handler(context)
    assert result.status == "completed"
    assert disclosure == result.checkpoint["reportItem"]["content"]["evidenceDisclosure"]
    assert disclosure == result.checkpoint["reportItem"]["content"]["update"]["evidenceDisclosure"]
    assert "evidenceDisclosure" in messages[-1].content and "unverified" in messages[-1].content


def test_notification_outbox_persists_and_dispatches_the_frozen_rumor_disclosure(tmp_path):
    now = datetime(2026, 9, 8, 13, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "rumor-notification.sqlite"
    initialize_schema(db_path)
    initialize_notifications_schema(db_path, applied_at=now)
    disclosure = {
        "verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
        "originEvidenceRef": None, "unverifiedReasons": ["独立来源缺失"], "conditionalAnalysis": "等待公司确认。",
    }
    store.enqueue_task(task_id="task-notification", kind="evening_scan", idempotency_key="task-notification", input_version="cfg",
                       input_cutoff_at=now.isoformat(), payload={"scanId": "scan-rumor"}, budget={"maxAttempts": 1},
                       created_at=now.isoformat(), db_path=db_path)
    claimed = store.claim_tasks(worker_id="fixture", now=now, lease_for=timedelta(seconds=30), limit=1, db_path=db_path)[0]
    store.finish_task(task_id=claimed.task_id, worker_id="fixture", status="completed", stage="published",
                      checkpoint={"evidenceDisclosure": disclosure}, error_text=None, finished_at=now, db_path=db_path)
    notification = enqueue_task_notification(task_id=claimed.task_id, db_path=db_path, created_at=now)
    assert notification.evidence_disclosure == disclosure
    sent: list[dict] = []
    dispatched = dispatch_task_notifications(
        db_path=db_path, list_device_tokens=lambda: ("device",), delete_device=lambda _: False,
        sender=lambda **kwargs: sent.append(kwargs) or DeliveryResult(ok=True), worker_id="fixture-push", now=now,
        retry_policy=NotificationRetryPolicy(timedelta(seconds=1), timedelta(seconds=2)),
    )
    assert dispatched == 1
    assert sent[0]["evidence_disclosure"] == disclosure
    assert "包含未核实传闻" in sent[0]["body"]


def test_notification_runtime_forwards_the_frozen_disclosure_to_apns_custom_payload(tmp_path, monkeypatch):
    from neckline.api.stores import upsert_device
    from neckline.k10 import notification_runtime

    now = datetime.now(timezone.utc).replace(microsecond=0)
    db_path = tmp_path / "rumor-apns.sqlite"
    initialize_schema(db_path)
    initialize_notifications_schema(db_path, applied_at=now)
    disclosure = {
        "verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
        "originEvidenceRef": None, "unverifiedReasons": ["独立来源缺失"], "conditionalAnalysis": "等待公司确认。",
    }
    store.enqueue_task(task_id="task-apns", kind="evening_scan", idempotency_key="task-apns", input_version="cfg",
                       input_cutoff_at=now.isoformat(), payload={"scanId": "scan-rumor"}, budget={"maxAttempts": 1},
                       created_at=now.isoformat(), db_path=db_path)
    claimed = store.claim_tasks(worker_id="fixture", now=now, lease_for=timedelta(seconds=30), limit=1, db_path=db_path)[0]
    store.finish_task(task_id=claimed.task_id, worker_id="fixture", status="completed", stage="published",
                      checkpoint={"evidenceDisclosure": disclosure}, error_text=None, finished_at=now, db_path=db_path)
    enqueue_task_notification(task_id=claimed.task_id, db_path=db_path, created_at=now)
    upsert_device("fixture-device", db_path=db_path)
    sent: list[dict] = []
    monkeypatch.setattr(notification_runtime, "apns_readiness", lambda: SimpleNamespace(ready=True, code="ready"))
    monkeypatch.setattr(notification_runtime, "push_kind_enabled", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(notification_runtime, "send_push", lambda *args, **kwargs: sent.append(kwargs) or SimpleNamespace(ok=True, reason="ok"))
    captured: dict[str, object] = {}
    monkeypatch.setattr(notification_runtime, "dispatch_task_notifications", lambda **kwargs: captured.update(kwargs) or 0)
    notification_runtime.create_notification_maintenance(
        db_path=db_path, worker_id="fixture", delivery_config=notification_runtime.NotificationDeliveryConfig(
            NotificationRetryPolicy(timedelta(seconds=1), timedelta(seconds=2)), 10,
        ),
    )()
    sender = captured["sender"]
    sender(token="fixture-device", title="title", body="body", kind="k10_evening", deep_link={"scanId": "scan-rumor"},
           evidence_disclosure=disclosure, collapse_id="fixture")
    assert sent[0]["custom"]["evidenceDisclosure"] == disclosure
