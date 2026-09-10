from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from neckline.k10.discovery import (
    CandidateComparison,
    CompanyMappingDraft,
    DiscoveryDocument,
    EventComparison,
    EventDraft,
    EvidenceRef,
    SqliteDiscoveryWriter,
    Verification,
    _merge_same_event_sources,
    _stable_id,
    persist_discovery,
    run_discovery,
)
from neckline.k10.schema import initialize_schema
from neckline.k10 import store
from neckline.k10.historical_cases import HistoricalCaseLoader
from neckline.k10.types import OpportunityPublicationInput
from neckline.k10.universe import CompanyMetadata


NOW = datetime(2026, 9, 7, 13, tzinfo=timezone.utc)


def _configuration() -> dict:
    return json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())


class _Metadata:
    def lookup(self, *, company_code, as_of):
        return CompanyMetadata(company_code, "chinext", False, "801080.SI", as_of)


class _Model:
    def __init__(self, *, classification: dict | None = None, same_event: bool = False) -> None:
        self.classification = classification
        self.same_event = same_event
        self.verify_inputs: list[EventDraft] = []
        self.map_inputs: list[EventDraft] = []
        self.compare_inputs: list[EventDraft] = []

    def understand(self, *, document):
        return (EventDraft(
            "event-shared", " Approval ", "confirmed", f"事项 {document.document_id}",
            "disclosure", {"document": document.document_id}, (document.evidence_ref,),
        ),)

    def map_companies(self, *, event, verification):
        self.map_inputs.append(event)
        return (CompanyMappingDraft("300001.SZ", "supply", event.source_refs, {"basis": "资料"}, "fixture"),)

    def compare_event(self, *, event, verification, mappings):
        self.compare_inputs.append(event)
        code = mappings[0].company_code
        return EventComparison("共同事实", {code: CandidateComparison(
            "公司比较", {"role": "primary", "priorityReason": "资料", "gap": "差异",
                         "rankChangeConditions": "反证", "twoDayReason": "两日理由"},
            event.source_refs, 1,
        )}, event.source_refs)

    def classify_opportunity(self, *, event, verification, mapping, comparison, previous):
        if self.classification is not None:
            return self.classification
        return {"kind": "initial", "relatedOpportunityId": None, "reason": "首发",
                "newFacts": "新增事实", "changedJudgment": None, "twoDayReason": "两日理由"}

    def prioritize(self, *, candidates):
        return tuple(dict.fromkeys((item.event.canonical_key, item.mapping.company_code) for item in candidates))


def _documents(*names: str) -> tuple[DiscoveryDocument, ...]:
    return tuple(DiscoveryDocument(name, 1, NOW.isoformat(), NOW.isoformat(), name, None, {}) for name in names)


def _run(*, model: _Model, documents: tuple[DiscoveryDocument, ...] = _documents("doc-1"), previous=()):
    def verify(event):
        model.verify_inputs.append(event)
        return Verification("verified", "已核验", event.source_refs)

    return run_discovery(documents=documents, configuration=_configuration(), model=model, verify=verify,
                         metadata=_Metadata(), cutoff_at=NOW, previous_opportunities=previous)


def _seed_open_opportunity(path, *, stage: str = "approval") -> dict:
    """Create one real, active formal opportunity and its only primary sample."""
    initialize_schema(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES('SSE', ?, 1)", [
            ("20260907",), ("20260908",), ("20260909",), ("20260910",),
        ])
    store.create_scan(scan_id="scan-old", window_kind="evening", cutoff_at=NOW.isoformat(), config_id=None,
                      config_revision=None, status="completed", coverage={}, created_at=NOW.isoformat(),
                      completed_at=NOW.isoformat(), db_path=path)
    event = store.append_event_revision(
        event_id=_stable_id("event", "event-shared"), stable_key="event-shared", headline="旧机会", event_kind="disclosure",
        facts={"stageKey": stage}, source_refs=[], supersedes_revision=None,
        created_at=NOW.isoformat(), db_path=path,
    )
    comparison = {
        "summary": "旧比较",
        "differences": {"role": "primary", "priorityReason": "资料", "gap": "差异",
                        "rankChangeConditions": "反证", "twoDayReason": "两日理由"},
        "evidenceRefs": [], "rank": 1,
        "classification": {"kind": "initial", "relatedOpportunityId": None, "reason": "首发",
                           "newFacts": "首次披露", "changedJudgment": None, "twoDayReason": "两日理由",
                           "opportunityKey": "event-shared\x1f300001.SZ"},
    }
    store.create_candidate(candidate_id="candidate-old", scan_id="scan-old", event_id=event.event_id,
                           event_revision=event.revision, company_code="300001.SZ", comparison=comparison,
                           evidence=[], created_at=NOW.isoformat(), db_path=path)
    store.publish_opportunities(
        batch_id="publication-old", scan_id="scan-old", publication_kind="evening",
        inputs=(OpportunityPublicationInput(
            candidate_id="candidate-old", company_code="300001.SZ", event_id=event.event_id,
            event_revision=event.revision, opportunity_key="event-shared\x1f300001.SZ",
            catalyst_stage=stage, category="primary", comparison=comparison, evidence_refs=(),
            source_marker="evening", related_opportunity_id=None,
        ),), db_path=path, clock=lambda: NOW,
    )
    old = store.list_opportunities(db_path=path, as_of=NOW)[0]
    return {**old, "canonicalKey": "event-shared"}


