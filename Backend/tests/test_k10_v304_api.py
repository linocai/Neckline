"""Regression coverage for the Build 35 read contract."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from neckline.api.k10 import create_router
from neckline.k10 import store
from neckline.k10.evaluation import evaluate_company_window, evaluation_state


def _client(path, tmp_path):
    app = FastAPI()
    app.include_router(create_router(
        lambda: path, lambda: None, lambda: tmp_path / "parquet",
        current_config_binding_provider=lambda: ("cfg-fixture", 1, None),
    ))
    return TestClient(app)


@pytest.mark.parametrize("persisted_kind", ["completed", "incomplete"])
def test_v304_results_keep_an_unconfigured_window_visible_without_scoring_it(tmp_path, persisted_kind):
    from tests.k10_v304_fixture import build_fixture

    path = tmp_path / "configuration-gap.sqlite"
    ids = build_fixture(path)
    with _client(path, tmp_path) as client:
        baseline = client.get("/api/v1/k10/results").json()
    window = next(item for item in store.list_company_windows(db_path=path)
                  if item["companyWindowId"] == ids["unconfiguredWindowId"])
    facts = (store.list_market_day_facts(company_code=window["companyCode"], db_path=path)
             if persisted_kind == "completed" else [])
    persisted = evaluate_company_window(window=window, market_facts=facts,
                                        as_of="2026-09-04T16:00:00+08:00")
    assert evaluation_state(persisted) == persisted_kind
    store.append_company_window_evaluation(
        company_window_id=window["companyWindowId"], state=evaluation_state(persisted),
        fact_refs=persisted.fact_refs, result=persisted.to_dict(),
        evaluated_at="2026-09-04T16:00:00+08:00", created_at="2026-09-04T16:00:00+08:00", db_path=path,
    )
    with _client(path, tmp_path) as client:
        result = client.get("/api/v1/k10/results")
    assert result.status_code == 200, result.text
    body = result.json()
    row = next(item for item in body["records"] if item["companyWindowId"] == ids["unconfiguredWindowId"])

    assert body["state"] == body["configurationState"] == "not_configured"
    assert "evaluationPolicy" in body["configurationMissing"]
    assert row["state"] == "not_configured"
    assert row["evaluationConfigurationState"] == "not_configured"
    assert "evaluationPolicy" in row["evaluationConfigurationMissing"]
    assert row["closeLimitHitAny"] is None and row["primaryEligible"] is False
    assert row["knownTouchDays"] == [] and row["factRefs"] == []
    assert body["primary"]["all"]["notConfiguredCount"] >= 1
    assert body["primary"]["all"]["knownHitCount"] == baseline["primary"]["all"]["knownHitCount"]
    assert body["primary"]["all"]["touchCount"] == baseline["primary"]["all"]["touchCount"]
    stored = next(item for item in store.list_company_window_evaluations(db_path=path)
                  if item["companyWindowId"] == window["companyWindowId"])
    assert stored["state"] == persisted_kind


def test_v304_valid_frozen_evaluation_remains_readable_when_another_window_is_invalid(tmp_path):
    from tests.k10_v304_fixture import build_fixture

    path = tmp_path / "valid-frozen-evaluation.sqlite"
    ids = build_fixture(path)
    with _client(path, tmp_path) as client:
        result = client.get("/api/v1/k10/results")
    assert result.status_code == 200, result.text
    row = next(item for item in result.json()["records"] if item["companyWindowId"] == ids["primaryWindowId"])
    assert row["evaluationConfigurationState"] == "configured"
    assert row["state"] in {"completed", "incomplete", "pending", "due"}


def test_v304_scan_and_lifecycle_hydrate_time_uncertainty_and_independent_evidence(tmp_path):
    from tests.k10_v304_fixture import build_fixture

    path = tmp_path / "source-and-withdrawal.sqlite"
    ids = build_fixture(path)
    with _client(path, tmp_path) as client:
        scan = client.get("/api/v1/k10/scans/scan-v304-coverage")
        detail = client.get("/api/v1/k10/opportunities/" + ids["withdrawnOpportunityId"])
    assert scan.status_code == detail.status_code == 200
    source = scan.json()["sourceCoverage"][0]
    assert scan.json()["coverageStatus"] == "partial"
    assert source["timeCoverage"] == "partial"
    assert source["unknownPublicationTimeCount"] == 1
    assert source["uncertainTimeDocumentRefs"] == [{
        "documentId": source["uncertainTimeDocumentRefs"][0]["documentId"],
        "factId": None, "companyCode": None, "tradeDate": None, "revision": 1,
        "sourceKey": "tushare-major-news", "title": "公开时间待核的合成资料", "url": None,
        "excerpt": None, "publishedAt": None, "publishedPrecision": "unknown",
        "fetchedAt": "2026-09-07T13:00:00+00:00", "collectedAt": None,
    }]
    withdrawal = next(item for item in detail.json()["lifecycleEvents"] if item["kind"] == "withdrawal")
    assert {ref["documentId"] for ref in withdrawal["sourceRefs"]} >= {
        "doc-v304-morning", "doc-v304-independent"
    }
    assert {ref["documentId"] for ref in withdrawal["independentVerificationRefs"]} == {"doc-v304-independent"}


def test_v304_event_groups_expose_overlap_metrics_separately_from_primary_rate(tmp_path):
    from tests.k10_v304_fixture import build_fixture

    path = tmp_path / "event-overlap.sqlite"
    ids = build_fixture(path)
    with _client(path, tmp_path) as client:
        result = client.get("/api/v1/k10/results")
    assert result.status_code == 200, result.text
    group = next(item for item in result.json()["eventGroups"] if ids["overlapWindowId"] in item["companyWindowIds"])

    assert group["primary"]["all"]["sampleCount"] == 0
    assert group["overlap"]["sampleCount"] == group["overlap"]["observedCompleteCount"] == 1
    assert group["overlap"]["hitCount"] == 1
