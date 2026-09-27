"""Schema 10 reader contract from the real B90 CLI/worker SQLite output.

This is intentionally a consumer-side check.  It never inserts a report row
or fabricates a JSON response: ``generate_b90_loopback`` owns the producer,
then this file reads the isolated database through the public FastAPI router
that the Swift client will use.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import explicit_bindings, generate_b90_loopback


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _report_cards(report: dict[str, Any]) -> list[dict[str, Any]]:
    if report["windowKind"] == "evening":
        return report["eveningCards"]
    return report["updatedCards"] + report["addedCards"]


def _assert_discovery(report: dict[str, Any]) -> None:
    discovery = report.get("discovery")
    assert isinstance(discovery, dict), "Schema 10 must state discovery truth before cards can be interpreted"
    assert set(discovery) == {"state", "outcome", "companyCount", "reasonCodes"}
    state, outcome = discovery["state"], discovery["outcome"]
    cards = _report_cards(report)
    assert state in {"complete", "partial", "unavailable"}
    assert outcome in {"recommendations", "no_recommendation", "not_completed"}
    assert isinstance(discovery["companyCount"], int) and discovery["companyCount"] >= 0
    assert isinstance(discovery["reasonCodes"], list)
    if outcome == "no_recommendation":
        assert state == "complete"
        assert cards == [], "normal zero must not be represented by a placeholder card"
    if outcome == "not_completed":
        assert state != "complete", "unfinished discovery must never be presented as normal zero"
    if outcome == "recommendations" and state == "complete":
        assert cards, "completed recommendations need readable cards"


def _assert_morning_review(report: dict[str, Any], *, parent_report_id: str) -> list[dict[str, Any]]:
    review = report.get("morningReview")
    assert isinstance(review, dict), "Schema 10 morning report must retain the frozen parent review channel"
    assert set(review) == {"state", "parentReportId", "targetCompanyCount", "targetReasonCount", "items"}
    assert review["parentReportId"] == parent_report_id
    assert review["state"] in {"complete", "partial", "unavailable"}
    assert isinstance(review["targetCompanyCount"], int) and review["targetCompanyCount"] >= 0
    assert isinstance(review["targetReasonCount"], int) and review["targetReasonCount"] >= 0
    items = review["items"]
    assert isinstance(items, list)
    assert len(items) == review["targetCompanyCount"], "every frozen evening company needs one review result"
    assert len({item["companyCode"] for item in items}) == review["targetCompanyCount"]
    opportunity_ids: set[str] = set()
    for item in items:
        assert set(item) == {
            "reviewId", "parentCardId", "companyCode", "companyName", "opportunityIds",
            "unreviewedOpportunityIds", "status", "outcome", "analysisText", "checkedScope", "sourceRefs",
        }
        assert item["reviewId"] and item["companyCode"]
        assert item["status"] in {"completed", "partial", "failed", "not_started"}
        assert item["outcome"] in {"changed", "no_material_change", "uncertain"}
        assert set(item["unreviewedOpportunityIds"]).issubset(item["opportunityIds"])
        if item["outcome"] == "no_material_change":
            assert item["status"] == "completed"
        opportunity_ids.update(item["opportunityIds"])
    assert len(opportunity_ids) == review["targetReasonCount"], "all frozen evening reasons remain visible"
    return items


def test_b90_actual_fastapi_loopback_exports_schema10_for_swift(tmp_path, monkeypatch):
    """Read B90 reports and one original-entry route through the public API."""
    generated = generate_b90_loopback(tmp_path / "producer", monkeypatch)
    assert generated.bindings == explicit_bindings(generated.database)

    with base.actual_api(generated.database, **generated.bindings) as client:
        evening_latest = client.get("/api/v1/k10/v2/reports/latest", params={"window": "evening"})
        morning_latest = client.get("/api/v1/k10/v2/reports/latest", params={"window": "morning"})
        evening_exact = client.get(f"/api/v1/k10/v2/reports/{generated.evening_report_id}")
        morning_exact = client.get(f"/api/v1/k10/v2/reports/{generated.morning_report_id}")
        for response in (evening_latest, morning_latest, evening_exact, morning_exact):
            assert response.status_code == 200
            assert response.json()["schemaVersion"] == 10

        evening = evening_exact.json()["report"]
        morning = morning_exact.json()["report"]
        assert evening_latest.json()["report"]["reportId"] == generated.evening_report_id
        assert morning_latest.json()["report"]["reportId"] == generated.morning_report_id
        assert evening["windowKind"] == "evening"
        assert morning["windowKind"] == "morning"
        _assert_discovery(evening)
        _assert_discovery(morning)

        catalysts = [catalyst for card in evening["eveningCards"] for catalyst in card["catalysts"]]
        assert catalysts, "the real B90 evening producer needs at least one published catalyst"
        for catalyst in catalysts:
            assert catalyst["analysisText"].strip(), "natural company-relation explanation is per catalyst"
            assert catalyst["sourceRefs"], "a catalyst explanation must retain its own sources"

        review_items = _assert_morning_review(morning, parent_report_id=generated.evening_report_id)
        source_refs = [ref for catalyst in catalysts for ref in catalyst["sourceRefs"]]
        source_refs.extend(ref for item in review_items for ref in item["sourceRefs"])
        document_ref = next((ref for ref in source_refs if ref.get("documentId")), None)
        assert document_ref is not None, "B90 reader needs a document identity for a raw-material entry"
        document = client.get(
            f"/api/v1/k10/documents/{document_ref['documentId']}",
            params={"revision": document_ref.get("revision"), "offset": 0, "limit": 6000},
        )
        assert document.status_code == 200
        document_json = document.json()
        assert document_json["contentKind"] in {"original", "excerpt", "unavailable"}
        if document_json["contentKind"] == "original":
            assert document_json["body"], "an original entry must contain readable original text"
        elif document_json["contentKind"] == "excerpt":
            assert document_json["body"] is None and document_json["excerpt"]
        else:
            assert document_json["body"] is None

    evidence = tmp_path / "evidence" / "backend"
    _write_json(evidence / "b90-evening-report.json", evening_latest.json())
    _write_json(evidence / "b90-morning-report.json", morning_latest.json())
    _write_json(evidence / "b90-source-document.json", document_json)
    _write_json(evidence / "b90-loopback-manifest.json", {
        "database": str(generated.database),
        "eveningReportId": generated.evening_report_id,
        "morningReportId": generated.morning_report_id,
        "bindings": generated.bindings,
        "entry": "actual CLI enqueue -> worker -> SQLite -> FastAPI GET",
    })