def _persist_source_document(path, document: DiscoveryDocument) -> None:
    store.append_document_version(
        document_id=document.document_id, source_key="fixture", external_id=document.document_id,
        canonical_url=None, content_sha256="a" * 64, published_at=document.published_at,
        published_precision="exact", fetched_at=document.fetched_at, original_text=document.original_text,
        excerpt=None, fetch_version="fixture", metadata={}, created_at=document.fetched_at, db_path=path,
    )


def test_same_event_sources_merge_before_one_verification_mapping_and_comparison(tmp_path):
    model = _Model(same_event=True)
    run = _run(model=model, documents=_documents("doc-a", "doc-b"))

    expected = (EvidenceRef("doc-a", 1), EvidenceRef("doc-b", 1))
    assert len(run.events) == len(model.verify_inputs) == len(model.map_inputs) == len(model.compare_inputs) == 1
    assert run.events[0].source_refs == expected
    assert run.events[0].facts["sourceFacts"] == [
        {"sourceRefs": [{"documentId": "doc-a", "revision": 1}], "facts": {"document": "doc-a"}},
        {"sourceRefs": [{"documentId": "doc-b", "revision": 1}], "facts": {"document": "doc-b"}},
    ]
    assert len(run.candidates) == 1
    assert run.candidates[0].comparison.evidence_refs == expected
    path = tmp_path / "merged-sources.sqlite"
    initialize_schema(path)
    store.create_scan(scan_id="scan-merge", window_kind="evening", cutoff_at=NOW.isoformat(), config_id=None,
                      config_revision=None, status="completed", coverage={}, created_at=NOW.isoformat(),
                      completed_at=NOW.isoformat(), db_path=path)
    writer = SqliteDiscoveryWriter(scan_id="scan-merge", db_path=path, created_at=NOW.isoformat())
    persist_discovery(run=run, writer=writer)
    candidate = store.get_candidate(candidate_id=writer.publication_inputs[0].candidate_id, db_path=path)
    assert candidate["comparison"]["evidenceRefs"] == [
        {"documentId": "doc-a", "revision": 1}, {"documentId": "doc-b", "revision": 1},
    ]


