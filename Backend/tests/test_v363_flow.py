"""3.6.3 report-flow regressions from the 29 September production stall."""
from __future__ import annotations

from datetime import datetime, timezone
from datetime import timedelta
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from threading import Event
from types import SimpleNamespace
import tracemalloc
import time

import httpx

import pytest

from neckline.k10.discovery import (
    DiscoveryDocument, DiscoverySliceYield, EventDraft, InvestigationOutcome,
    Verification, run_discovery,
    _event_execution_unit_id,
)
from tests.test_k10_discovery import _configuration, _Model, _Metadata, _verify
from tests import v340_acceptance_fixture as base
from tests.test_k10_execution import _seed as seed_execution
from tests.test_b92_report_loopback import (
    _bindings, _cli, _morning_wire, _FlashReportTransport, JIN10_TOKEN,
)
from tests.test_v350_cli_api import DirectRoundTransport
from tests.test_b92_mcp_protocol import rpc
from neckline.k10 import discovery as discovery_module, pipeline, store
from neckline.k10.cli import enqueue_collection
from neckline.k10.collection_runtime import create_collection_handler
from neckline.k10.jin10_mcp import Jin10Client
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from neckline.k10 import worker as worker_module
from neckline.k10.worker import TaskResult
from neckline.k10.schema import SqliteWriteBusy, initialize_schema
from tests.k10_v306_fixture import append_approved_execution_profile
from neckline.k10.windows import SHANGHAI


NOW = datetime(2026, 9, 29, 13, tzinfo=timezone.utc)


class _ScaleTransport(DirectRoundTransport):
    """525 valuable article bodies yield 2099 distinct research events."""

    def _company_for_event(self, event):
        return self.company_codes[base._event_number(event) % 40]

    def respond(self, request):
        packet = self._packet(request)
        if (isinstance(packet.get("companies"), list)
                and isinstance(packet.get("output"), dict)
                and "choices" in packet["output"]):
            self.rank_input_codes = tuple(row["companyCode"] for row in packet["companies"])
        if "items" in packet and "inputCount" not in packet:
            self._record("titleBatch")
            return self._ok({"items": [
                {"i": index, "status": "candidate" if self._title_number(row) < 525 else "no_value",
                 "matterKey": f"matter-{self._title_number(row)}", "stageKey": "new",
                 "reason": "新增事件需核验" if self._title_number(row) < 525 else "无新增经营事实",
                 "companyCodes": [self.company_codes[(self._title_number(row) * 4) % 40]]
                 if self._title_number(row) < 525 else []}
                for index, row in enumerate(packet["items"])]})
        response = super().respond(request)
        if packet.get("action") == "research_round":
            event_number = base._event_number(packet["evidencePacket"]["event"]["canonicalKey"])
            if event_number >= 40:
                result = json.loads(response.json()["choices"][0]["message"]["content"])
                result["companyAssessments"][0]["recommendation"] = "exclude"
                return self._ok(result)
        if isinstance(packet.get("documentId"), str):
            number = self.document_numbers[packet["documentId"]]
            result = json.loads(response.json()["choices"][0]["message"]["content"])
            original = result["events"][0]
            events = []
            for offset in range(3 if number == 524 else 4):
                event = json.loads(json.dumps(original))
                event["canonicalKey"] = f"event-{number * 4 + offset:04d}"
                event["headline"] = f"离线验收事件 {number * 4 + offset:04d}"
                event["claims"][0]["claimId"] = f"claim-{number * 4 + offset:04d}"
                events.append(event)
            return self._ok({"events": events, "needsFullText": False})
        return response


def test_b92_fullscale_report_continues_5734_titles_and_2099_events(tmp_path, monkeypatch):
    """The real B92 CLI/worker must finish a large frozen report across 110s slices."""
    db = tmp_path / "b92-fullscale.sqlite"
    run_id, run_rev, exec_id, exec_rev, _, _ = _bindings(db)
    at = datetime(2026, 9, 26, 22, tzinfo=SHANGHAI)
    published = datetime(2026, 9, 26, 20, 30, tzinfo=SHANGHAI).isoformat()
    fetched = datetime(2026, 9, 26, 20, 31, tzinfo=SHANGHAI).isoformat()
    for index in range(5734):
        source_id = f"v363-scale-{index:04d}"
        original = f"离线验收正文 {index:04d}：公司事件需要基于公开资料核验。"
        store.append_document_version(
            document_id=source_id, source_key="tushare-major-news", external_id=source_id,
            canonical_url=f"https://fixture.invalid/scale/{index:04d}",
            content_sha256=sha256(original.encode()).hexdigest(),
            published_at=published, published_precision="exact", fetched_at=fetched,
            original_text=original, excerpt=None, fetch_version="v363-scale",
            metadata={"title": (f"离线验收标题 {index:04d}：新增经营事件" if index < 525
                                else f"离线验收标题 {index:04d}：昨日行情回顾")},
            created_at=fetched, db_path=db)
    monkeypatch.setattr(base, "TITLE_COUNT", 5734)
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
        result = run_once(db_path=db, task_id=task_id, worker_id="v363-fullscale",
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
                                 "lastActualChangeAt": progress.get("lastActualChangeAt")})
        if result.status != "queued":
            break
    else:
        pytest.fail(f"5734/2099 task did not terminalize in 45 real worker passes: {stages[-6:]}, {failures[-2:]}, {pass_elapsed[-6:]}")
    assert result.status == "completed", (stages, failures, pass_elapsed)
    assert len(stages) > 2
    assert max(pass_elapsed) <= 125, "one noninterruptible phase exceeded the frozen 110-second slice"
    scan = store.get_scan(scan_id=first_scan_id, db_path=db)
    assert scan is not None
    counts = scan["coverage"]["titleDispositionCounts"]
    assert counts == {"input": 5734, "processed": 5734, "failed": 0, "unprocessed": 0}
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
    print("V363_SCALE_SUMMARY " + json.dumps({
        "passes": len(stages), "wallSecondsByPass": pass_elapsed,
        "stageByPass": stages, "progressByPass": progress_by_pass,
        "durableStages": durable_stages, "coverageBytes": coverage_bytes,
        "sqliteBytes": db_bytes, "materialCount": material_count,
        "originalRefCount": len(original_refs), "rankInputCount": len(transport.rank_input_codes),
        "researchWireCount": len(research_wires), "reportStatus": report["status"],
    }, ensure_ascii=False, separators=(",", ":")))


def _frozen_events(count: int):
    documents = tuple(DiscoveryDocument(f"flow-{index}", 1, NOW.isoformat(), NOW.isoformat(),
                                        f"原文 {index}", None, {}) for index in range(count))
    understood = {document.evidence_ref: (EventDraft(
        f"event-{index}", "reported", "reported", f"事件 {index}", "disclosure", {},
        (document.evidence_ref,)),)
        for index, document in enumerate(documents)}
    return documents, understood


