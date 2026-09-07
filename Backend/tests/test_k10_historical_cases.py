from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3

from neckline.k10.discovery import CompanyMappingDraft, DiscoveryDocument, EventDraft, EvidenceRef
from neckline.k10.historical_cases import HistoricalCaseLoader, apply_historical_assessments, make_historical_context_loader
from neckline.k10.schema import initialize_schema
from neckline.k10.store import append_document_version, append_event_revision
from neckline.k10.verification import VerificationEvidenceBundle


NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
NOW_TEXT = NOW.isoformat(timespec="seconds")


def _event(*, key: str = "battery", stage: str = "order", facts=None) -> EventDraft:
    return EventDraft(key, stage, "announcement", "电池订单进展", "disclosure",
                      facts if facts is not None else {"mechanism": "电池材料"}, (EvidenceRef("doc-current", 1),))


def _mapping() -> CompanyMappingDraft:
    return CompanyMappingDraft("300001.SZ", "order", (EvidenceRef("doc-current", 1),), {}, "fixture")


def _historic_event(path):
    append_document_version(document_id="doc-old", source_key="fixture", external_id="old",
        canonical_url="https://example.invalid/old", content_sha256="a" * 64,
        published_at=(NOW - timedelta(days=10)).isoformat(timespec="seconds"), published_precision="exact",
        fetched_at=(NOW - timedelta(days=10)).isoformat(timespec="seconds"), original_text="旧订单公告", excerpt=None,
        fetch_version="fixture", metadata={}, created_at=(NOW - timedelta(days=10)).isoformat(timespec="seconds"), db_path=path)
    append_event_revision(event_id="event-old", stable_key="old-battery", headline="历史电池订单", event_kind="disclosure",
        facts={"mechanism": "电池材料"}, source_refs=[{"documentId": "doc-old", "revision": 1}],
        supersedes_revision=None, created_at=(NOW - timedelta(days=10)).isoformat(timespec="seconds"), db_path=path)


def test_local_historical_case_preserves_observed_outcome_without_calling_it_failure(monkeypatch, tmp_path):
    path = tmp_path / "historical.sqlite"
    initialize_schema(path)
    _historic_event(path)
    monkeypatch.setattr("neckline.k10.historical_cases.store.list_opportunities", lambda **_: [{
        "opportunityId": "op-old", "eventId": "event-old", "eventRevision": 1, "catalystStage": "order",
        "companyWindowId": "window-old", "companyCode": "300123.SZ", "availableAt": (NOW - timedelta(days=8)).isoformat(timespec="seconds"),
    }])
    monkeypatch.setattr(HistoricalCaseLoader, "_visible_evaluation", lambda *_args, **_kwargs: {
        "factRefs": [{"factId": "fact-old", "companyCode": "300123.SZ", "tradeDate": "2026-08-28", "revision": 1}],
        "state": "completed", "result": {"due": True, "closeLimitHitAny": False,
            "d1": {"touchedLimitUp": False}, "d2": {"touchedLimitUp": False}, "gaps": [],
            "factRefs": [{"factId": "fact-old", "companyCode": "300123.SZ", "tradeDate": "2026-08-28", "revision": 1}],
            "d1PriceChanges": {"close": 0.01}, "d2PriceChanges": {"close": 0.02}, "windowPriceChanges": {"close": 0.02},
        },
    })

    context = make_historical_context_loader(db_path=path).load(event=_event(), mappings=(_mapping(),), as_of=NOW)
    assert context["historicalCoverage"]["state"] == "partial"
    assert context["historicalCases"][0]["outcome"] == "unclassified"
    assert context["historicalCases"][0]["observedFacts"]["relation"] == ["sameStage", "sameTopicOrMechanism"]
    assert context["historicalCases"][0]["observedFacts"]["outcome"]["state"] == "not_touched"
    assert context["historicalCases"][0]["observedFacts"]["outcome"]["facts"]["windowPriceChanges"] == {"close": 0.02}
    assert context["historicalCases"][0]["outcome"] != "failure"
    assert context["historicalCoverage"]["missingOutcomes"] == ["success", "flat", "failure"]


def test_zero_local_history_uses_injected_directed_gateway_and_freezes_public_evidence(tmp_path):
    path = tmp_path / "external.sqlite"
    initialize_schema(path)

    class Gateway:
        def __init__(self): self.calls = []
        def fetch(self, *, event, retrieved_at, cutoff_at, cutoff_inclusive=False):
            self.calls.append((event, retrieved_at, cutoff_at, cutoff_inclusive))
            document = DiscoveryDocument("doc-external", 2, (NOW - timedelta(days=90)).isoformat(timespec="seconds"),
                NOW_TEXT, None, "公开报道描述了历史案例，但没有可核两日行情。", {"title": "历史公开报道"})
            return VerificationEvidenceBundle("available", (document,), (document,), {"provider": "fixture", "state": "available"})

    gateway = Gateway()
    context = make_historical_context_loader(db_path=path, gateway=gateway, clock=lambda: NOW).load(
        event=_event(), mappings=(_mapping(),), as_of=NOW,
    )
    assert gateway.calls and "历史案例 成功 失败 平淡 公开报道" in gateway.calls[0][0].headline
    assert context["historicalCoverage"]["state"] == "partial"
    assert context["historicalCases"][0]["observedFacts"]["relation"] == ["directedEvidence"]
    assert context["historicalCases"][0]["categoryEvidence"] == [{"documentId": "doc-external", "revision": 2,
        "text": "公开报道描述了历史案例，但没有可核两日行情。"}]
    assert context["historicalCases"][0]["outcome"] == "unclassified"
    assert context["historicalCoverage"]["missingOutcomes"] == ["success", "flat", "failure"]


