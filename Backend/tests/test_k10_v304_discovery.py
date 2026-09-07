"""Regression coverage for the 3.0.4 discovery and comparison repairs."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from neckline.k10 import store
from neckline.k10.discovery import (
    CandidateComparison,
    CompanyMappingDraft,
    DiscoveryDocument,
    EventComparison,
    EventDraft,
    FrozenDiscoveryDraftCompatibilityError,
    SqliteDiscoveryWriter,
    Verification,
    freeze_discovery_run,
    run_discovery,
    thaw_discovery_run,
)
from neckline.k10.historical_cases import freeze_historical_context
from neckline.k10.pipeline import DeepSeekDiscoveryModel, PipelineError
from neckline.k10.schema import initialize_schema
from neckline.k10.types import OpportunityPublicationInput
from neckline.k10.universe import CompanyMetadata
from neckline.llm.base import LLMResult


NOW = datetime(2026, 9, 7, 13, tzinfo=timezone.utc)


def _configuration() -> dict:
    return json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())


def _db(path: Path) -> None:
    initialize_schema(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES('SSE', ?, 1)", [
            ("20260907",), ("20260908",), ("20260909",), ("20260910",),
        ])
    store.append_document_version(
        document_id="doc-1", source_key="fixture", external_id="doc-1", canonical_url=None,
        content_sha256="a" * 64, published_at=NOW.isoformat(), published_precision="exact",
        fetched_at=NOW.isoformat(), original_text="可核的事件资料", excerpt=None,
        fetch_version="fixture", metadata={"title": "可核资料"}, created_at=NOW.isoformat(), db_path=path,
    )
    store.create_scan(scan_id="scan-1", window_kind="evening", cutoff_at=NOW.isoformat(),
                      config_id=None, config_revision=None, status="completed", coverage={},
                      created_at=NOW.isoformat(), completed_at=NOW.isoformat(), db_path=path)


def _comparison(*, role: str, rank: int, key: str, history: dict | None = None) -> dict:
    value = {
        "summary": "完整公司比较", "rank": rank,
        "differences": {"role": role, "priorityReason": "公开资料支持", "gap": "公司差异",
                        "rankChangeConditions": "新增反证", "twoDayReason": "两日内催化"},
        "evidenceRefs": [{"documentId": "doc-1", "revision": 1}],
        "classification": {"kind": "initial", "opportunityKey": key, "reason": "首发",
                           "newFacts": "首次披露", "changedJudgment": None,
                           "twoDayReason": "两日内催化", "relatedOpportunityId": None},
    }
    if history is not None:
        value.update(history)
    return value


def test_duplicate_historical_coverage_refs_are_deduped_but_case_refs_and_api_detail_remain(tmp_path):
    """Two comparable companies can cite one old document without blocking publication."""
    from tests.test_k10_api import _client

    path = tmp_path / "history-coverage.sqlite"
    _db(path)
    cases = [
        {"caseId": "case-a", "outcome": "unclassified", "summary": "公司 A 历史案例",
         "observedAt": "2026-08-01T09:00:00+08:00", "sourceRefs": [{"documentId": "doc-1", "revision": 1}],
         "marketFacts": [], "sourceBasedDescription": "公开资料 A"},
        {"caseId": "case-b", "outcome": "unclassified", "summary": "公司 B 历史案例",
         "observedAt": "2026-08-02T09:00:00+08:00", "sourceRefs": [{"documentId": "doc-1", "revision": 1}],
         "marketFacts": [], "sourceBasedDescription": "公开资料 B"},
    ]
    history = freeze_historical_context({"historicalCases": cases, "historicalCoverage": {
        "state": "partial", "requestedOutcomes": ["success", "flat", "failure"],
        "presentOutcomes": [], "missingOutcomes": ["success", "flat", "failure"],
        "reason": "historical_cases_found_but_requested_outcomes_missing",
        "sourceRefs": [{"documentId": "doc-1", "revision": 1}, {"documentId": "doc-1", "revision": 1}],
    }})
    assert history["historicalCoverage"]["sourceRefs"] == [{"documentId": "doc-1", "revision": 1}]
    assert [case["sourceRefs"] for case in history["historicalCases"]] == [
        [{"documentId": "doc-1", "revision": 1}], [{"documentId": "doc-1", "revision": 1}],
    ]

    event = store.append_event_revision(event_id="event-history", stable_key="history", headline="历史比较",
                                        event_kind="disclosure", facts={}, source_refs=history["historicalCoverage"]["sourceRefs"],
                                        supersedes_revision=None, created_at=NOW.isoformat(), db_path=path)
    comparison = _comparison(role="primary", rank=1, key="history\x1f300001.SZ", history=history)
    store.create_candidate(candidate_id="candidate-history", scan_id="scan-1", event_id=event.event_id,
                           event_revision=event.revision, company_code="300001.SZ", comparison=comparison,
                           evidence=[], created_at=NOW.isoformat(), db_path=path)
    store.publish_opportunities(batch_id="batch-history", scan_id="scan-1", publication_kind="evening", inputs=[
        OpportunityPublicationInput(candidate_id="candidate-history", company_code="300001.SZ", event_id=event.event_id,
            event_revision=event.revision, opportunity_key="history\x1f300001.SZ", catalyst_stage="initial",
            category="primary", comparison=comparison, evidence_refs=tuple(comparison["evidenceRefs"]), source_marker="evening"),
    ], db_path=path, clock=lambda: NOW)
    with _client(path) as client:
        detail = client.get("/api/v1/k10/opportunities").json()["items"][0]
        response = client.get("/api/v1/k10/opportunities/" + detail["opportunityId"])
    assert response.status_code == 200, response.text
    assert len(response.json()["samples"][0]["comparison"]["historicalCases"]) == 2


class _Metadata:
    def lookup(self, *, company_code, as_of):
        return CompanyMetadata(company_code, "chinext", False, "801080.SI", as_of)


class _RankModel:
    def understand(self, *, document):
        return (
            EventDraft("event-a", "initial", "confirmed", "事件 A", "disclosure", {}, (document.evidence_ref,)),
            EventDraft("event-b", "initial", "confirmed", "事件 B", "disclosure", {}, (document.evidence_ref,)),
        )

    def map_companies(self, *, event, verification):
        codes = ("300001.SZ", "300002.SZ") if event.canonical_key == "event-a" else ("300003.SZ",)
        return tuple(CompanyMappingDraft(code, "initial", event.source_refs, {}, "fixture") for code in codes)

    def compare_event(self, *, event, verification, mappings):
        tied = event.canonical_key == "event-a"
        return EventComparison("共同事实", {
            mapping.company_code: CandidateComparison(
                mapping.company_code, {"role": "tied" if tied else "primary", "priorityReason": "资料",
                "gap": "差异不足" if tied else "直接受益", "rankChangeConditions": "新资料", "twoDayReason": "两日"},
                event.source_refs, 1,
            ) for mapping in mappings
        }, event.source_refs)

    def classify_opportunity(self, *, event, verification, mapping, comparison, previous):
        return {"kind": "initial", "relatedOpportunityId": None, "reason": "首发", "newFacts": "新事实",
                "changedJudgment": None, "twoDayReason": "两日"}

    def prioritize(self, *, candidates):
        return (("event-b", "300003.SZ"), ("event-a", "300001.SZ"), ("event-a", "300002.SZ"))


def test_event_rank_ties_and_global_display_rank_survive_publish_and_api(tmp_path):
    from tests.test_k10_api import _client

    path = tmp_path / "rank-namespaces.sqlite"
    _db(path)
    document = DiscoveryDocument("doc-1", 1, NOW.isoformat(), NOW.isoformat(), "资料", None, {})
    run = run_discovery(documents=(document,), configuration=_configuration(), model=_RankModel(),
                        verify=lambda event: Verification("verified", "核验", event.source_refs),
                        metadata=_Metadata(), cutoff_at=NOW)
    tied = [candidate for candidate in run.candidates if candidate.event.canonical_key == "event-a"]
    assert [candidate.comparison.rank for candidate in tied] == [1, 1]
    assert [candidate.display_rank for candidate in tied] == [2, 3]
    recovered = thaw_discovery_run(frozen=freeze_discovery_run(run), configuration=_configuration())
    recovered_tied = [candidate for candidate in recovered.candidates if candidate.event.canonical_key == "event-a"]
    assert [candidate.comparison.event_rank for candidate in recovered_tied] == [1, 1]
    assert [candidate.display_rank for candidate in recovered_tied] == [2, 3]

    writer = SqliteDiscoveryWriter(scan_id="scan-1", db_path=path, created_at=NOW.isoformat())
    from neckline.k10.discovery import persist_discovery
    persist_discovery(run=run, writer=writer)
    store.publish_opportunities(batch_id="batch-rank", scan_id="scan-1", publication_kind="evening",
                                inputs=tuple(replace(item, source_marker="evening") for item in writer.publication_inputs),
                                db_path=path, clock=lambda: NOW)
    samples = store.list_publication_samples(batch_id="batch-rank", db_path=path)
    tied_samples = [item for item in samples if item["eventId"] == next(sample["eventId"] for sample in samples if sample["companyCode"] == "300001.SZ")]
    assert [item["rank"] for item in tied_samples] == [2, 3]
    assert all(item["comparison"]["rank"] == item["comparison"]["eventRank"] == 1 for item in tied_samples)
    assert {item["comparison"]["rankNamespace"] for item in tied_samples} == {"event"}
    with _client(path) as client:
        details = [client.get("/api/v1/k10/opportunities/" + item["opportunityId"]).json() for item in client.get("/api/v1/k10/opportunities").json()["items"]]
    tied_api = [detail for detail in details if detail["eventHeadline"] == "事件 A"]
    assert len(tied_api) == 2
    assert {detail["samples"][0]["comparison"]["eventRank"] for detail in tied_api} == {1}


def test_legacy_global_only_frozen_draft_is_blocked_without_guessing_tied_event_rank(tmp_path):
    path = tmp_path / "legacy-frozen-rank.sqlite"
    _db(path)
    document = DiscoveryDocument("doc-1", 1, NOW.isoformat(), NOW.isoformat(), "资料", None, {})
    run = run_discovery(documents=(document,), configuration=_configuration(), model=_RankModel(),
                        verify=lambda event: Verification("verified", "核验", event.source_refs),
                        metadata=_Metadata(), cutoff_at=NOW)
    frozen = freeze_discovery_run(run)
    for row in frozen["candidates"]:
        row.pop("displayRank")
        comparison = row["comparison"]
        comparison.pop("rankNamespace")
        comparison.pop("eventRank")
        comparison["rank"] = {"300003.SZ": 1, "300001.SZ": 2, "300002.SZ": 3}[row["mapping"]["companyCode"]]

    with pytest.raises(FrozenDiscoveryDraftCompatibilityError, match="缺少事件内排序"):
        thaw_discovery_run(frozen=frozen, configuration=_configuration())


def test_probability_guard_covers_common_phrase_and_preserves_explicit_refusal():
    from neckline.k10.discovery import reject_uncalibrated_prediction

    for text in ("涨停概率可能达到70%", "封板几率预计为60%", "70%概率涨停", "资料显示明日涨停概率70%"):
        with pytest.raises(ValueError, match="未校准概率"):
            reject_uncalibrated_prediction({"summary": text})
    reject_uncalibrated_prediction({"summary": "不能估计涨停概率，也不输出封板概率。"})


class _ComparisonProvider:
    def __init__(self, payload: dict):
        self.payload = payload

    def chat(self, *_args, **_kwargs):
        return LLMResult(ok=True, content=json.dumps(self.payload, ensure_ascii=False),
                         provider="fixture", model="deepseek-v4-pro")


def _deepseek_comparison(payload: dict, *, history: dict | None = None) -> DeepSeekDiscoveryModel:
    model = DeepSeekDiscoveryModel(_ComparisonProvider(payload), historical_context_loader=(
        (lambda **_: history) if history is not None else None
    ))
    model.set_scan_cutoff(NOW)
    document = DiscoveryDocument("doc-current", 1, NOW.isoformat(), NOW.isoformat(), "当前资料", None, {})
    model._documents[document.evidence_ref] = document
    return model


def _compare_with(model: DeepSeekDiscoveryModel, codes=("300001.SZ",)):
    event = EventDraft("event-current", "initial", "confirmed", "当前事件", "disclosure", {},
                       (DiscoveryDocument("doc-current", 1, NOW.isoformat(), NOW.isoformat(), "当前资料", None, {}).evidence_ref,))
    mappings = tuple(CompanyMappingDraft(code, "initial", event.source_refs, {}, "fixture") for code in codes)
    return model.compare_event(event=event, verification=Verification("verified", "核验", event.source_refs), mappings=mappings)


def _candidate_row(code: str, summary: str) -> dict:
    return {"companyCode": code, "summary": summary, "role": "primary" if code == "300001.SZ" else "alternative",
            "rank": 1 if code == "300001.SZ" else 2, "priorityReason": "公开资料", "gap": "差异",
            "rankChangeConditions": "反证", "twoDayReason": "两日", "sourceRefs": [{"documentId": "doc-current", "revision": 1}]}


def test_deepseek_duplicate_company_rows_fail_before_dictionary_collapse():
    payload = {"summary": "整体比较", "sourceRefs": [{"documentId": "doc-current", "revision": 1}], "candidates": [
        _candidate_row("300001.SZ", "第一版"), _candidate_row("300002.SZ", "另一家公司"),
        _candidate_row("300001.SZ", "第二版覆盖第一版"),
    ]}
    with pytest.raises(PipelineError, match="同一公司不得重复"):
        _compare_with(_deepseek_comparison(payload), ("300001.SZ", "300002.SZ"))


def test_deepseek_historical_assessment_probability_fails_before_it_can_be_frozen():
    history = {"historicalCases": [{
        "caseId": "case-history", "outcome": "unclassified", "summary": "历史资料",
        "observedAt": "2026-08-01T09:00:00+08:00", "sourceRefs": [{"documentId": "doc-history", "revision": 1}],
        "marketFacts": [], "sourceBasedDescription": "公开复盘明确写明该案例成功。",
    }], "historicalCoverage": {
        "state": "partial", "requestedOutcomes": ["success", "flat", "failure"], "presentOutcomes": [],
        "missingOutcomes": ["success", "flat", "failure"], "reason": "fixture", "sourceRefs": [{"documentId": "doc-history", "revision": 1}],
    }}
    payload = {"summary": "整体比较", "sourceRefs": [{"documentId": "doc-current", "revision": 1}],
               "historicalAssessments": [{"caseId": "case-history", "outcome": "success",
                   "summary": "该历史案例涨停概率可能达到70%", "sourceQuote": "公开复盘明确写明该案例成功。",
                   "sourceRefs": [{"documentId": "doc-history", "revision": 1}]}],
               "candidates": [_candidate_row("300001.SZ", "公司比较")]}
    with pytest.raises(PipelineError, match="未校准概率"):
        _compare_with(_deepseek_comparison(payload, history=history))