def test_research_continuation_advances_past_a_completed_prefix():
    documents, understood = _frozen_events(12)
    completed: set[str] = set()
    fragments = {}
    wires: list[str] = []
    class Model:
        def understand(self, *, document):
            pytest.fail("frozen body should be restored")
    for _slice in range(12):
        guard_calls = 0
        def guard():
            nonlocal guard_calls
            guard_calls += 1
            if guard_calls > 6:
                raise DiscoverySliceYield()
        def investigate(event):
            if event.canonical_key not in completed:
                wires.append(event.canonical_key)
                completed.add(event.canonical_key)
            return InvestigationOutcome(
                Verification("needs_review", "背景", event.source_refs, {"state": "available"}),
                (), None, event.canonical_key)
        try:
            run = run_discovery(
                documents=documents, understood_by_document=understood,
                configuration=_configuration(), model=Model(), verify=lambda _: pytest.fail("direct research"),
                metadata=None, cutoff_at=NOW, leaseguard=guard,
                investigate=investigate, investigation_concurrency=2,
                research_terminal=lambda event: event.canonical_key in completed,
                assembled_events=fragments,
                assembly_checkpoint=lambda event, fragment: fragments.__setitem__(
                    _event_execution_unit_id(event), fragment),
            )
        except DiscoverySliceYield:
            continue
        assert len(run.events) == 12
        break
    else:
        pytest.fail("completed research prefix starved later events across slices")
    assert len(wires) == len(set(wires)) == 12


def test_2099_research_units_skip_967_terminal_but_resume_six_continue():
    documents, understood = _frozen_events(2099)
    prior_terminal = {f"event-{index}" for index in range(967)}
    prior_continue = {f"event-{index}" for index in range(967, 973)}
    terminal = set(prior_terminal)
    calls = {}
    newly_admitted = []
    class Model:
        def understand(self, *, document):
            pytest.fail("the frozen understanding must be restored")
    def investigate(event):
        key = event.canonical_key
        calls[key] = calls.get(key, 0) + 1
        if key not in prior_terminal:
            newly_admitted.append(key)
            terminal.add(key)
        return InvestigationOutcome(
            Verification("needs_review", "离线核验", event.source_refs, {"state": "available"}),
            (), None, key)
    run = run_discovery(documents=documents, understood_by_document=understood,
        configuration=_configuration(), model=Model(), verify=lambda _: pytest.fail("direct research"),
        metadata=None, cutoff_at=NOW, investigate=investigate,
        investigation_concurrency=6,
        research_terminal=lambda event: event.canonical_key in terminal)
    assert run.state == "completed" and len(run.events) == 2099
    assert len(calls) == 2099 and all(value == 1 for value in calls.values())
    assert len(newly_admitted) == len(set(newly_admitted)) == 1132
    assert set(newly_admitted) == {f"event-{index}" for index in range(967, 2099)}
    assert prior_continue <= set(newly_admitted)
    assert len(terminal) == 2099


def test_event_assembly_continues_after_the_research_phase():
    documents, understood = _frozen_events(12)
    fragments = {}
    assembly_calls: list[str] = []
    class Model:
        def understand(self, *, document):
            pytest.fail("frozen body should be restored")
    def investigate(event):
        assembly_calls.append(event.canonical_key)
        return InvestigationOutcome(
            Verification("needs_review", "背景", event.source_refs, {"state": "available"}),
            (), None, event.canonical_key)
    for _slice in range(12):
        guard_calls = 0
        def guard():
            nonlocal guard_calls
            guard_calls += 1
            if guard_calls > 4:
                raise DiscoverySliceYield()
        try:
            run = run_discovery(
                documents=documents, understood_by_document=understood,
                configuration=_configuration(), model=Model(), verify=lambda _: pytest.fail("direct research"),
                metadata=None, cutoff_at=NOW, leaseguard=guard,
                investigate=investigate, investigation_concurrency=2,
                research_terminal=lambda _event: True,
                assembled_events=fragments,
                assembly_checkpoint=lambda event, fragment: fragments.__setitem__(
                    _event_execution_unit_id(event), fragment),
            )
        except DiscoverySliceYield:
            continue
        assert len(run.events) == 12
        break
    else:
        pytest.fail("completed research could not progress through assembly")
    assert len(fragments) == 12
    assert len(assembly_calls) == len(set(assembly_calls)) == 12


def test_assembled_candidate_uses_original_event_input_on_next_slice():
    documents, understood = _frozen_events(2)
    model = _Model(count=1)
    fragments = {}
    def investigate(event):
        verification = _verify(event)
        mappings = model.map_companies(event=event, verification=verification)
        comparison = model.compare_event(event=event, verification=verification,
                                         mappings=mappings)
        return InvestigationOutcome(verification, mappings, comparison, event.canonical_key)
    def freeze(event, fragment):
        fragments[_event_execution_unit_id(event)] = fragment
    def first_guard():
        if fragments:
            raise DiscoverySliceYield()
    with pytest.raises(DiscoverySliceYield):
        run_discovery(documents=documents, understood_by_document=understood,
            configuration=_configuration(), model=model, verify=_verify,
            metadata=_Metadata(), cutoff_at=NOW, leaseguard=first_guard,
            investigate=investigate, research_terminal=lambda _: True,
            assembled_events=fragments, assembly_checkpoint=freeze)
    assert len(fragments) == 1
    resumed = run_discovery(documents=documents, understood_by_document=understood,
        configuration=_configuration(), model=model, verify=_verify,
        metadata=_Metadata(), cutoff_at=NOW, investigate=investigate,
        research_terminal=lambda _: True, assembled_events=fragments,
        assembly_checkpoint=freeze)
    assert resumed.state == "completed"
    assert len(resumed.events) == len(resumed.candidates) == len(fragments) == 2
    assert all("eventComparison" in event.derived_facts for event in resumed.events)


def test_eight_company_event_advances_across_short_slices():
    documents, understood = _frozen_events(1)
    model = _Model(count=8)
    fragments = {}
    classifications = []
    original_classify = model.classify_opportunity
    def classify(**kwargs):
        classifications.append(kwargs["mapping"].company_code)
        return original_classify(**kwargs)
    model.classify_opportunity = classify
    def investigate(event):
        verification = _verify(event)
        mappings = model.map_companies(event=event, verification=verification)
        return InvestigationOutcome(verification, mappings,
            model.compare_event(event=event, verification=verification, mappings=mappings),
            event.canonical_key)
    for _slice in range(8):
        guards = 0
        def guard():
            nonlocal guards
            guards += 1
            if guards > 3:
                raise DiscoverySliceYield()
        try:
            result = run_discovery(documents=documents, understood_by_document=understood,
                configuration=_configuration(), model=model, verify=_verify,
                metadata=_Metadata(), cutoff_at=NOW, leaseguard=guard,
                investigate=investigate, research_terminal=lambda _: True,
                assembled_events=fragments,
                assembly_checkpoint=lambda event, fragment: fragments.__setitem__(
                    _event_execution_unit_id(event), fragment),
                company_checkpoint=lambda event, code, fragment: fragments.__setitem__(
                    _event_execution_unit_id(event) + "@" + code, fragment))
        except DiscoverySliceYield:
            continue
        break
    else:
        pytest.fail("one eight-company event kept redoing its first two classifications")
    assert result.state == "completed"
    assert len(result.events) == 1 and len(result.candidates) == 8
    assert len(classifications) == len(set(classifications)) == 8


