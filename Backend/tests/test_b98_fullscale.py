"""B98 current collection producer, incident-scale continuation acceptance."""
import json
import sqlite3
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from neckline.k10 import pipeline, store
from neckline.k10.jin10_mcp import Jin10Client
from neckline.k10.collection_runtime import create_collection_handler
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from neckline.k10.windows import SHANGHAI
from tests import v340_acceptance_fixture as base
from tests.test_b92_report_loopback import _bindings, _cli, _news_wire, JIN10_TOKEN
from tests.test_b92_mcp_protocol import ProtocolWire, rpc
from tests.test_v363_flow import _ScaleTransport as OldScaleTransport


class _ScaleTransport(OldScaleTransport):
    def respond(self, request):
        packet = self._packet(request)
        if isinstance(packet.get("documentId"), str) and packet["documentId"] not in self.document_numbers:
            # Direct flash number comes only from the actual source text passed
            # to the deterministic model, never a repaired execution binding.
            text = packet.get("text", "")
            assert "离线验收正文 00000" in text
            self.document_numbers[packet["documentId"]] = 0
        response = super().respond(request)
        if "inputCount" in packet:
            value = json.loads(response.json()["choices"][0]["message"]["content"])
            value["selected"].append({"i": 999999, "reason": "陌生引用不能成为事实"})
            value["notSelected"] = "模型冗余记账错误不得否决有效选择"
            value["selectedCount"] = -1
            return self._ok(value)
        return response


