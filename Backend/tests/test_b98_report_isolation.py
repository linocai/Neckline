"""Current B92 producer/worker regressions for the Oct 8 review findings."""
import json
import sqlite3

import httpx
import pytest

from neckline.k10 import pipeline, store
from tests import test_b92_report_loopback as current
from tests import v340_acceptance_fixture as base


def read_case(root, window="evening"):
    db = root / "b92-flash.sqlite"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        task = conn.execute("SELECT task_id,status FROM k10_tasks WHERE kind=?",
                            (window + "_scan",)).fetchone()
        assert conn.execute("SELECT count(*) FROM k10_external_attempts WHERE state IN ('started','unknown')").fetchone()[0] == 0
    execution = store.task_execution_input(task_id=task[0], db_path=db)
    policy = execution["executionProfile"]["payload"]["discovery"]
    assert policy["reportInputContract"] == "k10-collected-input-3.6.1-b92"
    assert policy["investigationPromptContractRevision"] == "k10-research-3.6.1-b92"
    with base.actual_api(db, config_id="b92-isolated-run", config_revision=1,
                         execution_id="b92-isolated-execution", execution_revision=1) as client:
        envelope = client.get(f"/api/v1/k10/v2/reports/latest?window={window}")
        assert envelope.status_code == 200
        report = envelope.json()["report"]
        materials = client.get(f"/api/v1/k10/v2/reports/{report['reportId']}/materials")
        assert materials.status_code == 200
    return db, task, report, materials.json()


def run_evening(root, monkeypatch):
    # The original normal fixture has completeness assertions. Faulted cases
    # assert their real persisted/API outcome separately, without repairing
    # producer-created bindings, scan identity, or execution checkpoints.
    try:
        current._make_evening(root, monkeypatch)
    except AssertionError:
        pass
    return read_case(root)


def test_b92_blank_flash_does_not_abort_independent_work(tmp_path, monkeypatch):
    original = current._news_wire
    def factory(**kwargs):
        wire = original(**kwargs)
        reply = wire.reply
        def respond(request, body):
            response = reply(request, body)
            if body["params"]["name"] == "list_flash":
                value = response.json()
                value["result"]["structuredContent"]["data"]["items"].append({
                    "id": "whitespace-only", "url": "https://flash.jin10.com/detail/whitespace-only",
                    "time": "2026-09-26T07:01:00+08:00", "title": None, "content": "  \n"})
                return httpx.Response(200, json=value)
            return response
        wire.reply = respond
        return wire
    monkeypatch.setattr(current, "_news_wire", factory)
    _, task, report, materials = run_evening(tmp_path, monkeypatch)
    assert task[1] == "completed"
    assert report["status"] == "partial" and report["eveningCards"]
    assert materials["items"]


@pytest.mark.parametrize("mode", ["bad_sibling", "all_foreign", "missing", "empty"])
def test_b92_sort_fault_keeps_readable_report_and_completed_materials(tmp_path, monkeypatch, mode):
    original = current._FlashReportTransport.respond
    def respond(self, request):
        packet = self._packet(request)
        if "companies" in packet and "choices" in packet.get("output", {}):
            response = original(self, request)
            value = json.loads(response.json()["choices"][0]["message"]["content"])
            assert value["choices"]
            if mode == "bad_sibling":
                value["choices"].append({"companyCode": "bad-sibling", "catalystKeys": None})
            elif mode == "all_foreign":
                value["choices"] = [{"companyCode": value["choices"][0]["companyCode"],
                                     "catalystKeys": [{"canonicalKey": "foreign", "stageKey": "new"}]}]
            elif mode == "missing":
                value = {"other": []}
            else:
                value["choices"] = []
            return self._ok(value)
        return original(self, request)
    monkeypatch.setattr(current._FlashReportTransport, "respond", respond)
    _, task, report, materials = run_evening(tmp_path, monkeypatch)
    assert task[1] == "completed"
    assert materials["items"] and report["resultAvailableAt"]
    if mode == "bad_sibling":
        assert report["eveningCards"]
        assert report["delivery"]["rankingScope"] == "completed_subset"
    else:
        assert report["eveningCards"] == []
        if mode != "empty":
            assert report["delivery"]["rankingScope"] == "none"
            assert report["discovery"]["outcome"] == "not_completed"
    if mode != "empty":
        assert any(gap["stage"] == "prioritize" for gap in report["delivery"]["gaps"])