def test_company_fragments_keep_one_local_failure_across_slices():
    documents, understood = _frozen_events(1)
    model = _Model(count=4)
    fragments = {}
    classifications = []
    original_classify = model.classify_opportunity
    def classify(**kwargs):
        code = kwargs["mapping"].company_code
        classifications.append(code)
        if code == "300001.SZ":
            raise ValueError("fixture company classification rejected")
        return original_classify(**kwargs)
    model.classify_opportunity = classify
    def investigate(event):
        verification = _verify(event)
        mappings = model.map_companies(event=event, verification=verification)
        return InvestigationOutcome(verification, mappings,
            model.compare_event(event=event, verification=verification, mappings=mappings),
            event.canonical_key)
    for _slice in range(5):
        guards = 0
        def guard():
            nonlocal guards
            guards += 1
            if guards > 3:
                raise DiscoverySliceYield()
        try:
            result = run_discovery(documents=documents, understood_by_document=understood,
                configuration=_configuration(), model=model, verify=_verify,
                metadata=_Metadata(), cutoff_at=NOW, leaseguard=guard,
                investigate=investigate, research_terminal=lambda _: True,
                assembled_events=fragments,
                assembly_checkpoint=lambda event, fragment: fragments.__setitem__(
                    _event_execution_unit_id(event), fragment),
                company_checkpoint=lambda event, code, fragment: fragments.__setitem__(
                    _event_execution_unit_id(event) + "@" + code, fragment))
        except DiscoverySliceYield:
            continue
        break
    else:
        pytest.fail("a local company failure blocked the event from completing")
    assert result.state == "partial"
    assert len(result.candidates) == 3
    assert result.document_counts["eventFailed"] == 1
    assert [issue.code for issue in result.issues] == ["contract_invalid"]
    assert len(classifications) == len(set(classifications)) == 4
    assert _event_execution_unit_id(understood[documents[0].evidence_ref][0]) in fragments


def test_company_unknown_never_freezes_an_incomplete_event():
    documents, understood = _frozen_events(1)
    model = _Model(count=4)
    fragments = {}
    original_classify = model.classify_opportunity
    def classify(**kwargs):
        if kwargs["mapping"].company_code == "300001.SZ":
            raise pipeline.PipelineError("fixture unknown external outcome",
                                         code="provider_request_outcome_unknown")
        return original_classify(**kwargs)
    model.classify_opportunity = classify
    def investigate(event):
        verification = _verify(event)
        mappings = model.map_companies(event=event, verification=verification)
        return InvestigationOutcome(verification, mappings,
            model.compare_event(event=event, verification=verification, mappings=mappings),
            event.canonical_key)
    result = run_discovery(documents=documents, understood_by_document=understood,
        configuration=_configuration(), model=model, verify=_verify,
        metadata=_Metadata(), cutoff_at=NOW, investigate=investigate,
        research_terminal=lambda _: True, assembled_events=fragments,
        assembly_checkpoint=lambda event, fragment: fragments.__setitem__(
            _event_execution_unit_id(event), fragment),
        company_checkpoint=lambda event, code, fragment: fragments.__setitem__(
            _event_execution_unit_id(event) + "@" + code, fragment))
    unit = _event_execution_unit_id(understood[documents[0].evidence_ref][0])
    assert result.state == "partial"
    assert unit not in fragments
    assert set(fragments) == {unit + "@300000.SZ"}
    assert [issue.code for issue in result.issues] == ["provider_request_outcome_unknown"]


def test_company_fragment_rejects_changed_input_with_same_unit_key():
    documents, understood = _frozen_events(1)
    model = _Model(count=4)
    fragments = {}
    def investigate(event):
        verification = _verify(event)
        mappings = model.map_companies(event=event, verification=verification)
        return InvestigationOutcome(verification, mappings,
            model.compare_event(event=event, verification=verification, mappings=mappings),
            event.canonical_key)
    guards = 0
    def guard():
        nonlocal guards
        guards += 1
        if guards > 3:
            raise DiscoverySliceYield()
    def freeze_company(event, code, fragment):
        fragments[_event_execution_unit_id(event) + "@" + code] = fragment
    with pytest.raises(DiscoverySliceYield):
        run_discovery(documents=documents, understood_by_document=understood,
            configuration=_configuration(), model=model, verify=_verify,
            metadata=_Metadata(), cutoff_at=NOW, leaseguard=guard,
            investigate=investigate, research_terminal=lambda _: True,
            assembled_events=fragments, company_checkpoint=freeze_company)
    event = understood[documents[0].evidence_ref][0]
    assert set(fragments) == {_event_execution_unit_id(event) + "@300000.SZ",
                              _event_execution_unit_id(event) + "@300001.SZ"}
    changed = replace(event, headline="同身份但不同正文", derived_facts={"newFact": True})
    assert _event_execution_unit_id(changed) == _event_execution_unit_id(event)
    with pytest.raises(ValueError, match="公司组装检查点与冻结输入不匹配"):
        run_discovery(documents=documents,
            understood_by_document={documents[0].evidence_ref: (changed,)},
            configuration=_configuration(), model=model, verify=_verify,
            metadata=_Metadata(), cutoff_at=NOW, investigate=investigate,
            research_terminal=lambda _: True, assembled_events=fragments,
            company_checkpoint=freeze_company)


def test_private_slice_progress_changes_only_with_durable_stage_work(tmp_path):
    db = tmp_path / "progress.sqlite"
    run_id, run_rev, exec_id, exec_rev, _, _ = _bindings(db)
    task_id = _cli("enqueue", "--db", str(db), "--kind", "evening",
        "--trading-day", "2026-09-26", "--config-id", run_id,
        "--config-revision", str(run_rev), "--execution-config-id", exec_id,
        "--execution-config-revision", str(exec_rev))
    empty = pipeline._discovery_slice_progress(task_id=task_id, db_path=db)
    assert empty["lastActualChangeAt"] is None
    assert not any(empty["sliceDelta"].values())
    def persist(value):
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE k10_tasks SET checkpoint_json=json_set(checkpoint_json,"
                         "'$.executionProgress',json(?)) WHERE task_id=?",
                         (json.dumps(value), task_id))
    persist(empty)
    quiet = pipeline._discovery_slice_progress(task_id=task_id, db_path=db)
    assert quiet["lastActualChangeAt"] is None
    assert not any(quiet["sliceDelta"].values())
    store.record_execution_checkpoint(task_id=task_id, item_kind="document", item_key="title-1",
        stage="model:titleBatch", input_sha256="a" * 64, status="completed",
        attempt_count=1, network_attempt_count=1, repair_attempt_count=0, elapsed_ms=1,
        input_tokens=1, output_tokens=1, result={"itemCount": 1},
        safe_error_code=None, safe_error_ref=None, updated_at=NOW.isoformat(), db_path=db)
    advanced = pipeline._discovery_slice_progress(task_id=task_id, db_path=db)
    assert advanced["counts"]["titleBatches"] == 1
    assert advanced["sliceDelta"]["titleBatches"] == 1
    assert advanced["lastActualChangeAt"] is not None
    persist(advanced)
    repeated = pipeline._discovery_slice_progress(task_id=task_id, db_path=db)
    assert repeated["counts"] == advanced["counts"]
    assert repeated["lastActualChangeAt"] == advanced["lastActualChangeAt"]
    assert not any(repeated["sliceDelta"].values())


