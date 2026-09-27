"""B92 report entry-point acceptance using only isolated, deterministic services."""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from io import StringIO
import json
from pathlib import Path
import sqlite3
import httpx

from neckline.k10 import morning_runtime, pipeline, store
from neckline.k10.cli import enqueue_collection, main as cli_main
from neckline.k10.collection_runtime import create_collection_handler
from neckline.k10.jin10_mcp import Jin10Client
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.research_store import read_research_snapshot
from neckline.k10.v2_store import bind_strategy
from neckline.k10.windows import SHANGHAI
from neckline.k10.worker import run_once

from tests import v340_acceptance_fixture as base
from tests.test_b92_mcp_protocol import ProtocolWire, TOKEN as JIN10_TOKEN, rpc
from tests.test_v350_cli_api import DirectRoundTransport


CONFIG_DIR = Path(__file__).resolve().parents[1] / "neckline/config"
DAY = date(2026, 9, 26)
SLOT = datetime(2026, 9, 26, 8, 0, tzinfo=SHANGHAI)
RUN_AT = datetime(2026, 9, 26, 22, 0, tzinfo=SHANGHAI)


def _cli(*args: str) -> str:
    output = StringIO()
    with redirect_stdout(output):
        assert cli_main(list(args)) == 0
    return output.getvalue().strip()


def _bindings(db_path: Path) -> tuple[str, int, str, int, str, int]:
    old_id, old_revision, _, _ = base.seed_database(
        db_path, trading_day=DAY, fixture_now=SLOT - timedelta(days=1))
    old = store.read_run_config(config_id=old_id, revision=old_revision, db_path=db_path)
    payload = json.loads(json.dumps(old["payload"]))
    payload["strategySnapshotId"] = "k10-v2-b92-isolated"
    run_id = "b92-isolated-run"
    run_revision = store.append_run_config(
        config_id=run_id, payload=payload, created_at=SLOT.isoformat(), db_path=db_path)
    execution = json.loads(_cli("configure-execution", "--db", str(db_path),
        "--config-id", "b92-isolated-execution", "--file", str(CONFIG_DIR / "k10-execution-v4.json")))
    bind_strategy(db_path=db_path, snapshot_id=payload["strategySnapshotId"],
        config_id=run_id, config_revision=run_revision,
        execution_config_id=execution["configId"], execution_config_revision=execution["revision"],
        created_at=SLOT.isoformat())
    collection = json.loads(_cli("configure-collection", "--db", str(db_path),
        "--config-id", "b92-isolated-collection", "--file", str(CONFIG_DIR / "k10-collection-v1.json")))
    return (run_id, run_revision, execution["configId"], execution["revision"],
            collection["configId"], collection["revision"])


def test_collected_input_report_runs_without_any_provider_credential(tmp_path, monkeypatch):
    """A partial collection cannot make the report wait or batch-fetch sources."""
    db_path = tmp_path / "b92-minimal.sqlite"
    run_id, run_rev, exec_id, exec_rev, collection_id, collection_rev = _bindings(db_path)
    _cli("collection-control", "--db", str(db_path), "--state", "open",
         "--config-id", collection_id, "--config-revision", str(collection_rev))
    collection_task = _cli("enqueue-collection", "--db", str(db_path), "--slot", SLOT.isoformat(),
                           "--config-id", collection_id, "--config-revision", str(collection_rev))
    collected = run_once(db_path=db_path, task_id=collection_task, worker_id="b92-collection",
        lease_for=timedelta(minutes=5), clock=lambda: RUN_AT,
        handlers={"collect_news": create_collection_handler(tushare_token=None, jin10_token=None)},
        require_b76_contract=True)
    assert collected is not None and collected.status == "failed"

    # A real task producer freezes the B92 runtime and execution binding. The
    # historical fixture's source adapter is installed as a trap: B92 must not
    # call it, because only the collection worker owns ingestion.
    monkeypatch.setattr(base, "TITLE_COUNT", 0)
    transport, tavily = base.install_offline_transports(
        monkeypatch, refusal_event=None, selected_event_count=0, fixture_run_at=RUN_AT)
    provider = MeteredProvider(ledger_db=db_path, ledger_task="discovery", api_key="fixture",
        model="deepseek-v4-pro", name="fixture",
        api_url="https://fixture.invalid/v1/chat/completions", read_timeout=1,
        use_streaming=False)
    provider.max_attempts = 1
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro",
                        lambda **_kwargs: ProviderResolution("configured", provider, "fixture", None))
    report_task = _cli("enqueue", "--db", str(db_path), "--kind", "evening",
        "--trading-day", DAY.isoformat(), "--config-id", run_id,
        "--config-revision", str(run_rev), "--execution-config-id", exec_id,
        "--execution-config-revision", str(exec_rev))
    faults = []
    def handler(context):
        try:
            return pipeline.production_scan_handler(context, tushare_token=None,
                parquet_dir=tmp_path / "parquet", now=lambda: RUN_AT)
        except Exception as exc:
            faults.append(f"{type(exc).__name__}: {exc}")
            raise
    result = run_once(db_path=db_path, task_id=report_task, worker_id="b92-report",
        lease_for=timedelta(minutes=5), clock=lambda: RUN_AT,
        handlers={"evening_scan": handler}, require_b76_contract=True)
    assert result is not None and result.status == "completed", faults
    task_input = store.task_execution_input(task_id=report_task, db_path=db_path)
    scan = store.get_scan(scan_id=task_input["checkpoint"]["scanId"], db_path=db_path)
    assert scan["coverage"]["collectedInput"]["collectionTaskIds"] == [collection_task]
    assert scan["coverage"]["inputDocumentRefs"] == []
    assert transport.calls == [] and tavily.queries == []
    with base.actual_api(db_path, config_id=run_id, config_revision=run_rev,
                         execution_id=exec_id, execution_revision=exec_rev) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=evening")
        assert response.status_code == 200
        report = response.json()["report"]
    assert report["discovery"]["outcome"] == "not_completed"
    assert report["delivery"]["outcome"] == "partial"
    assert {gap["unitId"] for gap in report["delivery"]["gaps"]} == {
        "tushare-major-news", "jin10-flash", "jin10-news"}
    assert len(report["sourceCoverage"]["sources"]) == 3