def test_merged_source_labels_remain_visible_to_the_real_local_historical_case_loader(tmp_path):
    path = tmp_path / "merged-historical.sqlite"
    old_at = datetime(2026, 8, 1, 13, tzinfo=timezone.utc)
    initialize_schema(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES('SSE', ?, ?)", [
            ("20260731", 1), ("20260801", 0), ("20260802", 0), ("20260803", 1), ("20260804", 1),
            ("20260904", 1), ("20260905", 0), ("20260906", 0), ("20260907", 1), ("20260908", 1),
        ])
    store.create_scan(scan_id="old-scan", window_kind="evening", cutoff_at=old_at.isoformat(), config_id=None,
                      config_revision=None, status="completed", coverage={}, created_at=old_at.isoformat(),
                      completed_at=old_at.isoformat(), db_path=path)
    store.append_document_version(
        document_id="old-doc", source_key="fixture", external_id="old-doc", canonical_url=None,
        content_sha256="b" * 64, published_at=old_at.isoformat(), published_precision="exact",
        fetched_at=old_at.isoformat(), original_text="旧机器人订单案例", excerpt=None,
        fetch_version="fixture", metadata={}, created_at=old_at.isoformat(), db_path=path,
    )
    old_refs = [{"documentId": "old-doc", "revision": 1}]
    old_event = store.append_event_revision(
        event_id=_stable_id("event", "old-robot-order"), stable_key="old-robot-order",
        headline="旧机器人订单案例", event_kind="disclosure",
        facts={"mechanism": "robot_order", "stageKey": "approval"}, source_refs=old_refs,
        supersedes_revision=None, created_at=old_at.isoformat(), db_path=path,
    )
    comparison = {
        "summary": "旧比较",
        "differences": {"role": "primary", "priorityReason": "资料", "gap": "差异",
                        "rankChangeConditions": "反证", "twoDayReason": "两日理由"},
        "evidenceRefs": old_refs, "rank": 1,
        "classification": {"kind": "initial", "relatedOpportunityId": None, "reason": "首发",
                           "newFacts": "首次披露", "changedJudgment": None, "twoDayReason": "两日理由",
                           "opportunityKey": "old-robot-order\x1f300001.SZ"},
    }
    store.create_candidate(candidate_id="old-candidate", scan_id="old-scan", event_id=old_event.event_id,
                           event_revision=old_event.revision, company_code="300001.SZ", comparison=comparison,
                           evidence=[], created_at=old_at.isoformat(), db_path=path)
    store.publish_opportunities(
        batch_id="old-publication", scan_id="old-scan", publication_kind="evening",
        inputs=(OpportunityPublicationInput(
            candidate_id="old-candidate", company_code="300001.SZ", event_id=old_event.event_id,
            event_revision=old_event.revision, opportunity_key="old-robot-order\x1f300001.SZ",
            catalyst_stage="approval", category="primary", comparison=comparison, evidence_refs=old_refs,
            source_marker="evening", related_opportunity_id=None,
        ),), db_path=path, clock=lambda: old_at,
    )
    merged = _merge_same_event_sources((
        EventDraft("new-robot-order", "approval", "confirmed", "新机器人订单A", "disclosure",
                   {"mechanism": "robot_order"}, (EvidenceRef("doc-a", 1),)),
        EventDraft("new-robot-order", " approval ", "confirmed", "新机器人订单B", "disclosure",
                   {"theme": ["robot_order"]}, (EvidenceRef("doc-b", 1),)),
    ))[0]

    context = HistoricalCaseLoader(db_path=path).load(event=merged, mappings=(), as_of=NOW)
    assert merged.facts["mechanism"] == ["robot_order"]
    assert merged.facts["theme"] == ["robot_order"]
    assert context["historicalCoverage"]["state"] == "partial"
    assert len(context["historicalCases"]) == 1
    assert "sameTopicOrMechanism" in context["historicalCases"][0]["observedFacts"]["relation"]


def test_same_stage_material_stage_is_forced_to_continuation_without_primary_reentry():
    old = {"opportunityId": "old-approval", "companyCode": "300001.SZ", "canonicalKey": "event-shared",
           "catalystStage": "approval", "opportunityKey": "event-shared\x1f300001.SZ", "state": "active"}
    model = _Model(classification={
        "kind": "material_stage", "relatedOpportunityId": "old-approval", "reason": "模型误称新阶段",
        "newFacts": "同阶段补充资料", "changedJudgment": "判断变化", "twoDayReason": "两日理由",
    })
    run = _run(model=model, previous=(old,))

    assert not run.candidates and not run.deferred
    assert len(run.updates) == 1
    update = run.updates[0].opportunity
    assert update["kind"] == "continuation"
    assert update["relatedOpportunityId"] == "old-approval"
    assert "模型误称新阶段" in update["reason"]


def test_same_stage_continuation_persists_only_an_evidence_update_and_keeps_old_primary_sample(tmp_path):
    path = tmp_path / "same-stage.sqlite"
    old = _seed_open_opportunity(path)
    document = _documents("same-stage-doc")[0]
    _persist_source_document(path, document)
    model = _Model(classification={
        "kind": "material_stage", "relatedOpportunityId": old["opportunityId"], "reason": "模型误称新阶段",
        "newFacts": "同阶段补充资料", "changedJudgment": "判断变化", "twoDayReason": "两日理由",
    })
    run = _run(model=model, documents=(document,), previous=(old,))
    writer = SqliteDiscoveryWriter(scan_id="scan-same-stage", db_path=path, created_at=NOW.isoformat())
    persist_discovery(run=run, writer=writer)
    writer.publish_updates(at=NOW.isoformat())

    assert writer.publication_inputs == []
    assert len(store.list_company_windows(db_path=path)) == 1
    assert len(store.list_publication_samples(batch_id="publication-old", db_path=path)) == 1
    lifecycle = store.list_opportunity_lifecycle_events(opportunity_id=old["opportunityId"], db_path=path)
    assert [item["kind"] for item in lifecycle] == ["published", "evidence_update"]