def test_b92_morning_discovery_failure_keeps_completed_reviews(tmp_path, monkeypatch):
    original = pipeline.execute_scan
    def execute(**kwargs):
        if kwargs["kind"] == "morning":
            raise pipeline.PipelineError("isolated model selection fault", code="prioritize_json_contract_invalid")
        return original(**kwargs)
    monkeypatch.setattr(pipeline, "execute_scan", execute)
    try:
        current.generate_b92_loopback(tmp_path, monkeypatch, material_contrary=True)
    except AssertionError:
        pass
    _, task, report, _ = read_case(tmp_path, "morning")
    assert task[1] == "completed" and report["status"] == "partial"
    assert report["morningReview"]["items"]
    assert all(item["status"] == "completed" for item in report["morningReview"]["items"])
    assert report["lifecycleUpdates"]
    assert any(gap["reasonCode"] == "prioritize_json_contract_invalid" for gap in report["delivery"]["gaps"])


def make_collected_case(root, monkeypatch, *, wire=None, transport_type=None, before_report=None, expected_status="completed"):
    """Small real current producer path with ordinary worker continuations."""
    from datetime import datetime, timedelta
    from neckline.k10.collection_runtime import create_collection_handler
    from neckline.k10.jin10_mcp import Jin10Client
    from neckline.k10.metering import MeteredProvider
    from neckline.k10.providers import ProviderResolution
    from neckline.k10.worker import run_once
    db = root / "b92-flash.sqlite"
    run_id, run_rev, exec_id, exec_rev, collection_id, collection_rev = current._bindings(db)
    current._cli("collection-control", "--db", str(db), "--state", "open", "--config-id", collection_id,
                 "--config-revision", str(collection_rev))
    collect = current._cli("enqueue-collection", "--db", str(db), "--slot", current.SLOT.isoformat(),
                          "--config-id", collection_id, "--config-revision", str(collection_rev))
    if wire is None:
        wire = current._news_wire(article=True)
        news_reply = wire.reply
        def titled_reply(request, body):
            response = news_reply(request, body)
            if body["params"]["name"] in {"list_news", "get_news"}:
                value = response.json()
                data = value["result"]["structuredContent"]["data"]
                for row in data.get("items", [data]):
                    row["title"] = "离线验收标题 0000：公司新增经营事实"
                return httpx.Response(200, json=value)
            return response
        wire.reply = titled_reply
    def client_factory(**kwargs):
        return Jin10Client(**kwargs, transport=httpx.MockTransport(wire))
    collected = run_once(db_path=db, task_id=collect, worker_id="b98-collection",
        lease_for=timedelta(minutes=5), clock=lambda: current.RUN_AT,
        handlers={"collect_news": create_collection_handler(tushare_token=None,
            jin10_token=current.JIN10_TOKEN, client_factory=client_factory)}, require_b76_contract=True)
    assert collected.status == "failed"
    if before_report:
        before_report(db)
    monkeypatch.setattr(base, "TITLE_COUNT", 5)
    monkeypatch.setattr(base, "DeterministicTransport", transport_type or current._FlashReportTransport)
    monkeypatch.setattr(current._FlashReportTransport, "research_mode", "direct")
    monkeypatch.setattr(current._FlashReportTransport, "understand_mode", "distinct")
    transport, tavily = base.install_offline_transports(monkeypatch, refusal_event=None,
        selected_event_count=5, fixture_run_at=current.RUN_AT)
    monkeypatch.setenv("JIN10_MCP_TOKEN", current.JIN10_TOKEN)
    monkeypatch.setattr(pipeline, "Jin10Client", client_factory)
    monkeypatch.setattr(pipeline, "_now", lambda: current.RUN_AT)
    provider = MeteredProvider(ledger_db=db, ledger_task="discovery", api_key="fixture",
        model="deepseek-v4-pro", name="fixture", api_url="https://fixture.invalid/v1/chat/completions",
        read_timeout=1, use_streaming=False)
    provider.max_attempts = 1
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro",
                        lambda **_: ProviderResolution("configured", provider, "fixture", None))
    task = current._cli("enqueue", "--db", str(db), "--kind", "evening", "--trading-day", current.DAY.isoformat(),
        "--config-id", run_id, "--config-revision", str(run_rev), "--execution-config-id", exec_id,
        "--execution-config-revision", str(exec_rev))
    results = []
    def handle(context):
        return pipeline.production_scan_handler(context, tushare_token=None, parquet_dir=root / "parquet",
                                                now=lambda: current.RUN_AT)
    for _ in range(8):
        result = run_once(db_path=db, task_id=task, worker_id="b98-report", lease_for=timedelta(minutes=5),
            clock=lambda: datetime.now(current.SHANGHAI), handlers={"evening_scan": handle}, require_b76_contract=True)
        assert result is not None
        results.append(result.status)
        if result.status != "queued":
            break
    assert result.status == expected_status, results
    return (*read_case(root), transport, results)