def test_b92_collected_14469_inputs_2099_events_continue_to_readable_partial(tmp_path, monkeypatch):
    """The real B92 CLI/worker must finish a large frozen report across 110s slices."""
    db = tmp_path / "b92-fullscale.sqlite"
    run_id, run_rev, exec_id, exec_rev, collection_id, collection_rev = _bindings(db)
    at = datetime(2026, 9, 26, 22, tzinfo=SHANGHAI)
    slot = datetime(2026, 9, 26, 8, tzinfo=SHANGHAI)
    _cli("collection-control", "--db", str(db), "--state", "open",
         "--config-id", collection_id, "--config-revision", str(collection_rev))
    collection_task = _cli("enqueue-collection", "--db", str(db), "--slot", slot.isoformat(),
         "--config-id", collection_id, "--config-revision", str(collection_rev))
    original = _news_wire()
    pages = []
    def reply(request, body):
        if body["params"]["name"] != "list_flash":
            return original.reply(request, body)
        cursor = body["params"]["arguments"].get("cursor")
        first = cursor is None
        assert cursor in (None, "scale-page-two")
        pages.append(cursor)
        indices = range(8000) if first else range(8000, 14469)
        rows = [{"id": f"b98-scale-{index:05d}",
                 "url": f"https://flash.jin10.com/detail/b98-scale-{index:05d}",
                 "time": "2026-09-26T07:00:00+08:00",
                 "title": None if index == 0 else f"离线验收标题 {index:05d}：新增经营事件",
                 "content": f"离线验收正文 {index:05d}：公司事件需要基于公开资料核验。"}
                for index in indices]
        if first:
            rows.append({"id": "blank-rejected", "time": "2026-09-26T07:00:00+08:00",
                         "title": None, "content": "  \n"})
        return rpc(body, {"structuredContent": {"status": 200,
            "data": {"items": rows, "next_cursor": "scale-page-two" if first else None,
                     "has_more": first}}})
    wire = ProtocolWire(reply=reply, tools=original.tools)
    def client_factory(**kwargs):
        return Jin10Client(**kwargs, transport=httpx.MockTransport(wire))
    collected = run_once(db_path=db, task_id=collection_task, worker_id="b98-scale-collection",
        lease_for=timedelta(minutes=5), clock=lambda: at,
        handlers={"collect_news": create_collection_handler(tushare_token=None,
            jin10_token=JIN10_TOKEN, client_factory=client_factory)}, require_b76_contract=True)
    assert collected.status == "failed"  # TuShare missing and a source item gap.
    assert pages == [None, "scale-page-two"]
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_source_document_versions").fetchone()[0] == 14469
    from collections import Counter
    from neckline.k10 import research_store
    repeated_reads = Counter()
    completed_read_rows = Counter()
    completed_write_attempts = Counter()
    repeated_writes = Counter()
    read_keys = set()
    write_keys = set()
    actual_read = store.completed_execution_items
    def read_completed(**kwargs):
        rows = actual_read(**kwargs)
        for row in rows:
            key = (kwargs["task_id"], kwargs["item_kind"], row["itemKey"], kwargs["stage"])
            completed_read_rows[kwargs["stage"]] += 1
            if key in read_keys:
                repeated_reads[kwargs["stage"]] += 1
            read_keys.add(key)
        return rows
    actual_write = store.record_execution_checkpoint
    def write_completed(**kwargs):
        value = actual_write(**kwargs)
        if kwargs["status"] == "completed":
            key = (kwargs["task_id"], kwargs["item_kind"], kwargs["item_key"], kwargs["stage"])
            completed_write_attempts[kwargs["stage"]] += 1
            if key in write_keys:
                repeated_writes[kwargs["stage"]] += 1
            write_keys.add(key)
        return value
    actual_facts = research_store.task_research_facts
    def completed_facts(**kwargs):
        rows = actual_facts(**kwargs)
        for key in rows:
            identity = (kwargs["task_id"], "research_fact", key, "research_fact")
            completed_read_rows["research_fact"] += 1
            if identity in read_keys:
                repeated_reads["research_fact"] += 1
            read_keys.add(identity)
        return rows
    monkeypatch.setattr(store, "completed_execution_items", read_completed)
    monkeypatch.setattr(store, "record_execution_checkpoint", write_completed)
    monkeypatch.setattr(research_store, "task_research_facts", completed_facts)
    monkeypatch.setattr(base, "TITLE_COUNT", 14469)
    monkeypatch.setattr(base, "DeterministicTransport", _ScaleTransport)
    transport, tavily = base.install_offline_transports(
        monkeypatch, refusal_event=None, selected_event_count=525,
        all_events_same_company=False, fixture_run_at=at)
    provider = MeteredProvider(ledger_db=db, ledger_task="discovery", api_key="fixture",
        model="deepseek-v4-pro", name="fixture",
        api_url="https://fixture.invalid/v1/chat/completions", read_timeout=1,
        use_streaming=False)
    provider.max_attempts = 1
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro",
                        lambda **_kwargs: ProviderResolution("configured", provider, "fixture", None))
    task_id = _cli("enqueue", "--db", str(db), "--kind", "evening",
        "--trading-day", "2026-09-26", "--config-id", run_id,
        "--config-revision", str(run_rev), "--execution-config-id", exec_id,
        "--execution-config-revision", str(exec_rev))
    assert task_id.startswith("task_")
    source_binding = store.task_execution_input(task_id=task_id, db_path=db)
    assert source_binding is not None
    policy = source_binding["executionProfile"]["payload"]["discovery"]
    assert policy["reportInputContract"] == "k10-collected-input-3.6.1-b92"
    assert policy["investigationPromptContractRevision"] == "k10-research-3.6.1-b92"
    first_scan_id = None
    stages = []
    failures = []
    pass_elapsed = []
    progress_by_pass = []
    def handler(context):
        # This substitutes only elapsed work time. The production policy still
        # supplies its unmodified 110-second slice budget and concurrency six.
        ticks = [0]
        started = time.monotonic()
        def monotonic():
            ticks[0] += 1
            return max(ticks[0] * 0.04, time.monotonic() - started)
        with monkeypatch.context() as local:
            local.setattr(pipeline, "time", SimpleNamespace(monotonic=monotonic))
            try:
                return pipeline.production_scan_handler(
                    context, tushare_token=None, parquet_dir=tmp_path / "parquet",
                    now=lambda: at)
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
                raise
            finally:
                pass_elapsed.append(round(time.monotonic() - started, 2))
    for attempt in range(45):
        result = run_once(db_path=db, task_id=task_id, worker_id="b98-collected-fullscale",
            lease_for=timedelta(minutes=5), clock=lambda: datetime.now(SHANGHAI),
            handlers={"evening_scan": handler}, require_b76_contract=True)
        assert result is not None, failures
        frozen = store.task_execution_input(task_id=task_id, db_path=db)
        assert frozen is not None and frozen["inputVersion"] == source_binding["inputVersion"]
        checkpoint = frozen["checkpoint"]
        scan_id = checkpoint.get("scanId")
        if first_scan_id is None:
            first_scan_id = scan_id
        assert scan_id == first_scan_id
        progress = checkpoint.get("executionProgress") or {}
        stages.append(progress.get("phase"))
        progress_by_pass.append({"phase": progress.get("phase"),
                                 "counts": progress.get("counts"),
                                 "lastActualChangeAt": progress.get("lastActualChangeAt"),
                                 "sliceDelta": progress.get("sliceDelta"),
                                 "completedReadRows": dict(completed_read_rows),
                                 "repeatedReadRows": dict(repeated_reads),
                                 "completedWriteAttempts": dict(completed_write_attempts),
                                 "repeatedCompletedWrites": dict(repeated_writes)})
        if result.status != "queued":
            break
    else:
        pytest.fail(f"14469/2099 task did not terminalize in 45 real worker passes: {stages[-6:]}, {failures[-2:]}, {pass_elapsed[-6:]}")
    assert result.status == "completed", (stages, failures, pass_elapsed)
    assert len(stages) > 2
    assert all(any(value > 0 for value in item.get("sliceDelta", {}).values())
               for item in progress_by_pass[:-1]), "a continuation had zero durable progress"
    assert max(pass_elapsed) <= 125, "one noninterruptible phase exceeded the frozen 110-second slice"
    scan = store.get_scan(scan_id=first_scan_id, db_path=db)
    assert scan is not None
    counts = scan["coverage"]["titleDispositionCounts"]
    assert counts == {"input": 14469, "processed": 14469, "failed": 0, "unprocessed": 0}
    assert len(scan["coverage"]["researchInputUnitIds"]) == 2099
    research_wires = [event for kind, event in transport.calls if kind == "research:research_round"]
    assert len(research_wires) == len(set(research_wires)) == 2099
    assert len(set(transport.rank_input_codes)) == len(transport.rank_input_codes) == 40
    assert tavily.queries == []
    assert len(scan["coverage"]["rankingInput"]["companies"]) == 40
    with base.actual_api(db, config_id=run_id, config_revision=run_rev,
                         execution_id=exec_id, execution_revision=exec_rev) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=evening")
        assert response.status_code == 200
        report = response.json()["report"]
        cursor = None
        material_count = 0
        original_refs = set()
        while True:
            params = {"limit": 100, **({"cursor": cursor} if cursor is not None else {})}
            material = client.get(f"/api/v1/k10/v2/reports/{report['reportId']}/materials",
                                  params=params)
            assert material.status_code == 200
            payload = material.json()
            assert payload["items"]
            material_count += len(payload["items"])
            for item in payload["items"]:
                refs = list(item.get("sourceRefs", ()))
                for fact in item.get("facts", ()):
                    refs.extend(fact.get("sourceRefs", ()))
                for relation in item.get("companyRelations", ()):
                    refs.extend(relation.get("sourceRefs", ()))
                original_refs.update((ref["documentId"], ref["revision"]) for ref in refs)
            cursor = payload["page"]["nextCursor"]
            if cursor is None:
                break
        assert material_count == 2099 and len(original_refs) == 525
        for document_id, revision in original_refs:
            original = client.get(f"/api/v1/k10/documents/{document_id}",
                                  params={"revision": revision, "limit": 24000})
            assert original.status_code == 200
            assert original.json()["contentKind"] == "original" and original.json()["body"]
            assert original.json()["page"]["nextCursor"] is None
    assert report["status"] == "partial" and report["eveningCards"]
    assert any(g["stage"] == "title_triage" and g["reasonCode"] == "title_reconcile_partial"
               and g["sourceRefs"] == [] and not g["companyScopeKnown"]
               for g in report["delivery"]["gaps"])
    assert report["delivery"]["counts"]["eventInput"] == 2099
    assert report["delivery"]["counts"]["comparableCompanies"] == 30
    assert report["delivery"]["counts"]["publishedCompanies"] == 30
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA application_id").fetchone()[0] == 1313552690
        durable_stages = dict(conn.execute(
            "SELECT stage,COUNT(*) FROM k10_execution_item_checkpoints GROUP BY stage"))
        coverage_bytes = conn.execute(
            "SELECT LENGTH(coverage_json) FROM k10_scans WHERE scan_id=?", (first_scan_id,)
        ).fetchone()[0]
        db_bytes = conn.execute("PRAGMA page_count").fetchone()[0] * conn.execute(
            "PRAGMA page_size").fetchone()[0]
    print("B98_SCALE_SUMMARY " + json.dumps({
        "passes": len(stages), "wallSecondsByPass": pass_elapsed,
        "stageByPass": stages, "progressByPass": progress_by_pass,
        "durableStages": durable_stages,
        "completedReadRows": dict(completed_read_rows), "repeatedReadRows": dict(repeated_reads),
        "completedWriteAttempts": dict(completed_write_attempts), "repeatedCompletedWrites": dict(repeated_writes), "coverageBytes": coverage_bytes,
        "sqliteBytes": db_bytes, "materialCount": material_count,
        "originalRefCount": len(original_refs), "rankInputCount": len(transport.rank_input_codes),
        "researchWireCount": len(research_wires), "reportStatus": report["status"],
    }, ensure_ascii=False, separators=(",", ":")))
