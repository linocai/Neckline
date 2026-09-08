from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from neckline.api import k10 as k10_api
from neckline.api.k10 import _metrics, _source_ref, create_router
from neckline.api.k10_schemas import CompanyWindowEvaluationOut, MarketDayOut
from neckline.k10 import store
from neckline.k10.schema import initialize_schema
from neckline.k10.types import OpportunityPublicationInput


NOW = "2026-09-06T12:00:00+00:00"


def _client(path: Path, *, config_binding: tuple[str | None, int | None, str | None] = (None, None, None),
            execution_config_binding: tuple[str | None, int | None, str | None] = (None, None, None)) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(lambda: path, lambda: None, lambda: path.parent / "parquet",
                                    current_config_binding_provider=lambda: config_binding,
                                    current_execution_config_binding_provider=lambda: execution_config_binding))
    return TestClient(app)


def _ready_config() -> dict:
    return json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())


def _execution_config() -> dict:
    return json.loads((Path(__file__).parents[1] / "neckline/config/k10-execution-v1.json").read_text())


def _seed(path: Path) -> str:
    initialize_schema(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES('SSE',?,?)", [("20260904", 1), ("20260905", 0), ("20260906", 0), ("20260907", 1), ("20260908", 1), ("20260909", 1)])
    config = {
        "configVersion": "k10-v1.4", "universe": "chinext", "excludeBaijiu": True,
        "hardExclusions": {"approved": True, "board": "chinext", "priceLimit": "none", "st": "exclude", "swL2Exclusions": ["801125.SI"]},
        "sourceAdapters": ["fixture"], "modelRoutes": {"analysis": "deepseek-v4-pro"},
        "taskPolicies": {"analysis": {"maxAttempts": 1, "costLimit": 0}},
        "marketCollection": {"retryIntervalSeconds": 300, "retryUntilMinutesAfterClose": 120},
        "evaluationPolicy": {"version": "k10-evaluation-v1.4", "selectionFreeze": "d1_open_0930", "window": "d1_d2", "primaryMetric": "close_limit_up_any_d1_d2"},
    }
    revision = store.append_run_config(config_id="cfg", payload=config, created_at=NOW, db_path=path)
    store.append_document_version(document_id="doc-1", source_key="fixture-news", external_id="notice-1",
                                  canonical_url="https://example.test/notice", content_sha256="a" * 64,
                                  published_at="2026-09-06T10:00:00+00:00", published_precision="exact", fetched_at=NOW,
                                  original_text="合成原文", excerpt="原文摘要", fetch_version="fixture",
                                  metadata={"title": "合成公告"}, created_at=NOW, db_path=path)
    refs = [{"documentId": "doc-1", "revision": 1}]
    store.append_event_revision(event_id="event-1", stable_key="event", headline="合成催化", event_kind="policy",
                                facts={"共同事实": "政策已正式发布", "订单": {"text": "新增订单 10 亿元", "unit": "CNY"}},
                                source_refs=refs, supersedes_revision=None, created_at=NOW, db_path=path)
    store.create_scan(scan_id="scan-1", window_kind="evening", cutoff_at=NOW, config_id="cfg", config_revision=revision, status="completed", coverage={"status": "complete"}, created_at=NOW, completed_at=NOW, db_path=path)
    comparison = {
        "summary": "比较",
        "differences": {"role": "primary", "priorityReason": "直接受益", "gap": "备选缺少证据", "rankChangeConditions": "新证据", "twoDayReason": "窗口内催化"},
        "evidenceRefs": refs, "rank": 1,
        "classification": {"kind": "initial", "opportunityKey": "300001:initial", "reason": "首发", "newFacts": "新增披露", "changedJudgment": None, "twoDayReason": "两日可核", "relatedOpportunityId": None},
    }
    store.create_candidate(candidate_id="cand-1", scan_id="scan-1", event_id="event-1", event_revision=1, company_code="300001.SZ", comparison=comparison, evidence=refs, created_at=NOW, db_path=path)
    alternate = {**comparison, "rank": 2, "differences": {**comparison["differences"], "role": "alternative", "priorityReason": "受益较弱", "gap": "订单兑现较慢", "rankChangeConditions": "订单超预期", "twoDayReason": "催化尚可"},
                 "classification": {**comparison["classification"], "opportunityKey": "300002:initial"}}
    store.create_candidate(candidate_id="cand-2", scan_id="scan-1", event_id="event-1", event_revision=1, company_code="300002.SZ", comparison=alternate, evidence=refs, created_at=NOW, db_path=path)
    batch = store.publish_opportunities(batch_id="batch-1", scan_id="scan-1", publication_kind="evening", inputs=[
        OpportunityPublicationInput(candidate_id="cand-1", company_code="300001.SZ", event_id="event-1", event_revision=1, opportunity_key="300001:initial", catalyst_stage="initial", category="primary", comparison=comparison, evidence_refs=tuple(refs), source_marker="evening"),
        OpportunityPublicationInput(candidate_id="cand-2", company_code="300002.SZ", event_id="event-1", event_revision=1, opportunity_key="300002:initial", catalyst_stage="initial", category="alternative", comparison=alternate, evidence_refs=tuple(refs), source_marker="evening"),
    ], db_path=path, clock=lambda: datetime(2026, 9, 6, 20, tzinfo=timezone.utc))
    return batch.batch_id


def test_get_on_missing_schema_is_503_and_never_creates_a_database(tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite"
    with _client(path) as client:
        response = client.get("/api/v1/k10/publications")
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "not_configured"
    assert not path.exists()


def test_configuration_uses_explicit_ready_binding_before_any_scan_and_never_writes(tmp_path: Path) -> None:
    path = tmp_path / "configuration.sqlite"
    initialize_schema(path)
    assert store.append_run_config(config_id="current", payload=_ready_config(), created_at=NOW, db_path=path) == 1
    assert store.append_execution_config(config_id="execution", payload=_execution_config(), created_at=NOW, db_path=path) == 1
    before = sha256(path.read_bytes()).hexdigest()

    with _client(path, config_binding=("current", 1, None), execution_config_binding=("execution", 1, None)) as client:
        response = client.get("/api/v1/k10/configuration")

    assert response.status_code == 200
    body = response.json()
    assert body["configId"] == "current" and body["configRevision"] == 1
    assert {scope["scope"] for scope in body["scopes"]} == {"candidate", "analysis", "evaluation"}
    assert all(scope["state"] == "configured" for scope in body["scopes"])
    assert store.list_scans(window_kind=None, db_path=path) == []
    assert sha256(path.read_bytes()).hexdigest() == before


def test_configuration_uses_bound_revision_not_an_old_scan_or_another_config(tmp_path: Path) -> None:
    path = tmp_path / "configuration.sqlite"
    initialize_schema(path)
    old_revision = store.append_run_config(config_id="old", payload=_ready_config(), created_at=NOW, db_path=path)
    current_revision = store.append_run_config(config_id="current", payload=_ready_config(), created_at=NOW, db_path=path)
    current_update = _ready_config()
    current_update["sourceAdapters"] = [{"key": "newer-current-source"}]
    assert store.append_run_config(config_id="current", payload=current_update, created_at=NOW, db_path=path) == 2
    assert store.append_execution_config(config_id="execution", payload=_execution_config(), created_at=NOW, db_path=path) == 1
    store.create_scan(scan_id="old-scan", window_kind="evening", cutoff_at=NOW, config_id="old",
                      config_revision=old_revision, status="completed", coverage={"status": "complete"},
                      created_at=NOW, completed_at=NOW, db_path=path)

    with _client(path, config_binding=("current", current_revision, None), execution_config_binding=("execution", 1, None)) as client:
        response = client.get("/api/v1/k10/configuration")

    assert response.status_code == 200
    assert response.json()["configId"] == "current"
    assert response.json()["configRevision"] == current_revision
    assert all(scope["state"] == "configured" for scope in response.json()["scopes"])


@pytest.mark.parametrize(
    ("binding", "error"),
    [
        ((None, None, None), "未绑定 K10_CONFIG_ID"),
        (("current", True, None), "未绑定有效的 K10_CONFIG_REVISION"),
        (("current", None, "K10_CONFIG_REVISION 必须是正整数"), "K10_CONFIG_REVISION 必须是正整数"),
        (("missing", 1, None), "指向的配置修订不存在"),
        (("current", 99, None), "指向的配置修订不存在"),
    ],
)
def test_configuration_reports_missing_invalid_or_unknown_explicit_binding(tmp_path: Path, binding, error: str) -> None:
    path = tmp_path / "configuration.sqlite"
    initialize_schema(path)
    store.append_run_config(config_id="current", payload=_ready_config(), created_at=NOW, db_path=path)

    with _client(path, config_binding=binding) as client:
        response = client.get("/api/v1/k10/configuration")

    assert response.status_code == 200
    body = response.json()
    assert body["configId"] is None and body["configRevision"] is None
    assert all(scope["state"] == "not_configured" for scope in body["scopes"])
    assert all(any(error in message for message in scope["errors"]) for scope in body["scopes"])


@pytest.mark.parametrize(
    ("execution_binding", "error"),
    [
        ((None, None, None), "未绑定 K10_EXECUTION_CONFIG_ID"),
        (("execution", None, "K10_EXECUTION_CONFIG_REVISION 必须是正整数"), "K10_EXECUTION_CONFIG_REVISION 必须是正整数"),
        (("missing", 1, None), "执行配置修订不存在"),
    ],
)
def test_candidate_configuration_requires_current_explicit_execution_binding_only(tmp_path: Path, execution_binding, error: str) -> None:
    path = tmp_path / "execution-configuration.sqlite"
    initialize_schema(path)
    store.append_run_config(config_id="current", payload=_ready_config(), created_at=NOW, db_path=path)
    with _client(path, config_binding=("current", 1, None), execution_config_binding=execution_binding) as client:
        body = client.get("/api/v1/k10/configuration").json()
    scopes = {item["scope"]: item for item in body["scopes"]}
    assert scopes["candidate"]["state"] == "not_configured"
    assert any(error in message for message in scopes["candidate"]["errors"])
    assert scopes["analysis"]["state"] == scopes["evaluation"]["state"] == "configured"


def test_publications_project_company_cards_and_multifield_wire_contract(tmp_path: Path) -> None:
    path = tmp_path / "api.sqlite"; _seed(path)
    with _client(path) as client:
        publication = client.get("/api/v1/k10/publications").json()["items"][0]
        opportunity = client.get("/api/v1/k10/opportunities").json()["items"][0]
        card = client.get("/api/v1/k10/company-windows").json()["items"][0]
        detail = client.get("/api/v1/k10/opportunities/" + opportunity["opportunityId"]).json()
    assert publication["schemaVersion"] == "k10-api-v2" and publication["sampleCount"] == 2
    assert opportunity["sampleClass"] == "primary" and opportunity["d1TradeDate"] == "2026-09-07"
    assert card["selection"] is None and {sample["companyCandidateId"] for sample in card["samples"]} <= {"cand-1", "cand-2"}
    assert detail["lifecycleEvents"][0]["kind"] == "published"
    assert detail["eventHeadline"] == "合成催化"
    assert {item["text"] for item in detail["commonFacts"]} == {"政策已正式发布", "新增订单 10 亿元"}
    assert {item["companyCandidateId"] for item in detail["samples"]} == {"cand-1", "cand-2"}
    compared = {item["companyCandidateId"]: item["comparison"] for item in detail["samples"]}
    assert compared["cand-1"].get("priorityReason") == "直接受益"
    assert compared["cand-2"] == {"summary": "比较", "rationale": None, "rank": 2,
                                     "priorityReason": "受益较弱", "gap": "订单兑现较慢",
                                     "rankChangeConditions": "订单超预期", "twoDayReason": "催化尚可",
                                     "eventRank": None, "rankNamespace": None,
                                     "classification": {"kind": "initial", "reason": "首发", "newFacts": "新增披露",
                                                        "changedJudgment": None, "twoDayReason": "两日可核",
                                                        "relatedOpportunityId": None},
                                     "historicalCases": [], "historicalCoverage": None}
    evidence = compared and detail["samples"][0]["evidence"][0]["sourceRef"]
    assert evidence["documentId"] == "doc-1" and evidence["revision"] == 1
    assert evidence["sourceKey"] == "fixture-news" and evidence["title"] == "合成公告"
    assert evidence["fetchedAt"] == NOW and evidence["url"] == "https://example.test/notice"
    assert "pricePlan" not in str(card) and "planStatus" not in str(opportunity)


def test_keep_is_idempotent_and_selection_freeze_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "selection.sqlite"; _seed(path)
    action_at = "2026-09-07T01:20:00+00:00"  # 09:20 CST, before the 09:30 D1 freeze.
    monkeypatch.setattr(k10_api, "_now", lambda: action_at)
    with _client(path) as client:
        window_id = client.get("/api/v1/k10/company-windows").json()["items"][0]["companyWindowId"]
        first = client.post(f"/api/v1/k10/company-windows/{window_id}/selection", json={"action": "keep", "idempotencyKey": "keep-1"})
        replay = client.post(f"/api/v1/k10/company-windows/{window_id}/selection", json={"action": "keep", "idempotencyKey": "keep-1"})
        store.freeze_company_window_selection(company_window_id=window_id, frozen_at="2026-09-08T09:30:00+08:00", db_path=path)
        card = client.get(f"/api/v1/k10/company-windows/{window_id}").json()
        selection = client.get(f"/api/v1/k10/selections/{window_id}").json()
    assert first.status_code == replay.status_code == 200
    assert first.json()["observationId"] == replay.json()["observationId"]
    assert first.json()["lastActionAt"] == action_at and first.json()["postFreeze"] is False
    assert replay.json()["replayed"] is True
    assert card["selection"]["state"] == "selected"
    assert selection["state"] == "kept" and selection["analysisJobId"]


def test_opportunity_lifecycle_keeps_risk_until_verified_morning_review(tmp_path: Path) -> None:
    path = tmp_path / "lifecycle.sqlite"; _seed(path)
    opportunity = next(item for item in store.list_opportunities(db_path=path) if item["companyCode"] == "300001.SZ")
    opportunity_id = str(opportunity["opportunityId"])
    d2_before = str(opportunity["d2TradeDate"])
    store.append_opportunity_update(
        lifecycle_event_id="risk-1", opportunity_id=opportunity_id, kind="risk", reason="反证待核",
        source_refs=(), content={"reasonStatus": "needs_review", "sourceStatus": "complete"},
        occurred_at="2026-09-07T01:00:00+00:00", created_at="2026-09-07T01:00:00+00:00", db_path=path,
    )
    with _client(path) as client:
        cards = client.get("/api/v1/k10/opportunities").json()["items"]
    assert next(item for item in cards if item["opportunityId"] == opportunity_id)["lifecycle"] == "risk"

    # A normal discovery continuation has no verified morning status and must
    # not make a still-unresolved risk disappear from the card.
    store.append_opportunity_update(
        lifecycle_event_id="continuation-1", opportunity_id=opportunity_id, kind="evidence_update", reason="常规补充",
        source_refs=(), content={"classification": {"kind": "continuation"}},
        occurred_at="2026-09-07T02:00:00+00:00", created_at="2026-09-07T02:00:00+00:00", db_path=path,
    )
    with _client(path) as client:
        cards = client.get("/api/v1/k10/opportunities").json()["items"]
    assert next(item for item in cards if item["opportunityId"] == opportunity_id)["lifecycle"] == "risk"

    # The validated morning output is the only current event that clears it.
    store.append_opportunity_update(
        lifecycle_event_id="morning-current-1", opportunity_id=opportunity_id, kind="evidence_update", reason="晨间复核完成",
        source_refs=(), content={"reasonStatus": "current", "sourceStatus": "complete"},
        occurred_at="2026-09-07T03:00:00+00:00", created_at="2026-09-07T03:00:00+00:00", db_path=path,
    )
    with _client(path) as client:
        cards = client.get("/api/v1/k10/opportunities").json()["items"]
    assert next(item for item in cards if item["opportunityId"] == opportunity_id)["lifecycle"] == "evidence_update"

    store.withdraw_opportunity(opportunity_id=opportunity_id, reason="核心事实已推翻", source_refs=(),
                               withdrawn_at="2026-09-07T04:00:00+00:00", db_path=path)
    with _client(path) as client:
        card = next(item for item in client.get("/api/v1/k10/opportunities").json()["items"]
                    if item["opportunityId"] == opportunity_id)
    assert card["lifecycle"] == "withdrawal"
    assert card["d2TradeDate"] == d2_before


def test_document_reader_projects_tushare_html_without_mutating_frozen_source(tmp_path: Path) -> None:
    path = tmp_path / "document-reader.sqlite"; _seed(path)
    original_html = "<p>第一段 &amp; 内容</p><script>广告脚本();</script><style>.ad { display:none; }</style><p>第二段</p>"
    store.append_document_version(
        document_id="tushare-html", source_key="tushare-major-news", external_id="news-1", canonical_url=None,
        content_sha256="b" * 64, published_at=NOW, published_precision="exact", fetched_at=NOW,
        original_text=original_html, excerpt="摘要", fetch_version="fixture", metadata={"title": "通讯"},
        created_at=NOW, db_path=path,
    )
    plain_text = "保持原样\n含 &amp; 字符"
    store.append_document_version(
        document_id="plain-text", source_key="fixture-news", external_id="news-2", canonical_url=None,
        content_sha256="c" * 64, published_at=NOW, published_precision="exact", fetched_at=NOW,
        original_text=plain_text, excerpt="摘要", fetch_version="fixture", metadata={"title": "纯文本"},
        created_at=NOW, db_path=path,
    )
    store.append_document_version(
        document_id="tavily-excerpt", source_key="tavily_verification", external_id="search-1", canonical_url="https://example.test/tavily",
        content_sha256="d" * 64, published_at=None, published_precision="unknown", fetched_at=NOW,
        original_text=None, excerpt="Tavily 可读摘要", fetch_version="tavily-basic-general-v2", metadata={"title": "搜索资料"},
        created_at=NOW, db_path=path,
    )
    with _client(path) as client:
        first = client.get("/api/v1/k10/documents/tushare-html", params={"limit": 5}).json()
        pages = [first["body"]]
        cursor = first["page"]["nextCursor"]
        while cursor is not None:
            page = client.get("/api/v1/k10/documents/tushare-html", params={"offset": cursor, "limit": 5}).json()
            pages.append(page["body"])
            cursor = page["page"]["nextCursor"]
        plain = client.get("/api/v1/k10/documents/plain-text").json()
        tavily = client.get("/api/v1/k10/documents/tavily-excerpt", params={"offset": 9, "limit": 5}).json()
    rendered = "".join(pages)
    assert rendered == "第一段 & 内容\n\n第二段"
    assert "<" not in rendered and "广告脚本" not in rendered and ".ad" not in rendered
    assert plain["body"] == plain_text
    assert tavily["body"] is None and tavily["excerpt"] == "Tavily 可读摘要" and tavily["page"]["nextCursor"] is None
    stored = {item["documentId"]: item for item in store.list_source_document_versions(cutoff_at=None, db_path=path)}
    assert stored["tushare-html"]["originalText"] == original_html


def test_production_verification_summary_hides_technical_fact_keys_but_keeps_raw_detail(tmp_path: Path) -> None:
    path = tmp_path / "production-facts.sqlite"; _seed(path)
    facts = {
        "design": {"eventState": "ongoing", "phase": "trial", "stageKey": "pilot"},
        "eventState": "ongoing", "status": "trial", "coverage": {"source": "complete"},
        "verification": {
            "state": "verified", "summary": "核验资料显示项目仍在推进，相关公司具备直接受益条件。",
            "evidenceRefs": [{"documentId": "doc-1", "revision": 1}], "coverage": {"state": "available"},
        },
    }
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE k10_event_revisions SET facts_json=? WHERE event_id='event-1' AND revision=1",
                     (json.dumps(facts, ensure_ascii=False),))
    with _client(path) as client:
        opportunity_id = client.get("/api/v1/k10/opportunities").json()["items"][0]["opportunityId"]
        detail = client.get(f"/api/v1/k10/opportunities/{opportunity_id}").json()
    assert detail["commonFacts"] == [{"key": "共同事实", "text": "核验资料显示项目仍在推进，相关公司具备直接受益条件。", "rawDetail": facts}]
    assert "stageKey" not in " ".join(f"{item['key']}：{item['text']}" for item in detail["commonFacts"])
    with sqlite3.connect(path) as conn:
        stored = json.loads(conn.execute("SELECT facts_json FROM k10_event_revisions WHERE event_id='event-1' AND revision=1").fetchone()[0])
        needs_review = {**facts, "verification": {**facts["verification"], "state": "needs_review"}}
        conn.execute("UPDATE k10_event_revisions SET facts_json=? WHERE event_id='event-1' AND revision=1",
                     (json.dumps(needs_review, ensure_ascii=False),))
    with _client(path) as client:
        pending = client.get(f"/api/v1/k10/opportunities/{opportunity_id}").json()
    assert stored == facts
    assert pending["commonFacts"][0]["key"] == "待核事实"
    assert pending["commonFacts"][0]["rawDetail"] == needs_review


def test_market_snapshot_source_refs_keep_collection_separate_from_data_fetch_and_reject_invalid_urls() -> None:
    daily = _source_ref({
        "url": "market-data://daily/2026-08-24/300436.SZ", "tradeDate": "2026-08-24",
        "collectedAt": "2026-09-06T14:47:08+00:00", "dataFetchedAt": "unknown",
    })
    assert daily.sourceKey == "market_snapshot" and daily.companyCode == "300436.SZ" and daily.tradeDate == "2026-08-24"
    assert daily.title == "日行情快照 · 300436.SZ · 2026-08-24"
    assert daily.collectedAt == "2026-09-06T14:47:08+00:00" and daily.fetchedAt is None
    assert daily.factId is None and daily.revision is None and daily.documentId is None

    factor = _source_ref({
        "url": "market-data://adj_factor/300436.SZ", "companyCode": "300436.SZ",
        "collectedAt": "2026-09-06T14:47:08+00:00", "dataFetchedAt": "2026-09-06T14:40:00+00:00",
    })
    assert factor.sourceKey == "market_snapshot" and factor.title == "复权因子快照 · 300436.SZ"
    assert factor.tradeDate is None and factor.fetchedAt == "2026-09-06T14:40:00+00:00"

    mismatched = _source_ref({
        "url": "market-data://daily/2026-08-24/300436.SZ", "tradeDate": "2026-08-25",
        "collectedAt": "2026-09-06T14:47:08+00:00", "dataFetchedAt": "2026-09-06T14:40:00+00:00",
    })
    malformed = _source_ref({"url": "market-data://daily/2026-02-30/300436.SZ"})
    for ref in (mismatched, malformed):
        assert ref.sourceKey is None and ref.title is None and ref.companyCode is None and ref.tradeDate is None
        assert ref.factId is None and ref.revision is None and ref.url is None


def test_results_keep_overlap_and_missing_data_out_of_primary_denominator(tmp_path: Path) -> None:
    path = tmp_path / "results.sqlite"; _seed(path)
    with _client(path) as client:
        window_id = next(item["companyWindowId"] for item in client.get("/api/v1/k10/company-windows").json()["items"] if item["companyCode"] == "300001.SZ")
    market_revision = store.append_market_day_fact(company_code="300001.SZ", trade_date="2026-09-07", availability="data_gap",
        open_price=None, high_price=None, low_price=None, close_price=None, pre_close=None, limit_up_price=None,
        close_limit_up=None, touched_limit_up=None, source_refs=(), obtained_at="2026-09-07T16:01:00+08:00",
        created_at=NOW, db_path=path)
    store.freeze_company_window_selection(company_window_id=window_id, frozen_at="2026-09-08T09:30:00+08:00", db_path=path)
    store.append_company_window_evaluation(company_window_id=window_id, state="incomplete", fact_refs=[{"factId": store.market_day_fact_id(company_code="300001.SZ", trade_date="2026-09-07"), "companyCode": "300001.SZ", "tradeDate": "2026-09-07", "revision": market_revision}], result={"companyWindowId": window_id, "companyCode": "300001.SZ", "sampleClass": "primary", "selection": {"state": "unhandled", "actionIds": [], "frozenAt": "2026-09-07T09:30:00+08:00"}, "d1": {"tradeDate": "2026-09-07", "availability": "data_gap", "sourceRefs": []}, "d2": {"tradeDate": "2026-09-08", "availability": "data_gap", "sourceRefs": []}, "primaryEligible": False, "closeLimitHitAny": None, "firstTouchDay": None, "d1OpenGap": None, "gaps": [{"day": "D1", "reason": "data_gap"}]}, evaluated_at="2026-09-08T15:00:00+08:00", created_at=NOW, db_path=path)
    with _client(path) as client:
        result = client.get("/api/v1/k10/results").json()
    assert result["primary"]["unhandled"]["sampleCount"] == 1
    assert result["primary"]["unhandled"]["eligibleCount"] == 0
    assert result["records"][0]["d2"]["availability"] == "data_gap"
    fact = result["records"][0]["factRefs"][0]
    assert fact["factId"] == store.market_day_fact_id(company_code="300001.SZ", trade_date="2026-09-07")
    assert fact["companyCode"] == "300001.SZ" and fact["tradeDate"] == "2026-09-07" and fact["revision"] == 1
    assert fact["sourceKey"] == "market" and fact["fetchedAt"] == "2026-09-07T16:01:00+08:00" and fact["documentId"] is None


def test_metrics_require_boolean_limit_observation_and_never_assign_unfrozen_group() -> None:
    complete_day = lambda day, touched: MarketDayOut(tradeDate=day, availability="available", closeLimitUp=False,
        touchedLimitUp=touched, open=10, high=11, low=9, close=10, preClose=10, limitUpPrice=11)
    item = CompanyWindowEvaluationOut(companyWindowId="window", opportunityIds=[], companyCode="300001.SZ",
        sampleClass="primary", selection=None, state="completed", revision=1, updatedAt="2026-01-03T15:00:00+08:00",
        d1=complete_day("2026-01-02", True), d2=complete_day("2026-01-03", False), primaryEligible=True,
        closeLimitHitAny=False)
    metrics = _metrics([item], windows={"window": {"d2CloseAt": "2026-01-03T15:00:00+08:00"}})
    assert metrics.eligibleCount == 1 and metrics.touchRate == 1.0 and metrics.selectionPendingCount == 1
    incomplete_limit = item.model_copy(update={"d2": item.d2.model_copy(update={"touchedLimitUp": None})})
    assert _metrics([incomplete_limit], windows={"window": {"d2CloseAt": "2026-01-03T15:00:00+08:00"}}).eligibleCount == 0


def test_touch_rate_uses_the_same_eligible_or_observed_samples_for_numerator_and_denominator() -> None:
    day = lambda trade_date, touched: MarketDayOut(tradeDate=trade_date, availability="available", closeLimitUp=False,
        touchedLimitUp=touched, open=10, high=11, low=9, close=10, preClose=10, limitUpPrice=11)
    matured = CompanyWindowEvaluationOut(companyWindowId="matured", opportunityIds=[], companyCode="300001.SZ",
        sampleClass="primary", selection=None, state="completed", revision=1, updatedAt="2026-01-03T15:00:00+08:00",
        d1=day("2026-01-02", True), d2=day("2026-01-03", False), primaryEligible=True, closeLimitHitAny=False)
    not_due = matured.model_copy(update={"companyWindowId": "not-due"})
    primary = _metrics([matured, not_due], windows={
        "matured": {"d2CloseAt": "2026-01-03T15:00:00+08:00"},
        "not-due": {"d2CloseAt": "2027-01-03T15:00:00+08:00"},
    })
    assert primary.eligibleCount == 1 and primary.touchRate == 1.0 and primary.touchCount == 2

    incomplete_overlap = not_due.model_copy(update={"companyWindowId": "incomplete-overlap", "d2": day("2026-01-03", None)})
    overlap = _metrics([matured, incomplete_overlap], touch_denominator="observed")
    assert overlap.observedCompleteCount == 1 and overlap.touchRate == 1.0 and overlap.touchCount == 2