def test_pre_rank_aggregate_skips_all_event_fragments_on_resume(monkeypatch):
    documents, understood = _frozen_events(12)
    model = _Model(count=1)
    aggregates = {}
    classifications = []
    original_classify = model.classify_opportunity
    def classify(**kwargs):
        classifications.append(kwargs["event"].canonical_key)
        return original_classify(**kwargs)
    model.classify_opportunity = classify
    def investigate(event):
        verification = _verify(event)
        mappings = model.map_companies(event=event, verification=verification)
        return InvestigationOutcome(verification, mappings,
            model.compare_event(event=event, verification=verification, mappings=mappings),
            event.canonical_key)
    def postpone_ranking():
        raise DiscoverySliceYield()
    with pytest.raises(DiscoverySliceYield):
        run_discovery(documents=documents, understood_by_document=understood,
            configuration=_configuration(), model=model, verify=_verify,
            metadata=_Metadata(), cutoff_at=NOW, investigate=investigate,
            research_terminal=lambda _: True, finalization_guard=postpone_ranking,
            aggregate_checkpoint=lambda fragment: aggregates.__setitem__("pre_rank", fragment))
    assert len(classifications) == 12
    assert len(aggregates["pre_rank"]["run"]["events"]) == 12
    thaw_calls = []
    original_thaw = discovery_module.thaw_discovery_run
    def counting_thaw(**kwargs):
        thaw_calls.append(1)
        return original_thaw(**kwargs)
    monkeypatch.setattr(discovery_module, "thaw_discovery_run", counting_thaw)
    resumed = run_discovery(documents=documents, understood_by_document=understood,
        configuration=_configuration(), model=model, verify=_verify,
        metadata=_Metadata(), cutoff_at=NOW, investigate=investigate,
        research_terminal=lambda _: True, assembled_aggregate=aggregates["pre_rank"])
    assert resumed.state == "completed"
    assert len(resumed.events) == len(resumed.candidates) == 12
    assert len(classifications) == 12, "the prefix must not be classified again"
    assert len(thaw_calls) == 1, "one aggregate load replaces twelve fragment replays"
    first = understood[documents[0].evidence_ref][0]
    changed = dict(understood)
    changed[documents[0].evidence_ref] = (replace(first, headline="冻结后不同事件正文"),)
    with pytest.raises(ValueError, match="预排序组装检查点与冻结输入不匹配"):
        run_discovery(documents=documents, understood_by_document=changed,
            configuration=_configuration(), model=model, verify=_verify,
            metadata=_Metadata(), cutoff_at=NOW, investigate=investigate,
            research_terminal=lambda _: True, assembled_aggregate=aggregates["pre_rank"])


def test_pre_rank_aggregate_waits_for_pending_verification():
    documents, understood = _frozen_events(2)
    model = _Model(count=1)
    aggregates = {}
    fragments = {}
    seen = []
    def investigate(event):
        seen.append(event.canonical_key)
        verification = _verify(event)
        if event.canonical_key == "event-0" and seen.count("event-0") == 1:
            return InvestigationOutcome(
                Verification(verification.state, verification.summary,
                             verification.evidence_refs, {"state": "pending"}),
                (), None, event.canonical_key)
        mappings = model.map_companies(event=event, verification=verification)
        return InvestigationOutcome(verification, mappings,
            model.compare_event(event=event, verification=verification, mappings=mappings),
            event.canonical_key)
    def postpone_ranking():
        raise DiscoverySliceYield()
    with pytest.raises(DiscoverySliceYield):
        run_discovery(documents=documents, understood_by_document=understood,
            configuration=_configuration(), model=model, verify=_verify,
            metadata=_Metadata(), cutoff_at=NOW, investigate=investigate,
            research_terminal=lambda _: True, finalization_guard=postpone_ranking,
            assembled_events=fragments,
            assembly_checkpoint=lambda event, fragment: fragments.__setitem__(
                _event_execution_unit_id(event), fragment),
            aggregate_checkpoint=lambda fragment: aggregates.__setitem__("pre_rank", fragment))
    assert "pre_rank" not in aggregates, "a pending event must remain resumable"
    assert len(fragments) == 1
    resumed = run_discovery(documents=documents, understood_by_document=understood,
        configuration=_configuration(), model=model, verify=_verify,
        metadata=_Metadata(), cutoff_at=NOW, investigate=investigate,
        research_terminal=lambda _: True, assembled_events=fragments,
        assembly_checkpoint=lambda event, fragment: fragments.__setitem__(
            _event_execution_unit_id(event), fragment),
        aggregate_checkpoint=lambda fragment: aggregates.__setitem__("pre_rank", fragment))
    assert resumed.state == "completed"
    assert len(resumed.candidates) == 2
    assert seen.count("event-0") == 2 and seen.count("event-1") == 1
    assert "pre_rank" in aggregates