class _FlashReportTransport(DirectRoundTransport):
    review_packets = []
    morning_document_ids = set()
    morning_material_contrary = False
    research_mode = "direct"
    understand_mode = "distinct"
    progress_document_ids = set()
    progress_parent_opportunity_id = None

    def respond(self, request):
        message = json.loads(request.content)["messages"][-1]["content"]
        if "<untrusted-evidence>" in message and type(self).loopback_morning_discovery_zero:
            packet = json.loads(message.split("<untrusted-evidence>\n", 1)[1]
                                .split("\n</untrusted-evidence>", 1)[0])
            type(self).review_packets.append(packet)
            matching = [row for row in [*packet["morningSourceIndex"], *packet["morningDocuments"]]
                        if datetime.fromisoformat(row["publishedAt"]).astimezone(SHANGHAI).date()
                        == date(2026, 9, 27)]
            assert matching, (packet["morningSourceIndex"], packet["morningDocuments"])
            ref = matching[0]
            assert len(packet["parentReasons"]) == 2
            if not any(doc["documentId"] == ref["documentId"]
                       for doc in packet["morningDocuments"]):
                assert any(row["documentId"] == ref["documentId"]
                           for row in packet["morningSourceIndex"])
                return self._ok({"action": "read", **ref,
                    "rationale": "隔夜原件涉及前晚两个理由，先读取已采集正文。"})
            if type(self).morning_material_contrary:
                return self._ok({"action": "conclude", "material": True,
                    "reasonStatus": "needs_review", "observationStatus": "needs_review",
                    "affectedOpportunityIds": [packet["parentReasons"][0]["opportunityId"]],
                    "summary": "隔夜原件称客户终止测试，直接冲击首条送样理由，待核公司原件。",
                    "materialContraryEvidence": [{"documentId": ref["documentId"],
                        "revision": ref["revision"], "claim": "客户终止测试"}]})
            return self._ok({"action": "conclude", "material": False,
                "reasonStatus": "current", "observationStatus": "current",
                "summary": "已核对隔夜原文，两条冻结理由未见改变判断的新事实。",
                "materialContraryEvidence": []})
        if "<untrusted-k10-evidence>" in message:
            packet = self._packet(request)
            if (packet.get("action") == "research_round"
                    and packet["evidencePacket"]["event"].get("eventState") == "confirmed_order"):
                response = super().respond(request)
                result = json.loads(response.json()["choices"][0]["message"]["content"])
                result["companyAssessments"][0]["identity"] = {
                    "kind": "material_stage",
                    "relatedOpportunityId": type(self).progress_parent_opportunity_id,
                    "reason": "客户订单由此前送样进展推进为正式确认",
                    "newFacts": "公司确认客户订单",
                    "changedJudgment": "订单已确认，改变前一晚未确认判断",
                    "twoDayReason": "观察正式订单后续履行",
                }
                result["companyAssessments"][0]["analysisText"] = (
                    "新原件确认客户订单，构成前一晚送样但订单未确认理由的实质新进展。")
                return self._ok(result)
            if (packet.get("action") == "research_round"
                    and type(self).research_mode in {"tavily", "jin10-empty"}
                    and not packet["evidencePacket"].get("queryPaths")):
                event = packet["evidencePacket"]["event"]["canonicalKey"]
                claim_id = packet["evidencePacket"]["claims"][0]["claimId"]
                ref = packet["evidencePacket"]["allowedEvidenceRefs"][0]
                company = self._company_for_event(event)
                target = "tavily" if type(self).research_mode == "tavily" else "jin10-flash"
                query = (f"离线验收 {event} 公司公告" if target == "tavily" else "测试公司")
                return self._ok({"action": "research_round",
                    "conclusion": {"researchStatus": "continue_research", "eventDisposition": "待补查",
                        "companyMappings": [], "companyDispositions": [], "materialGaps": ["订单未确认"],
                        "stopReason": "当前问题需补证", "resumeCondition": "补证返回"},
                    "questions": [{"questionId": "q-1", "claimIds": [claim_id], "companyCodes": [company],
                        "question": "是否有公司公开确认订单？", "knownEvidence": [ref],
                        "missingEvidence": ["公司确认"], "supportCondition": "公司公告确认",
                        "refuteCondition": "公司公告否认", "decisionImpact": "影响当前消息价值",
                        "state": "open", "resumeCondition": "公司披露"}],
                    "queryPaths": [{"pathId": "path-1", "questionId": "q-1", "query": query,
                        "intent": "核对样品进展是否形成订单", "targetSource": target,
                        "newPathReason": "原始消息只有送样，无订单确认",
                        "expectedInformationGain": "确认或否定订单", "expectedJudgmentChange": "改变建议风险",
                        "purposeKind": "company_event_link",
                        "targetRefs": [{"kind": "company", "companyCode": company}],
                        "state": "planned", "resultSummary": None}],
                })
            document_id = packet.get("documentId")
            if isinstance(document_id, str):
                if document_id in type(self).progress_document_ids:
                    ref = {"documentId": document_id, "revision": packet["revision"]}
                    self._record("understand:real-progress", document_id)
                    return self._ok({"events": [{"canonicalKey": "event-001",
                        "stageKey": "signed-order", "eventState": "confirmed_order",
                        "headline": "公司确认客户订单", "eventKind": "disclosure",
                        "facts": {"newFact": "客户订单确认"}, "sourceRefs": [ref],
                        "claims": [{"text": "公司公告确认客户订单", "kind": "factual_assertion",
                            "novelty": "new_fact", "speaker": "公司", "subject": "客户订单",
                            "object": "订单", "action": "确认", "stageOrCondition": "正式订单",
                            "timeText": "2026-09-27", "verificationStatus": "unverified",
                            "decisionImpact": "改变前一晚订单未确认判断", "sourceRef": ref,
                            "location": "paragraph:1"}]}], "needsFullText": False})
                if document_id in type(self).morning_document_ids:
                    self._record("understand:morning-no-new-development", document_id)
                    return self._ok({"events": [], "needsFullText": False})
                # A raw flash deliberately never had a model-facing title.
                self.document_numbers.setdefault(document_id, len(self.document_numbers))
        response = super().respond(request)
        if ("<untrusted-k10-evidence>" in message and type(self).understand_mode == "merge"
                and isinstance(self._packet(request).get("documentId"), str)):
            result = json.loads(response.json()["choices"][0]["message"]["content"])
            for event in result.get("events", ()):
                event["canonicalKey"] = "event-000"
                event["headline"] = "同一送样事项的两个独立原件"
            return self._ok(result)
        return response


