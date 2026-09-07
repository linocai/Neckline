"""Extend the real Schema 3/API fixture with the 3.0.3 user-visible cases."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from pathlib import Path
import sqlite3

from neckline.k10 import store
from neckline.k10.evaluation import evaluate_company_window, evaluation_state
from neckline.k10.opportunity_discovery import validate_classification
from neckline.k10.types import OpportunityPublicationInput

from .k10_v302_fixture import build_fixture as build_previous_fixture


def _publish(path: Path, *, code: str, event_id: str, stage: str, cutoff: str,
             available: str, kind: str, old: dict | None = None) -> dict:
    marker = "v303-stage" if old else "v303-pending"
    document_id = "doc-" + marker
    text = "原审批之后，已正式签署实质订单，两个交易日内将披露交付安排。" if old else "本晨新增披露，固定两日观察尚未到期。"
    store.append_document_version(
        document_id=document_id, source_key="fixture_verification", external_id=marker,
        canonical_url="https://example.invalid/" + marker,
        content_sha256=sha256(text.encode()).hexdigest(), published_at=cutoff,
        published_precision="exact", fetched_at=cutoff, original_text=text, excerpt=text,
        fetch_version="fixture-v303", metadata={"title": "实质订单阶段" if old else "未到期样本"},
        created_at=cutoff, db_path=path,
    )
    refs = [{"documentId": document_id, "revision": 1}]
    prior = store.latest_event_revision(event_id=event_id, db_path=path) if old else None
    event_revision = store.append_event_revision(
        event_id=event_id, stable_key=event_id, headline="审批后的实质订单" if old else "晨间新增机会",
        event_kind="policy", facts={"stageKey": stage, "currentFacts": {"summary": text}},
        source_refs=refs, supersedes_revision=prior.revision if prior else None,
        created_at=cutoff, db_path=path,
    )
    revision = event_revision.revision
    decision = validate_classification(
        {"kind": "material_stage" if old else "initial", "relatedOpportunityId": old["opportunityId"] if old else None,
         "reason": "实质订单改变了原先仅获批的判断" if old else "首次发布",
         "newFacts": text, "changedJudgment": "从获批但未落地，转为实质订单已签署" if old else None,
         "twoDayReason": "两日内披露交付安排，若公告否认则撤回" if old else "两日内核查新披露"},
        canonical_key=event_id, stage_key=stage, company_code=code,
        previous=[{**old, "canonicalKey": event_id}] if old else [],
    )
    comparison = {"summary": text, "rank": 1, "evidenceRefs": refs, "classification": decision,
                  "differences": {"role": "primary", "priorityReason": "新增可核事实", "gap": "直接对象",
                                  "rankChangeConditions": "出现独立反证", "twoDayReason": decision["twoDayReason"]}}
    scan_id = "scan-" + marker
    store.create_scan(scan_id=scan_id, window_kind=kind, cutoff_at=cutoff, config_id="cfg-fixture", config_revision=1,
                      status="completed", coverage={"status": "complete"}, created_at=cutoff, completed_at=cutoff, db_path=path)
    candidate_id = "candidate-" + marker
    store.create_candidate(candidate_id=candidate_id, scan_id=scan_id, event_id=event_id, event_revision=revision,
                           company_code=code, comparison=comparison, evidence=refs, created_at=cutoff, db_path=path)
    store.publish_opportunities(
        batch_id="batch-" + marker, scan_id=scan_id, publication_kind=kind,
        inputs=[OpportunityPublicationInput(candidate_id=candidate_id, company_code=code, event_id=event_id,
                event_revision=revision, opportunity_key=decision["opportunityKey"], catalyst_stage=stage,
                category="primary", comparison=comparison, evidence_refs=tuple(refs), source_marker=kind,
                related_opportunity_id=old["opportunityId"] if old else None)],
        db_path=path, clock=lambda: datetime.fromisoformat(available),
    )
    return next(item for item in store.list_company_windows(db_path=path) if item["firstBatchId"] == "batch-" + marker)


def build_fixture(path: Path) -> dict[str, str]:
    ids = build_previous_fixture(path)
    with sqlite3.connect(path) as conn:
        conn.executemany("INSERT INTO trade_cal VALUES('SSE',?,?)", [
            ("20260904", 1), ("20260905", 0), ("20260906", 0), ("20260907", 1), ("20260908", 1),
        ])
    old = next(item for item in store.list_opportunities(db_path=path)
               if item["companyWindowId"] == ids["primaryWindowId"])
    stage = _publish(path, code="300001.SZ", event_id=old["eventId"], stage="order_signed",
                     cutoff="2026-09-02T21:00:00+08:00", available="2026-09-02T21:05:00+08:00", kind="evening", old=old)
    for day in (stage["d1TradeDate"], stage["d2TradeDate"]):
        store.append_market_day_fact(
            company_code=stage["companyCode"], trade_date=day, availability="suspended",
            open_price=None, high_price=None, low_price=None, close_price=None, pre_close=None,
            limit_up_price=None, close_limit_up=None, touched_limit_up=None,
            source_refs=[{"documentId": "doc-v303-stage", "revision": 1}],
            obtained_at="2026-09-04T16:00:00+08:00", created_at="2026-09-04T16:00:00+08:00", db_path=path,
        )
    evaluated_at = "2026-09-04T16:00:00+08:00"
    result = evaluate_company_window(window=stage, market_facts=store.latest_market_day_facts(company_code=stage["companyCode"], db_path=path), as_of=evaluated_at)
    store.append_company_window_evaluation(company_window_id=stage["companyWindowId"], state=evaluation_state(result),
        fact_refs=result.fact_refs, result=result.to_dict(), evaluated_at=evaluated_at, created_at=evaluated_at, db_path=path)
    store.freeze_company_window_selection(company_window_id=stage["companyWindowId"], frozen_at=evaluated_at, db_path=path)
    pending = _publish(path, code="300005.SZ", event_id="event-v303-pending", stage="initial",
                       cutoff="2026-09-07T09:00:00+08:00", available="2026-09-07T09:01:00+08:00", kind="morning")
    return {**ids, "stageWindowId": stage["companyWindowId"], "pendingWindowId": pending["companyWindowId"]}