def test_distinct_normalized_stage_remains_a_new_material_stage_opportunity():
    old = {"opportunityId": "old-approval", "companyCode": "300001.SZ", "canonicalKey": "event-shared",
           "catalystStage": "approval", "opportunityKey": "event-shared\x1f300001.SZ", "state": "active"}
    model = _Model(classification={
        "kind": "material_stage", "relatedOpportunityId": "old-approval", "reason": "正式签署",
        "newFacts": "订单已签署", "changedJudgment": "从审批进入签署", "twoDayReason": "签署催化",
    })

    def understand(*, document):
        return (EventDraft("event-shared", "order_signed", "confirmed", "订单签署", "disclosure",
                           {"document": document.document_id}, (document.evidence_ref,)),)

    model.understand = understand  # type: ignore[method-assign]
    run = _run(model=model, previous=(old,))
    assert len(run.candidates) == 1
    assert run.candidates[0].opportunity["kind"] == "material_stage"
    assert run.candidates[0].opportunity["opportunityKey"] == "event-shared\x1f300001.SZ\x1forder_signed"


def test_unbound_known_needs_review_marks_every_open_matching_opportunity_at_risk():
    old = tuple(
        {"opportunityId": opportunity_id, "companyCode": "300001.SZ", "canonicalKey": "event-shared",
         "catalystStage": stage, "opportunityKey": f"event-shared\x1f300001.SZ\x1f{stage}", "state": "active"}
        for opportunity_id, stage in (("old-approval", "approval"), ("old-order", "order_signed"))
    )
    model = _Model(classification={
        "kind": "needs_review", "relatedOpportunityId": None, "reason": "出现待核反证",
        "newFacts": None, "changedJudgment": None, "twoDayReason": None,
    })
    run = _run(model=model, previous=old)

    assert not run.candidates and not run.metadata_pending
    assert {item.opportunity["relatedOpportunityId"] for item in run.updates} == {"old-approval", "old-order"}
    assert all(item.opportunity["kind"] == "needs_review" for item in run.updates)
    assert all("模型未关联旧机会" in item.opportunity["reason"] for item in run.updates)


def test_unbound_known_needs_review_persists_a_real_risk_lifecycle_event(tmp_path):
    path = tmp_path / "known-risk.sqlite"
    old = _seed_open_opportunity(path)
    document = _documents("risk-doc")[0]
    _persist_source_document(path, document)
    model = _Model(classification={
        "kind": "needs_review", "relatedOpportunityId": None, "reason": "出现待核反证",
        "newFacts": None, "changedJudgment": None, "twoDayReason": None,
    })
    run = _run(model=model, documents=(document,), previous=(old,))
    writer = SqliteDiscoveryWriter(scan_id="scan-risk", db_path=path, created_at=NOW.isoformat())
    persist_discovery(run=run, writer=writer)
    writer.publish_updates(at=NOW.isoformat())

    assert writer.publication_inputs == []
    assert len(store.list_company_windows(db_path=path)) == 1
    lifecycle = store.list_opportunity_lifecycle_events(opportunity_id=old["opportunityId"], db_path=path)
    assert [item["kind"] for item in lifecycle] == ["published", "risk"]
    assert "模型未关联旧机会" in lifecycle[-1]["reason"]


@pytest.mark.parametrize("text", ["70%涨停概率", "70%封板概率", "虽然不能估计，但涨停概率70%"])
def test_positive_limit_probability_word_orders_are_rejected(text):
    model = _Model()

    def compare_event(*, event, verification, mappings):
        code = mappings[0].company_code
        return EventComparison(text, {code: CandidateComparison(
            "公司比较", {"role": "primary", "priorityReason": "资料", "gap": "差异",
                         "rankChangeConditions": "反证", "twoDayReason": "两日理由"},
            event.source_refs, 1,
        )}, event.source_refs)

    model.compare_event = compare_event  # type: ignore[method-assign]
    # Build 36 quarantines one malformed model comparison instead of throwing
    # away unrelated documents/events in the same scan.
    run = _run(model=model)
    assert run.state == "partial"
    assert any(issue.stage == "compare" and issue.code == "contract_invalid" for issue in run.issues)


def test_explicit_probability_refusal_remains_allowed():
    model = _Model()

    def compare_event(*, event, verification, mappings):
        code = mappings[0].company_code
        return EventComparison("不能估计70%涨停概率，也不输出封板概率。", {code: CandidateComparison(
            "公司比较", {"role": "primary", "priorityReason": "资料", "gap": "差异",
                         "rankChangeConditions": "反证", "twoDayReason": "两日理由"},
            event.source_refs, 1,
        )}, event.source_refs)

    model.compare_event = compare_event  # type: ignore[method-assign]
    assert _run(model=model).state == "completed"
