from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api.k10 import create_router
from .k10_v304_fixture import build_fixture


def test_build35_producer_api_contract_keeps_configuration_gaps_overlap_and_evidence(tmp_path):
    path = tmp_path / "fixture.sqlite"
    ids = build_fixture(path)
    app = FastAPI()
    app.include_router(create_router(lambda: path, lambda: None, lambda: tmp_path / "parquet",
                                    lambda: ("cfg-fixture", 1, None)))
    with TestClient(app) as client:
        results = client.get("/api/v1/k10/results").json()
        missing = next(row for row in results["records"] if row["companyWindowId"] == ids["unconfiguredWindowId"])
        assert missing["evaluationConfigurationState"] == "not_configured"
        assert missing["closeLimitHitAny"] is None
        assert missing["primaryEligible"] is False
        assert results["primary"]["all"]["notConfiguredCount"] == 1
        limit_gap = next(row for row in results["records"] if row["companyWindowId"] == ids["unhandledWindowId"])
        assert "limit_data_unavailable" in limit_gap["gaps"]
        assert results["primary"]["all"]["dataGapCount"] == 2
        overlap = next(group for group in results["eventGroups"] if ids["overlapWindowId"] in group["companyWindowIds"])
        assert overlap["overlap"]["hitCount"] == 1
        assert overlap["overlap"]["eligibleCount"] == 0

        scan = client.get("/api/v1/k10/scans/scan-v304-coverage").json()
        assert scan["coverageStatus"] == "partial"
        assert scan["sourceReplay"]["replaySeconds"] == 86400
        source = scan["sourceCoverage"][0]
        assert source["timeCoverage"] == "partial"
        assert source["unknownPublicationTimeCount"] == 1
        assert source["uncertainTimeDocumentRefs"][0]["publishedPrecision"] == "unknown"

        report = client.get("/api/v1/k10/morning-reports/latest").json()
        complete = next(row for row in report["items"] if row["section"] == "continuing_or_expiring")
        assert complete["coverageStatus"] == "complete"
        detail = client.get("/api/v1/k10/opportunities/" + ids["withdrawnOpportunityId"]).json()
        withdrawal = next(row for row in detail["lifecycleEvents"] if row["kind"] == "withdrawal")
        assert "doc-v304-independent" in {ref["documentId"] for ref in withdrawal["sourceRefs"]}
        tied = [row for row in detail["samples"] if row["category"] == "tied"]
        assert len(tied) == 2
        assert {row["comparison"]["eventRank"] for row in tied} == {1}
        assert {row["rank"] for row in tied} == {1, 2}