def _news_wire(*, article: bool = False, empty_question: bool = False):
    tools = [{"name": name, "inputSchema": {"type": "object",
              "properties": ({"id": {"type": "string"}} if name == "get_news" else
                             {"keyword": {"type": "string"}} if name == "search_flash" else
                             {"cursor": {"type": "string"}}),
              "additionalProperties": False}}
             for name in ("list_flash", "list_news", "get_news", "search_flash")]
    def reply(_request, body):
        name = body["params"]["name"]
        if name == "search_flash":
            assert empty_question and body["params"]["arguments"] == {"keyword": "测试公司"}
            return rpc(body, {"structuredContent": {"status": 200,
                "data": {"items": [], "next_cursor": None, "has_more": False}}})
        if name == "get_news":
            assert body["params"]["arguments"] == {"id": "news-002"}
            return rpc(body, {"structuredContent": {"status": 200,
                "data": {"id": "news-002", "url": "https://news.jin10.com/details/news-002",
                         "time": "2026-09-26T07:30:00+08:00", "title": "公告精选",
                         "content": "合集第一项：样品进展。合集第二项：客户测试仍需验证订单。"}}})
        items = ([{"id": "flash-001", "url": "https://flash.jin10.com/detail/flash-001",
                   "time": "2026-09-26T07:00:00+08:00", "title": None,
                   "content": "样品已送达测试客户，订单尚未确认；需要核对公开进展。"}]
                 if name == "list_flash" else
                 [{"id": "news-002", "url": "https://news.jin10.com/details/news-002",
                   "time": "2026-09-26T07:30:00+08:00", "title": "公告精选",
                   "intro": "今日多项公告汇总，主标题没有具体公司。"}] if article else [])
        return rpc(body, {"structuredContent": {"status": 200,
            "data": {"items": items, "next_cursor": None, "has_more": False}}})
    return ProtocolWire(reply=reply, tools=tools)