def test_b92_source_bad_sibling_does_not_drop_next_page(tmp_path, monkeypatch):
    from tests.test_b92_mcp_protocol import ProtocolWire, rpc
    original = current._news_wire(article=True)
    pages = []
    def reply(request, body):
        if body["params"]["name"] != "list_flash":
            return original.reply(request, body)
        cursor = body["params"]["arguments"].get("cursor")
        pages.append(cursor)
        first = cursor is None
        assert cursor in (None, "page-two")
        rows = [{"id": "good-first" if first else "good-next",
                 "url": "https://flash.jin10.com/detail/" + ("first" if first else "next"),
                 "time": "2026-09-26T07:00:00+08:00", "title": None,
                 "content": "样品送样事件待核验。"}]
        if first:
            rows.append({"id": "blank", "url": "https://flash.jin10.com/detail/blank",
                         "time": "2026-09-26T07:00:00+08:00", "title": None, "content": " \n"})
        return rpc(body, {"structuredContent": {"status": 200, "data": {"items": rows,
            "next_cursor": "page-two" if first else None, "has_more": first}}})
    db, _, report, materials, _, _ = make_collected_case(tmp_path, monkeypatch,
        wire=ProtocolWire(reply=reply, tools=original.tools))
    assert pages == [None, "page-two"]
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT count(*) FROM k10_source_documents WHERE source_key='jin10-flash'").fetchone()[0] == 2
        state = json.loads(conn.execute("SELECT checkpoint_json FROM k10_tasks WHERE kind='collect_news'").fetchone()[0])
    source = state["sources"]["jin10-flash"]
    assert source["state"] == "partial"
    assert source["rejectedItems"][0]["reasonCode"] == "flash_content_missing"
    assert report["eveningCards"] and len(materials["items"]) == 3


def test_b92_existing_blank_original_is_a_durable_ref_gap(tmp_path, monkeypatch):
    from hashlib import sha256
    def old_ingestion(db):
        # Simulate a legal old producer's stored original. No execution state
        # or report binding is repaired; the current report freezes this ref.
        store.append_document_version(document_id="old-blank-flash", source_key="jin10-flash",
            external_id="old-blank", canonical_url="https://flash.jin10.com/detail/old-blank",
            content_sha256=sha256(b"  \n").hexdigest(), published_at=current.SLOT.isoformat(),
            published_precision="exact", fetched_at=current.SLOT.isoformat(), original_text="  \n",
            excerpt=None, fetch_version="prior-producer", metadata={"title": None, "sourceKind": "flash"},
            created_at=current.SLOT.isoformat(), db_path=db)
    db, task, report, materials, _, _ = make_collected_case(tmp_path, monkeypatch, before_report=old_ingestion)
    assert task[1] == "completed" and report["eveningCards"] and materials["items"]
    gap = next(g for g in report["delivery"]["gaps"] if g["reasonCode"] == "source_content_empty")
    assert gap["sourceRefs"][0]["documentId"] == "old-blank-flash"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT count(*) FROM k10_execution_item_checkpoints WHERE stage='title_source_gap'").fetchone()[0] == 1


