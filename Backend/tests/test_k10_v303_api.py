"""API regressions for the user-visible 3.0.3 K10 repairs."""
from __future__ import annotations

from neckline.api.k10 import _comparison, _metrics
from neckline.api.k10_schemas import CompanyWindowEvaluationOut, MarketDayOut


_METRIC_FIELDS = {
    "incompleteCount", "pendingCount", "suspendedCount", "dataGapCount", "anomalyCount",
}


def test_api_projects_frozen_new_stage_reason_and_retains_legacy_absence(tmp_path):
    from tests.k10_v303_fixture import build_fixture
    from tests.test_k10_api import _client

    path = tmp_path / "v303-classification.sqlite"
    ids = build_fixture(path)
    with _client(path) as client:
        response = client.get(f"/api/v1/k10/company-windows/{ids['stageWindowId']}")
        assert response.status_code == 200, response.text
        samples = response.json()["opportunities"]
        stage_opportunity = next(item for item in samples if item["relatedOpportunityId"] is not None)
        detail = client.get(f"/api/v1/k10/opportunities/{stage_opportunity['opportunityId']}")
        assert detail.status_code == 200, detail.text
        classification = detail.json()["samples"][0]["comparison"]["classification"]

    assert classification == {
        "kind": "material_stage",
        "reason": "实质订单改变了原先仅获批的判断",
        "newFacts": "原审批之后，已正式签署实质订单，两个交易日内将披露交付安排。",
        "changedJudgment": "从获批但未落地，转为实质订单已签署",
        "twoDayReason": "两日内披露交付安排，若公告否认则撤回",
        "relatedOpportunityId": stage_opportunity["relatedOpportunityId"],
    }
    # B33 rows without a mappable frozen classification remain readable and do
    # not acquire a made-up explanation at the API boundary.
    assert _comparison({"summary": "legacy"}).classification is None
    assert _comparison({"classification": {"kind": "material_stage", "reason": "缺字段"}}).classification is None


def test_results_expose_all_category_counts_for_every_metric_projection(tmp_path):
    from tests.k10_v303_fixture import build_fixture
    from tests.test_k10_api import _client

    path = tmp_path / "v303-results.sqlite"
    build_fixture(path)
    with _client(path) as client:
        response = client.get("/api/v1/k10/results")
    assert response.status_code == 200, response.text
    body = response.json()

    projections = [*body["primary"].values(), body["overlap"]]
    for cohort in body["cohorts"]:
        projections.extend(cohort["primary"].values())
        projections.append(cohort["overlap"])
    for group in body["eventGroups"]:
        projections.extend(group["primary"].values())
    assert projections and all(_METRIC_FIELDS <= set(metric) for metric in projections)

    overall = body["primary"]["all"]
    assert overall["pendingCount"] == overall["suspendedCount"] == overall["anomalyCount"] == 1
    assert overall["incompleteCount"] == 4
    assert overall["dataGapCount"] == 2
    assert overall["dataGapCount"] <= overall["incompleteCount"]


def test_pending_data_gap_is_not_counted_as_completed_window_gap():
    pending = CompanyWindowEvaluationOut(
        companyWindowId="pending", companyCode="300005.SZ", sampleClass="primary", state="pending",
        revision=1, updatedAt="2026-09-07T09:01:00+08:00", d1=MarketDayOut(
            tradeDate="2026-09-07", availability="data_gap", closeLimitUp=None, touchedLimitUp=None,
        ), d2=MarketDayOut(
            tradeDate="2026-09-08", availability="data_gap", closeLimitUp=None, touchedLimitUp=None,
        ), primaryEligible=False, closeLimitHitAny=None,
    )
    metrics = _metrics([pending], windows={"pending": {"d2CloseAt": "2026-09-08T15:00:00+08:00"}})

    assert metrics.sampleCount == metrics.pendingCount == 1
    assert metrics.incompleteCount == metrics.dataGapCount == metrics.eligibleCount == 0