def _make_evening(tmp_path, monkeypatch, *, research_mode="direct", understand_mode="distinct"):
    db_path = tmp_path / "b92-flash.sqlite"
    run_id, run_rev, exec_id, exec_rev, collection_id, collection_rev = _bindings(db_path)
    _cli("collection-control", "--db", str(db_path), "--state", "open",
         "--config-id", collection_id, "--config-revision", str(collection_rev))
    collection_task = _cli("enqueue-collection", "--db", str(db_path), "--slot", SLOT.isoformat(),
                           "--config-id", collection_id, "--config-revision", str(collection_rev))
    wire = _news_wire(article=True, empty_question=research_mode == "jin10-empty")
    def client_factory(**kwargs):
        return Jin10Client(**kwargs, transport=httpx.MockTransport(wire))
    collected = run_once(db_path=db_path, task_id=collection_task, worker_id="b92-collection",
        lease_for=timedelta(minutes=5), clock=lambda: RUN_AT,
        handlers={"collect_news": create_collection_handler(
            tushare_token=None, jin10_token=JIN10_TOKEN, client_factory=client_factory)},
        require_b76_contract=True)
    assert collected is not None and collected.status == "failed"
    assert [call["params"]["name"] for call in wire.tool_calls] == ["list_flash", "list_news"]

    monkeypatch.setattr(base, "TITLE_COUNT", 2)
    monkeypatch.setattr(base, "DeterministicTransport", _FlashReportTransport)
    monkeypatch.setattr(_FlashReportTransport, "research_mode", research_mode)
    monkeypatch.setattr(_FlashReportTransport, "understand_mode", understand_mode)
    transport, tavily = base.install_offline_transports(
        monkeypatch, refusal_event=None, selected_event_count=2, fixture_run_at=RUN_AT)
    monkeypatch.setenv("JIN10_MCP_TOKEN", JIN10_TOKEN)
    monkeypatch.setattr(pipeline, "Jin10Client", client_factory)
    monkeypatch.setattr(pipeline, "_now", lambda: RUN_AT)
    provider = MeteredProvider(ledger_db=db_path, ledger_task="discovery", api_key="fixture",
        model="deepseek-v4-pro", name="fixture",
        api_url="https://fixture.invalid/v1/chat/completions", read_timeout=1,
        use_streaming=False)
    provider.max_attempts = 1
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro",
                        lambda **_kwargs: ProviderResolution("configured", provider, "fixture", None))
    report_task = _cli("enqueue", "--db", str(db_path), "--kind", "evening",
        "--trading-day", DAY.isoformat(), "--config-id", run_id,
        "--config-revision", str(run_rev), "--execution-config-id", exec_id,
        "--execution-config-revision", str(exec_rev))
    faults = []
    def handler(context):
        try:
            return pipeline.production_scan_handler(context, tushare_token=None,
                parquet_dir=tmp_path / "parquet", now=lambda: RUN_AT)
        except Exception as exc:
            faults.append(f"{type(exc).__name__}: {exc}")
            raise
    result = run_once(db_path=db_path, task_id=report_task, worker_id="b92-report",
        lease_for=timedelta(minutes=5), clock=lambda: RUN_AT,
        handlers={"evening_scan": handler}, require_b76_contract=True)
    assert result is not None and result.status == "completed", faults
    assert [call["params"]["name"] for call in wire.tool_calls[:3]] == [
        "list_flash", "list_news", "get_news"]
    if research_mode == "direct":
        assert tavily.queries == []
    assert any(name == "understand" for name, _ in transport.calls)
    task_input = store.task_execution_input(task_id=report_task, db_path=db_path)
    scan = store.get_scan(scan_id=task_input["checkpoint"]["scanId"], db_path=db_path)
    frozen_refs = scan["coverage"]["collectedInput"]["inputDocumentRefs"]
    parent = next(ref for ref in frozen_refs if ref["sourceKey"] == "jin10-news")
    body_refs = scan["coverage"]["supplementalBodyRefs"]
    assert len(body_refs) == 1 and body_refs[0] == {
        "documentId": parent["documentId"], "revision": parent["revision"] + 1}
    binding = store.selected_body_binding(task_id=report_task,
        parent_document_id=parent["documentId"], parent_revision=parent["revision"], db_path=db_path)
    assert binding["bodyRef"] == body_refs[0]
    terminal = scan["coverage"]["collectedInputConsumption"]["terminalRefs"]
    assert {"documentId": parent["documentId"], "revision": parent["revision"]} in terminal
    assert body_refs[0] in terminal, "decided full body must not re-enter the next report"
    with base.actual_api(db_path, config_id=run_id, config_revision=run_rev,
                         execution_id=exec_id, execution_revision=exec_rev) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=evening")
        assert response.status_code == 200
        report = response.json()["report"]
        flash_ref = next(ref for ref in frozen_refs if ref["sourceKey"] == "jin10-flash")
        flash_document = client.get(f"/api/v1/k10/documents/{flash_ref['documentId']}?revision=1")
        body_document = client.get(f"/api/v1/k10/documents/{body_refs[0]['documentId']}?revision=2")
        assert flash_document.status_code == body_document.status_code == 200
        flash_document, body_document = flash_document.json(), body_document.json()
        materials = (client.get(f"/api/v1/k10/v2/reports/{report['reportId']}/materials").json()
                     if research_mode == "jin10-empty" else None)
    if research_mode == "jin10-empty":
        assert not report["eveningCards"]
        assert [call["params"]["name"] for call in wire.tool_calls].count("search_flash") == 1
        links = store.task_execution_input(task_id=report_task, db_path=db_path)["checkpoint"]["toolQuestionLinks"]
        assert any({link["target"] for link in rows} == {"event-000:q-1", "event-001:q-1"}
                   for rows in links.values()), "one exact paid wire must audit both distinct questions"
        assert tavily.queries == []
        assert report["delivery"]["counts"]["eventProcessed"] == 2
        snapshots = [read_research_snapshot(snapshot_id=key, db_path=db_path)
                     for key in scan["coverage"]["researchSnapshotIds"]]
        assert all(item is not None and item.research_status == "pending_verification"
                   and item.execution_status == "ok" for item in snapshots)
        assert materials is not None and len(materials["items"]) == 2
        assert all(any("补查没有带来新的可见资料" in uncertainty
                       for uncertainty in item["uncertainties"])
                   for item in materials["items"])
        return {"dbPath": db_path, "scan": scan, "report": report, "wire": wire,
                "transport": transport, "tavily": tavily}
    if research_mode == "tavily":
        assert report["eveningCards"] and scan["coverage"]["discoveryIssues"] == []
        assert all(ref["sourceKey"] != "tavily" or ref["documentId"]
                   for card in report["eveningCards"] for ref in card["sourceRefs"])
        return {"dbPath": db_path, "scan": scan, "report": report, "wire": wire,
                "transport": transport, "tavily": tavily}
    if understand_mode == "merge":
        assert scan["coverage"]["discoveryIssues"] == []
        assert len(scan["coverage"]["discoveryDraft"]["events"]) == 1
        assert len(report["eveningCards"]) == 1
        refs = report["eveningCards"][0]["sourceRefs"]
        assert {ref["sourceKey"] for ref in refs} == {"jin10-flash", "jin10-news"}
        assert {(ref["documentId"], ref["revision"]) for ref in refs} >= {
            (flash_ref["documentId"], flash_ref["revision"]),
            (parent["documentId"], parent["revision"]),
            (body_refs[0]["documentId"], body_refs[0]["revision"]),
        }
        return {"dbPath": db_path, "scan": scan, "report": report}
    assert report["eveningCards"]
    assert sum(len(card["catalysts"]) for card in report["eveningCards"]) == 2
    refs = [ref for card in report["eveningCards"] for ref in card["sourceRefs"]]
    assert any(ref["sourceKey"] == "jin10-flash" and ref["title"] is None
               for ref in refs)
    assert any(ref["sourceKey"] == "jin10-news" and ref["revision"] == 2
               for ref in refs)
    assert any(ref["documentId"] == parent["documentId"] and ref["revision"] == 1
               for ref in refs), "selected directory and exact body must both remain visible"
    assert flash_document["originalTitle"] is None and flash_document["sourceKind"] == "flash"
    assert flash_document["contentKind"] == "original" and flash_document["eventTime"] is None
    assert body_document["originalTitle"] == "公告精选" and body_document["sourceKind"] == "article"
    assert body_document["contentKind"] == "original"
    return {"dbPath": db_path, "bindings": (run_id, run_rev, exec_id, exec_rev,
            collection_id, collection_rev), "eveningTaskId": report_task,
            "eveningReportId": report["reportId"], "flashDocumentRef": flash_ref,
            "collectionDocumentRef": body_refs[0], "expectedReasonCount": 2,
            "wire": wire, "transport": transport, "tavily": tavily}