@pytest.mark.parametrize("fault", ["mixed_catalysts", "duplicates", "known_failure", "wire_bookkeeping", "ranking_interruption", "aggregate_failure", "receipt_interruption", "title_gap_interruption", "partial_assembly_failure", "partial_assembly_draft_interruption", "manifest_failure", "admitted_snapshot_missing"])
def test_b92_sort_wire_and_restore_matrix(tmp_path, monkeypatch, fault):
    original = current._FlashReportTransport.respond
    interrupted = []
    from neckline.k10.schema import SqliteWriteBusy
    def respond(self, request):
        packet = self._packet(request)
        result = original(self, request)
        value = json.loads(result.json()["choices"][0]["message"]["content"])
        if "inputCount" in packet and fault in {"wire_bookkeeping", "title_gap_interruption"}:
            value["notSelected"] = [{"bad": "untrusted redundant data"}]
            value["selectedCount"] = -50
            if fault == "title_gap_interruption":
                value["selected"].append({"i": 999999, "reason": "陌生引用"})
            for selected in value["selected"]:
                selected["selectedRank"] = "bad"
            return self._ok(value)
        if "companies" in packet and "choices" in packet.get("output", {}):
            if fault == "mixed_catalysts":
                value["choices"][0]["catalystKeys"].append({"canonicalKey": "foreign", "stageKey": "new"})
            elif fault == "duplicates":
                value["choices"].append(value["choices"][0])
                value["choices"].append({"companyCode": value["choices"][0]["companyCode"], "catalystKeys": "bad"})
            elif fault == "known_failure":
                return self._ok({"bad": "no ordering array"})
            return self._ok(value)
        return result
    monkeypatch.setattr(current._FlashReportTransport, "respond", respond)
    record = store.record_execution_checkpoint
    def checkpoint(**kwargs):
        if fault == "receipt_interruption" and not interrupted and kwargs.get("stage") == "model:titleReconcile":
            interrupted.append(True)
            raise SqliteWriteBusy("receipt committed before derived title result")
        result = record(**kwargs)
        if not interrupted and ((fault == "ranking_interruption" and kwargs.get("stage") == "model:prioritize")
                               or (fault == "aggregate_failure" and kwargs.get("stage") == "discovery_pre_rank")
                               or (fault in {"partial_assembly_failure", "partial_assembly_draft_interruption", "admitted_snapshot_missing"} and kwargs.get("stage") == "discovery_assemble")
                               or (fault == "title_gap_interruption" and kwargs.get("stage") == "title_reconcile_gap")):
            interrupted.append(True)
            if fault == "admitted_snapshot_missing":
                with sqlite3.connect(kwargs["db_path"]) as conn:
                    conn.execute("DELETE FROM k10_research_snapshot_revisions WHERE task_id=?", (kwargs["task_id"],))
            if fault in {"aggregate_failure", "partial_assembly_failure", "partial_assembly_draft_interruption", "admitted_snapshot_missing"}:
                raise pipeline.PipelineError("settled content failure after completed aggregate", code="prioritize_json_contract_invalid")
            raise SqliteWriteBusy("interruption after durable ordering derivative")
        return result
    monkeypatch.setattr(store, "record_execution_checkpoint", checkpoint)
    update = store.update_running_scan_coverage
    draft_interrupted = []
    def coverage_write(**kwargs):
        result = update(**kwargs)
        coverage = kwargs["coverage"]
        if fault == "manifest_failure" and not interrupted and coverage.get("researchInputEvents"):
            interrupted.append(True)
            raise pipeline.PipelineError("content failure before first research admission", code="prioritize_json_contract_invalid")
        if fault == "partial_assembly_draft_interruption" and not draft_interrupted and coverage.get("discoveryChannelFailure"):
            draft_interrupted.append(True)
            raise SqliteWriteBusy("known partial draft durable before interruption")
        return result
    monkeypatch.setattr(store, "update_running_scan_coverage", coverage_write)
    db, _, report, materials, transport, results = make_collected_case(tmp_path, monkeypatch,
        expected_status="failed" if fault == "admitted_snapshot_missing" else "completed")
    if fault == "admitted_snapshot_missing":
        assert report["status"] == "failed" and not report["eveningCards"]
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT count(*) FROM k10_execution_item_checkpoints WHERE stage='research_input_boundary'").fetchone()[0] > 0
        return
    if fault == "manifest_failure":
        assert report["status"] == "partial" and report["delivery"]["rankingScope"] == "none"
        assert report["discovery"]["outcome"] == "not_completed"
        assert any(gap["reasonCode"] == "content_failure_not_admitted" for gap in report["delivery"]["gaps"])
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT count(*) FROM k10_research_snapshot_revisions").fetchone()[0] == 0
        return
    assert materials["items"] and report["resultAvailableAt"]
    if fault in {"known_failure", "aggregate_failure", "partial_assembly_failure", "partial_assembly_draft_interruption"}:
        assert not report["eveningCards"] and report["delivery"]["rankingScope"] == "none"
        assert report["discovery"]["outcome"] == "not_completed"
        assert report["availableAt"] is None
    else:
        assert len(report["eveningCards"]) == 1
        assert sum(len(c["catalysts"]) for c in report["eveningCards"]) == 2
    if fault in {"ranking_interruption", "receipt_interruption", "title_gap_interruption"}:
        assert interrupted and "queued" in results
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        wires = conn.execute("SELECT input_sha256,count(*) FROM k10_external_attempts WHERE task_id=(SELECT task_id FROM k10_tasks WHERE kind='evening_scan') GROUP BY stage,item_key,input_sha256 HAVING count(*)>1").fetchall()
        if fault != "known_failure":
            assert not wires
        assert conn.execute("SELECT count(*) FROM k10_task_notifications WHERE task_id=(SELECT task_id FROM k10_tasks WHERE kind='evening_scan')").fetchone()[0] == 1


