from __future__ import annotations

from datetime import datetime, timezone

import pytest

from neckline.k10.discovery import (
    CandidateComparison,
    CompanyMappingDraft,
    DiscoveryDocument,
    EvidenceRef,
    EventDraft,
    SqliteDiscoveryWriter,
    Verification,
    load_documents_from_store,
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

    def compare(self, *, event, verification, mapping, peers):
        differences = {"role": "primary", "priorityReason": mapping.company_code, "gap": "已核对同事件关系", "rankChangeConditions": "新增公司披露", "twoDayReason": "新披露进入两日观察"}
        if self.probability:
            differences["probability"] = 0.9
        return CandidateComparison(summary="具体比较", differences=differences, evidence_refs=event.source_refs)

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

    with pytest.raises(ValueError, match="未输入的原始资料"):
        run_discovery(documents=(document,), configuration=_configuration(), model=model, verify=bad_verify,
                      metadata=_Metadata(), cutoff_at=NOW)


def test_uncalibrated_probability_is_rejected():
    document = DiscoveryDocument("doc-1", 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
    with pytest.raises(ValueError, match="未校准概率"):
        run_discovery(documents=(document,), configuration=_configuration(), model=_Model(probability=True),
                      verify=_verify, metadata=_Metadata(), cutoff_at=NOW)


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
        def compare(self, *, event, verification, mapping, peers):
            return CandidateComparison("仅公司名单", {}, event.source_refs)
    document = DiscoveryDocument("doc-1", 1, NOW_TEXT, NOW_TEXT, "原文", None, {})
    with pytest.raises(ValueError, match="主推"):
        run_discovery(documents=(document,), configuration=_configuration(), model=Missing(),
                      verify=_verify, metadata=_Metadata(), cutoff_at=NOW)


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
