"""September 13 body failures: normalize, repair, and recover via real workers."""
import copy
from datetime import timedelta
import json
import sqlite3
from types import SimpleNamespace

import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses
from tests.test_b61_output_recovery import api_for
from tests.test_v310_investigation import _understand_event


def body():
    return {"events": [_understand_event(canonical_key="event", claim_id="claim")], "needsFullText": False}


@pytest.mark.parametrize("mode", ["needs_material", "missing_source", "missing_claims"])
def test_cli_worker_normalizes_or_repairs_only_affected_body_and_publishes(tmp_path, monkeypatch, mode):
    seen = []
    def edit(value):
        if "events" not in value:
            return
        seen.append(copy.deepcopy(value))
        if mode == "needs_material":
            value["needsFullText"] = True
        elif mode == "missing_source":
            for event in value["events"]:
                event.pop("sourceRefs")
        elif len(seen) == 1:
            for event in value["events"]:
                event.pop("claims")
    messages = []
    def observe(request):
        message = json.loads(request.content)["messages"][-1]["content"]
        messages.append(message)
    edit_responses(monkeypatch, edit)
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True, request_observer=observe)
    assert task.status == "completed"
    assert calls.count("understand") == (2 if mode == "missing_claims" else 1)
    assert calls.count("titleBatch") == 1
    report = api_for(db).get("/api/v1/k10/v2/reports/latest").json()["report"]
    assert report["eveningCards"] and report["status"] == "completed"
    from neckline.k10.notifications import initialize_notifications_schema, enqueue_task_notification, dispatch_task_notifications, DeliveryResult, NotificationRetryPolicy
    initialize_notifications_schema(db)
    notification = enqueue_task_notification(task_id=task_id, db_path=db, created_at=e2e.RUN_AT)
    deliveries = []
    def send(**kwargs):
        deliveries.append(kwargs["token"])
        return DeliveryResult(ok=True)
    args = dict(db_path=db, list_device_tokens=lambda: ("mac-fixture", "ios-fixture"), delete_device=lambda _: False,
        sender=send, worker_id="body-notify", now=e2e.RUN_AT, notification_id=notification.notification_id,
        retry_policy=NotificationRetryPolicy(timedelta(seconds=30), timedelta(minutes=15)))
    assert dispatch_task_notifications(**args) == 1
    assert dispatch_task_notifications(**args) == 0
    assert sorted(deliveries) == ["ios-fixture", "mac-fixture"]
    if mode == "needs_material":
        assert any('"additional_material_unresolved"' in message and '"action"' in message for message in messages)
    if mode == "missing_claims":
        assert any('events[].claims' in message and '上次输出未通过校验' in message for message in messages)


@pytest.mark.parametrize("mode", ["needs_material", "missing_source", "missing_claims"])
def test_paid_legacy_body_recovery_uses_original_task_and_no_repeat_for_derivable_results(tmp_path, monkeypatch, mode):
    decoder = pipeline.DeepSeekDiscoveryModel._decode_understand
    active = [True]
    def edit(value):
        if "events" not in value or not active[0]:
            return
        if mode == "needs_material":
            value["needsFullText"] = True
        else:
            for event in value["events"]:
                event.pop("sourceRefs" if mode == "missing_source" else "claims")
    edit_responses(monkeypatch, edit)
    def legacy(raw, **kwargs):
        if mode == "needs_material" and raw.get("needsFullText"):
            raise pipeline.PipelineError("全文理解仍要求更多资料", code="understand_full_incomplete")
        if mode == "missing_source" and "sourceRefs" not in raw["events"][0]:
            raise pipeline.PipelineError("模型输出缺少 sourceRefs")
        if mode == "missing_claims" and "claims" not in raw["events"][0]:
            raise pipeline.PipelineError("理解输出缺少 claims", code="investigation_claims_missing")
        return decoder(raw, **kwargs)
    with monkeypatch.context() as old:
        old.setattr(pipeline.DeepSeekDiscoveryModel, "_decode_understand", staticmethod(legacy))
        db, task_id, first, calls, _ = e2e._run(tmp_path, old, v2=True)
        assert first.status == "failed" and calls.count("understand") == 1
        scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
        before_hash = frozen_scan_input_sha256(scan_id=scan_id, db_path=db)
        with sqlite3.connect(db) as conn:
            before = conn.execute("SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND status='completed' ORDER BY item_key", (task_id,)).fetchall()
        old.setattr(pipeline.DeepSeekDiscoveryModel, "_decode_understand", staticmethod(decoder))
        active[0] = False
        assert recover_scan(db_path=db, scan_id=scan_id, execution_config_id="b39-execution", execution_config_revision=1,
            confirmed_input_sha256=before_hash, now=e2e.RUN_AT) == task_id
        resumed = e2e._http_transport(old, v2=True)
        done = run_once(db_path=db, task_id=task_id, worker_id="body-recovery", lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path/"parquet"), clock=lambda:e2e.RUN_AT)
        assert done.status == "completed"
        assert resumed.count("understand") == (1 if mode == "missing_claims" else 0)
        assert "titleBatch" not in resumed and frozen_scan_input_sha256(scan_id=scan_id, db_path=db) == before_hash
        with sqlite3.connect(db) as conn:
            after = conn.execute("SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND status='completed' ORDER BY item_key", (task_id,)).fetchall()
        assert all(row in after for row in before)
        assert api_for(db).get("/api/v1/k10/v2/reports/latest").json()["report"]["eveningCards"]