def test_no_title_flash_is_reported_from_real_collection_and_zero_tavily(tmp_path, monkeypatch):
    _make_evening(tmp_path, monkeypatch)


def test_b92_critical_question_can_use_tavily_after_local_evidence(tmp_path, monkeypatch):
    result = _make_evening(tmp_path, monkeypatch, research_mode="tavily")
    assert len(result["tavily"].queries) == 2
    assert all(query.startswith("离线验收 event-") for query in result["tavily"].queries)


def test_b92_empty_jin10_question_stops_unknown_without_query_churn(tmp_path, monkeypatch):
    result = _make_evening(tmp_path, monkeypatch, research_mode="jin10-empty")
    assert result["scan"]["coverage"]["discoveryIssues"] == []
    assert result["report"]["discovery"]["outcome"] == "not_completed"


def test_b92_cross_source_same_matter_merges_but_keeps_both_originals(tmp_path, monkeypatch):
    _make_evening(tmp_path, monkeypatch, understand_mode="merge")


def test_b92_real_order_progress_creates_new_stage_instead_of_duplicate(tmp_path, monkeypatch):
    from tests.test_b92_input_consumption import _collect, _next_evening

    first = _make_evening(tmp_path, monkeypatch)
    run_id, run_rev, exec_id, exec_rev, collection_id, collection_rev = first["bindings"]
    with base.actual_api(first["dbPath"], config_id=run_id, config_revision=run_rev,
                         execution_id=exec_id, execution_revision=exec_rev) as client:
        first_report = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()["report"]
    old_flash_id = first["flashDocumentRef"]["documentId"]
    old_catalyst = next(catalyst for card in first_report["eveningCards"]
                        for catalyst in card["catalysts"]
                        if any(ref["documentId"] == old_flash_id for ref in catalyst["sourceRefs"]))
    new_refs = _collect(first["dbPath"], collection_id, collection_rev,
        slot="2026-09-27T08:00:00+08:00", published_at="2026-09-27T07:00:00+08:00",
        content="公司公告确认此前送样客户已签署正式订单；属于新的实质进展。")
    assert len(new_refs) == 1
    monkeypatch.setattr(_FlashReportTransport, "progress_document_ids", {new_refs[0]["documentId"]})
    monkeypatch.setattr(_FlashReportTransport, "progress_parent_opportunity_id", old_catalyst["opportunityId"])
    next_at = datetime(2026, 9, 27, 22, tzinfo=SHANGHAI)
    monkeypatch.setattr(pipeline, "_now", lambda: next_at)
    # The first fixture seeds only three calendar dates. A second publication
    # needs its own official D2 coverage before lifecycle identity is assigned.
    with sqlite3.connect(first["dbPath"]) as connection:
        connection.executemany(
            "INSERT OR IGNORE INTO trade_cal(exchange,cal_date,is_open) VALUES ('SSE', ?, 1)",
            [("20260929",), ("20260930",)],
        )
    scan = _next_evening(first, tmp_path, monkeypatch)
    assert scan["coverage"]["discoveryIssues"] == []
    assert ("understand:real-progress", new_refs[0]["documentId"]) in first["transport"].calls
    with base.actual_api(first["dbPath"], config_id=run_id, config_revision=run_rev,
                         execution_id=exec_id, execution_revision=exec_rev) as client:
        report = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()["report"]
    progress = [catalyst for card in report["eveningCards"] for catalyst in card["catalysts"]
                if any(ref["documentId"] == new_refs[0]["documentId"] for ref in catalyst["sourceRefs"])]
    assert len(progress) == 1
    assert progress[0]["classification"] == "material_stage"
    assert progress[0]["opportunityId"] != old_catalyst["opportunityId"]


