from __future__ import annotations

import json

from neckline.k10 import store

from .k10_v302_fixture import build_fixture, export_api_json


def test_cross_layer_fixture_uses_real_store_api_and_runtime_paths(tmp_path):
    path = tmp_path / "fixture.sqlite"
    ids = build_fixture(path)
    windows = store.list_company_windows(db_path=path)
    assert {item["companyWindowId"] for item in windows} >= set(ids.values()) - {"report-fixture"}
    primary = next(item for item in windows if item["companyWindowId"] == ids["primaryWindowId"])
    overlap = next(item for item in windows if item["companyWindowId"] == ids["overlapWindowId"])
    assert primary["sampleClass"] == "primary" and overlap["sampleClass"] == "overlap"
    assert [item["state"] for item in store.list_company_window_evaluations(company_window_id=overlap["companyWindowId"], db_path=path)] == ["completed"]
    skipped = next(item for item in windows if item["companyWindowId"] == ids["skippedWindowId"])
    evaluations = store.list_company_window_evaluations(company_window_id=skipped["companyWindowId"], db_path=path)
    assert [(item["revision"], item["state"]) for item in evaluations] == [(2, "incomplete")]
    conflict = next(item for item in store.latest_market_day_facts(company_code="300002.SZ", db_path=path)
                    if item["tradeDate"] == skipped["d1TradeDate"])
    assert conflict["availability"] == "anomaly"
    assert next(check for check in conflict["metadata"]["fieldChecks"] if check["field"] == "high")["state"] == "conflict"
    chain = store.list_analysis_chain(company_window_id=primary["companyWindowId"], db_path=path)["items"]
    assert [item["revision"] for item in chain] == [1, 2]
    assert chain[1]["sourceRefs"] == [{"documentId": "doc-fixture", "revision": 2, "sourceKey": "fixture_verification",
                                          "fetchedAt": "2026-09-04T15:35:00+08:00", "publishedAt": "2026-09-04T15:30:00+08:00", "publishedPrecision": "exact"}]
    assert [[analysis["content"]["fullText"] for analysis in item["analyses"]] for item in chain] == [
        ["第一版正方全文", "第一版反方全文"], ["第二版正方全文", "第二版反方全文"]]
    assert [(store.get_task(task_id=task_id, db_path=path).status,
             store.get_task(task_id=task_id, db_path=path).attempt_count)
            for task_id in ("fixture-analysis-1", "fixture-analysis-2")] == [("completed", 1), ("completed", 1)]
    report = store.get_morning_report(report_id=ids["reportId"], db_path=path)
    assert {section for section, rows in report["groups"].items() if rows} == {
        "major_contrary", "thesis_changed", "continuing_or_expiring", "new", "needs_review"}
    report_items = [item for items in report["groups"].values() for item in items]
    assert len({item["opportunityId"] for item in report_items}) == 5
    assert all(item["content"]["sourceRefs"][0]["documentId"].startswith("doc-morning-") for item in report_items)
    assert all(item["content"]["independentVerificationRefs"][0]["documentId"].startswith("doc-independent-") for item in report_items)
    contrary = report["groups"]["major_contrary"][0]
    assert contrary["content"]["update"]["reasonStatus"] == "invalidated"
    assert contrary["content"]["lifecycleEventId"] and store.get_opportunity(opportunity_id=contrary["opportunityId"], db_path=path)["state"] == "withdrawn"


def test_fixture_exports_real_fastapi_payloads_for_swift_decoding(tmp_path):
    path = tmp_path / "fixture.sqlite"; build_fixture(path)
    outputs = export_api_json(path, tmp_path / "api-json")
    payloads = {name: json.loads(file.read_text(encoding="utf-8")) for name, file in outputs.items()}
    assert payloads["windows"]["items"]
    assert {item["section"] for item in payloads["morning"]["items"]} == {
        "major_contrary", "thesis_changed", "continuing_or_expiring", "new", "needs_review"}
    assert [item["revision"] for item in payloads["analysis"]["items"]] == [1, 2]
    historical = next(sample["comparison"] for item in payloads["windows"]["items"]
                      for sample in item["samples"] if sample["comparison"]["historicalCases"])
    assert historical["historicalCoverage"]["missingOutcomes"] == ["flat", "failure"]
    assert historical["historicalCases"][0]["outcome"] == "success"
    source_ref = historical["historicalCases"][0]["sourceRefs"][0]
    assert source_ref["documentId"] == "doc-fixture" and source_ref["revision"] == 1
    assert source_ref["url"] == "https://example.invalid/k10-fixture" and source_ref["excerpt"] == "可追溯的历史案例与比较资料"