def test_no_local_history_and_no_gateway_reports_a_real_coverage_gap(tmp_path):
    path = tmp_path / "empty.sqlite"
    initialize_schema(path)
    context = make_historical_context_loader(db_path=path).load(event=_event(), mappings=(_mapping(),), as_of=NOW)
    assert context == {"historicalCases": [], "historicalCoverage": {"state": "unavailable",
        "requestedOutcomes": ["success", "flat", "failure"], "presentOutcomes": [],
        "missingOutcomes": ["success", "flat", "failure"], "reason": "historical_gateway_not_configured", "sourceRefs": []}}


def test_public_case_stays_unclassified_without_source_quoted_assessment(tmp_path):
    path = tmp_path / "classified.sqlite"
    initialize_schema(path)

    class Gateway:
        def fetch(self, *, event, retrieved_at, cutoff_at, cutoff_inclusive=False):
            document = DiscoveryDocument("doc-classified", 1, (NOW - timedelta(days=30)).isoformat(timespec="seconds"), NOW_TEXT,
                None, "公开复盘明确写明该历史案例成功。", {"historicalCase": {"outcome": "success", "summary": "不能信任的 metadata"}})
            return VerificationEvidenceBundle("available", (document,), (document,), {"state": "available"})

    context = make_historical_context_loader(db_path=path, gateway=Gateway(), clock=lambda: NOW).load(
        event=_event(), mappings=(_mapping(),), as_of=NOW,
    )
    assert context["historicalCases"][0]["outcome"] == "unclassified"
    classified = apply_historical_assessments(context=context, assessments=[{
        "caseId": context["historicalCases"][0]["caseId"], "outcome": "success", "summary": "来源明确的成功案例",
        "sourceQuote": "公开复盘明确写明该历史案例成功。", "sourceRefs": [{"documentId": "doc-classified", "revision": 1}],
    }])
    assert classified["historicalCases"][0]["outcome"] == "success"
    assert classified["historicalCoverage"]["presentOutcomes"] == ["success"]
    assert classified["historicalCoverage"]["missingOutcomes"] == ["flat", "failure"]


def test_visible_local_evaluation_never_reads_a_later_revision_or_market_fact(tmp_path):
    path = tmp_path / "asof.sqlite"; initialize_schema(path)
    early = (NOW - timedelta(days=2)).isoformat(timespec="seconds")
    late = (NOW + timedelta(days=2)).isoformat(timespec="seconds")
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO k10_market_day_fact_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("300123.SZ", "2026-09-03", 1, "available", 1, 1, 1, 1, 1, 1, 0, 0, None, "{}", "[]", early, early))
        connection.execute("INSERT INTO k10_market_day_fact_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("300123.SZ", "2026-09-04", 1, "available", 1, 1, 1, 1, 1, 1, 0, 0, None, "{}", "[]", late, late))
        connection.execute("INSERT INTO k10_company_window_evaluation_revisions VALUES(?,?,?,?,?,?,?)",
            ("window-old", 1, "completed", '[{"companyCode":"300123.SZ","tradeDate":"2026-09-03","revision":1}]', '{"due":true}', early, early))
        connection.execute("INSERT INTO k10_company_window_evaluation_revisions VALUES(?,?,?,?,?,?,?)",
            ("window-old", 2, "completed", '[{"companyCode":"300123.SZ","tradeDate":"2026-09-04","revision":1}]', '{"due":true}', late, late))
    loader = make_historical_context_loader(db_path=path)
    visible = loader._visible_evaluation(company_window_id="window-old", as_of=NOW)
    assert visible is not None and visible["revision"] == 1
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE k10_company_window_evaluation_revisions SET result_json=? WHERE company_window_id='window-old' AND revision=1",
                           (json.dumps({"due": True, "factRefs": [{"companyCode": "300123.SZ", "tradeDate": "2026-09-04", "revision": 1}]}),))
    assert loader._visible_evaluation(company_window_id="window-old", as_of=NOW) is None
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE k10_company_window_evaluation_revisions SET result_json='{}' WHERE company_window_id='window-old' AND revision=1")
        connection.execute("UPDATE k10_company_window_evaluation_revisions SET fact_refs_json='[{\"companyCode\":\"300123.SZ\",\"tradeDate\":\"2026-09-04\",\"revision\":1}]' WHERE company_window_id='window-old' AND revision=1")
    assert loader._visible_evaluation(company_window_id="window-old", as_of=NOW) is None


def test_same_stage_without_canonical_or_mechanism_match_is_not_local_history(monkeypatch, tmp_path):
    path = tmp_path / "stage-only.sqlite"; initialize_schema(path); _historic_event(path)
    monkeypatch.setattr("neckline.k10.historical_cases.store.list_opportunities", lambda **_: [{
        "opportunityId": "op-old", "eventId": "event-old", "eventRevision": 1, "catalystStage": "order",
        "companyWindowId": "window-old", "companyCode": "300123.SZ", "availableAt": (NOW - timedelta(days=8)).isoformat(timespec="seconds"),
    }])
    context = make_historical_context_loader(db_path=path).load(
        event=_event(key="unrelated", facts={"mechanism": "完全不同机制"}), mappings=(_mapping(),), as_of=NOW,
    )
    assert context["historicalCases"] == []
