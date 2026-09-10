from __future__ import annotations

from datetime import datetime, timezone
import threading

import pytest

from neckline.k10.discovery import (
    CandidateComparison,
    CompanyMappingDraft,
    DiscoveryDocument,
    DiscoverySliceYield,
    EvidenceRef,
    EventComparison,
    EventDraft,
    SqliteDiscoveryWriter,
    Verification,
    freeze_event_drafts,
    load_documents_from_store,
    prepare_document_for_analysis,
    persist_discovery,
    run_discovery,
)
from neckline.k10.schema import initialize_schema as _initialize_schema, read_connection
from neckline.k10.store import append_document_version, create_scan, list_candidates
from neckline.k10.universe import CompanyMetadata


NOW = datetime(2026, 9, 6, 13, tzinfo=timezone.utc)
NOW_TEXT = NOW.isoformat(timespec="seconds")


def initialize_schema(path):
    import sqlite3
    result = _initialize_schema(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        if not conn.execute("SELECT 1 FROM trade_cal LIMIT 1").fetchone():
            conn.executemany("INSERT INTO trade_cal VALUES('SSE', ?, ?)",
                [("20260904",1),("20260905",0),("20260906",0),("20260907",1),("20260908",1),
                 ("20260909",1),("20260910",1),("20260911",1),("20260912",0),("20260913",0),("20260914",1)])
    return result


def _configuration():
    import json
    from pathlib import Path
    return json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())



def _seed_documents(path, count=1):
    initialize_schema(path)
    create_scan(scan_id="scan-1", window_kind="evening", cutoff_at=NOW_TEXT, config_id=None,
                config_revision=None, status="completed", coverage={}, created_at=NOW_TEXT,
                completed_at=NOW_TEXT, db_path=path)
    for number in range(count):
        append_document_version(
            document_id=f"doc-{number}", source_key="fixture", external_id=f"external-{number}",
            canonical_url=f"https://example.invalid/{number}", content_sha256=f"{number:064x}",
            published_at=NOW_TEXT, published_precision="exact", fetched_at=NOW_TEXT,
            original_text=f"原始资料 {number}", excerpt=None, fetch_version="fixture-v1", metadata={},
            created_at=NOW_TEXT, db_path=path,
        )


class _Metadata:
    def __init__(self, *, missing=(), excluded=()):
        self.missing, self.excluded = set(missing), set(excluded)

    def lookup(self, *, company_code, as_of):
        if company_code in self.missing:
            return None
        return CompanyMetadata(company_code=company_code, board="main" if company_code in self.excluded else "chinext",
                               is_st=False, sw_l2_code="801080.SI", as_of=as_of)


class _Model:
    def __init__(self, *, count=1, denial=False, probability=False):
        self.count, self.denial, self.probability = count, denial, probability
        self.calls = 0

    def understand(self, *, document: DiscoveryDocument):
        self.calls += 1
        state = "denial" if self.denial and document.document_id == "doc-1" else "announcement"
        return (EventDraft(canonical_key="event-shared", stage_key=f"stage-{document.document_id}", event_state=state,
                           headline="测试事件", event_kind="disclosure", facts={"source": document.document_id},
                           source_refs=(document.evidence_ref,)),)

    def map_companies(self, *, event, verification):
        return tuple(CompanyMappingDraft(company_code=f"30{number:04d}.SZ", affected_stage="supply",
                                         relation_evidence=event.source_refs, inference={"step": number},
                                         uncertainty="fixture") for number in range(self.count))

    def compare_event(self, *, event, verification, mappings):
        candidates = {}
        for rank, mapping in enumerate(mappings, start=1):
            candidates[mapping.company_code] = CandidateComparison(
                summary=f"{mapping.company_code} 的具体比较",
                differences={"role": "primary" if rank == 1 else "alternative",
                             "priorityReason": "涨停概率70%" if self.probability else mapping.company_code,
                             "gap": "已核对同事件关系", "rankChangeConditions": "新增公司披露",
                             "twoDayReason": "新披露进入两日观察"},
                evidence_refs=event.source_refs, rank=rank,
            )
        return EventComparison(summary="事件共同事实已经核对", candidates=candidates,
                               evidence_refs=event.source_refs)

    def classify_opportunity(self, *, event, verification, mapping, comparison, previous):
        exact = next((old for old in previous if old.get("canonicalKey") == event.canonical_key), None)
        return {"kind": "continuation" if exact else "initial", "relatedOpportunityId": exact["opportunityId"] if exact else None,
                "reason": "与已有事实比较", "newFacts": "本轮公司披露", "changedJudgment": None,
                "twoDayReason": "新披露进入两日观察"}

    def prioritize(self, *, candidates):
        return tuple(dict.fromkeys((item.event.canonical_key, item.mapping.company_code) for item in candidates))