def test_b92_known_failure_inside_real_execute_scan_keeps_morning_reviews(tmp_path, monkeypatch):
    from unittest.mock import patch
    original = pipeline.execute_scan
    def execute(**kwargs):
        if kwargs["kind"] == "morning":
            with patch("neckline.k10.pipeline.run_discovery", side_effect=pipeline.PipelineError(
                    "content fault inside current execution", code="prioritize_json_contract_invalid")):
                return original(**kwargs)
        return original(**kwargs)
    monkeypatch.setattr(pipeline, "execute_scan", execute)
    try:
        current.generate_b92_loopback(tmp_path, monkeypatch, material_contrary=True)
    except AssertionError:
        pass
    _, task, report, _ = read_case(tmp_path, "morning")
    assert task[1] == "completed" and report["status"] == "partial"
    assert report["morningReview"]["items"] and report["lifecycleUpdates"]


def test_b92_corrupt_persisted_canonical_cannot_become_wire_gap(tmp_path, monkeypatch):
    from neckline.k10.schema import SqliteWriteBusy
    record = store.record_execution_checkpoint
    corrupted = []
    def checkpoint(**kwargs):
        result = record(**kwargs)
        if kwargs.get("stage") == "model:titleReconcile" and not corrupted:
            corrupted.append(True)
            with sqlite3.connect(kwargs["db_path"]) as conn:
                value = json.loads(conn.execute("SELECT result_json FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:titleReconcile'", (kwargs["task_id"],)).fetchone()[0])
                value["notSelected"] = "bad persisted canonical"
                conn.execute("UPDATE k10_execution_item_checkpoints SET result_json=? WHERE task_id=? AND stage='model:titleReconcile'",
                    (json.dumps(value), kwargs["task_id"]))
            raise SqliteWriteBusy("isolated interruption after canonical corruption")
        return result
    monkeypatch.setattr(store, "record_execution_checkpoint", checkpoint)
    db, task, report, _, transport, results = make_collected_case(tmp_path, monkeypatch, expected_status="failed")
    assert corrupted and "queued" in results
    assert not report["eveningCards"]
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT json_extract(checkpoint_json,'$.safeErrorCode') FROM k10_tasks WHERE task_id=?", (task[0],)).fetchone()[0] == "model_cache_corrupt"
        assert conn.execute("SELECT count(*) FROM k10_execution_item_checkpoints WHERE stage='title_reconcile_gap'").fetchone()[0] == 0
    assert sum(kind == "titleGlobal" for kind, _ in transport.calls) == 1