def _morning_wire(*, include_late_old=False, material_contrary=False):
    tools = [{"name": name, "inputSchema": {"type": "object", "properties":
              {"cursor": {"type": "string"}}, "additionalProperties": False}}
             for name in ("list_flash", "list_news", "get_news")]
    def reply(_request, body):
        items = ([{"id": "flash-morning-002", "url": "https://flash.jin10.com/detail/flash-morning-002",
                   "time": "2026-09-27T07:30:00+08:00", "title": None,
                   "content": ("客户终止测试，前晚送样理由面临新的实质反证。" if material_contrary else
                               "客户测试继续进行，订单仍未得到公司确认；与前晚两个理由有关。")},
                  *([{"id": "flash-late-old-003", "url": "https://flash.jin10.com/detail/flash-late-old-003",
                      "time": "2026-09-20T07:00:00+08:00", "title": None,
                      "content": "较早的项目送样消息，今日才补入采集；应由晚报判断当前价值。"}]
                    if include_late_old else [])]
                 if body["params"]["name"] == "list_flash" else [])
        return rpc(body, {"structuredContent": {"status": 200,
            "data": {"items": items, "next_cursor": None, "has_more": False}}})
    return ProtocolWire(reply=reply, tools=tools)


def generate_b92_loopback(root: Path, monkeypatch, *, include_late_old=False,
                          material_contrary=False):
    """Real collection and report producers for an evening plus next morning API."""
    root.mkdir(parents=True, exist_ok=True)
    evening = _make_evening(root, monkeypatch)
    db_path = evening["dbPath"]
    run_id, run_rev, exec_id, exec_rev, collection_id, collection_rev = evening["bindings"]
    morning_at = datetime(2026, 9, 27, 8, 35, tzinfo=SHANGHAI)
    morning_slot = datetime(2026, 9, 27, 8, 0, tzinfo=SHANGHAI)
    collection_task = enqueue_collection(db_path=db_path, slot=morning_slot,
        config_id=collection_id, config_revision=collection_rev, now=morning_at)
    assert isinstance(collection_task, str)
    wire = _morning_wire(include_late_old=include_late_old,
                         material_contrary=material_contrary)
    def client_factory(**kwargs):
        return Jin10Client(**kwargs, transport=httpx.MockTransport(wire))
    collected = run_once(db_path=db_path, task_id=collection_task, worker_id="b92-morning-collection",
        lease_for=timedelta(minutes=5), clock=lambda: morning_at,
        handlers={"collect_news": create_collection_handler(
            tushare_token=None, jin10_token=JIN10_TOKEN, client_factory=client_factory)},
        require_b76_contract=True)
    assert collected is not None and collected.status == "failed"
    assert [row["params"]["name"] for row in wire.tool_calls] == ["list_flash", "list_news"]
    collected_checkpoint = store.task_execution_input(task_id=collection_task, db_path=db_path)["checkpoint"]
    morning_document_ids = {row["documentId"] for row in
                            collected_checkpoint["sources"]["jin10-flash"]["documentRefs"]}
    assert len(morning_document_ids) == (2 if include_late_old else 1)
    monkeypatch.setattr(pipeline, "Jin10Client", client_factory)
    monkeypatch.setattr(pipeline, "_now", lambda: morning_at)
    monkeypatch.setattr(_FlashReportTransport, "review_packets", [])
    monkeypatch.setattr(_FlashReportTransport, "morning_document_ids", morning_document_ids)
    monkeypatch.setattr(_FlashReportTransport, "morning_material_contrary", material_contrary)
    monkeypatch.setattr(_FlashReportTransport, "loopback_morning_discovery_zero", True)
    provider = MeteredProvider(ledger_db=db_path, ledger_task="discovery", api_key="fixture",
        model="deepseek-v4-pro", name="fixture",
        api_url="https://fixture.invalid/v1/chat/completions", read_timeout=1,
        use_streaming=False)
    provider.max_attempts = 1
    resolver = lambda **_kwargs: ProviderResolution("configured", provider, "fixture", None)
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", resolver)
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", resolver)
    report_task = _cli("enqueue", "--db", str(db_path), "--kind", "morning",
        "--trading-day", "2026-09-27", "--config-id", run_id,
        "--config-revision", str(run_rev), "--execution-config-id", exec_id,
        "--execution-config-revision", str(exec_rev))
    # Report producer freezes once, before either morning channel starts.
    # The reference is obtained from that immutable input, not inferred from
    # the latest document revision or inserted into a report row.
    def handler(context):
        return pipeline.production_scan_handler(context, tushare_token=None,
            parquet_dir=root / "parquet", now=lambda: morning_at)
    result = run_once(db_path=db_path, task_id=report_task, worker_id="b92-morning-report",
        lease_for=timedelta(minutes=5), clock=lambda: morning_at,
        handlers={"morning_scan": handler}, require_b76_contract=True)
    assert result is not None and result.status == "completed"
    task_input = store.task_execution_input(task_id=report_task, db_path=db_path)
    scan_id = task_input["checkpoint"]["scanId"]
    scan = store.get_scan(scan_id=scan_id, db_path=db_path)
    assert scan["coverage"]["discoveryIssues"] == []
    assert scan["coverage"]["discoveryState"] == "completed"
    frozen_refs = scan["coverage"]["collectedInput"]["inputDocumentRefs"]
    morning_ref = next(ref for ref in frozen_refs if ref["sourceKey"] == "jin10-flash"
                       and datetime.fromisoformat(ref["publishedAt"]).astimezone(SHANGHAI).date()
                       == date(2026, 9, 27))
    with base.actual_api(db_path, config_id=run_id, config_revision=run_rev,
                         execution_id=exec_id, execution_revision=exec_rev) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=morning")
        assert response.status_code == 200
        morning = response.json()["report"]
    assert morning["reportId"] and morning["morningReview"]["items"]
    assert all(item["status"] == "completed" for item in morning["morningReview"]["items"])
    assert all(item["outcome"] == ("changed" if material_contrary else "uncertain")
               and "无需额外外搜" in item["checkedScope"]
               for item in morning["morningReview"]["items"])
    assert all(gap["reasonCode"] != "independent_verification_pending"
               for gap in morning["delivery"]["gaps"])
    if material_contrary:
        assert len(morning["lifecycleUpdates"]) == 1
        assert morning["lifecycleUpdates"][0]["kind"] == "risk"
    else:
        assert morning["lifecycleUpdates"] == [], "incomplete coverage alone is not a new risk fact"
        assert "needs_review_items" not in morning["coverageGaps"]
    assert any(any(doc["documentId"] == morning_ref["documentId"]
                   for doc in packet["morningDocuments"])
               for packet in _FlashReportTransport.review_packets)
    assert morning["discovery"]["outcome"] == "not_completed"
    return {**{key: value for key, value in evening.items() if key not in {"wire", "transport", "tavily"}},
            "dbPath": str(db_path), "morningTaskId": report_task,
            "morningReportId": morning["reportId"], "morningDocumentRef": morning_ref,
            "expectedSourceCoverageState": "partial",
            "bindings": {"config_id": run_id, "config_revision": run_rev,
                         "execution_id": exec_id, "execution_revision": exec_rev},
            "morningReport": morning, "reviewPackets": _FlashReportTransport.review_packets,
            "tavilyQueries": list(evening["tavily"].queries)}