def _verify(event):
    return Verification(state="verified", summary="核验完成", evidence_refs=event.source_refs)


def test_full_fake_pipeline_persists_event_mapping_and_only_thirty_evening_companies(tmp_path):
    path = tmp_path / "discovery.db"
    _seed_documents(path)
    documents = load_documents_from_store(cutoff_at=NOW_TEXT, db_path=path)
    result = run_discovery(documents=documents, configuration=_configuration(), model=_Model(count=31),
                           verify=_verify, metadata=_Metadata(), cutoff_at=NOW)
    assert result.state == "completed"
    assert len(result.candidates) == 30
    assert result.deferred_count == 1

    from dataclasses import replace
    from neckline.k10 import store
    writer = SqliteDiscoveryWriter(scan_id="scan-1", db_path=path, created_at=NOW_TEXT)
    persist_discovery(run=result, writer=writer)
    store.publish_opportunities(batch_id="pub-1", scan_id="scan-1", publication_kind="evening",
        inputs=tuple(replace(item, source_marker="evening") for item in writer.publication_inputs), db_path=path, clock=lambda: NOW)
    assert len(list_candidates(scan_id="scan-1", state="offered", db_path=path)) == 30
    with read_connection(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_company_mappings").fetchone()[0] == 31


def test_html_preparation_keeps_fallback_and_table_facts_without_executing_markup():
    document = DiscoveryDocument("doc-html", 1, NOW_TEXT, NOW_TEXT,
                                 "<div>订单金额 <td>12亿元</td><script>drop()</script><embed src='x'><noscript>更正公告</noscript></div>",
                                 None, {})
    prepared = prepare_document_for_analysis(document)
    assert prepared.evidence_ref == document.evidence_ref
    assert "订单金额" in prepared.analysis_text and "12亿元" in prepared.analysis_text and "更正公告" in prepared.analysis_text
    assert "drop()" not in prepared.analysis_text
    assert prepared.extraction["version"] == "html-readable-v1"


def test_understanding_checkpoint_resumes_only_validated_document_results():
    documents = tuple(DiscoveryDocument(f"doc-{index}", 1, NOW_TEXT, NOW_TEXT, f"资料{index}", None, {}) for index in range(2))
    checkpoints = []

    class FailsOne(_Model):
        def understand(self, *, document):
            self.calls += 1
            if document.document_id == "doc-0":
                raise RuntimeError("malformed response")
            return super().understand(document=document)

    first_model = FailsOne()
    first = run_discovery(documents=documents, configuration=_configuration(), model=first_model, verify=_verify,
                          metadata=_Metadata(), cutoff_at=NOW, checkpoint=checkpoints.append)
    completed = [item for item in checkpoints if item["state"] == "completed"]
    assert first.state == "partial" and first.document_counts["understandFailed"] == 1 and len(completed) == 1
    recovered = {EvidenceRef("doc-1", 1): tuple(
        EventDraft(row["canonicalKey"], row["stageKey"], row["eventState"], row["headline"], row["eventKind"],
                   row["facts"], (EvidenceRef("doc-1", 1),)) for row in completed[0]["events"]
    )}
    retry_model = _Model()
    resumed = run_discovery(documents=documents, configuration=_configuration(), model=retry_model, verify=_verify,
                            metadata=_Metadata(), cutoff_at=NOW, understood_by_document=recovered)
    assert resumed.state == "completed" and retry_model.calls == 1


def test_understanding_window_refills_after_each_completion_and_keeps_frozen_event_order():
    """A slow first item must not hold the next window slot hostage.

    The first document waits for document two to start.  A barrier implementation
    cannot start document two until that wait ends; a bounded continuous queue can
    checkpoint the quick peer, refill, and keep final merged refs in frozen order.
    """
    documents = tuple(DiscoveryDocument(f"doc-{index}", 1, NOW_TEXT, NOW_TEXT, f"资料{index}", None, {})
                      for index in range(3))
    document_two_started = threading.Event()
    checkpoints: list[dict] = []

    class WindowModel(_Model):
        def understand(self, *, document):
            self.calls += 1
            if document.document_id == "doc-0":
                assert document_two_started.wait(timeout=1)
            elif document.document_id == "doc-2":
                document_two_started.set()
            return (EventDraft(canonical_key="shared", stage_key="same-stage", event_state="announcement",
                               headline=document.document_id, event_kind="disclosure", facts={"doc": document.document_id},
                               source_refs=(document.evidence_ref,)),)

    verified_refs: list[tuple[EvidenceRef, ...]] = []
    def verify(event):
        verified_refs.append(event.source_refs)
        return Verification("verified", "核验完成", event.source_refs)

    first = run_discovery(documents=documents, configuration=_configuration(), model=WindowModel(), verify=verify,
                          metadata=_Metadata(), cutoff_at=NOW, checkpoint=checkpoints.append,
                          understand_concurrency=2, document_batch_size=2)
    assert first.state == "completed"
    assert [item["documentRef"]["documentId"] for item in checkpoints if item["state"] == "completed"] != ["doc-0", "doc-1", "doc-2"]
    assert verified_refs == [(EvidenceRef("doc-0", 1), EvidenceRef("doc-1", 1), EvidenceRef("doc-2", 1))]

    recovered = {
        EvidenceRef(item["documentRef"]["documentId"], item["documentRef"]["revision"]):
        tuple(EventDraft(row["canonicalKey"], row["stageKey"], row["eventState"], row["headline"], row["eventKind"],
                         row["facts"], tuple(EvidenceRef(ref["documentId"], ref["revision"]) for ref in row["sourceRefs"]))
              for row in item["events"])
        for item in checkpoints if item["state"] == "completed"
    }
    resumed_refs: list[tuple[EvidenceRef, ...]] = []
    resumed = run_discovery(documents=documents, configuration=_configuration(), model=_Model(),
                            verify=lambda event: (resumed_refs.append(event.source_refs) or Verification("verified", "核验完成", event.source_refs)),
                            metadata=_Metadata(), cutoff_at=NOW, understood_by_document=recovered,
                            understand_concurrency=2, document_batch_size=2)
    assert resumed.state == "completed"
    assert resumed_refs == verified_refs
    assert [(event.headline, event.facts, event.source_refs) for event in resumed.events] == [
        (event.headline, event.facts, event.source_refs) for event in first.events
    ]


def test_understanding_slice_stops_admission_but_drains_inflight_checkpoints():
    documents = tuple(DiscoveryDocument(f"doc-{index}", 1, NOW_TEXT, NOW_TEXT, f"资料{index}", None, {})
                      for index in range(3))
    release_second = threading.Event()
    checkpoints: list[dict] = []

    class SliceModel(_Model):
        def __init__(self):
            super().__init__()
            self.seen: list[str] = []

        def understand(self, *, document):
            self.calls += 1
            self.seen.append(document.document_id)
            if document.document_id == "doc-1":
                release_second.wait(timeout=1)
            return (EventDraft("event-" + document.document_id, "stage", "announcement", "测试事件", "disclosure", {},
                               (document.evidence_ref,)),)

    calls = 0
    def slice_after_initial_window():
        nonlocal calls
        calls += 1
        if calls == 3:
            release_second.set()
            from neckline.k10.discovery import DiscoverySliceYield
            raise DiscoverySliceYield()

    model = SliceModel()
    with pytest.raises(DiscoverySliceYield):
        run_discovery(documents=documents, configuration=_configuration(), model=model, verify=_verify,
                      metadata=_Metadata(), cutoff_at=NOW, checkpoint=checkpoints.append,
                      leaseguard=slice_after_initial_window, understand_concurrency=2, document_batch_size=2)
    assert model.seen == ["doc-0", "doc-1"]
    assert [item["documentRef"]["documentId"] for item in checkpoints if item["state"] == "completed"] == ["doc-0", "doc-1"]


def test_pending_independent_verification_does_not_spend_company_comparison_calls():
    class Recording(_Model):
        def __init__(self):
            super().__init__()
            self.mapping_calls = 0

        def map_companies(self, **kwargs):
            self.mapping_calls += 1
            return super().map_companies(**kwargs)

    model = Recording()
    document = DiscoveryDocument("doc", 1, NOW_TEXT, NOW_TEXT, "资料", None, {})
    pending = run_discovery(documents=(document,), configuration=_configuration(), model=model,
                            verify=lambda _event: Verification("needs_review", "额度待核", (), {"state": "pending"}),
                            metadata=_Metadata(), cutoff_at=NOW)
    assert pending.state == "partial" and not pending.candidates and model.mapping_calls == 0
    assert [(issue.stage, issue.code) for issue in pending.issues] == [("verify", "verification_pending")]


def test_new_stage_and_denial_become_event_revisions_and_missing_metadata_is_pending(tmp_path):
    path = tmp_path / "stages.db"
    _seed_documents(path, count=2)
    result = run_discovery(documents=load_documents_from_store(cutoff_at=NOW_TEXT, db_path=path),
                           configuration=_configuration(), model=_Model(denial=True), verify=_verify,
                           metadata=_Metadata(missing={"300000.SZ"}), cutoff_at=NOW)
    assert len(result.events) == 2
    assert not result.candidates and len(result.metadata_pending) == 2
    persist_discovery(run=result, writer=SqliteDiscoveryWriter(scan_id="scan-1", db_path=path, created_at=NOW_TEXT))
    with read_connection(path) as conn:
        rows = conn.execute("SELECT revision,facts_json FROM k10_event_revisions ORDER BY revision").fetchall()
    assert [row[0] for row in rows] == [1, 2]
    assert "denial" in rows[1][1]
    assert list_candidates(scan_id="scan-1", state=None, db_path=path) == []


def test_missing_model_configuration_short_circuits_before_model_and_invalid_evidence_is_rejected():
    document = DiscoveryDocument("doc-1", 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
    model = _Model()
    bad_config = _configuration(); del bad_config["modelRoutes"]
    result = run_discovery(documents=(document,), configuration=bad_config, model=model, verify=_verify,
                           metadata=_Metadata(), cutoff_at=NOW)
    assert result.state == "not_configured" and model.calls == 0

    def bad_verify(event):
        return Verification("verified", "bad", (EvidenceRef("invented", 1),))

    run = run_discovery(documents=(document,), configuration=_configuration(), model=model, verify=bad_verify,
                        metadata=_Metadata(), cutoff_at=NOW)
    assert run.state == "partial"
    assert [(item.stage, item.code) for item in run.issues] == [("verify_or_map", "contract_invalid")]


def test_uncalibrated_probability_is_rejected():
    document = DiscoveryDocument("doc-1", 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
    run = run_discovery(documents=(document,), configuration=_configuration(), model=_Model(probability=True),
                        verify=_verify, metadata=_Metadata(), cutoff_at=NOW)
    assert run.state == "partial" and run.issues[0].stage == "compare"


def test_event_comparison_preserves_model_order_and_shared_primary_rank():
    class WholeEvent(_Model):
        def __init__(self):
            super().__init__(count=2)
            self.compare_calls = 0

        def compare_event(self, *, event, verification, mappings):
            self.compare_calls += 1
            return super().compare_event(event=event, verification=verification, mappings=mappings)

    document = DiscoveryDocument("doc-1", 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
    model = WholeEvent()
    run = run_discovery(documents=(document,), configuration=_configuration(), model=model, verify=_verify,
                        metadata=_Metadata(), cutoff_at=NOW)
    assert model.compare_calls == 1
    assert [(item.mapping.company_code, item.comparison.rank, item.comparison.differences["role"])
            for item in run.candidates] == [("300000.SZ", 1, "primary"), ("300001.SZ", 2, "alternative")]
    assert run.events[0].facts["eventComparison"]["summary"] == "事件共同事实已经核对"

    class Reversed(WholeEvent):
        def compare_event(self, *, event, verification, mappings):
            result = super().compare_event(event=event, verification=verification, mappings=mappings)
            rows = dict(result.candidates)
            second = mappings[1].company_code
            rows[second] = CandidateComparison("相反的独立判断", {"role": "primary", "priorityReason": "更强",
                "gap": "不应独立判断", "rankChangeConditions": "资料", "twoDayReason": "催化"}, event.source_refs, rank=1)
            return EventComparison(result.summary, rows, result.evidence_refs)

    shared = run_discovery(documents=(document,), configuration=_configuration(), model=Reversed(), verify=_verify,
                           metadata=_Metadata(), cutoff_at=NOW)
    assert shared.state == "completed" and not shared.issues
    assert [(item.mapping.company_code, item.comparison.rank, item.comparison.differences["role"])
            for item in shared.candidates] == [("300000.SZ", 1, "primary"), ("300001.SZ", 1, "primary")]


@pytest.mark.parametrize("text", ["不输出涨停概率，无法估计涨停概率。", "不使用机械预测分数，只说明资料缺口。"])
def test_negative_probability_or_score_declaration_is_allowed(text):
    class Negative(_Model):
        def compare_event(self, *, event, verification, mappings):
            result = super().compare_event(event=event, verification=verification, mappings=mappings)
            only = mappings[0].company_code
            comparison = result.candidates[only]
            return EventComparison(text, {only: CandidateComparison(text, comparison.differences,
                comparison.evidence_refs, comparison.rank)}, result.evidence_refs)

    document = DiscoveryDocument("doc-1", 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
    assert run_discovery(documents=(document,), configuration=_configuration(), model=Negative(), verify=_verify,
                         metadata=_Metadata(), cutoff_at=NOW).state == "completed"


@pytest.mark.parametrize("field", ["summary", "reverse_summary", "differences", "classification", "numeric_key"])
def test_nested_positive_prediction_is_rejected_from_every_discovery_string(field):
    class Predicted(_Model):
        def compare_event(self, *, event, verification, mappings):
            result = super().compare_event(event=event, verification=verification, mappings=mappings)
            only = mappings[0].company_code
            comparison = result.candidates[only]
            if field == "summary":
                return EventComparison("涨停概率70%", result.candidates, result.evidence_refs)
            if field == "reverse_summary":
                return EventComparison("70%概率涨停", result.candidates, result.evidence_refs)
            if field == "differences":
                return EventComparison(result.summary, {only: CandidateComparison(comparison.summary,
                    {**comparison.differences, "gap": "机械预测分数：80分"}, comparison.evidence_refs, comparison.rank)}, result.evidence_refs)
            if field == "numeric_key":
                return EventComparison(result.summary, {only: CandidateComparison(comparison.summary,
                    {**comparison.differences, "priorityScore": "80"}, comparison.evidence_refs, comparison.rank)}, result.evidence_refs)
            return result

        def classify_opportunity(self, **kwargs):
            result = super().classify_opportunity(**kwargs)
            if field == "classification":
                result["reason"] = "涨停概率为70%"
            return result

    document = DiscoveryDocument("doc-1", 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
    run = run_discovery(documents=(document,), configuration=_configuration(), model=Predicted(), verify=_verify,
                        metadata=_Metadata(), cutoff_at=NOW)
    assert run.state == "partial"
    assert run.issues and run.issues[0].code == "contract_invalid"


def test_same_company_multiple_catalysts_share_quota_card_and_window(tmp_path):
    class Multiple(_Model):
        def understand(self, *, document):
            return tuple(EventDraft(key, "initial", "announcement", key, "disclosure", {"new": key},
                                    (document.evidence_ref,)) for key in ("policy-a", "order-b"))

        def prioritize(self, *, candidates):
            companies = {}
            for item in candidates:
                companies.setdefault(item.mapping.company_code, (item.event.canonical_key, item.mapping.company_code))
            return tuple(companies.values())

    from dataclasses import replace
    from neckline.k10 import store
    path = tmp_path / "multiple.sqlite"
    _seed_documents(path)
    result = run_discovery(documents=load_documents_from_store(cutoff_at=NOW_TEXT, db_path=path),
                           configuration=_configuration(), model=Multiple(count=31), verify=_verify,
                           metadata=_Metadata(), cutoff_at=NOW)
    assert len(result.candidates) == 60
    assert len({item.mapping.company_code for item in result.candidates}) == 30
    assert result.deferred_count == 1
    writer = SqliteDiscoveryWriter(scan_id="scan-1", db_path=path, created_at=NOW_TEXT)
    persist_discovery(run=result, writer=writer)
    store.publish_opportunities(batch_id="pub", scan_id="scan-1", publication_kind="evening",
        inputs=tuple(replace(item, source_marker="evening") for item in writer.publication_inputs),
        db_path=path, clock=lambda: NOW)
    assert len(store.list_company_windows(db_path=path)) == 30
    assert len(store.list_opportunities(db_path=path)) == 60


def test_continuations_do_not_consume_new_company_quota_or_reopen_expired_opportunity():
    previous = tuple({"companyCode": f"30{number:04d}.SZ", "canonicalKey": "event-shared",
                      "opportunityKey": f"event-shared\x1f30{number:04d}.SZ",
                      "opportunityId": f"old-{number}", "state": "expired"} for number in range(5))
    document = DiscoveryDocument("doc-1", 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
    result = run_discovery(documents=(document,), configuration=_configuration(), model=_Model(count=35),
                           verify=_verify, metadata=_Metadata(), cutoff_at=NOW, previous_opportunities=previous)
    assert len(result.candidates) == 30 and result.deferred_count == 0
    assert len(result.updates) == 5
    assert all(item.opportunity["kind"] == "continuation" for item in result.updates)


def test_new_stage_requires_changed_judgment_and_frozen_draft_preserves_classification():
    from neckline.k10.discovery import freeze_discovery_run, thaw_discovery_run
    from neckline.k10.opportunity_discovery import validate_classification
    previous = ({"opportunityId": "old", "companyCode": "300001.SZ", "canonicalKey": "event"},)
    with pytest.raises(ValueError, match="关键判断"):
        validate_classification({"kind": "material_stage", "relatedOpportunityId": "old", "reason": "新阶段",
                                 "newFacts": "新审批", "twoDayReason": "新审批进入市场", "changedJudgment": None},
                                canonical_key="event", stage_key="approval", company_code="300001.SZ", previous=previous)
    document = DiscoveryDocument("doc-1", 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
    original = run_discovery(documents=(document,), configuration=_configuration(), model=_Model(),
                             verify=_verify, metadata=_Metadata(), cutoff_at=NOW)
    frozen = freeze_discovery_run(original)
    restored = thaw_discovery_run(frozen=frozen, configuration=_configuration())
    assert restored.candidates[0].opportunity == original.candidates[0].opportunity


def test_incomplete_company_comparison_cannot_be_formally_recommended():
    class Missing(_Model):
        def compare_event(self, *, event, verification, mappings):
            return EventComparison("仅公司名单", {mappings[0].company_code: CandidateComparison("仅公司名单", {}, event.source_refs, 1)}, event.source_refs)
    document = DiscoveryDocument("doc-1", 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
    rejected = run_discovery(documents=(document,), configuration=_configuration(), model=Missing(),
                             verify=_verify, metadata=_Metadata(), cutoff_at=NOW)
    assert rejected.state == "partial" and rejected.issues[0].stage == "compare"


def test_relationship_background_is_not_promoted_to_a_formal_alternative():
    class Background(_Model):
        def classify_opportunity(self, **kwargs):
            return {"kind": "background", "reason": "只有历史关系，暂无两日新机会依据"}
    document = DiscoveryDocument("doc-1", 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
    run = run_discovery(documents=(document,), configuration=_configuration(), model=Background(count=31),
                        verify=_verify, metadata=_Metadata(), cutoff_at=NOW)
    assert not run.candidates and not run.updates and run.deferred_count == 0
    assert len(run.background) == 31


def test_first_needs_review_is_persisted_pending_without_a_window_or_sample(tmp_path):
    """A verified formal candidate must not be blocked by an unrelated first-seen evidence gap."""
    from dataclasses import replace
    from neckline.k10 import store

    class Mixed(_Model):
        def understand(self, *, document):
            code = "300436.SZ" if document.document_id == "doc-0" else "300207.SZ"
            key = "guang-sheng-tang" if code == "300436.SZ" else "xin-wang-da"
            return (EventDraft(key, "initial", "announcement", key, "disclosure", {"companyCode": code},
                               (document.evidence_ref,)),)

        def map_companies(self, *, event, verification):
            return (CompanyMappingDraft(event.facts["companyCode"], "supply", event.source_refs,
                                        {"basis": "公告"}, "fixture"),)

        def classify_opportunity(self, *, event, verification, mapping, comparison, previous):
            if mapping.company_code == "300207.SZ":
                return {"kind": "needs_review", "relatedOpportunityId": None,
                        "reason": "缺少可追溯核验依据，待核", "newFacts": None,
                        "changedJudgment": None, "twoDayReason": None}
            return {"kind": "initial", "relatedOpportunityId": None, "reason": "已核实新增事实",
                    "newFacts": "本轮披露", "changedJudgment": None, "twoDayReason": "两日催化"}

    def mixed_verify(event):
        state = "verified" if event.canonical_key == "guang-sheng-tang" else "needs_review"
        refs = event.source_refs if state == "verified" else ()
        return Verification(state, "核验完成" if state == "verified" else "缺少可追溯核验依据，待核", refs)

    path = tmp_path / "first-review-pending.sqlite"
    _seed_documents(path, count=2)
    run = run_discovery(documents=load_documents_from_store(cutoff_at=NOW_TEXT, db_path=path),
                        configuration=_configuration(), model=Mixed(), verify=mixed_verify,
                        metadata=_Metadata(), cutoff_at=NOW)
    assert [item.mapping.company_code for item in run.candidates] == ["300436.SZ"]
    assert [(item.mapping.company_code, item.opportunity["kind"]) for item in run.metadata_pending] == [
        ("300207.SZ", "needs_review")
    ]
    assert not run.updates and run.deferred_count == 0

    writer = SqliteDiscoveryWriter(scan_id="scan-1", db_path=path, created_at=NOW_TEXT)
    persist_discovery(run=run, writer=writer)
    assert len(store.list_candidates(scan_id="scan-1", state=None, db_path=path)) == 1
    with read_connection(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_company_mappings").fetchone()[0] == 2
    store.publish_opportunities(batch_id="pub", scan_id="scan-1", publication_kind="evening",
        inputs=tuple(replace(item, source_marker="evening") for item in writer.publication_inputs),
        db_path=path, clock=lambda: NOW)
    assert [item["companyCode"] for item in store.list_company_windows(db_path=path)] == ["300436.SZ"]
    assert [item["companyCode"] for item in store.list_opportunities(db_path=path)] == ["300436.SZ"]


@pytest.mark.parametrize("verification_state", ["needs_review", "contradicted"])
def test_unresolved_or_contradicted_first_seen_new_opportunity_stays_pending(verification_state):
    """A model classification never self-certifies a new candidate over verification state."""
    document = DiscoveryDocument("doc-1", 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
    run = run_discovery(
        documents=(document,), configuration=_configuration(), model=_Model(),
        verify=lambda event: Verification(verification_state, "核验资料不足或存在反证", event.source_refs),
        metadata=_Metadata(), cutoff_at=NOW,
    )
    assert not run.candidates and not run.deferred
    assert [(item.mapping.company_code, item.opportunity["kind"]) for item in run.metadata_pending] == [
        ("300000.SZ", "needs_review")
    ]


def test_contradicted_material_stage_is_a_risk_update_not_a_new_candidate():
    class NewStage(_Model):
        def classify_opportunity(self, *, event, verification, mapping, comparison, previous):
            return {"kind": "material_stage", "relatedOpportunityId": "old", "reason": "新阶段披露",
                    "newFacts": "补充事项", "changedJudgment": "范围扩大", "twoDayReason": "新阶段理由"}

    document = DiscoveryDocument("doc-1", 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
    previous = ({"opportunityId": "old", "companyCode": "300000.SZ", "canonicalKey": "event-shared",
                 "opportunityKey": "event-shared\x1f300000.SZ"},)
    run = run_discovery(
        documents=(document,), configuration=_configuration(), model=NewStage(),
        verify=lambda event: Verification("contradicted", "新阶段资料被反证", event.source_refs),
        metadata=_Metadata(), cutoff_at=NOW, previous_opportunities=previous,
    )
    assert not run.candidates and not run.metadata_pending
    assert [(item.mapping.company_code, item.opportunity["kind"]) for item in run.updates] == [
        ("300000.SZ", "needs_review")
    ]
    assert "反证" in run.updates[0].opportunity["reason"]


def test_empty_formal_set_skips_prioritization_but_preserves_other_discovery_outputs():
    class EmptyFormalSet(_Model):
        def understand(self, *, document):
            key = document.document_id
            return (EventDraft(key, "stage", "announcement", key, "disclosure", {}, (document.evidence_ref,)),)

        def map_companies(self, *, event, verification):
            codes = {"pending": "300001.SZ", "excluded": "300002.SZ", "update": "300003.SZ"}
            return (CompanyMappingDraft(codes[event.canonical_key], "supply", event.source_refs,
                                        {"basis": "公告"}, "fixture"),)

        def classify_opportunity(self, *, event, verification, mapping, comparison, previous):
            if event.canonical_key == "update":
                return {"kind": "continuation", "relatedOpportunityId": "old", "reason": "常规进展",
                        "newFacts": None, "changedJudgment": None, "twoDayReason": None}
            return {"kind": "initial", "relatedOpportunityId": None, "reason": "首次披露",
                    "newFacts": "本轮披露", "changedJudgment": None, "twoDayReason": "两日理由"}

        def prioritize(self, *, candidates):
            raise AssertionError("empty formal set must not request a ranking")

    documents = tuple(DiscoveryDocument(key, 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
                      for key in ("pending", "excluded", "update"))

    def verify(event):
        state = "needs_review" if event.canonical_key == "pending" else "verified"
        return Verification(state, "待核" if state == "needs_review" else "核验完成", event.source_refs)

    previous = ({"opportunityId": "old", "companyCode": "300003.SZ", "canonicalKey": "update",
                 "opportunityKey": "update\x1f300003.SZ"},)
    run = run_discovery(documents=documents, configuration=_configuration(), model=EmptyFormalSet(), verify=verify,
                        metadata=_Metadata(excluded={"300002.SZ"}), cutoff_at=NOW,
                        previous_opportunities=previous)
    assert run.state == "completed"
    assert [event.canonical_key for event in run.events] == ["pending", "excluded", "update"]
    assert not run.candidates and not run.deferred
    assert [item.mapping.company_code for item in run.metadata_pending] == ["300001.SZ"]
    assert [item.mapping.company_code for item in run.excluded] == ["300002.SZ"]
    assert [item.mapping.company_code for item in run.updates] == ["300003.SZ"]