@pytest.mark.parametrize("failure", ["sort_wire", "review_wire", "atomic_interruption"])
def test_b92_morning_wire_channels_and_atomic_resume(tmp_path, monkeypatch, failure):
    from threading import current_thread
    from neckline.k10.worker import run_once
    from neckline.k10.schema import SqliteWriteBusy
    from datetime import datetime, timedelta
    original = current._FlashReportTransport.respond
    seen = []
    def respond(self, request):
        message = json.loads(request.content)["messages"][-1]["content"]
        if "<untrusted-evidence>" in message and failure == "review_wire":
            seen.append("review-wire")
            return self._ok({"action": "conclude", "material": "bad"})
        packet = self._packet(request) if "<untrusted-k10-evidence>" in message else {}
        if (isinstance(packet.get("documentId"), str)
                and packet["documentId"] in type(self).morning_document_ids):
            ref = {"documentId": packet["documentId"], "revision": packet["revision"]}
            return self._ok({"events": [{"canonicalKey": "event-002", "stageKey": "morning-new",
                "eventState": "rumor", "headline": "独立公司的隔夜新事实", "eventKind": "rumor", "facts": {},
                "sourceRefs": [ref], "claims": [{"text": "独立公司项目送样", "kind": "rumor", "novelty": "new_fact",
                    "speaker": "供应商", "subject": "项目", "object": "样品", "action": "送样",
                    "stageOrCondition": "待确认", "timeText": "隔夜", "verificationStatus": "unverified",
                    "decisionImpact": "影响独立公司判断", "sourceRef": ref, "location": "paragraph:1"}]}],
                "needsFullText": False})
        if ("k10-morning-discovery" in current_thread().name and "companies" in packet
                and "choices" in packet.get("output", {}) and failure == "sort_wire"):
            seen.append("sort-wire")
            return self._ok({"bad": "missing model ordering array"})
        return original(self, request)
    monkeypatch.setattr(current._FlashReportTransport, "respond", respond)
    interrupted = []
    finish = store.finish_task_with_publication
    def finalizer(conn, **kwargs):
        if (failure == "atomic_interruption" and not interrupted
                and conn.execute("SELECT kind FROM k10_tasks WHERE task_id=?", (kwargs["task_id"],)).fetchone()[0] == "morning_scan"):
            interrupted.append(True)
            raise SqliteWriteBusy("morning aggregate before transaction commit")
        return finish(conn, **kwargs)
    monkeypatch.setattr(store, "finish_task_with_publication", finalizer)
    try:
        current.generate_b92_loopback(tmp_path, monkeypatch, material_contrary=True)
    except AssertionError:
        pass
    db = tmp_path / "b92-flash.sqlite"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        task = conn.execute("SELECT task_id,status FROM k10_tasks WHERE kind='morning_scan'").fetchone()
        before_wires = conn.execute("SELECT count(*) FROM k10_external_attempts WHERE task_id=?", (task[0],)).fetchone()[0]
        if failure == "atomic_interruption":
            assert task[1] == "queued"
            assert conn.execute("SELECT count(*) FROM k10_task_notifications WHERE task_id=?", (task[0],)).fetchone()[0] == 0
            assert conn.execute("SELECT status FROM k10_v2_report_runs WHERE window_kind='morning'").fetchone()[0] == "running"
    if failure == "atomic_interruption":
        at = datetime(2026, 9, 27, 8, 35, tzinfo=current.SHANGHAI)
        result = run_once(db_path=db, task_id=task[0], worker_id="b98-morning-resume", lease_for=timedelta(minutes=5),
            clock=lambda: datetime.now(current.SHANGHAI), require_b76_contract=True,
            handlers={"morning_scan": lambda context: pipeline.production_scan_handler(context,
                tushare_token=None, parquet_dir=tmp_path / "parquet", now=lambda: at)})
        assert result.status == "completed"
    _, task, report, materials = read_case(tmp_path, "morning")
    assert task[1] == "completed" and report["resultAvailableAt"]
    if failure == "sort_wire":
        assert "sort-wire" in seen and materials["items"]
        assert report["morningReview"]["items"] and report["lifecycleUpdates"]
        assert not report["addedCards"] and report["discovery"]["outcome"] == "not_completed"
    elif failure == "review_wire":
        assert "review-wire" in seen and report["addedCards"] and materials["items"]
        assert any(item["status"] == "failed" for item in report["morningReview"]["items"])
    else:
        assert interrupted and report["addedCards"] and report["morningReview"]["items"]
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT count(*) FROM k10_task_notifications WHERE task_id=?", (task[0],)).fetchone()[0] == 1
        if failure == "atomic_interruption":
            assert conn.execute("SELECT count(*) FROM k10_external_attempts WHERE task_id=?", (task[0],)).fetchone()[0] == before_wires