def test_source_derivation_never_overwrites_explicit_conflicting_evidence():
    raw = body()
    raw["events"][0]["sourceRefs"] = [{"documentId":"unknown", "revision":1}]
    with pytest.raises(pipeline.PipelineError, match="引用"):
        pipeline.DeepSeekDiscoveryModel._decode_understand(raw, require_claims=True, full_text=True)


def test_missing_sources_without_claims_cannot_invent_evidence():
    raw = body()
    raw["events"][0].pop("sourceRefs")
    raw["events"][0]["claims"] = []
    with pytest.raises(pipeline.PipelineError) as error:
        pipeline.DeepSeekDiscoveryModel._decode_understand(raw, require_claims=True, full_text=True)
    assert error.value.code == "understand_json_contract_invalid"


def test_empty_incomplete_body_cannot_silently_become_no_events():
    with pytest.raises(pipeline.PipelineError):
        pipeline.DeepSeekDiscoveryModel._decode_understand({"events":[], "needsFullText":True}, require_claims=True, full_text=True)
    assert pipeline.DeepSeekDiscoveryModel._decode_understand({"events":[], "needsFullText":False}, require_claims=True, full_text=True) == ((), False)


def test_material_gap_preserves_original_claims_and_does_not_mutate_reply():
    raw = body(); raw["needsFullText"] = True
    before = copy.deepcopy(raw)
    events, flag = pipeline.DeepSeekDiscoveryModel._decode_understand(raw, require_claims=True, full_text=True)
    assert raw == before and flag is True
    assert events[0].facts["researchClaims"] == raw["events"][0]["claims"]
    assert events[0].facts["sourceMaterialCoverage"]["state"] == "additional_material_unresolved"


def test_unrepaired_missing_claims_stops_only_after_bound_repair_and_never_publishes(tmp_path, monkeypatch):
    def edit(value):
        for event in value.get("events", []):
            event.pop("claims", None)
    edit_responses(monkeypatch, edit)
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    assert task.status == "failed" and calls.count("understand") == 2
    assert not any(call.startswith("research:") for call in calls)
    report = api_for(db).get("/api/v1/k10/v2/reports/latest").json()["report"]
    assert report["status"] == "failed" and report["eveningCards"] == []


def test_merged_source_gap_reaches_research_without_accepting_null_as_a_collection():
    from dataclasses import replace
    from neckline.k10.discovery import _merge_same_event_sources
    from neckline.k10.research_runtime import _Investigation
    raw = body(); raw["needsFullText"] = True
    event = pipeline.DeepSeekDiscoveryModel._decode_understand(raw, require_claims=True, full_text=True)[0][0]
    event = replace(event, facts={**event.facts, "sourceFacts": None})
    merged = _merge_same_event_sources([event, event])[0]
    context = {key: "fixture" for key in ("canonicalKey", "stageKey", "eventState", "headline", "eventKind")}
    obj = SimpleNamespace(event=merged, context=context, allowed=set(merged.source_refs),
        state={key:[] for key in ("stageResults", "claims", "questions", "paths", "evidenceUpdates", "fulltextRequests")},
        snapshot=SimpleNamespace(news_cutoff_at="2026-09-13T21:00:00+08:00"), _company_scope=lambda:{}, _cards=lambda:[])
    packet = _Investigation._packet(obj)
    assert len(packet["sourceMaterialGaps"]) == 2
    assert all(row["state"] == "additional_material_unresolved" for row in packet["sourceMaterialGaps"])