def test_b92_two_channel_morning_reads_saved_flash_and_all_parent_reasons(tmp_path, monkeypatch):
    generated = generate_b92_loopback(tmp_path, monkeypatch)
    assert generated["reviewPackets"]
    assert all(len(packet["parentReasons"]) == 2 for packet in generated["reviewPackets"])
    assert generated["tavilyQueries"] == []


def test_b92_material_contrary_fact_still_creates_targeted_risk(tmp_path, monkeypatch):
    generated = generate_b92_loopback(tmp_path, monkeypatch, material_contrary=True)
    report = generated["morningReport"]
    assert len(report["lifecycleUpdates"]) == 1
    assert "客户终止测试" in report["lifecycleUpdates"][0]["reason"]


def test_b92_late_old_morning_collection_cannot_widen_new_discovery_window(tmp_path, monkeypatch):
    generated = generate_b92_loopback(tmp_path, monkeypatch, include_late_old=True)
    task = store.task_execution_input(task_id=generated["morningTaskId"],
                                      db_path=Path(generated["dbPath"]))
    scan = store.get_scan(scan_id=task["checkpoint"]["scanId"],
                          db_path=Path(generated["dbPath"]))
    frozen = scan["coverage"]["collectedInput"]["inputDocumentRefs"]
    old = [ref for ref in frozen if datetime.fromisoformat(ref["publishedAt"])
           .astimezone(SHANGHAI).date() == date(2026, 9, 20)]
    assert len(old) == 1, "late original must remain durably frozen for the next evening"
    assert all(ref["documentId"] != old[0]["documentId"]
               for ref in scan["coverage"]["titleInputManifest"]), (
        "the morning new-discovery channel must retain its strict overnight window")
    assert scan["coverage"]["documentCounts"]["input"] == 1
    assert scan["coverage"]["discoveryIssues"] == []