def _small_b92_evening(tmp_path, monkeypatch):
    db = tmp_path / "b92-interrupted.sqlite"
    run_id, run_rev, exec_id, exec_rev, _, _ = _bindings(db)
    at = datetime(2026, 9, 26, 22, tzinfo=SHANGHAI)
    seen_at = datetime(2026, 9, 26, 20, 30, tzinfo=SHANGHAI).isoformat()
    for index in range(2):
        original = f"离线断点资料 {index}：公司披露送样进展，订单尚待确认。"
        store.append_document_version(document_id=f"interrupted-{index}",
            source_key="tushare-major-news", external_id=f"interrupted-{index}",
            canonical_url=f"https://fixture.invalid/interrupted/{index}",
            content_sha256=sha256(original.encode()).hexdigest(),
            published_at=seen_at, published_precision="exact", fetched_at=seen_at,
            original_text=original, excerpt=None, fetch_version="v363-interrupted",
            metadata={"title": f"离线验收标题 {index:04d}：新增经营事件"},
            created_at=seen_at, db_path=db)
    monkeypatch.setattr(base, "TITLE_COUNT", 2)
    monkeypatch.setattr(base, "DeterministicTransport", _ScaleTransport)
    transport, tavily = base.install_offline_transports(
        monkeypatch, refusal_event=None, selected_event_count=2,
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
    def handler(context):
        return pipeline.production_scan_handler(context, tushare_token=None,
            parquet_dir=tmp_path / "parquet", now=lambda: at)
    return db, task_id, transport, tavily, handler, (run_id, run_rev, exec_id, exec_rev)


def test_b92_paid_ranking_survives_busy_draft_write_in_same_task(tmp_path, monkeypatch):
    db, task_id, transport, tavily, handler, binding = _small_b92_evening(tmp_path, monkeypatch)
    actual_write = store.update_running_scan_coverage
    interrupted = []
    def busy_once(*args, **kwargs):
        if not interrupted and isinstance(kwargs.get("coverage"), dict) and "discoveryDraft" in kwargs["coverage"]:
            interrupted.append(True)
            raise SqliteWriteBusy("isolated draft write contention")
        return actual_write(*args, **kwargs)
    monkeypatch.setattr(store, "update_running_scan_coverage", busy_once)
    first = run_once(db_path=db, task_id=task_id, worker_id="v363-draft-first",
        lease_for=timedelta(minutes=5), clock=lambda: datetime.now(SHANGHAI),
        handlers={"evening_scan": handler}, require_b76_contract=True)
    assert interrupted and first is not None and first.status == "queued"
    frozen = store.task_execution_input(task_id=task_id, db_path=db)
    scan_id = frozen["checkpoint"]["scanId"]
    scan = store.get_scan(scan_id=scan_id, db_path=db)
    assert "discoveryDraft" not in scan["coverage"]
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA application_id").fetchone()[0] == 1313552690
        assert conn.execute("SELECT COUNT(*) FROM k10_execution_item_checkpoints "
                            "WHERE task_id=? AND stage='model:prioritize' AND status='completed'",
                            (task_id,)).fetchone()[0] == 1
    rank_wires = [kind for kind, _ in transport.calls if kind == "prioritize"]
    research_wires = [key for kind, key in transport.calls if kind == "research:research_round"]
    assert len(rank_wires) == 1 and len(research_wires) == 8
    monkeypatch.setattr(store, "update_running_scan_coverage", actual_write)
    second = run_once(db_path=db, task_id=task_id, worker_id="v363-draft-resume",
        lease_for=timedelta(minutes=5), clock=lambda: datetime.now(SHANGHAI),
        handlers={"evening_scan": handler}, require_b76_contract=True)
    assert second is not None and second.status == "completed"
    assert len([kind for kind, _ in transport.calls if kind == "prioritize"]) == 1
    assert len([key for kind, key in transport.calls if kind == "research:research_round"]) == 8
    assert tavily.queries == []
    assert store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"] == scan_id
    with base.actual_api(db, config_id=binding[0], config_revision=binding[1],
                         execution_id=binding[2], execution_revision=binding[3]) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=evening")
        assert response.status_code == 200
        report = response.json()["report"]
        assert report["status"] == "partial"
        materials = client.get(f"/api/v1/k10/v2/reports/{report['reportId']}/materials")
        assert materials.status_code == 200 and materials.json()["items"]


def test_b92_snapshot_fact_survives_missing_coverage_and_assembly_write(tmp_path, monkeypatch):
    db, task_id, transport, _tavily, handler, _binding = _small_b92_evening(tmp_path, monkeypatch)
    actual_checkpoint = store.record_execution_checkpoint
    interrupted = []
    def busy_after_research(*args, **kwargs):
        if kwargs.get("stage") == "discovery_assemble" and not interrupted:
            interrupted.append(True)
            raise SqliteWriteBusy("isolated event assembly contention")
        return actual_checkpoint(*args, **kwargs)
    monkeypatch.setattr(store, "record_execution_checkpoint", busy_after_research)
    first = run_once(db_path=db, task_id=task_id, worker_id="v363-snapshot-first",
        lease_for=timedelta(minutes=5), clock=lambda: datetime.now(SHANGHAI),
        handlers={"evening_scan": handler}, require_b76_contract=True)
    assert interrupted and first is not None and first.status == "queued"
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    scan = store.get_scan(scan_id=scan_id, db_path=db)
    assert "researchSnapshotIds" not in scan["coverage"]
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA application_id").fetchone()[0] == 1313552690
        assert conn.execute("SELECT COUNT(*) FROM k10_research_snapshot_revisions "
                            "WHERE task_id=? AND json_type(snapshot_json,'$.terminalOutcome')='object'",
                            (task_id,)).fetchone()[0] == 8
    first_wires = [key for kind, key in transport.calls if kind == "research:research_round"]
    assert len(first_wires) == len(set(first_wires)) == 8
    monkeypatch.setattr(store, "record_execution_checkpoint", actual_checkpoint)
    resumed = run_once(db_path=db, task_id=task_id, worker_id="v363-snapshot-resume",
        lease_for=timedelta(minutes=5), clock=lambda: datetime.now(SHANGHAI),
        handlers={"evening_scan": handler}, require_b76_contract=True)
    assert resumed is not None and resumed.status == "completed"
    assert store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"] == scan_id
    assert [key for kind, key in transport.calls if kind == "research:research_round"] == first_wires


def test_b92_dependency_checkpoint_resumes_before_one_global_rank(tmp_path, monkeypatch):
    db, task_id, transport, _tavily, handler, binding = _small_b92_evening(tmp_path, monkeypatch)
    actual_checkpoint = store.record_execution_checkpoint
    interrupted = []
    def stop_after_dependency_write(*args, **kwargs):
        result = actual_checkpoint(*args, **kwargs)
        if kwargs.get("stage") == "discovery_dependencies" and not interrupted:
            interrupted.append(True)
            raise SqliteWriteBusy("isolated post-commit dependency interruption")
        return result
    monkeypatch.setattr(store, "record_execution_checkpoint", stop_after_dependency_write)
    first = run_once(db_path=db, task_id=task_id, worker_id="v363-dependency-first",
        lease_for=timedelta(minutes=5), clock=lambda: datetime.now(SHANGHAI),
        handlers={"evening_scan": handler}, require_b76_contract=True)
    assert interrupted and first is not None and first.status == "queued"
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA application_id").fetchone()[0] == 1313552690
        stages = dict(conn.execute("SELECT stage,COUNT(*) FROM k10_execution_item_checkpoints "
                                   "WHERE task_id=? GROUP BY stage", (task_id,)).fetchall())
    assert stages["discovery_pre_rank"] == stages["discovery_dependencies"] == 1
    assert stages.get("model:prioritize", 0) == 0
    first_research = [key for kind, key in transport.calls if kind == "research:research_round"]
    assert len(first_research) == 8
    monkeypatch.setattr(store, "record_execution_checkpoint", actual_checkpoint)
    second = run_once(db_path=db, task_id=task_id, worker_id="v363-dependency-resume",
        lease_for=timedelta(minutes=5), clock=lambda: datetime.now(SHANGHAI),
        handlers={"evening_scan": handler}, require_b76_contract=True)
    assert second is not None and second.status == "completed"
    assert store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"] == scan_id
    assert [key for kind, key in transport.calls if kind == "research:research_round"] == first_research
    assert [kind for kind, _ in transport.calls if kind == "prioritize"] == ["prioritize"]
    with base.actual_api(db, config_id=binding[0], config_revision=binding[1],
                         execution_id=binding[2], execution_revision=binding[3]) as client:
        report = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()["report"]
        assert report["status"] == "partial"


def test_b92_private_event_write_interruption_keeps_publication_atomic(tmp_path, monkeypatch):
    db, task_id, transport, _tavily, handler, binding = _small_b92_evening(tmp_path, monkeypatch)
    actual_append = discovery_module.SqliteDiscoveryWriter.append_event
    interrupted = []
    def append_then_busy(self, *, event, verification):
        result = actual_append(self, event=event, verification=verification)
        if self._scan_id != "research-input" and not interrupted:
            interrupted.append(True)
            raise SqliteWriteBusy("isolated private event continuation")
        return result
    monkeypatch.setattr(discovery_module.SqliteDiscoveryWriter, "append_event", append_then_busy)
    first = run_once(db_path=db, task_id=task_id, worker_id="v363-private-first",
        lease_for=timedelta(minutes=5), clock=lambda: datetime.now(SHANGHAI),
        handlers={"evening_scan": handler}, require_b76_contract=True)
    assert interrupted and first is not None and first.status == "queued"
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    assert "discoveryDraft" in store.get_scan(scan_id=scan_id, db_path=db)["coverage"]
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA application_id").fetchone()[0] == 1313552690
        assert conn.execute("SELECT COUNT(*) FROM k10_v2_report_runs WHERE scan_id=? AND available_at IS NOT NULL",
                            (scan_id,)).fetchone()[0] == 0
    first_wires = tuple(transport.calls)
    monkeypatch.setattr(discovery_module.SqliteDiscoveryWriter, "append_event", actual_append)
    second = run_once(db_path=db, task_id=task_id, worker_id="v363-private-resume",
        lease_for=timedelta(minutes=5), clock=lambda: datetime.now(SHANGHAI),
        handlers={"evening_scan": handler}, require_b76_contract=True)
    assert second is not None and second.status == "completed"
    assert tuple(transport.calls) == first_wires
    assert store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"] == scan_id
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA application_id").fetchone()[0] == 1313552690
        assert conn.execute("SELECT COUNT(DISTINCT event_id) FROM k10_event_revisions").fetchone()[0] == 8
        assert conn.execute("SELECT MAX(revision) FROM k10_event_revisions").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM k10_event_revisions").fetchone()[0] == 16
        assert conn.execute("SELECT COUNT(*) FROM k10_v2_report_runs WHERE scan_id=?",
                            (scan_id,)).fetchone()[0] == 1
    with base.actual_api(db, config_id=binding[0], config_revision=binding[1],
                         execution_id=binding[2], execution_revision=binding[3]) as client:
        report = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()["report"]
        assert report["status"] == "partial"


def test_morning_consumption_preserves_undecided_failed_unknown_and_new_revisions(tmp_path):
    db = tmp_path / "consumption-matrix.sqlite"
    run_id, run_rev, exec_id, exec_rev, _, _ = _bindings(db)
    prior_at = "2026-09-28T21:00:00+08:00"
    morning_at = "2026-09-29T08:30:00+08:00"
    evening_at = "2026-09-29T21:00:00+08:00"
    observed = "2026-09-29T08:00:00+08:00"
    for name in "ABCDE":
        text = f"资料{name}第一版"
        store.append_document_version(document_id=name, source_key="jin10-flash",
            external_id=name, canonical_url=f"https://fixture.invalid/{name}",
            content_sha256=sha256(text.encode()).hexdigest(), published_at=observed,
            published_precision="exact", fetched_at=observed, original_text=text,
            excerpt=None, fetch_version="fixture", metadata={}, created_at=observed,
            db_path=db)
    store.append_document_version(document_id="A", source_key="jin10-flash",
        external_id="A", canonical_url="https://fixture.invalid/A",
        content_sha256=sha256(b"A-second").hexdigest(), published_at=observed,
        published_precision="exact", fetched_at="2026-09-29T12:00:00+08:00",
        original_text="资料A第二版", excerpt=None, fetch_version="fixture",
        metadata={}, created_at="2026-09-29T12:00:00+08:00", db_path=db)
    def ref(name, revision=1):
        return {"documentId": name, "revision": revision}
    def scan(name, window, cutoff, status, refs, *, frozen_at=None, terminal=(), available=True,
             extra=None):
        coverage = {"collectedInput": {"inputFrozenAt": frozen_at or cutoff,
                     "inputDocumentRefs": list(refs)},
                    "collectedInputConsumption": {"terminalRefs": list(terminal)},
                    **(extra or {})}
        store.create_scan(scan_id=name, window_kind=window, cutoff_at=cutoff,
            config_id=run_id, config_revision=run_rev, status=status,
            coverage=coverage, created_at=cutoff, completed_at=cutoff, db_path=db)
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO k10_v2_report_runs(report_id,scan_id,strategy_snapshot_id,"
                "window_kind,parent_report_id,cutoff_at,verification_cutoff_at,available_at,"
                "status,error_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("report_" + name, name, "k10-v2-b92-isolated", window, None,
                 cutoff, cutoff, cutoff if available else None,
                 "partial" if available else "failed", None, cutoff))
        return coverage
    original = scan("prior-evening", "evening", prior_at, "completed", [],
                    frozen_at=prior_at)
    scan("published-morning", "morning", morning_at, "partial", [ref("A"), ref("B")],
         terminal=[ref("A")], extra={"b90MorningParent": {"reviewSource": [ref("E")]}})
    scan("failed-morning", "morning", "2026-09-29T08:31:00+08:00", "failed", [ref("C")],
         terminal=[ref("C")], available=False)
    store.create_scan(scan_id="unknown-scan", window_kind="morning", cutoff_at=morning_at,
        config_id=run_id, config_revision=run_rev, status="running",
        coverage={"collectedInput": {"inputDocumentRefs": [ref("D")]}},
        created_at=morning_at, completed_at=None, db_path=db)
    store.enqueue_task(task_id="unknown-task", kind="morning_scan",
        idempotency_key="unknown-task", input_version="matrix", input_cutoff_at=morning_at,
        payload={"windowKind": "morning"}, budget={"maxAttempts": 1},
        created_at=morning_at, db_path=db)
    store.bind_task_execution(task_id="unknown-task", execution_config_id=exec_id,
        execution_config_revision=exec_rev, binding_kind="scheduled",
        bound_at=morning_at, db_path=db)
    store.bind_scan_execution(scan_id="unknown-scan", task_id="unknown-task",
        execution_config_id=exec_id, execution_config_revision=exec_rev,
        binding_kind="scheduled", bound_at=morning_at, db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO k10_external_attempts(attempt_id,task_id,stage,item_key,"
            "attempt_key,input_sha256,state,started_at) VALUES (?,?,?,?,?,?,?,?)",
            ("unknown-wire", "unknown-task", "research", "D@1", "D-wire",
             "f" * 64, "unknown", morning_at))
    store.create_scan(scan_id="next-evening", window_kind="evening", cutoff_at=evening_at,
        config_id=run_id, config_revision=run_rev, status="running", coverage={},
        created_at=evening_at, completed_at=None, db_path=db)
    frozen = store.freeze_collected_input(db_path=db, scan_id="next-evening",
        window="evening", source_keys=["jin10-flash"],
        frozen_at=datetime(2026, 9, 29, 22, tzinfo=SHANGHAI))
    selected = {(row["documentId"], row["revision"]) for row in frozen["inputDocumentRefs"]}
    assert selected == {("A", 2), ("B", 1), ("C", 1), ("E", 1)}
    assert store.freeze_collected_input(db_path=db, scan_id="prior-evening",
        window="evening", source_keys=["jin10-flash"],
        frozen_at=datetime(2026, 9, 28, 22, tzinfo=SHANGHAI)) == original["collectedInput"]


def test_empty_complete_sources_distinguish_unavailable_parent_from_zero_parent(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "_morning_target_items", lambda **_kwargs: [])
    parent = SimpleNamespace(db_path=tmp_path / "unused.sqlite", input_cutoff_at=NOW.isoformat(),
                             require_lease=lambda: None)
    outputs = {}
    for state in ("unavailable", "complete"):
        monkeypatch.setattr(store, "get_scan", lambda **_kwargs: {
            "coverage": {"b90MorningParent": {"state": state, "targets": []}}})
        report, work_ids, review_state = pipeline._assemble_morning_report(
            parent=parent, scan_id="scan-empty-parent", cutoff_at=NOW,
            configuration={}, config_id="isolated", config_revision=1,
            source_status="complete", morning_refs=(), generated_at=NOW,
            persist=False)
        delivery = pipeline._morning_delivery_for_report(
            delivery={"outcome": "complete", "rankingScope": "none", "gaps": []}, report=report)
        outputs[state] = report, work_ids, review_state, delivery
    unavailable, no_work, state, delivery = outputs["unavailable"]
    assert no_work == [] and state == "unavailable"
    assert unavailable["status"] == "partial"
    assert unavailable["coverage"]["gaps"] == ["morning_parent_unavailable", "morning_review_unavailable"]
    assert delivery["outcome"] == "partial"
    assert [(gap["reasonCode"], gap["unitId"]) for gap in delivery["gaps"]] == [
        ("morning_parent_unavailable", "report_scan-empty-parent")]
    zero, no_work, state, delivery = outputs["complete"]
    assert no_work == [] and state == "completed"
    assert zero["status"] == "completed" and zero["coverage"]["gaps"] == []
    assert delivery["outcome"] == "complete" and delivery["gaps"] == []


def test_progress_get_projects_scalars_beside_large_unrelated_payloads(tmp_path):
    db = tmp_path / "large-progress.sqlite"
    execution_id, execution_rev = seed_execution(db)
    store.create_scan(scan_id="scan-progress", window_kind="evening", cutoff_at=NOW.isoformat(),
        config_id="strategy", config_revision=1, status="running",
        coverage={"executionState": "investigation", "factCacheHits": 1},
        created_at=NOW.isoformat(), completed_at=None, db_path=db)
    store.bind_scan_execution(scan_id="scan-progress", task_id="task-1",
        execution_config_id=execution_id, execution_config_revision=execution_rev,
        binding_kind="scheduled", bound_at=NOW.isoformat(), db_path=db)
    store.record_execution_checkpoint(task_id="task-1", item_kind="event", item_key="unrelated",
        stage="unrelated", input_sha256="a" * 64, status="completed", attempt_count=1,
        network_attempt_count=0, repair_attempt_count=0, elapsed_ms=0,
        input_tokens=None, output_tokens=None, result={"unused": "small"},
        safe_error_code=None, safe_error_ref=None, updated_at=NOW.isoformat(), db_path=db)
    before = store.execution_progress_for_scan(scan_id="scan-progress", db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE k10_scans SET coverage_json=? WHERE scan_id='scan-progress'",
                     (json.dumps({"executionState": "investigation", "factCacheHits": 1,
                                  "unusedTree": "x" * 3_540_000}),))
        conn.execute("UPDATE k10_execution_item_checkpoints SET result_json=? WHERE item_key='unrelated'",
                     (json.dumps({"unused": "y" * 14_500_000}),))
    tracemalloc.start()
    try:
        with ThreadPoolExecutor(max_workers=4) as workers:
            after = list(workers.map(lambda _: store.execution_progress_for_scan(
                scan_id="scan-progress", db_path=db), range(4)))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert after == [before] * 4
    assert peak < 5_000_000, "progress GET must not decode multi-MB irrelevant JSON in Python"


def test_real_scan_gets_keep_the_same_dto_with_four_large_coverage_trees(tmp_path):
    db = tmp_path / "large-api-progress.sqlite"
    execution_id, execution_rev = seed_execution(db)
    for index in range(4):
        task_id = "task-1" if index == 0 else f"task-api-{index}"
        if index:
            store.enqueue_task(task_id=task_id, kind="evening_scan", idempotency_key=task_id,
                input_version="frozen", input_cutoff_at=NOW.isoformat(), payload={"windowKind": "evening"},
                budget={"maxAttempts": 1}, created_at=NOW.isoformat(), db_path=db)
            store.bind_task_execution(task_id=task_id, execution_config_id=execution_id,
                execution_config_revision=execution_rev, binding_kind="scheduled",
                bound_at=NOW.isoformat(), db_path=db)
        scan_id = f"scan-api-{index}"
        store.create_scan(scan_id=scan_id, window_kind="evening",
            cutoff_at=(NOW + timedelta(seconds=index)).isoformat(), config_id="strategy",
            config_revision=1, status="running", coverage={"executionState": "investigation"},
            created_at=NOW.isoformat(), completed_at=None, db_path=db)
        store.bind_scan_execution(scan_id=scan_id, task_id=task_id,
            execution_config_id=execution_id, execution_config_revision=execution_rev,
            binding_kind="scheduled", bound_at=NOW.isoformat(), db_path=db)
    endpoints = ["/api/v1/k10/scans/latest?window=evening",
                 *(f"/api/v1/k10/scans/scan-api-{index}" for index in range(4))]
    with base.actual_api(db, config_id="strategy", config_revision=1,
                         execution_id=execution_id, execution_revision=execution_rev) as client:
        before = [client.get(endpoint).json() for endpoint in endpoints]
        with sqlite3.connect(db) as conn:
            for index in range(4):
                conn.execute("UPDATE k10_scans SET coverage_json=? WHERE scan_id=?",
                    (json.dumps({"executionState": "investigation", "unusedTree": "x" * 3_540_000}),
                     f"scan-api-{index}"))
        tracemalloc.start()
        try:
            with ThreadPoolExecutor(max_workers=4) as workers:
                after = list(workers.map(lambda endpoint: client.get(endpoint).json(), endpoints))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    assert after == before
    assert all(item["scanId"].startswith("scan-api-") for item in after)
    assert peak < 12_000_000, "scan GET must not deserialize four unrelated coverage trees"


def test_worker_maintenance_does_not_let_evening_continuation_starve_older_work(tmp_path, monkeypatch):
    db = tmp_path / "fair-worker.sqlite"
    initialize_schema(db)
    start = datetime(2026, 9, 30, 0, 0, 2, tzinfo=timezone.utc)
    clock = [start]
    store.set_run_control(state="open", reason_code="fixture", changed_at=start.isoformat(),
                          changed_by="test", db_path=db)
    store.set_collection_control(state="open", reason_code="fixture", changed_at=start.isoformat(),
                                 changed_by="test", db_path=db)
    execution_id, execution_rev = append_approved_execution_profile(
        db_path=db, created_at=start.isoformat(), config_id="v363-fair-execution")
    for task_id, kind, created_at in (
        ("evening", "evening_scan", "2026-09-30T08:00:00+08:00"),
        ("collection", "collect_news", "2026-09-30T00:00:01+00:00"),
        ("market", "collect_market_day_fact", "2026-09-30T08:00:01.500+08:00"),
    ):
        store.enqueue_task(task_id=task_id, kind=kind, idempotency_key=task_id,
            input_version="frozen", input_cutoff_at=created_at, payload={"windowKind": kind},
            budget={"maxAttempts": 1}, created_at=created_at, db_path=db)
        if kind != "collect_news":
            store.bind_task_execution(task_id=task_id, execution_config_id=execution_id,
                execution_config_revision=execution_rev, binding_kind="scheduled",
                bound_at=start.isoformat(), db_path=db)
    original_run_once = worker_module.run_once
    monkeypatch.setattr(worker_module, "run_once",
                        lambda **kwargs: original_run_once(**{**kwargs, "clock": lambda: clock[0]}))
    seen: list[str] = []
    stop = Event()
    def evening(context):
        seen.append("evening")
        if seen.count("evening") == 1:
            return TaskResult("failed", "slice", context.checkpoint,
                retry_at=clock[0] + timedelta(seconds=1), retry_kind="continuation",
                safe_error_code="DISCOVERY_SLICE")
        return TaskResult("completed", "done", context.checkpoint)
    def finish(kind):
        def handler(context):
            seen.append(kind)
            return TaskResult("completed", "done", context.checkpoint)
        return handler
    def maintenance():
        # A deterministic 1.1-second maintenance interval, without a flaky
        # wall-clock sleep, models the real worker loop between slices.
        clock[0] += timedelta(milliseconds=1100)
        if seen == ["evening"]:
            store.enqueue_task(task_id="morning", kind="morning_scan", idempotency_key="morning",
                input_version="frozen", input_cutoff_at=clock[0].isoformat(), payload={"windowKind": "morning"},
                budget={"maxAttempts": 1}, created_at=clock[0].isoformat(), db_path=db)
            store.bind_task_execution(task_id="morning", execution_config_id=execution_id,
                execution_config_revision=execution_rev, binding_kind="scheduled",
                bound_at=clock[0].isoformat(), db_path=db)
        if seen.count("evening") == 2:
            stop.set()
    worker_module.run_worker(db_path=db, worker_id="v363-fair", lease_for=timedelta(minutes=5),
        idle_seconds=0.01, handlers={"evening_scan": evening, "morning_scan": finish("morning"),
                              "collect_news": finish("collection"),
                              "collect_market_day_fact": finish("market")},
        maintenance=maintenance, stop=stop)
    assert seen == ["evening", "morning", "collection", "market", "evening"]


@pytest.mark.parametrize("zero_parent", [False, True])
def test_missing_parent_morning_keeps_new_material_but_reports_parent_gap(tmp_path, monkeypatch, zero_parent):
    db = tmp_path / "no-parent.sqlite"
    run_id, run_rev, exec_id, exec_rev, collection_id, collection_rev = _bindings(db)
    at = datetime(2026, 9, 27, 8, 35, tzinfo=SHANGHAI)
    slot = datetime(2026, 9, 27, 8, 0, tzinfo=SHANGHAI)
    provider = MeteredProvider(ledger_db=db, ledger_task="discovery", api_key="fixture",
        model="deepseek-v4-pro", name="fixture", api_url="https://fixture.invalid/v1/chat/completions",
        read_timeout=1, use_streaming=False)
    provider.max_attempts = 1
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro",
                        lambda **_kwargs: ProviderResolution("configured", provider, "fixture", None))
    if zero_parent:
        # The preceding official report is produced through the same CLI and
        # worker boundary. It is readable but publishes zero formal cards.
        prior_at = datetime(2026, 9, 26, 22, tzinfo=SHANGHAI)
        monkeypatch.setattr(pipeline, "_now", lambda: prior_at)
        parent_task = _cli("enqueue", "--db", str(db), "--kind", "evening",
            "--trading-day", "2026-09-26", "--config-id", run_id,
            "--config-revision", str(run_rev), "--execution-config-id", exec_id,
            "--execution-config-revision", str(exec_rev))
        parent_result = run_once(db_path=db, task_id=parent_task, worker_id="v363-zero-parent",
            lease_for=timedelta(minutes=5), clock=lambda: prior_at,
            handlers={"evening_scan": lambda context: pipeline.production_scan_handler(
                context, tushare_token=None, parquet_dir=tmp_path / "parent-parquet",
                now=lambda: prior_at)}, require_b76_contract=True)
        assert parent_result is not None and parent_result.status == "completed"
        with base.actual_api(db, config_id=run_id, config_revision=run_rev,
                             execution_id=exec_id, execution_revision=exec_rev) as client:
            parent_report = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()["report"]
        assert parent_report["eveningCards"] == []
    # Give every source an actually exhausted, complete interval so this test
    # isolates the missing parent rather than incidental collection gaps.
    collection = store.read_execution_config(config_id=collection_id,
        revision=collection_rev, db_path=db)
    complete_payload = json.loads(json.dumps(collection["payload"]))
    for source in complete_payload["sources"]:
        source["bootstrapStartAt"] = "2026-09-27T07:30:00+08:00"
    collection_rev = store.append_execution_config(config_id=collection_id,
        payload=complete_payload, created_at=at.isoformat(), db_path=db)
    _cli("collection-control", "--db", str(db), "--state", "open",
         "--config-id", collection_id, "--config-revision", str(collection_rev))
    collection_task = enqueue_collection(db_path=db, slot=slot,
        config_id=collection_id, config_revision=collection_rev, now=at)
    wire = _morning_wire()
    original_reply = wire.reply
    def reply(request, body):
        if body["params"]["name"] == "list_news":
            return rpc(body, {"structuredContent": {"status": 200, "data": {
                "items": [{"id": "morning-catalog-001", "url": "https://news.jin10.com/details/morning-catalog-001",
                           "time": "2026-09-27T07:30:00+08:00", "title": "标题 002：晨间公告目录",
                           "intro": "客户测试仍在继续，订单未确认。"}],
                "next_cursor": None, "has_more": False}}})
        return original_reply(request, body)
    wire.reply = reply
    def client_factory(**kwargs):
        return Jin10Client(**kwargs, transport=httpx.MockTransport(wire))
    collected = run_once(db_path=db, task_id=collection_task, worker_id="v363-collect",
        lease_for=timedelta(minutes=5), clock=lambda: at,
        handlers={"collect_news": create_collection_handler(
            tushare_token="fixture-token", jin10_token=JIN10_TOKEN,
            tushare_request=lambda _payload: {"code": 0, "data": {
                "fields": ["pub_time", "src", "title", "content"], "items": []}},
            client_factory=client_factory)},
        require_b76_contract=True)
    assert collected is not None and collected.status == "completed"
    collection_checkpoint = store.task_execution_input(task_id=collection_task, db_path=db)["checkpoint"]
    catalog_id = collection_checkpoint["sources"]["jin10-news"]["documentRefs"][0]["documentId"]
    monkeypatch.setattr(base, "TITLE_COUNT", 3)
    monkeypatch.setattr(base, "DeterministicTransport", _FlashReportTransport)
    monkeypatch.setattr(_FlashReportTransport, "morning_document_ids", {catalog_id})
    monkeypatch.setattr(_FlashReportTransport, "loopback_morning_discovery_zero", False)
    monkeypatch.setattr(_FlashReportTransport, "research_mode", "direct")
    base.install_offline_transports(monkeypatch, refusal_event=None,
                                    selected_event_count=2, fixture_run_at=at)
    monkeypatch.setenv("JIN10_MCP_TOKEN", JIN10_TOKEN)
    monkeypatch.setattr(pipeline, "Jin10Client", client_factory)
    monkeypatch.setattr(pipeline, "_now", lambda: at)
    report_task = _cli("enqueue", "--db", str(db), "--kind", "morning",
        "--trading-day", "2026-09-27", "--config-id", run_id,
        "--config-revision", str(run_rev), "--execution-config-id", exec_id,
        "--execution-config-revision", str(exec_rev))
    result = run_once(db_path=db, task_id=report_task, worker_id="v363-no-parent",
        lease_for=timedelta(minutes=5), clock=lambda: at,
        handlers={"morning_scan": lambda context: pipeline.production_scan_handler(
            context, tushare_token="fixture-token", parquet_dir=tmp_path / "parquet", now=lambda: at)},
        require_b76_contract=True)
    assert result is not None and result.status == "completed"
    scan_id = store.task_execution_input(task_id=report_task, db_path=db)["checkpoint"]["scanId"]
    assert store.get_scan(scan_id=scan_id, db_path=db)["coverage"]["b90MorningParent"]["state"] == (
        "complete" if zero_parent else "unavailable")
    with base.actual_api(db, config_id=run_id, config_revision=run_rev,
                         execution_id=exec_id, execution_revision=exec_rev) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=morning")
        assert response.status_code == 200
        envelope = response.json()
        report = envelope["report"]
        materials = client.get(f"/api/v1/k10/v2/reports/{report['reportId']}/materials").json()
    assert report["status"] == "partial"
    assert report["morningReview"]["state"] == ("complete" if zero_parent else "unavailable")
    assert report["morningReview"]["targetCompanyCount"] == 0
    assert report["delivery"]["outcome"] == "partial"
    parent_gaps = [gap for gap in report["delivery"]["gaps"]
                   if gap["reasonCode"] == "morning_parent_unavailable"]
    if zero_parent:
        assert parent_gaps == []
    else:
        assert len(parent_gaps) == 1 and parent_gaps[0]["unitId"] == report["reportId"]
    assert materials["items"], "newly researched morning material must remain readable"
    morning_coverage = store.get_scan(scan_id=scan_id, db_path=db)["coverage"]
    decided = {(ref["documentId"], ref["revision"])
               for ref in morning_coverage["collectedInputConsumption"]["terminalRefs"]}
    flash = collection_checkpoint["sources"]["jin10-flash"]["documentRefs"][0]
    assert (flash["documentId"], flash["revision"]) in decided
    directory = os.environ.get("NK_V363_API_DIR")
    if directory:
        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        name = "zero-parent" if zero_parent else "parent-unavailable"
        (output / f"{name}.json").write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")
        (output / f"{name}-materials.json").write_text(
            json.dumps(materials, ensure_ascii=False, indent=2) + "\n")
    from tests.test_b92_input_consumption import _next_evening
    following = _next_evening({"dbPath": db, "bindings": (
        run_id, run_rev, exec_id, exec_rev, collection_id, collection_rev)}, tmp_path, monkeypatch)
    following_refs = {(ref["documentId"], ref["revision"])
                      for ref in following["coverage"]["collectedInput"]["inputDocumentRefs"]}
    assert (flash["documentId"], flash["revision"]) not in following_refs
