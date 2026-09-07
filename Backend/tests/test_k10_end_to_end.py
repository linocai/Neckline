"""K10-v1.4 cross-layer path with an isolated database and fake providers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api.k10 import create_router
from neckline.k10 import initialize_schema, store
from neckline.k10.morning_runtime import morning_review_handler
from neckline.k10.providers import ProviderResolution
from neckline.k10.runtime import production_analysis_handler
from neckline.k10.types import OpportunityPublicationInput
from neckline.k10.worker import run_once
from neckline.llm.base import LLMResult


NOW = "2026-09-06T12:00:00+00:00"


class FakeProvider:
    def __init__(self, results): self.results = list(results)
    def chat(self, *_args, **_kwargs): return self.results.pop(0)


def _result(text: str) -> LLMResult:
    return LLMResult(ok=True, content=text, provider="deepseek", model="deepseek-v4-pro", prompt_tokens=2, completion_tokens=3, total_tokens=5, usage_unavailable=False)


def _client(path: Path) -> TestClient:
    app = FastAPI(); app.include_router(create_router(lambda: path, lambda: None, lambda: path.parent / "parquet")); return TestClient(app)


def _seed(path: Path) -> str:
    initialize_schema(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES('SSE',?,?)", [("20260904", 1), ("20260905", 0), ("20260906", 0), ("20260907", 1), ("20260908", 1), ("20260909", 1)])
    config = {"configVersion": "k10-v1.4", "universe": "chinext", "excludeBaijiu": True,
        "hardExclusions": {"approved": True, "board": "chinext", "priceLimit": "none", "st": "exclude", "swL2Exclusions": ["801125.SI"]},
        "sourceAdapters": ["fixture"], "modelRoutes": {"analysis": "deepseek-v4-pro", "morning": "deepseek-v4-pro"},
        "taskPolicies": {"analysis": {"maxAttempts": 2, "modelMaxAttempts": 2, "timeoutSeconds": 90, "costLimit": None}, "morning": {"maxAttempts": 2, "modelMaxAttempts": 2, "timeoutSeconds": 90, "costLimit": None}},
        "evaluationPolicy": {"version": "k10-evaluation-v1.4", "selectionFreeze": "d1_open_0930", "window": "d1_d2", "primaryMetric": "close_limit_up_any_d1_d2"}}
    revision = store.append_run_config(config_id="cfg", payload=config, created_at=NOW, db_path=path)
    store.append_document_version(document_id="doc-1", source_key="fixture", external_id="1", canonical_url="https://example.test/source", content_sha256="a" * 64, published_at=NOW, published_precision="exact", fetched_at=NOW, original_text="原文", excerpt=None, fetch_version="fixture", metadata={}, created_at=NOW, db_path=path)
    refs = [{"documentId": "doc-1", "revision": 1, "fetchedAt": NOW}]
    store.append_event_revision(event_id="event-1", stable_key="event", headline="合成催化", event_kind="policy", facts={}, source_refs=refs, supersedes_revision=None, created_at=NOW, db_path=path)
    store.create_scan(scan_id="scan-1", window_kind="evening", cutoff_at=NOW, config_id="cfg", config_revision=revision, status="completed", coverage={"status": "complete"}, created_at=NOW, completed_at=NOW, db_path=path)
    comparison = {"summary": "比较", "differences": {"role": "primary", "priorityReason": "直接受益", "gap": "备选缺少证据", "rankChangeConditions": "新证据", "twoDayReason": "窗口内催化"}, "evidenceRefs": refs, "rank": 1, "classification": {"kind": "initial", "opportunityKey": "300001:initial", "reason": "首发", "newFacts": "新增披露", "changedJudgment": None, "twoDayReason": "两日可核", "relatedOpportunityId": None}}
    store.create_candidate(candidate_id="cand-1", scan_id="scan-1", event_id="event-1", event_revision=1, company_code="300001.SZ", comparison=comparison, evidence=refs, created_at=NOW, db_path=path)
    batch = store.publish_opportunities(batch_id="batch-1", scan_id="scan-1", publication_kind="evening", inputs=[OpportunityPublicationInput(candidate_id="cand-1", company_code="300001.SZ", event_id="event-1", event_revision=1, opportunity_key="300001:initial", catalyst_stage="initial", category="primary", comparison=comparison, evidence_refs=tuple(refs), source_marker="evening")], db_path=path, clock=lambda: datetime(2026, 9, 6, 20, tzinfo=timezone.utc))
    return batch.batch_id


def test_selected_debate_then_morning_contrary_withdraws_without_erasing_history(tmp_path, monkeypatch):
    path = tmp_path / "k10.db"; _seed(path)
    with _client(path) as client:
        opportunity = client.get("/api/v1/k10/opportunities").json()["items"][0]
        chosen = client.post(f"/api/v1/k10/company-windows/{opportunity['companyWindowId']}/selection", json={"action": "keep", "idempotencyKey": "keep-1"}).json()
    from neckline.k10 import runtime, morning_runtime
    analysis = FakeProvider([_result("正方全文"), _result("反方全文")])
    monkeypatch.setattr(runtime, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution("configured", analysis, "deepseek", None))
    run_once(db_path=path, worker_id="analysis", lease_for=timedelta(minutes=5), handlers={"analysis": production_analysis_handler()}, clock=lambda: datetime(2026, 9, 6, 13, tzinfo=timezone.utc))
    assert store.get_task(task_id=chosen["analysisJobId"], db_path=path).status == "completed"
    pro = store.load_analysis_revision(observation_id=chosen["observationId"], input_cutoff_at=opportunity["availableAt"], analysis_kind="pro", db_path=path)
    con = store.load_analysis_revision(observation_id=chosen["observationId"], input_cutoff_at=opportunity["availableAt"], analysis_kind="con", db_path=path)
    assert {pro["content"]["fullText"], con["content"]["fullText"]} == {"正方全文", "反方全文"}
    assert "pricePlan" not in str(pro["content"]) and "holdingExitPlan" not in str(con["content"]) and "收益" not in str(con["content"])

    morning_cutoff = "2026-09-07T09:00:00+08:00"
    store.append_document_version(document_id="doc-2", source_key="fixture", external_id="2", canonical_url="https://example.test/contra", content_sha256="b" * 64, published_at=morning_cutoff, published_precision="exact", fetched_at=morning_cutoff, original_text="否认", excerpt=None, fetch_version="fixture", metadata={}, created_at=morning_cutoff, db_path=path)
    store.append_document_version(document_id="doc-3", source_key="fixture-independent", external_id="3", canonical_url="https://example.test/confirm", content_sha256="c" * 64, published_at=morning_cutoff, published_precision="exact", fetched_at=morning_cutoff, original_text="独立核验", excerpt=None, fetch_version="fixture", metadata={}, created_at=morning_cutoff, db_path=path)
    store.enqueue_task(task_id="morning-task", kind="morning_review", idempotency_key="morning", input_version="v", input_cutoff_at=morning_cutoff, payload={"candidateId": "cand-1", "observationId": chosen["observationId"], "originalCutoffAt": NOW, "morningEvidenceRefs": [{"documentId": "doc-2", "revision": 1}], "independentVerificationRefs": [{"documentId": "doc-3", "revision": 1}], "companyWindowId": opportunity["companyWindowId"], "displayRank": 1, "selectionState": "kept", "lifecycle": "active", "isNew": False, "sourceStatus": "complete", "configId": "cfg", "configRevision": 1}, budget={"maxAttempts": 2}, created_at=morning_cutoff, db_path=path)
    morning = FakeProvider([_result('{"material":true,"reasonStatus":"invalidated","observationStatus":"needs_review","summary":"晨间反证","materialContraryEvidence":[{"documentId":"doc-3","revision":1,"claim":"重大反证"}]}')])
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution("configured", morning, "deepseek", None))
    run_once(db_path=path, worker_id="morning", lease_for=timedelta(minutes=5), handlers={"morning_review": morning_review_handler}, clock=lambda: datetime(2026, 9, 7, 1, tzinfo=timezone.utc))
    assert store.get_task(task_id="morning-task", db_path=path).status == "completed"
    assert store.get_opportunity(opportunity_id=opportunity["opportunityId"], db_path=path)["state"] == "withdrawn"
    assert any(item["kind"] == "withdrawal" for item in store.list_opportunity_lifecycle_events(opportunity_id=opportunity["opportunityId"], db_path=path))
    assert store.get_opportunity(opportunity_id=opportunity["opportunityId"], db_path=path)["d2TradeDate"] == opportunity["d2TradeDate"]
