"""V3.0.4 morning-report evidence and coverage regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from neckline.k10 import pipeline, store
from neckline.k10.morning import build_morning_update, record_morning_update
from neckline.k10.types import Task
from neckline.k10.worker import TaskContext
from tests.test_k10_api import _client, _seed


def _document(path, *, document_id: str, source_key: str, text: str) -> None:
    store.append_document_version(
        document_id=document_id, source_key=source_key, external_id=document_id,
        canonical_url=f"https://example.test/{document_id}", content_sha256=(document_id[-1] * 64),
        published_at="2026-09-07T00:40:00+00:00", published_precision="exact",
        fetched_at="2026-09-07T00:50:00+00:00", original_text=text, excerpt=text,
        fetch_version="fixture", metadata={}, created_at="2026-09-07T00:50:00+00:00", db_path=path,
    )


def test_v304_fallback_item_uses_the_same_coverage_status_contract_as_normal_items(tmp_path):
    path = tmp_path / "fallback.sqlite"
    _seed(path)
    cutoff_dt = datetime(2026, 9, 7, 1, tzinfo=timezone.utc)
    cutoff = cutoff_dt.isoformat().replace("+00:00", "Z")
    store.create_scan(scan_id="morning-fallback", window_kind="morning", cutoff_at=cutoff,
                      config_id="cfg", config_revision=1, status="completed", coverage={"status": "complete"},
                      created_at=cutoff, completed_at=cutoff, db_path=path)
    target = pipeline._morning_target_items(scan_id="morning-fallback", cutoff_at=cutoff_dt,
                                            db_path=path, morning_refs=[])[0]
    item = pipeline._morning_fallback_item(
        scan_id="morning-fallback", target=target, cutoff_at=cutoff, source_status="complete",
        summary="完整覆盖下无实质变化，继续观察。", task_status="completed",
        reason_status="current", material=False,
    )
    assert item is not None
    assert item["content"]["coverage"] == {"status": "complete", "taskStatus": "completed"}
    groups = {section: [] for section in ("major_contrary", "thesis_changed", "continuing_or_expiring", "new", "needs_review")}
    groups[item["content"]["section"]].append(item)
    store.append_morning_report(report_id="fallback-report", scan_id="morning-fallback", cutoff_at=cutoff,
                                generated_at=cutoff, status="completed", coverage={"status": "complete"},
                                groups=groups, created_at=cutoff, db_path=path)
    with _client(path) as client:
        body = client.get("/api/v1/k10/morning-reports/latest").json()
    assert body["items"][0]["coverageStatus"] == "complete"


def test_v304_withdrawal_preserves_independent_refs_without_relabelling_morning_refs(tmp_path):
    path = tmp_path / "withdrawal.sqlite"
    _seed(path)
    _document(path, document_id="doc-v304-morning", source_key="fixture-news", text="晨间资料出现风险。")
    _document(path, document_id="doc-v304-independent", source_key="fixture-verification", text="独立核验确认核心理由失效。")
    cutoff_dt = datetime(2026, 9, 7, 1, tzinfo=timezone.utc)
    target = pipeline._morning_target_items(scan_id="morning-withdraw", cutoff_at=cutoff_dt,
                                            db_path=path, morning_refs=[])[0]
    update = build_morning_update(
        cutoff_at=cutoff_dt.isoformat(), candidate_id=target["candidateId"], observation_id=None,
        reason_status="invalidated", source_status="complete", observation_status="needs_review",
        material_contrary_evidence=[{"documentId": "doc-v304-independent", "revision": 1, "claim": "核心理由失效"}],
        source_refs=[{"documentId": "doc-v304-morning", "revision": 1}],
        independent_verification_refs=[{"documentId": "doc-v304-independent", "revision": 1}],
        summary="独立核验确认核心理由失效。",
    )
    record_morning_update(repository=store, db_path=path, update=update, opportunity_id=target["opportunityId"],
                          update_id="withdraw-v304", created_at=cutoff_dt.isoformat(), occurred_at=cutoff_dt.isoformat())

    lifecycle = store.list_opportunity_lifecycle_events(opportunity_id=target["opportunityId"], db_path=path)
    withdrawal = lifecycle[-1]
    assert {(ref["documentId"], ref["revision"]) for ref in withdrawal["sourceRefs"]} == {
        ("doc-v304-morning", 1), ("doc-v304-independent", 1),
    }
    assert withdrawal["content"]["independentVerificationRefs"] == [{"documentId": "doc-v304-independent", "revision": 1}]

    next_targets = pipeline._morning_target_items(
        scan_id="next-morning", cutoff_at=cutoff_dt + timedelta(days=1), db_path=path, morning_refs=[]
    )
    next_target = next(item for item in next_targets if item["opportunityId"] == target["opportunityId"])
    assert next_target["independentVerificationRefs"] == [{"documentId": "doc-v304-independent", "revision": 1}]
    with _client(path) as client:
        body = client.get(f"/api/v1/k10/opportunities/{target['opportunityId']}").json()
    visible = body["lifecycleEvents"][-1]["sourceRefs"]
    assert {ref["documentId"] for ref in visible} == {"doc-v304-morning", "doc-v304-independent"}


def _runtime_context(*, path, independent_refs, cutoff: str = "2026-09-07T01:00:00+00:00") -> TaskContext:
    from tests.morning_context_fixture import persist_context
    return persist_context(TaskContext(
        Task("morning-v304", "morning_review", "queued", 1, None, None, {
            "candidateId": "candidate-v304", "originalCutoffAt": "2026-09-06T12:00:00+00:00",
            "morningEvidenceRefs": [{"documentId": "doc-v304-morning", "revision": 1}],
            "independentVerificationRefs": independent_refs,
            "companyWindowId": "window-v304", "displayRank": 1, "selectionState": "unhandled",
            "lifecycle": "active", "isNew": False, "sourceStatus": "complete",
            "configId": "cfg", "configRevision": 1,
        }), {}, {}, "v", cutoff, path, Event(),
    ))


def _install_runtime_fakes(monkeypatch, *, response):
    from neckline.k10 import morning_runtime

    class Provider:
        def chat(self, *_args, **_kwargs):
            return SimpleNamespace(ok=True, content=json.dumps(response, ensure_ascii=False))

    monkeypatch.setattr(morning_runtime.store, "read_run_config", lambda **_: {"payload": {"modelRoutes": {"morning": "deepseek-v4-pro"}}})
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", lambda **_: SimpleNamespace(provider=Provider(), error=None))
    monkeypatch.setattr(morning_runtime.store, "load_candidate_context", lambda **_: {
        "observationIds": [], "opportunity": {"opportunityId": "opportunity-v304", "state": "active"},
    })
    monkeypatch.setattr(morning_runtime.store, "load_document_versions", lambda *, refs, **_: [
        {"documentId": ref["documentId"], "revision": ref["revision"], "fetchedAt": "2026-09-07T00:50:00+00:00"}
        for ref in refs
    ])
    return morning_runtime


def test_v304_runtime_rejects_invalidated_claim_with_only_unrelated_independent_ref(tmp_path, monkeypatch):
    runtime = _install_runtime_fakes(monkeypatch, response={
        "material": True, "reasonStatus": "invalidated", "observationStatus": "needs_review", "summary": "撤回",
        "materialContraryEvidence": [{"documentId": "doc-v304-morning", "revision": 1, "claim": "普通晨间反证"}],
    })
    result = runtime.morning_review_handler(_runtime_context(path=tmp_path/"morning.sqlite", independent_refs=[{"documentId": "doc-v304-independent", "revision": 1}]))
    assert result.status == "failed"
    assert result.stage == "model"
    assert "直接引用独立核验资料" in result.error


def test_v304_runtime_keeps_pending_morning_contrary_visible_before_independent_confirmation(tmp_path, monkeypatch):
    runtime = _install_runtime_fakes(monkeypatch, response={
        "material": True, "reasonStatus": "needs_review", "observationStatus": "needs_review", "summary": "待核重大反证",
        "materialContraryEvidence": [{"documentId": "doc-v304-morning", "revision": 1, "claim": "尚待独立核验"}],
    })
    monkeypatch.setattr(runtime, "record_morning_update", lambda **_: "lifecycle-v304")
    result = runtime.morning_review_handler(_runtime_context(path=tmp_path/"morning.sqlite", independent_refs=[]))
    assert result.status == "completed"
    assert result.checkpoint["reportSection"] == "needs_review"
    assert result.checkpoint["reportItem"]["content"]["independentVerificationRefs"] == []
