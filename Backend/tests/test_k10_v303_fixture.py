from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api.k10 import create_router
from neckline.k10 import store

from .k10_v303_fixture import build_fixture


def test_real_api_exposes_stage_rationale_without_rewriting_the_original_opportunity(tmp_path):
    path = tmp_path / "fixture.sqlite"
    ids = build_fixture(path)
    app = FastAPI()
    app.include_router(create_router(lambda: path, lambda: None, lambda: tmp_path / "parquet"))
    client = TestClient(app)
    response = client.get("/api/v1/k10/company-windows")
    response.raise_for_status()
    windows = response.json()["items"]
    stage = next(item for item in windows if item["companyWindowId"] == ids["stageWindowId"])
    classification = stage["samples"][0]["comparison"]["classification"]
    old = next(item for item in store.list_opportunities(db_path=path)
               if item["companyWindowId"] == ids["primaryWindowId"])
    assert classification["kind"] == "material_stage"
    assert classification["relatedOpportunityId"] == old["opportunityId"]
    assert "订单" in classification["newFacts"]
    assert "获批" in classification["changedJudgment"]
    assert "两日" in classification["twoDayReason"]
    assert (old["d1TradeDate"], old["d2TradeDate"]) == ("2026-08-31", "2026-09-01")
    assert stage["sampleClass"] == "primary"
    assert (stage["d1TradeDate"], stage["d2TradeDate"]) == ("2026-09-03", "2026-09-04")


def test_real_api_separates_pending_suspension_gaps_anomalies_and_overlap(tmp_path):
    path = tmp_path / "fixture.sqlite"
    build_fixture(path)
    app = FastAPI()
    app.include_router(create_router(lambda: path, lambda: None, lambda: tmp_path / "parquet"))
    response = TestClient(app).get("/api/v1/k10/results")
    response.raise_for_status()
    results = response.json()
    primary = results["primary"]["all"]
    assert {key: primary[key] for key in (
        "sampleCount", "eligibleCount", "hitCount", "incompleteCount", "pendingCount",
        "suspendedCount", "dataGapCount", "anomalyCount",
    )} == {"sampleCount": 6, "eligibleCount": 1, "hitCount": 1, "incompleteCount": 4,
           "pendingCount": 1, "suspendedCount": 1, "dataGapCount": 2, "anomalyCount": 1}
    assert results["overlap"]["sampleCount"] == 1
    assert results["overlap"]["eligibleCount"] == 0
    assert results["overlap"]["hitCount"] == 1