def test_b92_morning_manifest_failure_keeps_completed_reviews(tmp_path, monkeypatch):
    """Known failure after exact input freeze precedes the first snapshot."""
    update = store.update_running_scan_coverage
    injected = []
    original = pipeline.run_discovery
    def discovery(**kwargs):
        checkpoint = kwargs.get("research_input_checkpoint")
        def freeze(events):
            checkpoint(events)
            if kwargs["phase"] == "morning" and events:
                injected.append(True)
                raise pipeline.PipelineError("known failure before morning research admission", code="prioritize_json_contract_invalid")
        return original(**{**kwargs, "research_input_checkpoint": freeze})
    monkeypatch.setattr(pipeline, "run_discovery", discovery)
    original_wire = current._FlashReportTransport.respond
    def respond(self, request):
        message = json.loads(request.content)["messages"][-1]["content"]
        packet = self._packet(request) if "<untrusted-k10-evidence>" in message else {}
        if (isinstance(packet.get("documentId"), str)
                and packet["documentId"] in type(self).morning_document_ids):
            ref = {"documentId": packet["documentId"], "revision": packet["revision"]}
            return self._ok({"events": [{"canonicalKey": "event-002", "stageKey": "morning-new",
                "eventState": "rumor", "headline": "独立公司的隔夜新事实", "eventKind": "rumor", "facts": {},
                "sourceRefs": [ref], "claims": [{"text": "独立公司项目送样", "kind": "rumor", "novelty": "new_fact",
                    "speaker": "供应商", "subject": "项目", "object": "样品", "action": "送样",
                    "stageOrCondition": "待确认", "timeText": "隔夜", "verificationStatus": "unverified",
                    "decisionImpact": "影响独立公司判断", "sourceRef": ref, "location": "paragraph:1"}]}],
                "needsFullText": False})
        return original_wire(self, request)
    monkeypatch.setattr(current._FlashReportTransport, "respond", respond)
    try:
        current.generate_b92_loopback(tmp_path, monkeypatch, material_contrary=True)
    except AssertionError:
        pass
    _, task, report, _ = read_case(tmp_path, "morning")
    assert injected and task[1] == "completed" and report["status"] == "partial"
    assert report["morningReview"]["items"] and report["lifecycleUpdates"]
    assert report["availableAt"] is None and report["resultAvailableAt"]
    assert any(gap["reasonCode"] == "content_failure_not_admitted" for gap in report["delivery"]["gaps"])


def test_b92_unranked_partial_consumption_keeps_completed_research_terminal(tmp_path, monkeypatch):
    from tests.test_b92_input_consumption import _next_evening
    original = current._FlashReportTransport.respond
    def respond(self, request):
        packet = self._packet(request)
        if "companies" in packet and "choices" in packet.get("output", {}):
            return self._ok({"bad": "no final ordering"})
        return original(self, request)
    monkeypatch.setattr(current._FlashReportTransport, "respond", respond)
    db, task, report, materials, transport, _ = make_collected_case(tmp_path, monkeypatch)
    assert report["availableAt"] is None and report["resultAvailableAt"] and materials["items"]
    scan = store.get_scan(scan_id=store.task_execution_input(task_id=task[0],db_path=db)["checkpoint"]["scanId"],db_path=db)
    consumed = scan["coverage"]["collectedInputConsumption"]["terminalRefs"]
    assert consumed
    prior_calls = len(transport.calls)
    first = {"dbPath": db, "bindings": ("b92-isolated-run",1,"b92-isolated-execution",1,None,None)}
    later = _next_evening(first, tmp_path, monkeypatch)
    assert later["coverage"]["collectedInput"]["inputDocumentRefs"] == []
    assert len(transport.calls) == prior_calls
